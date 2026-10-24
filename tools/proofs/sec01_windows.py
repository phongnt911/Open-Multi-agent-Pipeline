"""SEC-01 Windows AppContainer proof harness.

This tool is deliberately independent of the OMP production runner.  It creates
a per-run AppContainer profile, launches a real Windows child with
``PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES``, and records what the OS allowed
and denied.  The fixture is temporary and owned by this proof; no repository or
user credential is read or modified.

The proof chooses AppContainer/lowbox as a *candidate* boundary.  It does not
integrate that boundary into OMP and it does not turn AppContainer into a
general sandbox claim.  Job Object is used only for bounded process cleanup.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import http.server
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence
import traceback

SCHEMA = "omp.sec01.windows-proof.v1"
PROFILE_PREFIX = "OMP.SEC01.Proof."
CHECK_NAMES = ("source", "runtime", "vault", "config", "audit", "credentials")

if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
else:  # pragma: no cover - only exercised by import on non-Windows CI
    kernel32 = advapi32 = userenv = ole32 = None


class ProofError(RuntimeError):
    """A native setup or proof step failed closed."""


class ProofUnsupported(ProofError):
    """The host does not expose a prerequisite for this proof."""


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
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
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


TOKEN_QUERY = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
TOKEN_USER = 1
TOKEN_PRIVILEGES_INFORMATION = 3
TOKEN_ELEVATION_INFORMATION = 20
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_IS_APP_CONTAINER = 29
TOKEN_CAPABILITIES = 30
TOKEN_APPCONTAINER_SID = 31
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
OBJECT_INHERIT_ACE = 0x1
CONTAINER_INHERIT_ACE = 0x2
FILE_ALL_ACCESS = 0x1F01FF
FILE_GENERIC_READ = 0x00120089
FILE_GENERIC_EXECUTE = 0x001200A0
ACL_REVISION = 2
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
OWNED_PROCESS_CLEANUP_TIMEOUT_MS = 5000
SYNCHRONIZE = 0x00100000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009


def _last_error() -> OSError:
    error = ctypes.get_last_error()
    return OSError(error, ctypes.FormatError(error))


def _check(ok: bool, operation: str) -> None:
    if not ok:
        raise ProofError(f"{operation} failed: {ctypes.FormatError(ctypes.get_last_error())}")


def _prototypes() -> None:
    """Declare Win32 prototypes once; wrong ctypes defaults can corrupt pointers."""
    if os.name != "nt":
        return
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.GetHandleInformation.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.GetHandleInformation.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.LookupPrivilegeNameW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(LUID), wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.LookupPrivilegeNameW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAceEx.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
    ]
    advapi32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.GetAppContainerFolderPath.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)
    ]
    userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None


def _sid_string(sid: int) -> str:
    out = wintypes.LPWSTR()
    _check(bool(advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out))),
           "ConvertSidToStringSidW")
    try:
        return out.value or ""
    finally:
        kernel32.LocalFree(out)


def _token_buffer(token: int, information_class: int) -> ctypes.Array[ctypes.c_char]:
    size = wintypes.DWORD(0)
    advapi32.GetTokenInformation(token, information_class, None, 0, ctypes.byref(size))
    error = ctypes.get_last_error()
    if error not in (0, 122):
        raise ProofError(f"GetTokenInformation({information_class}) size failed: {ctypes.FormatError(error)}")
    if size.value == 0:
        raise ProofError(f"GetTokenInformation({information_class}) returned an empty buffer")
    data = ctypes.create_string_buffer(size.value)
    _check(bool(advapi32.GetTokenInformation(
        token, information_class, data, size, ctypes.byref(size)
    )), f"GetTokenInformation({information_class})")
    return data


def _sid_from_blob(blob: ctypes.Array[ctypes.c_char]) -> int | None:
    value = ctypes.cast(blob, ctypes.POINTER(SID_AND_ATTRIBUTES)).contents.Sid
    return int(value) if value else None


def _token_privileges(token: int) -> list[dict[str, str]]:
    blob = _token_buffer(token, TOKEN_PRIVILEGES_INFORMATION)
    count = ctypes.cast(blob, ctypes.POINTER(wintypes.DWORD)).contents.value
    base = ctypes.addressof(blob) + ctypes.sizeof(wintypes.DWORD)
    output: list[dict[str, str]] = []
    for index in range(count):
        item = ctypes.cast(
            base + index * ctypes.sizeof(LUID_AND_ATTRIBUTES),
            ctypes.POINTER(LUID_AND_ATTRIBUTES),
        ).contents
        name = ctypes.create_unicode_buffer(256)
        size = wintypes.DWORD(len(name))
        if not advapi32.LookupPrivilegeNameW(None, ctypes.byref(item.Luid), name, ctypes.byref(size)):
            raise _last_error()
        output.append({
            "name": name.value,
            "attributes": f"0x{item.Attributes:08x}",
        })
    return output


def _token_groups_count(token: int, information_class: int) -> int:
    blob = _token_buffer(token, information_class)
    return int(ctypes.cast(blob, ctypes.POINTER(wintypes.DWORD)).contents.value)


def _sid_text(sid: int | None) -> str | None:
    return _sid_string(sid) if sid is not None else None


def _token_info(process_id: int) -> dict[str, Any]:
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    _check(bool(process), f"OpenProcess({process_id})")
    try:
        token = wintypes.HANDLE()
        _check(bool(advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token))),
               "OpenProcessToken")
        try:
            is_container = wintypes.DWORD()
            _check(bool(advapi32.GetTokenInformation(
                token, TOKEN_IS_APP_CONTAINER, ctypes.byref(is_container),
                ctypes.sizeof(is_container), ctypes.byref(wintypes.DWORD(0)),
            )), "TokenIsAppContainer")
            user_blob = _token_buffer(token, TOKEN_USER)
            container_blob = _token_buffer(token, TOKEN_APPCONTAINER_SID)
            integrity_blob = _token_buffer(token, TOKEN_INTEGRITY_LEVEL)
            user_sid = _sid_from_blob(user_blob)
            container_sid = _sid_from_blob(container_blob)
            integrity_sid = _sid_from_blob(integrity_blob)
            elevation = TOKEN_ELEVATION()
            _check(bool(advapi32.GetTokenInformation(
                token, TOKEN_ELEVATION_INFORMATION, ctypes.byref(elevation),
                ctypes.sizeof(elevation), ctypes.byref(wintypes.DWORD(0)),
            )), "TokenElevation")
            user_text = _sid_text(user_sid)
            container_text = _sid_text(container_sid)
            integrity_text = _sid_text(integrity_sid)
            return {
                "is_app_container": bool(is_container.value),
                "user_sid": user_text,
                "app_container_sid": container_text,
                "integrity_sid": integrity_text,
                "integrity_level": (
                    "low" if integrity_text and integrity_text.endswith("-4096") else "other"
                ),
                "elevated": bool(elevation.TokenIsElevated),
                "privileges": _token_privileges(token),
                "capability_count": _token_groups_count(token, TOKEN_CAPABILITIES),
            }
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


def _current_token_summary() -> dict[str, Any]:
    process = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    _check(bool(advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token))),
           "OpenProcessToken(current)")
    try:
        user_blob = _token_buffer(token, TOKEN_USER)
        integrity_blob = _token_buffer(token, TOKEN_INTEGRITY_LEVEL)
        user_sid = _sid_from_blob(user_blob)
        integrity_sid = _sid_from_blob(integrity_blob)
        elevation = TOKEN_ELEVATION()
        _check(bool(advapi32.GetTokenInformation(
            token, TOKEN_ELEVATION_INFORMATION, ctypes.byref(elevation),
            ctypes.sizeof(elevation), ctypes.byref(wintypes.DWORD(0)),
        )), "TokenElevation(current)")
        return {
            "user_sid": _sid_text(user_sid),
            "integrity_sid": _sid_text(integrity_sid),
            "elevated": bool(elevation.TokenIsElevated),
            "privileges": _token_privileges(token),
        }
    finally:
        kernel32.CloseHandle(token)


def _current_user_sid() -> tuple[ctypes.Array[ctypes.c_char], int]:
    """Return the current user's SID pointer and the buffer that owns it.

    The caller must keep the returned buffer alive while the SID pointer is in
    use; the pointer addresses memory inside the buffer.
    """
    process = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    _check(bool(advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token))),
           "OpenProcessToken(current)")
    try:
        blob = _token_buffer(token, TOKEN_USER)
        sid = _sid_from_blob(blob)
        if sid is None:
            raise ProofError("current user token has no user SID")
        return blob, sid
    finally:
        kernel32.CloseHandle(token)


def _sid_length(sid: int) -> int:
    pointer = ctypes.cast(ctypes.c_void_p(sid), ctypes.POINTER(ctypes.c_ubyte))
    return 8 + 4 * int(pointer[1])


def _set_dacl(path: Path, entries: Sequence[tuple[int, int, int]]) -> None:
    """Set a protected DACL.  Entries are (SID pointer, access mask, inherit flags)."""
    size = 8
    for sid, _mask, _flags in entries:
        size += 8 + _sid_length(sid)
    buffer = ctypes.create_string_buffer(max(size, 256))
    _check(bool(advapi32.InitializeAcl(buffer, ctypes.sizeof(buffer), ACL_REVISION)),
           "InitializeAcl")
    for sid, mask, flags in entries:
        _check(bool(advapi32.AddAccessAllowedAceEx(
            buffer, ACL_REVISION, flags, mask, ctypes.c_void_p(sid)
        )), "AddAccessAllowedAceEx")
    result = advapi32.SetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, ctypes.cast(buffer, ctypes.c_void_p), None,
    )
    if result:
        raise ProofError(f"SetNamedSecurityInfoW({path}) failed: {ctypes.FormatError(result)}")


def _environment_block(values: Mapping[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    text = "".join(f"{key}={value}\0" for key, value in sorted(values.items())) + "\0"
    block = (ctypes.c_wchar * (len(text) + 1))()
    for index, char in enumerate(text):
        block[index] = char
    return block


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"<unreadable: {exc}>"


def _create_job() -> int:
    job = kernel32.CreateJobObjectW(None, None)
    _check(bool(job), "CreateJobObjectW")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    _check(bool(kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
        ctypes.sizeof(info),
    )), "SetInformationJobObject")
    return int(job)


def _job_active_processes(job: int) -> int:
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    returned = wintypes.DWORD()
    _check(bool(kernel32.QueryInformationJobObject(
        ctypes.c_void_p(job), JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
    )), "QueryInformationJobObject")
    return int(info.ActiveProcesses)


def _attribute_list(container_sid: int) -> tuple[ctypes.Array[ctypes.c_char], Any]:
    size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    error = ctypes.get_last_error()
    if error not in (0, 122):
        raise ProofError(f"InitializeProcThreadAttributeList size failed: {ctypes.FormatError(error)}")
    buffer = ctypes.create_string_buffer(size.value)
    _check(bool(kernel32.InitializeProcThreadAttributeList(
        buffer, 1, 0, ctypes.byref(size)
    )), "InitializeProcThreadAttributeList")
    capabilities = SECURITY_CAPABILITIES(ctypes.c_void_p(container_sid), None, 0, 0)
    _check(bool(kernel32.UpdateProcThreadAttribute(
        buffer, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
        ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None,
    )), "UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
    return buffer, capabilities


def _start_http_probe() -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            requests.append(self.path)
            body = b"SEC01-PROBE-OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.requests = requests  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="sec01-http-probe", daemon=True)
    thread.start()
    return server, thread


def _stop_http_probe(server: http.server.ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _terminate_process_bounded(
    process: int,
    timeout_ms: int = OWNED_PROCESS_CLEANUP_TIMEOUT_MS,
) -> list[str]:
    """Terminate an owned process and wait for it within a fixed bound.

    The helper never raises: callers must preserve the original exception.
    It returns human-readable cleanup errors so a test or caller can report
    that the bounded wait was not satisfied.
    """
    errors: list[str] = []
    if not process:
        return ["owned process handle is missing"]
    try:
        kernel32.TerminateProcess(ctypes.c_void_p(process), 1)
    except BaseException as exc:
        errors.append(f"TerminateProcess failed: {type(exc).__name__}: {exc}")
    try:
        wait = int(kernel32.WaitForSingleObject(ctypes.c_void_p(process), timeout_ms))
    except BaseException as exc:
        errors.append(f"WaitForSingleObject failed: {type(exc).__name__}: {exc}")
        return errors
    if wait == WAIT_TIMEOUT:
        errors.append(f"owned process did not exit within {timeout_ms} ms")
    elif wait != WAIT_OBJECT_0:
        errors.append(
            f"owned process wait failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    return errors


def _close_owned_handles(thread: int, process: int) -> list[str]:
    """Close an owned thread/process pair, returning errors instead of raising."""
    errors: list[str] = []
    for label, handle in (("thread", thread), ("process", process)):
        if not handle:
            continue
        try:
            if not kernel32.CloseHandle(ctypes.c_void_p(handle)):
                error = ctypes.get_last_error()
                if error:
                    errors.append(
                        f"CloseHandle({label}) failed: {ctypes.FormatError(error)}"
                    )
        except BaseException as exc:
            errors.append(f"CloseHandle({label}) failed: {type(exc).__name__}: {exc}")
    return errors


def _terminate_and_close_owned_process(
    process: int,
    thread: int,
    timeout_ms: int = OWNED_PROCESS_CLEANUP_TIMEOUT_MS,
) -> list[str]:
    """Best-effort terminate/wait/close for a process created by this proof."""
    errors = _terminate_process_bounded(process, timeout_ms)
    errors.extend(_close_owned_handles(thread, process))
    return errors


def _add_cleanup_note(exc: BaseException, errors: list[str]) -> None:
    """Attach cleanup diagnostics without replacing the original exception."""
    if not errors:
        return
    note = "SEC-01 owned-process cleanup: " + "; ".join(errors)
    if hasattr(exc, "add_note"):
        exc.add_note(note)


def _create_process(
    *,
    application: str,
    command_line: str,
    current_directory: str,
    env_values: Mapping[str, str],
    attributes: ctypes.Array[ctypes.c_char] | None,
    job: int | None,
    suspended: bool,
) -> dict[str, Any]:
    """Create a child, inspect its token, and optionally attach it to a job.

    The child is always created suspended so token inspection and job
    assignment happen before any Worker code runs.
    """
    startup = STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(startup)
    if attributes is not None:
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
    process_info = PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(command_line)
    environment = _environment_block(env_values)
    flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
    if suspended:
        flags |= CREATE_SUSPENDED
    # Ownership starts before the native call: an interrupt can be raised at the
    # CreateProcessW return boundary after the OS has created the child but
    # before ``ok``/``result`` exist. The raw PROCESS_INFORMATION already holds
    # the owned handles, so the except branch below must use it directly.
    try:
        ok = kernel32.CreateProcessW(
            application, command, None, None, False, flags,
            ctypes.cast(environment, ctypes.c_void_p), current_directory,
            ctypes.byref(startup), ctypes.byref(process_info),
        )
        create_error = ctypes.get_last_error()
        if not ok:
            raise ProofError(
                f"CreateProcessW({application}) failed: {ctypes.FormatError(create_error)}"
            )
        result: dict[str, Any] = {
            "command": command_line,
            "pid": int(process_info.dwProcessId),
            "process": int(process_info.hProcess),
            "thread": int(process_info.hThread),
            "token": None,
            "assigned_to_job": False,
        }
        result["token"] = _token_info(result["pid"])
        if job is not None:
            _check(bool(kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(job), process_info.hProcess
            )), "AssignProcessToJobObject")
            in_job = wintypes.BOOL()
            _check(bool(kernel32.IsProcessInJob(
                process_info.hProcess, ctypes.c_void_p(job), ctypes.byref(in_job)
            )), "IsProcessInJob")
            result["assigned_to_job"] = bool(in_job.value)
        return result
    except BaseException as exc:
        # Covers interrupts at the native return boundary, token-query/job-attach
        # failures and any other exit after CreateProcessW created the child.
        # Read the raw structure: it is populated by the native call even when
        # the caller never reached the ``ok``/``result`` assignments.
        owned_process = int(process_info.hProcess or 0)
        owned_thread = int(process_info.hThread or 0)
        if owned_process or owned_thread:
            _add_cleanup_note(
                exc,
                _terminate_and_close_owned_process(owned_process, owned_thread),
            )
        raise


def _finish_process(child: Mapping[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Resume a suspended child, wait for it within a bound, and close handles."""
    process = wintypes.HANDLE(child["process"])
    thread = wintypes.HANDLE(child["thread"])
    started = time.monotonic()
    try:
        if kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            raise _last_error()
        wait = kernel32.WaitForSingleObject(process, int(timeout_seconds * 1000))
        timed_out = wait == WAIT_TIMEOUT
        if timed_out:
            kernel32.TerminateProcess(process, 1)
            kernel32.WaitForSingleObject(process, 5000)
        elif wait != WAIT_OBJECT_0:
            raise ProofError(
                f"WaitForSingleObject failed: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        code = wintypes.DWORD()
        _check(bool(kernel32.GetExitCodeProcess(process, ctypes.byref(code))),
               "GetExitCodeProcess")
        return {
            "command": child["command"],
            "pid": child["pid"],
            "token": child["token"],
            "assigned_to_job": child["assigned_to_job"],
            "exit_code": int(code.value),
            "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except BaseException as exc:
        # Resume/wait/get-exit failures and interrupts must stop the owned child
        # within a bound before the finally block closes its handles.
        _add_cleanup_note(exc, _terminate_process_bounded(child["process"]))
        raise
    finally:
        _close_owned_handles(child["thread"], child["process"])


def _launch(
    *,
    application: str,
    command_line: str,
    current_directory: str,
    env_values: Mapping[str, str],
    attributes: ctypes.Array[ctypes.c_char] | None,
    job: int | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    child = _create_process(
        application=application, command_line=command_line,
        current_directory=current_directory, env_values=env_values,
        attributes=attributes, job=job, suspended=True,
    )
    return _finish_process(child, timeout_seconds)


def _cmd_command(command: str) -> tuple[str, str]:
    comspec = os.environ.get("ComSpec", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                                     "System32", "cmd.exe"))
    return comspec, f'"{comspec}" /d /c {command}'


def _run_cmd(
    *,
    command: str,
    current_directory: str,
    env_values: Mapping[str, str],
    attributes: ctypes.Array[ctypes.c_char] | None,
    job: int | None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    comspec, command_line = _cmd_command(command)
    return _launch(
        application=comspec, command_line=command_line,
        current_directory=current_directory, env_values=env_values,
        attributes=attributes, job=job, timeout_seconds=timeout_seconds,
    )


def _write_fixture(root: Path, user_sid: int, container_sid: int) -> dict[str, Any]:
    allowed = root / "allowed"
    sensitive = {name: root / name for name in CHECK_NAMES}
    allowed.mkdir()
    for path in sensitive.values():
        path.mkdir()
    # The proof root may be traversed by the container, but its children decide
    # their own access.  No inherited ACE from the profile folder survives.
    _set_dacl(root, [
        (user_sid, FILE_ALL_ACCESS, 0),
        (container_sid, FILE_GENERIC_READ | FILE_GENERIC_EXECUTE, 0),
    ])
    _set_dacl(allowed, [
        (user_sid, FILE_ALL_ACCESS, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE),
        (container_sid, FILE_ALL_ACCESS, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE),
    ])
    for path in sensitive.values():
        _set_dacl(path, [
            (user_sid, FILE_ALL_ACCESS, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE),
        ])

    values: dict[str, str] = {}
    for name, path in sensitive.items():
        token = f"FAKE-{name.upper()}-{secrets.token_hex(8)}"
        values[name] = token
        target = path / f"{name}-canary.txt"
        target.write_text(token + "\n", encoding="utf-8")
    allowed_value = f"FAKE-ALLOWED-{secrets.token_hex(8)}"
    values["allowed"] = allowed_value
    (allowed / "allowed-canary.txt").write_text(allowed_value + "\n", encoding="utf-8")
    return {
        "root": root,
        "allowed": allowed,
        "sensitive": sensitive,
        "values": values,
        "before": {
            name: _file_hash(path / f"{name}-canary.txt")
            for name, path in sensitive.items()
        } | {"allowed": _file_hash(allowed / "allowed-canary.txt")},
    }


def _allowed_entry(
    *,
    command: str,
    result: Mapping[str, Any],
    output_path: Path,
    expected: str,
) -> dict[str, Any]:
    text = _read_text(output_path)
    matches = text.strip() == expected.strip()
    return {
        "command": command,
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "output": text[:2000],
        "expected": expected,
        "content_matches": matches,
        "ok": result["exit_code"] == 0 and not result["timed_out"] and matches,
    }


def _denied_entry(
    *,
    name: str,
    path: Path,
    command: str,
    result: Mapping[str, Any],
    output_path: Path,
    canary: str,
    hash_before: str,
    hash_after: str,
) -> dict[str, Any]:
    text = _read_text(output_path)
    text_denied = "Access is denied" in text
    unchanged = hash_before == hash_after
    return {
        "asset": name,
        "path": str(path),
        "command": command,
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "output": text[:2000],
        "access_denied": text_denied or (result["exit_code"] not in (0, None) and unchanged),
        "access_denied_text": text_denied,
        "canary_visible": canary in text,
        "hash_before": hash_before,
        "hash_after": hash_after,
        "hash_unchanged": unchanged,
    }


def _environment_values(
    *, folder: Path, allowed: Path, allowed_value: str
) -> dict[str, str]:
    comspec = os.environ.get(
        "ComSpec",
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"),
    )
    return {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "windir": os.environ.get("windir", r"C:\Windows"),
        "ComSpec": comspec,
        "PATH": r"C:\Windows\System32;C:\Windows",
        "TEMP": str(allowed),
        "TMP": str(allowed),
        "LOCALAPPDATA": str(folder),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
        "HOMEPATH": os.environ.get("HOMEPATH", ""),
        "USERNAME": os.environ.get("USERNAME", ""),
        "USERDOMAIN": os.environ.get("USERDOMAIN", ""),
        "OMP_SEC01_ALLOWED": allowed_value,
        "=C:": r"C:\Windows\System32",
    }


def _parse_environment(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key:
            values[key] = value
    return values


def _probe_network(
    *,
    url: str,
    allowed: Path,
    attributes: ctypes.Array[ctypes.c_char],
    job: int,
    env_values: Mapping[str, str],
) -> dict[str, Any]:
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    if not curl.exists():
        return {
            "status": "NOT PROVEN",
            "reason": "curl.exe is not available to issue a bounded loopback probe",
            "external_endpoint_contacted": False,
        }
    output = allowed / "network-worker.txt"
    body = allowed / "network-body.txt"
    command = (
        f'curl.exe --max-time 3 --silent --show-error '
        f'--output "{body}" "{url}" > "{output}" 2>&1'
    )
    try:
        result = _run_cmd(
            command=command,
            current_directory=r"C:\Windows\System32",
            env_values=env_values,
            attributes=attributes,
            job=job,
            timeout_seconds=8,
        )
    except ProofError as exc:
        return {
            "status": "NOT PROVEN",
            "reason": str(exc),
            "url": url,
            "external_endpoint_contacted": False,
        }
    text = _read_text(output)
    body_text = _read_text(body)
    return {
        "status": "NOT PROVEN",
        "reason": (
            "Only a bounded loopback probe was attempted; general network "
            "isolation is not established and no external endpoint was contacted."
        ),
        "url": url,
        "curl_present": True,
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "output": text[:2000],
        "body": body_text[:200],
        "loopback_reachable": result["exit_code"] == 0 and "SEC01-PROBE-OK" in body_text,
        "external_endpoint_contacted": False,
        "token": result["token"],
    }


def _prove_job_kill(
    *,
    job: int,
    env_values: Mapping[str, str],
    attributes: ctypes.Array[ctypes.c_char],
) -> dict[str, Any]:
    """Close the only job handle and prove KILL_ON_JOB_CLOSE terminates a child."""
    comspec, command_line = _cmd_command("ping -n 30 127.0.0.1 >nul")
    child = _create_process(
        application=comspec,
        command_line=command_line,
        current_directory=r"C:\Windows\System32",
        env_values=env_values,
        attributes=attributes,
        job=job,
        suspended=True,
    )
    process = wintypes.HANDLE(child["process"])
    thread = wintypes.HANDLE(child["thread"])
    started = time.monotonic()
    try:
        _check(bool(kernel32.CloseHandle(ctypes.c_void_p(job))), "CloseHandle(job)")
        wait = kernel32.WaitForSingleObject(process, 5000)
        terminated = wait == WAIT_OBJECT_0
        code = wintypes.DWORD()
        if terminated:
            kernel32.GetExitCodeProcess(process, ctypes.byref(code))
        return {
            "command": child["command"],
            "assigned_before_resume": child["assigned_to_job"],
            "wait_result": int(wait),
            "wait_within_timeout": terminated,
            "process_terminated": terminated,
            "exit_code": int(code.value) if terminated else None,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        kernel32.CloseHandle(thread)
        kernel32.CloseHandle(process)


def _cleanup(
    *,
    fixture_root: Path | None,
    profile_name: str,
    profile_created: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fixture_root": str(fixture_root) if fixture_root is not None else None,
        "fixture_removed": fixture_root is None,
        "profile_deleted": False,
        "profile_hresult": None,
        "errors": [],
    }
    if fixture_root is not None:
        try:
            if fixture_root.exists():
                shutil.rmtree(fixture_root)
            result["fixture_removed"] = not fixture_root.exists()
        except OSError as exc:
            result["errors"].append(f"fixture removal: {exc}")
    if profile_created:
        hresult = int(userenv.DeleteAppContainerProfile(profile_name))
        result["profile_hresult"] = hresult
        result["profile_deleted"] = hresult == 0
        if hresult != 0:
            result["errors"].append(f"DeleteAppContainerProfile HRESULT={hresult}")
    result["ok"] = (
        result["fixture_removed"]
        and (not profile_created or result["profile_deleted"])
        and not result["errors"]
    )
    return result


def _properties() -> dict[str, dict[str, str]]:
    return {
        "worker_identity": {
            "status": "PROVEN",
            "detail": "The Worker token is an AppContainer token with the proof profile SID, low integrity, no capabilities, and the same user SID as the parent.",
        },
        "fixture_read_denial": {
            "status": "PROVEN",
            "detail": "The protected DACL denied reads of every sensitive fixture directory to the AppContainer token.",
        },
        "fixture_write_denial": {
            "status": "PROVEN",
            "detail": "Write attempts against every sensitive canary failed and all canary hashes stayed unchanged.",
        },
        "fixture_allowed_access": {
            "status": "PROVEN",
            "detail": "The same AppContainer Worker could read and write the explicitly allowed fixture directory.",
        },
        "same_user_control": {
            "status": "PROVEN",
            "detail": "A same-user, non-AppContainer child could read and write the same fixture, so the denials are not a global ACL failure.",
        },
        "job_lifecycle": {
            "status": "PROVEN",
            "detail": "The proof-owned Job Object held the Worker before resume and KILL_ON_JOB_CLOSE terminated a suspended child within five seconds.",
        },
        "real_source_runtime_vault": {
            "status": "NOT PROVEN",
            "detail": "The proof used temporary fake directories; it did not protect the real OMP repository, runtime, vault, config, or audit files.",
        },
        "real_credentials": {
            "status": "NOT PROVEN",
            "detail": "No real credential store, token, cookie, or secret was read; the credentials directory contains only a fake canary.",
        },
        "network": {
            "status": "NOT PROVEN",
            "detail": "Only a bounded loopback probe was attempted; general network isolation and capability policy are not established.",
        },
        "inherited_handles": {
            "status": "NOT PROVEN",
            "detail": "The proof creates children with bInheritHandles=FALSE but does not exercise arbitrary inherited-handle leakage.",
        },
        "production_worker_compatibility": {
            "status": "NOT PROVEN",
            "detail": "The proof ran cmd.exe and curl.exe, not the real OMP Worker CLI or a provider runtime.",
        },
        "omp_integration": {
            "status": "NOT PROVEN",
            "detail": "OMP does not launch Workers with AppContainer; the production runner and recovery path are unchanged.",
        },
    }


def _limitations() -> list[dict[str, str]]:
    return [
        {
            "id": "real-assets",
            "status": "NOT PROVEN",
            "reason": "Temporary fake canaries stand in for the real OMP repository, runtime, vault, config, and audit files.",
        },
        {
            "id": "credentials",
            "status": "NOT PROVEN",
            "reason": "No real credential store or provider secret was accessed; the credentials fixture is fake.",
        },
        {
            "id": "network",
            "status": "NOT PROVEN",
            "reason": "A loopback probe is not a general network-isolation proof; no external endpoint was contacted.",
        },
        {
            "id": "handles",
            "status": "NOT PROVEN",
            "reason": "bInheritHandles=FALSE is used, but inherited-handle leakage is not exercised.",
        },
        {
            "id": "production-worker",
            "status": "NOT PROVEN",
            "reason": "The proof used system cmd.exe and curl.exe, not the OMP Worker CLI, Python runtime, or a real provider.",
        },
        {
            "id": "omp-integration",
            "status": "NOT PROVEN",
            "reason": "No production runner, recovery, vault, or policy code was changed or exercised under AppContainer.",
        },
        {
            "id": "acl-installation",
            "status": "NOT PROVEN",
            "reason": "The proof configures fixture DACLs itself; it does not prove OMP can install or maintain such ACLs for real assets.",
        },
    ]


def run_proof() -> tuple[dict[str, Any], int]:
    if os.name != "nt":
        return {
            "schema": SCHEMA,
            "verdict": "UNSUPPORTED",
            "reason": "SEC-01 proof is Windows-only; Linux cannot substitute.",
        }, 2
    _prototypes()
    profile_name = PROFILE_PREFIX + secrets.token_hex(8)
    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate": "Windows AppContainer (lowbox) launched with SECURITY_CAPABILITIES",
        "host": {
            "platform": platform.platform(),
            "windows_build": platform.version(),
            "machine": platform.machine(),
            "python": sys.version,
            "python_executable": sys.executable,
        },
        "principal": None,
        "profile": {"name": profile_name, "sid": None, "folder": None},
        "fixture": {},
        "controls": {},
        "workers": {},
        "properties": _properties(),
        "lifecycle": {},
        "cleanup": {},
        "limitations": _limitations(),
    }
    profile_sid = ctypes.c_void_p()
    profile_created = False
    job = 0
    fixture_root: Path | None = None
    fixture: dict[str, Any] | None = None
    attributes: tuple[ctypes.Array[ctypes.c_char], Any] | None = None
    server: http.server.ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    parent_secret_name = "OMP_SEC01_PARENT_SECRET"
    parent_secret_value = "PARENT-" + secrets.token_hex(8)
    saved_parent_secret = os.environ.get(parent_secret_name)
    proof_ok = False
    try:
        evidence["principal"] = _current_token_summary()
        user_sid_blob, user_sid = _current_user_sid()
        _check(userenv.CreateAppContainerProfile(
            profile_name, profile_name, "OMP SEC-01 temporary proof",
            None, 0, ctypes.byref(profile_sid),
        ) == 0, "CreateAppContainerProfile")
        profile_created = True
        if not profile_sid.value:
            raise ProofError("CreateAppContainerProfile returned a NULL SID")
        profile_sid_value = int(profile_sid.value)
        profile_folder = wintypes.LPWSTR()
        _check(userenv.GetAppContainerFolderPath(
            _sid_string(profile_sid_value), ctypes.byref(profile_folder)
        ) == 0, "GetAppContainerFolderPath")
        folder = Path(profile_folder.value or "")
        ole32.CoTaskMemFree(profile_folder)
        if not folder.is_dir():
            raise ProofError(f"AppContainer profile folder is missing: {folder}")
        evidence["profile"].update({
            "sid": _sid_string(profile_sid_value),
            "folder": str(folder),
        })
        attributes = _attribute_list(profile_sid_value)
        job = _create_job()
        evidence["lifecycle"]["job_created"] = True
        fixture_root = Path(tempfile.mkdtemp(prefix="proof-", dir=str(folder)))
        fixture = _write_fixture(fixture_root, user_sid, profile_sid_value)
        allowed = fixture["allowed"]
        sensitive = fixture["sensitive"]
        values = fixture["values"]
        evidence["fixture"] = {
            "root": str(fixture_root),
            "allowed": str(allowed),
            "sensitive": {name: str(path) for name, path in sensitive.items()},
            "canary_hashes_before": fixture["before"],
        }
        env_values = _environment_values(
            folder=folder, allowed=allowed, allowed_value=values["allowed"]
        )
        os.environ[parent_secret_name] = parent_secret_value

        control: dict[str, Any] = {"allowed": {}, "sensitive": {}}
        control_read_out = allowed / "control-allowed-read.out"
        command = f'type "{allowed / "allowed-canary.txt"}" > "{control_read_out}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed), env_values=env_values,
            attributes=None, job=job,
        )
        control["token"] = result["token"]
        control["allowed"]["read"] = _allowed_entry(
            command=command, result=result, output_path=control_read_out,
            expected=values["allowed"],
        )
        control_write = allowed / "control-allowed-write.txt"
        command = f'echo CONTROL-WRITE-OK > "{control_write}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed), env_values=env_values,
            attributes=None, job=job,
        )
        control["allowed"]["write"] = _allowed_entry(
            command=command, result=result, output_path=control_write,
            expected="CONTROL-WRITE-OK",
        )
        for name, path in sensitive.items():
            canary_path = path / f"{name}-canary.txt"
            control_read_out = allowed / f"control-{name}-read.out"
            command = f'type "{canary_path}" > "{control_read_out}" 2>&1'
            result = _run_cmd(
                command=command, current_directory=str(allowed),
                env_values=env_values, attributes=None, job=job,
            )
            control["sensitive"].setdefault(name, {})["read"] = _allowed_entry(
                command=command, result=result, output_path=control_read_out,
                expected=values[name],
            )
            control_write = path / "control-write.txt"
            command = f'echo CONTROL-WRITE-OK > "{control_write}" 2>&1'
            result = _run_cmd(
                command=command, current_directory=str(allowed),
                env_values=env_values, attributes=None, job=job,
            )
            entry = _allowed_entry(
                command=command, result=result, output_path=control_write,
                expected="CONTROL-WRITE-OK",
            )
            entry["path"] = str(control_write)
            control["sensitive"][name]["write"] = entry
            try:
                control_write.unlink()
            except OSError:
                pass
        evidence["controls"]["same_user"] = control

        identity_out = allowed / "worker-whoami.out"
        command = (
            f'whoami /user > "{identity_out}" 2>&1 '
            f'& whoami /groups >> "{identity_out}" 2>&1'
        )
        result = _run_cmd(
            command=command, current_directory=str(allowed),
            env_values=env_values, attributes=attributes[0], job=job,
        )
        identity_text = _read_text(identity_out)
        evidence["workers"]["identity"] = {
            "command": command,
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
            "assigned_to_job": result["assigned_to_job"],
            "output": identity_text[:4000],
            "user_sid_in_output": evidence["principal"]["user_sid"] in identity_text,
            "low_integrity_in_output": "Low Mandatory Level" in identity_text,
        }
        evidence["workers"]["token"] = result["token"]

        allowed_read_out = allowed / "worker-allowed-read.out"
        command = f'type "{allowed / "allowed-canary.txt"}" > "{allowed_read_out}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed),
            env_values=env_values, attributes=attributes[0], job=job,
        )
        evidence["workers"]["allowed"] = {
            "read": _allowed_entry(
                command=command, result=result, output_path=allowed_read_out,
                expected=values["allowed"],
            ),
        }
        allowed_write = allowed / "worker-write.txt"
        command = f'echo WORKER-WRITE-OK > "{allowed_write}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed),
            env_values=env_values, attributes=attributes[0], job=job,
        )
        evidence["workers"]["allowed"]["write"] = _allowed_entry(
            command=command, result=result, output_path=allowed_write,
            expected="WORKER-WRITE-OK",
        )

        ready_path = allowed / "worker-ready.txt"
        command = f'echo SEC01-READY > "{ready_path}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed),
            env_values=env_values, attributes=attributes[0], job=job,
        )
        evidence["workers"]["ready"] = _allowed_entry(
            command=command, result=result, output_path=ready_path,
            expected="SEC01-READY",
        )
        evidence["workers"]["ready"]["assigned_to_job"] = result["assigned_to_job"]

        sensitive_evidence: dict[str, Any] = {}
        for name, path in sensitive.items():
            canary_path = path / f"{name}-canary.txt"
            canary = values[name]
            before = _file_hash(canary_path)
            read_out = allowed / f"worker-{name}-read.out"
            command = f'type "{canary_path}" > "{read_out}" 2>&1'
            result = _run_cmd(
                command=command, current_directory=str(allowed),
                env_values=env_values, attributes=attributes[0], job=job,
            )
            after_read = _file_hash(canary_path)
            read_entry = _denied_entry(
                name=name, path=canary_path, command=command, result=result,
                output_path=read_out, canary=canary, hash_before=before,
                hash_after=after_read,
            )
            write_err = allowed / f"worker-{name}-write.err"
            # Group the failing redirection so cmd writes its own OS error to
            # the error file; a bare ``> file 2> err`` loses that text because
            # the first redirection fails before stderr is redirected.
            command = f'(echo TAMPER > "{canary_path}") 2> "{write_err}"'
            result = _run_cmd(
                command=command, current_directory=str(allowed),
                env_values=env_values, attributes=attributes[0], job=job,
            )
            after_write = _file_hash(canary_path)
            write_entry = _denied_entry(
                name=name, path=canary_path, command=command, result=result,
                output_path=write_err, canary=canary, hash_before=before,
                hash_after=after_write,
            )
            sensitive_evidence[name] = {
                "read": read_entry,
                "write": write_entry,
                "hash_unchanged": before == after_write,
            }
        evidence["workers"]["sensitive"] = sensitive_evidence

        env_out = allowed / "worker-env.out"
        command = f'set > "{env_out}" 2>&1'
        result = _run_cmd(
            command=command, current_directory=str(allowed),
            env_values=env_values, attributes=attributes[0], job=job,
        )
        env_text = _read_text(env_out)
        env_map = _parse_environment(env_text)
        evidence["workers"]["environment"] = {
            "command": command,
            "exit_code": result["exit_code"],
            "keys": sorted(env_map),
            "parent_secret_name": parent_secret_name,
            "parent_secret_visible": parent_secret_name in env_map,
            "proof_value_visible": env_map.get("OMP_SEC01_ALLOWED") == values["allowed"],
            "explicit_block": "OMP_SEC01_ALLOWED" in env_map,
            "output": env_text[:4000],
        }

        server, server_thread = _start_http_probe()
        port = int(server.server_address[1])
        url = f"http://127.0.0.1:{port}/probe"
        network = _probe_network(
            url=url, allowed=allowed, attributes=attributes[0], job=job,
            env_values=env_values,
        )
        network["parent_requests_seen"] = len(server.requests)  # type: ignore[attr-defined]
        evidence["workers"]["network"] = network

        evidence["lifecycle"]["main"] = {
            "assigned_before_resume": evidence["workers"]["identity"]["assigned_to_job"],
            "active_processes_after_exit": _job_active_processes(job),
        }
        evidence["lifecycle"]["cleanup_kill"] = _prove_job_kill(
            job=job, env_values=env_values, attributes=attributes[0],
        )
        job = 0
        proof_ok = True
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        evidence["traceback"] = traceback.format_exc()
    finally:
        os.environ.pop(parent_secret_name, None)
        if saved_parent_secret is not None:
            os.environ[parent_secret_name] = saved_parent_secret
        if server is not None:
            try:
                _stop_http_probe(server, server_thread)  # type: ignore[arg-type]
            except Exception as exc:
                evidence.setdefault("cleanup_errors", []).append(f"http probe stop: {exc}")
        if job:
            try:
                kernel32.TerminateJobObject(ctypes.c_void_p(job), 1)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(ctypes.c_void_p(job))
            except Exception:
                pass
        if attributes is not None:
            try:
                kernel32.DeleteProcThreadAttributeList(
                    ctypes.cast(attributes[0], ctypes.c_void_p)
                )
            except Exception:
                pass
        evidence["cleanup"] = _cleanup(
            fixture_root=fixture_root,
            profile_name=profile_name,
            profile_created=profile_created,
        )
        if not evidence["cleanup"]["ok"]:
            proof_ok = False
            evidence["error"] = (
                f"{evidence['error']}; cleanup failed"
                if evidence.get("error") else "cleanup failed"
            )
    evidence["verdict"] = "PASS" if proof_ok else "FAILED"
    failures = verify_evidence(evidence)
    evidence["verification_failures"] = failures
    if failures:
        evidence["verdict"] = "FAILED"
        return evidence, 1
    return evidence, 0


