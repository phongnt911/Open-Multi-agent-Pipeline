"""Single-task MVP. The checkout isolates Git changes, not OS permissions."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import BinaryIO, Iterator, NamedTuple, TypeAlias, TypedDict, cast
import uuid

from .windows_job import (
    WindowsJobCleanupTimeout,
    WindowsJobError,
    WindowsJobProcess,
)


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
STATE_DIGEST_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
AUDIT_SCHEMA_VERSION = 1
ABORT_MARKER_SCHEMA_VERSION = 1
ABORT_MARKER_FILENAME = "aborted.json"
ABORTED_STATE_FILENAME = "aborted-state.json"
ABORT_MARKER_FIELDS = frozenset({
    "schema_version", "action", "run_id", "state_sha256", "child_pid",
    "created_utc",
})


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


class StateSnapshot(NamedTuple):
    """Exact checkpoint bytes, their digest, and the validated state."""

    raw: bytes
    digest: str
    state: State


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


def _parse_json_bytes(
    raw: bytes, path: Path, error: type[OmpError]
) -> JsonValue:
    """Parse UTF-8 JSON bytes without rewriting or exposing payload contents."""
    try:
        text = raw.decode("utf-8")
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


def _sanitize_exception(exc: BaseException | None) -> Exception | None:
    """Return a clean clone of an exception without secrets, filenames, or raw text.

    Preserves KeyboardInterrupt/SystemExit semantics by re-raising immediately.
    For OSError (and subclasses), returns an instance of the exact class with
    a generic message and cleared cause/context/filename.
    For OmpError, returns an instance of the same class with sanitized cause.
    """
    if exc is None:
        return None
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc
    if isinstance(exc, OSError):
        error_cls = type(exc)
        clone = error_cls(error_cls.__name__)
        clone.__cause__ = None
        clone.__context__ = None
        clone.__suppress_context__ = True
        return clone
    if isinstance(exc, OmpError):
        error_cls = type(exc)
        sanitized_cause = _sanitize_exception(exc.__cause__)
        clone = error_cls(error_cls.__name__)
        clone.__cause__ = sanitized_cause
        clone.__context__ = None
        clone.__suppress_context__ = True
        return clone
    error_cls = type(exc)
    clone = OperationError(error_cls.__name__)
    clone.__cause__ = None
    clone.__context__ = None
    clone.__suppress_context__ = True
    return clone


def _read_state_snapshot(
    checkpoint: Path,
    *,
    expected_digest: str | None = None,
    expected_run_id: str | None = None,
    oid_length: int | None = None,
    error: type[OmpError] = OperationError,
) -> StateSnapshot:
    """Read, hash, and validate exact checkpoint bytes.

    The digest is SHA-256 of the raw file bytes, not of a normalized JSON
    representation. A mismatch fails closed without writing the checkpoint.

    Raises:
        OmpError: *error* when the file cannot be read, is invalid, or does not
            match the requested run ID/digest.
    """
    try:
        raw = checkpoint.read_bytes()
    except OSError as exc:
        sanitized = _sanitize_exception(exc)
        raise error(
            f"Cannot read checkpoint: {type(exc).__name__}"
        ) from sanitized
    digest = hashlib.sha256(raw).hexdigest()
    value = _parse_json_bytes(raw, checkpoint, error)
    state = validate_state(value, oid_length=oid_length, error=error)
    if expected_run_id is not None and state["run_id"] != expected_run_id:
        raise error("state run_id does not match requested run ID")
    if expected_digest is not None and digest != expected_digest.lower():
        raise error("state digest does not match --state-sha256")
    return StateSnapshot(raw=raw, digest=digest, state=state)


def _read_state_digest(
    checkpoint: Path, *, error: type[OmpError] = OperationError
) -> str:
    """Return the SHA-256 digest of exact checkpoint bytes."""
    try:
        raw = checkpoint.read_bytes()
    except OSError as exc:
        sanitized = _sanitize_exception(exc)
        raise error(
            f"Cannot read checkpoint: {type(exc).__name__}"
        ) from sanitized
    return hashlib.sha256(raw).hexdigest()


def _abort_marker_path(root: Path, run_id: str) -> Path:
    """Return the sidecar path that marks *run_id* as aborted."""
    return root / ".omp" / "runs" / run_id / ABORT_MARKER_FILENAME


def _aborted_state_path(root: Path, run_id: str) -> Path:
    """Return the archived checkpoint path for an aborted run."""
    return root / ".omp" / "runs" / run_id / ABORTED_STATE_FILENAME


def _read_abort_marker(
    root: Path, run_id: str, *, error: type[OmpError] = OperationError
) -> JsonObject | None:
    """Read and strictly validate an abort marker, if present.

    Raises:
        OmpError: *error* when the marker exists but is unreadable, has unknown
            fields, or does not bind the requested run/digest.
    """
    path = _abort_marker_path(root, run_id)
    try:
        path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        sanitized = _sanitize_exception(exc)
        raise error(
            f"Cannot stat abort marker: {type(exc).__name__}"
        ) from sanitized
    marker = read_object(path, error)
    _check_fields(marker, ABORT_MARKER_FIELDS, "abort marker", error)
    _schema_version(
        marker,
        "abort marker",
        allow_legacy=False,
        error=error,
        expected_version=ABORT_MARKER_SCHEMA_VERSION,
    )
    for field in ("action", "run_id", "state_sha256", "child_pid", "created_utc"):
        if field not in marker:
            raise error(f"abort marker is missing field: {field}")
    if marker["action"] != "abort":
        raise error("abort marker action must be abort")
    if marker["run_id"] != run_id:
        raise error("abort marker run_id does not match requested run ID")
    digest = marker["state_sha256"]
    if not isinstance(digest, str) or not STATE_DIGEST_PATTERN.fullmatch(digest):
        raise error("abort marker state_sha256 must be a 64-character SHA-256")
    child_pid = marker["child_pid"]
    if child_pid is not None and (
        not _is_int(child_pid) or child_pid <= 0
    ):
        raise error("abort marker child_pid must be null or a positive integer")
    created = marker["created_utc"]
    if not isinstance(created, str) or not created.strip():
        raise error("abort marker created_utc must be a nonempty string")
    return marker


def _check_not_aborted(root: Path, state: State) -> None:
    """Reject a run when a matching abort marker exists.

    Raises:
        OperationError: the marker is invalid/mismatched or the run was
            intentionally abandoned. The checkpoint and clone are retained.
    """
    marker = _read_abort_marker(root, state["run_id"])
    if marker is None:
        return
    digest = _read_state_digest(root / ".omp/state.json")
    if marker["state_sha256"].lower() != digest:
        raise OperationError(
            "Abort marker does not match checkpoint bytes; retain run for inspection"
        )
    raise OperationError(
        "Run was aborted; state, clone and logs were retained; start a new run"
    )


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace *path* with *data* only after the complete bytes reach disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            try:
                os.unlink(name)
            except OSError:
                pass


def _utc_timestamp() -> str:
    """Return a sortable UTC timestamp for an audit event."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _audit_event_path(root: Path, run_id: str) -> Path:
    """Return a unique, atomic audit event path for *run_id*."""
    filename = f"{_utc_timestamp()}-{uuid.uuid4().hex}.json"
    return root / ".omp" / "audit" / run_id / filename


