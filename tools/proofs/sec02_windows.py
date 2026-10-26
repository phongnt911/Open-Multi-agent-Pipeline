"""SEC-02 Windows Trusted Verification Proof.

This tool demonstrates Phase A of SEC-02:
1. Native CPython (3.12 and 3.14) runs under an AppContainer (lowbox) boundary.
2. The Worker has read/execute on its copied runtime and read/write in its workspace.
3. The Worker is denied read and write to sensitive assets (vault, manifest, runner_config, canaries).
4. The Trusted Oracle runs in a separate trusted process, does not import untrusted candidate code,
   and independently validates artifacts against trusted identity rather than trusting Worker claims.
5. All process descendants within the Job Object must terminate before the Oracle inspects artifacts;
   active descendants result in an immediate fail-closed rejection.
6. Seven specific adversary attacks (tamper expected, import injection, config tamper, fake PASS stdout,
   early exit 0, replay, and monkeypatching) are natively executed and detected via the common Oracle.
   Attacks that fail to execute (timeout, launch error) are never credited as detections.
7. Owned resources (profile, SID, Job Object, temporary directory, attribute lists) are strictly guarded
   from first allocation and boundedly cleaned up across all failure paths.
"""
from __future__ import annotations

import argparse
class _MockWinDLL:
    def __getattr__(self, name: str) -> Any:
        def _dummy(*args: Any, **kwargs: Any) -> Any:
            return 0
        return _dummy


try:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    userenv = ctypes.windll.userenv
except Exception:
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
    kernel32 = _MockWinDLL()  # type: ignore[assignment]
    advapi32 = _MockWinDLL()  # type: ignore[assignment]
    userenv = _MockWinDLL()  # type: ignore[assignment]

import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

try:
    from tools.proofs import sec01_windows as sec01
except Exception:
    class _MockSec01:
        userenv = userenv
        advapi32 = advapi32
        kernel32 = kernel32
        class _Ole32:
            CoTaskMemFree = lambda *args: None
        ole32 = _Ole32()
        @staticmethod
        def _prototypes() -> None:
            pass
        @staticmethod
        def _sid_string(sid: Any) -> str:
            return "S-1-15-2-mock"
        @staticmethod
        def _create_job() -> int:
            return 12345
        @staticmethod
        def _job_active_processes(job: int) -> int:
            return 0
        @staticmethod
        def _attribute_list(sid: Any) -> tuple[Any, Any]:
            return (None, None)
        @staticmethod
        def _environment_values(**kwargs: Any) -> dict[str, str]:
            return {}
        @staticmethod
        def _launch(**kwargs: Any) -> dict[str, Any]:
            return {"exit_code": 0, "timed_out": False, "command": kwargs.get("command_line", "")}
    sec01 = _MockSec01()  # type: ignore[assignment]

SCHEMA = "omp.sec02.windows-trusted-verification-proof.v1"
PROFILE_PREFIX = "OMP.SEC02.Proof."
CHECK_NAMES = ("vault", "manifest", "runner_config", "canaries")
REQUIRED_ATTACKS = (
    "tamper_expected",
    "import_injection",
    "tamper_runner_config",
    "fake_pass_stdout",
    "early_exit_zero",
    "replay_old_results",
    "worker_monkeypatching",
)
SHA256_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")
APPROVED_MANIFEST_FIELDS = (
    "task_id",
    "run_id",
    "nonce",
    "tree_id",
    "candidate_hash",
    "approved_argv_pattern",
    "approved_timeout_seconds",
    "spec",
)

LEGIT_CANDIDATE_CODE = """import json, sys
with open("task_input.json", "r", encoding="utf-8") as f:
    inp = json.load(f)
res = inp["a"] + inp["b"]
output = {
    "task_id": inp["task_id"],
    "run_id": inp["run_id"],
    "nonce": inp["nonce"],
    "tree_id": inp["tree_id"],
    "result": res,
}
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump(output, f)
print("WORKER_COMPLETED_SUCCESSFULLY")
"""
LEGIT_CANDIDATE_HASH = hashlib.sha256(LEGIT_CANDIDATE_CODE.encode("utf-8")).hexdigest()


def make_canonical_tree_id(task_id: str, candidate_hash: str) -> str:
    """Derive canonical tree identity from task_id and candidate code hash."""
    return f"TREE-{hashlib.sha256((task_id + ':' + candidate_hash).encode('utf-8')).hexdigest()[:16]}"


_last_wait_timeout_ms: list[int] = []
if sec01 is not None and hasattr(sec01, "kernel32") and hasattr(sec01.kernel32, "WaitForSingleObject"):
    _orig_wait = sec01.kernel32.WaitForSingleObject

    def _tracked_wait(h: Any, ms: int) -> int:
        _last_wait_timeout_ms.append(int(ms))
        return _orig_wait(h, ms)

    sec01.kernel32.WaitForSingleObject = _tracked_wait


class Sec02ProofError(RuntimeError):
    """An error occurred during SEC-02 native proof execution."""


def _ensure_prototypes() -> None:
    """Ensure all required Win32 prototypes are initialized."""
    if sec01 is not None and hasattr(sec01, "_prototypes"):
        sec01._prototypes()
    if ctypes is not None and wintypes is not None:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.DeleteProcThreadAttributeList.restype = None
        advapi32.FreeSid.argtypes = [ctypes.c_void_p]
        advapi32.FreeSid.restype = ctypes.c_void_p


def _file_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of path contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Return value when it is a JSON object-like mapping, else None."""
    return value if isinstance(value, Mapping) else None


def _is_sha256(value: Any) -> bool:
    """Return True for a 64-character hexadecimal SHA-256 digest string."""
    return isinstance(value, str) and bool(SHA256_REGEX.match(value))


def _is_number(value: Any) -> bool:
    """Return True for finite int/float values, excluding bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_zero_exit(value: Any) -> bool:
    """Return True only for a strict integer exit code of zero."""
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _is_argv_basename(token: Any) -> bool:
    """Return True for a non-empty bare filename token without a directory."""
    return isinstance(token, str) and bool(token) and ntpath.basename(token) == token


def _valid_argv_pattern(pattern: Any) -> bool:
    """Validate the approved four-token isolated Python argv pattern."""
    return (
        isinstance(pattern, list)
        and len(pattern) == 4
        and all(isinstance(token, str) and token for token in pattern)
        and _is_argv_basename(pattern[0])
        and pattern[1:3] == ["-I", "-P"]
        and _is_argv_basename(pattern[3])
    )


def _approved_argv_matches(pattern: Any, argv: Any) -> bool:
    """Return True when argv is exactly the approved isolated invocation shape."""
    if not _valid_argv_pattern(pattern):
        return False
    if not (
        isinstance(argv, list)
        and len(argv) == 4
        and all(isinstance(token, str) and token for token in argv)
    ):
        return False
    return (
        argv[1:3] == ["-I", "-P"]
        and ntpath.basename(argv[0]) == pattern[0]
        and ntpath.basename(argv[3]) == pattern[3]
    )


def _validate_manifest(
    manifest: Any, expected_target: str | None = None
) -> tuple[dict[str, Any] | None, str]:
    """Validate approved manifest fields and return a normalized copy."""
    if not isinstance(manifest, Mapping):
        return None, "Manifest must be a JSON object"
    for key in APPROVED_MANIFEST_FIELDS:
        if key not in manifest:
            return None, f"Manifest missing approved field '{key}'"
    for key in ("task_id", "run_id", "nonce"):
        value = manifest.get(key)
        if not (isinstance(value, str) and value):
            return None, f"Manifest {key} must be a non-empty string"
    candidate_hash = manifest.get("candidate_hash")
    if not (isinstance(candidate_hash, str) and SHA256_REGEX.match(candidate_hash)):
        return None, "Manifest candidate_hash must be a SHA-256 digest"
    tree_id = manifest.get("tree_id")
    canonical_tree = make_canonical_tree_id(manifest["task_id"], candidate_hash)
    if tree_id != canonical_tree:
        return (
            None,
            f"Manifest tree_id '{tree_id}' does not match canonical tree identity '{canonical_tree}'",
        )
    if not _valid_argv_pattern(manifest.get("approved_argv_pattern")):
        return None, "Manifest approved_argv_pattern must be ['python.exe', '-I', '-P', '<script>.py']"
    timeout = manifest.get("approved_timeout_seconds")
    if not _is_number(timeout) or timeout <= 0:
        return None, "Manifest approved_timeout_seconds must be a positive number"
    spec = _as_mapping(manifest.get("spec"))
    if spec is None:
        return None, "Manifest spec must be a JSON object"
    for key in ("a", "b", "expected"):
        if not _is_number(spec.get(key)):
            return None, f"Manifest spec.{key} must be a number"
    if expected_target is not None and manifest.get("target") != expected_target:
        return (
            None,
            f"Manifest target mismatch: expected '{expected_target}', got '{manifest.get('target')}'",
        )
    return dict(manifest), ""


