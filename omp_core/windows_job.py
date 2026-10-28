"""Windows Job Object containment for one child process tree.

The module is intentionally small: it creates one Job Object with
``KILL_ON_JOB_CLOSE``, creates the child suspended with the job attached by
``PROC_THREAD_ATTRIBUTE_JOB_LIST``, then resumes it only after the caller has
saved its PID.  The job handle is not inheritable.  If the owner process dies,
Windows closes the handle and terminates every process still in the job.

This is process-lifecycle containment, not a security sandbox.  A job does not
restrict filesystem, network, credentials, or memory access.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections.abc import Mapping as _MappingABC
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Mapping, Sequence

if os.name == "nt":  # pragma: no branch - imported for type checking on POSIX
    import msvcrt


class WindowsJobError(OSError):
    """Job creation, attachment, or bounded cleanup failed without argv."""


class WindowsJobCleanupTimeout(WindowsJobError):
    """The job still contained live processes at the bounded deadline."""


# Win32 constants and structures.  Declarations are kept explicit so 64-bit
# pointer sizes are not guessed from Python object sizes.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_HANDLE_FLAG_INHERIT = 0x00000001
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_ERROR_INSUFFICIENT_BUFFER = 122
_DEFAULT_CLEANUP_SECONDS = 10.0
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_TOKEN_INTEGRITY_LEVEL = 25
_TOKEN_IS_APP_CONTAINER = 29
_TOKEN_CAPABILITIES = 30
_TOKEN_APPCONTAINER_SID = 31
_ERROR_ALREADY_EXISTS = 183
_LOW_INTEGRITY_SID = "S-1-16-4096"
_APP_CONTAINER_OWNER = object()


class _LARGE_INTEGER(ctypes.Union):
    _fields_ = [
        ("QuadPart", ctypes.c_longlong),
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", _LARGE_INTEGER),
        ("TotalKernelTime", _LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", _LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", _LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _STARTUPINFOW),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Sid", ctypes.c_void_p),
        ("Attributes", wintypes.DWORD),
    ]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:
    """Return the configured Kernel32 API table.

    Raises:
        WindowsJobError: the module is used on a non-Windows host.
    """
    if os.name != "nt":
        raise WindowsJobError("Windows Job Objects require Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    api.SetInformationJobObject.restype = wintypes.BOOL
    api.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.QueryInformationJobObject.restype = wintypes.BOOL
    api.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    api.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    api.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID,
        ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID,
    ]
    api.UpdateProcThreadAttribute.restype = wintypes.BOOL
    api.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    api.DeleteProcThreadAttributeList.restype = None
    api.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
        wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
        wintypes.LPVOID, wintypes.LPVOID,
    ]
    api.CreateProcessW.restype = wintypes.BOOL
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    api.IsProcessInJob.restype = wintypes.BOOL
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateJobObject.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.SetHandleInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
    ]
    api.SetHandleInformation.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetCurrentProcess.argtypes = []
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.LocalFree.argtypes = [wintypes.HLOCAL]
    api.LocalFree.restype = wintypes.HLOCAL
    return api


def _advapi32() -> ctypes.WinDLL:
    """Return the configured Advapi32 API table for token and SID calls."""
    if os.name != "nt":
        raise WindowsJobError("Windows token queries require Windows")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    api.OpenProcessToken.restype = wintypes.BOOL
    api.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetTokenInformation.restype = wintypes.BOOL
    api.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
    ]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    api.FreeSid.argtypes = [ctypes.c_void_p]
    api.FreeSid.restype = ctypes.c_void_p
    return api


def _userenv() -> ctypes.WinDLL:
    """Return the configured Userenv API table for AppContainer profiles."""
    if os.name != "nt":
        raise WindowsJobError("Windows AppContainer profiles require Windows")
    api = ctypes.WinDLL("userenv", use_last_error=True)
    api.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    api.CreateAppContainerProfile.restype = ctypes.c_long
    api.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    api.DeleteAppContainerProfile.restype = ctypes.c_long
    api.GetAppContainerFolderPath.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR),
    ]
    api.GetAppContainerFolderPath.restype = ctypes.c_long
    return api


def _ole32() -> ctypes.WinDLL:
    """Return the configured Ole32 API table for task-memory free."""
    if os.name != "nt":
        raise WindowsJobError("Windows task-memory free requires Windows")
    api = ctypes.WinDLL("ole32", use_last_error=True)
    api.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    api.CoTaskMemFree.restype = None
    return api


def _check(ok: object, stage: str) -> None:
    """Raise a redacted WindowsJobError when a Win32 call reports failure."""
    if ok:
        return
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip()
    raise WindowsJobError(f"{stage} failed (WinError {code}: {detail})")


def _sid_string(advapi: ctypes.WinDLL, sid: int) -> str:
    """Return the string form of *sid*, freeing the Win32-owned buffer."""
    kernel32 = _kernel32()
    out = wintypes.LPWSTR()
    _check(
        advapi.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out)),
        "convert AppContainer SID to string",
    )
    if not out.value:
        raise WindowsJobError("convert AppContainer SID to string was empty")
    value = out.value
    if kernel32.LocalFree(out):
        raise WindowsJobError("free AppContainer SID string failed")
    return value


def _app_container_folder(userenv: ctypes.WinDLL, sid: str) -> Path:
    """Return the profile folder reported by Userenv for *sid*."""
    out = wintypes.LPWSTR()
    result = int(userenv.GetAppContainerFolderPath(sid, ctypes.byref(out)))
    if result != 0:
        raise WindowsJobError(
            "read AppContainer profile folder failed "
            f"(HRESULT 0x{result & 0xFFFFFFFF:08x})"
        )
    try:
        value = out.value or ""
    finally:
        _ole32().CoTaskMemFree(out)
    if not value:
        raise WindowsJobError("AppContainer profile folder was empty")
    return Path(value)


def _free_sid(advapi: ctypes.WinDLL, sid: int) -> None:
    """Free one owned SID; ``FreeSid`` returns NULL on success."""
    if advapi.FreeSid(ctypes.c_void_p(sid)):
        raise WindowsJobError("free AppContainer SID failed")


def _delete_profile(userenv: ctypes.WinDLL, name: str) -> None:
    """Delete one owned profile and raise on a non-zero HRESULT."""
    result = int(userenv.DeleteAppContainerProfile(name))
    if result != 0:
        raise WindowsJobError(
            "delete AppContainer profile failed "
            f"(HRESULT 0x{result & 0xFFFFFFFF:08x})"
        )


def _validate_environment(env: Mapping[str, str]) -> None:
    """Reject an environment mapping that Windows cannot pass verbatim.

    Raises:
        ValueError: the value is not a text-to-text mapping or contains an
            empty key, ``=`` in a key, or a NUL in a key or value.
    """
    if not isinstance(env, _MappingABC):
        raise ValueError("env must be a mapping of text keys to text values")
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("env keys and values must be text")
        if not key:
            raise ValueError("env keys must not be empty")
        drive_key = (
            len(key) == 3 and key[0] == "=" and key[2] == ":" and key[1].isalpha()
        )
        if "=" in key and not drive_key:
            raise ValueError("env keys must not contain '='")
        if "\0" in key or "\0" in value:
            raise ValueError("env keys and values must not contain NUL")


def _token_buffer(
    advapi: ctypes.WinDLL, token: wintypes.HANDLE, information_class: int
) -> ctypes.Array[ctypes.c_char]:
    """Return one sized token-information buffer."""
    size = wintypes.DWORD()
    advapi.GetTokenInformation(
        token, information_class, None, 0, ctypes.byref(size)
    )
    if size.value == 0:
        _check(False, f"size token information {information_class}")
    if size.value > 1024 * 1024:
        raise WindowsJobError(
            f"token information {information_class} is unreasonably large"
        )
    data = ctypes.create_string_buffer(size.value)
    returned = wintypes.DWORD()
    _check(
        advapi.GetTokenInformation(
            token, information_class, data, size, ctypes.byref(returned)
        ),
        f"query token information {information_class}",
    )
    return data


def _token_sid(
    advapi: ctypes.WinDLL, token: wintypes.HANDLE, information_class: int
) -> str:
    """Return the string SID carried by one token-information class."""
    blob = _token_buffer(advapi, token, information_class)
    record = ctypes.cast(blob, ctypes.POINTER(_SID_AND_ATTRIBUTES)).contents
    if not record.Sid:
        raise WindowsJobError(
            f"token information {information_class} carries no SID"
        )
    return _sid_string(advapi, int(record.Sid))


def _token_capability_count(
    advapi: ctypes.WinDLL, token: wintypes.HANDLE
) -> int:
    """Return the number of AppContainer capabilities in *token*."""
    blob = _token_buffer(advapi, token, _TOKEN_CAPABILITIES)
    return int(ctypes.cast(blob, ctypes.POINTER(wintypes.DWORD)).contents.value)


def _open_process_token(
    advapi: ctypes.WinDLL, process: wintypes.HANDLE
) -> wintypes.HANDLE:
    """Open a query-only token for *process*."""
    token = wintypes.HANDLE()
    _check(
        advapi.OpenProcessToken(
            process, _TOKEN_QUERY, ctypes.byref(token)
        ),
        "open contained process token",
    )
    return token


def _current_user_sid(api: ctypes.WinDLL, advapi: ctypes.WinDLL) -> str:
    """Return the current process user SID as text."""
    token = _open_process_token(advapi, api.GetCurrentProcess())
    try:
        return _token_sid(advapi, token, _TOKEN_USER)
    finally:
        api.CloseHandle(token)


def _verify_container_token(
    api: ctypes.WinDLL,
    advapi: ctypes.WinDLL,
    process: wintypes.HANDLE,
    profile: "WindowsAppContainerProfile",
    expected_user_sid: str,
) -> None:
    """Verify the suspended child token before the caller may resume it.

    Raises:
        WindowsJobError: any token query failed or the token is not the same
            user inside the expected low-integrity zero-capability container.
    """
    token = _open_process_token(advapi, process)
    try:
        is_container = wintypes.DWORD()
        returned = wintypes.DWORD()
        _check(
            advapi.GetTokenInformation(
                token,
                _TOKEN_IS_APP_CONTAINER,
                ctypes.byref(is_container),
                ctypes.sizeof(is_container),
                ctypes.byref(returned),
            ),
            "query AppContainer token flag",
        )
        if not is_container.value:
            raise WindowsJobError(
                "contained process token is not an AppContainer"
            )
        if _token_sid(advapi, token, _TOKEN_APPCONTAINER_SID) != profile.sid:
            raise WindowsJobError(
                "contained process AppContainer SID does not match the profile"
            )
        if _token_sid(advapi, token, _TOKEN_INTEGRITY_LEVEL) != _LOW_INTEGRITY_SID:
            raise WindowsJobError(
                "contained process is not at low mandatory integrity"
            )
        if _token_capability_count(advapi, token) != 0:
            raise WindowsJobError(
                "contained process has AppContainer capabilities"
            )
        if _token_sid(advapi, token, _TOKEN_USER) != expected_user_sid:
            raise WindowsJobError(
                "contained process runs as a different user"
            )
    except BaseException:
        api.CloseHandle(token)
        raise
    if not api.CloseHandle(token):
        _check(False, "close contained process token")


def _release_partial_process_information(
    api: ctypes.WinDLL, info: _PROCESS_INFORMATION
) -> list[str]:
    """Release handles a ``CreateProcessW`` call returned before it raised.

    The ownership guard runs before the raw ``PROCESS_INFORMATION`` is copied
    into a managed object, so an interrupt at the native return cannot leak a
    suspended child or its thread handle.  Returns every cleanup step that
    could not be positively completed.
    """
    problems: list[str] = []
    if info.hProcess:
        try:
            if not api.TerminateProcess(info.hProcess, 1):
                problems.append("terminate failed")
        except BaseException:
            problems.append("terminate failed")
        try:
            if api.WaitForSingleObject(info.hProcess, 5000) != _WAIT_OBJECT_0:
                problems.append("wait failed")
        except BaseException:
            problems.append("wait failed")
        try:
            if not api.CloseHandle(info.hProcess):
                problems.append("close process failed")
        except BaseException:
            problems.append("close process failed")
        info.hProcess = wintypes.HANDLE(0)
    if info.hThread:
        try:
            if not api.CloseHandle(info.hThread):
                problems.append("close thread failed")
        except BaseException:
            problems.append("close thread failed")
        info.hThread = wintypes.HANDLE(0)
    return problems


def _wait_job_empty(
    api: ctypes.WinDLL, job: wintypes.HANDLE, timeout: float
) -> tuple[bool, int]:
    """Wait boundedly until *job* reports no active processes."""
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        active = _active_processes(api, job)
        if active == 0:
            return True, 0
        if time.monotonic() >= deadline:
            return False, active
        time.sleep(0.01)


def _stop_started_process(
    api: ctypes.WinDLL,
    process: wintypes.HANDLE | None,
    job: wintypes.HANDLE | None,
    timeout: float,
) -> list[str]:
    """Stop a failed start tree and report every unproven cleanup step."""
    problems: list[str] = []
    if process is not None:
        try:
            if not api.TerminateProcess(process, 1):
                problems.append("terminate failed")
        except BaseException:
            problems.append("terminate failed")
        try:
            if api.WaitForSingleObject(process, 5000) != _WAIT_OBJECT_0:
                problems.append("wait failed")
        except BaseException:
            problems.append("wait failed")
    if job is not None:
        terminated = False
        if process is not None:
            try:
                terminated = bool(api.TerminateJobObject(job, 1))
            except BaseException:
                terminated = False
        try:
            empty, active = _wait_job_empty(api, job, timeout)
        except BaseException:
            problems.append("job query failed")
        else:
            if not empty:
                if not terminated:
                    problems.append("terminate job failed")
                problems.append(f"job not empty ({active} active)")
    return problems


def _active_processes(api: ctypes.WinDLL, job: wintypes.HANDLE) -> int:
    """Return the number of processes currently in *job*."""
    info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    _check(
        api.QueryInformationJobObject(
            job,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        ),
        "query job accounting",
    )
    return int(info.ActiveProcesses)


def _create_job(api: ctypes.WinDLL) -> wintypes.HANDLE:
    """Create a non-inheritable job with kill-on-close enabled."""
    job = api.CreateJobObjectW(None, None)
    _check(job, "create job object")
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        _check(
            api.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ),
            "configure job object",
        )
    except BaseException:
        api.CloseHandle(job)
        raise
    return job


def _environment_block(env: Mapping[str, str] | None) -> str:
    """Return a sorted, double-NUL-terminated Unicode environment block."""
    source = os.environ if env is None else env
    return "\0".join(f"{key}={value}" for key, value in sorted(source.items())) + "\0\0"


def _write_all(fd: int, data: bytes) -> None:
    """Write *data* to *fd*, retrying partial writes."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise WindowsJobError("write stdin failed")
        view = view[written:]