def _write_audit_event(
    root: Path,
    *,
    action: str,
    result: str,
    reason_code: str,
    state: State,
    before_digest: str,
    after_digest: str,
    checkpoint_changed: bool,
    child_pid_after: int | None,
    operator_confirmed_child_stopped: bool = False,
    containment_proof: str = "none",
    abort_marker: str | None = None,
) -> Path:
    """Persist one sanitized audit event atomically.

    The event intentionally excludes config, task, argv, prompt, logs, and
    raw exception text. ``reason_code`` is an OMP-owned enum-like value.

    Raises:
        OperationError: the audit file could not be written. Callers must not
            report success in that case.
    """
    path = _audit_event_path(root, state["run_id"])
    event: JsonObject = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "timestamp_utc": _utc_timestamp(),
        "action": action,
        "result": result,
        "reason_code": reason_code,
        "run_id": state["run_id"],
        "state_path": ".omp/state.json",
        "before_sha256": before_digest,
        "after_sha256": after_digest,
        "phase_before": state["phase"],
        "phase_after": state["phase"],
        "state_schema_version": state["schema_version"],
        "child_pid_before": state.get("child_pid"),
        "child_pid_after": child_pid_after,
        "checkpoint_changed": checkpoint_changed,
        "operator_confirmed_child_stopped": operator_confirmed_child_stopped,
        "containment_proof": containment_proof,
        "abort_marker": abort_marker,
    }
    failed_save: OperationError | None = None
    try:
        save(path, event)
    except OSError as exc:
        sanitized = _sanitize_exception(exc)
        err = OperationError(
            f"Cannot persist {action} audit event: {type(exc).__name__}"
        )
        err.__cause__ = sanitized
        err.__suppress_context__ = True
        failed_save = err
    if failed_save is not None:
        failed_save.__context__ = None
        raise failed_save
    return path


def _audit_refusal(
    root: Path,
    *,
    action: str,
    reason_code: str,
    snapshot: StateSnapshot,
    message: str,
    operator_confirmed_child_stopped: bool = False,
    containment_proof: str = "none",
    abort_marker: str | None = None,
) -> None:
    """Audit a refusal, then raise the refusal without mutating state.

    If audit persistence fails, the audit error becomes primary and the
    refusal remains in its cause chain.
    """
    failed_audit: OperationError | None = None
    try:
        _write_audit_event(
            root,
            action=action,
            result="refused",
            reason_code=reason_code,
            state=snapshot.state,
            before_digest=snapshot.digest,
            after_digest=snapshot.digest,
            checkpoint_changed=False,
            child_pid_after=snapshot.state.get("child_pid"),
            operator_confirmed_child_stopped=operator_confirmed_child_stopped,
            containment_proof=containment_proof,
            abort_marker=abort_marker,
        )
    except OperationError as audit_exc:
        failed_audit = audit_exc

    if failed_audit is not None:
        sanitized_audit_io = _sanitize_exception(failed_audit.__cause__)
        refusal = OperationError(message)
        refusal.__cause__ = sanitized_audit_io
        refusal.__context__ = None
        refusal.__suppress_context__ = True

        sanitized_audit_exc = OperationError(str(failed_audit))
        sanitized_audit_exc.__cause__ = refusal
        sanitized_audit_exc.__context__ = None
        sanitized_audit_exc.__suppress_context__ = True
        raise sanitized_audit_exc

    raise OperationError(message)


