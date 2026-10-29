"""SEC-02-B1b: Offline L2 layout, environment sanitization, manifest, and artifact reader.

This module provides offline primitives for AppContainer workers:
1. Environment allowlist sanitization (no parent/task leakage, safe paths).
2. Internal verification manifest schema (omp.sec02.b1b.manifest.v1) with atomic publication.
3. Disposable owned L2 layout with Windows DACL and Low Integrity Label.
4. Secure handle-based artifact reader with Job Object containment proof.

This is an offline security component used by tests and verification; it does
not modify the public CLI, orchestrator, or checkpoint schemas.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
from typing import Any, Mapping, Sequence

MANIFEST_SCHEMA_VERSION = "omp.sec02.b1b.manifest.v1"
LOW_INTEGRITY_SID = "S-1-16-4096"
MAX_ARTIFACT_SIZE_BYTES = 64 * 1024  # 64 KiB
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OID_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_PATTERN = re.compile(
    r"\b(?:bearer|token|secret|password|passwd|api[_-]?key|auth(?:orization)?|credential)\b(?:[\s:=-]+(?:bearer\s+)?)?['\"]?[^\s,;\)\}\]\'\"]+['\"]?"
    r"|(?:token|secret|password|bearer|auth)[_=-]?[A-Za-z0-9_\-]{4,}"
    r"|[A-Za-z0-9_]*(?:token|secret|password|bearer|auth)[A-Za-z0-9_]*"
    r"|\b[0-9a-fA-F]{32,64}\b"
    r"|(?:REVIEW_)?FAKE_[A-Za-z0-9_]+",
    re.IGNORECASE,
)
_DANGEROUS_ENV_KEYS = frozenset({
    "PYTHONPATH",
    "PYTHONHOME",
    "COMSPEC",
    "SHELL",
    "LD_PRELOAD",
    "PATHEXT",
    "PROMPT",
    "PS1",
})

# Win32 Constants
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_ALL_ACCESS = 0x1F01FF
_FILE_GENERIC_READ = 0x120089
_FILE_GENERIC_EXECUTE = 0x1200A0
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_TYPE_DISK = 0x0001
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_LABEL_SECURITY_INFORMATION = 0x00000010
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_UNPROTECTED_DACL_SECURITY_INFORMATION = 0x40000000
_ACL_REVISION = 2
_ACL_SIZE_INFORMATION_CLASS = 2
_OBJECT_INHERIT_ACE = 0x1
_CONTAINER_INHERIT_ACE = 0x2
_SYSTEM_MANDATORY_LABEL_NO_WRITE_UP = 0x1
_ACCESS_ALLOWED_ACE_TYPE = 0x0
_ACCESS_DENIED_ACE_TYPE = 0x1
_SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
_SE_DACL_PROTECTED = 0x1000
_ERROR_INSUFFICIENT_BUFFER = 122
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_VOLUME_NAME_DOS = 0x0
_FILE_NAME_NORMALIZED = 0x0


def _sanitize_text(text: str) -> str:
    """Sanitize secrets, tokens, and credentials from exception messages."""
    if not text:
        return text
    return _SECRET_PATTERN.sub("[REDACTED]", text)


def _sanitize_exception_chain(exc: BaseException) -> BaseException:
    """Traverse __cause__ and __context__ recursively, redacting sensitive substrings from all nodes."""
    stack = [exc]
    visited = set()
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in visited:
            continue
        visited.add(id(cur))
        if cur.args:
            cur.args = tuple(_sanitize_text(str(a)) if isinstance(a, str) else a for a in cur.args)
        for attr in ("filename", "filename2", "strerror"):
            val = getattr(cur, attr, None)
            if isinstance(val, str):
                try:
                    setattr(cur, attr, _sanitize_text(val))
                except (AttributeError, TypeError):
                    pass
        stack.extend((cur.__cause__, cur.__context__))
    return exc


class TrustedLayoutError(OSError):
    """Base exception for layout, manifest, environment, or artifact errors."""


class TrustedLayoutSecurityError(TrustedLayoutError):
    """Raised when a security boundary, policy, or invariant is violated."""


class TrustedLayoutBlockedError(TrustedLayoutSecurityError):
    """Raised when an OS prerequisite or containment proof is missing."""


# Win32 Structures
class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
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


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", _FILETIME),
        ("ftLastAccessTime", _FILETIME),
        ("ftLastWriteTime", _FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:
    if os.name != "nt":
        raise TrustedLayoutBlockedError("Windows kernel32 requires Windows")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p
    ]
    k32.ReadFile.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetFileType.argtypes = [wintypes.HANDLE]
    k32.GetFileType.restype = wintypes.DWORD
    k32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)
    ]
    k32.GetFileInformationByHandle.restype = wintypes.BOOL
    k32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD
    ]
    k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree.restype = ctypes.c_void_p
    k32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    k32.MoveFileExW.restype = wintypes.BOOL
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    return k32


def _advapi32() -> ctypes.WinDLL:
    if os.name != "nt":
        raise TrustedLayoutBlockedError("Windows advapi32 requires Windows")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)
    ]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = wintypes.DWORD
    advapi.InitializeAcl.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD
    ]
    advapi.InitializeAcl.restype = wintypes.BOOL
    advapi.AddAccessAllowedAceEx.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
    ]
    advapi.AddAccessAllowedAceEx.restype = wintypes.BOOL
    advapi.AddMandatoryAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
    ]
    advapi.AddMandatoryAce.restype = wintypes.BOOL
    advapi.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    ]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)
    ]
    advapi.GetFileSecurityW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)
    ]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorSacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)
    ]
    advapi.GetSecurityDescriptorSacl.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD)
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int
    ]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
    ]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    return advapi


def _sid_to_string(advapi: ctypes.WinDLL, k32: ctypes.WinDLL, sid_ptr: ctypes.c_void_p) -> str:
    string_ptr = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_ptr)):
        raise TrustedLayoutError(
            f"ConvertSidToStringSidW failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    try:
        return string_ptr.value or ""
    finally:
        k32.LocalFree(string_ptr)


def current_user_sid() -> str:
    """Return the current Windows process user SID string.

    Raises:
        TrustedLayoutBlockedError: not running on Windows or token query failed.
    """
    if os.name != "nt":
        raise TrustedLayoutBlockedError("current_user_sid requires Windows")
    k32 = _kernel32()
    advapi = _advapi32()
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise TrustedLayoutBlockedError(
            f"OpenProcessToken failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise TrustedLayoutBlockedError(
                f"GetTokenInformation(TOKEN_USER) failed: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        sid_ptr = ctypes.c_void_p.from_buffer(buffer)
        return _sid_to_string(advapi, k32, sid_ptr)
    finally:
        k32.CloseHandle(token)


def build_trusted_environment(
    run_id: str,
    task_id: str,
    *,
    temp_dir: Path,
    runtime_path: Path | None = None,
    local_app_data: Path | str | None = None,
    extra_omp_keys: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct an explicit allowlist environment mapping for an AppContainer child.

    The returned dictionary does not copy ``os.environ`` from the caller, does
    not accept arbitrary variables from untrusted task input, and strictly rejects
    dangerous variables (such as PYTHONPATH/PYTHONHOME) and duplicate keys.

    Args:
        run_id: A valid run identifier matching ``^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$``.
        task_id: Non-empty task identifier string without NUL.
        temp_dir: Owned temporary directory inside the run workspace.
        runtime_path: Optional approved directory containing the Python executable.
        local_app_data: Optional Path to the AppContainer profile folder (for LOCALAPPDATA).
        extra_omp_keys: Optional mapping of additional variables starting with ``OMP_``.

    Returns:
        A sanitized dictionary mapping environment variable names to string values.

    Raises:
        ValueError: any input argument is invalid or contains forbidden characters.
        TrustedLayoutSecurityError: an attempt to override restricted keys is detected.
    """
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
        raise ValueError(f"invalid run_id: {run_id!r}")
    if not isinstance(task_id, str) or not task_id or "\0" in task_id:
        raise ValueError("task_id must be a non-empty string without NUL")

    if not isinstance(temp_dir, Path):
        raise ValueError(f"temp_dir must be a Path instance, got {type(temp_dir).__name__}")
    if not temp_dir.is_absolute():
        raise ValueError(f"temp_dir must be an absolute path: {temp_dir!r}")
    if not temp_dir.exists() or not temp_dir.is_dir():
        raise ValueError(f"temp_dir must be an existing directory: {temp_dir!r}")
    if _is_reparse_point(temp_dir):
        raise TrustedLayoutSecurityError(f"temp_dir is a reparse point: {temp_dir!r}")
    curr_t = temp_dir.parent
    while curr_t != curr_t.parent:
        if _is_reparse_point(curr_t):
            raise TrustedLayoutSecurityError(f"temp_dir ancestor {curr_t} is a reparse point")
        curr_t = curr_t.parent

    temp_path_str = str(temp_dir)
    if "\0" in temp_path_str or ";" in temp_path_str:
        raise ValueError("temp_dir must not contain NUL or ';'")

    system_root = os.environ.get("SystemRoot") or "C:\\Windows"
    if "\0" in system_root or not os.path.isdir(system_root):
        raise TrustedLayoutSecurityError(f"invalid or missing SystemRoot: {system_root!r}")
    system_drive = os.path.splitdrive(system_root)[0] or "C:"

    system32 = str(Path(system_root) / "System32")
    if runtime_path is not None:
        if not isinstance(runtime_path, Path):
            raise ValueError(f"runtime_path must be a Path instance, got {type(runtime_path).__name__}")
        if not runtime_path.is_absolute():
            raise ValueError(f"runtime_path must be an absolute path: {runtime_path!r}")
        runtime_str = str(runtime_path)
        if "\0" in runtime_str or ";" in runtime_str:
            raise ValueError("runtime_path must not contain NUL or ';'")
        if not runtime_path.exists() or not runtime_path.is_dir():
            raise ValueError(f"runtime_path must be an existing directory: {runtime_path!r}")
        if _is_reparse_point(runtime_path):
            raise TrustedLayoutSecurityError(f"runtime_path is a reparse point: {runtime_path!r}")
        curr_rt = runtime_path.parent
        while curr_rt != curr_rt.parent:
            if _is_reparse_point(curr_rt):
                raise TrustedLayoutSecurityError(f"runtime_path ancestor {curr_rt} is a reparse point")
            curr_rt = curr_rt.parent
        path_value = f"{runtime_str};{system32}"
    else:
        path_value = system32

    env: dict[str, str] = {
        "SystemRoot": system_root,
        "SystemDrive": system_drive,
        "PATH": path_value,
        "TEMP": temp_path_str,
        "TMP": temp_path_str,
        "OMP_RUN_ID": run_id,
        "OMP_TASK_ID": task_id,
    }

    if local_app_data is not None:
        lad_path = Path(local_app_data)
        if not lad_path.is_absolute():
            raise ValueError(f"local_app_data must be an absolute path: {local_app_data!r}")
        lad_str = str(lad_path)
        if "\0" in lad_str:
            raise ValueError("local_app_data must not contain NUL")
        if lad_path.exists() and _is_reparse_point(lad_path):
            raise TrustedLayoutSecurityError(f"local_app_data is a reparse point: {local_app_data!r}")
        env["LOCALAPPDATA"] = lad_str

    seen_keys: set[str] = {k.upper() for k in env}

    if extra_omp_keys is not None:
        for k, v in extra_omp_keys.items():
            if not isinstance(k, str) or not k:
                raise ValueError("extra environment keys must be non-empty strings")
            if not isinstance(v, str):
                raise ValueError(f"value for key {k!r} must be a string")
            if not v:
                raise ValueError(f"empty value forbidden for key {k!r}")
            if "\0" in k or "\0" in v:
                raise ValueError(f"key or value contains NUL: {k!r}")
            if not _ENV_KEY_PATTERN.match(k):
                raise ValueError(f"invalid environment key name: {k!r}")
            k_upper = k.upper()
            if k_upper in seen_keys:
                raise ValueError(f"duplicate environment key: {k!r}")
            if k_upper in _DANGEROUS_ENV_KEYS:
                raise TrustedLayoutSecurityError(f"forbidden environment override: {k!r}")
            if not k.startswith("OMP_"):
                raise TrustedLayoutSecurityError(f"extra key must start with 'OMP_': {k!r}")
            seen_keys.add(k_upper)
            env[k] = v

    return env