def _unlink_with_retry(path: Path, attempts: int = 50) -> bool:
    """Delete a private stdin file after child handles close.

    Windows can report a sharing violation briefly after the last job process
    exits.  The retry is bounded and reports whether the file is gone; a
    leftover file stays under the run directory and is never committed.
    """
    for attempt in range(attempts):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(0.01)
    return False


def _inheritable_handle(fd: int) -> int:
    """Return the OS handle for *fd* and mark it inheritable."""
    handle = int(msvcrt.get_osfhandle(fd))
    if handle == -1:
        raise WindowsJobError("cannot obtain OS file handle")
    _check(
        _kernel32().SetHandleInformation(
            wintypes.HANDLE(handle), _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT
        ),
        "set handle inheritance",
    )
    return handle


def _attribute_list(api: ctypes.WinDLL, job: wintypes.HANDLE,
                    handles: Sequence[int],
                    container_sid: int | None = None
                    ) -> tuple[object, ...]:
    """Build the process attribute list and return its live backing objects.

    The legacy path uses exactly two attributes (job and handle lists).  The
    AppContainer path adds ``SECURITY_CAPABILITIES`` with zero capabilities and
    the profile SID.  The returned tuple must stay alive until
    ``CreateProcessW`` returns.
    """
    count = 2 if container_sid is None else 3
    size = ctypes.c_size_t()
    api.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        _check(False, "size process attribute list")
    buffer = ctypes.create_string_buffer(size.value)
    pointer = ctypes.cast(buffer, wintypes.LPVOID)
    _check(
        api.InitializeProcThreadAttributeList(
            pointer, count, 0, ctypes.byref(size)
        ),
        "initialize process attribute list",
    )
    job_handles = (wintypes.HANDLE * 1)(job)
    handle_array = (wintypes.HANDLE * len(handles))(*handles)
    keepalive: list[object] = [buffer, job_handles, handle_array]
    try:
        if container_sid is not None:
            capabilities = _SECURITY_CAPABILITIES(
                ctypes.c_void_p(container_sid), None, 0, 0
            )
            keepalive.append(capabilities)
            _check(
                api.UpdateProcThreadAttribute(
                    pointer,
                    0,
                    _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                    ctypes.byref(capabilities),
                    ctypes.sizeof(capabilities),
                    None,
                    None,
                ),
                "attach security capabilities attribute",
            )
        _check(
            api.UpdateProcThreadAttribute(
                pointer,
                0,
                _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.cast(job_handles, wintypes.LPVOID),
                ctypes.sizeof(job_handles),
                None,
                None,
            ),
            "attach job list attribute",
        )
        _check(
            api.UpdateProcThreadAttribute(
                pointer,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(handle_array, wintypes.LPVOID),
                ctypes.sizeof(handle_array),
                None,
                None,
            ),
            "attach handle list attribute",
        )
    except BaseException:
        api.DeleteProcThreadAttributeList(pointer)
        raise
    return tuple(keepalive)


