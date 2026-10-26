"""SEC-02 B0 integration probe: combined containment, layout and recovery evidence.

This tool is the design checkpoint between the accepted Phase A proof and any
production integration.  It is standalone: it never imports ``omp_core`` and it
does not modify the repository.  It answers five questions with native evidence:

1. Can ``SECURITY_CAPABILITIES``, ``JOB_LIST`` and ``HANDLE_LIST`` be applied in
   one ``CreateProcessW`` call, with the child suspended, the Job membership
   observable before resume, and a simulated PID checkpoint saved before resume?
2. Do both CPython Workers (3.12 and 3.14) run lowbox/isolated with an empty
   capability set, an explicit environment allowlist and no fake secret?
3. Which workspace layout is reachable: L1 (workspace inside the AppContainer
   profile) or L2 (workspace shaped like ``.omp/runs/<run>/checkout`` outside the
   profile), while the trusted vault/state stay denied to the Worker?
4. Are explicit stdio handles usable while a fake protected handle outside the
   handle allowlist is not inherited, and are no extra privileges added?
5. Do injected attribute/create/save/resume/query faults and a root early-exit
   with a live descendant end in bounded, owned cleanup with no replacement?

Evidence schema: ``omp.sec02.b0.integration-proof.v1``.  This is a probe schema,
not a production state/config version.  The CLI is::

    python -m tools.proofs.sec02_integration_windows run --out <json>
    python -m tools.proofs.sec02_integration_windows verify <json>

``run`` exits 0 only when every required property is proven.  A missing
prerequisite is reported as BLOCKED and exits nonzero; it is never upgraded to
PASS.  ``verify`` re-derives every property from raw fields and rejects a report
whose stored claims do not match the raw evidence.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

from tools.proofs import sec01_windows as sec01
from tools.proofs import sec02_windows as sec02

SCHEMA = "omp.sec02.b0.integration-proof.v1"
PROFILE_PREFIX = "OMP.SEC02.B0."
PROPERTY_IDS = ("B0-2", "B0-3", "B0-4", "B0-5", "B0-6", "B0-7")
RUNTIME_KEYS = ("3.12", "3.14")
ATTRIBUTE_NAMES = ("SECURITY_CAPABILITIES", "JOB_LIST", "HANDLE_LIST")
HANDLE_ALLOWLIST = ("stdin", "stdout", "stderr")
CANARY_NAMES = ("vault", "state")
FAULT_SITES = ("attribute", "create", "save", "resume", "query")
STOP_DEADLINE_SECONDS = 5.0
CHILD_TIMEOUT_SECONDS = 10.0
LOW_INTEGRITY = "S-1-16-4096"
MEDIUM_INTEGRITY = "S-1-16-8192"
SAFE_PRIVILEGES = frozenset({"SeChangeNotifyPrivilege"})
NOT_PROVEN = (
    "network isolation is not exercised",
    "owner hard-kill and power-loss recovery are not exercised",
    "real credentials, providers and arbitrary pytest plugins are not exercised",
    "machine-wide ACL/label/policy changes are out of scope",
)
SHA256_REGEX_LENGTH = 64

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
HANDLE_FLAG_INHERIT = 0x00000001
STARTF_USESTDHANDLES = 0x00000100
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x00000080
DUPLICATE_SAME_ACCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_INSUFFICIENT_BUFFER = 122
LABEL_SECURITY_INFORMATION = 0x00000010
SE_DACL_PROTECTED = 0x0004
ACL_SIZE_INFORMATION_CLASS = 2
ACCESS_ALLOWED_ACE_TYPE = 0x0
ACCESS_DENIED_ACE_TYPE = 0x1
SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11

_WORKER_SCRIPT = """import ctypes, json, os, sys

def attempt(call):
    try:
        return {"ok": True, "value": call()}
    except BaseException as exc:
        return {"ok": False, "error": type(exc).__name__}

def probe_handle(raw):
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetHandleInformation.restype = wintypes.BOOL
    k32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD
    ]
    k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    flags = wintypes.DWORD()
    # An invalid handle can surface as a raised Windows exception instead of a
    # FALSE return, so both outcomes must mean "not usable".
    try:
        if not k32.GetHandleInformation(ctypes.c_void_p(raw), ctypes.byref(flags)):
            return {"valid": False, "path": "", "error": ctypes.get_last_error()}
        buffer = ctypes.create_unicode_buffer(1024)
        length = k32.GetFinalPathNameByHandleW(ctypes.c_void_p(raw), buffer, 1024, 0)
    except BaseException as exc:
        return {"valid": False, "path": "", "error": type(exc).__name__}
    return {"valid": True, "path": buffer.value if length else "", "error": 0}

env = {key: value for key, value in os.environ.items() if key.startswith("OMP_")}
vault = os.environ["OMP_SEC02_VAULT_CANARY"]
state = os.environ["OMP_SEC02_STATE_CANARY"]
result = {
    "env": env,
    "read": attempt(lambda: open("allowed_input.txt", "r", encoding="utf-8").read()),
    "write": attempt(lambda: open("allowed_output.txt", "w", encoding="utf-8").write("B0-WORKER-WROTE")),
    "vault_read": attempt(lambda: open(vault, "r", encoding="utf-8").read()),
    "vault_write": attempt(lambda: open(vault, "a", encoding="utf-8").write("x")),
    "state_read": attempt(lambda: open(state, "r", encoding="utf-8").read()),
    "state_write": attempt(lambda: open(state, "a", encoding="utf-8").write("x")),
    "handle": probe_handle(int(os.environ["OMP_SEC02_PROTECTED_HANDLE"])),
    "flags": {
        "isolated": sys.flags.isolated,
        "safe_path": sys.flags.safe_path,
        "version": sys.version,
    },
}
with open("worker_result.json", "w", encoding="utf-8") as handle:
    json.dump(result, handle)
print("B0-WORKER-STDOUT", flush=True)
with open("ready.json", "w", encoding="utf-8") as handle:
    handle.write("READY")
"""

_DESCENDANT_SCRIPT = """import os, sys, time

os.spawnv(os.P_NOWAIT, sys.executable, [sys.executable, "-I", "-P", "descendant_child.py"])
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    if os.path.exists("descendant_ready.txt"):
        break
    time.sleep(0.05)
with open("root_exit.json", "w", encoding="utf-8") as handle:
    handle.write("ROOT-EXITING")
"""

_DESCENDANT_CHILD_SCRIPT = """import time

with open("descendant_ready.txt", "w", encoding="utf-8") as handle:
    handle.write("READY")
time.sleep(5)
"""


class B0ProofError(RuntimeError):
    """A native B0 probe step failed closed."""


class InjectedFault(B0ProofError):
    """A deliberate fault used to prove cleanup and no-replacement behavior."""


class LaunchBlocked(B0ProofError):
    """A launch was refused because the probe is in a stopped state."""


class StopState:
    """Tracks whether a fault has permanently stopped further Worker launches."""

    def __init__(self) -> None:
        self.stopped = False


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


def _ensure_prototypes() -> None:
    """Declare the extra Win32 prototypes used only by this probe."""
    sec01._prototypes()
    if os.name != "nt":
        return
    kernel32 = sec01.kernel32
    advapi32 = sec01.advapi32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetHandleInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD
    ]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorSacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorSacl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
    ]
    advapi32.GetAce.restype = wintypes.BOOL


def _check(ok: bool, operation: str) -> None:
    if not ok:
        raise B0ProofError(
            f"{operation} failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_REGEX_LENGTH
        and all(char in "0123456789abcdefABCDEF" for char in value)
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _enabled_privilege(privilege: Mapping[str, Any]) -> bool:
    """Return True when a token privilege has SE_PRIVILEGE_ENABLED set."""
    try:
        return bool(int(str(privilege.get("attributes", "0")), 16) & 0x2)
    except (TypeError, ValueError):
        return True


def redact(text: str, secrets: Sequence[str] = (), paths: Sequence[str] = ()) -> str:
    """Replace secret values and owned paths in a diagnostic string.

    Cleanup and fault diagnostics must not leak argv paths or fake secrets, so
    every native failure message passes through this helper before evidence.
    """
    cleaned = text
    for value in sorted((item for item in secrets if item), key=len, reverse=True):
        cleaned = cleaned.replace(value, "<redacted>")
    for value in sorted((item for item in paths if item), key=len, reverse=True):
        cleaned = cleaned.replace(value, "<redacted>")
        cleaned = cleaned.replace(value.replace("\\", "/"), "<redacted>")
    return cleaned


def bounded_stop(
    active: Callable[[], int],
    terminate: Callable[[], bool],
    deadline_seconds: float = STOP_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Terminate and reap an owned Job within a fixed deadline.

    Returns ``ok`` only when termination was requested successfully and the Job
    reported zero active processes before the deadline.  It never raises; errors
    are returned so a caller can keep the original failure as the cause.
    """
    errors: list[str] = []
    try:
        if not bool(terminate()):
            errors.append("TerminateJobObject returned FALSE")
    except BaseException as exc:
        errors.append(f"TerminateJobObject error: {type(exc).__name__}")
    deadline = time.monotonic() + max(0.0, deadline_seconds)
    stopped = False
    try:
        while True:
            if active() == 0:
                stopped = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    except BaseException as exc:
        errors.append(f"active-process query error: {type(exc).__name__}")
    if not stopped:
        errors.append("Job still has active processes after deadline")
    return {"ok": stopped and not errors, "stopped": stopped, "errors": errors}


def run_fault_case(
    *,
    site: str,
    launch: Callable[[str], None],
    cleanup: Callable[[], Mapping[str, Any]],
    stop_state: StopState,
    secrets: Sequence[str] = (),
    paths: Sequence[str] = (),
) -> dict[str, Any]:
    """Run one fault site and record whether cleanup ran and replacement stopped.

    ``launch`` is expected to raise :class:`InjectedFault` for ``site`` and
    :class:`LaunchBlocked` once ``stop_state`` is stopped.  Any other failure is
    recorded as an unreached or unsafe case rather than hidden.
    """
    started = time.monotonic()
    reached = False
    error = ""
    try:
        launch(site)
    except InjectedFault as exc:
        reached = True
        error = redact(str(exc), secrets, paths)
    except BaseException as exc:
        error = redact(f"{type(exc).__name__}: {exc}", secrets, paths)
    stop_state.stopped = True
    try:
        observed = dict(cleanup())
    except BaseException as exc:
        observed = {"cleanup_errors": [f"cleanup error: {type(exc).__name__}"]}
    replacement_blocked = False
    try:
        launch("replacement")
    except LaunchBlocked:
        replacement_blocked = True
    except BaseException:
        replacement_blocked = False
    return {
        "reached": reached,
        "error": error,
        "replacement_blocked": replacement_blocked,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "active_after_cleanup": observed.get("active_after_cleanup", -1),
        "cleanup_errors": list(observed.get("cleanup_errors", [])),
    }