class TrustedManifest:
    """Internal verification manifest (omp.sec02.b1b.manifest.v1).

    Binds task identity, run identity, nonce, candidate bytes SHA-256,
    canonical tree identity, approved invocation arguments, timeout, and
    expected artifact relative path. This manifest is published atomically
    into the trusted vault directory before Worker execution.
    """

    __slots__ = (
        "schema_version",
        "task_id",
        "run_id",
        "nonce",
        "verifier_version",
        "candidate_bytes_sha256",
        "canonical_tree_oid",
        "tree_algorithm",
        "approved_argv",
        "timeout_seconds",
        "spec_hash",
        "expected_artifact_relpath",
    )

    def __init__(
        self,
        *,
        schema_version: str = MANIFEST_SCHEMA_VERSION,
        task_id: str,
        run_id: str,
        nonce: str,
        verifier_version: str,
        candidate_bytes_sha256: str,
        canonical_tree_oid: str,
        tree_algorithm: str,
        approved_argv: Sequence[str],
        timeout_seconds: float | int,
        spec_hash: str,
        expected_artifact_relpath: str,
    ) -> None:
        if schema_version != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported manifest schema_version: {schema_version!r}")
        if not isinstance(task_id, str) or not task_id or "\0" in task_id:
            raise ValueError("task_id must be a non-empty string without NUL")
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        if not isinstance(nonce, str) or len(nonce) < 16 or "\0" in nonce:
            raise ValueError("nonce must be a string with at least 16 characters")
        if not isinstance(verifier_version, str) or not verifier_version:
            raise ValueError("verifier_version must be a non-empty string")
        if not isinstance(candidate_bytes_sha256, str) or not _SHA256_HEX_PATTERN.match(candidate_bytes_sha256):
            raise ValueError("candidate_bytes_sha256 must be a 64-character lowercase hex string")
        if tree_algorithm not in ("sha1", "sha256"):
            raise ValueError(f"unsupported tree_algorithm: {tree_algorithm!r}")
        if not isinstance(canonical_tree_oid, str) or not _OID_PATTERN.match(canonical_tree_oid):
            raise ValueError("canonical_tree_oid must be a 40 or 64 character lowercase hex string")
        if tree_algorithm == "sha1" and len(canonical_tree_oid) != 40:
            raise ValueError("canonical_tree_oid must be 40 hex characters for sha1 tree_algorithm")
        if tree_algorithm == "sha256" and len(canonical_tree_oid) != 64:
            raise ValueError("canonical_tree_oid must be 64 hex characters for sha256 tree_algorithm")
        if isinstance(approved_argv, (str, bytes)) or not approved_argv:
            raise ValueError("approved_argv must be a non-empty sequence of strings")
        for arg in approved_argv:
            if not isinstance(arg, str) or not arg or "\0" in arg:
                raise ValueError("approved_argv entries must be non-empty strings without NUL")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a finite positive number (bool not accepted)")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be strictly positive: {timeout_seconds!r}")
        if not isinstance(spec_hash, str) or not _SHA256_HEX_PATTERN.match(spec_hash):
            raise ValueError("spec_hash must be a 64-character lowercase hex string")
        if not isinstance(expected_artifact_relpath, str) or not expected_artifact_relpath:
            raise ValueError("expected_artifact_relpath must be a non-empty string")
        if (
            expected_artifact_relpath.startswith("/")
            or "\\" in expected_artifact_relpath
            or "\0" in expected_artifact_relpath
            or ":" in expected_artifact_relpath
            or any(part in ("..", ".", "") for part in expected_artifact_relpath.split("/"))
        ):
            raise ValueError(f"invalid expected_artifact_relpath: {expected_artifact_relpath!r}")

        self.schema_version = schema_version
        self.task_id = task_id
        self.run_id = run_id
        self.nonce = nonce
        self.verifier_version = verifier_version
        self.candidate_bytes_sha256 = candidate_bytes_sha256
        self.canonical_tree_oid = canonical_tree_oid
        self.tree_algorithm = tree_algorithm
        self.approved_argv = tuple(approved_argv)
        self.timeout_seconds = float(timeout_seconds)
        self.spec_hash = spec_hash
        self.expected_artifact_relpath = expected_artifact_relpath

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrustedManifest":
        """Validate and construct a TrustedManifest from a dictionary.

        Raises:
            ValueError: required field missing, unknown field present, or type mismatch.
        """
        expected_keys = {
            "schema_version",
            "task_id",
            "run_id",
            "nonce",
            "verifier_version",
            "candidate_bytes_sha256",
            "canonical_tree_oid",
            "tree_algorithm",
            "approved_argv",
            "timeout_seconds",
            "spec_hash",
            "expected_artifact_relpath",
        }
        actual_keys = set(data.keys())
        unknown = actual_keys - expected_keys
        if unknown:
            raise ValueError(f"unknown fields in manifest: {sorted(unknown)}")
        missing = expected_keys - actual_keys
        if missing:
            raise ValueError(f"missing fields in manifest: {sorted(missing)}")
        return cls(
            schema_version=data["schema_version"],
            task_id=data["task_id"],
            run_id=data["run_id"],
            nonce=data["nonce"],
            verifier_version=data["verifier_version"],
            candidate_bytes_sha256=data["candidate_bytes_sha256"],
            canonical_tree_oid=data["canonical_tree_oid"],
            tree_algorithm=data["tree_algorithm"],
            approved_argv=data["approved_argv"],
            timeout_seconds=data["timeout_seconds"],
            spec_hash=data["spec_hash"],
            expected_artifact_relpath=data["expected_artifact_relpath"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the dictionary representation of this manifest."""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "nonce": self.nonce,
            "verifier_version": self.verifier_version,
            "candidate_bytes_sha256": self.candidate_bytes_sha256,
            "canonical_tree_oid": self.canonical_tree_oid,
            "tree_algorithm": self.tree_algorithm,
            "approved_argv": list(self.approved_argv),
            "timeout_seconds": self.timeout_seconds,
            "spec_hash": self.spec_hash,
            "expected_artifact_relpath": self.expected_artifact_relpath,
        }

    def to_json(self) -> str:
        """Serialize this manifest to canonical formatted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def publish_atomic(self, vault_dir: Path) -> Path:
        """Atomically write and fsync this manifest into ``vault_dir``.

        Never overwrites an existing different manifest (no-clobber guarantee).

        Args:
            vault_dir: Path to the trusted vault directory.

        Returns:
            The Path of the published manifest file.

        Raises:
            TrustedLayoutSecurityError: manifest file already exists with different contents.
            TrustedLayoutError: I/O error during publication.
        """
        target = vault_dir / "approved_manifest.json"
        content_bytes = (self.to_json() + "\n").encode("utf-8")
        if target.exists():
            try:
                existing_bytes = target.read_bytes()
                if existing_bytes == content_bytes:
                    return target
            except OSError as exc:
                _sanitize_exception_chain(exc)
                clean_msg = _sanitize_text(str(exc))
                raise TrustedLayoutError(f"cannot read existing manifest: {clean_msg}") from exc
            raise TrustedLayoutSecurityError(
                "refusing to overwrite existing manifest with differing content"
            )

        temp_name = f".tmp_manifest_{secrets.token_hex(8)}"
        temp_path = vault_dir / temp_name
        cleanup_error: BaseException | None = None
        try:
            fd = os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content_bytes)
                f.flush()
                os.fsync(f.fileno())

            # Atomic commit without clobber (MoveFileEx without MOVEFILE_REPLACE_EXISTING)
            try:
                os.rename(temp_path, target)
                return target
            except FileExistsError:
                try:
                    existing_bytes = target.read_bytes()
                except OSError as exc:
                    _sanitize_exception_chain(exc)
                    clean_msg = _sanitize_text(str(exc))
                    raise TrustedLayoutError(f"cannot read existing manifest: {clean_msg}") from exc
                if existing_bytes == content_bytes:
                    return target
                raise TrustedLayoutSecurityError(
                    "refusing to overwrite existing manifest with differing content"
                )
        except BaseException as exc:
            _sanitize_exception_chain(exc)
            cleanup_error: Exception | None = None
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError as ce:
                cleanup_error = ce
                _sanitize_exception_chain(cleanup_error)

            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                if cleanup_error is not None:
                    exc.__context__ = cleanup_error
                raise exc

            if isinstance(exc, (TrustedLayoutSecurityError, TrustedLayoutBlockedError)):
                raise exc

            clean_op = _sanitize_text(str(exc))
            if cleanup_error is not None:
                clean_cl = _sanitize_text(str(cleanup_error))
                err_msg = f"manifest atomic publish failed: {clean_op}; cleanup failed: {clean_cl}"
            else:
                err_msg = f"manifest atomic publish failed: {clean_op}"

            err = TrustedLayoutError(err_msg)
            if cleanup_error is not None:
                cleanup_error.__context__ = exc
                err.__cause__ = cleanup_error
            else:
                err.__cause__ = exc
            raise _sanitize_exception_chain(err) from exc
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
        return target

    def verify_candidate(
        self,
        candidate_bytes: bytes,
        invocation_argv: Sequence[str],
    ) -> bool:
        """Verify that candidate bytes and invocation arguments match this manifest.

        Raises:
            TrustedLayoutSecurityError: SHA-256 drift or invocation mismatch detected.
        """
        digest = hashlib.sha256(candidate_bytes).hexdigest()
        if digest != self.candidate_bytes_sha256:
            raise TrustedLayoutSecurityError(
                "candidate bytes SHA-256 does not match approved manifest"
            )
        if tuple(invocation_argv) != self.approved_argv:
            raise TrustedLayoutSecurityError(
                "invocation arguments do not match approved manifest"
            )
        return True


class OwnedL2Layout:
    """Manages an exclusively owned L2 workspace layout with Windows DACL and Low Label.

    Structure:
      ``<root>/.omp/runs/<run_id>/checkout`` (Worker workspace, Low Integrity, Container Full Access)
      ``<root>/.omp/runs/<run_id>/vault``    (Trusted vault, User-only, Container NO ACE)
      ``<root>/.omp/runs/<run_id>/state``    (Trusted state, User-only, Container NO ACE)
      ``<root>/.omp/runs/<run_id>/log``      (Trusted log, User-only, Container NO ACE)
      ``<root>/.omp/runs/<run_id>/tmp``      (Worker temp, Container Full Access)
    """

    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        user_sid: str,
        container_sid: str,
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.user_sid = user_sid
        self.container_sid = container_sid
        self.omp_dir = root / ".omp"
        self.runs_dir = self.omp_dir / "runs"
        self.run_dir = self.runs_dir / run_id
        self.checkout_dir = self.run_dir / "checkout"
        self.vault_dir = self.run_dir / "vault"
        self.state_dir = self.run_dir / "state"
        self.log_dir = self.run_dir / "log"
        self.temp_dir = self.run_dir / "tmp"
        self._cleaned = False

    @classmethod
    def create(
        cls,
        parent_dir: Path,
        run_id: str,
        *,
        user_sid: str,
        container_sid: str,
    ) -> "OwnedL2Layout":
        """Create and securely configure a new disposable L2 layout.

        Args:
            parent_dir: Directory in which to create the new disposable root.
            run_id: Non-empty valid run identifier.
            user_sid: Controller user SID string.
            container_sid: AppContainer SID string.

        Returns:
            Configured and verified OwnedL2Layout instance.

        Raises:
            TrustedLayoutSecurityError: root already exists or reparse point detected.
            TrustedLayoutBlockedError: native security setup or verification failed.
        """
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        if not user_sid or not container_sid:
            raise ValueError("user_sid and container_sid must be non-empty")
        if not parent_dir.is_dir():
            raise TrustedLayoutError(f"parent_dir is not an existing directory: {parent_dir}")
        if _is_reparse_point(parent_dir):
            raise TrustedLayoutSecurityError(f"parent_dir is a reparse point: {parent_dir}")

        root = parent_dir / f"omp_l2_{secrets.token_hex(8)}"
        try:
            root.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            raise TrustedLayoutSecurityError(f"cannot create disposable root {root}: {exc}") from exc

        layout = cls(root=root, run_id=run_id, user_sid=user_sid, container_sid=container_sid)
        try:
            layout._setup_directories_and_security()
            layout._verify_security_configuration()
            return layout
        except BaseException as exc:
            clean_exc: BaseException | None = None
            try:
                layout.cleanup()
            except BaseException as ce:
                clean_exc = ce
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                if clean_exc is not None and not isinstance(clean_exc, (KeyboardInterrupt, SystemExit)):
                    try:
                        exc.add_note(f"cleanup error: {_sanitize_text(str(clean_exc))}")
                    except AttributeError:
                        pass
                raise exc
            if clean_exc is not None:
                raise TrustedLayoutError(
                    f"layout setup failed: {_sanitize_text(str(exc))}; cleanup failed: {_sanitize_text(str(clean_exc))}"
                ) from exc
            raise

    def _setup_directories_and_security(self) -> None:
        """Create directories and apply protected DACLs and mandatory label."""
        for d in (
            self.omp_dir, self.runs_dir, self.run_dir,
            self.checkout_dir, self.vault_dir, self.state_dir, self.log_dir, self.temp_dir,
        ):
            d.mkdir(parents=False, exist_ok=False)
            if _is_reparse_point(d):
                raise TrustedLayoutSecurityError(f"directory is a reparse point: {d}")

        if os.name == "nt":
            # Apply traverse DACL on parent containers so container can reach checkout
            _set_traverse_dacl(self.root, self.user_sid, self.container_sid)
            _set_traverse_dacl(self.omp_dir, self.user_sid, self.container_sid)
            _set_traverse_dacl(self.runs_dir, self.user_sid, self.container_sid)
            _set_traverse_dacl(self.run_dir, self.user_sid, self.container_sid)

            # Apply full access with inheritance on checkout and temp
            _set_container_full_dacl(self.checkout_dir, self.user_sid, self.container_sid)
            _set_container_full_dacl(self.temp_dir, self.user_sid, self.container_sid)

            # Apply user-only DACL (no ACE for container) on vault, state, log
            _set_user_only_dacl(self.vault_dir, self.user_sid)
            _set_user_only_dacl(self.state_dir, self.user_sid)
            _set_user_only_dacl(self.log_dir, self.user_sid)

            # Set explicit low mandatory integrity label on checkout
            _set_mandatory_label(self.checkout_dir, LOW_INTEGRITY_SID)

    def _verify_security_configuration(self) -> None:
        """Query DACLs and mandatory label; fail-closed on any failure."""
        if os.name != "nt":
            return

        # Verify ancestors have protected DACL with user full and container traverse
        traverse_mask = _FILE_GENERIC_EXECUTE
        for name, d in (
            ("root", self.root),
            ("omp_dir", self.omp_dir),
            ("runs_dir", self.runs_dir),
            ("run_dir", self.run_dir),
        ):
            dacl = _query_dacl(d, self.user_sid, self.container_sid)
            if not dacl["protected"] or not dacl["user_ace"] or not dacl["container_ace"] or dacl["extra_aces"]:
                raise TrustedLayoutBlockedError(f"{name} DACL verification failed")
            if len(dacl["aces"]) != 2:
                raise TrustedLayoutSecurityError(f"{name} DACL must have exactly 2 ACEs, got {len(dacl['aces'])}")
            for ace in dacl["aces"]:
                if ace["sid"] == self.user_sid:
                    if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or ace["flags"] != 0:
                        raise TrustedLayoutSecurityError(f"{name} user traverse ACE invalid: {ace}")
                elif ace["sid"] == self.container_sid:
                    if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != traverse_mask or ace["flags"] != 0:
                        raise TrustedLayoutSecurityError(f"{name} container traverse ACE invalid: {ace}")
                else:
                    raise TrustedLayoutSecurityError(f"{name} unexpected ACE SID: {ace}")

        # Verify checkout has container ACE and user ACE, is protected, and has exact mask/flags
        checkout_dacl = _query_dacl(self.checkout_dir, self.user_sid, self.container_sid)
        if not checkout_dacl["protected"] or not checkout_dacl["container_ace"] or not checkout_dacl["user_ace"]:
            raise TrustedLayoutBlockedError("checkout_dir DACL verification failed")
        if checkout_dacl["extra_aces"]:
            raise TrustedLayoutSecurityError(
                f"checkout_dir DACL contains unauthorized ACE(s): {checkout_dacl['extra_aces']}"
            )
        if len(checkout_dacl["aces"]) != 2:
            raise TrustedLayoutSecurityError(f"checkout_dir DACL must have exactly 2 ACEs, got {len(checkout_dacl['aces'])}")
        for ace in checkout_dacl["aces"]:
            if ace["sid"] == self.container_sid:
                if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or (ace["flags"] & 3) != 3:
                    raise TrustedLayoutSecurityError(f"checkout_dir container ACE invalid: {ace}")
            elif ace["sid"] == self.user_sid:
                if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or (ace["flags"] & 3) != 3:
                    raise TrustedLayoutSecurityError(f"checkout_dir user ACE invalid: {ace}")
            else:
                raise TrustedLayoutSecurityError(f"checkout_dir unexpected ACE SID: {ace}")

        # Verify temp has container ACE and user ACE, is protected, and has exact mask/flags
        temp_dacl = _query_dacl(self.temp_dir, self.user_sid, self.container_sid)
        if not temp_dacl["protected"] or not temp_dacl["container_ace"] or not temp_dacl["user_ace"]:
            raise TrustedLayoutBlockedError("temp_dir DACL verification failed")
        if temp_dacl["extra_aces"]:
            raise TrustedLayoutSecurityError(
                f"temp_dir DACL contains unauthorized ACE(s): {temp_dacl['extra_aces']}"
            )
        if len(temp_dacl["aces"]) != 2:
            raise TrustedLayoutSecurityError(f"temp_dir DACL must have exactly 2 ACEs, got {len(temp_dacl['aces'])}")
        for ace in temp_dacl["aces"]:
            if ace["sid"] == self.container_sid:
                if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or (ace["flags"] & 3) != 3:
                    raise TrustedLayoutSecurityError(f"temp_dir container ACE invalid: {ace}")
            elif ace["sid"] == self.user_sid:
                if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or (ace["flags"] & 3) != 3:
                    raise TrustedLayoutSecurityError(f"temp_dir user ACE invalid: {ace}")
            else:
                raise TrustedLayoutSecurityError(f"temp_dir unexpected ACE SID: {ace}")

        # Verify vault, state, log have NO container ACE, are protected, and have NO extra ACEs
        for name, d in (("vault_dir", self.vault_dir), ("state_dir", self.state_dir), ("log_dir", self.log_dir)):
            dacl = _query_dacl(d, self.user_sid, self.container_sid)
            if not dacl["protected"] or dacl["container_ace"] or not dacl["user_ace"]:
                raise TrustedLayoutBlockedError(f"{name} DACL verification failed")
            if dacl["extra_aces"]:
                raise TrustedLayoutSecurityError(
                    f"{name} DACL contains unauthorized ACE(s): {dacl['extra_aces']}"
                )
            if len(dacl["aces"]) != 1:
                raise TrustedLayoutSecurityError(f"{name} DACL must have exactly 1 ACE, got {len(dacl['aces'])}")
            for ace in dacl["aces"]:
                if ace["sid"] == self.user_sid:
                    if ace["type"] != _ACCESS_ALLOWED_ACE_TYPE or ace["mask"] != _FILE_ALL_ACCESS or (ace["flags"] & 3) != 3:
                        raise TrustedLayoutSecurityError(f"{name} user ACE invalid: {ace}")
                else:
                    raise TrustedLayoutSecurityError(f"{name} unexpected ACE SID: {ace}")

        # Verify checkout has explicit low integrity label
        label = _query_mandatory_label(self.checkout_dir)
        if label != LOW_INTEGRITY_SID:
            raise TrustedLayoutBlockedError(
                f"checkout_dir mandatory label mismatch: expected {LOW_INTEGRITY_SID}, got {label!r}"
            )

    def cleanup(self) -> None:
        """Safely remove the disposable root and its contents."""
        if self._cleaned:
            return
        if not self.root.exists():
            self._cleaned = True
            return
        errors: list[str] = []
        if os.name == "nt":
            # Reset DACLs on subdirectories so current process can remove them without permission errors
            for d in (self.checkout_dir, self.temp_dir, self.vault_dir, self.state_dir, self.log_dir):
                if d.exists():
                    try:
                        _reset_dacl_for_deletion(d)
                    except Exception as exc:
                        errors.append(f"reset DACL on {d.name} failed: {_sanitize_text(str(exc))}")
            if self.root.exists():
                try:
                    _reset_dacl_for_deletion(self.root)
                except Exception as exc:
                    errors.append(f"reset DACL on root failed: {_sanitize_text(str(exc))}")
        try:
            shutil.rmtree(self.root, ignore_errors=False)
        except Exception as exc:
            errors.append(f"rmtree failed: {_sanitize_text(str(exc))}")

        if self.root.exists() or errors:
            self._cleaned = False
            msg = "; ".join(errors) if errors else "root directory still exists after cleanup"
            err = TrustedLayoutError(f"layout cleanup failed: {msg}")
            raise _sanitize_exception_chain(err)

        self._cleaned = True


def _is_reparse_point(path: Path) -> bool:
    """Check if *path* is a symbolic link, junction, or reparse point."""
    try:
        st = os.lstat(path)
        if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
        return os.path.islink(path)
    except OSError as exc:
        raise TrustedLayoutSecurityError(
            f"cannot determine reparse point status for {path}: {_sanitize_text(str(exc))}"
        ) from None


# Win32 DACL and Label Helpers
def _set_traverse_dacl(path: Path, user_sid: str, container_sid: str) -> None:
    _set_dacl_entries(path, [
        (user_sid, _FILE_ALL_ACCESS, 0),
        (container_sid, _FILE_GENERIC_EXECUTE, 0),
    ])


def _set_container_full_dacl(path: Path, user_sid: str, container_sid: str) -> None:
    _set_dacl_entries(path, [
        (user_sid, _FILE_ALL_ACCESS, _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE),
        (container_sid, _FILE_ALL_ACCESS, _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE),
    ])


def _set_user_only_dacl(path: Path, user_sid: str) -> None:
    _set_dacl_entries(path, [
        (user_sid, _FILE_ALL_ACCESS, _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE),
    ])


def _set_dacl_entries(path: Path, entries: Sequence[tuple[str, int, int]]) -> None:
    advapi = _advapi32()
    k32 = _kernel32()
    allocated_sids: list[ctypes.c_void_p] = []
    try:
        size = 8
        parsed_entries: list[tuple[ctypes.c_void_p, int, int]] = []
        for sid_str, mask, flags in entries:
            sid_ptr = ctypes.c_void_p()
            if not advapi.ConvertStringSidToSidW(sid_str, ctypes.byref(sid_ptr)):
                raise TrustedLayoutBlockedError(
                    f"ConvertStringSidToSidW failed for {sid_str!r}: {ctypes.FormatError(ctypes.get_last_error())}"
                )
            allocated_sids.append(sid_ptr)
            parsed_entries.append((sid_ptr, mask, flags))
            size += 8 + int(advapi.GetLengthSid(sid_ptr))

        buffer = ctypes.create_string_buffer(max(size, 256))
        if not advapi.InitializeAcl(buffer, ctypes.sizeof(buffer), _ACL_REVISION):
            raise TrustedLayoutBlockedError(
                f"InitializeAcl failed: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        for sid_ptr, mask, flags in parsed_entries:
            if not advapi.AddAccessAllowedAceEx(buffer, _ACL_REVISION, flags, mask, sid_ptr):
                raise TrustedLayoutBlockedError(
                    f"AddAccessAllowedAceEx failed: {ctypes.FormatError(ctypes.get_last_error())}"
                )
        res = advapi.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, ctypes.cast(buffer, ctypes.c_void_p), None,
        )
        if res != 0:
            raise TrustedLayoutBlockedError(
                f"SetNamedSecurityInfoW(DACL) failed with code {res}: {ctypes.FormatError(res)}"
            )
    finally:
        for ptr in allocated_sids:
            k32.LocalFree(ptr)


def _reset_dacl_for_deletion(path: Path) -> None:
    advapi = _advapi32()
    res = advapi.SetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION | _UNPROTECTED_DACL_SECURITY_INFORMATION,
        None, None, None, None,
    )
    if res != 0:
        raise TrustedLayoutError(
            f"SetNamedSecurityInfoW(reset DACL) failed for {path} with code {res}: native error {res}"
        )