def _load_approved_manifest(
    path: Path, *, expected_target: str | None = None
) -> tuple[dict[str, Any] | None, str]:
    """Read and validate the approved manifest from the trusted vault."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"Malformed manifest JSON: {type(exc).__name__}"
    return _validate_manifest(raw, expected_target=expected_target)


def _manifest_binding_error(
    manifest: Mapping[str, Any], identity: Mapping[str, Any]
) -> str:
    """Return a mismatch description when manifest fields do not bind identity."""
    for key in ("task_id", "run_id", "nonce", "tree_id", "candidate_hash"):
        if manifest.get(key) != identity.get(key):
            return (
                f"Manifest {key} mismatch: expected '{identity.get(key)}', "
                f"got '{manifest.get(key)}'"
            )
    pattern = manifest.get("approved_argv_pattern")
    approved_argv = identity.get("approved_argv")
    if not _approved_argv_matches(pattern, approved_argv):
        return (
            f"Manifest approved_argv_pattern mismatch: pattern {pattern} "
            f"does not bind approved argv {approved_argv}"
        )
    manifest_timeout = manifest.get("approved_timeout_seconds")
    identity_timeout = identity.get("approved_timeout_seconds")
    if not _is_number(manifest_timeout) or manifest_timeout != identity_timeout:
        return (
            f"Manifest approved_timeout_seconds mismatch: "
            f"expected {identity_timeout}, got {manifest_timeout}"
        )
    spec = _as_mapping(manifest.get("spec"))
    if spec is None:
        return "Manifest spec must be a JSON object"
    for key in ("a", "b", "expected"):
        if not _is_number(spec.get(key)):
            return f"Manifest spec.{key} must be a number"
    if spec.get("expected") != identity.get("expected_result"):
        return (
            f"Manifest spec expected mismatch: expected {identity.get('expected_result')}, "
            f"got {spec.get('expected')}"
        )
    identity_spec = identity.get("spec")
    if identity_spec is not None and spec != identity_spec:
        return "Manifest spec mismatch with trusted identity spec"
    return ""


def find_python_runtimes() -> dict[str, Path]:
    """Discover available Python 3.12 and 3.14 executables."""
    runtimes: dict[str, Path] = {}

    cur_exe = Path(sys.executable).resolve()
    if sys.version_info[:2] == (3, 12):
        runtimes["3.12"] = cur_exe
    else:
        p312_candidates = [
            Path(".omp/platform-venv312/Scripts/python.exe").resolve(),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe",
        ]
        which_312 = shutil.which("python3.12")
        if which_312:
            p312_candidates.append(Path(which_312).resolve())
        for cand in p312_candidates:
            if cand.is_file():
                try:
                    res = subprocess.run([str(cand), "--version"], capture_output=True, text=True, timeout=5)
                    if res.returncode == 0 and "3.12" in res.stdout:
                        runtimes["3.12"] = cand
                        break
                except Exception:
                    pass

    if sys.version_info[:2] == (3, 14):
        runtimes["3.14"] = cur_exe
    else:
        p314_candidates = [
            Path(os.environ.get("APPDATA", "")) / "uv" / "python" / "cpython-3.14.8-windows-x86_64-none" / "python.exe",
            Path(".omp/python-runtimes/cpython-3.14.8-windows-x86_64-none/python.exe").resolve(),
            Path(".omp/python-runtimes/cpython-3.14-windows-x86_64-none/python.exe").resolve(),
            Path(".omp/platform-venv314/Scripts/python.exe").resolve(),
        ]
        for cand in p314_candidates:
            if cand.is_file():
                try:
                    res = subprocess.run([str(cand), "--version"], capture_output=True, text=True, timeout=5)
                    if res.returncode == 0 and "3.14" in res.stdout:
                        runtimes["3.14"] = cand
                        break
                except Exception:
                    pass

    return runtimes


def prepare_minimal_runtime(src_exe: Path, target_dir: Path) -> Path:
    """Copy the minimal CPython runtime and stdlib needed for the Worker."""
    target_dir.mkdir(parents=True, exist_ok=True)
    src_dir = src_exe.parent
    lib_dir = src_dir / "Lib"
    if not lib_dir.is_dir():
        src_dir = src_dir.parent
        lib_dir = src_dir / "Lib"

    # 1. Copy python.exe and DLLs
    target_exe = target_dir / "python.exe"
    shutil.copy2(src_exe if src_exe.name == "python.exe" else (src_dir / "python.exe"), target_exe)
    for dll in src_dir.glob("*.dll"):
        shutil.copy2(dll, target_dir / dll.name)

    # 2. Copy minimal Lib directory
    target_lib = target_dir / "Lib"
    target_lib.mkdir(parents=True, exist_ok=True)

    if lib_dir.is_dir():
        for py_file in lib_dir.glob("*.py"):
            shutil.copy2(py_file, target_lib / py_file.name)

        for pkg in ["encodings", "re", "json", "collections", "unittest", "importlib"]:
            pkg_dir = lib_dir / pkg
            if pkg_dir.is_dir():
                shutil.copytree(pkg_dir, target_lib / pkg, dirs_exist_ok=True)

    src_dlls = src_dir / "DLLs"
    if src_dlls.is_dir():
        target_dlls = target_dir / "DLLs"
        target_dlls.mkdir(parents=True, exist_ok=True)
        for d in ["_hashlib.pyd"]:
            if (src_dlls / d).is_file():
                shutil.copy2(src_dlls / d, target_dlls / d)

    return target_exe


class OwnedSession:
    """Manages all owned Windows resources with strict allocation guards and bounded cleanup."""

    def __init__(self, prefix: str = PROFILE_PREFIX) -> None:
        self.profile_name: str | None = None
        self.container_sid_ptr: int | None = None
        self.container_sid_str: str | None = None
        self.profile_folder: Path | None = None
        self.job: int | None = None
        self.temp_dir: tempfile.TemporaryDirectory | None = None
        self.fixture_root: Path | None = None
        self.cleanup_errors: list[str] = []
        self._prefix = prefix

    def allocate(self) -> None:
        """Allocate AppContainer profile, folder, Job Object, and temporary fixture root."""
        _ensure_prototypes()
        self.profile_name = self._prefix + secrets.token_hex(8)
        container_sid_ptr = ctypes.c_void_p()

        hr = sec01.userenv.CreateAppContainerProfile(
            self.profile_name, self.profile_name, self.profile_name, None, 0,
            ctypes.byref(container_sid_ptr)
        )
        if hr != 0:
            raise Sec02ProofError(f"CreateAppContainerProfile failed: 0x{hr & 0xFFFFFFFF:08x}")

        self.container_sid_ptr = container_sid_ptr.value
        self.container_sid_str = sec01._sid_string(self.container_sid_ptr)

        profile_folder_ptr = wintypes.LPWSTR()
        hr_folder = sec01.userenv.GetAppContainerFolderPath(
            self.container_sid_str, ctypes.byref(profile_folder_ptr)
        )
        if hr_folder != 0:
            raise Sec02ProofError(f"GetAppContainerFolderPath failed: 0x{hr_folder & 0xFFFFFFFF:08x}")

        self.profile_folder = Path(profile_folder_ptr.value or "")
        sec01.ole32.CoTaskMemFree(profile_folder_ptr)

        self.job = sec01._create_job()
        self.temp_dir = tempfile.TemporaryDirectory(dir=str(self.profile_folder))
        self.fixture_root = Path(self.temp_dir.name)

    def cleanup(self) -> None:
        """Bounded cleanup of all owned resources in reverse allocation order."""
        # 1. Terminate and wait for active processes in Job Object, then close handle
        if self.job is not None:
            try:
                active = sec01._job_active_processes(self.job)
                if active > 0:
                    term_ok = bool(kernel32.TerminateJobObject(ctypes.c_void_p(self.job), 1))
                    if not term_ok:
                        self.cleanup_errors.append("TerminateJobObject returned FALSE")
                    deadline = time.monotonic() + 5.0
                    stopped = False
                    while time.monotonic() < deadline:
                        if sec01._job_active_processes(self.job) == 0:
                            stopped = True
                            break
                        time.sleep(0.05)
                    if not stopped:
                        self.cleanup_errors.append("Job still has active processes after deadline")
            except BaseException as exc:
                self.cleanup_errors.append(f"TerminateJobObject cleanup error: {type(exc).__name__}")

            try:
                close_ok = bool(kernel32.CloseHandle(ctypes.c_void_p(self.job)))
                if not close_ok:
                    self.cleanup_errors.append("CloseHandle(job) returned FALSE")
                else:
                    self.job = None
            except BaseException as exc:
                self.cleanup_errors.append(f"CloseHandle(job) error: {type(exc).__name__}")

        # 2. Remove temporary fixture directory
        if self.temp_dir is not None:
            try:
                self.temp_dir.cleanup()
                self.temp_dir = None
                self.fixture_root = None
            except BaseException as exc:
                self.cleanup_errors.append(f"Fixture temp_dir cleanup error: {type(exc).__name__}")

        # 3. Free AppContainer SID pointer
        if self.container_sid_ptr is not None:
            try:
                free_res = advapi32.FreeSid(ctypes.c_void_p(self.container_sid_ptr))
                if free_res is not None and free_res != 0:
                    self.cleanup_errors.append("FreeSid returned non-zero pointer")
                else:
                    self.container_sid_ptr = None
            except BaseException as exc:
                self.cleanup_errors.append(f"FreeSid error: {type(exc).__name__}")

        # 4. Delete AppContainer Profile
        if self.profile_name is not None:
            try:
                del_hr = sec01.userenv.DeleteAppContainerProfile(self.profile_name)
                if del_hr != 0:
                    self.cleanup_errors.append(f"DeleteAppContainerProfile failed: 0x{del_hr & 0xFFFFFFFF:08x}")
                else:
                    self.profile_name = None
            except BaseException as exc:
                self.cleanup_errors.append(f"DeleteAppContainerProfile error: {type(exc).__name__}")


def setup_fixture(
    fixture_root: Path,
    user_sid: int,
    container_sid: int,
    runtimes: Mapping[str, Path],
    task_id: str,
    run_id: str,
    nonce: str,
    tree_id: str,
) -> dict[str, Any]:
    """Create the fixture directory structure and configure exact DACLs."""
    workspaces: dict[str, Path] = {}
    for ver in ["3.12", "3.14"]:
        ws = fixture_root / f"workspace_{ver.replace('.', '_')}"
        ws.mkdir(parents=True, exist_ok=True)
        sec01._set_dacl(ws, [
            (user_sid, sec01.FILE_ALL_ACCESS, sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
            (container_sid, sec01.FILE_ALL_ACCESS, sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
        ])
        workspaces[ver] = ws

    prepared_runtimes: dict[str, Path] = {}
    for ver, exe_path in runtimes.items():
        rt_dir = fixture_root / f"runtime_{ver.replace('.', '_')}"
        target_exe = prepare_minimal_runtime(exe_path, rt_dir)
        sec01._set_dacl(rt_dir, [
            (user_sid, sec01.FILE_ALL_ACCESS, sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
            (container_sid, sec01.FILE_GENERIC_READ | sec01.FILE_GENERIC_EXECUTE,
             sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
        ])
        prepared_runtimes[ver] = target_exe

    sensitive_dirs: dict[str, Path] = {}
    sensitive_files: dict[str, dict[str, Path]] = {"3.12": {}, "3.14": {}, "control": {}}
    hashes_before: dict[str, dict[str, str]] = {"3.12": {}, "3.14": {}, "control": {}}

    for name in CHECK_NAMES:
        s_dir = fixture_root / name
        s_dir.mkdir(parents=True, exist_ok=True)
        sec01._set_dacl(s_dir, [
            (user_sid, sec01.FILE_ALL_ACCESS, sec01.OBJECT_INHERIT_ACE | sec01.CONTAINER_INHERIT_ACE),
        ])
        sensitive_dirs[name] = s_dir
        for target in ["3.12", "3.14", "control"]:
            if name == "manifest":
                canary_file = s_dir / f"manifest_{target.replace('.', '_')}.json"
                manifest_content = json.dumps({
                    "task_id": task_id,
                    "run_id": run_id,
                    "nonce": nonce,
                    "tree_id": tree_id,
                    "target": target,
                    "candidate_hash": LEGIT_CANDIDATE_HASH,
                    "approved_argv_pattern": ["python.exe", "-I", "-P", "worker_legit.py"],
                    "approved_timeout_seconds": 10.0,
                    "spec": {"a": 40, "b": 2, "expected": 42},
                }, indent=2) + "\n"
                canary_file.write_text(manifest_content, encoding="utf-8")
            else:
                canary_file = s_dir / f"{name}_{target.replace('.', '_')}_canary.txt"
                canary_content = f"CANARY_SECRET_{name.upper()}_{target}_{secrets.token_hex(8)}\n"
                canary_file.write_text(canary_content, encoding="utf-8")

            sensitive_files[target][name] = canary_file
            hashes_before[target][name] = _file_hash(canary_file)

    return {
        "workspaces": workspaces,
        "runtimes": prepared_runtimes,
        "sensitive_dirs": sensitive_dirs,
        "sensitive_files": sensitive_files,
        "hashes_before": hashes_before,
    }


def run_worker_in_appcontainer(
    *,
    python_exe: Path,
    script_path: Path,
    workspace: Path,
    profile_folder: Path,
    container_sid: int,
    job: int,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Launch a Python worker under AppContainer with explicit isolation flags and attribute disposal."""
    attr_blob, attr_obj = sec01._attribute_list(container_sid)
    try:
        env_values = sec01._environment_values(
            folder=profile_folder,
            allowed=workspace,
            allowed_value="omp_sec02_proof_allowed",
        )
        command_line = f'"{python_exe}" -I -P "{script_path}"'
        res = sec01._launch(
            application=str(python_exe),
            command_line=command_line,
            current_directory=str(workspace),
            env_values=env_values,
            attributes=attr_blob,
            job=job,
            timeout_seconds=timeout_seconds,
        )

        actual_cmd = res.get("command", command_line)
        res["actual_command_line"] = actual_cmd
        res["actual_argv"] = shlex.split(actual_cmd)
        if _last_wait_timeout_ms:
            res["actual_timeout_seconds"] = float(_last_wait_timeout_ms.pop() / 1000.0)
        else:
            res["actual_timeout_seconds"] = float(timeout_seconds)
        res["candidate_file"] = str(script_path)
        res["candidate_hash"] = _file_hash(script_path) if script_path.is_file() else ""

        # Check if descendant processes are still active in the Job Object
        active_processes = sec01._job_active_processes(job)
        descendants_active = False
        if active_processes > 0:
            descendants_active = True
            term_ok = bool(kernel32.TerminateJobObject(ctypes.c_void_p(job) if ctypes else job, 1))
            deadline = time.monotonic() + 5.0
            stopped = False
            while time.monotonic() < deadline:
                if sec01._job_active_processes(job) == 0:
                    stopped = True
                    break
                time.sleep(0.05)

            if not term_ok or not stopped:
                remaining = sec01._job_active_processes(job)
                raise Sec02ProofError(
                    f"Job termination failed or processes remained active after deadline "
                    f"(active={remaining}, term_ok={term_ok})"
                )

        res["descendants_active"] = descendants_active
        res["active_processes_after_root"] = active_processes
        return res
    finally:
        if kernel32 is not None and hasattr(kernel32, "DeleteProcThreadAttributeList"):
            kernel32.DeleteProcThreadAttributeList(attr_blob)