class _OwnedFixture:
    """Owns the AppContainer profile, Job, temporary root and their cleanup."""

    def __init__(self) -> None:
        self.profile_name: str | None = None
        self.container_sid_ptr: int | None = None
        self.container_sid_str: str | None = None
        self.profile_folder: Path | None = None
        self.job: int | None = None
        self.temp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.cleanup_errors: list[str] = []
        self.handles_closed = True

    def allocate(self) -> None:
        """Create the fixture, releasing whatever succeeded if setup fails."""
        try:
            self._allocate()
        except BaseException as exc:
            self.cleanup()
            if self.cleanup_errors and hasattr(exc, "add_note"):
                exc.add_note("partial fixture cleanup: " + "; ".join(self.cleanup_errors))
            raise

    def _allocate(self) -> None:
        """Create the profile, profile folder, Job and temporary fixture root."""
        _ensure_prototypes()
        self.profile_name = PROFILE_PREFIX + secrets.token_hex(8)
        sid_ptr = ctypes.c_void_p()
        hr = sec01.userenv.CreateAppContainerProfile(
            self.profile_name, self.profile_name, self.profile_name, None, 0,
            ctypes.byref(sid_ptr),
        )
        if hr != 0:
            raise B0ProofError(
                f"CreateAppContainerProfile failed: 0x{hr & 0xFFFFFFFF:08x}"
            )
        self.container_sid_ptr = sid_ptr.value
        self.container_sid_str = sec01._sid_string(self.container_sid_ptr)
        folder = wintypes.LPWSTR()
        hr = sec01.userenv.GetAppContainerFolderPath(
            self.container_sid_str, ctypes.byref(folder)
        )
        if hr != 0:
            raise B0ProofError(
                f"GetAppContainerFolderPath failed: 0x{hr & 0xFFFFFFFF:08x}"
            )
        self.profile_folder = Path(folder.value or "")
        sec01.ole32.CoTaskMemFree(folder)
        self.job = sec01._create_job()
        self.temp = tempfile.TemporaryDirectory(prefix="omp-sec02-b0-")
        self.root = Path(self.temp.name)

    def cleanup(self) -> None:
        """Bounded cleanup of owned resources in reverse allocation order."""
        if self.job is not None:
            result = bounded_stop(
                lambda: sec01._job_active_processes(self.job or 0),
                lambda: bool(sec01.kernel32.TerminateJobObject(
                    ctypes.c_void_p(self.job), 1
                )),
            )
            self.cleanup_errors.extend(result["errors"])
            try:
                if not sec01.kernel32.CloseHandle(ctypes.c_void_p(self.job)):
                    self.cleanup_errors.append("CloseHandle(job) returned FALSE")
                    self.handles_closed = False
                # A FALSE close leaves the handle in an unknown state; clear it so
                # a second cleanup pass cannot double-close it.
                self.job = None
            except BaseException as exc:
                self.cleanup_errors.append(f"CloseHandle(job) error: {type(exc).__name__}")
                self.handles_closed = False
                self.job = None
        if self.temp is not None:
            try:
                self.temp.cleanup()
                self.temp = None
                self.root = None
            except BaseException as exc:
                self.cleanup_errors.append(f"fixture cleanup error: {type(exc).__name__}")
        if self.container_sid_ptr is not None:
            try:
                # FreeSid returns NULL on success; a non-NULL result is a failure.
                if sec01.advapi32.FreeSid(ctypes.c_void_p(self.container_sid_ptr)):
                    self.cleanup_errors.append("FreeSid returned non-NULL")
                # Clear either way: retrying an unknown SID pointer risks a
                # double free, and the recorded error already fails the run.
                self.container_sid_ptr = None
            except BaseException as exc:
                self.cleanup_errors.append(f"FreeSid error: {type(exc).__name__}")
                self.container_sid_ptr = None
        if self.profile_name is not None:
            try:
                hr = sec01.userenv.DeleteAppContainerProfile(self.profile_name)
                if hr != 0:
                    self.cleanup_errors.append(
                        f"DeleteAppContainerProfile failed: 0x{hr & 0xFFFFFFFF:08x}"
                    )
                else:
                    self.profile_name = None
            except BaseException as exc:
                self.cleanup_errors.append(
                    f"DeleteAppContainerProfile error: {type(exc).__name__}"
                )


def _prepare_runtime(src_exe: Path, target_dir: Path) -> Path:
    """Copy a minimal CPython runtime that can import ``ctypes`` for handle probes."""
    target_exe = sec02.prepare_minimal_runtime(src_exe, target_dir)
    src_dir = src_exe.parent
    if not (src_dir / "Lib").is_dir():
        src_dir = src_dir.parent
    target_lib = target_dir / "Lib"
    target_dlls = target_dir / "DLLs"
    target_dlls.mkdir(parents=True, exist_ok=True)
    for package in ("ctypes", "sysconfig"):
        source_dir = src_dir / "Lib" / package
        source_file = src_dir / "Lib" / f"{package}.py"
        if source_dir.is_dir():
            shutil.copytree(source_dir, target_lib / package, dirs_exist_ok=True)
        elif source_file.is_file():
            shutil.copy2(source_file, target_lib / f"{package}.py")
    for name in ("_ctypes.pyd", "libffi-8.dll", "_struct.pyd"):
        source = src_dir / "DLLs" / name
        if source.is_file():
            shutil.copy2(source, target_dlls / name)
    return target_exe