def _set_mandatory_label(path: Path, label_sid: str) -> None:
    advapi = _advapi32()
    k32 = _kernel32()
    sid_ptr = ctypes.c_void_p()
    if not advapi.ConvertStringSidToSidW(label_sid, ctypes.byref(sid_ptr)):
        raise TrustedLayoutBlockedError(
            f"ConvertStringSidToSidW failed for label {label_sid!r}: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    try:
        buffer = ctypes.create_string_buffer(256)
        if not advapi.InitializeAcl(buffer, ctypes.sizeof(buffer), _ACL_REVISION):
            raise TrustedLayoutBlockedError(
                f"InitializeAcl(SACL) failed: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        if not advapi.AddMandatoryAce(
            buffer, _ACL_REVISION,
            _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE,
            _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP,
            sid_ptr,
        ):
            raise TrustedLayoutBlockedError(
                f"AddMandatoryAce failed: {ctypes.FormatError(ctypes.get_last_error())}"
            )
        res = advapi.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _LABEL_SECURITY_INFORMATION,
            None, None, None, ctypes.cast(buffer, ctypes.c_void_p),
        )
        if res != 0:
            raise TrustedLayoutBlockedError(
                f"SetNamedSecurityInfoW(LABEL) failed with code {res}: {ctypes.FormatError(res)}"
            )
    finally:
        k32.LocalFree(sid_ptr)