def _discard_partial_profile(
    userenv: ctypes.WinDLL,
    advapi: ctypes.WinDLL,
    pointer: int,
    name: str,
    exc: BaseException,
    *,
    delete: bool,
) -> None:
    """Release profile resources owned by a failed create, preserving *exc*."""
    notes: list[str] = []
    if pointer:
        try:
            _free_sid(advapi, pointer)
        except BaseException:
            notes.append("SID release failed")
    if delete:
        try:
            _delete_profile(userenv, name)
        except BaseException:
            notes.append("profile deletion failed")
    if notes and hasattr(exc, "add_note"):
        exc.add_note("partial profile cleanup: " + "; ".join(notes))


class WindowsAppContainerProfile:
    """Own one AppContainer profile, its SID allocation, and its folder.

    Create it with :meth:`create`; the object is the only supported source of
    an AppContainer SID for :meth:`WindowsJobProcess.start`.  :meth:`close`
    releases only resources this object created and refuses while a bound
    process tree is live or unverified.
    """

    def __init__(
        self,
        *,
        name: str,
        sid: str,
        folder: Path,
        sid_pointer: int,
        userenv: ctypes.WinDLL,
        advapi: ctypes.WinDLL,
        owner: object,
    ) -> None:
        if owner is not _APP_CONTAINER_OWNER:
            raise WindowsJobError(
                "AppContainer profiles must be created with create()"
            )
        self._owner_token = owner
        self._name = name
        self._sid = sid
        self._folder = folder
        self._sid_pointer: int | None = sid_pointer
        self._sid_release_failed = False
        self._failed_start: str | None = None
        self._userenv = userenv
        self._advapi = advapi
        self._closed = False
        self._bound: WindowsJobProcess | None = None

    @classmethod
    def create(cls, name: str) -> "WindowsAppContainerProfile":
        """Create and own a new AppContainer profile named *name*.

        An existing profile with the same name is never adopted or deleted.

        Raises:
            ValueError: *name* is not a non-empty string without NUL.
            WindowsJobError: profile or SID allocation, SID conversion, or
                folder lookup failed; partially created profile resources are
                released before the error is re-raised.
        """
        if os.name != "nt":
            raise WindowsJobError(
                "Windows AppContainer profiles require Windows"
            )
        if not isinstance(name, str) or not name:
            raise ValueError("profile name must be a non-empty string")
        if "\0" in name:
            raise ValueError("profile name must not contain NUL")
        userenv = _userenv()
        advapi = _advapi32()
        sid_pointer = ctypes.c_void_p()
        try:
            result = int(
                userenv.CreateAppContainerProfile(
                    name, name, name, None, 0, ctypes.byref(sid_pointer)
                )
            )
        except BaseException as exc:
            # A non-zero out pointer is the only ownership evidence available
            # when the native call is interrupted before it returns a result.
            pointer = int(sid_pointer.value or 0)
            _discard_partial_profile(
                userenv, advapi, pointer, name, exc,
                delete=bool(pointer),
            )
            raise
        if result != 0:
            code = result & 0xFFFFFFFF
            if (code & 0xFFFF) == _ERROR_ALREADY_EXISTS:
                raise WindowsJobError(
                    "create AppContainer profile failed: profile name already "
                    f"exists (HRESULT 0x{code:08x})"
                )
            raise WindowsJobError(
                "create AppContainer profile failed "
                f"(HRESULT 0x{code:08x})"
            )
        pointer = int(sid_pointer.value or 0)
        try:
            if not pointer:
                raise WindowsJobError(
                    "create AppContainer profile returned a null SID"
                )
            sid = _sid_string(advapi, pointer)
            folder = _app_container_folder(userenv, sid)
            return cls(
                name=name,
                sid=sid,
                folder=folder,
                sid_pointer=pointer,
                userenv=userenv,
                advapi=advapi,
                owner=_APP_CONTAINER_OWNER,
            )
        except BaseException as exc:
            _discard_partial_profile(
                userenv, advapi, pointer, name, exc, delete=True,
            )
            raise

    @property
    def name(self) -> str:
        """Return the profile name passed to :meth:`create`."""
        return self._name

    @property
    def sid(self) -> str:
        """Return the AppContainer SID as text."""
        return self._sid

    @property
    def folder(self) -> Path:
        """Return the profile folder reported by Windows."""
        return self._folder

    @property
    def closed(self) -> bool:
        """Return whether this profile released all owned resources."""
        return self._closed

    def _bind(self, process: "WindowsJobProcess") -> None:
        """Bind one suspended process to this profile."""
        self._bound = process

    def _release_bound(self, process: "WindowsJobProcess") -> None:
        """Drop the binding once *process* proved its tree stopped."""
        if self._bound is process and process.tree_stopped:
            self._bound = None

    def close(self) -> None:
        """Free this profile's SID and delete the profile it created.

        Closing is idempotent after a full success.  A failure leaves
        :attr:`closed` false and raises, so a partial release is never reported
        as success.

        Raises:
            WindowsJobError: a bound process tree is still live or its state
                cannot be verified, or SID/profile release failed.
        """
        if self._closed:
            return
        if self._sid_release_failed:
            raise WindowsJobError(
                "cannot close AppContainer profile: SID release previously "
                "failed"
            )
        if self._failed_start is not None:
            raise WindowsJobError(
                "cannot close AppContainer profile: previous start cleanup "
                "was unproven"
            )
        if self._sid_pointer is None:
            raise WindowsJobError(
                "cannot close AppContainer profile: SID is no longer owned"
            )
        bound = self._bound
        if bound is not None and not bound.tree_stopped:
            raise WindowsJobError(
                "cannot close AppContainer profile: bound process tree is "
                "live or unverified"
            )
        pointer = self._sid_pointer
        self._sid_pointer = None
        try:
            _free_sid(self._advapi, pointer)
        except BaseException:
            self._sid_release_failed = True
            raise
        _delete_profile(self._userenv, self._name)
        if bound is not None:
            self._bound = None
            if bound._app_container is self:
                bound._app_container = None
        self._closed = True


