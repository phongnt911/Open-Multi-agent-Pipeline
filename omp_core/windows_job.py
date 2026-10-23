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
    return api


def _check(ok: object, stage: str) -> None:
    """Raise a redacted WindowsJobError when a Win32 call reports failure."""
    if ok:
        return
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip()
    raise WindowsJobError(f"{stage} failed (WinError {code}: {detail})")


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


def _unlink_with_retry(path: Path, attempts: int = 50) -> None:
    """Delete a private stdin file after child handles close.

    Windows can report a sharing violation briefly after the last job process
    exits.  The retry is bounded and best effort; a leftover file stays under
    the run directory and is never committed.
    """
    for attempt in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                return
            time.sleep(0.01)


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
                    handles: Sequence[int]) -> tuple[object, object, object]:
    """Build a two-attribute list and return its live backing objects.

    The returned tuple must stay alive until ``CreateProcessW`` returns.
    """
    size = ctypes.c_size_t()
    api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
        _check(False, "size process attribute list")
    buffer = ctypes.create_string_buffer(size.value)
    pointer = ctypes.cast(buffer, wintypes.LPVOID)
    _check(
        api.InitializeProcThreadAttributeList(pointer, 2, 0, ctypes.byref(size)),
        "initialize process attribute list",
    )
    job_handles = (wintypes.HANDLE * 1)(job)
    handle_array = (wintypes.HANDLE * len(handles))(*handles)
    try:
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
    return buffer, job_handles, handle_array


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
    ) -> None:
        self._api = api
        self._job = job
        self._process = process
        self._thread = thread
        self._stdin_path = stdin_path
        self.cleanup_timeout = cleanup_timeout
        self.pid = pid
        self.in_job = in_job
        self.returncode: int | None = None
        self._resumed = False
        self._stopped = False
        self._closed = False
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
    ) -> "WindowsJobProcess":
        """Create *argv* suspended inside a new kill-on-close Job Object.

        Raises:
            WindowsJobError: any Win32 setup, inheritance, or job-membership
                check failed.  No process is left running on a raised error.
            ValueError: argv is empty or contains NUL.
        """
        if os.name != "nt":
            raise WindowsJobError("Windows Job Objects require Windows")
        if not argv:
            raise ValueError("argv must not be empty")
        if any("\0" in arg for arg in argv):
            raise ValueError("argv must not contain NUL")
        api = _kernel32()
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

            attributes = _attribute_list(
                api, job, (stdin_handle, log_handle)
            )
            buffer, job_handles, handle_array = attributes
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
            _check(
                api.CreateProcessW(
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
                ),
                "create contained process",
            )
            process = info.hProcess
            thread = info.hThread
            # Keep the arrays alive through CreateProcessW.
            del job_handles, handle_array
            in_job = wintypes.BOOL()
            _check(
                api.IsProcessInJob(process, job, ctypes.byref(in_job)),
                "verify job membership",
            )
            if not in_job.value:
                raise WindowsJobError("created process is not in the job")
            return cls(
                api=api,
                job=job,
                process=process,
                thread=thread,
                pid=int(info.dwProcessId),
                stdin_path=stdin_path,
                cleanup_timeout=cleanup_timeout,
                in_job=bool(in_job.value),
            )
        except BaseException:
            if process is not None:
                api.TerminateProcess(process, 1)
                api.WaitForSingleObject(process, 5000)
            if thread is not None:
                api.CloseHandle(thread)
            if process is not None:
                api.CloseHandle(process)
            if job is not None:
                api.CloseHandle(job)
            if stdin_fd is not None:
                try:
                    os.close(stdin_fd)
                except OSError:
                    pass
                stdin_fd = None
            if log_fd is not None:
                try:
                    os.close(log_fd)
                except OSError:
                    pass
                log_fd = None
            if stdin_path is not None:
                _unlink_with_retry(stdin_path)
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
        proved by a successful wait or termination.
        """
        if self._closed:
            return
        self._closed = True
        for name in ("_thread", "_process", "_job"):
            handle = getattr(self, name)
            if handle:
                self._api.CloseHandle(handle)
                setattr(self, name, wintypes.HANDLE(0))
        _unlink_with_retry(self._stdin_path)