def _query_dacl(path: Path, user_sid: str, container_sid: str) -> dict[str, Any]:
    advapi = _advapi32()
    k32 = _kernel32()
    needed = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    advapi.GetFileSecurityW(str(path), _DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(needed))
    err = ctypes.get_last_error()
    if err != _ERROR_INSUFFICIENT_BUFFER and err != 0:
        raise TrustedLayoutBlockedError(f"GetFileSecurityW(DACL size) failed: {ctypes.FormatError(err)}")
    buffer = ctypes.create_string_buffer(needed.value or 1)
    if not advapi.GetFileSecurityW(str(path), _DACL_SECURITY_INFORMATION, buffer, needed, ctypes.byref(needed)):
        raise TrustedLayoutBlockedError(
            f"GetFileSecurityW(DACL) failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    if not advapi.GetSecurityDescriptorDacl(
        buffer, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
    ):
        raise TrustedLayoutBlockedError(
            f"GetSecurityDescriptorDacl failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    if not advapi.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision)):
        raise TrustedLayoutBlockedError(
            f"GetSecurityDescriptorControl failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    is_protected = bool(control.value & _SE_DACL_PROTECTED)
    if not present.value or not dacl.value:
        raise TrustedLayoutBlockedError(f"null or missing DACL on {path}")

    container_ace = False
    user_ace = False
    extra_aces: list[str] = []
    aces: list[dict[str, Any]] = []

    info = _AclSizeInformation()
    if not advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), _ACL_SIZE_INFORMATION_CLASS):
        raise TrustedLayoutBlockedError("GetAclInformation(DACL) failed")
    for i in range(int(info.AceCount)):
        ace_ptr = ctypes.c_void_p()
        if not advapi.GetAce(dacl, i, ctypes.byref(ace_ptr)):
            raise TrustedLayoutBlockedError(f"GetAce(DACL, {i}) failed")
        header = ctypes.cast(ace_ptr, ctypes.POINTER(_AceHeader)).contents
        if header.AceType not in (_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE):
            raise TrustedLayoutBlockedError(f"unsupported ACE type: {header.AceType}")
        allowed = ctypes.cast(ace_ptr, ctypes.POINTER(_AccessAllowedAce)).contents
        sid_start = ctypes.addressof(allowed) + _AccessAllowedAce.SidStart.offset
        sid_str = _sid_to_string(advapi, k32, ctypes.c_void_p(sid_start))
        ace_record = {
            "type": int(header.AceType),
            "flags": int(header.AceFlags),
            "mask": int(allowed.Mask),
            "sid": sid_str,
        }
        aces.append(ace_record)
        if header.AceType == _ACCESS_ALLOWED_ACE_TYPE:
            if sid_str == container_sid:
                container_ace = True
            elif sid_str == user_sid:
                user_ace = True
            else:
                extra_aces.append(sid_str)
        else:
            extra_aces.append(f"DENY:{sid_str}")
    return {
        "protected": is_protected,
        "present": bool(present.value),
        "aces": aces,
        "container_ace": container_ace,
        "user_ace": user_ace,
        "extra_aces": extra_aces,
    }