def _value(evidence: Mapping[str, Any], *keys: str) -> Any:
    current: Any = evidence
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _is_sha256(value: Any) -> bool:
    """Return true only for a lowercase hexadecimal SHA-256 digest."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _check_success_raw(
    label: str, entry: Mapping[str, Any], failures: list[str]
) -> None:
    """Reconcile a successful access claim with its raw exit/output fields."""
    if entry.get("exit_code") != 0:
        failures.append(f"{label} exit code is not 0")
    if entry.get("timed_out") is not False:
        failures.append(f"{label} timed out or timeout flag is missing")
    if entry.get("content_matches") is not True:
        failures.append(f"{label} content does not match expected")
    output = entry.get("output")
    expected = entry.get("expected")
    if not isinstance(output, str) or not isinstance(expected, str):
        failures.append(f"{label} output or expected text is missing")
    elif output.strip() != expected.strip():
        failures.append(f"{label} output text does not match expected")


def _check_denied_hashes(
    name: str, entry: Mapping[str, Any], failures: list[str]
) -> None:
    """Reconcile raw before/after canary hashes with the unchanged claim."""
    read = entry.get("read")
    write = entry.get("write")
    if not isinstance(read, Mapping) or not isinstance(write, Mapping):
        return
    for label, item in (("read", read), ("write", write)):
        before = item.get("hash_before")
        after = item.get("hash_after")
        if not _is_sha256(before) or not _is_sha256(after):
            failures.append(f"{name} {label} hash fields are not SHA-256")
            continue
        if before != after:
            failures.append(f"{name} {label} hash_after does not match hash_before")
    hashes = [
        read.get("hash_before"),
        read.get("hash_after"),
        write.get("hash_before"),
        write.get("hash_after"),
    ]
    if all(_is_sha256(item) for item in hashes):
        if len(set(hashes)) != 1:
            failures.append(f"{name} raw canary hashes disagree")
        if entry.get("hash_unchanged") is True and len(set(hashes)) != 1:
            failures.append(f"{name} hash_unchanged claim contradicts raw hashes")
        if entry.get("hash_unchanged") is not True and len(set(hashes)) == 1:
            failures.append(f"{name} hash_unchanged is false but raw hashes are equal")


def verify_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """Return human-readable failures; an empty list means the evidence passes."""
    failures: list[str] = []
    if not isinstance(evidence, Mapping):
        return ["evidence must be a mapping"]
    if evidence.get("schema") != SCHEMA:
        failures.append(f"schema must be {SCHEMA!r}")
    if evidence.get("verdict") != "PASS":
        failures.append("verdict must be PASS")

    principal = evidence.get("principal")
    if not isinstance(principal, Mapping):
        failures.append("principal evidence is missing")
        principal = {}
    profile = evidence.get("profile")
    if not isinstance(profile, Mapping):
        failures.append("profile evidence is missing")
        profile = {}
    workers = evidence.get("workers")
    if not isinstance(workers, Mapping):
        failures.append("worker evidence is missing")
        workers = {}
    token = workers.get("token")
    if not isinstance(token, Mapping):
        failures.append("worker token evidence is missing")
        token = {}

    principal_sid = principal.get("user_sid")
    profile_sid = profile.get("sid")
    if not principal_sid:
        failures.append("principal user SID is missing")
    if token.get("is_app_container") is not True:
        failures.append("worker token is not an AppContainer token")
    if token.get("user_sid") != principal_sid:
        failures.append("worker user SID does not match the parent principal")
    if not profile_sid or token.get("app_container_sid") != profile_sid:
        failures.append("worker AppContainer SID does not match the created profile")
    if token.get("integrity_level") != "low":
        failures.append("worker token is not at low integrity")
    if token.get("integrity_level") == "low" and token.get("integrity_sid") != "S-1-16-4096":
        failures.append("worker low-integrity claim contradicts integrity_sid")
    if token.get("capability_count") != 0:
        failures.append("worker token unexpectedly has capabilities")
    if token.get("elevated") is not False:
        failures.append("worker token is elevated")
    privilege_names = {
        item.get("name")
        for item in token.get("privileges", [])
        if isinstance(item, Mapping)
    }
    for name in (
        "SeAssignPrimaryTokenPrivilege",
        "SeIncreaseQuotaPrivilege",
        "SeImpersonatePrivilege",
        "SeCreateTokenPrivilege",
    ):
        if name in privilege_names:
            failures.append(f"worker token retains forbidden privilege {name}")

    identity = workers.get("identity")
    if not isinstance(identity, Mapping):
        failures.append("worker identity evidence is missing")
    else:
        if identity.get("exit_code") != 0:
            failures.append("worker identity command did not exit 0")
        if identity.get("assigned_to_job") is not True:
            failures.append("worker identity process was not assigned to the job")
        if identity.get("user_sid_in_output") is not True:
            failures.append("worker identity output does not show the parent user SID")
        if identity.get("low_integrity_in_output") is not True:
            failures.append("worker identity output does not show low integrity")
        if identity.get("timed_out") is not False:
            failures.append("worker identity command timed out or timeout flag is missing")
        identity_output = identity.get("output")
        if not isinstance(identity_output, str):
            failures.append("worker identity raw output is missing")
        else:
            if identity.get("user_sid_in_output") is True and principal_sid not in identity_output:
                failures.append("worker identity flag contradicts raw output user SID")
            if (
                identity.get("low_integrity_in_output") is True
                and "Low Mandatory Level" not in identity_output
            ):
                failures.append("worker identity flag contradicts raw low-integrity text")

    allowed = workers.get("allowed")
    if not isinstance(allowed, Mapping):
        failures.append("allowed-fixture evidence is missing")
    else:
        for action in ("read", "write"):
            entry = allowed.get(action)
            if not isinstance(entry, Mapping) or entry.get("ok") is not True:
                failures.append(f"allowed fixture {action} did not succeed")
            if isinstance(entry, Mapping):
                _check_success_raw(f"allowed fixture {action}", entry, failures)

    ready = workers.get("ready")
    if not isinstance(ready, Mapping):
        failures.append("worker readiness marker evidence is missing")
    else:
        if ready.get("ok") is not True:
            failures.append("worker did not write the readiness marker")
        if ready.get("assigned_to_job") is not True:
            failures.append("readiness marker worker was not assigned to the job")
        _check_success_raw("worker readiness marker", ready, failures)

    sensitive = workers.get("sensitive")
    if not isinstance(sensitive, Mapping):
        failures.append("sensitive-fixture evidence is missing")
        sensitive = {}
    for name in CHECK_NAMES:
        entry = sensitive.get(name)
        if not isinstance(entry, Mapping):
            failures.append(f"sensitive fixture {name} evidence is missing")
            continue
        for action in ("read", "write"):
            item = entry.get(action)
            if not isinstance(item, Mapping):
                failures.append(f"{name} {action} evidence is missing")
                continue
            if item.get("access_denied") is not True:
                failures.append(f"{name} {action} was not denied by the OS")
            if item.get("access_denied_text") is not True:
                failures.append(f"{name} {action} did not capture an OS denial message")
            if item.get("canary_visible") is not False:
                failures.append(f"{name} {action} exposed the fake canary")
            if item.get("timed_out") is not False:
                failures.append(f"{name} {action} timed out")
            if item.get("exit_code") in (0, None):
                failures.append(f"{name} {action} unexpectedly exited successfully")
        if entry.get("hash_unchanged") is not True:
            failures.append(f"{name} canary changed after the write attempt")
        _check_denied_hashes(name, entry, failures)

    control = _value(evidence, "controls", "same_user")
    if not isinstance(control, Mapping):
        failures.append("same-user control evidence is missing")
    else:
        control_token = control.get("token")
        if not isinstance(control_token, Mapping) or control_token.get("is_app_container") is not False:
            failures.append("same-user control is missing a non-AppContainer token")
        if isinstance(control_token, Mapping):
            if control_token.get("user_sid") != principal_sid:
                failures.append(
                    "same-user control user SID does not match the parent principal"
                )
            if control_token.get("elevated") is not False:
                failures.append("same-user control token is elevated")
        control_allowed = control.get("allowed")
        if not isinstance(control_allowed, Mapping):
            failures.append("same-user control allowed-fixture evidence is missing")
        else:
            for action in ("read", "write"):
                item = control_allowed.get(action)
                if not isinstance(item, Mapping) or item.get("ok") is not True:
                    failures.append(f"same-user control allowed fixture {action} failed")
                if isinstance(item, Mapping):
                    _check_success_raw(
                        f"same-user control allowed fixture {action}", item, failures
                    )
        control_sensitive = control.get("sensitive")
        if not isinstance(control_sensitive, Mapping):
            failures.append("same-user control sensitive-fixture evidence is missing")
        else:
            for name in CHECK_NAMES:
                item = control_sensitive.get(name)
                if not isinstance(item, Mapping):
                    failures.append(f"same-user control {name} evidence is missing")
                    continue
                for action in ("read", "write"):
                    entry = item.get(action)
                    if not isinstance(entry, Mapping) or entry.get("ok") is not True:
                        failures.append(f"same-user control {name} {action} failed")
                    if isinstance(entry, Mapping):
                        _check_success_raw(
                            f"same-user control {name} {action}", entry, failures
                        )

    environment = workers.get("environment")
    if not isinstance(environment, Mapping):
        failures.append("worker environment evidence is missing")
    else:
        if environment.get("parent_secret_visible") is not False:
            failures.append("parent environment secret leaked into the worker")
        if environment.get("proof_value_visible") is not True:
            failures.append("explicit proof environment value was not visible")
        if environment.get("explicit_block") is not True:
            failures.append("worker environment was not built from the explicit block")
        parent_name = environment.get("parent_secret_name")
        keys = environment.get("keys")
        if isinstance(parent_name, str) and isinstance(keys, list) and parent_name in keys:
            failures.append("parent environment secret name appears in the worker environment")
        environment_output = environment.get("output")
        if not isinstance(environment_output, str):
            failures.append("worker environment raw output is missing")
        else:
            if isinstance(parent_name, str) and parent_name in environment_output:
                failures.append("parent environment secret name appears in raw output")
            if (
                environment.get("proof_value_visible") is True
                and "OMP_SEC01_ALLOWED=" not in environment_output
            ):
                failures.append("proof environment value flag contradicts raw output")

    network = workers.get("network")
    if not isinstance(network, Mapping):
        failures.append("network evidence is missing")
    else:
        if network.get("status") != "NOT PROVEN":
            failures.append("network must remain NOT PROVEN")
        if network.get("external_endpoint_contacted") is not False:
            failures.append("network probe contacted an external endpoint")

    lifecycle = evidence.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        failures.append("lifecycle evidence is missing")
    else:
        if lifecycle.get("job_created") is not True:
            failures.append("proof-owned job was not created")
        main = lifecycle.get("main")
        if not isinstance(main, Mapping):
            failures.append("main worker lifecycle evidence is missing")
        else:
            if main.get("assigned_before_resume") is not True:
                failures.append("main worker was not assigned to the job before resume")
            if main.get("active_processes_after_exit") != 0:
                failures.append("job still has active processes after the main worker exited")
        kill = lifecycle.get("cleanup_kill")
        if not isinstance(kill, Mapping):
            failures.append("cleanup-kill lifecycle evidence is missing")
        else:
            if kill.get("assigned_before_resume") is not True:
                failures.append("cleanup-kill child was not assigned before resume")
            if kill.get("wait_within_timeout") is not True:
                failures.append("cleanup-kill child did not terminate within the bound")
            if kill.get("process_terminated") is not True:
                failures.append("cleanup-kill child was not terminated")
            if kill.get("process_terminated") is True and kill.get("wait_result") != WAIT_OBJECT_0:
                failures.append("cleanup-kill terminated claim contradicts wait_result")

    cleanup = evidence.get("cleanup")
    if not isinstance(cleanup, Mapping):
        failures.append("cleanup evidence is missing")
    else:
        if cleanup.get("fixture_removed") is not True:
            failures.append("fixture directory was not removed")
        if cleanup.get("profile_deleted") is not True:
            failures.append("AppContainer profile was not deleted")
        if cleanup.get("ok") is not True:
            failures.append("cleanup did not complete cleanly")
        if cleanup.get("ok") is True and cleanup.get("errors") != []:
            failures.append("cleanup ok claim contradicts non-empty errors")
        if (
            cleanup.get("profile_deleted") is True
            and cleanup.get("profile_hresult") != 0
        ):
            failures.append("profile_deleted claim contradicts profile_hresult")

    properties = evidence.get("properties")
    if not isinstance(properties, Mapping):
        failures.append("property status map is missing")
    else:
        expected = {
            "worker_identity": "PROVEN",
            "fixture_read_denial": "PROVEN",
            "fixture_write_denial": "PROVEN",
            "fixture_allowed_access": "PROVEN",
            "same_user_control": "PROVEN",
            "job_lifecycle": "PROVEN",
            "real_source_runtime_vault": "NOT PROVEN",
            "real_credentials": "NOT PROVEN",
            "network": "NOT PROVEN",
            "inherited_handles": "NOT PROVEN",
            "production_worker_compatibility": "NOT PROVEN",
            "omp_integration": "NOT PROVEN",
        }
        for key, status in expected.items():
            item = properties.get(key)
            if not isinstance(item, Mapping) or item.get("status") != status:
                failures.append(f"property {key} must be {status}")

    limitations = evidence.get("limitations")
    if not isinstance(limitations, list):
        failures.append("limitations list is missing")
        limitations = []
    required_ids = {
        "real-assets",
        "credentials",
        "network",
        "handles",
        "production-worker",
        "omp-integration",
        "acl-installation",
    }
    seen_ids = set()
    for item in limitations:
        if not isinstance(item, Mapping):
            failures.append("limitation entry is not a mapping")
            continue
        seen_ids.add(item.get("id"))
        if item.get("status") != "NOT PROVEN":
            failures.append(f"limitation {item.get('id')} must be NOT PROVEN")
    missing = sorted(required_ids - seen_ids)
    if missing:
        failures.append(f"missing limitations: {', '.join(missing)}")

    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write evidence JSON to this path")
    parser.add_argument("--verify", type=Path, help="verify an existing evidence JSON file")
    args = parser.parse_args(argv)
    if args.verify is not None:
        try:
            data = json.loads(args.verify.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read evidence: {exc}", file=sys.stderr)
            return 2
        if not isinstance(data, dict):
            print("evidence root must be an object", file=sys.stderr)
            return 2
        failures = verify_evidence(data)
        print(json.dumps(
            {"ok": not failures, "failures": failures}, indent=2, ensure_ascii=False
        ))
        return 0 if not failures else 1
    evidence, code = run_proof()
    text = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"cannot write evidence: {exc}", file=sys.stderr)
            return 2
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