def _create_file(path: str, access: int, creation: int) -> int:
    handle = sec01.kernel32.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, creation,
        FILE_ATTRIBUTE_NORMAL, None,
    )
    if not handle or int(handle) == INVALID_HANDLE_VALUE:
        raise B0ProofError(
            f"CreateFileW({path}) failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    return int(handle)


def _duplicate_handle(handle: int) -> int:
    """Duplicate one owned handle as inheritable, or raise with the Win32 error."""
    duplicated = wintypes.HANDLE()
    _check(
        bool(sec01.kernel32.DuplicateHandle(
            sec01.kernel32.GetCurrentProcess(), wintypes.HANDLE(handle),
            sec01.kernel32.GetCurrentProcess(), ctypes.byref(duplicated),
            0, True, DUPLICATE_SAME_ACCESS,
        )),
        "DuplicateHandle(stderr)",
    )
    return int(duplicated.value)


def _set_handle_inherit(handle: int) -> None:
    """Mark one owned handle inheritable, or raise with the Win32 error."""
    _check(
        bool(sec01.kernel32.SetHandleInformation(
            wintypes.HANDLE(handle), HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT
        )),
        "SetHandleInformation",
    )


def _handles_closed(cleanup_errors: Sequence[str]) -> bool:
    """Return True only when no handle close failure was recorded."""
    return not any(error.startswith("CloseHandle(") for error in cleanup_errors)


def _open_protected_handle(path: Path) -> int:
    """Open the protected canary as inheritable, closing it if setup fails."""
    handle = _create_file(str(path), GENERIC_READ, OPEN_EXISTING)
    try:
        _set_handle_inherit(handle)
    except BaseException as exc:
        try:
            if sec01.kernel32.CloseHandle(wintypes.HANDLE(handle)):
                note = ""
            else:
                note = "CloseHandle(protected) returned FALSE"
        except BaseException as close_exc:
            note = f"CloseHandle(protected) error: {type(close_exc).__name__}"
        if note and hasattr(exc, "add_note"):
            exc.add_note(note)
        raise
    return handle


def _open_stdio_handles(log_path: Path) -> dict[str, int]:
    """Open inheritable NUL/log handles that are explicitly passed to the child.

    Ownership starts at the first successful allocation: any later failure
    closes every handle created so far before the error propagates.
    """
    handles: dict[str, int] = {}
    try:
        handles["stdin"] = _create_file("NUL", GENERIC_READ | GENERIC_WRITE, OPEN_EXISTING)
        handles["stdout"] = _create_file(str(log_path), GENERIC_WRITE, CREATE_ALWAYS)
        # stderr is a duplicate of stdout so both share one file pointer and the
        # child's diagnostics cannot overwrite earlier output.
        handles["stderr"] = _duplicate_handle(handles["stdout"])
        for handle in handles.values():
            _set_handle_inherit(handle)
        return handles
    except BaseException as exc:
        errors: list[str] = []
        _close_stdio_handles(handles, errors)
        if errors and hasattr(exc, "add_note"):
            exc.add_note("partial stdio cleanup: " + "; ".join(errors))
        raise


def _close_stdio_handles(handles: Mapping[str, int], errors: list[str]) -> None:
    for label, handle in handles.items():
        try:
            if not sec01.kernel32.CloseHandle(wintypes.HANDLE(handle)):
                errors.append(f"CloseHandle({label}) returned FALSE")
        except BaseException as exc:
            errors.append(f"CloseHandle({label}) error: {type(exc).__name__}")


def _handle_inheritable(handle: int) -> bool | None:
    flags = wintypes.DWORD()
    if not sec01.kernel32.GetHandleInformation(
        wintypes.HANDLE(handle), ctypes.byref(flags)
    ):
        return None
    return bool(flags.value & HANDLE_FLAG_INHERIT)


def _combined_attribute_list(
    container_sid: int, job: int, handles: Sequence[int]
) -> tuple[Any, tuple[Any, ...]]:
    """Build one attribute list carrying security capabilities, Job and handles.

    Returns the list buffer and the objects that must stay alive until
    ``CreateProcessW`` returns.  Any failure after the list exists deletes it
    before raising, so a partial attribute list is never leaked.
    """
    kernel32 = sec01.kernel32
    count = 3
    size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
    error = ctypes.get_last_error()
    if error not in (0, ERROR_INSUFFICIENT_BUFFER) or not size.value:
        raise B0ProofError(
            f"InitializeProcThreadAttributeList size failed: {ctypes.FormatError(error)}"
        )
    buffer = ctypes.create_string_buffer(size.value)
    _check(
        bool(kernel32.InitializeProcThreadAttributeList(
            buffer, count, 0, ctypes.byref(size)
        )),
        "InitializeProcThreadAttributeList",
    )
    capabilities = sec01.SECURITY_CAPABILITIES(ctypes.c_void_p(container_sid), None, 0, 0)
    job_handle = wintypes.HANDLE(job)
    handle_array = (wintypes.HANDLE * len(handles))(
        *(wintypes.HANDLE(handle) for handle in handles)
    )
    try:
        _check(
            bool(kernel32.UpdateProcThreadAttribute(
                buffer, 0, sec01.PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None,
            )),
            "UpdateProcThreadAttribute(SECURITY_CAPABILITIES)",
        )
        _check(
            bool(kernel32.UpdateProcThreadAttribute(
                buffer, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                ctypes.byref(job_handle), ctypes.sizeof(job_handle), None, None,
            )),
            "UpdateProcThreadAttribute(JOB_LIST)",
        )
        _check(
            bool(kernel32.UpdateProcThreadAttribute(
                buffer, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handle_array, ctypes.sizeof(handle_array), None, None,
            )),
            "UpdateProcThreadAttribute(HANDLE_LIST)",
        )
    except BaseException:
        kernel32.DeleteProcThreadAttributeList(buffer)
        raise
    return buffer, (capabilities, job_handle, handle_array)


def _create_suspended(
    *,
    application: str,
    command_line: str,
    current_directory: str,
    env_values: Mapping[str, str],
    attributes: Any,
    handles: Mapping[str, int],
) -> dict[str, Any]:
    """Create the child suspended with the combined attributes and explicit stdio."""
    startup = sec01.STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(startup)
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
    startup.StartupInfo.hStdInput = wintypes.HANDLE(handles["stdin"])
    startup.StartupInfo.hStdOutput = wintypes.HANDLE(handles["stdout"])
    startup.StartupInfo.hStdError = wintypes.HANDLE(handles["stderr"])
    startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
    process_info = sec01.PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(command_line)
    environment = sec01._environment_block(env_values)
    flags = (
        sec01.EXTENDED_STARTUPINFO_PRESENT
        | sec01.CREATE_UNICODE_ENVIRONMENT
        | sec01.CREATE_SUSPENDED
    )
    try:
        ok = sec01.kernel32.CreateProcessW(
            application, command, None, None, True, flags,
            ctypes.cast(environment, ctypes.c_void_p), current_directory,
            ctypes.byref(startup), ctypes.byref(process_info),
        )
        error = ctypes.get_last_error()
        if not ok:
            raise B0ProofError(
                f"CreateProcessW({application}) failed: {ctypes.FormatError(error)}"
            )
        return {
            "process": int(process_info.hProcess),
            "thread": int(process_info.hThread),
            "pid": int(process_info.dwProcessId),
        }
    except BaseException as exc:
        process = int(process_info.hProcess or 0)
        thread = int(process_info.hThread or 0)
        if process or thread:
            errors = sec01._terminate_and_close_owned_process(process, thread)
            if errors and hasattr(exc, "add_note"):
                exc.add_note("SEC-02 B0 owned-process cleanup: " + "; ".join(errors))
        raise


def _resume(child: Mapping[str, Any]) -> None:
    if sec01.kernel32.ResumeThread(wintypes.HANDLE(child["thread"])) == 0xFFFFFFFF:
        raise B0ProofError(
            f"ResumeThread failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )


def _wait(child: Mapping[str, Any], timeout_seconds: float) -> tuple[int, bool]:
    wait = sec01.kernel32.WaitForSingleObject(
        wintypes.HANDLE(child["process"]), int(timeout_seconds * 1000)
    )
    timed_out = wait == sec01.WAIT_TIMEOUT
    if timed_out:
        sec01.kernel32.TerminateProcess(wintypes.HANDLE(child["process"]), 1)
        sec01.kernel32.WaitForSingleObject(wintypes.HANDLE(child["process"]), 5000)
    elif wait != sec01.WAIT_OBJECT_0:
        raise B0ProofError(
            f"WaitForSingleObject failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    code = wintypes.DWORD()
    _check(
        bool(sec01.kernel32.GetExitCodeProcess(
            wintypes.HANDLE(child["process"]), ctypes.byref(code)
        )),
        "GetExitCodeProcess",
    )
    return int(code.value), timed_out


def _is_in_job(process_handle: int, job: int) -> bool:
    in_job = wintypes.BOOL()
    _check(
        bool(sec01.kernel32.IsProcessInJob(
            wintypes.HANDLE(process_handle), wintypes.HANDLE(job), ctypes.byref(in_job)
        )),
        "IsProcessInJob",
    )
    return bool(in_job.value)


def _inject(fault_site: str | None, name: str) -> None:
    if fault_site == name:
        raise InjectedFault(f"injected fault at {name}")


def _launch_worker(
    *,
    runtime_exe: Path,
    script_path: Path,
    workspace: Path,
    profile_folder: Path,
    container_sid: int,
    job: int,
    stop_state: StopState,
    env_values: Mapping[str, str],
    timeout_seconds: float = CHILD_TIMEOUT_SECONDS,
    checkpoint_path: Path | None = None,
    fault_site: str | None = None,
    cleanup_log: list[str] | None = None,
) -> dict[str, Any]:
    """Launch one Worker and capture checkpoint ordering, token and Job membership.

    The child is created suspended with all three attributes at once.  The token
    and Job membership are read before resume, the simulated checkpoint is saved
    before resume, and readiness is only observed after resume.
    """
    if stop_state.stopped:
        raise LaunchBlocked("probe is stopped after a fault; replacement launch refused")
    errors: list[str] = cleanup_log if cleanup_log is not None else []
    log_path = workspace / f"stdio_{secrets.token_hex(4)}.log"
    handles = _open_stdio_handles(log_path)
    stdio_inheritable = all(_handle_inheritable(handle) is True for handle in handles.values())
    attribute_buffer: Any = None
    child: dict[str, Any] | None = None
    closed = False
    try:
        attribute_buffer, keepalive = _combined_attribute_list(
            container_sid, job, [handles["stdin"], handles["stdout"], handles["stderr"]]
        )
        _inject(fault_site, "attribute")
        command_line = f'"{runtime_exe}" -I -P "{script_path}"'
        child = _create_suspended(
            application=str(runtime_exe),
            command_line=command_line,
            current_directory=str(workspace),
            env_values=env_values,
            attributes=attribute_buffer,
            handles=handles,
        )
        _inject(fault_site, "create")
        token = sec01._token_info(child["pid"])
        in_job = _is_in_job(child["process"], job)
        ready_before = (workspace / "ready.json").is_file()
        checkpoint_saved = False
        pid_matches = False
        if checkpoint_path is not None:
            _inject(fault_site, "save")
            checkpoint_path.write_text(
                json.dumps({"child_pid": child["pid"], "saved_utc": _utc()}),
                encoding="utf-8",
            )
            checkpoint_saved = True
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            pid_matches = saved.get("child_pid") == child["pid"]
        _inject(fault_site, "resume")
        _resume(child)
        active_after_resume = sec01._job_active_processes(job)
        _inject(fault_site, "query")
        exit_code, timed_out = _wait(child, timeout_seconds)
        result: dict[str, Any] | None = {
            "pid": child["pid"],
            "token": token,
            "job_member": in_job,
            "active_after_resume": active_after_resume,
            "checkpoint": {
                "saved_before_resume": checkpoint_saved,
                "pid_matches": pid_matches,
                "ready_absent_before_resume": not ready_before,
                "ready_after_resume": (workspace / "ready.json").is_file(),
            },
            "exit_code": exit_code,
            "timed_out": timed_out,
            "log_path": str(log_path),
            "keepalive": len(keepalive),
            "stdio_inheritable": stdio_inheritable,
        }
    except BaseException:
        stop_state.stopped = True
        if child is not None and not closed:
            errors.extend(sec01._terminate_and_close_owned_process(
                child["process"], child["thread"]
            ))
            closed = True
        raise
    finally:
        if attribute_buffer is not None:
            try:
                sec01.kernel32.DeleteProcThreadAttributeList(attribute_buffer)
            except BaseException as exc:
                errors.append(f"DeleteProcThreadAttributeList error: {type(exc).__name__}")
        _close_stdio_handles(handles, errors)
        if child is not None and not closed:
            errors.extend(sec01._close_owned_handles(child["thread"], child["process"]))
    if result is None:
        raise B0ProofError("launch finished without a result")
    result["handles_closed"] = _handles_closed(errors)
    return result


def _dacl_observation(
    path: Path, *, user_sid: str, container_sid: str
) -> dict[str, Any]:
    """Read the DACL ACEs and control flags; any native failure raises."""
    needed = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    sized = sec01.advapi32.GetFileSecurityW(
        str(path), sec01.DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(needed)
    )
    error = ctypes.get_last_error()
    if not sized and error != ERROR_INSUFFICIENT_BUFFER:
        raise B0ProofError(
            f"GetFileSecurityW(DACL size) failed: {ctypes.FormatError(error)}"
        )
    buffer = ctypes.create_string_buffer(needed.value or 1)
    _check(
        bool(sec01.advapi32.GetFileSecurityW(
            str(path), sec01.DACL_SECURITY_INFORMATION, buffer, needed,
            ctypes.byref(needed),
        )),
        "GetFileSecurityW(DACL)",
    )
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    _check(
        bool(sec01.advapi32.GetSecurityDescriptorDacl(
            buffer, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        )),
        "GetSecurityDescriptorDacl",
    )
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    _check(
        bool(sec01.advapi32.GetSecurityDescriptorControl(
            buffer, ctypes.byref(control), ctypes.byref(revision)
        )),
        "GetSecurityDescriptorControl",
    )
    protected = bool(control.value & SE_DACL_PROTECTED)
    aces: list[dict[str, Any]] = []
    if present.value and dacl.value:
        size_info = _AclSizeInformation()
        _check(
            bool(sec01.advapi32.GetAclInformation(
                dacl, ctypes.byref(size_info), ctypes.sizeof(size_info),
                ACL_SIZE_INFORMATION_CLASS,
            )),
            "GetAclInformation",
        )
        for index in range(int(size_info.AceCount)):
            ace = ctypes.c_void_p()
            _check(
                bool(sec01.advapi32.GetAce(dacl, index, ctypes.byref(ace))),
                "GetAce",
            )
            header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
            if header.AceType not in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
                continue
            allowed = ctypes.cast(ace, ctypes.POINTER(_AccessAllowedAce)).contents
            sid_pointer = ctypes.addressof(allowed) + _AccessAllowedAce.SidStart.offset
            aces.append({
                "type": "allow" if header.AceType == ACCESS_ALLOWED_ACE_TYPE else "deny",
                "mask": f"0x{allowed.Mask:08x}",
                "sid": sec01._sid_string(sid_pointer),
            })
    return {
        "aces": aces,
        "query": "ok",
        "protected": protected,
        "container_ace": any(ace["sid"] == container_sid for ace in aces),
        "user_ace": any(ace["sid"] == user_sid for ace in aces),
    }


def _label_observation(path: Path) -> tuple[str | None, str]:
    """Return ``(label_sid, status)`` for the mandatory label of *path*.

    Status is ``"ok"`` when an explicit label was read and ``"absent"`` when the
    query succeeded and no label exists.  Any native failure raises, so a denied
    or failed query can never be recorded as a successful no-label observation.
    """
    size = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    sized = sec01.advapi32.GetFileSecurityW(
        str(path), LABEL_SECURITY_INFORMATION, None, 0, ctypes.byref(size)
    )
    error = ctypes.get_last_error()
    if not sized and error != ERROR_INSUFFICIENT_BUFFER:
        raise B0ProofError(
            f"GetFileSecurityW(LABEL size) failed: {ctypes.FormatError(error)}"
        )
    if not size.value:
        raise B0ProofError("GetFileSecurityW(LABEL) returned an empty size")
    buffer = ctypes.create_string_buffer(size.value)
    _check(
        bool(sec01.advapi32.GetFileSecurityW(
            str(path), LABEL_SECURITY_INFORMATION, buffer, size, ctypes.byref(size)
        )),
        "GetFileSecurityW(LABEL)",
    )
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    sacl = ctypes.c_void_p()
    _check(
        bool(sec01.advapi32.GetSecurityDescriptorSacl(
            buffer, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)
        )),
        "GetSecurityDescriptorSacl",
    )
    if not present.value or not sacl.value:
        return None, "absent"
    info = _AclSizeInformation()
    _check(
        bool(sec01.advapi32.GetAclInformation(
            sacl, ctypes.byref(info), ctypes.sizeof(info), ACL_SIZE_INFORMATION_CLASS
        )),
        "GetAclInformation(SACL)",
    )
    for index in range(int(info.AceCount)):
        ace = ctypes.c_void_p()
        _check(
            bool(sec01.advapi32.GetAce(sacl, index, ctypes.byref(ace))),
            "GetAce(SACL)",
        )
        header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
        if header.AceType != SYSTEM_MANDATORY_LABEL_ACE_TYPE:
            continue
        allowed = ctypes.cast(ace, ctypes.POINTER(_AccessAllowedAce)).contents
        sid_pointer = ctypes.addressof(allowed) + _AccessAllowedAce.SidStart.offset
        return sec01._sid_string(sid_pointer), "ok"
    return None, "absent"


def _security_observation(
    path: Path, *, user_sid: str, container_sid: str
) -> dict[str, Any]:
    """Read DACL and mandatory label; a failed query is reported, never hidden."""
    observation: dict[str, Any] = {
        "dacl": {"aces": [], "query": "failed"},
        "label_sid": None,
        "label_query": "failed",
        "error": "",
    }
    try:
        observation["dacl"] = _dacl_observation(
            path, user_sid=user_sid, container_sid=container_sid
        )
        label_sid, label_status = _label_observation(path)
        observation["label_sid"] = label_sid
        observation["label_query"] = label_status
    except BaseException as exc:
        observation["error"] = f"{type(exc).__name__}: {exc}"
    return observation


def _set_acl(path: Path, user_sid: int, container_sid: int, container_mask: int) -> None:
    sec01._set_dacl(path, [
        (user_sid, sec01.FILE_ALL_ACCESS,
         sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
        (container_sid, container_mask,
         sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
    ])


def _set_traverse_acl(path: Path, user_sid: int, container_sid: int) -> None:
    sec01._set_dacl(path, [
        (user_sid, sec01.FILE_ALL_ACCESS, 0),
        (container_sid, sec01.FILE_GENERIC_READ | sec01.FILE_GENERIC_EXECUTE, 0),
    ])


def _set_user_only_acl(path: Path, user_sid: int) -> None:
    sec01._set_dacl(path, [
        (user_sid, sec01.FILE_ALL_ACCESS,
         sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
    ])


def _prepare_fixture(
    *,
    fixture: _OwnedFixture,
    user_sid: int,
    container_sid: int,
    runtimes: Mapping[str, Path],
) -> dict[str, Any]:
    """Create the owned L1/L2 workspaces, vault/state canaries and runtime copies."""
    root = fixture.root
    profile_folder = fixture.profile_folder
    if root is None or profile_folder is None:
        raise B0ProofError("fixture was not allocated")
    _set_traverse_acl(root, user_sid, container_sid)
    prepared: dict[str, Path] = {}
    for ver, source in runtimes.items():
        target = root / f"runtime_{ver.replace('.', '_')}"
        prepared[ver] = _prepare_runtime(source, target)
        _set_acl(
            target, user_sid, container_sid,
            sec01.FILE_GENERIC_READ | sec01.FILE_GENERIC_EXECUTE,
        )
    vault = root / "vault"
    state = root / "state"
    vault.mkdir()
    state.mkdir()
    _set_user_only_acl(vault, user_sid)
    _set_user_only_acl(state, user_sid)
    canaries: dict[str, Path] = {}
    for name, directory in (("vault", vault), ("state", state)):
        canary = directory / f"{name}_canary.txt"
        canary.write_text(
            f"B0-CANARY-{name.upper()}-{secrets.token_hex(8)}\n", encoding="utf-8"
        )
        canaries[name] = canary
        (directory / f"{name}_control.txt").write_text("CONTROL\n", encoding="utf-8")
    l1_root = profile_folder / "b0_workspace"
    l1_root.mkdir()
    _set_acl(l1_root, user_sid, container_sid, sec01.FILE_ALL_ACCESS)
    l2_root = root / "runs" / "RUN-B0"
    l2_checkout = l2_root / "checkout"
    l2_checkout.mkdir(parents=True)
    _set_traverse_acl(root / "runs", user_sid, container_sid)
    _set_traverse_acl(l2_root, user_sid, container_sid)
    _set_acl(l2_checkout, user_sid, container_sid, sec01.FILE_ALL_ACCESS)
    workspaces: dict[str, dict[str, Path]] = {"L1": {}, "L2": {}}
    for ver in runtimes:
        suffix = ver.replace(".", "_")
        for layout, parent in (("L1", l1_root), ("L2", l2_checkout)):
            workspace = parent / f"ws_{suffix}"
            workspace.mkdir()
            _set_acl(workspace, user_sid, container_sid, sec01.FILE_ALL_ACCESS)
            (workspace / "allowed_input.txt").write_text(
                "B0-ALLOWED-INPUT\n", encoding="utf-8"
            )
            workspaces[layout][ver] = workspace
    return {
        "runtimes": prepared,
        "workspaces": workspaces,
        "vault": vault,
        "state": state,
        "canaries": canaries,
        "hashes_before": {name: _sha256(path) for name, path in canaries.items()},
    }


def _same_user_control(vault: Path, state: Path) -> dict[str, bool]:
    """Prove the trusted user can still read/write the vault/state controls."""
    results: dict[str, bool] = {}
    for name, directory in (("vault", vault), ("state", state)):
        control = directory / f"{name}_control.txt"
        try:
            read_ok = control.read_text(encoding="utf-8") == "CONTROL\n"
        except OSError:
            read_ok = False
        try:
            with control.open("a", encoding="utf-8") as handle:
                handle.write("CONTROL-WRITE\n")
            write_ok = True
        except OSError:
            write_ok = False
        results[f"{name}_read_ok"] = read_ok
        results[f"{name}_write_ok"] = write_ok
    return results


def _worker_env(
    *,
    workspace: Path,
    profile_folder: Path,
    allowed_value: str,
    canaries: Mapping[str, Path],
    protected_handle: int,
) -> dict[str, str]:
    env = sec01._environment_values(
        folder=profile_folder, allowed=workspace, allowed_value=allowed_value
    )
    env.update({
        "OMP_SEC02_ALLOWED": allowed_value,
        "OMP_SEC02_VAULT_CANARY": str(canaries["vault"]),
        "OMP_SEC02_STATE_CANARY": str(canaries["state"]),
        "OMP_SEC02_PROTECTED_HANDLE": str(protected_handle),
    })
    return env


def _read_worker_result(workspace: Path) -> dict[str, Any] | None:
    path = workspace / "worker_result.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _denied(attempt: Any) -> bool:
    return (
        isinstance(attempt, Mapping)
        and attempt.get("ok") is False
        and isinstance(attempt.get("error"), str)
        and attempt.get("error") in ("PermissionError", "OSError", "FileNotFoundError")
    )


def _normalize_handle_path(value: str) -> str:
    text = value.replace("/", "\\")
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return text.lower()


def _protected_usable(reported: Mapping[str, Any], target: str) -> bool:
    """Derive usability from the raw Worker report; producer and verifier share it."""
    path = reported.get("path")
    return (
        reported.get("valid") is True
        and isinstance(path, str)
        and bool(path)
        and _normalize_handle_path(path) == _normalize_handle_path(target)
    )


def _run_layout_case(
    *,
    fixture: _OwnedFixture,
    runtime_exe: Path,
    ver: str,
    layout: str,
    workspace: Path,
    canaries: Mapping[str, Path],
    fake_secret: str,
    protected_handle: int,
    stop_state: StopState,
) -> dict[str, Any]:
    """Run one Worker in one layout and capture controller-side and Worker evidence."""
    script = workspace / "worker.py"
    script.write_text(_WORKER_SCRIPT, encoding="utf-8")
    (workspace / "ready.json").unlink(missing_ok=True)
    (workspace / "worker_result.json").unlink(missing_ok=True)
    allowed_value = f"B0-ALLOWED-{secrets.token_hex(6)}"
    env = _worker_env(
        workspace=workspace,
        profile_folder=fixture.profile_folder or Path("."),
        allowed_value=allowed_value,
        canaries=canaries,
        protected_handle=protected_handle,
    )
    checkpoint = (fixture.root or Path(".")) / "state" / f"checkpoint_{ver}_{layout}.json"
    cleanup_log: list[str] = []
    os.environ["OMP_SEC02_FAKE_SECRET"] = fake_secret
    try:
        launch = _launch_worker(
            runtime_exe=runtime_exe,
            script_path=script,
            workspace=workspace,
            profile_folder=fixture.profile_folder or Path("."),
            container_sid=fixture.container_sid_ptr or 0,
            job=fixture.job or 0,
            stop_state=stop_state,
            env_values=env,
            checkpoint_path=checkpoint,
            cleanup_log=cleanup_log,
        )
    finally:
        os.environ.pop("OMP_SEC02_FAKE_SECRET", None)
    result = _read_worker_result(workspace)
    log_text = ""
    try:
        log_text = Path(launch["log_path"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    if result is None:
        result = {}
    worker_env = result.get("env") if isinstance(result.get("env"), Mapping) else {}
    flags = result.get("flags") if isinstance(result.get("flags"), Mapping) else {}
    handle_probe = result.get("handle") if isinstance(result.get("handle"), Mapping) else {}
    protected_target = str(canaries["vault"])
    protected_usable = _protected_usable(handle_probe, protected_target)
    write_path = workspace / "allowed_output.txt"
    write_ok = False
    try:
        write_ok = write_path.read_text(encoding="utf-8") == "B0-WORKER-WROTE"
    except OSError:
        write_ok = False
    read_attempt = result.get("read")
    return {
        "status": "PASS" if launch["exit_code"] == 0 and not launch["timed_out"] else "FAIL",
        "layout": layout,
        "runtime": ver,
        "workspace": str(workspace),
        "executable": str(runtime_exe),
        "sys_version": str(flags.get("version") or ""),
        "isolated": flags.get("isolated"),
        "safe_path": flags.get("safe_path"),
        "exit_code": launch["exit_code"],
        "timed_out": launch["timed_out"],
        "job_member": launch["job_member"],
        "active_after_resume": launch["active_after_resume"],
        "token": launch["token"],
        "checkpoint": launch["checkpoint"],
        "stdout_marker_visible": "B0-WORKER-STDOUT" in log_text,
        "stdio_inheritable": launch["stdio_inheritable"],
        "handles_closed": launch["handles_closed"],
        "stdio_excerpt": log_text[:2000],
        "read_ok": (
            isinstance(read_attempt, Mapping)
            and read_attempt.get("ok") is True
            and read_attempt.get("value") == "B0-ALLOWED-INPUT\n"
        ),
        "write_ok": write_ok,
        "vault_read_denied": _denied(result.get("vault_read")),
        "vault_write_denied": _denied(result.get("vault_write")),
        "state_read_denied": _denied(result.get("state_read")),
        "state_write_denied": _denied(result.get("state_write")),
        "fake_secret_key_present": "OMP_SEC02_FAKE_SECRET" in worker_env,
        "fake_secret_visible": fake_secret in worker_env.values(),
        "allowed_value_visible": allowed_value in worker_env.values(),
        "protected_handle": {
            "value": protected_handle,
            "target": protected_target,
            "inheritable": _handle_inheritable(protected_handle),
            "usable": protected_usable,
            "reported": dict(handle_probe),
        },
        "cleanup_errors": cleanup_log,
    }


def _layout_section(
    cases: Mapping[str, Mapping[str, Any]],
    workspace: Path,
    *,
    user_sid: str,
    container_sid: str,
) -> dict[str, Any]:
    values = list(cases.values())
    return {
        "workspace_kind": "",
        "workspace_read_ok": all(case["read_ok"] for case in values),
        "workspace_write_ok": all(case["write_ok"] for case in values),
        "vault_read_denied": all(case["vault_read_denied"] for case in values),
        "vault_write_denied": all(case["vault_write_denied"] for case in values),
        "state_read_denied": all(case["state_read_denied"] for case in values),
        "state_write_denied": all(case["state_write_denied"] for case in values),
        "hashes_unchanged": True,
        "security": _security_observation(
            workspace, user_sid=user_sid, container_sid=container_sid
        ),
        "per_runtime": {ver: dict(case) for ver, case in cases.items()},
    }


def _layout_ok(section: Mapping[str, Any]) -> bool:
    keys = (
        "workspace_read_ok", "workspace_write_ok", "vault_read_denied",
        "vault_write_denied", "state_read_denied", "state_write_denied",
        "hashes_unchanged",
    )
    return all(section.get(key) is True for key in keys)


def _runtime_section(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "executable": case["executable"],
        "sys_version": case["sys_version"],
        "minor": case["runtime"],
        "isolated": case["isolated"],
        "safe_path": case["safe_path"],
        "exit_code": case["exit_code"],
        "timed_out": case["timed_out"],
        "job_member": case["job_member"],
        "token": case["token"],
        "env": {
            "fake_secret_key_present": case["fake_secret_key_present"],
            "fake_secret_visible": case["fake_secret_visible"],
            "allowed_value_visible": case["allowed_value_visible"],
        },
        "io": {"read_ok": case["read_ok"], "write_ok": case["write_ok"]},
        "checkpoint": case["checkpoint"],
        "stdout_marker_visible": case["stdout_marker_visible"],
        "protected_handle": case["protected_handle"],
    }


def _source_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    names = (
        "tools/proofs/sec02_integration_windows.py",
        "tools/proofs/sec02_windows.py",
        "tools/proofs/sec01_windows.py",
        "omp_core/main.py",
        "omp_core/windows_job.py",
    )
    return {name: _sha256(root / name) for name in names if (root / name).is_file()}


def _fault_matrix(
    *,
    fixture: _OwnedFixture,
    runtime_exe: Path,
    workspace: Path,
    canaries: Mapping[str, Path],
    fake_secret: str,
) -> dict[str, Any]:
    """Inject attribute/create/save/resume/query faults and one live descendant."""
    job = fixture.job or 0
    script = workspace / "worker.py"
    script.write_text(_WORKER_SCRIPT, encoding="utf-8")
    env = _worker_env(
        workspace=workspace,
        profile_folder=fixture.profile_folder or Path("."),
        allowed_value="B0-FAULT-ALLOWED",
        canaries=canaries,
        protected_handle=0,
    )
    sites: dict[str, Any] = {}
    for site in FAULT_SITES:
        stop_state = StopState()
        cleanup_log: list[str] = []

        def launch(
            selected: str,
            stop_state: StopState = stop_state,
            cleanup_log: list[str] = cleanup_log,
        ) -> None:
            _launch_worker(
                runtime_exe=runtime_exe,
                script_path=script,
                workspace=workspace,
                profile_folder=fixture.profile_folder or Path("."),
                container_sid=fixture.container_sid_ptr or 0,
                job=job,
                stop_state=stop_state,
                env_values=env,
                checkpoint_path=(fixture.root or workspace)
                / "state"
                / f"checkpoint_fault_{selected}.json",
                fault_site=selected,
                cleanup_log=cleanup_log,
            )

        def cleanup(cleanup_log: list[str] = cleanup_log) -> dict[str, Any]:
            return {
                "active_after_cleanup": sec01._job_active_processes(job),
                "cleanup_errors": list(cleanup_log),
            }

        sites[site] = run_fault_case(
            site=site,
            launch=launch,
            cleanup=cleanup,
            stop_state=stop_state,
            secrets=[fake_secret],
            paths=[str(workspace), str(fixture.root), str(runtime_exe)],
        )
    descendant_script = workspace / "descendant.py"
    descendant_script.write_text(_DESCENDANT_SCRIPT, encoding="utf-8")
    (workspace / "descendant_child.py").write_text(
        _DESCENDANT_CHILD_SCRIPT, encoding="utf-8"
    )
    (workspace / "descendant_ready.txt").unlink(missing_ok=True)
    stop_state = StopState()
    launch = _launch_worker(
        runtime_exe=runtime_exe,
        script_path=descendant_script,
        workspace=workspace,
        profile_folder=fixture.profile_folder or Path("."),
        container_sid=fixture.container_sid_ptr or 0,
        job=job,
        stop_state=stop_state,
        env_values=env,
    )
    ready = (workspace / "descendant_ready.txt").is_file()
    log_text = ""
    try:
        log_text = Path(launch["log_path"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    active_after_root = sec01._job_active_processes(job)
    started = time.monotonic()
    stop = bounded_stop(
        lambda: sec01._job_active_processes(job),
        lambda: bool(sec01.kernel32.TerminateJobObject(ctypes.c_void_p(job), 1)),
    )
    return {
        "sites": sites,
        "descendant": {
            "ready": ready,
            "root_exited": launch["exit_code"] == 0 and not launch["timed_out"],
            "active_after_root": active_after_root,
            "stop_ok": stop["ok"],
            "stop_ms": int((time.monotonic() - started) * 1000),
            "active_after_stop": sec01._job_active_processes(job),
            "cleanup_errors": stop["errors"],
            "stdio_excerpt": log_text[:2000],
        },
    }


def compute_properties(evidence: Mapping[str, Any]) -> dict[str, str]:
    """Derive each B0 property status from the raw evidence fields."""
    blocked = _as_mapping(evidence.get("blocked")) or {}
    properties: dict[str, str] = {}
    for prop in PROPERTY_IDS:
        if blocked.get(prop):
            properties[prop] = "BLOCKED"
        else:
            properties[prop] = (
                "PASS" if not _property_failures(evidence, prop) else "FAIL"
            )
    return properties


def _property_failures(evidence: Mapping[str, Any], prop: str) -> list[str]:
    if prop == "B0-2":
        return _check_attributes(evidence)
    if prop == "B0-3":
        return _check_runtimes(evidence)
    if prop == "B0-4":
        return _check_layout(evidence)
    if prop == "B0-5":
        return _check_handles(evidence)
    if prop == "B0-6":
        return _check_faults(evidence)
    if prop == "B0-7":
        return _check_negative_controls(evidence)
    return [f"Unknown property '{prop}'"]


def _check_attributes(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    attributes = _as_mapping(evidence.get("attributes"))
    if attributes is None:
        return ["attributes must be a JSON object"]
    if list(attributes.get("names") or []) != list(ATTRIBUTE_NAMES):
        failures.append(
            f"attributes.names must be {list(ATTRIBUTE_NAMES)}, got {attributes.get('names')}"
        )
    if attributes.get("attach_mode") != "at_create":
        failures.append("attributes.attach_mode must be 'at_create'")
    if attributes.get("job_handle_inheritable") is not False:
        failures.append("attributes.job_handle_inheritable must be false")
    if attributes.get("fallback_used") is not False:
        failures.append("attributes.fallback_used must be false")
    if attributes.get("job_member_before_resume") is not True:
        failures.append("attributes.job_member_before_resume must be true")
    runtimes = _as_mapping(evidence.get("runtimes")) or {}
    for ver in RUNTIME_KEYS:
        section = _as_mapping(runtimes.get(ver))
        if section is None:
            failures.append(f"runtimes.{ver} must be a JSON object")
            continue
        checkpoint = _as_mapping(section.get("checkpoint")) or {}
        for key in (
            "saved_before_resume", "pid_matches",
            "ready_absent_before_resume", "ready_after_resume",
        ):
            if checkpoint.get(key) is not True:
                failures.append(f"runtimes.{ver}.checkpoint.{key} must be true")
    layout = _as_mapping(evidence.get("layout")) or {}
    selected = layout.get("selected")
    selected_section = (
        _as_mapping(layout.get(selected)) if isinstance(selected, str) else None
    )
    per_runtime = _as_mapping((selected_section or {}).get("per_runtime")) or {}
    for ver in RUNTIME_KEYS:
        case = _as_mapping(per_runtime.get(ver)) or {}
        if case.get("job_member") is not True:
            failures.append(
                f"layout case {ver} must be a Job member before resume"
            )
    return failures


def _check_token(
    token: Any,
    prefix: str,
    controller: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> list[str]:
    """Validate one Worker token against the controller and AppContainer profile."""
    value = _as_mapping(token)
    if value is None:
        return [f"{prefix} must be a JSON object"]
    failures: list[str] = []
    if value.get("is_app_container") is not True:
        failures.append(f"{prefix}.is_app_container must be true")
    if value.get("user_sid") != controller.get("user_sid"):
        failures.append(f"{prefix}.user_sid does not match controller")
    if value.get("app_container_sid") != profile.get("sid"):
        failures.append(f"{prefix}.app_container_sid does not match profile")
    if value.get("integrity_sid") != LOW_INTEGRITY:
        failures.append(f"{prefix}.integrity_sid must be low")
    if value.get("capability_count") != 0 or isinstance(
        value.get("capability_count"), bool
    ):
        failures.append(f"{prefix}.capability_count must be 0")
    if value.get("elevated") is not False:
        failures.append(f"{prefix}.elevated must be false")
    return failures


def _check_runtimes(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    controller = _as_mapping(evidence.get("controller")) or {}
    profile = _as_mapping(evidence.get("profile")) or {}
    runtimes = _as_mapping(evidence.get("runtimes")) or {}
    for ver in RUNTIME_KEYS:
        section = _as_mapping(runtimes.get(ver))
        if section is None:
            failures.append(f"Python {ver} runtime evidence is missing")
            continue
        if section.get("minor") != ver:
            failures.append(f"Python {ver} minor must be '{ver}'")
        if not str(section.get("sys_version", "")).startswith(ver):
            failures.append(f"Python {ver} sys_version must start with '{ver}'")
        if section.get("isolated") != 1 or isinstance(section.get("isolated"), bool):
            failures.append(f"Python {ver} must run with isolated=1")
        if section.get("safe_path") is not True:
            failures.append(f"Python {ver} must run with safe_path=true")
        if section.get("exit_code") != 0 or not _is_int(section.get("exit_code")):
            failures.append(
                f"Python {ver} Worker exit_code must be the integer 0, "
                f"got {section.get('exit_code')!r}"
            )
        if section.get("timed_out") is not False:
            failures.append(f"Python {ver} Worker must not time out")
        if section.get("job_member") is not True:
            failures.append(f"Python {ver} Worker must be a Job member")
        token = _as_mapping(section.get("token")) or {}
        failures.extend(_check_token(token, f"runtimes.{ver}.token", controller, profile))
        env = _as_mapping(section.get("env")) or {}
        if env.get("fake_secret_key_present") is not False:
            failures.append(f"Python {ver} fake secret key must not reach the Worker")
        if env.get("fake_secret_visible") is not False:
            failures.append(f"Python {ver} fake secret value must not reach the Worker")
        if env.get("allowed_value_visible") is not True:
            failures.append(f"Python {ver} allowed env value must reach the Worker")
        io = _as_mapping(section.get("io")) or {}
        if io.get("read_ok") is not True:
            failures.append(f"Python {ver} allowed fixture read must succeed")
        if io.get("write_ok") is not True:
            failures.append(f"Python {ver} allowed fixture write must succeed")
    layout = _as_mapping(evidence.get("layout")) or {}
    selected = layout.get("selected")
    selected_section = (
        _as_mapping(layout.get(selected)) if isinstance(selected, str) else None
    )
    per_runtime = _as_mapping((selected_section or {}).get("per_runtime")) or {}
    for ver in RUNTIME_KEYS:
        case = _as_mapping(per_runtime.get(ver))
        if case is None:
            failures.append(f"runtimes.{ver} has no matching selected layout case")
            continue
        try:
            expected = _runtime_section(case)
        except BaseException:
            failures.append(f"layout.{selected}.per_runtime.{ver} is malformed")
            continue
        if dict(runtimes.get(ver) or {}) != expected:
            failures.append(
                f"runtimes.{ver} does not match "
                f"layout.{selected}.per_runtime.{ver}"
            )
    return failures


def _case_protected(case: Mapping[str, Any]) -> Mapping[str, Any]:
    return _as_mapping(case.get("protected_handle")) or {}


def _all_layout_cases(layout: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return every typed layout case that is present, in L1/L2 runtime order."""
    cases: list[Mapping[str, Any]] = []
    for name in ("L1", "L2"):
        section = _as_mapping(layout.get(name)) or {}
        per_runtime = _as_mapping(section.get("per_runtime")) or {}
        for ver in RUNTIME_KEYS:
            case = _as_mapping(per_runtime.get(ver))
            if case is not None:
                cases.append(case)
    return cases


def _canaries_unchanged(layout: Mapping[str, Any]) -> bool | None:
    """Return the before/after canary comparison, or None when it is invalid."""
    canaries = _as_mapping(layout.get("canaries"))
    if canaries is None:
        return None
    unchanged = True
    for name in CANARY_NAMES:
        record = _as_mapping(canaries.get(name))
        if record is None or not _is_sha256(record.get("before")) or not _is_sha256(
            record.get("after")
        ):
            return None
        unchanged = unchanged and record.get("before") == record.get("after")
    return unchanged


def _check_canaries(layout: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    canaries = _as_mapping(layout.get("canaries"))
    if canaries is None:
        return ["layout.canaries must be a JSON object"]
    for name in CANARY_NAMES:
        record = _as_mapping(canaries.get(name))
        if record is None:
            failures.append(f"layout.canaries.{name} must be a JSON object")
            continue
        before = record.get("before")
        after = record.get("after")
        if not _is_sha256(before):
            failures.append(f"layout.canaries.{name}.before must be a SHA-256 digest")
        if not _is_sha256(after):
            failures.append(f"layout.canaries.{name}.after must be a SHA-256 digest")
        if _is_sha256(before) and _is_sha256(after) and before != after:
            failures.append(f"layout.canaries.{name} changed during the probe")
    return failures


def _check_security(
    security: Any,
    prefix: str,
    controller: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> list[str]:
    """Validate a DACL/label observation and re-derive the ACE claims from raw ACEs."""
    value = _as_mapping(security)
    if value is None:
        return [f"{prefix} must be a JSON object"]
    failures: list[str] = []
    if value.get("error") != "":
        failures.append(f"{prefix}.error must be empty")
    dacl = _as_mapping(value.get("dacl"))
    if dacl is None:
        failures.append(f"{prefix}.dacl must be a JSON object")
    else:
        if dacl.get("query") != "ok":
            failures.append(f"{prefix}.dacl.query must be 'ok'")
        if dacl.get("protected") is not True:
            failures.append(f"{prefix}.dacl.protected must be true")
        aces = dacl.get("aces")
        sids: list[str] = []
        if not isinstance(aces, list):
            failures.append(f"{prefix}.dacl.aces must be a list")
        else:
            for index, ace in enumerate(aces):
                record = _as_mapping(ace)
                if record is None:
                    failures.append(f"{prefix}.dacl.aces[{index}] must be an object")
                    continue
                if record.get("type") not in ("allow", "deny"):
                    failures.append(f"{prefix}.dacl.aces[{index}].type is invalid")
                mask = record.get("mask")
                try:
                    if not isinstance(mask, str) or not mask.startswith("0x"):
                        raise ValueError
                    int(mask, 16)
                except (TypeError, ValueError):
                    failures.append(f"{prefix}.dacl.aces[{index}].mask is invalid")
                sid = record.get("sid")
                if not isinstance(sid, str) or not sid.startswith("S-1-"):
                    failures.append(f"{prefix}.dacl.aces[{index}].sid is invalid")
                else:
                    sids.append(sid)
        for claim, expected, label in (
            ("container_ace", profile.get("sid"), "profile SID"),
            ("user_ace", controller.get("user_sid"), "controller user SID"),
        ):
            if not isinstance(expected, str) or not expected.startswith("S-1-"):
                failures.append(f"{prefix}.{claim} has no valid {label} to match")
                continue
            derived = expected in sids
            if dacl.get(claim) is not derived:
                failures.append(
                    f"{prefix}.dacl.{claim} does not match the raw ACE records"
                )
            elif derived is not True:
                failures.append(f"{prefix}.dacl.{claim} must be true")
    status = value.get("label_query")
    label_sid = value.get("label_sid")
    if status == "ok":
        if not str(label_sid or "").startswith("S-1-16-"):
            failures.append(
                f"{prefix}.label_sid must be a mandatory-label SID when "
                "label_query is 'ok'"
            )
    elif status == "absent":
        if label_sid is not None:
            failures.append(
                f"{prefix}.label_sid must be null when label_query is 'absent'"
            )
    else:
        failures.append(f"{prefix}.label_query must be 'ok' or 'absent'")
    return failures


def _check_case(
    case: Any,
    layout_name: str,
    ver: str,
    controller: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> list[str]:
    """Validate one typed per-layout, per-runtime Worker case."""
    prefix = f"layout.{layout_name}.per_runtime.{ver}"
    value = _as_mapping(case)
    if value is None:
        return [f"{prefix} must be a JSON object"]
    failures: list[str] = []
    if value.get("status") != "PASS":
        failures.append(f"{prefix}.status must be 'PASS'")
    if value.get("layout") != layout_name:
        failures.append(f"{prefix}.layout must be '{layout_name}'")
    if value.get("runtime") != ver:
        failures.append(f"{prefix}.runtime must be '{ver}'")
    for key in ("workspace", "executable"):
        if not isinstance(value.get(key), str) or not value.get(key):
            failures.append(f"{prefix}.{key} must be a non-empty string")
    if not str(value.get("sys_version", "")).startswith(ver):
        failures.append(f"{prefix}.sys_version must start with '{ver}'")
    if value.get("isolated") != 1 or isinstance(value.get("isolated"), bool):
        failures.append(f"{prefix}.isolated must be the integer 1")
    if value.get("safe_path") is not True:
        failures.append(f"{prefix}.safe_path must be true")
    if value.get("exit_code") != 0 or not _is_int(value.get("exit_code")):
        failures.append(f"{prefix}.exit_code must be the integer 0")
    if value.get("timed_out") is not False:
        failures.append(f"{prefix}.timed_out must be false")
    if value.get("job_member") is not True:
        failures.append(f"{prefix}.job_member must be true")
    active = value.get("active_after_resume")
    if not _is_int(active) or active < 0:
        failures.append(f"{prefix}.active_after_resume must be a non-negative integer")
    checkpoint = _as_mapping(value.get("checkpoint")) or {}
    for key in (
        "saved_before_resume",
        "pid_matches",
        "ready_absent_before_resume",
        "ready_after_resume",
    ):
        if checkpoint.get(key) is not True:
            failures.append(f"{prefix}.checkpoint.{key} must be true")
    failures.extend(
        _check_token(value.get("token"), f"{prefix}.token", controller, profile)
    )
    for key in (
        "stdout_marker_visible",
        "stdio_inheritable",
        "handles_closed",
        "read_ok",
        "write_ok",
        "vault_read_denied",
        "vault_write_denied",
        "state_read_denied",
        "state_write_denied",
        "allowed_value_visible",
    ):
        if value.get(key) is not True:
            failures.append(f"{prefix}.{key} must be true")
    for key in ("fake_secret_key_present", "fake_secret_visible"):
        if value.get(key) is not False:
            failures.append(f"{prefix}.{key} must be false")
    protected = _as_mapping(value.get("protected_handle"))
    if protected is None:
        failures.append(f"{prefix}.protected_handle must be a JSON object")
    else:
        if protected.get("inheritable") is not True:
            failures.append(f"{prefix}.protected_handle.inheritable must be true")
        if protected.get("usable") is not False:
            failures.append(f"{prefix}.protected_handle.usable must be false")
        if not _is_int(protected.get("value")):
            failures.append(f"{prefix}.protected_handle.value must be an integer")
        target = protected.get("target")
        if not isinstance(target, str) or not target:
            failures.append(
                f"{prefix}.protected_handle.target must be a non-empty string"
            )
        reported = _as_mapping(protected.get("reported"))
        if reported is None:
            failures.append(f"{prefix}.protected_handle.reported must be an object")
        else:
            valid = reported.get("valid")
            path = reported.get("path")
            error = reported.get("error")
            if not isinstance(valid, bool):
                failures.append(
                    f"{prefix}.protected_handle.reported.valid must be a boolean"
                )
            if not isinstance(path, str):
                failures.append(
                    f"{prefix}.protected_handle.reported.path must be a string"
                )
            if not (_is_int(error) or isinstance(error, str)):
                failures.append(
                    f"{prefix}.protected_handle.reported.error must be an "
                    "integer or a name"
                )
            elif valid is True and error != 0:
                failures.append(
                    f"{prefix}.protected_handle.reported.error must be 0 when valid"
                )
            elif valid is False and not error:
                failures.append(
                    f"{prefix}.protected_handle.reported.error must record the "
                    "failure when not valid"
                )
            if valid is True and path == "":
                failures.append(
                    f"{prefix}.protected_handle.reported.path must be non-empty "
                    "when valid"
                )
            if valid is False and path != "":
                failures.append(
                    f"{prefix}.protected_handle.reported.path must be empty "
                    "when not valid"
                )
            if (
                isinstance(target, str)
                and target
                and isinstance(valid, bool)
                and isinstance(path, str)
            ):
                derived = _protected_usable(reported, target)
                if protected.get("usable") is not derived:
                    failures.append(
                        f"{prefix}.protected_handle.usable does not match the "
                        "raw Worker report"
                    )
    cleanup_errors = value.get("cleanup_errors")
    if not isinstance(cleanup_errors, list) or cleanup_errors:
        failures.append(f"{prefix}.cleanup_errors must be an empty list")
    return failures


def _check_aggregates(
    section: Mapping[str, Any],
    cases: Mapping[str, Mapping[str, Any]],
    name: str,
) -> list[str]:
    """Re-derive each stored aggregate from its per-runtime cases."""
    failures: list[str] = []
    values = [case for case in cases.values() if case]
    for aggregate, case_key in (
        ("workspace_read_ok", "read_ok"),
        ("workspace_write_ok", "write_ok"),
        ("vault_read_denied", "vault_read_denied"),
        ("vault_write_denied", "vault_write_denied"),
        ("state_read_denied", "state_read_denied"),
        ("state_write_denied", "state_write_denied"),
    ):
        expected = bool(values) and all(
            case.get(case_key) is True for case in values
        )
        if section.get(aggregate) is not expected:
            failures.append(
                f"layout.{name}.{aggregate} does not match its per_runtime cases"
            )
    return failures


def _check_layout(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    layout = _as_mapping(evidence.get("layout"))
    if layout is None:
        return ["layout must be a JSON object"]
    controller = _as_mapping(evidence.get("controller")) or {}
    profile = _as_mapping(evidence.get("profile")) or {}
    selected = layout.get("selected")
    if selected not in ("L1", "L2"):
        failures.append("layout.selected must be 'L1' or 'L2'")
    failures.extend(_check_canaries(layout))
    unchanged = _canaries_unchanged(layout)
    sections: dict[str, Mapping[str, Any]] = {}
    for name in ("L1", "L2"):
        section = _as_mapping(layout.get(name))
        if section is None:
            failures.append(f"layout.{name} must be a JSON object")
            continue
        sections[name] = section
        for key in (
            "workspace_read_ok", "workspace_write_ok", "vault_read_denied",
            "vault_write_denied", "state_read_denied", "state_write_denied",
            "hashes_unchanged",
        ):
            if section.get(key) is not True:
                failures.append(f"layout.{name}.{key} must be true")
        if unchanged is not None and section.get("hashes_unchanged") is not unchanged:
            failures.append(
                f"layout.{name}.hashes_unchanged does not match the canary digests"
            )
        per_runtime = _as_mapping(section.get("per_runtime"))
        if per_runtime is None:
            failures.append(f"layout.{name}.per_runtime must be a JSON object")
            per_runtime = {}
        elif set(per_runtime) != set(RUNTIME_KEYS):
            failures.append(
                f"layout.{name}.per_runtime must contain exactly "
                f"{list(RUNTIME_KEYS)}"
            )
        cases: dict[str, Mapping[str, Any]] = {}
        for ver in RUNTIME_KEYS:
            case = _as_mapping(per_runtime.get(ver))
            cases[ver] = case or {}
            failures.extend(
                _check_case(per_runtime.get(ver), name, ver, controller, profile)
            )
        failures.extend(_check_aggregates(section, cases, name))
        failures.extend(
            _check_security(
                section.get("security"), f"layout.{name}.security", controller, profile
            )
        )
    control = _as_mapping(layout.get("same_user_control")) or {}
    for key in ("vault_read_ok", "vault_write_ok", "state_read_ok", "state_write_ok"):
        if control.get(key) is not True:
            failures.append(f"layout.same_user_control.{key} must be true")
    if list(layout.get("runtimes_observed") or []) != list(RUNTIME_KEYS):
        failures.append("layout.runtimes_observed must be ['3.12', '3.14']")
    if selected in sections and not _layout_ok(sections[selected]):
        failures.append(
            f"layout.selected '{selected}' does not satisfy its own aggregate"
        )
    if selected in sections:
        per_runtime = _as_mapping(sections[selected].get("per_runtime")) or {}
        for ver in RUNTIME_KEYS:
            case = _as_mapping(per_runtime.get(ver)) or {}
            if case.get("status") != "PASS":
                failures.append(
                    f"layout.{selected}.per_runtime.{ver}.status is not PASS"
                )
    return failures


def _check_handles(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    handles = _as_mapping(evidence.get("handles"))
    if handles is None:
        return ["handles must be a JSON object"]
    if list(handles.get("allowlist") or []) != list(HANDLE_ALLOWLIST):
        failures.append(f"handles.allowlist must be {list(HANDLE_ALLOWLIST)}")
    layout = _as_mapping(evidence.get("layout")) or {}
    cases = _all_layout_cases(layout)
    if len(cases) != len(RUNTIME_KEYS) * 2:
        failures.append("handles aggregate requires all four layout cases")
    flags = _as_mapping(handles.get("inheritable_flags")) or {}
    for name in HANDLE_ALLOWLIST:
        if flags.get(name) is not True:
            failures.append(f"handles.inheritable_flags.{name} must be true")
    if flags.get("protected") is not True:
        failures.append("handles.inheritable_flags.protected must be true")
    if flags.get("job") is not False:
        failures.append("handles.inheritable_flags.job must be false")
    if any(case.get("stdio_inheritable") is not True for case in cases):
        failures.append("a layout case reports a non-inheritable stdio handle")
    if any(_case_protected(case).get("inheritable") is not True for case in cases):
        failures.append("a layout case reports a non-inheritable protected handle")
    if handles.get("protected_handle_usable") is not False:
        failures.append("protected handle outside the allowlist must not be usable")
    if any(_case_protected(case).get("usable") is not False for case in cases):
        failures.append("a layout case reports a usable protected handle")
    if handles.get("fake_secret_visible") is not False:
        failures.append("fake secret must not be visible to the Worker")
    if any(case.get("fake_secret_visible") is not False for case in cases):
        failures.append("a layout case reports the fake secret reaching the Worker")
    if any(case.get("handles_closed") is not True for case in cases):
        failures.append("a layout case did not confirm handle cleanup")
    if any(case.get("cleanup_errors") for case in cases):
        failures.append("a layout case reported cleanup errors")
    privileges = handles.get("enabled_privileges")
    if not isinstance(privileges, list) or any(
        not isinstance(item, str) for item in privileges
    ):
        failures.append("handles.enabled_privileges must be a list of names")
    elif set(privileges) - SAFE_PRIVILEGES:
        failures.append(f"Worker has unexpected enabled privileges: {privileges}")
    else:
        derived = sorted({
            privilege["name"]
            for case in cases
            for privilege in (
                (_as_mapping(case.get("token")) or {}).get("privileges")
                if isinstance(
                    (_as_mapping(case.get("token")) or {}).get("privileges"), list
                )
                else []
            )
            if isinstance(privilege, Mapping) and _enabled_privilege(privilege)
        })
        if list(privileges) != derived:
            failures.append(
                "handles.enabled_privileges does not match the layout cases"
            )
    runtimes = _as_mapping(evidence.get("runtimes")) or {}
    for ver in RUNTIME_KEYS:
        section = _as_mapping(runtimes.get(ver)) or {}
        if section.get("stdout_marker_visible") is not True:
            failures.append(f"Python {ver} explicit stdout handle did not carry output")
    return failures


def _check_faults(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    faults = _as_mapping(evidence.get("faults"))
    if faults is None:
        return ["faults must be a JSON object"]
    sites = _as_mapping(faults.get("sites")) or {}
    for site in FAULT_SITES:
        record = _as_mapping(sites.get(site))
        if record is None:
            failures.append(f"faults.sites.{site} is missing")
            continue
        if record.get("reached") is not True:
            failures.append(f"fault site '{site}' was not reached")
        if record.get("active_after_cleanup") != 0 or not _is_int(
            record.get("active_after_cleanup")
        ):
            failures.append(f"fault site '{site}' left an active owned process")
        if not isinstance(record.get("cleanup_errors"), list) or record.get(
            "cleanup_errors"
        ):
            failures.append(f"fault site '{site}' reported cleanup errors")
        if record.get("replacement_blocked") is not True:
            failures.append(f"fault site '{site}' did not block replacement launch")
        elapsed = record.get("elapsed_ms")
        if not _is_number(elapsed) or elapsed < 0 or elapsed > 5000:
            failures.append(f"fault site '{site}' exceeded the stop deadline")
        if not isinstance(record.get("error"), str) or not record.get("error"):
            failures.append(f"fault site '{site}' has no diagnostic")
    descendant = _as_mapping(faults.get("descendant")) or {}
    if descendant.get("ready") is not True:
        failures.append("descendant readiness was not observed")
    if descendant.get("root_exited") is not True:
        failures.append("root did not exit before descendant cleanup")
    if not _is_int(descendant.get("active_after_root")) or descendant.get(
        "active_after_root", 0
    ) < 1:
        failures.append("no descendant was active after root exit")
    if descendant.get("stop_ok") is not True:
        failures.append("descendant cleanup was not bounded")
    stop_ms = descendant.get("stop_ms")
    if not _is_number(stop_ms) or stop_ms < 0 or stop_ms > 5000:
        failures.append("descendant cleanup exceeded the stop deadline")
    if descendant.get("active_after_stop") != 0:
        failures.append("descendants remained active after cleanup")
    descendant_errors = descendant.get("cleanup_errors")
    if not isinstance(descendant_errors, list) or descendant_errors:
        failures.append("descendant cleanup reported errors")
    return failures


def _check_negative_controls(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if evidence.get("not_run") != []:
        failures.append("not_run must be empty for a PASS report")
    controls = evidence.get("verifier_negative_controls")
    if not isinstance(controls, list) or not controls:
        return failures + ["verifier_negative_controls must be a non-empty list"]
    for index, control in enumerate(controls):
        record = _as_mapping(control)
        if record is None:
            failures.append(f"verifier_negative_controls[{index}] must be an object")
            continue
        if not isinstance(record.get("mutation"), str) or not record.get("mutation"):
            failures.append(f"verifier_negative_controls[{index}] has no mutation name")
        if record.get("rejected") is not True:
            failures.append(
                f"verifier accepted mutated evidence '{record.get('mutation')}'"
            )
    return failures


def verify_evidence(evidence: Mapping[str, object]) -> list[str]:
    """Validate a B0 evidence report and return every failure.

    Never raises on malformed input: an unexpected structure is reported as a
    failure so ``verify`` can exit nonzero without a traceback.  Stored property
    claims are re-derived from raw fields, so editing a raw value without
    updating the claim (or the reverse) is rejected.
    """
    try:
        return _verify_evidence(evidence)
    except BaseException as exc:
        return [f"Malformed evidence structure: {type(exc).__name__}"]


def _verify_evidence(evidence: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if not isinstance(evidence, Mapping):
        return ["Evidence must be a JSON object"]
    if evidence.get("schema") != SCHEMA:
        failures.append(
            f"Invalid schema: expected '{SCHEMA}', got '{evidence.get('schema')}'"
        )
    if evidence.get("verdict") != "PASS":
        failures.append(f"Verdict is not PASS: got '{evidence.get('verdict')}'")
    controller = _as_mapping(evidence.get("controller"))
    if controller is None:
        failures.append("controller must be a JSON object")
        controller = {}
    if not str(controller.get("python_version", "")).startswith("3.12."):
        failures.append("controller must be the native CPython 3.12 runtime")
    if not str(controller.get("user_sid", "")).startswith("S-1-5-"):
        failures.append("controller user_sid is missing or invalid")
    if controller.get("integrity_sid") != MEDIUM_INTEGRITY:
        failures.append("controller integrity must be medium")
    if controller.get("elevated") is not False:
        failures.append("controller must not be elevated")
    if controller.get("native") is not True:
        failures.append("controller must record a native run")
    profile = _as_mapping(evidence.get("profile"))
    if profile is None:
        failures.append("profile must be a JSON object")
        profile = {}
    if not str(profile.get("sid", "")).startswith("S-1-15-2-"):
        failures.append("profile AppContainer SID is missing or invalid")
    if not profile.get("folder"):
        failures.append("profile folder is missing")
    if not profile.get("name"):
        failures.append("profile name is missing")
    cleanup = _as_mapping(evidence.get("cleanup"))
    if cleanup is None:
        failures.append("cleanup must be a JSON object")
        cleanup = {}
    for key in ("profile_deleted", "fixture_removed", "job_closed", "handles_closed"):
        if cleanup.get(key) is not True:
            failures.append(f"cleanup.{key} must be true")
    if cleanup.get("errors"):
        failures.append(f"cleanup reported errors: {cleanup.get('errors')}")
    deadline = cleanup.get("stop_deadline_seconds")
    if not _is_number(deadline) or not 0 < deadline <= STOP_DEADLINE_SECONDS:
        failures.append("cleanup.stop_deadline_seconds must be within 5 seconds")
    hashes = _as_mapping(evidence.get("source_hashes")) or {}
    for name in (
        "tools/proofs/sec02_integration_windows.py",
        "tools/proofs/sec02_windows.py",
        "omp_core/main.py",
        "omp_core/windows_job.py",
    ):
        if not _is_sha256(hashes.get(name)):
            failures.append(f"source_hashes.{name} must be a SHA-256 digest")
    not_proven = evidence.get("not_proven")
    if not isinstance(not_proven, list) or not not_proven or any(
        not isinstance(item, str) or not item for item in not_proven
    ):
        failures.append("not_proven must record non-empty limitations")
    blocked = _as_mapping(evidence.get("blocked"))
    if blocked is None:
        failures.append("blocked must be a JSON object")
        blocked = {}
    properties = _as_mapping(evidence.get("properties"))
    if properties is None:
        failures.append("properties must be a JSON object")
        properties = {}
    computed = compute_properties(evidence)
    extra = set(properties) - set(PROPERTY_IDS)
    if extra:
        failures.append(f"properties contains unknown ids: {sorted(extra)}")
    for prop in PROPERTY_IDS:
        raw_failures = _property_failures(evidence, prop)
        failures.extend(raw_failures)
        stored = properties.get(prop)
        if stored != computed[prop]:
            failures.append(
                f"Property {prop} claim is '{stored}' but raw evidence derives "
                f"'{computed[prop]}'"
            )
        if computed[prop] != "PASS":
            failures.append(f"Property {prop} is {computed[prop]}, not PASS")
    return failures


def _negative_controls(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Prove the verifier rejects mutations of its own report."""
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("missing_runtime", lambda copy: copy["runtimes"].pop("3.14")),
        ("not_run_property", lambda copy: copy["properties"].__setitem__("B0-3", "NOT RUN")),
        ("exit_bool", lambda copy: copy["runtimes"]["3.12"].__setitem__("exit_code", True)),
        ("raw_claim_mismatch", lambda copy: copy["attributes"].__setitem__("fallback_used", True)),
        ("cleanup_error", lambda copy: copy["cleanup"]["errors"].append("injected")),
        ("verdict_not_pass", lambda copy: copy.__setitem__("verdict", "FAIL")),
        (
            "layout_case_denial",
            lambda copy: copy["layout"]["L2"]["per_runtime"]["3.14"].__setitem__(
                "vault_read_denied", False
            ),
        ),
        (
            "layout_case_token",
            lambda copy: copy["layout"]["L1"]["per_runtime"]["3.12"]["token"].__setitem__(
                "is_app_container", False
            ),
        ),
        (
            "layout_case_missing",
            lambda copy: copy["layout"]["L2"]["per_runtime"].pop("3.14"),
        ),
        (
            "layout_case_cleanup_error",
            lambda copy: copy["layout"]["L1"]["per_runtime"]["3.12"][
                "cleanup_errors"
            ].append("CloseHandle(stdout) returned FALSE"),
        ),
        (
            "descendant_cleanup_error",
            lambda copy: copy["faults"]["descendant"]["cleanup_errors"].append(
                "descendant remained active"
            ),
        ),
        (
            "protected_handle_aggregate",
            lambda copy: copy["layout"]["L2"]["per_runtime"]["3.14"][
                "protected_handle"
            ].__setitem__("usable", True),
        ),
        (
            "canary_mismatch",
            lambda copy: copy["layout"]["canaries"]["vault"].__setitem__(
                "after", "b" * 64
            ),
        ),
        (
            "label_query_failed",
            lambda copy: copy["layout"]["L2"]["security"].__setitem__(
                "label_query", "failed"
            ),
        ),
        (
            "unselected_handle_report",
            lambda copy: (
                copy["layout"]["L1"]["per_runtime"]["3.12"]["protected_handle"][
                    "reported"
                ].__setitem__("valid", True),
                copy["layout"]["L1"]["per_runtime"]["3.12"]["protected_handle"][
                    "reported"
                ].__setitem__(
                    "path",
                    copy["layout"]["L1"]["per_runtime"]["3.12"][
                        "protected_handle"
                    ]["target"],
                ),
            ),
        ),
        (
            "handle_report_missing",
            lambda copy: copy["layout"]["L1"]["per_runtime"]["3.14"][
                "protected_handle"
            ].__setitem__("reported", {}),
        ),
        (
            "dacl_aces_emptied",
            lambda copy: copy["layout"]["L2"]["security"]["dacl"].__setitem__(
                "aces", []
            ),
        ),
        (
            "container_ace_removed",
            lambda copy: copy["layout"]["L2"]["security"]["dacl"].__setitem__(
                "aces",
                [
                    ace
                    for ace in copy["layout"]["L2"]["security"]["dacl"]["aces"]
                    if ace.get("sid") != copy["profile"]["sid"]
                ],
            ),
        ),
        (
            "dacl_protected_unobserved",
            lambda copy: copy["layout"]["L1"]["security"]["dacl"].__setitem__(
                "protected", False
            ),
        ),
    ]
    controls: list[dict[str, Any]] = []
    for name, mutate in mutations:
        candidate = json.loads(json.dumps(evidence))
        try:
            mutate(candidate)
        except BaseException:
            controls.append({"mutation": name, "rejected": False})
            continue
        controls.append({"mutation": name, "rejected": bool(verify_evidence(candidate))})
    return controls


def _blocked_evidence(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "verdict": "BLOCKED",
        "generated_utc": _utc(),
        "reason": reason,
        "controller": {},
        "profile": {},
        "runtimes": {},
        "attributes": {},
        "layout": {},
        "handles": {},
        "faults": {},
        "cleanup": {"errors": []},
        "source_hashes": _source_hashes(),
        "not_run": [reason],
        "not_proven": list(NOT_PROVEN),
        "blocked": {prop: reason for prop in PROPERTY_IDS},
        "verifier_negative_controls": [],
        "properties": {prop: "BLOCKED" for prop in PROPERTY_IDS},
    }


def run_probe() -> tuple[dict[str, Any], int]:
    """Run the native B0 probe and return ``(evidence, exit_code)``.

    Exit code 0 means every required property was proven on this host.  Missing
    prerequisites return BLOCKED with exit code 1.  All owned resources are
    cleaned up in a ``finally`` block; cleanup errors fail the run.
    """
    if os.name != "nt":
        return _blocked_evidence("Host is not Windows"), 1
    try:
        _ensure_prototypes()
        runtimes = sec02.find_python_runtimes()
    except BaseException as exc:
        return _blocked_evidence(
            f"native setup unavailable: {type(exc).__name__}"
        ), 1
    missing = [ver for ver in RUNTIME_KEYS if ver not in runtimes]
    if missing:
        return _blocked_evidence(
            f"missing CPython Worker runtime(s): {', '.join(missing)}"
        ), 1

    fixture = _OwnedFixture()
    user_blob, user_sid = sec01._current_user_sid()
    user_text = sec01._sid_string(user_sid)
    controller = sec01._current_token_summary()
    fake_secret = f"B0-FAKE-SECRET-{secrets.token_hex(8)}"
    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "verdict": "FAIL",
        "generated_utc": _utc(),
        "controller": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "user_sid": user_text,
            "integrity_sid": controller.get("integrity_sid"),
            "elevated": controller.get("elevated"),
            "native": True,
        },
        "profile": {},
        "runtimes": {},
        "attributes": {},
        "layout": {},
        "handles": {},
        "faults": {},
        "cleanup": {},
        "source_hashes": _source_hashes(),
        "not_run": [],
        "not_proven": list(NOT_PROVEN),
        "blocked": {},
        "verifier_negative_controls": [],
        "properties": {},
    }
    stop_state = StopState()
    # Bind before setup so a failure before the first case still records cleanup
    # instead of raising UnboundLocalError and hiding the original cause.
    cases: dict[str, dict[str, dict[str, Any]]] = {"L1": {}, "L2": {}}
    try:
        fixture.allocate()
        evidence["profile"] = {
            "name": fixture.profile_name,
            "sid": fixture.container_sid_str,
            "folder": str(fixture.profile_folder),
        }
        prepared = _prepare_fixture(
            fixture=fixture,
            user_sid=user_sid,
            container_sid=fixture.container_sid_ptr or 0,
            runtimes=runtimes,
        )
        canaries = prepared["canaries"]
        protected_handle = _open_protected_handle(canaries["vault"])
        try:
            for layout in ("L1", "L2"):
                for ver in RUNTIME_KEYS:
                    cases[layout][ver] = _run_layout_case(
                        fixture=fixture,
                        runtime_exe=prepared["runtimes"][ver],
                        ver=ver,
                        layout=layout,
                        workspace=prepared["workspaces"][layout][ver],
                        canaries=canaries,
                        fake_secret=fake_secret,
                        protected_handle=protected_handle,
                        stop_state=stop_state,
                    )
        finally:
            try:
                if not sec01.kernel32.CloseHandle(wintypes.HANDLE(protected_handle)):
                    fixture.cleanup_errors.append("CloseHandle(protected) returned FALSE")
                    fixture.handles_closed = False
            except BaseException as exc:
                fixture.cleanup_errors.append(
                    f"CloseHandle(protected) error: {type(exc).__name__}"
                )
                fixture.handles_closed = False
        hashes_after = {
            name: _sha256(path) for name, path in canaries.items()
        }
        hashes_unchanged = hashes_after == prepared["hashes_before"]
        l1_section = _layout_section(
            cases["L1"],
            prepared["workspaces"]["L1"]["3.12"],
            user_sid=user_text,
            container_sid=fixture.container_sid_str or "",
        )
        l2_section = _layout_section(
            cases["L2"],
            prepared["workspaces"]["L2"]["3.12"],
            user_sid=user_text,
            container_sid=fixture.container_sid_str or "",
        )
        l1_section["hashes_unchanged"] = hashes_unchanged
        l2_section["hashes_unchanged"] = hashes_unchanged
        l1_section["workspace_kind"] = "profile"
        l2_section["workspace_kind"] = "run_checkout"
        selected = "L2" if _layout_ok(l2_section) else "L1" if _layout_ok(l1_section) else "L2"
        selected_cases = cases[selected]
        evidence["runtimes"] = {
            ver: _runtime_section(selected_cases[ver]) for ver in RUNTIME_KEYS
        }
        evidence["attributes"] = {
            "names": list(ATTRIBUTE_NAMES),
            "attach_mode": "at_create",
            "job_handle_inheritable": _handle_inheritable(fixture.job or 0),
            "fallback_used": False,
            "job_member_before_resume": all(
                case["job_member"] for case in selected_cases.values()
            ),
        }
        all_cases = [
            case for layout_cases in cases.values() for case in layout_cases.values()
        ]
        evidence["layout"] = {
            "selected": selected,
            "L1": l1_section,
            "L2": l2_section,
            "same_user_control": _same_user_control(
                prepared["vault"], prepared["state"]
            ),
            "runtimes_observed": [
                ver for ver in RUNTIME_KEYS
                if selected_cases[ver]["status"] == "PASS"
            ],
            "canaries": {
                name: {
                    "before": prepared["hashes_before"][name],
                    "after": hashes_after[name],
                }
                for name in canaries
            },
        }
        evidence["handles"] = {
            "allowlist": list(HANDLE_ALLOWLIST),
            "inheritable_flags": {
                "stdin": all(case["stdio_inheritable"] for case in all_cases),
                "stdout": all(case["stdio_inheritable"] for case in all_cases),
                "stderr": all(case["stdio_inheritable"] for case in all_cases),
                "protected": all(
                    case["protected_handle"]["inheritable"] is True
                    for case in all_cases
                ),
                "job": _handle_inheritable(fixture.job or 0),
            },
            "protected_handle_usable": any(
                case["protected_handle"]["usable"] for case in all_cases
            ),
            "fake_secret_visible": any(
                case["fake_secret_visible"] for case in all_cases
            ),
            "enabled_privileges": sorted({
                privilege["name"]
                for case in all_cases
                for privilege in (case["token"].get("privileges") or [])
                if _enabled_privilege(privilege)
            }),
        }
        evidence["faults"] = _fault_matrix(
            fixture=fixture,
            runtime_exe=prepared["runtimes"]["3.12"],
            workspace=prepared["workspaces"]["L2"]["3.12"],
            canaries=canaries,
            fake_secret=fake_secret,
        )
    except BaseException as exc:
        evidence["verdict"] = "FAIL"
        evidence["reason"] = redact(
            f"{type(exc).__name__}: {exc}",
            [fake_secret],
            [str(fixture.root), str(fixture.profile_folder)],
        )
    finally:
        fixture.cleanup()
        case_handles_closed = all(
            case.get("handles_closed") is True
            for layout_cases in cases.values()
            for case in layout_cases.values()
        )
        evidence["cleanup"] = {
            "profile_deleted": fixture.profile_name is None,
            "fixture_removed": fixture.temp is None,
            "job_closed": fixture.job is None,
            "handles_closed": fixture.handles_closed and case_handles_closed,
            "errors": list(fixture.cleanup_errors),
            "stop_deadline_seconds": STOP_DEADLINE_SECONDS,
        }

    evidence["properties"] = compute_properties(evidence)
    if not evidence.get("blocked"):
        controls = _negative_controls(evidence)
        evidence["verifier_negative_controls"] = controls
        evidence["properties"] = compute_properties(evidence)
        if not all(record["rejected"] for record in controls):
            evidence["properties"]["B0-7"] = "FAIL"
    # The verifier requires verdict == PASS, so set the provisional verdict
    # before the self-check and downgrade it only if a failure is found.
    evidence["verdict"] = "PASS"
    self_check = verify_evidence(evidence)
    if self_check:
        evidence["verdict"] = "FAIL"
        evidence["self_check_failures"] = self_check[:10]
        return evidence, 1
    return evidence, 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for the SEC-02 B0 integration probe."""
    parser = argparse.ArgumentParser(
        description="SEC-02 B0 integration probe (native Windows)"
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    run_cmd = sub.add_parser("run", help="Execute the native B0 probe")
    run_cmd.add_argument("--out", type=Path, help="Path to write JSON evidence")
    verify_cmd = sub.add_parser("verify", help="Verify a JSON evidence report")
    verify_cmd.add_argument("report", type=Path, help="Path to evidence report")
    args = parser.parse_args(argv)

    if args.subcommand == "run":
        evidence, exit_code = run_probe()
        text = json.dumps(evidence, indent=2, ensure_ascii=False)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"Evidence written to {args.out}")
        else:
            print(text)
        print(f"verdict={evidence.get('verdict')} exit={exit_code}")
        return exit_code

    if args.subcommand == "verify":
        if not args.report.is_file():
            print(f"Error: report file not found: {args.report}", file=sys.stderr)
            return 1
        try:
            data = json.loads(args.report.read_text(encoding="utf-8"))
        except BaseException as exc:
            print(f"Error reading JSON: {type(exc).__name__}", file=sys.stderr)
            return 1
        failures = verify_evidence(data)
        if failures:
            print("VERIFICATION FAILED:")
            for failure in failures:
                print(f" - {failure}")
            return 1
        print("VERIFICATION PASSED: all SEC-02 B0 properties are proven.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