def _audit_error_or_raise(
    root: Path,
    *,
    action: str,
    reason_code: str,
    snapshot: StateSnapshot,
    original: BaseException,
    message: str,
    child_pid_after: int | None,
    checkpoint_changed: bool = False,
    abort_marker: str | None = None,
    after_digest: str | None = None,
) -> None:
    """Audit an operational failure, then raise without claiming success.

    If audit persistence also fails, the audit error becomes primary and keeps
    *original* in its cause chain.
    """
    if isinstance(original, (KeyboardInterrupt, SystemExit)):
        raise original
    sanitized_original = _sanitize_exception(original)
    actual_after_digest = after_digest
    if actual_after_digest is None:
        if checkpoint_changed:
            actual_after_digest = "unknown"
        else:
            actual_after_digest = snapshot.digest
    failed_audit: OperationError | None = None
    try:
        _write_audit_event(
            root,
            action=action,
            result="error",
            reason_code=reason_code,
            state=snapshot.state,
            before_digest=snapshot.digest,
            after_digest=actual_after_digest,
            checkpoint_changed=checkpoint_changed,
            child_pid_after=child_pid_after,
            abort_marker=abort_marker,
        )
    except OperationError as audit_exc:
        failed_audit = audit_exc

    if failed_audit is not None:
        sanitized_audit_io = _sanitize_exception(failed_audit.__cause__)
        if (
            sanitized_audit_io is not None
            and sanitized_original is not None
            and sanitized_original.__cause__ is None
        ):
            sanitized_original.__cause__ = sanitized_audit_io
            sanitized_original.__context__ = None
            sanitized_original.__suppress_context__ = True
        sanitized_audit_exc = OperationError(str(failed_audit))
        sanitized_audit_exc.__cause__ = sanitized_original
        sanitized_audit_exc.__context__ = None
        sanitized_audit_exc.__suppress_context__ = True
        raise sanitized_audit_exc

    op_error = OperationError(message)
    op_error.__cause__ = sanitized_original
    op_error.__context__ = None
    op_error.__suppress_context__ = True
    raise op_error


def _pid_state(pid: int) -> str:
    """Classify a PID as ``alive``, ``absent``, or ``unknown``.

    This is deliberately read-only. On Windows it opens the process with
    ``PROCESS_QUERY_LIMITED_INFORMATION`` only; it never requests terminate
    rights and never signals/kills the PID. A PID is not an ownership proof.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        error_invalid_parameter = 87
        error_not_found = 1168
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            error = ctypes.get_last_error()
            if error in (error_invalid_parameter, error_not_found):
                return "absent"
            return "unknown"
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return "unknown"
            return "alive" if code.value == still_active else "absent"
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        return "unknown"
    except OSError:
        return "unknown"
    return "alive"


def _check_recover_checkout(root: Path, state: State) -> None:
    """Validate that recover has a plausible checkout for the current phase.

    Raises:
        OperationError: an interrupted clone or missing checkout means recover
            must not pretend the run is ready.
    """
    checkout = root / ".omp" / "runs" / state["run_id"] / "checkout"
    if state["phase"] == "preparing":
        if checkout.exists():
            raise OperationError(
                "Interrupted clone: recover will not overwrite it; retain it "
                "for inspection or abort the run"
            )
        return
    if not checkout.is_dir():
        raise OperationError(
            "Checkout directory is missing; recover cannot restore evidence"
        )


def _timeout_error(
    label: str, exc: subprocess.TimeoutExpired
) -> subprocess.TimeoutExpired:
    """Return a timeout that exposes only *label* and the timeout value.

    The original ``TimeoutExpired`` command field can contain worker argv and
    provider tokens. Callers may chain this sanitized error instead.
    """
    return subprocess.TimeoutExpired(label, exc.timeout)


CLEANUP_TIMEOUT_SECONDS = 10


def _start_process(
    argv: list[str],
    checkout: Path,
    log_path: Path,
    prompt: str | None,
    run_dir: Path,
) -> tuple[subprocess.Popen[bytes] | WindowsJobProcess, BinaryIO | None]:
    """Start one process, using Kill-on-close Job containment on Windows.

    On Windows the returned process is suspended and already attached to the
    job. The caller must save ``child_pid`` and then call ``resume`` so no
    Worker code executes before containment and the checkpoint are in place.
    """
    if os.name == "nt":
        process = WindowsJobProcess.start(
            argv,
            checkout,
            log_path,
            stdin_text=prompt or "",
            temp_dir=run_dir,
        )
        return process, None
    log = log_path.open("ab")
    try:
        process = subprocess.Popen(
            argv,
            cwd=checkout,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        log.close()
        raise
    return process, log


def _tree_stopped(process: subprocess.Popen[bytes] | WindowsJobProcess) -> bool:
    """Return whether the whole process tree is proven stopped."""
    if isinstance(process, WindowsJobProcess):
        return process.tree_stopped
    return process.poll() is not None


def stop(
    process: subprocess.Popen[bytes] | WindowsJobProcess, label: str = "child"
) -> None:
    """Stop the child process tree and wait for it to exit.

    Side effects:
        On Windows, terminates the owned job and waits boundedly for an empty
        job. On POSIX, sends SIGKILL to the process group. A non-contained
        Windows process still uses bounded ``taskkill /T /F`` for compatibility.

    Raises:
        subprocess.TimeoutExpired: cleanup did not finish within the bound.
            The exception contains *label* and the timeout, not argv.
        WindowsJobError: job termination or accounting failed.
    """
    if isinstance(process, WindowsJobProcess):
        try:
            process.terminate_tree(CLEANUP_TIMEOUT_SECONDS)
        except WindowsJobCleanupTimeout as exc:
            raise subprocess.TimeoutExpired(
                f"{label} cleanup", CLEANUP_TIMEOUT_SECONDS
            ) from exc
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CLEANUP_TIMEOUT_SECONDS,
        )
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    cleanup_timeout: subprocess.TimeoutExpired | None = None
    try:
        process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        cleanup_timeout = _timeout_error(f"{label} cleanup", exc)
    if cleanup_timeout is not None:
        raise cleanup_timeout


def _redact_diagnostic(
    text: str,
    argv: list[str] | None = None,
    prompt: str | None = None,
) -> str:
    """Redact command-line values and prompt text from a diagnostic message."""
    fragments: set[str] = set()
    for argument in argv or []:
        if argument:
            fragments.add(argument)
            if argument.startswith("-") and "=" in argument:
                fragments.add(argument.split("=", 1)[1])
    if prompt:
        fragments.add(prompt)
    for fragment in sorted(fragments, key=len, reverse=True):
        text = text.replace(fragment, "<redacted>")
    return text or "<no detail>"


def _clone_diagnostic(exc: BaseException, message: str) -> BaseException:
    """Create a sanitized exception of the same broad public class.

    The clone never links the original exception, because its ``cmd``,
    filename, or context could still contain Worker argv or a token.
    """
    if isinstance(exc, WindowsJobCleanupTimeout):
        return WindowsJobCleanupTimeout(message)
    if isinstance(exc, WindowsJobError):
        return WindowsJobError(message)
    if isinstance(exc, OperationError):
        return OperationError(message)
    if isinstance(exc, OSError):
        return OSError(message)
    if not isinstance(exc, Exception):
        try:
            return type(exc)(message)
        except Exception:
            return OperationError(message)
    return OperationError(message)


def _clone_diagnostic_chain(
    exc: BaseException,
    argv: list[str] | None = None,
    prompt: str | None = None,
    seen: dict[int, BaseException] | None = None,
) -> BaseException:
    """Clone *exc* and its cause/context chain without linking raw errors.

    Every message is redacted before it is copied.  The clone keeps the broad
    public type of each node, so a nested ``WindowsJobError`` still exposes its
    stage and WinError text without exposing argv, prompt, or a raw traceback.
    """
    if seen is None:
        seen = {}
    existing = seen.get(id(exc))
    if existing is not None:
        return existing
    message = _redact_diagnostic(
        str(exc) or type(exc).__name__, argv, prompt
    )
    clone = _clone_diagnostic(exc, message)
    seen[id(exc)] = clone
    for attribute in ("__cause__", "__context__"):
        nested = getattr(exc, attribute)
        if nested is None:
            continue
        sanitized = _clone_diagnostic_chain(nested, argv, prompt, seen)
        if sanitized is not clone:
            setattr(clone, attribute, sanitized)
    return clone


def _public_operation_error(
    label: str,
    exc: BaseException,
    argv: list[str] | None = None,
    prompt: str | None = None,
) -> BaseException:
    """Return an argv-free public form of an operation error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(label, exc)
    message = _redact_diagnostic(
        str(exc) or type(exc).__name__, argv, prompt
    )
    if not isinstance(exc, Exception):
        return _clone_diagnostic_chain(exc, argv, prompt)
    if isinstance(exc, WindowsJobError):
        public = OperationError(
            f"{label} containment failed: {message}"
        )
        public.__cause__ = _clone_diagnostic_chain(exc, argv, prompt)
        return public
    if isinstance(exc, OperationError):
        return _clone_diagnostic_chain(exc, argv, prompt)
    if isinstance(exc, OSError):
        return _clone_diagnostic_chain(exc, argv, prompt)
    return OperationError(f"{label} failed: {message}")


