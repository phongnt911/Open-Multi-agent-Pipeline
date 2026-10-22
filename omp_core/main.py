"""Single-task MVP. The checkout isolates Git changes, not OS permissions."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Iterator, TypeAlias, TypedDict, cast
import uuid


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

SCHEMA_VERSION = 2
CONFIG_SCHEMA_VERSION = 1
TASK_SCHEMA_VERSION = 1

PHASES = frozenset({
    "preparing", "worker", "verifying", "committing", "complete",
})
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "preparing": frozenset({"worker"}),
    "worker": frozenset({"verifying"}),
    "verifying": frozenset({"committing"}),
    "committing": frozenset({"complete"}),
    "complete": frozenset(),
}
COMPLETION_KINDS = frozenset({"committed", "no_change"})

CONFIG_FIELDS = frozenset({
    "schema_version", "worker_argv", "test_argv", "timeout_seconds",
})
TASK_FIELDS = frozenset({
    "schema_version", "specification", "allowed_files_to_modify",
})
STATE_V1_FIELDS = frozenset({
    "schema_version", "run_id", "phase", "base_commit", "config", "task",
    "child_pid", "last_error", "result_commit",
})
STATE_V2_FIELDS = frozenset({
    "schema_version", "run_id", "phase", "base_commit", "config", "task",
    "child_pid", "last_error", "result_commit",
    "verification_evidence", "commit_intent", "completion_kind",
})
STATE_FIELDS = STATE_V2_FIELDS

STATE_REQUIRED_FIELDS = (
    "schema_version", "run_id", "phase", "base_commit", "config", "task",
)
VERIFICATION_EVIDENCE_FIELDS = frozenset({
    "candidate_tree", "command_hash", "task_hash", "run_id", "exit_code",
})
COMMIT_INTENT_FIELDS = frozenset({
    "run_id", "base_commit", "expected_tree", "commit_marker",
})
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
COMMIT_OID_PATTERN = re.compile(
    r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z"
)


class Config(TypedDict):
    """Versioned Worker and verification settings stored in .omp/config.json."""

    schema_version: int
    worker_argv: list[str]
    test_argv: list[str]
    timeout_seconds: int


class Task(TypedDict):
    """Versioned task specification and exact files the Worker may change."""

    schema_version: int
    specification: str
    allowed_files_to_modify: list[str]


class VerificationEvidence(TypedDict):
    """Cryptographic evidence verifying candidate tree with trusted test command."""

    candidate_tree: str
    command_hash: str
    task_hash: str
    run_id: str
    exit_code: int


class CommitIntent(TypedDict):
    """Pre-commit intent recorded to reconcile interrupted commits on resume."""

    run_id: str
    base_commit: str
    expected_tree: str
    commit_marker: str


class State(TypedDict, total=False):
    """Checkpoint fields used by OMP.

    ``schema_version``, ``run_id``, ``phase``, ``base_commit``, ``config`` and
    ``task`` are required. Checkpoint v2 adds ``verification_evidence``,
    ``commit_intent``, and ``completion_kind``. ``validate_state`` is the
    authoritative boundary check.
    """

    schema_version: int
    run_id: str
    phase: str
    base_commit: str
    config: Config
    task: Task
    child_pid: int | None
    last_error: str
    result_commit: str
    verification_evidence: VerificationEvidence
    commit_intent: CommitIntent
    completion_kind: str


class OmpError(ValueError):
    """Expected OMP failure reported without a traceback.

    ``ValueError`` remains the base type for compatibility with the original
    MVP API. Use ``InputError`` or ``OperationError`` to distinguish invalid
    input from a failed operation.
    """


class InputError(OmpError):
    """Invalid CLI, config, or task input detected before side effects."""


class OperationError(OmpError):
    """A validated Git, filesystem, lock, or child-process operation failed."""


DEFAULT_TEST_SCRIPT = (
    "import sys, unittest\n"
    "suite = unittest.defaultTestLoader.discover('tests')\n"
    "count = suite.countTestCases()\n"
    "print(f'Discovered {count} test(s)')\n"
    "if count == 0:\n"
    "    print('FAIL: no tests discovered', file=sys.stderr)\n"
    "    sys.exit(1)\n"
    "sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())"
)


def read(path: Path, error: type[OmpError] = InputError) -> JsonValue:
    """Read UTF-8 JSON from *path*.

    Raises:
        OmpError: the requested subclass when the file cannot be read, is not
            valid UTF-8, does not contain valid JSON, exceeds the interpreter
            number limit, or nests too deeply. Messages distinguish read,
            encoding, syntax, value, and nesting failures without echoing the
            payload. The original error is preserved as ``__cause__``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise error(f"Cannot read JSON from {path}: {exc}") from exc
    except UnicodeError as exc:
        raise error(f"Cannot decode UTF-8 JSON from {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise error(f"Invalid JSON in {path}: {exc}") from exc
    except ValueError as exc:
        raise error(f"JSON value is not supported in {path}") from exc
    except RecursionError as exc:
        raise error(f"JSON nesting is too deep in {path}") from exc


def read_object(path: Path, error: type[OmpError] = InputError) -> JsonObject:
    """Read a JSON object and reject other top-level types.

    This is not full schema validation; DAT-01 owns version and field checks.
    It prevents the MVP from failing later with ``AttributeError`` on a list or
    scalar document.

    Raises:
        OmpError: the requested subclass for unreadable, invalid, or non-object
            JSON.
    """
    value = read(path, error)
    if not isinstance(value, dict):
        raise error(f"{path} must contain a JSON object")
    return cast(JsonObject, value)


def save(path: Path, value: JsonValue) -> None:
    """Replace a checkpoint only after its complete contents reach disk.

    Side effects:
        Creates *path*'s parent directory, writes a sibling temporary file,
        flushes and fsyncs it, then replaces *path* atomically.

    Raises:
        OSError: directory creation, write, fsync, or replace failed. A
            leftover temporary file is removed when possible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            try:
                os.unlink(name)
            except OSError:
                pass


@contextmanager
def lock(path: Path) -> Iterator[None]:
    """Hold an exclusive non-blocking project lock for the context lifetime.

    Side effects:
        Creates *path* and its parent directory and keeps the file open until
        the context exits.

    Raises:
        OperationError: another process holds the lock or the OS lock call
            fails. The original ``OSError`` is chained.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OperationError(
                "Another OMP operation holds the project lock"
            ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def git(root: Path, *args: str) -> str:
    """Run Git in *root* and return stripped UTF-8 stdout.

    Raises:
        OperationError: Git is unavailable, execution fails, or it exits
            nonzero. Git's stderr or failure class is used as the message and
            the original error is chained.
    """
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(detail or "Git command failed") from exc
    except FileNotFoundError as exc:
        raise OperationError("Git executable was not found") from exc
    except OSError as exc:
        raise OperationError(
            f"Git command could not be executed: {type(exc).__name__}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise OperationError("Git output is not valid UTF-8") from exc


def git_object_id_length(root: Path) -> int:
    """Return the repository object ID length using read-only Git metadata.

    SHA-1 repositories use 40 hexadecimal characters and SHA-256 repositories
    use 64. Git metadata failures (missing Git, permissions, a corrupt repo, or
    an unsupported ``--show-object-format`` option) propagate as
    ``OperationError`` with their cause; this boundary never guesses SHA-1.

    Raises:
        OperationError: Git cannot report a supported object format.
    """
    object_format = git(root, "rev-parse", "--show-object-format")
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    raise OperationError("Unsupported Git object format")


def _is_int(value: JsonValue) -> bool:
    """Return whether *value* is a JSON integer, not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _object(
    value: JsonValue, label: str, error: type[OmpError]
) -> JsonObject:
    """Return *value* as a JSON object or raise *error*."""
    if not isinstance(value, dict):
        raise error(f"{label} must be a JSON object")
    return value


def _check_fields(
    value: JsonObject,
    allowed: frozenset[str],
    label: str,
    error: type[OmpError],
) -> None:
    """Reject unknown fields without echoing their values."""
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise error(f"{label} has unknown field(s): {', '.join(unknown)}")


def _schema_version(
    value: JsonObject,
    label: str,
    *,
    allow_legacy: bool,
    error: type[OmpError],
    expected_version: int = 1,
) -> int:
    """Validate the supported schema version, accepting legacy inputs."""
    if "schema_version" not in value:
        if allow_legacy:
            return expected_version
        raise error(f"{label} is missing schema_version")
    raw = value["schema_version"]
    if not _is_int(raw):
        raise error(f"{label} schema_version must be integer {expected_version}")
    if raw != expected_version:
        raise error(f"Unsupported {label} schema_version")
    return expected_version


def _validate_oid(
    value: JsonValue,
    label: str,
    error: type[OmpError],
    oid_length: int | None = None,
) -> str:
    """Validate a full Git object ID in a SHA-1 or SHA-256 repository."""
    lengths = (oid_length,) if oid_length is not None else (40, 64)
    if (not isinstance(value, str) or len(value) not in lengths
            or not COMMIT_OID_PATTERN.fullmatch(value)):
        expected = f"{oid_length}" if oid_length is not None else "40 or 64"
        raise error(
            f"{label} must be a full {expected}-character hexadecimal "
            "Git object ID"
        )
    return value


def _safe_allowed_path(name: str) -> bool:
    """Return whether *name* is an exact relative path safe for the MVP."""
    if (not name or "\x00" in name or "\\" in name or ":" in name
            or name.startswith("/") or any(char in name for char in "*?[]")):
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return not any(part.lower() in (".git", ".omp") for part in parts)


def validate_config(
    value: JsonValue,
    *,
    allow_legacy: bool = True,
    error: type[OmpError] = InputError,
) -> Config:
    """Validate and normalize a config object.

    Legacy config without ``schema_version`` is accepted as v0 and normalized
    to v1 in the returned in-memory object. The caller's JSON object is not
    modified. Unknown fields and invalid values fail closed.

    Raises:
        OmpError: *error* when the top-level value, version, field set, argv,
            timeout, or nested value is invalid.
    """
    config = _object(value, "config", error)
    _check_fields(config, CONFIG_FIELDS, "config", error)
    version = _schema_version(
        config, "config", allow_legacy=allow_legacy, error=error,
        expected_version=CONFIG_SCHEMA_VERSION,
    )
    argv_values: dict[str, list[str]] = {}
    for key in ("worker_argv", "test_argv"):
        if key not in config:
            raise error(f"config is missing field: {key}")
        raw_argv = config[key]
        if not isinstance(raw_argv, list) or not raw_argv:
            raise error(f"{key} must be a nonempty argument array")
        argv: list[str] = []
        for arg in raw_argv:
            if not isinstance(arg, str) or not arg:
                raise error(f"{key} must contain only nonempty strings")
            if "\x00" in arg:
                raise error(f"{key} must not contain NUL bytes")
            argv.append(arg)
        argv_values[key] = argv
    if "timeout_seconds" not in config:
        raise error("config is missing field: timeout_seconds")
    timeout = config["timeout_seconds"]
    if not _is_int(timeout) or not 1 <= timeout <= 86400:
        raise error("timeout_seconds must be an integer from 1 to 86400")
    return {
        "schema_version": version,
        "worker_argv": argv_values["worker_argv"],
        "test_argv": argv_values["test_argv"],
        "timeout_seconds": timeout,
    }


def validate_task(
    value: JsonValue,
    *,
    allow_legacy: bool = True,
    error: type[OmpError] = InputError,
) -> Task:
    """Validate and normalize a task object.

    Legacy task without ``schema_version`` is accepted as v0 and normalized to
    v1 in the returned in-memory object. The caller's JSON object is not
    modified. Allowed paths are exact relative file paths using ``/``.

    Raises:
        OmpError: *error* when the top-level value, version, field set,
            specification, or allowed path is invalid.
    """
    task = _object(value, "task", error)
    _check_fields(task, TASK_FIELDS, "task", error)
    version = _schema_version(
        task, "task", allow_legacy=allow_legacy, error=error,
        expected_version=TASK_SCHEMA_VERSION,
    )
    if "specification" not in task:
        raise error("task is missing field: specification")
    specification = task["specification"]
    if not isinstance(specification, str) or not specification.strip():
        raise error("specification must be a nonempty string")
    if "allowed_files_to_modify" not in task:
        raise error("task is missing field: allowed_files_to_modify")
    raw_paths = task["allowed_files_to_modify"]
    if not isinstance(raw_paths, list) or not raw_paths:
        raise error("allowed_files_to_modify must be a nonempty path array")
    paths: list[str] = []
    for name in raw_paths:
        if not isinstance(name, str) or not _safe_allowed_path(name):
            raise error("allowed_files_to_modify contains an unsafe path")
        paths.append(name)
    return {
        "schema_version": version,
        "specification": specification,
        "allowed_files_to_modify": paths,
    }


def validate(
    config: JsonValue, task: JsonValue
) -> tuple[Config, Task]:
    """Validate input config/task and return normalized in-memory copies.

    Legacy input without ``schema_version`` is normalized to v1 in memory only;
    source files are never rewritten by validation.

    Raises:
        InputError: config or task is invalid.
    """
    return validate_config(config), validate_task(task)


def compute_task_hash(task: Task) -> str:
    """Return a deterministic SHA-256 hash of task specification and allowed files."""
    import hashlib
    canonical = json.dumps(
        {
            "specification": task["specification"],
            "allowed_files_to_modify": sorted(task["allowed_files_to_modify"]),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_verification_evidence(
    value: JsonValue,
    state_run_id: str,
    *,
    oid_length: int | None = None,
    error: type[OmpError] = OperationError,
) -> VerificationEvidence:
    """Validate verification evidence fields.

    Raises:
        OmpError: if any field is missing, invalid, or mismatched.
    """
    evidence = _object(value, "verification_evidence", error)
    _check_fields(
        evidence, VERIFICATION_EVIDENCE_FIELDS, "verification_evidence", error
    )
    for req in ("candidate_tree", "command_hash", "task_hash", "run_id", "exit_code"):
        if req not in evidence:
            raise error(f"verification_evidence is missing field: {req}")
    candidate_tree = _validate_oid(
        evidence["candidate_tree"], "candidate_tree", error, oid_length
    )
    command_hash = evidence["command_hash"]
    if (
        not isinstance(command_hash, str)
        or len(command_hash) != 64
        or not re.fullmatch(r"[0-9a-fA-F]{64}", command_hash)
    ):
        raise error(
            "verification_evidence command_hash must be a 64-character hexadecimal SHA-256 hash"
        )
    task_hash = evidence["task_hash"]
    if (
        not isinstance(task_hash, str)
        or len(task_hash) != 64
        or not re.fullmatch(r"[0-9a-fA-F]{64}", task_hash)
    ):
        raise error(
            "verification_evidence task_hash must be a 64-character hexadecimal SHA-256 hash"
        )
    run_id = evidence["run_id"]
    if not isinstance(run_id, str) or run_id != state_run_id:
        raise error("verification_evidence run_id must match state run_id")
    exit_code = evidence["exit_code"]
    if not _is_int(exit_code) or exit_code != 0:
        raise error("verification_evidence exit_code must be integer 0")
    return {
        "candidate_tree": candidate_tree,
        "command_hash": command_hash,
        "task_hash": task_hash,
        "run_id": run_id,
        "exit_code": exit_code,
    }


def validate_commit_intent(
    value: JsonValue,
    state_run_id: str,
    state_base_commit: str,
    *,
    oid_length: int | None = None,
    error: type[OmpError] = OperationError,
) -> CommitIntent:
    """Validate commit intent fields.

    Raises:
        OmpError: if any field is missing, invalid, or mismatched.
    """
    intent = _object(value, "commit_intent", error)
    _check_fields(intent, COMMIT_INTENT_FIELDS, "commit_intent", error)
    for req in ("run_id", "base_commit", "expected_tree", "commit_marker"):
        if req not in intent:
            raise error(f"commit_intent is missing field: {req}")
    run_id = intent["run_id"]
    if not isinstance(run_id, str) or run_id != state_run_id:
        raise error("commit_intent run_id must match state run_id")
    base_commit = _validate_oid(
        intent["base_commit"], "base_commit", error, oid_length
    )
    if base_commit != state_base_commit:
        raise error("commit_intent base_commit must match state base_commit")
    expected_tree = _validate_oid(
        intent["expected_tree"], "expected_tree", error, oid_length
    )
    commit_marker = intent["commit_marker"]
    if not isinstance(commit_marker, str) or not commit_marker.strip():
        raise error("commit_intent commit_marker must be a nonempty string")
    return {
        "run_id": run_id,
        "base_commit": base_commit,
        "expected_tree": expected_tree,
        "commit_marker": commit_marker,
    }


def validate_state(
    value: JsonValue,
    *,
    oid_length: int | None = None,
    error: type[OmpError] = OperationError,
) -> State:
    """Validate a checkpoint object and return a normalized copy.

    Supports checkpoint schema version 1 (read-only compatibility) and schema
    version 2 (with verification evidence, commit intent, and completion kind).
    Config and task nested in state must be v1. Unknown fields, bad types,
    unsafe paths/IDs and inconsistent phases fail closed.

    Raises:
        OmpError: *error* when state is unreadable, unsupported, or invalid.
    """
    state = _object(value, "state", error)
    if "schema_version" not in state:
        raise error("state is missing field: schema_version")
    raw_version = state["schema_version"]
    if not _is_int(raw_version):
        raise error("state schema_version must be integer 1 or 2")
    if raw_version not in (1, 2):
        raise error("Unsupported state schema_version")
    version = raw_version

    allowed_fields = STATE_V1_FIELDS if version == 1 else STATE_V2_FIELDS
    _check_fields(state, allowed_fields, "state", error)
    for key in STATE_REQUIRED_FIELDS:
        if key not in state:
            raise error(f"state is missing field: {key}")

    run_id = state["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise error(
            "state run_id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
        )
    phase = state["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise error("state phase is not a supported phase")
    base_commit = _validate_oid(
        state["base_commit"], "base_commit", error, oid_length
    )

    config = validate_config(
        state["config"], allow_legacy=False, error=error
    )
    task = validate_task(state["task"], allow_legacy=False, error=error)

    child_pid = state.get("child_pid")
    if child_pid is not None and (
        not _is_int(child_pid) or child_pid <= 0
    ):
        raise error("state child_pid must be null or a positive integer")

    normalized: State = {
        "schema_version": version,
        "run_id": run_id,
        "phase": phase,
        "base_commit": base_commit,
        "config": config,
        "task": task,
        "child_pid": child_pid,
    }
    if "last_error" in state:
        last_error = state["last_error"]
        if not isinstance(last_error, str):
            raise error("state last_error must be a string")
        normalized["last_error"] = last_error
    if "result_commit" in state:
        normalized["result_commit"] = _validate_oid(
            state["result_commit"], "result_commit", error, oid_length
        )

    if version == 1:
        if phase == "complete" and "result_commit" not in state:
            raise error("state phase complete requires result_commit")
        return normalized

    # Schema version 2 specific validations
    if "verification_evidence" in state:
        normalized["verification_evidence"] = validate_verification_evidence(
            state["verification_evidence"],
            run_id,
            oid_length=oid_length,
            error=error,
        )
    if "commit_intent" in state:
        normalized["commit_intent"] = validate_commit_intent(
            state["commit_intent"],
            run_id,
            base_commit,
            oid_length=oid_length,
            error=error,
        )
    if "verification_evidence" in normalized and "commit_intent" in normalized:
        if (
            normalized["commit_intent"]["expected_tree"]
            != normalized["verification_evidence"]["candidate_tree"]
        ):
            raise error(
                "verification_evidence candidate_tree does not match commit intent expected_tree"
            )
    if "completion_kind" in state:
        kind = state["completion_kind"]
        if not isinstance(kind, str) or kind not in COMPLETION_KINDS:
            raise error(
                f"state completion_kind must be one of: {sorted(COMPLETION_KINDS)}"
            )
        normalized["completion_kind"] = kind

    if (
        phase in ("committing", "complete")
        and "verification_evidence" not in normalized
    ):
        raise error(f"state phase {phase} requires verification_evidence")
    if (
        phase in ("committing", "complete")
        and "commit_intent" not in normalized
    ):
        raise error(
            f"state phase {phase} requires commit_intent (cannot proceed without commit intent)"
        )
    if phase == "complete":
        if "result_commit" not in normalized:
            raise error("state phase complete requires result_commit")
        if "completion_kind" not in normalized:
            raise error("state phase complete requires completion_kind")
        if normalized["completion_kind"] == "no_change":
            if normalized["result_commit"] != base_commit:
                raise error(
                    "state completion_kind no_change requires result_commit equal to base_commit"
                )
    return normalized


def _timeout_error(
    label: str, exc: subprocess.TimeoutExpired
) -> subprocess.TimeoutExpired:
    """Return a timeout that exposes only *label* and the timeout value.

    The original ``TimeoutExpired`` command field can contain worker argv and
    provider tokens. Callers may chain this sanitized error instead.
    """
    return subprocess.TimeoutExpired(label, exc.timeout)


def stop(process: subprocess.Popen[bytes], label: str = "child") -> None:
    """Stop the child process tree and wait for the child to exit.

    Side effects:
        Sends SIGKILL to the process group on POSIX or runs ``taskkill /T /F``
        on Windows. Waits at most 10 seconds.

    Raises:
        subprocess.TimeoutExpired: the child did not exit within 10 seconds.
            The exception contains *label* and the timeout, not the original
            argv.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    cleanup_timeout: subprocess.TimeoutExpired | None = None
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        cleanup_timeout = _timeout_error(f"{label} cleanup", exc)
    if cleanup_timeout is not None:
        raise cleanup_timeout


def _public_operation_error(label: str, exc: BaseException) -> BaseException:
    """Return an argv-free public form of an operation error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(label, exc)
    return exc


def _public_cleanup_error(label: str, exc: BaseException) -> BaseException:
    """Return an argv-free public form of a cleanup error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(f"{label} cleanup", exc)
    return OperationError(f"{label} cleanup failed: {type(exc).__name__}")


def execute(
    argv: list[str],
    checkout: Path,
    run_dir: Path,
    state: State,
    checkpoint: Path,
    label: str,
    prompt: str | None = None,
) -> None:
    """Run one Worker or test command inside *checkout*.

    Side effects:
        Appends stdout/stderr to ``<label>.log`` under *run_dir*, records
        ``child_pid`` in *state*/checkpoint before sending input, and clears it
        after the process exits. On any exception the child tree is stopped.

    Raises:
        OperationError: the command cannot be started or exits nonzero. The
            log path or failure class is included without echoing argv.
        subprocess.TimeoutExpired: the command or its cleanup exceeds the
            configured timeout. The exception is sanitized to a label.
        KeyboardInterrupt/SystemExit: propagated after stopping the child.
    """
    with (run_dir / f"{label}.log").open("ab") as log:
        options: dict[str, object] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=checkout,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                **options,
            )
        except OSError as exc:
            raise OperationError(
                f"Cannot start {label}: {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            raise OperationError(
                f"Cannot start {label}: invalid argument"
            ) from exc
        state["child_pid"] = process.pid
        operation_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        save_error: BaseException | None = None
        try:
            try:
                save(checkpoint, state)
                process.communicate(
                    (prompt or "").encode("utf-8"),
                    timeout=state["config"]["timeout_seconds"],
                )
                if process.returncode:
                    raise OperationError(
                        f"{label} exited {process.returncode}; "
                        f"see {run_dir / (label + '.log')}"
                    )
            except BaseException as exc:
                operation_error = exc
            if operation_error is not None:
                try:
                    stop(process, label)
                except BaseException as exc:
                    cleanup_error = exc
        finally:
            if process.poll() is not None:
                state["child_pid"] = None
            try:
                save(checkpoint, state)
            except BaseException as exc:
                save_error = exc

        primary_error: BaseException | None = None
        if operation_error is not None:
            public_operation = _public_operation_error(label, operation_error)
            if cleanup_error is not None:
                public_cleanup = _public_cleanup_error(label, cleanup_error)
                public_cleanup.__cause__ = public_operation
                primary_error = public_cleanup
            else:
                primary_error = public_operation
        elif cleanup_error is not None:
            primary_error = _public_cleanup_error(label, cleanup_error)

        if save_error is not None:
            if primary_error is not None:
                save_error.__cause__ = primary_error
            raise save_error
        if primary_error is not None:
            raise primary_error


def check_scope(checkout: Path, state: State) -> None:
    """Verify HEAD, index, and changed paths still match the task scope.

    Side effects:
        None; the checkout is left untouched for inspection on failure.

    Raises:
        OperationError: HEAD changed, files were staged, paths fall outside
            ``allowed_files_to_modify``, or a path is a symlink/escapes the
            checkout.
    """
    if git(checkout, "rev-parse", "HEAD") != state["base_commit"]:
        raise OperationError("Worker changed Git HEAD")
    if git(checkout, "diff", "--cached", "--name-only"):
        raise OperationError("Worker staged files; review checkout before resuming")
    try:
        changed = subprocess.check_output([
            "git", "-C", str(checkout), "diff", "--name-only", "-z",
            state["base_commit"]
        ], stderr=subprocess.PIPE).decode("utf-8").split("\0")
        untracked = subprocess.check_output([
            "git", "-C", str(checkout), "ls-files", "--others",
            "--exclude-standard", "-z"
        ], stderr=subprocess.PIPE).decode("utf-8").split("\0")
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(detail or "Cannot inspect checkout changes") from exc
    outside = (set(changed + untracked) - {""}) - set(
        state["task"]["allowed_files_to_modify"]
    )
    if outside:
        raise OperationError(f"Changes outside task scope: {sorted(outside)}")
    for name in state["task"]["allowed_files_to_modify"]:
        path = checkout / name
        if path.is_symlink() or not path.resolve().is_relative_to(checkout.resolve()):
            raise OperationError(f"Path escapes checkout: {name}")


def transition(state: State, target_phase: str) -> State:
    """Enforce state transition invariants and advance *state* phase.

    Raises:
        OperationError: invalid transition or invariant violated.
    """
    current_phase = state.get("phase")
    valid_targets = VALID_TRANSITIONS.get(current_phase or "", frozenset())
    if target_phase not in valid_targets:
        raise OperationError(
            f"Invalid phase transition: cannot transition from {current_phase} to {target_phase}"
        )
    if target_phase == "verifying":
        if state.get("child_pid") is not None:
            raise OperationError("Cannot transition to verifying: child_pid must be null")
    elif target_phase == "committing":
        if state.get("child_pid") is not None:
            raise OperationError("Cannot transition to committing: child_pid must be null")
        evidence = state.get("verification_evidence")
        if not evidence:
            raise OperationError("Cannot transition to committing: verification_evidence is required")
        intent = state.get("commit_intent")
        if not intent:
            raise OperationError("Cannot transition to committing: commit_intent is required")
        evidence_val = validate_verification_evidence(
            evidence, state.get("run_id") or "", error=OperationError
        )
        intent_val = validate_commit_intent(
            intent,
            state.get("run_id") or "",
            state.get("base_commit") or "",
            error=OperationError,
        )
        if intent_val["expected_tree"] != evidence_val["candidate_tree"]:
            raise OperationError(
                "verification_evidence candidate_tree does not match commit intent expected_tree"
            )
    elif target_phase == "complete":
        if state.get("child_pid") is not None:
            raise OperationError("Cannot transition to complete: child_pid must be null")
        if not state.get("result_commit"):
            raise OperationError("Cannot transition to complete: result_commit is required")
        if state.get("completion_kind") not in COMPLETION_KINDS:
            raise OperationError("Cannot transition to complete: valid completion_kind is required")
        evidence = state.get("verification_evidence")
        if not evidence:
            raise OperationError("Cannot transition to complete: verification_evidence is required")
        intent = state.get("commit_intent")
        if not intent:
            raise OperationError("Cannot transition to complete: commit_intent is required")
        evidence_val = validate_verification_evidence(
            evidence, state.get("run_id") or "", error=OperationError
        )
        intent_val = validate_commit_intent(
            intent,
            state.get("run_id") or "",
            state.get("base_commit") or "",
            error=OperationError,
        )
        if intent_val["expected_tree"] != evidence_val["candidate_tree"]:
            raise OperationError(
                "verification_evidence candidate_tree does not match commit intent expected_tree"
            )
        if state["completion_kind"] == "no_change" and state["result_commit"] != state["base_commit"]:
            raise OperationError("Cannot transition to complete: no_change requires result_commit == base_commit")

    state["phase"] = target_phase
    return state


def compute_command_hash(test_argv: list[str], timeout_seconds: int) -> str:
    """Return a deterministic SHA-256 hash of test execution settings (argv and timeout)."""
    import hashlib
    canonical = json.dumps(
        {
            "test_argv": test_argv,
            "timeout_seconds": timeout_seconds,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def filter_existing_or_tracked_paths(
    checkout: Path,
    allowed_files: list[str],
    env: dict[str, str] | None = None,
) -> list[str]:
    """Return allowed paths that exist in the working tree or are tracked by Git.

    Preserves support for file modifications, additions, and deletions while
    ignoring allowed paths that do not exist and were never tracked.
    """
    existing_or_tracked: list[str] = []
    for rel in allowed_files:
        if (checkout / rel).exists():
            existing_or_tracked.append(rel)
        else:
            res = subprocess.run(
                ["git", "-C", str(checkout), "ls-files", "--", rel],
                env=env,
                capture_output=True,
                text=True,
            )
            if res.stdout.strip():
                existing_or_tracked.append(rel)
    return existing_or_tracked


def compute_candidate_tree(
    checkout: Path, allowed_files: list[str], run_dir: Path
) -> str:
    """Compute the Git tree object ID of allowed modifications in *checkout*.

    Uses an isolated temporary index file so the primary checkout index and
    working tree are not mutated.

    Raises:
        OperationError: Git fails to read HEAD or write the tree.
    """
    temp_index = run_dir / f"candidate_{uuid.uuid4().hex}.index"
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index.resolve())
    try:
        subprocess.check_output(
            ["git", "-C", str(checkout), "read-tree", "HEAD"],
            env=env,
            stderr=subprocess.PIPE,
        )
        existing_or_tracked = filter_existing_or_tracked_paths(
            checkout, allowed_files, env=env
        )
        if existing_or_tracked:
            subprocess.check_output(
                ["git", "-C", str(checkout), "add", "-A", "--", *existing_or_tracked],
                env=env,
                stderr=subprocess.PIPE,
            )
        tree_oid = subprocess.check_output(
            ["git", "-C", str(checkout), "write-tree"],
            env=env,
            stderr=subprocess.PIPE,
        ).decode("utf-8").strip()
        return tree_oid
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(detail or "Cannot compute candidate tree") from exc
    except OSError as exc:
        raise OperationError(
            f"Cannot compute candidate tree: {type(exc).__name__}"
        ) from exc
    finally:
        if temp_index.exists():
            try:
                temp_index.unlink()
            except OSError:
                pass


def check_provenance(state: State, checkout: Path, base_tree: str) -> None:
    """Verify evidence integrity, task identity, command identity, and intent.

    Raises:
        OperationError: if evidence, command, task, or intent is missing or mismatched.
    """
    evidence = state.get("verification_evidence")
    if not evidence:
        raise OperationError("Missing verification evidence")
    if evidence.get("run_id") != state["run_id"]:
        raise OperationError("Verification evidence run_id mismatch")
    if evidence.get("exit_code") != 0:
        raise OperationError("Verification evidence has nonzero exit code")
    if compute_command_hash(
        state["config"]["test_argv"], state["config"]["timeout_seconds"]
    ) != evidence.get("command_hash"):
        raise OperationError("Test command does not match verification evidence")
    if compute_task_hash(state["task"]) != evidence.get("task_hash"):
        raise OperationError("Task does not match verification evidence")
    intent = state.get("commit_intent")
    if not intent:
        raise OperationError("Unexpected commit during recovery without commit intent")
    if intent.get("run_id") != state["run_id"]:
        raise OperationError("Commit intent run_id mismatch")
    if intent.get("base_commit") != state["base_commit"]:
        raise OperationError("Commit intent base_commit mismatch")
    if intent.get("expected_tree") != evidence["candidate_tree"]:
        raise OperationError(
            "verification_evidence candidate_tree does not match commit intent expected_tree"
        )


def advance(root: Path) -> None:
    """Advance the checkpoint for *root* by one or more phases.

    Side effects:
        Clones the local repository into ``.omp/runs/<run_id>/checkout``, runs
        the configured Worker/test commands, writes logs, and creates a commit
        inside that checkout. It never changes the source repository or pushes.

    Raises:
        OperationError: the checkpoint, Git state, child process, test result,
            or task scope is invalid.
        subprocess.TimeoutExpired: a child command exceeds its timeout.
        KeyboardInterrupt/SystemExit: propagated after the child is stopped and
            the checkpoint is updated.
    """
    checkpoint = root / ".omp/state.json"
    state = validate_state(read_object(checkpoint, OperationError))
    state = validate_state(
        state, oid_length=git_object_id_length(root)
    )
    if state["schema_version"] == 1:
        raise OperationError(
            "Cannot resume run with schema_version 1; retain run/checkout and start a new run"
        )
    run_dir = root / ".omp/runs" / state["run_id"]
    checkout = run_dir / "checkout"
    if state.get("child_pid"):
        raise OperationError(
            f"Previous child PID {state['child_pid']} may still be running. "
            "Confirm it and its descendants have stopped, then clear child_pid "
            "in state.json."
        )
    if state["phase"] != "preparing" and not checkout.exists():
        if state["phase"] == "complete":
            raise OperationError("Checkout directory is missing for complete run")
        raise OperationError("Checkout directory is missing; retain state for inspection")
    if state["phase"] == "complete":
        if git(checkout, "status", "--porcelain"):
            raise OperationError("Checkout is dirty in complete run; inspect before resuming")
        base_tree = git(checkout, "rev-parse", f"{state['base_commit']}^{{tree}}")
        check_provenance(state, checkout, base_tree)
        head = git(checkout, "rev-parse", "HEAD")
        if head != state["result_commit"]:
            raise OperationError("Checkout HEAD does not match result_commit")
        head_tree = git(checkout, "rev-parse", "HEAD^{tree}")
        evidence = state["verification_evidence"]
        intent = state["commit_intent"]
        if state.get("completion_kind") == "no_change":
            if state["result_commit"] != state["base_commit"]:
                raise OperationError("no_change run requires result_commit to match base_commit")
            if head != state["base_commit"]:
                raise OperationError("no_change run requires checkout HEAD to match base_commit")
            if evidence["candidate_tree"] != base_tree:
                raise OperationError("candidate_tree does not match base_tree in no_change run")
            if head_tree != base_tree:
                raise OperationError("checkout HEAD tree does not match base_tree in no_change run")
        elif state.get("completion_kind") == "committed":
            if head_tree != evidence["candidate_tree"]:
                raise OperationError("Checkout HEAD tree does not match verification evidence candidate_tree")
            parent = git(checkout, "rev-parse", "HEAD^")
            if parent != state["base_commit"]:
                raise OperationError("Checkout HEAD parent does not match base_commit")
            commit_subject = git(checkout, "log", "-1", "--format=%s")
            if commit_subject != intent["commit_marker"]:
                raise OperationError("Checkout commit marker does not match commit intent")
        else:
            raise OperationError("Unsupported completion_kind")
        print(f"Completed {state['result_commit']} in {checkout}")
        return
    try:
        if state["phase"] == "preparing":
            if checkout.exists():
                raise OperationError(
                    "Interrupted clone: retain it for inspection; start a new run"
                )
            run_dir.mkdir(parents=True, exist_ok=True)
            git(root, "clone", "--local", "--no-hardlinks", "--no-checkout",
                root.as_posix(), checkout.as_posix())
            git(checkout, "checkout", "--detach", state["base_commit"])
            git(checkout, "remote", "remove", "origin")
            transition(state, "worker")
            save(checkpoint, state)
        if state["phase"] == "worker":
            prompt = (state["task"]["specification"] + "\nOnly modify these files: "
                      + ", ".join(state["task"]["allowed_files_to_modify"])
                      + "\nDo not commit, stage files, install packages, change tests, or run migrations."
                      + "\nThis may be a resumed task. Inspect existing changes first.")
            execute(state["config"]["worker_argv"], checkout, run_dir, state,
                    checkpoint, "worker", prompt)
            transition(state, "verifying")
            save(checkpoint, state)
        if state["phase"] == "verifying":
            check_scope(checkout, state)
            tree_before = compute_candidate_tree(
                checkout, state["task"]["allowed_files_to_modify"], run_dir
            )
            execute(
                state["config"]["test_argv"],
                checkout,
                run_dir,
                state,
                checkpoint,
                "tests",
            )
            check_scope(checkout, state)
            tree_after = compute_candidate_tree(
                checkout, state["task"]["allowed_files_to_modify"], run_dir
            )
            if tree_after != tree_before:
                raise OperationError(
                    "Candidate files were modified during verification; evidence invalidated"
                )
            message = f"OMP task {state['run_id']}"
            state["verification_evidence"] = {
                "candidate_tree": tree_before,
                "command_hash": compute_command_hash(
                    state["config"]["test_argv"],
                    state["config"]["timeout_seconds"],
                ),
                "task_hash": compute_task_hash(state["task"]),
                "run_id": state["run_id"],
                "exit_code": 0,
            }
            state["commit_intent"] = {
                "run_id": state["run_id"],
                "base_commit": state["base_commit"],
                "expected_tree": tree_before,
                "commit_marker": message,
            }
            transition(state, "committing")
            save(checkpoint, state)
        if state["phase"] == "committing":
            base_tree = git(checkout, "rev-parse", f"{state['base_commit']}^{{tree}}")
            check_provenance(state, checkout, base_tree)
            evidence = state["verification_evidence"]
            intent = state["commit_intent"]
            head = git(checkout, "rev-parse", "HEAD")

            if head == state["base_commit"]:
                git(checkout, "reset")
                check_scope(checkout, state)
                current_tree = compute_candidate_tree(
                    checkout, state["task"]["allowed_files_to_modify"], run_dir
                )
                if current_tree != evidence["candidate_tree"]:
                    raise OperationError("Candidate tree does not match verification evidence")

                if evidence["candidate_tree"] == base_tree:
                    state["result_commit"] = state["base_commit"]
                    state["completion_kind"] = "no_change"
                    transition(state, "complete")
                    state.pop("last_error", None)
                    save(checkpoint, state)
                    print(f"Completed {state['result_commit']} in {checkout}")
                    return

                message = intent["commit_marker"]
                paths_to_stage = filter_existing_or_tracked_paths(
                    checkout, state["task"]["allowed_files_to_modify"]
                )
                if paths_to_stage:
                    git(checkout, "add", "-A", "--", *paths_to_stage)
                staged_tree = git(checkout, "write-tree")
                if staged_tree != intent["expected_tree"]:
                    raise OperationError("Staged tree does not match commit intent expected tree")
                git(
                    checkout,
                    "-c", "user.name=OMP",
                    "-c", "user.email=omp@localhost",
                    "commit", "-m", message,
                )
                head = git(checkout, "rev-parse", "HEAD")
                parent = git(checkout, "rev-parse", "HEAD^")
                commit_subject = git(checkout, "log", "-1", "--format=%s")
                head_tree = git(checkout, "rev-parse", "HEAD^{tree}")
                if (
                    commit_subject != intent["commit_marker"]
                    or parent != intent["base_commit"]
                    or head_tree != intent["expected_tree"]
                    or head_tree != evidence["candidate_tree"]
                ):
                    raise OperationError(
                        "Created commit does not match commit intent or verification evidence"
                    )
            else:
                commit_subject = git(checkout, "log", "-1", "--format=%s")
                parent = git(checkout, "rev-parse", "HEAD^")
                head_tree = git(checkout, "rev-parse", "HEAD^{tree}")
                if (
                    commit_subject != intent["commit_marker"]
                    or parent != intent["base_commit"]
                    or head_tree != intent["expected_tree"]
                    or head_tree != evidence["candidate_tree"]
                ):
                    raise OperationError(
                        "Unexpected commit during recovery does not match commit intent or evidence"
                    )

            if git(checkout, "status", "--porcelain"):
                raise OperationError(
                    "Checkout is dirty after commit; inspect before resuming"
                )
            state["result_commit"] = head
            state["completion_kind"] = "committed"
            transition(state, "complete")
            state.pop("last_error", None)
            save(checkpoint, state)
            print(f"Completed {state['result_commit']} in {checkout}")
    except BaseException as exc:
        state["last_error"] = str(exc) or type(exc).__name__
        try:
            save(checkpoint, state)
        except BaseException as save_exc:
            save_exc.__cause__ = exc
            raise save_exc
        raise


def _require_git_root(root: Path) -> None:
    """Raise InputError unless *root* is the Git repository root."""
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise InputError("--root must be the Git repository root")


def _read_state(root: Path, oid_length: int | None = None) -> State:
    """Read and validate the authoritative checkpoint for *root*."""
    return validate_state(
        read_object(root / ".omp/state.json", OperationError),
        oid_length=oid_length,
    )


def _read_inputs(root: Path, task_path: Path) -> tuple[Config, Task]:
    """Read and validate config and task without writing or spawning."""
    return validate(
        read_object(root / ".omp/config.json"),
        read_object(task_path),
    )


def _check_existing_run(root: Path, oid_length: int) -> None:
    """Reject an unfinished run while accepting a valid complete checkpoint."""
    checkpoint = root / ".omp/state.json"
    if not checkpoint.exists():
        return
    state = _read_state(root, oid_length)
    if state["phase"] != "complete":
        raise OperationError("Unfinished run exists; use resume")


def _check_clean(root: Path) -> None:
    """Reject project changes before a new run mutates runtime state."""
    if git(root, "status", "--porcelain"):
        raise OperationError("Commit or stash project changes before running")


def main() -> None:
    """Run the ``omp`` command line interface.

    Expected input and operation failures print ``OMP: <message>`` to stderr
    and exit 1 without a traceback. Successful commands return normally.
    ``run`` and ``resume`` validate authoritative data before the lock and
    validate it again after acquiring the lock before any mutation.

    Side effects:
        Dispatches to state, lock, Git, process, and filesystem operations
        under ``--root``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    run = sub.add_parser("run")
    run.add_argument("task", type=Path)
    sub.add_parser("resume")
    sub.add_parser("status")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "status":
            state = _read_state(root, git_object_id_length(root))
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return
        if args.command == "init":
            _require_git_root(root)
            with lock(root / ".omp/lock"):
                path = root / ".omp/config.json"
                if path.exists():
                    raise InputError("Configuration already exists")
                save(path, {
                    "schema_version": CONFIG_SCHEMA_VERSION,
                    "worker_argv": ["codex", "exec", "--ephemeral", "--sandbox",
                                    "workspace-write", "-"],
                    "test_argv": [sys.executable, "-B", "-c", DEFAULT_TEST_SCRIPT],
                    "timeout_seconds": 600,
                })
                print(f"Created {path}; review worker and test commands before running")
            return
        if args.command == "run":
            _require_git_root(root)
            oid_length = git_object_id_length(root)
            _check_existing_run(root, oid_length)
            config, task = _read_inputs(root, args.task)
            _check_clean(root)
            with lock(root / ".omp/lock"):
                _check_existing_run(root, oid_length)
                config, task = _read_inputs(root, args.task)
                _check_clean(root)
                state = validate_state({
                    "schema_version": SCHEMA_VERSION,
                    "run_id": uuid.uuid4().hex,
                    "phase": "preparing",
                    "base_commit": git(root, "rev-parse", "HEAD"),
                    "config": config,
                    "task": task,
                    "child_pid": None,
                }, oid_length=oid_length)
                save(root / ".omp/state.json", state)
                advance(root)
            return
        oid_length = git_object_id_length(root)
        state = _read_state(root, oid_length)
        if state["schema_version"] == 1 and state["phase"] != "complete":
            raise OperationError(
                "Cannot resume run with schema_version 1; retain run/checkout and start a new run"
            )
        with lock(root / ".omp/lock"):
            state = _read_state(root, oid_length)
            if state["schema_version"] == 1 and state["phase"] != "complete":
                raise OperationError(
                    "Cannot resume run with schema_version 1; retain run/checkout and start a new run"
                )
            advance(root)
    except (OmpError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"OMP: {exc}\n")


if __name__ == "__main__":
    main()