def verify_solution_artifact(
    *,
    workspace: Path,
    identity: Mapping[str, Any],
    worker_result: Mapping[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Common Trusted Oracle: Evaluates candidate solution artifact against trusted identity.

    Runs strictly in Controller process; never imports candidate code.
    Fails closed immediately if descendant processes are still active, worker timed out,
    worker exited non-zero, manifest hash changed, candidate hash changed,
    invocation argv/timeout mismatch, or artifact fields do not match.
    """
    if worker_result.get("descendants_active"):
        return False, f"Descendants still active in Job Object (count={worker_result.get('active_processes_after_root')})", {}

    if worker_result.get("timed_out"):
        return False, "Worker execution timed out", {}

    if not _is_zero_exit(worker_result.get("exit_code")):
        return False, f"Worker exited with non-zero exit code {worker_result.get('exit_code')!r}", {}

    # Check candidate code integrity on disk and launch record
    candidate_file = None
    if "approved_argv" in identity and identity["approved_argv"]:
        candidate_file = Path(identity["approved_argv"][-1])
    elif "candidate_file" in worker_result:
        candidate_file = Path(worker_result["candidate_file"])
    else:
        candidate_file = workspace / "worker_legit.py"

    if "candidate_hash" in identity:
        if not candidate_file.is_file():
            return False, f"Candidate file missing on disk: {candidate_file}", {}
        disk_cand_hash = _file_hash(candidate_file)
        if disk_cand_hash != identity["candidate_hash"]:
            return False, f"Candidate disk hash mismatch: expected {identity['candidate_hash']}, got {disk_cand_hash}", {}
        actual_cand_hash = worker_result.get("candidate_hash")
        if actual_cand_hash != identity["candidate_hash"]:
            return False, f"Candidate launch hash mismatch: expected {identity['candidate_hash']}, got {actual_cand_hash}", {}

    # Check invocation argv and timeout
    if "approved_argv" in identity:
        actual_argv = worker_result.get("actual_argv")
        if actual_argv != identity["approved_argv"]:
            return False, f"Invocation argv mismatch: expected {identity['approved_argv']}, got {actual_argv}", {}

    if "approved_timeout_seconds" in identity:
        actual_timeout = worker_result.get("actual_timeout_seconds")
        if actual_timeout != identity["approved_timeout_seconds"]:
            return False, f"Invocation timeout mismatch: expected {identity['approved_timeout_seconds']}, got {actual_timeout}", {}

    # Check manifest integrity on disk
    manifest_path = Path(identity["manifest_path"])
    if not manifest_path.is_file():
        return False, f"Manifest file missing: {manifest_path}", {}

    cur_manifest_hash = _file_hash(manifest_path)
    if cur_manifest_hash != identity["manifest_hash"]:
        return False, f"Manifest hash mismatch: expected {identity['manifest_hash']}, got {cur_manifest_hash}", {}

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Malformed manifest JSON: {exc}", {}
    if not isinstance(manifest_data, Mapping):
        return False, "Manifest content must be a JSON object", {}
    binding_error = _manifest_binding_error(manifest_data, identity)
    if binding_error:
        return False, binding_error, {}

    sol_file = workspace / "solution.json"
    if not sol_file.is_file():
        return False, "Missing solution.json artifact", {}

    try:
        data = json.loads(sol_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Malformed solution.json JSON: {exc}", {}

    if not isinstance(data, dict):
        return False, "solution.json content must be a JSON object", {}

    if data.get("task_id") != identity["task_id"]:
        return False, f"task_id mismatch: expected '{identity['task_id']}', got '{data.get('task_id')}'", data
    if data.get("run_id") != identity["run_id"]:
        return False, f"run_id mismatch: expected '{identity['run_id']}', got '{data.get('run_id')}'", data
    if data.get("nonce") != identity["nonce"]:
        return False, f"nonce mismatch: expected '{identity['nonce']}', got '{data.get('nonce')}'", data
    if data.get("tree_id") != identity["tree_id"]:
        return False, f"tree_id mismatch: expected '{identity['tree_id']}', got '{data.get('tree_id')}'", data
    if data.get("result") != identity["expected_result"]:
        return False, f"result mismatch: expected {identity['expected_result']}, got {data.get('result')}", data

    return True, "Valid solution artifact verified against expected result and bound identity", data


def test_legitimate_task(
    python_exe: Path,
    workspace: Path,
    profile_folder: Path,
    container_sid: int,
    job: int,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute a legitimate task and verify the black-box Oracle accepts it."""
    spec = _as_mapping(identity.get("spec"))
    if spec is None:
        raise Sec02ProofError("Trusted identity is missing approved spec")
    task_input = {
        "task_id": identity["task_id"],
        "run_id": identity["run_id"],
        "nonce": identity["nonce"],
        "tree_id": identity["tree_id"],
        "a": spec["a"],
        "b": spec["b"],
    }
    (workspace / "task_input.json").write_text(json.dumps(task_input), encoding="utf-8")

    script = Path(identity["approved_argv"][-1])
    script.write_bytes(LEGIT_CANDIDATE_CODE.encode("utf-8"))
    cand_hash = identity["candidate_hash"]

    res = run_worker_in_appcontainer(
        python_exe=python_exe,
        script_path=script,
        workspace=workspace,
        profile_folder=profile_folder,
        container_sid=container_sid,
        job=job,
        timeout_seconds=identity["approved_timeout_seconds"],
    )

    oracle_passed, oracle_reason, sol_payload = verify_solution_artifact(
        workspace=workspace,
        identity=identity,
        worker_result=res,
    )

    return {
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "descendants_active": res.get("descendants_active", False),
        "active_processes_after_root": res.get("active_processes_after_root", 0),
        "actual_argv": res.get("actual_argv"),
        "actual_timeout_seconds": res.get("actual_timeout_seconds"),
        "candidate_hash": res.get("candidate_hash", cand_hash),
        "token": res.get("token"),
        "solution_exists": (workspace / "solution.json").exists(),
        "oracle_passed": oracle_passed,
        "oracle_reason": oracle_reason,
        "solution_payload": sol_payload,
    }


def test_os_denials(
    python_exe: Path,
    workspace: Path,
    profile_folder: Path,
    container_sid: int,
    job: int,
    sensitive_files: Mapping[str, Path],
    hashes_before: Mapping[str, str],
) -> dict[str, Any]:
    """Test that OS denies Worker read and write access to all sensitive assets."""
    denial_code = """
import json, sys

sensitive_files = %s
results = {}

for name, path in sensitive_files.items():
    read_err = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read()
    except Exception as exc:
        read_err = type(exc).__name__

    write_err = None
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("TAMPERED_BY_WORKER\\n")
    except Exception as exc:
        write_err = type(exc).__name__

    results[name] = {"read_err": read_err, "write_err": write_err}

with open("denials_output.json", "w", encoding="utf-8") as f:
    json.dump(results, f)
""" % json.dumps({k: str(v) for k, v in sensitive_files.items()})

    script = workspace / "worker_probe_denials.py"
    script.write_text(denial_code, encoding="utf-8")

    res = run_worker_in_appcontainer(
        python_exe=python_exe,
        script_path=script,
        workspace=workspace,
        profile_folder=profile_folder,
        container_sid=container_sid,
        job=job,
    )

    denials_output_file = workspace / "denials_output.json"
    worker_results = {}
    if denials_output_file.exists():
        worker_results = json.loads(denials_output_file.read_text(encoding="utf-8"))

    denials: dict[str, Any] = {}
    all_denied = True
    for name, path in sensitive_files.items():
        w_res = worker_results.get(name, {})
        read_denied = w_res.get("read_err") == "PermissionError"
        write_denied = w_res.get("write_err") == "PermissionError"
        cur_hash = _file_hash(path)
        hash_unchanged = (cur_hash == hashes_before[name])

        if not (read_denied and write_denied and hash_unchanged):
            all_denied = False

        denials[name] = {
            "path": str(path),
            "read_denied": read_denied,
            "read_exception": w_res.get("read_err"),
            "write_denied": write_denied,
            "write_exception": w_res.get("write_err"),
            "hash_before": hashes_before[name],
            "hash_after": cur_hash,
            "hash_unchanged": hash_unchanged,
        }

    return {
        "all_denied": all_denied,
        "assets": denials,
    }


def test_attacks(
    python_exe: Path,
    workspace: Path,
    profile_folder: Path,
    container_sid: int,
    job: int,
    sensitive_files: Mapping[str, Path],
    hashes_before: Mapping[str, str],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Test 7 specific adversary attack scenarios and verify Oracle detection via common Oracle."""
    attacks: dict[str, Any] = {}

    # Helper to clean workspace markers between attacks
    def _clean_markers(*marker_names: str) -> None:
        for m in marker_names:
            p = workspace / m
            if p.exists():
                p.unlink()

    # Attack 1: Tamper with test / expected file in vault
    manifest_file = sensitive_files["manifest"]
    _clean_markers("tamper_status.json", "solution.json")
    tamper_code = f"""
import sys, json
attempted = True
perm_error = False
try:
    with open(r"{manifest_file}", "w", encoding="utf-8") as f:
        f.write('{{"expected": 999}}\\n')
except Exception as exc:
    perm_error = (type(exc).__name__ == "PermissionError")

with open("tamper_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": attempted, "perm_error": perm_error}}, f)

# Write artifact with tampered expected
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{"task_id": "{identity['task_id']}", "run_id": "{identity['run_id']}", "nonce": "{identity['nonce']}", "tree_id": "{identity['tree_id']}", "result": 999}}, f)
"""
    script1 = workspace / "attack1_tamper_expected.py"
    script1.write_text(tamper_code, encoding="utf-8")
    res1 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script1, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file1 = workspace / "tamper_status.json"
    executed1 = status_file1.exists() and (res1["exit_code"] == 0) and (not res1["timed_out"])
    hash_after1 = _file_hash(manifest_file)
    write_blocked1 = (hash_after1 == hashes_before["manifest"])
    passed1, reason1, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res1
    )
    attacks["tamper_expected"] = {
        "description": "Attempt to overwrite expected solution in vault",
        "launch_result": {"exit_code": res1["exit_code"], "timed_out": res1["timed_out"]},
        "attack_executed": executed1,
        "write_blocked": write_blocked1,
        "hash_unchanged": write_blocked1,
        "oracle_verdict": "PASS" if passed1 else "FAIL",
        "oracle_reason": reason1,
        "attack_detected": executed1 and (not passed1) and write_blocked1,
        "oracle_fooled": passed1,
    }

    # Attack 2: Import injection (sitecustomize.py in workspace)
    _clean_markers("injection_status.json", "injected.txt", "sitecustomize.py", "solution.json")
    sitecustomize = workspace / "sitecustomize.py"
    sitecustomize.write_text("import sys; open('injected.txt', 'w').write('INJECTED')\n", encoding="utf-8")
    verify_isolation_code = f"""