def _query_mandatory_label(path: Path) -> str | None:
    advapi = _advapi32()
    k32 = _kernel32()
    needed = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    advapi.GetFileSecurityW(str(path), _LABEL_SECURITY_INFORMATION, None, 0, ctypes.byref(needed))
    err = ctypes.get_last_error()
    if err != _ERROR_INSUFFICIENT_BUFFER and err != 0:
        raise TrustedLayoutBlockedError(f"GetFileSecurityW(LABEL size) failed: {ctypes.FormatError(err)}")
    buffer = ctypes.create_string_buffer(needed.value or 1)
    if not advapi.GetFileSecurityW(str(path), _LABEL_SECURITY_INFORMATION, buffer, needed, ctypes.byref(needed)):
        raise TrustedLayoutBlockedError(
            f"GetFileSecurityW(LABEL) failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    sacl = ctypes.c_void_p()
    if not advapi.GetSecurityDescriptorSacl(
        buffer, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)
    ):
        raise TrustedLayoutBlockedError(
            f"GetSecurityDescriptorSacl failed: {ctypes.FormatError(ctypes.get_last_error())}"
        )
    if not present.value or not sacl.value:
        return None
    info = _AclSizeInformation()
    if not advapi.GetAclInformation(sacl, ctypes.byref(info), ctypes.sizeof(info), _ACL_SIZE_INFORMATION_CLASS):
        raise TrustedLayoutBlockedError("GetAclInformation(SACL) failed")
    for i in range(int(info.AceCount)):
        ace_ptr = ctypes.c_void_p()
        if advapi.GetAce(sacl, i, ctypes.byref(ace_ptr)):
            header = ctypes.cast(ace_ptr, ctypes.POINTER(_AceHeader)).contents
            if header.AceType == _SYSTEM_MANDATORY_LABEL_ACE_TYPE:
                allowed = ctypes.cast(ace_ptr, ctypes.POINTER(_AccessAllowedAce)).contents
                sid_start = ctypes.addressof(allowed) + _AccessAllowedAce.SidStart.offset
                return _sid_to_string(advapi, k32, ctypes.c_void_p(sid_start))
    return None