class WindowsJobProcess:
    """A suspended, job-contained process owned by the current process.

    Create it with :meth:`start`.  The process cannot execute before
    :meth:`resume`.  Closing the object closes the job handle; because
    ``KILL_ON_JOB_CLOSE`` is set, any process still in the tree is terminated
    by Windows.  The object never treats ``returncode`` alone as proof that
    descendants stopped; use :attr:`tree_stopped`.
    """

    def __init__(
        self,
        *,
        api: ctypes.WinDLL,
        job: wintypes.HANDLE,
        process: wintypes.HANDLE,
        thread: wintypes.HANDLE,
        pid: int,
        stdin_path: Path,
        cleanup_timeout: float,
        in_job: bool,
        app_container: WindowsAppContainerProfile | None = None,
    ) -> None:
        self._api = api
        self._job = job
        self._process = process
        self._thread = thread
        self._stdin_path = stdin_path
        self.cleanup_timeout = cleanup_timeout
        self.pid = pid
        self.in_job = in_job
        self._app_container = app_container
        self.returncode: int | None = None
        self._resumed = False
        self._stopped = False
        self._closed = False
        self._close_failed = False
        self._active = 1

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        cwd: Path,
        log_path: Path,
        *,
        stdin_text: str = "",
        temp_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        cleanup_timeout: float = _DEFAULT_CLEANUP_SECONDS,
        app_container: WindowsAppContainerProfile | None = None,
    ) -> "WindowsJobProcess":
        """Create *argv* suspended inside a new kill-on-close Job Object.

        With ``app_container=None`` (the default) the legacy two-attribute
        path and ``env=None`` inheritance behavior are unchanged.  With a
        profile, ``env`` must be an explicit text mapping, the profile must be
        open and unbound, and the child is created at low integrity with zero
        capabilities.  The child token is verified before this method returns;
        the caller still saves the PID and calls :meth:`resume`.

        Raises:
            WindowsJobError: any Win32 setup, token identity, inheritance, or
                job-membership check failed.  No process is resumed, and no
                replacement process is started.
            ValueError: argv, env, or profile input is invalid.  Validation
                happens before any file, log, or process side effect.
        """
        if os.name != "nt":
            raise WindowsJobError("Windows Job Objects require Windows")
        if not argv:
            raise ValueError("argv must not be empty")
        if any("\0" in arg for arg in argv):
            raise ValueError("argv must not contain NUL")
        if app_container is not None:
            if not isinstance(app_container, WindowsAppContainerProfile):
                raise ValueError(
                    "app_container must be a WindowsAppContainerProfile"
                )
            if app_container._owner_token is not _APP_CONTAINER_OWNER:
                raise ValueError(
                    "app_container must be created by "
                    "WindowsAppContainerProfile.create"
                )
            if app_container.closed:
                raise WindowsJobError(
                    "cannot start a process with a closed AppContainer "
                    "profile"
                )
            if app_container._sid_release_failed:
                raise WindowsJobError(
                    "cannot start a process with an AppContainer profile "
                    "whose SID release previously failed"
                )
            if app_container._sid_pointer is None:
                raise WindowsJobError(
                    "cannot start a process with an AppContainer profile "
                    "whose SID is no longer owned"
                )
            if app_container._failed_start is not None:
                raise WindowsJobError(
                    "cannot start a process with an AppContainer profile "
                    "whose previous start cleanup was unproven"
                )
            if env is None:
                raise ValueError(
                    "an explicit env mapping is required with app_container"
                )
            _validate_environment(env)
            bound = app_container._bound
            if bound is not None and not bound.tree_stopped:
                raise WindowsJobError(
                    "AppContainer profile is bound to a live or unverified "
                    "process tree"
                )
        api = _kernel32()
        advapi = _advapi32() if app_container is not None else None
        expected_user_sid = (
            _current_user_sid(api, advapi) if advapi is not None else ""
        )
        directory = temp_dir or Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        job: wintypes.HANDLE | None = None
        process: wintypes.HANDLE | None = None
        thread: wintypes.HANDLE | None = None
        stdin_fd: int | None = None
        log_fd: int | None = None
        stdin_path: Path | None = None
        attribute_pointer: object | None = None
        started: WindowsJobProcess | None = None
        raw_problems: list[str] = []
        try:
            job = _create_job(api)
            stdin_fd, raw_stdin_path = tempfile.mkstemp(
                prefix=".omp-stdin-", suffix=".tmp", dir=directory
            )
            stdin_path = Path(raw_stdin_path)
            _write_all(stdin_fd, stdin_text.encode("utf-8"))
            os.lseek(stdin_fd, 0, os.SEEK_SET)
            stdin_handle = _inheritable_handle(stdin_fd)
            log_fd = os.open(
                log_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
            log_handle = _inheritable_handle(log_fd)

            container_sid = (
                None if app_container is None else app_container._sid_pointer
            )
            attributes = _attribute_list(
                api, job, (stdin_handle, log_handle), container_sid
            )
            buffer = attributes[0]
            attribute_pointer = ctypes.cast(buffer, wintypes.LPVOID)
            startup = _STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = wintypes.HANDLE(stdin_handle)
            startup.StartupInfo.hStdOutput = wintypes.HANDLE(log_handle)
            startup.StartupInfo.hStdError = wintypes.HANDLE(log_handle)
            startup.lpAttributeList = attribute_pointer
            command = subprocess.list2cmdline(list(argv))
            command_buffer = ctypes.create_unicode_buffer(command)
            environment = ctypes.create_unicode_buffer(_environment_block(env))
            current_directory = str(cwd)
            info = _PROCESS_INFORMATION()
            flags = (
                _CREATE_SUSPENDED
                | _CREATE_NEW_PROCESS_GROUP
                | _CREATE_UNICODE_ENVIRONMENT
                | _EXTENDED_STARTUPINFO_PRESENT
            )
            try:
                created = api.CreateProcessW(
                    None,
                    command_buffer,
                    None,
                    None,
                    True,
                    flags,
                    environment,
                    current_directory,
                    ctypes.byref(startup),
                    ctypes.byref(info),
                )
            except BaseException:
                # Ownership guard at the native return: an interrupt before
                # the raw handles are copied into a managed object must still
                # release the child and thread handles.
                raw_problems.extend(
                    _release_partial_process_information(api, info)
                )
                raise
            if not created:
                error = ctypes.get_last_error()
                raw_problems.extend(
                    _release_partial_process_information(api, info)
                )
                detail = ctypes.FormatError(error).strip()
                raise WindowsJobError(
                    "create contained process failed "
                    f"(WinError {error}: {detail})"
                )
            process = info.hProcess
            thread = info.hThread
            # Keep the attribute list and its backing objects alive through
            # the native call above.
            del attributes
            in_job = wintypes.BOOL()
            _check(
                api.IsProcessInJob(process, job, ctypes.byref(in_job)),
                "verify job membership",
            )
            if not in_job.value:
                raise WindowsJobError("created process is not in the job")
            if app_container is not None:
                _verify_container_token(
                    api, advapi, process, app_container, expected_user_sid
                )
            started = cls(
                api=api,
                job=job,
                process=process,
                thread=thread,
                pid=int(info.dwProcessId),
                stdin_path=stdin_path,
                cleanup_timeout=cleanup_timeout,
                in_job=bool(in_job.value),
                app_container=app_container,
            )
            if app_container is not None:
                app_container._bind(started)
            return started
        except BaseException as exc:
            if app_container is not None:
                # Pessimistic until every cleanup step below is proven, so an
                # interrupt during cleanup cannot leave a reusable profile.
                app_container._failed_start = (
                    "start failed before cleanup proved the tree stopped"
                )
            problems = list(raw_problems)
            problems.extend(
                _stop_started_process(api, process, job, cleanup_timeout)
            )
            if thread is not None:
                try:
                    if not api.CloseHandle(thread):
                        problems.append("close thread failed")
                except BaseException:
                    problems.append("close thread failed")
            if process is not None:
                try:
                    if not api.CloseHandle(process):
                        problems.append("close process failed")
                except BaseException:
                    problems.append("close process failed")
            if job is not None:
                try:
                    if not api.CloseHandle(job):
                        problems.append("close job failed")
                except BaseException:
                    problems.append("close job failed")
            if stdin_fd is not None:
                try:
                    os.close(stdin_fd)
                except OSError:
                    problems.append("close stdin failed")
                stdin_fd = None
            if log_fd is not None:
                try:
                    os.close(log_fd)
                except OSError:
                    problems.append("close log failed")
                log_fd = None
            if stdin_path is not None:
                if not _unlink_with_retry(stdin_path):
                    problems.append("unlink stdin failed")
            if app_container is not None and not problems:
                app_container._failed_start = None
                if started is not None:
                    if app_container._bound is started:
                        app_container._bound = None
                    if started._app_container is app_container:
                        started._app_container = None
            if problems and hasattr(exc, "add_note"):
                exc.add_note("process cleanup: " + "; ".join(problems))
            raise
        finally:
            if attribute_pointer is not None:
                try:
                    api.DeleteProcThreadAttributeList(attribute_pointer)
                except Exception:
                    pass
            if stdin_fd is not None:
                try:
                    os.close(stdin_fd)
                except OSError:
                    pass
            if log_fd is not None:
                try:
                    os.close(log_fd)
                except OSError:
                    pass

    @property
    def resumed(self) -> bool:
        """Return whether the initial thread has been resumed."""
        return self._resumed

    @property
    def active_processes(self) -> int:
        """Return the current job process count, querying Windows."""
        if self._closed:
            return 0 if self._stopped else self._active
        self._active = _active_processes(self._api, self._job)
        return self._active

    @property
    def tree_stopped(self) -> bool:
        """Return whether root exit and an empty job were positively observed."""
        if self._stopped:
            return True
        if self.returncode is None:
            return False
        try:
            return self.active_processes == 0
        except WindowsJobError:
            return False

    def resume(self) -> None:
        """Resume the initial thread after the caller saved the PID.

        Raises:
            WindowsJobError: the thread could not be resumed.  The caller must
                stop the job; no worker code has run before this call.
        """
        if self._closed:
            raise WindowsJobError("cannot resume a closed contained process")
        if self._resumed:
            return
        result = self._api.ResumeThread(self._thread)
        if result == _WAIT_FAILED:
            _check(False, "resume contained process")
        self._resumed = True
        self._api.CloseHandle(self._thread)
        self._thread = wintypes.HANDLE(0)

    def _wait_root(self, timeout: float | None) -> None:
        """Wait for the root process and record its exit code."""
        if self.returncode is not None:
            return
        if timeout is None:
            milliseconds = _INFINITE
        else:
            milliseconds = max(0, int(timeout * 1000))
        result = self._api.WaitForSingleObject(self._process, milliseconds)
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("contained process", timeout)
        if result != _WAIT_OBJECT_0:
            _check(False, "wait for contained process")
        code = wintypes.DWORD()
        _check(
            self._api.GetExitCodeProcess(self._process, ctypes.byref(code)),
            "read contained process exit code",
        )
        # WaitForSingleObject already signaled, so 259 is a valid exit code,
        # not the STILL_ACTIVE state.
        self.returncode = int(code.value)

    def wait(self, timeout: float | None = None) -> int:
        """Wait for root exit, then require the whole job to be empty.

        If the root exits while descendants remain, the job is terminated and
        an error is raised.  A bare root exit is never reported as tree stop.

        Raises:
            subprocess.TimeoutExpired: root did not exit within *timeout*.
            WindowsJobError: descendants remained or containment could not be
                queried.  Cleanup is attempted first.
        """
        self._wait_root(timeout)
        try:
            active = self.active_processes
        except WindowsJobError as exc:
            raise WindowsJobError(
                "cannot verify contained process tree is empty"
            ) from exc
        if active:
            try:
                self.terminate_tree(self.cleanup_timeout)
            except WindowsJobError as exc:
                raise WindowsJobError(
                    f"root exited with {active} descendant(s) and cleanup failed"
                ) from exc
            raise WindowsJobError(
                f"root exited with {active} descendant(s); job terminated them"
            )
        self._stopped = True
        return self.returncode if self.returncode is not None else 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        """Wait for the contained tree and return empty output buffers.

        The prompt has already been written to the private stdin file before
        the process was resumed.  *input* is accepted for Popen compatibility
        but is not written again.
        """
        del input
        self.wait(timeout)
        return b"", b""

    def poll(self) -> int | None:
        """Return root exit code without claiming descendants have stopped."""
        if self.returncode is not None:
            return self.returncode
        if self._closed:
            return None
        result = self._api.WaitForSingleObject(self._process, 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result != _WAIT_OBJECT_0:
            _check(False, "poll contained process")
        code = wintypes.DWORD()
        _check(
            self._api.GetExitCodeProcess(self._process, ctypes.byref(code)),
            "read contained process exit code",
        )
        self.returncode = int(code.value)
        return self.returncode

    def terminate_tree(self, timeout: float | None = None) -> None:
        """Terminate the job and wait boundedly until it is empty.

        Raises:
            WindowsJobCleanupTimeout: processes remained live at the deadline.
                The caller must keep ``child_pid`` and fail closed.
            WindowsJobError: Windows rejected termination or accounting.
        """
        if self._closed:
            if not self._stopped:
                raise WindowsJobError("contained process is already closed")
            return
        deadline = time.monotonic() + (
            self.cleanup_timeout if timeout is None else timeout
        )
        try:
            active = self.active_processes
        except WindowsJobError:
            active = 1
        if active:
            if not self._api.TerminateJobObject(self._job, 1):
                # It may have exited between accounting and termination.
                try:
                    if _active_processes(self._api, self._job) == 0:
                        active = 0
                    else:
                        _check(False, "terminate job object")
                except WindowsJobError:
                    _check(False, "terminate job object")
        while True:
            try:
                active = _active_processes(self._api, self._job)
            except WindowsJobError:
                raise
            if active == 0:
                self._stopped = True
                return
            if time.monotonic() >= deadline:
                raise WindowsJobCleanupTimeout(
                    f"job still has {active} active process(es) after "
                    f"{timeout if timeout is not None else self.cleanup_timeout:g}s"
                )
            time.sleep(0.01)

    def close(self) -> None:
        """Close owned handles and remove the private stdin file.

        Closing the job handle is itself a kill-on-close action.  This method
        does not mark a tree stopped unless :attr:`tree_stopped` was already
        proved by a successful wait or termination, and does not release a
        bound AppContainer profile while that proof is missing.

        Raises:
            KeyboardInterrupt: the caller interrupted a ``CloseHandle`` call.
                Remaining handles and the stdin file are still released first;
                the failure stays sticky and the profile binding is kept.
            SystemExit: same as ``KeyboardInterrupt`` for an exit request.
            WindowsJobError: a ``CloseHandle`` call failed.  The object stays
                closed to avoid double-closing unknown handles, and later calls
                keep reporting the failure instead of success.
        """
        if self._close_failed:
            raise WindowsJobError("contained process close previously failed")
        if self._closed:
            return
        self._closed = True
        # Pessimistic until every step below is proven, so an interrupt during
        # cleanup cannot turn into a later false success.
        self._close_failed = True
        failed: list[str] = []
        interrupted: BaseException | None = None
        for name in ("_thread", "_process", "_job"):
            handle = getattr(self, name)
            if handle:
                try:
                    if not self._api.CloseHandle(handle):
                        failed.append(name.lstrip("_"))
                except (KeyboardInterrupt, SystemExit) as exc:
                    # Keep the signal's own type; the handle state is unknown,
                    # so it is not closed a second time.
                    if interrupted is None:
                        interrupted = exc
                    failed.append(name.lstrip("_"))
                except BaseException:
                    failed.append(name.lstrip("_"))
                setattr(self, name, wintypes.HANDLE(0))
        if not _unlink_with_retry(self._stdin_path):
            failed.append("stdin")
        if interrupted is not None:
            if failed and hasattr(interrupted, "add_note"):
                interrupted.add_note(
                    "close contained process: " + ", ".join(failed)
                )
            raise interrupted
        if failed:
            raise WindowsJobError(
                "close contained process failed: " + ", ".join(failed)
            )
        container = self._app_container
        if container is not None:
            container._release_bound(self)
            self._app_container = None
        self._close_failed = False