import os, json
injected = os.path.exists("injected.txt")
with open("injection_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": True, "injected": injected}}, f)
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{"task_id": "{identity['task_id']}", "run_id": "{identity['run_id']}", "nonce": "{identity['nonce']}", "tree_id": "{identity['tree_id']}", "result": 42 if injected else 999}}, f)
"""
    script2 = workspace / "attack2_verify_isolation.py"
    script2.write_text(verify_isolation_code, encoding="utf-8")
    res2 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script2, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file2 = workspace / "injection_status.json"
    executed2 = status_file2.exists() and (res2["exit_code"] == 0) and (not res2["timed_out"])
    injected_file = workspace / "injected.txt"
    sitecustomize_ignored = not injected_file.exists()
    passed2, reason2, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res2
    )
    attacks["import_injection"] = {
        "description": "Drop sitecustomize.py in workspace to intercept runner",
        "launch_result": {"exit_code": res2["exit_code"], "timed_out": res2["timed_out"]},
        "attack_executed": executed2,
        "sitecustomize_ignored": sitecustomize_ignored,
        "oracle_verdict": "PASS" if passed2 else "FAIL",
        "oracle_reason": reason2,
        "attack_detected": executed2 and (not passed2) and sitecustomize_ignored,
        "oracle_fooled": passed2,
    }
    _clean_markers("sitecustomize.py", "injected.txt", "injection_status.json")

    # Attack 3: Tamper runner config
    config_file = sensitive_files["runner_config"]
    _clean_markers("config_tamper_status.json", "solution.json")
    tamper_cfg = f"""