def _public_cleanup_error(
    label: str,
    exc: BaseException,
    argv: list[str] | None = None,
    prompt: str | None = None,
) -> BaseException:
    """Return an argv-free public form of a cleanup error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(f"{label} cleanup", exc)
    message = _redact_diagnostic(
        str(exc) or type(exc).__name__, argv, prompt
    )
    return OperationError(f"{label} cleanup failed: {message}")


def _cleanup_root_cause(
    label: str,
    exc: BaseException,
    argv: list[str] | None = None,
    prompt: str | None = None,
) -> WindowsJobError | None:
    """Return a sanitized Win32 cleanup root cause, if one exists."""
    root: BaseException | None = None
    if isinstance(exc, subprocess.TimeoutExpired):
        candidate = exc.__cause__
        if isinstance(candidate, WindowsJobError):
            root = candidate
    elif isinstance(exc, WindowsJobError):
        root = exc
    if root is None:
        return None
    message = _redact_diagnostic(
        str(root) or type(root).__name__, argv, prompt
    )
    if isinstance(root, WindowsJobCleanupTimeout):
        return WindowsJobCleanupTimeout(message)
    return WindowsJobError(message)


def _public_save_error(
    exc: BaseException,
    argv: list[str] | None = None,
    prompt: str | None = None,
) -> BaseException:
    """Return a sanitized final-save error without original context."""
    message = _redact_diagnostic(
        str(exc) or type(exc).__name__, argv, prompt
    )
    return _clone_diagnostic(exc, message)


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

    On Windows the process is created suspended in a Job Object with
    ``KILL_ON_JOB_CLOSE``. ``child_pid`` is saved before the initial thread is
    resumed; the PID is cleared only after the job is positively empty.

    Side effects:
        Appends stdout/stderr to ``<label>.log`` under *run_dir*, records
        ``child_pid`` in *state*/checkpoint before sending input, and clears it
        after the process tree is stopped. On any exception the child tree is
        stopped.

    Raises:
        OperationError: the command cannot be started, exits nonzero, or leaves
            an uncontained/uncertain tree. The log path or failure class is
            included without echoing argv.
        subprocess.TimeoutExpired: the command or its cleanup exceeds the
            configured timeout. The exception is sanitized to a label.
        KeyboardInterrupt/SystemExit: propagated after stopping the child.
    """
    log_path = run_dir / f"{label}.log"
    process: subprocess.Popen[bytes] | WindowsJobProcess
    log: BinaryIO | None
    try:
        process, log = _start_process(argv, checkout, log_path, prompt, run_dir)
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
            if isinstance(process, WindowsJobProcess):
                process.resume()
            process.communicate(
                (prompt or "").encode("utf-8"),
                timeout=state["config"]["timeout_seconds"],
            )
            if process.returncode:
                raise OperationError(
                    f"{label} exited {process.returncode}; "
                    f"see {log_path}"
                )
        except BaseException as exc:
            operation_error = exc
        if operation_error is not None:
            try:
                stop(process, label)
            except BaseException as exc:
                cleanup_error = exc
    finally:
        if _tree_stopped(process):
            state["child_pid"] = None
        try:
            save(checkpoint, state)
        except BaseException as exc:
            save_error = exc
        if log is not None:
            try:
                log.close()
            except OSError:
                pass
        if isinstance(process, WindowsJobProcess):
            process.close()

    primary_error: BaseException | None = None
    if operation_error is not None:
        public_operation = _public_operation_error(
            label, operation_error, argv, prompt
        )
        if cleanup_error is not None:
            public_cleanup = _public_cleanup_error(
                label, cleanup_error, argv, prompt
            )
            cleanup_root = _cleanup_root_cause(
                label, cleanup_error, argv, prompt
            )
            if cleanup_root is not None:
                cleanup_root.__cause__ = public_operation
                public_cleanup.__cause__ = cleanup_root
            else:
                public_cleanup.__cause__ = public_operation
            primary_error = public_cleanup
        else:
            primary_error = public_operation
    elif cleanup_error is not None:
        public_cleanup = _public_cleanup_error(
            label, cleanup_error, argv, prompt
        )
        cleanup_root = _cleanup_root_cause(
            label, cleanup_error, argv, prompt
        )
        if cleanup_root is not None:
            public_cleanup.__cause__ = cleanup_root
        primary_error = public_cleanup

    if save_error is not None:
        public_save = _public_save_error(save_error, argv, prompt)
        if primary_error is not None:
            public_save.__cause__ = primary_error
        raise public_save
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