def _get_final_path_by_handle(k32: ctypes.WinDLL, handle: wintypes.HANDLE) -> str:
    """Retrieve the normalized final path for an open file/directory handle."""
    req_len = k32.GetFinalPathNameByHandleW(handle, None, 0, _FILE_NAME_NORMALIZED | _VOLUME_NAME_DOS)
    if req_len == 0:
        err = ctypes.get_last_error()
        raise TrustedLayoutError(f"GetFinalPathNameByHandleW length query failed: {ctypes.FormatError(err)}")
    buf = ctypes.create_unicode_buffer(req_len + 1)
    ret_len = k32.GetFinalPathNameByHandleW(handle, buf, req_len + 1, _FILE_NAME_NORMALIZED | _VOLUME_NAME_DOS)
    if ret_len == 0:
        err = ctypes.get_last_error()
        raise TrustedLayoutError(f"GetFinalPathNameByHandleW failed: {ctypes.FormatError(err)}")
    path_val = buf.value
    if path_val.startswith("\\\\?\\"):
        path_val = path_val[4:]
    return os.path.normcase(os.path.normpath(path_val))


def read_trusted_artifact(
    checkout_dir: Path,
    rel_path: str,
    *,
    job_process: Any,
    manifest: TrustedManifest,
    candidate_relpath: str | None = None,
    actual_argv: Sequence[str] | None = None,
    actual_timeout: float | int | None = None,
    actual_tree_oid: str | None = None,
    actual_spec_hash: str | None = None,
) -> dict[str, Any]:
    """Securely read and validate a JSON artifact produced by an AppContainer worker.

    Pre-requisites:
        *job_process* must be a verified WindowsJobProcess with ``tree_stopped == True``
        and ``active_processes == 0``. If any processes are still running or containment
        state is unknown, this function refuses to open the artifact.

    File Inspection:
        The artifact is opened with ``FILE_FLAG_OPEN_REPARSE_POINT``, verifying that
        it is a disk file, not a reparse point (symlink/junction), has no external
        hardlinks (``nNumberOfLinks <= 1``), and its final path resolves strictly
        inside *checkout_dir* without alternate data streams (ADS).

    Size and Content Limits:
        Reads at most 64 KiB (65536 bytes). If the file exceeds this limit, it is
        rejected. Parses strict UTF-8 JSON and validates that `run_id`, `task_id`,
        and `nonce` match *manifest*.

    Args:
        checkout_dir: The verified checkout directory.
        rel_path: Relative path matching *manifest.expected_artifact_relpath*.
        job_process: WindowsJobProcess instance proving containment stopped.
        manifest: Expected TrustedManifest.
        candidate_relpath: Optional relative path to candidate script on disk.
        actual_argv: Optional invocation arguments to verify against manifest.
        actual_timeout: Optional actual execution duration to verify against manifest.
        actual_tree_oid: Optional tree OID to verify against manifest.
        actual_spec_hash: Optional spec hash to verify against manifest.

    Returns:
        The validated dictionary parsed from the artifact.

    Raises:
        TrustedLayoutSecurityError: containment violation, reparse point, path traversal,
            size overflow, or manifest binding mismatch.
        TrustedLayoutError: I/O or decoding failure.
    """
    # 1. Job Process Proof
    if job_process is None or isinstance(job_process, bool):
        raise TrustedLayoutSecurityError("job_process must be a WindowsJobProcess, not a boolean")

    from omp_core.windows_job import WindowsJobProcess
    if not isinstance(job_process, WindowsJobProcess):
        raise TrustedLayoutSecurityError(
            f"job_process must be an instance of WindowsJobProcess, got {type(job_process).__name__}"
        )

    if not getattr(job_process, "tree_stopped", False):
        raise TrustedLayoutSecurityError("refusing to read artifact: process tree has not stopped")
    try:
        active = getattr(job_process, "active_processes", -1)
    except Exception as exc:
        raise TrustedLayoutSecurityError(
            f"cannot verify active processes in job: {exc}"
        ) from exc
    if active is False or not isinstance(active, int):
        raise TrustedLayoutSecurityError("active_processes must be an integer, not a boolean")
    if active != 0:
        raise TrustedLayoutSecurityError(
            f"refusing to read artifact: job still contains {active} active process(es)"
        )

    # 2. Relative Path Validation
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("rel_path must be a non-empty string")
    if rel_path != manifest.expected_artifact_relpath:
        raise TrustedLayoutSecurityError(
            f"rel_path {rel_path!r} does not match expected_artifact_relpath {manifest.expected_artifact_relpath!r}"
        )
    if (
        rel_path.startswith("/")
        or "\\" in rel_path
        or "\0" in rel_path
        or ":" in rel_path
        or any(part in ("..", ".", "") for part in rel_path.split("/"))
    ):
        raise TrustedLayoutSecurityError(f"invalid artifact relative path: {rel_path!r}")

    # 3. Candidate script verification on disk
    approved_candidate: str | None = None
    search_args = (
        manifest.approved_argv[1:]
        if len(manifest.approved_argv) > 1
        else manifest.approved_argv
    )
    for arg in search_args:
        if arg.startswith("-"):
            continue
        approved_candidate = arg
        break

    if approved_candidate is None:
        raise TrustedLayoutSecurityError("cannot identify candidate script in approved manifest")

    if candidate_relpath is not None:
        if Path(candidate_relpath).as_posix() != Path(approved_candidate).as_posix():
            raise TrustedLayoutSecurityError(
                f"candidate_relpath {candidate_relpath!r} does not match approved script {approved_candidate!r}"
            )

    candidate_name = approved_candidate
    if (
        Path(candidate_name).is_absolute()
        or ":" in candidate_name
        or candidate_name.startswith(("/", "\\"))
        or any(part in ("..", ".", "") for part in Path(candidate_name).parts)
    ):
        raise TrustedLayoutSecurityError(f"invalid candidate path: {candidate_name!r}")

    candidate_to_check = checkout_dir / candidate_name
    if not candidate_to_check.exists() or not candidate_to_check.is_file():
        raise TrustedLayoutSecurityError(f"approved candidate file missing: {candidate_to_check}")
    try:
        cand_bytes = candidate_to_check.read_bytes()
    except OSError as exc:
        raise TrustedLayoutError(f"cannot read candidate file {candidate_to_check}: {exc}") from exc
    cand_hash = hashlib.sha256(cand_bytes).hexdigest()
    if cand_hash != manifest.candidate_bytes_sha256:
        raise TrustedLayoutSecurityError(
            "candidate bytes SHA-256 does not match approved manifest"
        )

    # 4. Mandatory execution bindings: argv, timeout, tree OID, spec hash
    if actual_argv is None:
        raise TrustedLayoutSecurityError("missing required execution binding: actual_argv")
    if tuple(actual_argv) != tuple(manifest.approved_argv):
        raise TrustedLayoutSecurityError("actual invocation argv does not match approved manifest")

    if actual_timeout is None:
        raise TrustedLayoutSecurityError("missing required execution binding: actual_timeout")
    if isinstance(actual_timeout, bool) or not isinstance(actual_timeout, (int, float)):
        raise TrustedLayoutSecurityError(
            f"actual_timeout must be a finite positive number, got {type(actual_timeout).__name__}"
        )
    if not math.isfinite(actual_timeout) or actual_timeout <= 0:
        raise TrustedLayoutSecurityError(f"actual_timeout must be finite and positive, got {actual_timeout!r}")
    if actual_timeout != manifest.timeout_seconds:
        raise TrustedLayoutSecurityError(
            f"actual timeout ({actual_timeout}) does not match approved manifest timeout ({manifest.timeout_seconds})"
        )

    if actual_tree_oid is None:
        raise TrustedLayoutSecurityError("missing required execution binding: actual_tree_oid")
    if not isinstance(actual_tree_oid, str) or actual_tree_oid != manifest.canonical_tree_oid:
        raise TrustedLayoutSecurityError("actual tree OID does not match approved manifest")

    if actual_spec_hash is None:
        raise TrustedLayoutSecurityError("missing required execution binding: actual_spec_hash")
    if not isinstance(actual_spec_hash, str) or actual_spec_hash != manifest.spec_hash:
        raise TrustedLayoutSecurityError("actual spec hash does not match approved manifest")

    target_path = checkout_dir / rel_path
    if not target_path.exists():
        raise TrustedLayoutError(f"artifact not found: {rel_path}")

    # 6. Handle-based open and inspection on Windows
    if os.name == "nt":
        raw_bytes = _read_file_handle_windows(target_path, checkout_dir)
    else:
        # Fallback for non-Windows mock test environments
        if os.path.islink(target_path):
            raise TrustedLayoutSecurityError("reparse point or symlink detected")
        raw_bytes = target_path.read_bytes()
        if len(raw_bytes) > MAX_ARTIFACT_SIZE_BYTES:
            raise TrustedLayoutSecurityError(
                f"artifact size ({len(raw_bytes)} bytes) exceeds limit of {MAX_ARTIFACT_SIZE_BYTES} bytes"
            )

    # 7. Strict UTF-8 Decoding
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TrustedLayoutError(f"artifact is not valid UTF-8: {exc}") from exc

    # 8. Strict JSON Parsing
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TrustedLayoutError(f"artifact contains malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustedLayoutSecurityError("artifact JSON must be a top-level dictionary")

    # 9. Strict Artifact Schema Validation
    allowed_artifact_keys = {"task_id", "run_id", "nonce", "status", "output", "tests_run", "failures", "errors", "details"}
    extra_keys = set(data.keys()) - allowed_artifact_keys
    if extra_keys:
        raise TrustedLayoutSecurityError(f"artifact contains unapproved keys: {sorted(extra_keys)}")

    status_val = data.get("status")
    if status_val not in ("PASS", "FAIL", "success"):
        raise TrustedLayoutSecurityError(f"artifact status must be 'PASS', 'FAIL', or 'success', got {status_val!r}")

    if "tests_run" in data:
        tr = data["tests_run"]
        if isinstance(tr, bool) or not isinstance(tr, int) or tr < 0:
            raise TrustedLayoutSecurityError(f"artifact tests_run must be non-negative int, got {tr!r}")

    if "failures" in data:
        f = data["failures"]
        if isinstance(f, bool) or not isinstance(f, int) or f < 0:
            raise TrustedLayoutSecurityError(f"artifact failures must be non-negative int, got {f!r}")

    if "errors" in data:
        e = data["errors"]
        if isinstance(e, bool) or not isinstance(e, int) or e < 0:
            raise TrustedLayoutSecurityError(f"artifact errors must be non-negative int, got {e!r}")

    if "output" in data and not isinstance(data["output"], str):
        raise TrustedLayoutSecurityError(f"artifact output must be a string, got {type(data['output']).__name__}")

    if "details" in data and not isinstance(data["details"], (str, dict, list)):
        raise TrustedLayoutSecurityError(f"artifact details must be str, dict, or list, got {type(data['details']).__name__}")

    if status_val in ("PASS", "success"):
        if data.get("failures", 0) > 0 or data.get("errors", 0) > 0:
            raise TrustedLayoutSecurityError(f"artifact status is {status_val} but has failures or errors")

    # 10. Manifest Binding
    if data.get("task_id") != manifest.task_id:
        raise TrustedLayoutSecurityError("artifact task_id does not match manifest")
    if data.get("run_id") != manifest.run_id:
        raise TrustedLayoutSecurityError("artifact run_id does not match manifest")
    if data.get("nonce") != manifest.nonce:
        raise TrustedLayoutSecurityError("artifact nonce does not match manifest")

    return data


def _read_file_handle_windows(target_path: Path, checkout_dir: Path) -> bytes:
    """Read file content via Win32 handle without following reparse points."""
    try:
        return _read_file_handle_windows_impl(target_path, checkout_dir)
    except BaseException as exc:
        _sanitize_exception_chain(exc)
        raise


def _read_file_handle_windows_impl(target_path: Path, checkout_dir: Path) -> bytes:
    k32 = _kernel32()

    # 1. Anchor checkout directory identity with owned directory handle
    if not checkout_dir.is_absolute():
        raise TrustedLayoutSecurityError(f"checkout_dir must be an absolute path: {checkout_dir}")
    if _is_reparse_point(checkout_dir):
        raise TrustedLayoutSecurityError(f"checkout directory is a reparse point: {checkout_dir}")

    # Check ancestors of checkout_dir
    curr = checkout_dir.parent
    while curr != curr.parent:
        if _is_reparse_point(curr):
            raise TrustedLayoutSecurityError(f"checkout ancestor directory {curr} is a reparse point")
        curr = curr.parent

    dir_handle = k32.CreateFileW(
        str(checkout_dir),
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if dir_handle == _INVALID_HANDLE_VALUE or not dir_handle:
        err = ctypes.get_last_error()
        raise TrustedLayoutError(f"CreateFileW on checkout_dir failed: native error {err}")

    primary_exc: BaseException | None = None
    close_errors: list[Exception] = []
    handle = _INVALID_HANDLE_VALUE

    try:
        dir_info = _BY_HANDLE_FILE_INFORMATION()
        if not k32.GetFileInformationByHandle(dir_handle, ctypes.byref(dir_info)):
            raise TrustedLayoutError(f"GetFileInformationByHandle on checkout_dir failed: native error {ctypes.get_last_error()}")
        if dir_info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise TrustedLayoutSecurityError("checkout directory is a reparse point")

        checkout_final = _get_final_path_by_handle(k32, dir_handle)

        # 2. Open artifact file handle with FILE_FLAG_OPEN_REPARSE_POINT while dir_handle remains open
        handle = k32.CreateFileW(
            str(target_path),
            _GENERIC_READ,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE or not handle:
            err = ctypes.get_last_error()
            raise TrustedLayoutError(f"CreateFileW failed for {target_path}: native error {err}")

        try:
            # Check file type
            file_type = k32.GetFileType(handle)
            if file_type != _FILE_TYPE_DISK:
                raise TrustedLayoutSecurityError(
                    f"refusing to read non-disk file type: {file_type}"
                )

            # Check attributes and hardlinks
            info = _BY_HANDLE_FILE_INFORMATION()
            if not k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise TrustedLayoutError(
                    f"GetFileInformationByHandle failed: native error {ctypes.get_last_error()}"
                )

            if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise TrustedLayoutSecurityError("reparse point or symlink detected")
            if info.nNumberOfLinks > 1:
                raise TrustedLayoutSecurityError(
                    f"multiple hard links detected ({info.nNumberOfLinks}), refusing"
                )

            # Check final path
            artifact_final = _get_final_path_by_handle(k32, handle)

            # Reject alternate data streams
            if ":" in Path(artifact_final).name:
                raise TrustedLayoutSecurityError("alternate data stream detected in artifact path")

            # Verify final path is inside checkout
            prefix = checkout_final if checkout_final.endswith("\\") else checkout_final + "\\"
            if not artifact_final.startswith(prefix):
                raise TrustedLayoutSecurityError(
                    f"final path {artifact_final} escapes checkout directory {checkout_final}"
                )

            # Verify volume serial number matches checkout directory
            if dir_info.dwVolumeSerialNumber != info.dwVolumeSerialNumber:
                raise TrustedLayoutSecurityError("artifact volume serial number does not match checkout directory")

            # Verify checkout directory handle identity has not moved/changed
            curr_dir_final = _get_final_path_by_handle(k32, dir_handle)
            if curr_dir_final != checkout_final:
                raise TrustedLayoutSecurityError("checkout directory identity changed during read operation")

            # Read file with limit + 1
            buffer_size = MAX_ARTIFACT_SIZE_BYTES + 1
            read_buffer = ctypes.create_string_buffer(buffer_size)
            bytes_read = wintypes.DWORD(0)
            if not k32.ReadFile(handle, read_buffer, buffer_size, ctypes.byref(bytes_read), None):
                raise TrustedLayoutError(
                    f"ReadFile failed: native error {ctypes.get_last_error()}"
                )

            if bytes_read.value > MAX_ARTIFACT_SIZE_BYTES:
                raise TrustedLayoutSecurityError(
                    f"artifact size ({bytes_read.value} bytes) exceeds limit of {MAX_ARTIFACT_SIZE_BYTES} bytes"
                )

            # Re-check file information to verify identity didn't swap
            info_after = _BY_HANDLE_FILE_INFORMATION()
            if not k32.GetFileInformationByHandle(handle, ctypes.byref(info_after)):
                raise TrustedLayoutError(f"GetFileInformationByHandle re-check failed: native error {ctypes.get_last_error()}")
            if (
                info.dwVolumeSerialNumber != info_after.dwVolumeSerialNumber
                or info.nFileIndexHigh != info_after.nFileIndexHigh
                or info.nFileIndexLow != info_after.nFileIndexLow
            ):
                raise TrustedLayoutSecurityError("file identity changed during read operation")

            return read_buffer.raw[:bytes_read.value]
        except BaseException as exc:
            primary_exc = exc
            raise
        finally:
            if handle not in (_INVALID_HANDLE_VALUE, None):
                if not k32.CloseHandle(handle):
                    err = ctypes.get_last_error()
                    close_errors.append(
                        TrustedLayoutError(f"CloseHandle failed for artifact file: native error {err}")
                    )
    except BaseException as exc:
        if primary_exc is None:
            primary_exc = exc
        raise
    finally:
        if dir_handle not in (_INVALID_HANDLE_VALUE, None):
            if not k32.CloseHandle(dir_handle):
                err = ctypes.get_last_error()
                close_errors.append(
                    TrustedLayoutError(f"CloseHandle failed for checkout_dir: native error {err}")
                )

        if primary_exc is not None:
            _sanitize_exception_chain(primary_exc)
            if close_errors:
                for ce in close_errors:
                    _sanitize_exception_chain(ce)
                curr = primary_exc
                for ce in close_errors:
                    ce.__context__ = curr.__context__
                    curr.__context__ = ce
            raise primary_exc
        elif close_errors:
            for ce in close_errors:
                _sanitize_exception_chain(ce)
            first = close_errors[0]
            if len(close_errors) > 1:
                first.__context__ = close_errors[1]
            raise first