import sys, json
attempted = True
perm_error = False
try:
    with open(r"{config_file}", "w", encoding="utf-8") as f:
        f.write('{{"allow_all": true}}\\n')
except Exception as exc:
    perm_error = (type(exc).__name__ == "PermissionError")
with open("config_tamper_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": attempted, "perm_error": perm_error}}, f)
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{"task_id": "{identity['task_id']}", "run_id": "{identity['run_id']}", "nonce": "{identity['nonce']}", "tree_id": "{identity['tree_id']}", "result": 999}}, f)
"""
    script3 = workspace / "attack3_tamper_config.py"
    script3.write_text(tamper_cfg, encoding="utf-8")
    res3 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script3, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file3 = workspace / "config_tamper_status.json"
    executed3 = status_file3.exists() and (res3["exit_code"] == 0) and (not res3["timed_out"])
    hash_cfg_after = _file_hash(config_file)
    write_blocked3 = (hash_cfg_after == hashes_before["runner_config"])
    passed3, reason3, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res3
    )
    attacks["tamper_runner_config"] = {
        "description": "Attempt to edit runner config policy",
        "launch_result": {"exit_code": res3["exit_code"], "timed_out": res3["timed_out"]},
        "attack_executed": executed3,
        "write_blocked": write_blocked3,
        "hash_unchanged": write_blocked3,
        "oracle_verdict": "PASS" if passed3 else "FAIL",
        "oracle_reason": reason3,
        "attack_detected": executed3 and (not passed3) and write_blocked3,
        "oracle_fooled": passed3,
    }
    _clean_markers("config_tamper_status.json")

    # Attack 4: Fake PASS on stdout
    _clean_markers("fake_pass_status.json", "solution.json")
    fake_pass_code = f"""
import sys, json
with open("fake_pass_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": True}}, f)
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{"task_id": "{identity['task_id']}", "run_id": "{identity['run_id']}", "nonce": "{identity['nonce']}", "tree_id": "{identity['tree_id']}", "result": 999}}, f)
print("PASSED: 10/10 tests passed successfully")
sys.exit(0)
"""
    script4 = workspace / "attack4_fake_pass.py"
    script4.write_text(fake_pass_code, encoding="utf-8")
    res4 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script4, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file4 = workspace / "fake_pass_status.json"
    executed4 = status_file4.exists() and (res4["exit_code"] == 0) and (not res4["timed_out"])
    passed4, reason4, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res4
    )
    attacks["fake_pass_stdout"] = {
        "description": "Worker prints PASS on stdout with invalid solution",
        "launch_result": {"exit_code": res4["exit_code"], "timed_out": res4["timed_out"]},
        "attack_executed": executed4,
        "oracle_verdict": "PASS" if passed4 else "FAIL",
        "oracle_reason": reason4,
        "attack_detected": executed4 and (not passed4),
        "oracle_fooled": passed4,
    }
    _clean_markers("fake_pass_status.json")

    # Attack 5: Early exit 0 without artifacts
    _clean_markers("early_exit_marker.txt", "solution.json")
    early_exit_code = """
import sys, os
with open("early_exit_marker.txt", "w", encoding="utf-8") as f:
    f.write("EARLY_EXIT_ATTEMPTED")
os._exit(0)
"""
    script5 = workspace / "attack5_early_exit.py"
    script5.write_text(early_exit_code, encoding="utf-8")
    res5 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script5, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    marker_file5 = workspace / "early_exit_marker.txt"
    executed5 = marker_file5.exists() and (res5["exit_code"] == 0) and (not res5["timed_out"])
    passed5, reason5, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res5
    )
    attacks["early_exit_zero"] = {
        "description": "Worker exits 0 immediately without writing artifacts",
        "launch_result": {"exit_code": res5["exit_code"], "timed_out": res5["timed_out"]},
        "attack_executed": executed5,
        "oracle_verdict": "PASS" if passed5 else "FAIL",
        "oracle_reason": reason5,
        "attack_detected": executed5 and (not passed5),
        "oracle_fooled": passed5,
    }
    _clean_markers("early_exit_marker.txt")

    # Attack 6: Replay attack (stale run_id / nonce executed via Worker)
    _clean_markers("replay_status.json", "solution.json")
    replay_code = f"""
import json
with open("replay_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": True}}, f)
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{
        "task_id": "{identity['task_id']}",
        "run_id": "STALE_RUN_ID_PREVIOUS",
        "nonce": "STALE_NONCE_PREVIOUS",
        "tree_id": "{identity['tree_id']}",
        "result": 42
    }}, f)
"""
    script6 = workspace / "attack6_replay.py"
    script6.write_text(replay_code, encoding="utf-8")
    res6 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script6, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file6 = workspace / "replay_status.json"
    executed6 = status_file6.exists() and (res6["exit_code"] == 0) and (not res6["timed_out"])
    passed6, reason6, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res6
    )
    attacks["replay_old_results"] = {
        "description": "Worker writes solution from an earlier run_id/nonce",
        "launch_result": {"exit_code": res6["exit_code"], "timed_out": res6["timed_out"]},
        "attack_executed": executed6,
        "oracle_verdict": "PASS" if passed6 else "FAIL",
        "oracle_reason": reason6,
        "attack_detected": executed6 and (not passed6),
        "oracle_fooled": passed6,
    }
    _clean_markers("replay_status.json")

    # Attack 7: Monkeypatching unittest / runner inside Worker interpreter
    _clean_markers("monkeypatch_status.json", "solution.json")
    monkeypatch_code = f"""
import sys, json, unittest

# Worker actively monkeypatches assertion logic in its own process
orig_equal = unittest.TestCase.assertEqual
unittest.TestCase.assertEqual = lambda self, a, b, msg=None: True

tc = unittest.TestCase()
# Normal evaluation would fail: 40 + 2 == 999
tc.assertEqual(40 + 2, 999)

with open("monkeypatch_status.json", "w", encoding="utf-8") as f:
    json.dump({{"attempted": True, "patched": True}}, f)

# Forged output claimed by Worker because internal assertion succeeded
with open("solution.json", "w", encoding="utf-8") as f:
    json.dump({{"task_id": "{identity['task_id']}", "run_id": "{identity['run_id']}", "nonce": "{identity['nonce']}", "tree_id": "{identity['tree_id']}", "result": 999}}, f)