def _check_complete_run(root: Path, state: State) -> Path:
    """Validate a complete run read-only and return its checkout path.

    Raises:
        OperationError: checkout/commit tree/provenance no longer matches the
            complete checkpoint. No Worker or test command runs.
    """
    checkout = root / ".omp/runs" / state["run_id"] / "checkout"
    if not checkout.exists():
        raise OperationError("Checkout directory is missing for complete run")
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
            raise OperationError(
                "Checkout HEAD tree does not match verification evidence candidate_tree"
            )
        parent = git(checkout, "rev-parse", "HEAD^")
        if parent != state["base_commit"]:
            raise OperationError("Checkout HEAD parent does not match base_commit")
        commit_subject = git(checkout, "log", "-1", "--format=%s")
        if commit_subject != intent["commit_marker"]:
            raise OperationError("Checkout commit marker does not match commit intent")
    else:
        raise OperationError("Unsupported completion_kind")
    return checkout


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
    _check_not_aborted(root, state)
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
        _check_complete_run(root, state)
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
    """Reject an unfinished run while accepting complete or safely aborted runs.

    An aborted run is terminal only when its marker binds the current exact
    checkpoint bytes and no child_pid remains. Otherwise a new run could race a
    possibly live Worker.
    """
    checkpoint = root / ".omp/state.json"
    try:
        checkpoint.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        sanitized = _sanitize_exception(exc)
        raise OperationError(
            f"Cannot stat checkpoint: {type(exc).__name__}"
        ) from sanitized
    snapshot = _read_state_snapshot(
        checkpoint, oid_length=oid_length, error=OperationError
    )
    state = snapshot.state

    marker = _read_abort_marker(root, state["run_id"])
    if marker is not None:
        if marker["state_sha256"].lower() != snapshot.digest:
            raise OperationError(
                "Abort marker does not match checkpoint bytes; retain run for inspection"
            )
        archive = _aborted_state_path(root, state["run_id"])
        try:
            archived = archive.read_bytes()
        except OSError as exc:
            sanitized = _sanitize_exception(exc)
            raise OperationError(
                "Aborted run checkpoint archive is missing; retain run for inspection"
            ) from sanitized
        if archived != snapshot.raw:
            raise OperationError(
                "Aborted run checkpoint archive does not match state.json; retain both"
            )
        if state.get("child_pid") is not None:
            raise OperationError(
                "Aborted run still records child_pid; inspect it before starting a new run"
            )
        if marker.get("child_pid") is not None:
            raise OperationError(
                "Abort marker records child_pid; inspect it before starting a new run"
            )
        return

    if state["phase"] == "complete":
        if state.get("child_pid") is not None:
            raise OperationError(
                "Complete run still records child_pid; inspect before starting a new run"
            )
        return

    raise OperationError("Unfinished run exists; use resume")


def _check_clean(root: Path) -> None:
    """Reject project changes before a new run mutates runtime state."""
    if git(root, "status", "--porcelain"):
        raise OperationError("Commit or stash project changes before running")