"""
    script7 = workspace / "attack7_monkeypatch.py"
    script7.write_text(monkeypatch_code, encoding="utf-8")
    res7 = run_worker_in_appcontainer(
        python_exe=python_exe, script_path=script7, workspace=workspace,
        profile_folder=profile_folder, container_sid=container_sid, job=job,
    )
    status_file7 = workspace / "monkeypatch_status.json"
    executed7 = status_file7.exists() and (res7["exit_code"] == 0) and (not res7["timed_out"])
    passed7, reason7, _ = verify_solution_artifact(
        workspace=workspace, identity=identity, worker_result=res7
    )
    attacks["worker_monkeypatching"] = {
        "description": "Worker attempts to monkeypatch testing framework",
        "launch_result": {"exit_code": res7["exit_code"], "timed_out": res7["timed_out"]},
        "attack_executed": executed7,
        "oracle_verdict": "PASS" if passed7 else "FAIL",
        "oracle_reason": reason7,
        "attack_detected": executed7 and (not passed7),
        "oracle_fooled": passed7,
    }
    _clean_markers("monkeypatch_status.json")

    return attacks


def test_same_user_control(
    control_files: Mapping[str, Path]
) -> dict[str, Any]:
    """Verify that a same-user process outside AppContainer can access the canaries."""
    control_results: dict[str, Any] = {}
    all_ok = True

    for name, path in control_files.items():
        read_ok = False
        try:
            content = path.read_text(encoding="utf-8")
            read_ok = ("CANARY_SECRET" in content) or ("TASK-SEC02-VERIFY" in content)
        except Exception:
            read_ok = False

        write_ok = False
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("CONTROL_WRITE\n")
            write_ok = True
        except Exception:
            write_ok = False

        if not (read_ok and write_ok):
            all_ok = False

        control_results[name] = {"read_ok": read_ok, "write_ok": write_ok}

    return {
        "all_accessible": all_ok,
        "assets": control_results,
    }


def run_proof() -> tuple[dict[str, Any], int]:
    """Execute the full SEC-02 Phase A native proof on Windows."""
    if os.name != "nt":
        return {
            "schema": SCHEMA,
            "verdict": "UNSUPPORTED",
            "reason": "Host is not Windows",
        }, 1

    _ensure_prototypes()
    user_blob, user_sid = sec01._current_user_sid()
    user_str = sec01._sid_string(user_sid)
    cur_token = sec01._current_token_summary()

    # Discover Python runtimes
    runtimes = find_python_runtimes()
    if "3.12" not in runtimes:
        return {
            "schema": SCHEMA,
            "verdict": "BLOCKED",
            "reason": "Python 3.12 controller runtime not found",
        }, 1
    if "3.14" not in runtimes:
        return {
            "schema": SCHEMA,
            "verdict": "BLOCKED",
            "reason": "Python 3.14 secondary runtime not found (required by A2)",
        }, 1

    session = OwnedSession()
    task_id = "TASK-SEC02-VERIFY"
    run_id = f"RUN-{secrets.token_hex(6)}"
    nonce = secrets.token_hex(8)
    tree_id = make_canonical_tree_id(task_id, LEGIT_CANDIDATE_HASH)

    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "verdict": "FAIL",
        "principal": {
            "user_sid": user_str,
            "integrity_sid": cur_token.get("integrity_sid"),
            "elevated": cur_token.get("elevated", False),
        },
        "profile": {},
        "runtimes_tested": {},
        "oracle": {
            "process_separation": True,
            "black_box_evaluation": True,
            "task_id": task_id,
            "run_id": run_id,
            "nonce": nonce,
            "tree_id": tree_id,
            "candidate_hash": LEGIT_CANDIDATE_HASH,
            "approved_argv_pattern": ["python.exe", "-I", "-P", "worker_legit.py"],
            "approved_timeout_seconds": 10.0,
            "expected_result": 42,
            "task_identity_bound": True,
            "run_identity_bound": True,
            "manifest_identity_bound": True,
            "manifest_hash": "",
        },
        "cleanup": {
            "profile_deleted": False,
            "fixture_removed": False,
            "errors": [],
        },
    }

    all_passed = True
    try:
        session.allocate()
        assert session.profile_name is not None
        assert session.container_sid_str is not None
        assert session.container_sid_ptr is not None
        assert session.profile_folder is not None
        assert session.job is not None
        assert session.fixture_root is not None

        evidence["profile"] = {
            "name": session.profile_name,
            "sid": session.container_sid_str,
            "folder": str(session.profile_folder),
        }

        fixture = setup_fixture(
            fixture_root=session.fixture_root,
            user_sid=user_sid,
            container_sid=session.container_sid_ptr,
            runtimes=runtimes,
            task_id=task_id,
            run_id=run_id,
            nonce=nonce,
            tree_id=tree_id,
        )
        prepared_runtimes = fixture["runtimes"]
        workspaces = fixture["workspaces"]
        all_sensitive_files = fixture["sensitive_files"]
        all_hashes_before = fixture["hashes_before"]

        # The approved manifest is the source of trusted identity, spec and invocation.
        approved_manifests: dict[str, dict[str, Any]] = {}
        manifest_errors: dict[str, str] = {}
        for ver in ["3.12", "3.14"]:
            approved, manifest_err = _load_approved_manifest(
                all_sensitive_files[ver]["manifest"], expected_target=ver
            )
            if approved is None:
                manifest_errors[ver] = manifest_err
                all_passed = False
            else:
                approved_manifests[ver] = approved

        primary_manifest = approved_manifests.get("3.12")
        if primary_manifest is not None:
            evidence["oracle"].update({
                "task_id": primary_manifest["task_id"],
                "run_id": primary_manifest["run_id"],
                "nonce": primary_manifest["nonce"],
                "tree_id": primary_manifest["tree_id"],
                "candidate_hash": primary_manifest["candidate_hash"],
                "approved_argv_pattern": primary_manifest["approved_argv_pattern"],
                "approved_timeout_seconds": primary_manifest["approved_timeout_seconds"],
                "expected_result": primary_manifest["spec"]["expected"],
            })
        if manifest_errors:
            evidence["oracle"]["manifest_errors"] = manifest_errors

        # Record trusted manifest hash in Oracle evidence
        trusted_manifest_path = all_sensitive_files["3.12"]["manifest"]
        evidence["oracle"]["manifest_hash"] = all_hashes_before["3.12"]["manifest"]

        # Same-user control on dedicated control files
        t_control = test_same_user_control(all_sensitive_files["control"])
        if not t_control["all_accessible"]:
            all_passed = False

        for ver in ["3.12", "3.14"]:
            if ver not in prepared_runtimes:
                all_passed = False
                evidence["runtimes_tested"][ver] = {
                    "status": "FAIL",
                    "reason": f"Python {ver} runtime missing or failed to prepare",
                }
                continue

            approved = approved_manifests.get(ver)
            if approved is None:
                evidence["runtimes_tested"][ver] = {
                    "status": "FAIL",
                    "reason": manifest_errors.get(ver, "approved manifest unavailable"),
                }
                continue
            if primary_manifest is not None and any(
                approved[key] != primary_manifest[key]
                for key in APPROVED_MANIFEST_FIELDS
            ):
                all_passed = False
                evidence["runtimes_tested"][ver] = {
                    "status": "FAIL",
                    "reason": "approved manifest differs from primary runtime manifest",
                }
                continue

            rt_exe = prepared_runtimes[ver]
            ws = workspaces[ver]
            sensitive_files = all_sensitive_files[ver]
            hashes_before = all_hashes_before[ver]

            approved_script = ws / approved["approved_argv_pattern"][3]
            identity = {
                "task_id": approved["task_id"],
                "run_id": approved["run_id"],
                "nonce": approved["nonce"],
                "tree_id": approved["tree_id"],
                "expected_result": approved["spec"]["expected"],
                "spec": approved["spec"],
                "manifest_path": sensitive_files["manifest"],
                "manifest_hash": hashes_before["manifest"],
                "candidate_hash": approved["candidate_hash"],
                "approved_argv": [str(rt_exe), "-I", "-P", str(approved_script)],
                "approved_timeout_seconds": approved["approved_timeout_seconds"],
            }

            # 1. Legitimate task
            t_legit = test_legitimate_task(
                python_exe=rt_exe,
                workspace=ws,
                profile_folder=session.profile_folder,
                container_sid=session.container_sid_ptr,
                job=session.job,
                identity=identity,
            )

            # 2. OS Denials
            t_denials = test_os_denials(
                python_exe=rt_exe,
                workspace=ws,
                profile_folder=session.profile_folder,
                container_sid=session.container_sid_ptr,
                job=session.job,
                sensitive_files=sensitive_files,
                hashes_before=hashes_before,
            )

            # 3. Adversary attacks
            t_attacks = test_attacks(
                python_exe=rt_exe,
                workspace=ws,
                profile_folder=session.profile_folder,
                container_sid=session.container_sid_ptr,
                job=session.job,
                sensitive_files=sensitive_files,
                hashes_before=hashes_before,
                identity=identity,
            )

            rt_ok = (
                t_legit["oracle_passed"]
                and (t_legit["exit_code"] == 0)
                and (not t_legit["timed_out"])
                and (not t_legit["descendants_active"])
                and t_denials["all_denied"]
                and all(
                    att.get("attack_executed") and att.get("attack_detected") and not att.get("oracle_fooled", True)
                    for att in t_attacks.values()
                )
                and t_control["all_accessible"]
            )
            if not rt_ok:
                all_passed = False

            evidence["runtimes_tested"][ver] = {
                "status": "PASS" if rt_ok else "FAIL",
                "executable": str(rt_exe),
                "legitimate_task": t_legit,
                "os_denials": t_denials,
                "attacks": t_attacks,
                "same_user_control": t_control,
            }

    except (KeyboardInterrupt, SystemExit):
        all_passed = False
        raise
    except Exception as exc:
        all_passed = False
        session.cleanup_errors.append(f"Proof execution exception: {type(exc).__name__}")
    finally:
        session.cleanup()
        evidence["cleanup"]["profile_deleted"] = (session.profile_name is None)
        evidence["cleanup"]["fixture_removed"] = (session.temp_dir is None)
        evidence["cleanup"]["errors"] = session.cleanup_errors

    if all_passed and not session.cleanup_errors:
        evidence["verdict"] = "PASS"
        exit_code = 0
    else:
        evidence["verdict"] = "FAIL"
        exit_code = 1

    return evidence, exit_code


def verify_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """Verify a SEC-02 Phase A evidence report without raising on malformed structure."""
    try:
        return _verify_evidence(evidence)
    except Exception as exc:
        return [f"Malformed evidence structure: {type(exc).__name__}"]


def _verify_evidence(evidence: Mapping[str, Any]) -> list[str]:
    """Strictly validate a SEC-02 Phase A evidence report and return all failures."""
    failures: list[str] = []
    if not isinstance(evidence, Mapping):
        return ["Evidence must be a JSON object"]

    if evidence.get("schema") != SCHEMA:
        failures.append(f"Invalid schema: expected '{SCHEMA}', got '{evidence.get('schema')}'")

    if evidence.get("verdict") != "PASS":
        failures.append(f"Verdict is not PASS: got '{evidence.get('verdict')}'")

    principal = _as_mapping(evidence.get("principal"))
    if principal is None:
        failures.append("Principal must be a JSON object")
        principal = {}
    principal_user_sid = principal.get("user_sid", "")
    if not (isinstance(principal_user_sid, str) and principal_user_sid.startswith("S-1-5-")):
        failures.append("Missing or invalid user_sid in principal")
    if principal.get("elevated", True) is not False:
        failures.append("Principal must not be elevated")
    if principal.get("integrity_sid") != "S-1-16-8192":
        failures.append(f"Principal integrity must be medium (S-1-16-8192), got '{principal.get('integrity_sid')}'")

    profile = _as_mapping(evidence.get("profile"))
    if profile is None:
        failures.append("Profile must be a JSON object")
        profile = {}
    profile_sid = profile.get("sid", "")
    if not (isinstance(profile_sid, str) and profile_sid.startswith("S-1-15-2-")):
        failures.append("Missing or invalid AppContainer SID in profile")
    if not profile.get("folder"):
        failures.append("Missing profile folder in evidence")

    runtimes = _as_mapping(evidence.get("runtimes_tested"))
    if runtimes is None:
        failures.append("runtimes_tested must be a JSON object")
        runtimes = {}
    # Both 3.12 and 3.14 are mandatory per AC A2
    for req_ver in ["3.12", "3.14"]:
        req_data = _as_mapping(runtimes.get(req_ver))
        if req_ver not in runtimes:
            failures.append(f"Python {req_ver} runtime test is required but missing from report")
        elif req_data is None:
            failures.append(f"Python {req_ver} test status must be PASS, got 'None'")
        elif req_data.get("status") != "PASS":
            failures.append(f"Python {req_ver} test status must be PASS, got '{req_data.get('status')}'")

    oracle = _as_mapping(evidence.get("oracle"))
    if oracle is None:
        failures.append("Oracle must be a JSON object")
        oracle = {}
    if not oracle.get("process_separation"):
        failures.append("Oracle must maintain process separation")
    if not oracle.get("black_box_evaluation"):
        failures.append("Oracle must perform black-box evaluation")
    if not (oracle.get("task_identity_bound") and oracle.get("run_identity_bound") and oracle.get("manifest_identity_bound")):
        failures.append("Oracle must bind task, run, and manifest identity")
    oracle_task_id = oracle.get("task_id")
    if not isinstance(oracle_task_id, str) or not oracle_task_id:
        failures.append("Oracle missing task_id")
        oracle_task_id = ""
    if not oracle.get("run_id"):
        failures.append("Oracle missing run_id")
    if not oracle.get("nonce"):
        failures.append("Oracle missing nonce")
    if not oracle.get("tree_id"):
        failures.append("Oracle missing tree_id")
    if oracle.get("expected_result") != 42:
        failures.append(f"Oracle expected_result must match approved spec expected value 42, got '{oracle.get('expected_result')}'")
    cand_hash_oracle = oracle.get("candidate_hash", "")
    if not _is_sha256(cand_hash_oracle):
        failures.append("Oracle missing valid candidate_hash SHA-256 digest")
    elif cand_hash_oracle != LEGIT_CANDIDATE_HASH:
        failures.append(f"Oracle candidate_hash does not match approved candidate hash '{LEGIT_CANDIDATE_HASH}'")
    canonical_tree = make_canonical_tree_id(oracle_task_id, LEGIT_CANDIDATE_HASH)
    if oracle.get("tree_id") != canonical_tree:
        failures.append(f"Oracle tree_id '{oracle.get('tree_id')}' does not match canonical tree identity '{canonical_tree}'")
    manifest_hash = oracle.get("manifest_hash", "")
    if not _is_sha256(manifest_hash):
        failures.append("Oracle missing valid manifest_hash SHA-256 digest")
    approved_pattern = oracle.get("approved_argv_pattern")
    if not _valid_argv_pattern(approved_pattern):
        failures.append("Oracle missing or invalid approved_argv_pattern")
    approved_timeout = oracle.get("approved_timeout_seconds")
    if not _is_number(approved_timeout) or approved_timeout <= 0:
        failures.append("Oracle missing or invalid approved_timeout_seconds")

    for ver, raw_data in runtimes.items():
        data = _as_mapping(raw_data)
        if data is None:
            failures.append(f"Runtime {ver} evidence must be a JSON object")
            continue
        if data.get("status") != "PASS":
            continue

        legit = _as_mapping(data.get("legitimate_task"))
        if legit is None:
            failures.append(f"Runtime {ver} legitimate_task must be a JSON object")
            continue
        token = _as_mapping(legit.get("token"))
        if token is None:
            failures.append(f"Runtime {ver} Worker token must be a JSON object")
            token = {}
        if not token.get("is_app_container"):
            failures.append(f"Runtime {ver} Worker token is not AppContainer")
        if token.get("integrity_sid") != "S-1-16-4096":
            failures.append(f"Runtime {ver} Worker token integrity must be low (S-1-16-4096)")
        if token.get("capability_count", -1) != 0:
            failures.append(f"Runtime {ver} Worker capability_count must be 0")

        # Bind Worker SIDs to Principal and Profile
        if token.get("user_sid") != principal_user_sid:
            failures.append(f"Runtime {ver} Worker user_sid '{token.get('user_sid')}' does not match principal '{principal_user_sid}'")
        if token.get("app_container_sid") != profile_sid:
            failures.append(f"Runtime {ver} Worker app_container_sid '{token.get('app_container_sid')}' does not match profile '{profile_sid}'")

        if not _is_zero_exit(legit.get("exit_code")):
            failures.append(f"Runtime {ver} legitimate task exit_code must be 0 (strict int), got {legit.get('exit_code')!r}")
        if legit.get("timed_out", True) is not False:
            failures.append(f"Runtime {ver} legitimate task must not time out")

        # Provenance: candidate_hash, actual_argv, actual_timeout_seconds
        c_hash = legit.get("candidate_hash", "")
        if not (isinstance(c_hash, str) and SHA256_REGEX.match(c_hash)):
            failures.append(f"Runtime {ver} legitimate task missing or malformed candidate_hash SHA-256 digest")
        elif c_hash != LEGIT_CANDIDATE_HASH:
            failures.append(f"Runtime {ver} legitimate task candidate_hash does not match approved hash '{LEGIT_CANDIDATE_HASH}'")

        actual_argv = legit.get("actual_argv")
        if not _approved_argv_matches(approved_pattern, actual_argv):
            failures.append(
                f"Runtime {ver} legitimate task actual_argv does not match approved "
                f"argv pattern/order {approved_pattern}: got {actual_argv}"
            )

        actual_timeout = legit.get("actual_timeout_seconds")
        if (
            not _is_number(actual_timeout)
            or not _is_number(approved_timeout)
            or actual_timeout != approved_timeout
        ):
            failures.append(
                f"Runtime {ver} legitimate task actual_timeout_seconds must match "
                f"approved {approved_timeout}, got {actual_timeout}"
            )

        # Contradiction check: active count vs flag
        active_cnt = legit.get("active_processes_after_root", 0)
        if not isinstance(active_cnt, int) or isinstance(active_cnt, bool):
            failures.append(f"Runtime {ver} active_processes_after_root must be a strict int")
            active_cnt = 0
        desc_flag = legit.get("descendants_active", False)
        if not isinstance(desc_flag, bool):
            failures.append(f"Runtime {ver} descendants_active must be a boolean")
            desc_flag = True
        if active_cnt > 0 and not desc_flag:
            failures.append(f"Runtime {ver} contradictory descendant state: active_processes_after_root > 0 but descendants_active is False")
        if active_cnt != 0 or desc_flag:
            failures.append(f"Runtime {ver} legitimate task had active descendants")

        if not legit.get("oracle_passed"):
            failures.append(f"Runtime {ver} legitimate task oracle check failed: {legit.get('oracle_reason')}")

        # Cross-check legitimate solution payload against Oracle trusted identity
        payload = _as_mapping(legit.get("solution_payload"))
        if payload is None:
            failures.append(f"Runtime {ver} solution_payload must be a JSON object")
            payload = {}
        if payload.get("task_id") != oracle.get("task_id"):
            failures.append(f"Runtime {ver} payload task_id '{payload.get('task_id')}' does not match oracle '{oracle.get('task_id')}'")
        if payload.get("run_id") != oracle.get("run_id"):
            failures.append(f"Runtime {ver} payload run_id '{payload.get('run_id')}' does not match oracle '{oracle.get('run_id')}'")
        if payload.get("nonce") != oracle.get("nonce"):
            failures.append(f"Runtime {ver} payload nonce '{payload.get('nonce')}' does not match oracle '{oracle.get('nonce')}'")
        if payload.get("tree_id") != oracle.get("tree_id"):
            failures.append(f"Runtime {ver} payload tree_id '{payload.get('tree_id')}' does not match oracle '{oracle.get('tree_id')}'")
        if payload.get("result") != oracle.get("expected_result"):
            failures.append(f"Runtime {ver} payload result '{payload.get('result')}' does not match expected '{oracle.get('expected_result')}'")

        denials = _as_mapping(data.get("os_denials"))
        if denials is None:
            failures.append(f"Runtime {ver} os_denials must be a JSON object")
            denials = {}
        if not denials.get("all_denied"):
            failures.append(f"Runtime {ver} did not achieve all OS denials")
        denial_assets = _as_mapping(denials.get("assets"))
        if denial_assets is None:
            failures.append(f"Runtime {ver} os_denials.assets must be a JSON object")
            denial_assets = {}

        for c_name in CHECK_NAMES:
            asset = _as_mapping(denial_assets.get(c_name))
            if asset is None:
                failures.append(f"Runtime {ver} os_denials asset '{c_name}' must be a JSON object")
                asset = {}
            if not asset.get("read_denied"):
                failures.append(f"Runtime {ver} read not denied for asset '{c_name}'")
            if not asset.get("write_denied"):
                failures.append(f"Runtime {ver} write not denied for asset '{c_name}'")
            if not asset.get("hash_unchanged"):
                failures.append(f"Runtime {ver} hash changed for asset '{c_name}'")
            hb = asset.get("hash_before", "")
            ha = asset.get("hash_after", "")
            if not (_is_sha256(hb) and _is_sha256(ha)):
                failures.append(f"Runtime {ver} asset '{c_name}' hash values are not valid SHA-256 digests")
            if hb != ha:
                failures.append(f"Runtime {ver} asset '{c_name}' hash_before != hash_after")

        control = _as_mapping(data.get("same_user_control"))
        if control is None:
            failures.append(f"Runtime {ver} same_user_control must be a JSON object")
            control = {}
        if not control.get("all_accessible"):
            failures.append(f"Runtime {ver} same-user control failed to access assets")
        control_assets = _as_mapping(control.get("assets"))
        if control_assets is None:
            failures.append(f"Runtime {ver} same_user_control.assets must be a JSON object")
            control_assets = {}
        for c_name in CHECK_NAMES:
            c_asset = _as_mapping(control_assets.get(c_name))
            if c_asset is None:
                failures.append(f"Runtime {ver} same-user control asset '{c_name}' must be a JSON object")
                c_asset = {}
            if not (c_asset.get("read_ok") and c_asset.get("write_ok")):
                failures.append(f"Runtime {ver} same-user control asset '{c_name}' not fully accessible")

        attacks = _as_mapping(data.get("attacks"))
        if attacks is None:
            failures.append(f"Runtime {ver} attacks dictionary must not be empty")
            attacks = {}
        elif not attacks:
            failures.append(f"Runtime {ver} attacks dictionary must not be empty")
        for req_atk in REQUIRED_ATTACKS:
            if req_atk not in attacks:
                failures.append(f"Runtime {ver} missing required attack: '{req_atk}'")
                continue
            atk_data = _as_mapping(attacks[req_atk])
            if atk_data is None:
                failures.append(f"Runtime {ver} attack '{req_atk}' must be a JSON object")
                continue
            launch = _as_mapping(atk_data.get("launch_result"))
            if launch is None:
                failures.append(f"Runtime {ver} attack '{req_atk}' missing launch_result dictionary")
            else:
                if launch.get("timed_out", True) is not False:
                    failures.append(f"Runtime {ver} attack '{req_atk}' timed out during execution")
                if not _is_zero_exit(launch.get("exit_code")):
                    failures.append(
                        f"Runtime {ver} attack '{req_atk}' launch exit_code must be 0 "
                        f"(strict int), got {launch.get('exit_code')!r}"
                    )
            if not atk_data.get("attack_executed", False):
                failures.append(f"Runtime {ver} attack '{req_atk}' was not actually executed")

            # Contradiction check: verdict vs fooled
            v = atk_data.get("oracle_verdict")
            fooled = atk_data.get("oracle_fooled", True)
            if v == "PASS" and not fooled:
                failures.append(f"Runtime {ver} attack '{req_atk}' contradictory: verdict is PASS but oracle_fooled is False")
            if v != "FAIL":
                failures.append(f"Runtime {ver} attack '{req_atk}' oracle_verdict must be FAIL, got '{v}'")
            if fooled:
                failures.append(f"Runtime {ver} oracle was fooled by attack '{req_atk}'")
            if not atk_data.get("attack_detected", False):
                failures.append(f"Runtime {ver} attack '{req_atk}' was not detected")

    cleanup = _as_mapping(evidence.get("cleanup"))
    if cleanup is None:
        failures.append("Cleanup must be a JSON object")
        cleanup = {}
    if not cleanup.get("profile_deleted"):
        failures.append("AppContainer profile was not deleted during cleanup")
    if not cleanup.get("fixture_removed"):
        failures.append("Fixture was not removed during cleanup")
    if cleanup.get("errors"):
        failures.append(f"Cleanup reported errors: {cleanup.get('errors')}")

    return failures


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for sec02_windows proof tool."""
    parser = argparse.ArgumentParser(description="SEC-02 Windows Trusted Verification Proof")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    run_cmd = sub.add_parser("run", help="Execute the native proof")
    run_cmd.add_argument("--out", type=Path, help="Path to write JSON evidence report")

    ver_cmd = sub.add_parser("verify", help="Verify a JSON evidence report")
    ver_cmd.add_argument("report", type=Path, help="Path to evidence report file")

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        evidence, exit_code = run_proof()
        out_text = json.dumps(evidence, indent=2, ensure_ascii=False)
        if args.out:
            args.out.write_text(out_text, encoding="utf-8")
            print(f"Evidence report written to {args.out}")
        else:
            print(out_text)
        return exit_code

    if args.subcommand == "verify":
        if not args.report.is_file():
            print(f"Error: report file not found: {args.report}", file=sys.stderr)
            return 1
        try:
            data = json.loads(args.report.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Error reading JSON: {exc}", file=sys.stderr)
            return 1
        failures = verify_evidence(data)
        if failures:
            print("VERIFICATION FAILED:")
            for f in failures:
                print(f" - {f}")
            return 1
        print("VERIFICATION PASSED: Report satisfies all SEC-02 Phase A criteria.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