def _validate_recovery_request(run_id: str, state_sha256: str) -> str:
    """Validate run/digest CLI input and return a normalized digest."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise InputError("RUN_ID must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    if not STATE_DIGEST_PATTERN.fullmatch(state_sha256):
        raise InputError("--state-sha256 must be 64 hexadecimal characters")
    return state_sha256.lower()


def _preflight_recovery(
    root: Path, run_id: str, expected_digest: str
) -> tuple[int, StateSnapshot]:
    """Validate a recovery request before taking the exclusive lock."""
    _require_git_root(root)
    oid_length = git_object_id_length(root)
    snapshot = _read_state_snapshot(
        root / ".omp/state.json",
        expected_digest=expected_digest,
        expected_run_id=run_id,
        oid_length=oid_length,
        error=InputError,
    )
    return oid_length, snapshot


def _revalidate_recovery(
    root: Path,
    *,
    action: str,
    run_id: str,
    expected_digest: str,
    oid_length: int,
) -> StateSnapshot:
    """Re-read and revalidate checkpoint bytes under the exclusive lock.

    A run ID or digest change is audited as a refusal. Callers must not mutate
    the checkpoint after this function returns.
    """
    snapshot = _read_state_snapshot(
        root / ".omp/state.json", oid_length=oid_length, error=OperationError
    )
    if snapshot.state["run_id"] != run_id:
        _audit_refusal(
            root,
            action=action,
            reason_code="state_run_id_mismatch",
            snapshot=snapshot,
            message="Checkpoint run_id changed while waiting for the lock; retry",
        )
    if snapshot.digest != expected_digest:
        _audit_refusal(
            root,
            action=action,
            reason_code="state_digest_mismatch",
            snapshot=snapshot,
            message=(
                "Checkpoint bytes changed while waiting for the lock; "
                "retry with the current --state-sha256"
            ),
        )
    return snapshot


def _abort_marker_relative(run_id: str) -> str:
    """Return the stable relative abort-marker path used in audit events."""
    return f".omp/runs/{run_id}/{ABORT_MARKER_FILENAME}"


def recover(
    root: Path,
    run_id: str,
    state_sha256: str,
    confirm_child_stopped: bool,
) -> None:
    """Prepare an interrupted checkpoint for ``resume`` without running it.

    Side effects:
        With explicit ``confirm_child_stopped`` and an absent PID, clears only
        ``state.child_pid`` atomically. Always writes one sanitized audit event.
        Never starts a Worker/test, kills a process, or modifies the checkout.

    Raises:
        InputError: invalid arguments or pre-lock checkpoint mismatch.
        OperationError: contention, refusal, invalid evidence, or checkpoint /
            audit persistence failure. A false success is never printed.
    """
    expected_digest = _validate_recovery_request(run_id, state_sha256)
    oid_length, _ = _preflight_recovery(root, run_id, expected_digest)
    checkpoint = root / ".omp/state.json"
    with lock(root / ".omp/lock"):
        snapshot = _revalidate_recovery(
            root,
            action="recover",
            run_id=run_id,
            expected_digest=expected_digest,
            oid_length=oid_length,
        )
        marker_read_error: OperationError | None = None
        try:
            marker = _read_abort_marker(root, run_id)
        except OperationError as exc:
            marker_read_error = exc
        if marker_read_error is not None:
            _audit_refusal(
                root,
                action="recover",
                reason_code="abort_marker_invalid",
                snapshot=snapshot,
                message=str(marker_read_error),
            )
        if marker is not None:
            if marker["state_sha256"].lower() != snapshot.digest:
                _audit_refusal(
                    root,
                    action="recover",
                    reason_code="abort_marker_digest_mismatch",
                    snapshot=snapshot,
                    message=(
                        "Abort marker does not match checkpoint bytes; "
                        "retain run for inspection"
                    ),
                    abort_marker=_abort_marker_relative(run_id),
                )
            _audit_refusal(
                root,
                action="recover",
                reason_code="already_aborted",
                snapshot=snapshot,
                message=(
                    "Run was aborted; state, clone and logs were retained; "
                    "start a new run"
                ),
                abort_marker=_abort_marker_relative(run_id),
            )
        state = snapshot.state
        if state["schema_version"] == 1:
            if state["phase"] != "complete":
                _audit_refusal(
                    root,
                    action="recover",
                    reason_code="unsupported_schema",
                    snapshot=snapshot,
                    message=(
                        "Cannot recover legacy schema_version 1 run; "
                        "retain evidence and start a new run"
                    ),
                )
            if state.get("child_pid") is not None:
                _audit_refusal(
                    root,
                    action="recover",
                    reason_code="complete_child_pid_recorded",
                    snapshot=snapshot,
                    message=(
                        "Complete legacy checkpoint still records child_pid; "
                        "inspect it before treating the run as complete. "
                        "OMP did not kill it."
                    ),
                )
            _write_audit_event(
                root,
                action="recover",
                result="no_change",
                reason_code="legacy_complete",
                state=state,
                before_digest=snapshot.digest,
                after_digest=snapshot.digest,
                checkpoint_changed=False,
                child_pid_after=state.get("child_pid"),
            )
            print(f"Run {run_id} is already complete; no recovery mutation")
            return
        if state["phase"] == "complete":
            if state.get("child_pid") is not None:
                _audit_refusal(
                    root,
                    action="recover",
                    reason_code="complete_child_pid_recorded",
                    snapshot=snapshot,
                    message=(
                        "Complete checkpoint still records child_pid; inspect it "
                        "before treating the run as complete. OMP did not kill it."
                    ),
                )
            try:
                _check_complete_run(root, state)
            except (OperationError, OSError, subprocess.SubprocessError) as exc:
                _audit_refusal(
                    root,
                    action="recover",
                    reason_code="complete_validation_failed",
                    snapshot=snapshot,
                    message=str(exc),
                )
            _write_audit_event(
                root,
                action="recover",
                result="no_change",
                reason_code="complete_verified",
                state=state,
                before_digest=snapshot.digest,
                after_digest=snapshot.digest,
                checkpoint_changed=False,
                child_pid_after=state.get("child_pid"),
            )
            print(f"Run {run_id} is already complete; no recovery mutation")
            return
        try:
            _check_recover_checkout(root, state)
        except OperationError as exc:
            _audit_refusal(
                root,
                action="recover",
                reason_code="checkout_unavailable",
                snapshot=snapshot,
                message=str(exc),
            )
        child_pid = state.get("child_pid")
        if child_pid is None:
            _write_audit_event(
                root,
                action="recover",
                result="no_change",
                reason_code="checkpoint_ready",
                state=state,
                before_digest=snapshot.digest,
                after_digest=snapshot.digest,
                checkpoint_changed=False,
                child_pid_after=None,
            )
            print(
                f"Run {run_id} checkpoint is ready for resume; no mutation"
            )
            return
        pid_state = _pid_state(child_pid)
        if pid_state == "alive":
            _audit_refusal(
                root,
                action="recover",
                reason_code="child_pid_alive",
                snapshot=snapshot,
                message=(
                    f"Previous child PID {child_pid} is alive; stop it and its "
                    "descendants outside OMP, then retry. OMP did not kill it."
                ),
            )
        if pid_state == "unknown":
            _audit_refusal(
                root,
                action="recover",
                reason_code="child_pid_ownership_unknown",
                snapshot=snapshot,
                message=(
                    f"Cannot determine state of previous child PID {child_pid}; "
                    "ownership is unknown, so recover refuses without killing it."
                ),
            )
        if not confirm_child_stopped:
            _audit_refusal(
                root,
                action="recover",
                reason_code="child_pid_absent_unconfirmed",
                snapshot=snapshot,
                message=(
                    f"Previous child PID {child_pid} is absent, but absence is "
                    "not tree-stop proof. Inspect it and its descendants, then "
                    "rerun with --confirm-child-stopped. OMP did not kill it."
                ),
            )
        normalized = dict(state)
        normalized["child_pid"] = None
        validation_error: OperationError | None = None
        try:
            updated = validate_state(
                cast(JsonValue, normalized), oid_length=oid_length
            )
        except OperationError as exc:
            validation_error = exc
        if validation_error is not None:
            _audit_refusal(
                root,
                action="recover",
                reason_code="recovered_state_invalid",
                snapshot=snapshot,
                message=str(validation_error),
            )
        save_error: OSError | None = None
        try:
            save(checkpoint, updated)
        except OSError as exc:
            save_error = exc
        if save_error is not None:
            _audit_error_or_raise(
                root,
                action="recover",
                reason_code="checkpoint_save_failed",
                snapshot=snapshot,
                original=save_error,
                message=(
                    f"Cannot save recovered checkpoint: {type(save_error).__name__}"
                ),
                child_pid_after=child_pid,
            )
        digest_error: OperationError | None = None
        try:
            after_digest = _read_state_digest(checkpoint)
        except OperationError as exc:
            digest_error = exc
        if digest_error is not None:
            _audit_error_or_raise(
                root,
                action="recover",
                reason_code="checkpoint_digest_failed",
                snapshot=snapshot,
                original=digest_error,
                message=(
                    "Checkpoint was saved but its digest could not be read; "
                    "inspect state before resuming"
                ),
                child_pid_after=None,
                checkpoint_changed=True,
                after_digest="unknown",
            )
        _write_audit_event(
            root,
            action="recover",
            result="success",
            reason_code="child_pid_absent_operator_attested",
            state=state,
            before_digest=snapshot.digest,
            after_digest=after_digest,
            checkpoint_changed=True,
            child_pid_after=None,
            operator_confirmed_child_stopped=True,
            containment_proof="operator_attestation_only",
        )
        print(
            f"Recovered run {run_id}: cleared child_pid after operator "
            "attestation; resume will revalidate evidence"
        )


def abort(root: Path, run_id: str, state_sha256: str) -> None:
    """Mark an unfinished run as aborted without deleting evidence.

    Side effects:
        Writes an exact checkpoint archive and an atomic abort marker, then an
        audit event. The live checkpoint, clone, logs and provenance are kept.
        Never kills a process, clears child_pid, runs a Worker, or claims that a
        process tree stopped.

    Raises:
        InputError: invalid arguments or pre-lock checkpoint mismatch.
        OperationError: contention, refusal, or archive/marker/audit failure.
    """
    expected_digest = _validate_recovery_request(run_id, state_sha256)
    oid_length, _ = _preflight_recovery(root, run_id, expected_digest)
    with lock(root / ".omp/lock"):
        snapshot = _revalidate_recovery(
            root,
            action="abort",
            run_id=run_id,
            expected_digest=expected_digest,
            oid_length=oid_length,
        )
        marker_read_error: OperationError | None = None
        try:
            marker = _read_abort_marker(root, run_id)
        except OperationError as exc:
            marker_read_error = exc
        if marker_read_error is not None:
            _audit_refusal(
                root,
                action="abort",
                reason_code="abort_marker_invalid",
                snapshot=snapshot,
                message=str(marker_read_error),
            )
        state = snapshot.state
        if marker is not None:
            if marker["state_sha256"].lower() != snapshot.digest:
                _audit_refusal(
                    root,
                    action="abort",
                    reason_code="abort_marker_digest_mismatch",
                    snapshot=snapshot,
                    message=(
                        "Abort marker does not match checkpoint bytes; "
                        "retain run for inspection"
                    ),
                    abort_marker=_abort_marker_relative(run_id),
                )
            archive = _aborted_state_path(root, run_id)
            archive_restore_error: OSError | None = None
            try:
                if archive.exists():
                    if archive.read_bytes() != snapshot.raw:
                        _audit_refusal(
                            root,
                            action="abort",
                            reason_code="aborted_state_conflict",
                            snapshot=snapshot,
                            message=(
                                "Existing aborted-state archive does not match "
                                "the current checkpoint; retain both for inspection"
                            ),
                            abort_marker=_abort_marker_relative(run_id),
                        )
                else:
                    _write_bytes_atomic(archive, snapshot.raw)
            except OSError as exc:
                archive_restore_error = exc
            if archive_restore_error is not None:
                _audit_error_or_raise(
                    root,
                    action="abort",
                    reason_code="aborted_state_save_failed",
                    snapshot=snapshot,
                    original=archive_restore_error,
                    message=(
                        f"Cannot restore aborted checkpoint archive: "
                        f"{type(archive_restore_error).__name__}"
                    ),
                    child_pid_after=state.get("child_pid"),
                    abort_marker=_abort_marker_relative(run_id),
                )
            _write_audit_event(
                root,
                action="abort",
                result="no_change",
                reason_code="already_aborted",
                state=state,
                before_digest=snapshot.digest,
                after_digest=snapshot.digest,
                checkpoint_changed=False,
                child_pid_after=state.get("child_pid"),
                abort_marker=_abort_marker_relative(run_id),
            )
            print(f"Run {run_id} is already aborted; no change")
            return
        if state.get("child_pid") is not None:
            _audit_refusal(
                root,
                action="abort",
                reason_code="child_pid_still_recorded",
                snapshot=snapshot,
                message=(
                    "Run still records a child_pid; resolve only after external "
                    "inspection with recover --confirm-child-stopped, then abort. "
                    "OMP did not kill or stop it."
                ),
            )
        if state["phase"] == "complete":
            _write_audit_event(
                root,
                action="abort",
                result="no_change",
                reason_code="already_complete",
                state=state,
                before_digest=snapshot.digest,
                after_digest=snapshot.digest,
                checkpoint_changed=False,
                child_pid_after=state.get("child_pid"),
            )
            print(f"Run {run_id} is already complete; nothing to abort")
            return
        archive = _aborted_state_path(root, run_id)
        marker_path = _abort_marker_path(root, run_id)
        archive_save_error: OSError | None = None
        try:
            if archive.exists():
                if archive.read_bytes() != snapshot.raw:
                    _audit_refusal(
                        root,
                        action="abort",
                        reason_code="aborted_state_conflict",
                        snapshot=snapshot,
                        message=(
                            "Existing aborted-state archive does not match the "
                            "current checkpoint; retain both for inspection"
                        ),
                    )
            else:
                _write_bytes_atomic(archive, snapshot.raw)
        except OSError as exc:
            archive_save_error = exc
        if archive_save_error is not None:
            _audit_error_or_raise(
                root,
                action="abort",
                reason_code="aborted_state_save_failed",
                snapshot=snapshot,
                original=archive_save_error,
                message=(
                    f"Cannot archive checkpoint before abort: "
                    f"{type(archive_save_error).__name__}"
                ),
                child_pid_after=state.get("child_pid"),
            )
        marker_event: JsonObject = {
            "schema_version": ABORT_MARKER_SCHEMA_VERSION,
            "action": "abort",
            "run_id": run_id,
            "state_sha256": snapshot.digest,
            "child_pid": state.get("child_pid"),
            "created_utc": _utc_timestamp(),
        }
        marker_save_error: OSError | None = None
        try:
            save(marker_path, marker_event)
        except OSError as exc:
            marker_save_error = exc
        if marker_save_error is not None:
            _audit_error_or_raise(
                root,
                action="abort",
                reason_code="abort_marker_save_failed",
                snapshot=snapshot,
                original=marker_save_error,
                message=f"Cannot persist abort marker: {type(marker_save_error).__name__}",
                child_pid_after=state.get("child_pid"),
                abort_marker=_abort_marker_relative(run_id),
            )
        _write_audit_event(
            root,
            action="abort",
            result="success",
            reason_code="aborted",
            state=state,
            before_digest=snapshot.digest,
            after_digest=snapshot.digest,
            checkpoint_changed=False,
            child_pid_after=state.get("child_pid"),
            abort_marker=_abort_marker_relative(run_id),
        )
        print(
            f"Aborted run {run_id}; checkpoint, clone, logs and provenance "
            "were retained"
        )


def main() -> None:
    """Run the ``omp`` command line interface.

    Expected input and operation failures print ``OMP: <message>`` to stderr
    and exit 1 without a traceback. Successful commands return normally.
    ``run``, ``resume``, ``recover``, and ``abort`` validate authoritative data
    before the lock and validate it again after acquiring the lock before any
    mutation. ``recover`` never runs a Worker; ``abort`` only writes sidecars.

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
    recover_parser = sub.add_parser("recover")
    recover_parser.add_argument("run_id")
    recover_parser.add_argument("--state-sha256", required=True)
    recover_parser.add_argument("--confirm-child-stopped", action="store_true")
    abort_parser = sub.add_parser("abort")
    abort_parser.add_argument("run_id")
    abort_parser.add_argument("--state-sha256", required=True)
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
        if args.command == "recover":
            recover(
                root,
                args.run_id,
                args.state_sha256,
                args.confirm_child_stopped,
            )
            return
        if args.command == "abort":
            abort(root, args.run_id, args.state_sha256)
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
        _check_not_aborted(root, state)
        if state["schema_version"] == 1 and state["phase"] != "complete":
            raise OperationError(
                "Cannot resume run with schema_version 1; retain run/checkout and start a new run"
            )
        with lock(root / ".omp/lock"):
            state = _read_state(root, oid_length)
            _check_not_aborted(root, state)
            if state["schema_version"] == 1 and state["phase"] != "complete":
                raise OperationError(
                    "Cannot resume run with schema_version 1; retain run/checkout and start a new run"
                )
            advance(root)
    except (OmpError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"OMP: {exc}\n")


if __name__ == "__main__":
    main()
