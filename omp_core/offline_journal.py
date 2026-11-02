"""Fixture-only Oracle and append-only journal; no execution or restart authority.

The trusted harness supplies an exclusive directory outside Worker access.
Records are observations, not live resource ownership or an OS security proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
from typing import Any, BinaryIO, Mapping, NamedTuple, NoReturn

from .trusted_layout import TrustedManifest

EXPECTATION_SCHEMA_VERSION = "omp.sec02.b1c.expectation.v1"
JOURNAL_SCHEMA_VERSION = "omp.sec02.b1c.journal.v1"
MAX_RECORD_SIZE_BYTES = 65536
_MAX_INT = 2**31 - 1
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_HEX = {n: re.compile(rf"[0-9a-f]{{{n}}}") for n in (32, 40, 64)}
_FILENAME = re.compile(r"([0-9]{8,10})\.json")
EXPECTATION_FIELDS = frozenset({"schema_version", "task_id", "tests_run", "output_sha256"})
RESOURCES_FIELDS = frozenset({"profile_name", "profile_sid", "child_pid"})
_BOOL_FIELDS = ("tree_stopped", "oracle_pass", "process_closed", "profile_closed")
OBSERVATIONS_FIELDS = frozenset({"exit_code", *_BOOL_FIELDS})
_IDENTITY_FIELDS = ("run_id", "task_id", "nonce", "manifest_sha256",
                    "expectation_sha256", "tree_algorithm", "candidate_tree",
                    "candidate_sha256")
JOURNAL_RECORD_FIELDS = frozenset({
    "schema_version", "revision", "previous_sha256", *_IDENTITY_FIELDS,
    "phase", "resources", "observations", "reason_code",
})
VALID_PHASE_TRANSITIONS = {
    "preparing": frozenset({"preparing", "worker", "cleaning", "blocked"}),
    "worker": frozenset({"worker", "verifying", "cleaning", "blocked"}),
    "verifying": frozenset({"verifying", "cleaning", "blocked"}),
    "cleaning": frozenset({"cleaning", "complete", "blocked"}),
    "complete": frozenset(),
    "blocked": frozenset(),
}
PHASES = frozenset(VALID_PHASE_TRANSITIONS)
VALID_REASONS = frozenset({
    "allocation_failed", "journal_failed", "start_failed", "resume_failed",
    "worker_failed", "stop_unknown", "reader_failed", "oracle_mismatch",
    "process_close_failed", "profile_close_failed", "interrupted",
})


class OfflineJournalError(OSError):
    """Journal boundary failure; messages never include supplied data."""


class OfflineJournalSecurityError(OfflineJournalError):
    """Invalid schema, identity, transition, or fixture expectation."""


class OfflineJournalCorruptionError(OfflineJournalSecurityError):
    """Malformed, inconsistent, or changed on-disk history."""


class OfflineJournalLockError(OfflineJournalError):
    """Native lock acquisition or release failed."""


def _sanitize_exception(exc: BaseException) -> BaseException:
    """Redact arbitrary I/O diagnostics while preserving exception types/links."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.args = ("[REDACTED]",)
        if isinstance(current, OSError):
            current.strerror = "[REDACTED]"
            current.filename = None
            current.filename2 = None
        if isinstance(current, json.JSONDecodeError):
            current.doc = ""
            current.msg = "Invalid JSON"
            current.pos, current.lineno, current.colno = 0, 1, 1
        if isinstance(current, UnicodeError):
            current.object = b"" if isinstance(current, UnicodeDecodeError) else ""
            current.start = current.end = 0
            current.reason = "Invalid text"
        if isinstance(current, SystemExit):
            if not isinstance(current.code, (int, type(None))):
                current.code = "[REDACTED]"
        if hasattr(current, "__notes__"):
            safe_notes = {"Journal temporary cleanup also failed",
                          "Journal lock cleanup also failed",
                          "Journal lock close also failed"}
            current.__notes__ = [note if note in safe_notes else "[REDACTED]"
                                 for note in current.__notes__]
        pending.extend(e for e in (current.__cause__, current.__context__)
                       if e is not None)
    return exc


def _reraise(exc: BaseException, message: str,
             error: type[OfflineJournalError] = OfflineJournalError) -> NoReturn:
    if isinstance(exc, OfflineJournalError):
        for linked in (exc.__cause__, exc.__context__):
            if linked is not None:
                _sanitize_exception(linked)
        raise exc
    _sanitize_exception(exc)
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        raise exc
    raise error(message) from exc


def _integer(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _hex(value: object, size: int) -> bool:
    return isinstance(value, str) and _HEX[size].fullmatch(value) is not None


def _closed(value: object, fields: frozenset[str]) -> bool:
    return isinstance(value, Mapping) and set(value) == fields


def canonical_json_bytes(data: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON; reject invalid/oversized values without I/O."""
    try:
        raw = json.dumps(dict(data), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        _reraise(exc, "Invalid canonical JSON", OfflineJournalSecurityError)
    if len(raw) > MAX_RECORD_SIZE_BYTES:
        raise OfflineJournalSecurityError("Record exceeds size limit")
    return raw


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OfflineJournalCorruptionError("Duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value: str) -> NoReturn:
    raise OfflineJournalCorruptionError("Nonfinite JSON number")


def parse_bounded_record_json(raw_bytes: bytes) -> dict[str, Any]:
    """Parse a bounded strict object, rejecting duplicate keys and deep nesting."""
    if not isinstance(raw_bytes, bytes) or len(raw_bytes) > MAX_RECORD_SIZE_BYTES:
        raise OfflineJournalCorruptionError("Invalid record size or type")
    try:
        record = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        _reraise(exc, "Invalid record JSON", OfflineJournalCorruptionError)
    if not isinstance(record, dict):
        raise OfflineJournalCorruptionError("Record must be an object")
    pending = [(record, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > 3:
            raise OfflineJournalCorruptionError("Record nesting exceeds limit")
        values = value.values() if isinstance(value, dict) else value
        pending.extend((child, depth + 1) for child in values
                       if isinstance(child, (dict, list)))
    return record


@dataclass(frozen=True, slots=True, kw_only=True)
class FixtureExpectation:
    """Immutable trusted fixture input, approved before launching a Worker."""

    task_id: str
    tests_run: int
    output_sha256: str
    schema_version: str = EXPECTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (self.schema_version != EXPECTATION_SCHEMA_VERSION
                or not isinstance(self.task_id, str)
                or _ID.fullmatch(self.task_id) is None
                or not _integer(self.tests_run, 1, _MAX_INT)
                or not _hex(self.output_sha256, 64)):
            raise OfflineJournalSecurityError("Invalid fixture expectation")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached closed-schema mapping."""
        return {name: getattr(self, name) for name in EXPECTATION_FIELDS}

    def canonical_bytes(self) -> bytes:
        """Return exact bytes whose digest binds the approved expectation."""
        return canonical_json_bytes(self.to_dict())

    @property
    def digest(self) -> str:
        """Return the SHA-256 of canonical approved input."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FixtureExpectation:
        """Validate a closed mapping; unknown fields never appear in errors."""
        if not _closed(data, EXPECTATION_FIELDS):
            raise OfflineJournalSecurityError("Invalid expectation fields")
        return cls(**data)


def verify_artifact(artifact: Mapping[str, Any], *, expectation: FixtureExpectation,
                    manifest: TrustedManifest) -> None:
    """Compare a B1b-reader artifact to approved fixture input; never OS proof.

    Raises OfflineJournalSecurityError for any failed binding or comparison.
    This function neither runs tests nor accepts journal observations as authority.
    """
    if (not isinstance(expectation, FixtureExpectation)
            or not isinstance(manifest, TrustedManifest)):
        raise OfflineJournalSecurityError("Invalid approved input types")
    if (expectation.task_id != manifest.task_id
            or expectation.digest != manifest.spec_hash):
        raise OfflineJournalSecurityError("Expectation binding mismatch")
    required = {"task_id", "run_id", "nonce", "status", "output",
                "tests_run", "failures", "errors"}
    if not isinstance(artifact, Mapping) or not required <= set(artifact):
        raise OfflineJournalSecurityError("Invalid fixture artifact fields")
    if any(artifact[key] != getattr(manifest, key)
           for key in ("task_id", "run_id", "nonce")):
        raise OfflineJournalSecurityError("Artifact identity mismatch")
    if (artifact["status"] != "PASS"
            or not _integer(artifact["tests_run"], 1, _MAX_INT)
            or artifact["tests_run"] != expectation.tests_run
            or any(type(artifact[key]) is not int or artifact[key] != 0
                   for key in ("failures", "errors"))
            or not isinstance(artifact["output"], str)):
        raise OfflineJournalSecurityError("Fixture result mismatch")
    try:
        actual = hashlib.sha256(artifact["output"].encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        _reraise(exc, "Invalid artifact text", OfflineJournalSecurityError)
    if actual != expectation.output_sha256:
        raise OfflineJournalSecurityError("Fixture output mismatch")


def _check_chain_link(previous_record: Mapping[str, Any],
                      record: Mapping[str, Any], previous_sha256: str | None) -> None:
    if record["revision"] != previous_record["revision"] + 1:
        raise OfflineJournalCorruptionError("Revision sequence mismatch")
    if previous_sha256 is None or record["previous_sha256"] != previous_sha256:
        raise OfflineJournalCorruptionError("Previous record digest mismatch")


def validate_snapshot_record(record: Mapping[str, Any], *,
                             expected_revision: int | None = None,
                             previous_record: Mapping[str, Any] | None = None,
                             previous_sha256: str | None = None) -> None:
    """Validate schema and, with a validated predecessor, history invariants."""
    if not _closed(record, JOURNAL_RECORD_FIELDS):
        raise OfflineJournalSecurityError("Invalid record fields")
    rev, phase = record["revision"], record["phase"]
    res, obs, reason = record["resources"], record["observations"], record["reason_code"]
    if (record["schema_version"] != JOURNAL_SCHEMA_VERSION
            or not _integer(rev, 0, _MAX_INT)
            or (expected_revision is not None and rev != expected_revision)
            or not isinstance(phase, str) or phase not in PHASES
            or not _closed(res, RESOURCES_FIELDS)
            or not _closed(obs, OBSERVATIONS_FIELDS)
            or (reason is not None and
                (not isinstance(reason, str) or reason not in VALID_REASONS))):
        raise OfflineJournalSecurityError("Invalid record schema")
    if any(not isinstance(record[key], str) or _ID.fullmatch(record[key]) is None
           for key in ("run_id", "task_id")):
        raise OfflineJournalSecurityError("Invalid record identity")
    if (not _hex(record["nonce"], 32)
            or any(not _hex(record[key], 64)
                   for key in ("manifest_sha256", "expectation_sha256", "candidate_sha256"))
            or record["tree_algorithm"] not in ("sha1", "sha256")
            or not _hex(record["candidate_tree"],
                        40 if record["tree_algorithm"] == "sha1" else 64)):
        raise OfflineJournalSecurityError("Invalid record digest or algorithm")
    if ((rev == 0 and record["previous_sha256"] is not None)
            or (rev > 0 and not _hex(record["previous_sha256"], 64))):
        raise OfflineJournalSecurityError("Invalid predecessor digest")
    pid, exit_code = res["child_pid"], obs["exit_code"]
    if (any(res[key] is not None and not isinstance(res[key], str)
            for key in ("profile_name", "profile_sid"))
            or (pid is not None and (type(pid) is not int or pid <= 0))
            or (exit_code is not None and not _integer(exit_code, -(2**31), _MAX_INT))
            or any(obs[key] is not None and type(obs[key]) is not bool
                   for key in _BOOL_FIELDS)):
        raise OfflineJournalSecurityError("Invalid resource or observation type")
    if rev == 0 and (phase != "preparing" or reason is not None
                     or any(value is not None for value in (*res.values(), *obs.values()))):
        raise OfflineJournalSecurityError("Invalid initial snapshot")
    if phase == "preparing" and (pid is not None or any(v is not None for v in obs.values())):
        raise OfflineJournalSecurityError("Preparing snapshot contains process observations")
    if phase == "blocked" and reason is None:
        raise OfflineJournalSecurityError("Blocked snapshot requires a reason")
    if phase in ("verifying", "complete") and (
            pid is not None or exit_code != 0 or obs["tree_stopped"] is not True):
        raise OfflineJournalSecurityError("Verification requires a stopped tree")
    if phase == "complete" and (
            reason is not None or not all(obs[key] is True for key in _BOOL_FIELDS)):
        raise OfflineJournalSecurityError("Incomplete completion observations")
    if previous_record is None:
        return
    _check_chain_link(previous_record, record, previous_sha256)
    if any(record[key] != previous_record[key] for key in _IDENTITY_FIELDS):
        raise OfflineJournalSecurityError("Record identity changed")
    prev_phase = previous_record["phase"]
    if phase not in VALID_PHASE_TRANSITIONS[prev_phase]:
        raise OfflineJournalSecurityError("Invalid phase transition")
    prev_res, prev_obs = previous_record["resources"], previous_record["observations"]
    if any(prev_res[key] is not None and res[key] != prev_res[key]
           for key in ("profile_name", "profile_sid")):
        raise OfflineJournalSecurityError("Profile inventory changed")
    prev_pid = prev_res["child_pid"]
    if ((prev_pid is None and pid is not None
         and (phase != "worker" or prev_obs["tree_stopped"] is True))
            or (prev_pid is not None and pid is not None and pid != prev_pid)
            or (prev_pid is not None and pid is None and obs["tree_stopped"] is not True)):
        raise OfflineJournalSecurityError("Invalid PID transition")
    if (any(prev_obs[key] is True and obs[key] is not True for key in _BOOL_FIELDS)
            or (prev_obs["exit_code"] is not None and exit_code != prev_obs["exit_code"])):
        raise OfflineJournalSecurityError("Observation regressed")
    if previous_record["reason_code"] is not None and reason != previous_record["reason_code"]:
        raise OfflineJournalSecurityError("Failure reason changed")
    if phase == "cleaning" and reason is None and (
            prev_phase not in ("verifying", "cleaning")
            or prev_obs["oracle_pass"] is not True or obs["oracle_pass"] is not True
            or pid is not None or exit_code != 0 or obs["tree_stopped"] is not True):
        raise OfflineJournalSecurityError("Successful cleanup requires verified result")


class JournalInspection(NamedTuple):
    """Detached historical data only; contains no live owner or resume authority."""

    head_revision: int
    head_sha256: str
    snapshots: tuple[dict[str, Any], ...]
    is_valid: bool = True


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECORD_SIZE_BYTES + 1)
    except OSError as exc:
        _reraise(exc, "Cannot read journal record")
    if len(raw) > MAX_RECORD_SIZE_BYTES:
        raise OfflineJournalCorruptionError("Record exceeds size limit")
    return raw


def inspect_journal(directory: Path) -> JournalInspection:
    """Read a consistent prefix without creating files, locks, or live owners.

    Malformed history and I/O failures raise OfflineJournalError subclasses.
    Concurrent append may yield an older prefix; inspection never authorizes work.
    """
    try:
        files = []
        for item in directory.iterdir():
            if not item.name.endswith(".json"):
                continue
            match = _FILENAME.fullmatch(item.name)
            if match is None:
                raise OfflineJournalCorruptionError("Invalid revision filename")
            revision = int(match[1])
            if revision > _MAX_INT or item.name != f"{revision:08d}.json" or not item.is_file():
                raise OfflineJournalCorruptionError("Invalid revision filename")
            files.append((revision, item))
        files.sort()
        if not files:
            raise OfflineJournalCorruptionError("Missing initial record")
        snapshots = []
        digest = None
        previous = None
        for expected, (revision, path) in enumerate(files):
            if revision != expected:
                raise OfflineJournalCorruptionError("Revision sequence gap")
            raw = _read_bounded(path)
            record = parse_bounded_record_json(raw)
            validate_snapshot_record(record, expected_revision=expected,
                                     previous_record=previous, previous_sha256=digest)
            snapshots.append(record)
            previous = record
            digest = hashlib.sha256(raw).hexdigest()
        return JournalInspection(files[-1][0], digest, tuple(snapshots))
    except (OSError, ValueError, RecursionError) as exc:
        _reraise(exc, "Cannot inspect journal")


def _publish(directory: Path, revision: int, raw: bytes) -> Path:
    target = directory / f"{revision:08d}.json"
    temp = directory / f".journal-{secrets.token_hex(16)}.tmp"
    fd: int | None = None
    stream: BinaryIO | None = None
    owned = False
    primary: BaseException | None = None
    cleanup: BaseException | None = None
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_BINARY, 0o600)
        owned = True
        stream = os.fdopen(fd, "wb")
        fd = None
        if stream.write(raw) != len(raw):
            raise OSError("Short journal write")
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        stream = None
        try:
            os.rename(temp, target)  # Windows rename never replaces an existing file.
        except FileExistsError:
            if _read_bounded(target) != raw:
                raise OfflineJournalSecurityError("Conflicting revision publication")
    except BaseException as exc:
        primary = exc
    operations = []
    if stream is not None:
        operations.append((stream.close, False))
    if fd is not None:
        operations.append((lambda: os.close(fd), False))
    if owned:
        operations.append((temp.unlink, True))
    for operation, missing_ok in operations:
        try:
            operation()
        except FileNotFoundError as exc:
            if not missing_ok:
                cleanup = cleanup or exc
        except BaseException as exc:
            cleanup = cleanup or exc
    if primary is not None:
        if cleanup is not None:
            primary.add_note("Journal temporary cleanup also failed")
        _reraise(primary, "Journal publication failed")
    if cleanup is not None:
        _reraise(cleanup, "Journal temporary cleanup failed")
    return target


def _release_lock(stream: BinaryIO, held: bool) -> tuple[BaseException | None, bool]:
    primary = None
    if held and not stream.closed:
        try:
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            held = False
        except BaseException as exc:
            primary = exc
    try:
        stream.close()
        if not stream.closed:
            raise OSError("Journal lock stream did not close")
    except BaseException as exc:
        if primary is None:
            primary = exc
        else:
            primary.add_note("Journal lock close also failed")
    if stream.closed:
        held = False
    return primary, held


class OfflineJournal:
    """Live fixture owner created by create_journal, never restored from inspection."""

    def __init__(self, directory: Path, initial_record: Mapping[str, Any],
                 lock_stream: BinaryIO) -> None:
        self._directory = directory
        self._lock_stream: BinaryIO | None = lock_stream
        self._lock_held = True
        self._closed = False
        self._head_revision = 0
        self._head_record = parse_bounded_record_json(canonical_json_bytes(initial_record))
        self._head_sha256 = hashlib.sha256(canonical_json_bytes(initial_record)).hexdigest()

    @property
    def directory(self) -> Path:
        """Return the borrowed control directory; close never deletes it."""
        return self._directory

    @property
    def head_revision(self) -> int:
        """Return the last successfully published revision."""
        return self._head_revision

    @property
    def head_sha256(self) -> str:
        """Return the last successfully published exact-byte digest."""
        return self._head_sha256

    @property
    def head_file_path(self) -> Path:
        """Return the last published immutable record path."""
        return self._directory / f"{self._head_revision:08d}.json"

    @property
    def lock_file_path(self) -> Path:
        """Return the lock file for fixture diagnostics, never resume authority."""
        return self._directory / ".journal.lock"

    @property
    def closed(self) -> bool:
        """Return whether append is disabled, including after failed cleanup."""
        return self._closed

    def __enter__(self) -> OfflineJournal:
        if self._closed:
            raise OfflineJournalError("Journal owner is closed")
        return self

    def __exit__(self, exc_type: object, exc_val: BaseException | None,
                 exc_tb: object) -> None:
        if exc_val is not None:
            _sanitize_exception(exc_val)
        try:
            self.close()
        except BaseException as cleanup:
            if exc_val is None:
                raise
            _sanitize_exception(cleanup)
            exc_val.add_note("Journal lock cleanup also failed")

    def close(self) -> None:
        """Disable append and release the lock; report cleanup errors, retain failed handles."""
        self._closed = True
        if self._lock_stream is None:
            return
        failure, self._lock_held = _release_lock(self._lock_stream, self._lock_held)
        if self._lock_stream.closed:
            self._lock_stream = None
        if failure is not None:
            _reraise(failure, "Journal lock cleanup failed", OfflineJournalLockError)

    def append_snapshot(self, record: Mapping[str, Any]) -> Path:
        """Validate/revalidate and publish a revision; never spawn or resume anything."""
        if self._closed:
            raise OfflineJournalError("Journal owner is closed")
        validate_snapshot_record(record)
        raw = canonical_json_bytes(record)
        record = parse_bounded_record_json(raw)
        validate_snapshot_record(record)
        inspection = inspect_journal(self._directory)
        if (inspection.head_revision != self._head_revision
                or inspection.head_sha256 != self._head_sha256):
            raise OfflineJournalCorruptionError("Journal head changed outside live owner")
        if record["revision"] == self._head_revision:
            if raw == _read_bounded(self.head_file_path):
                return self.head_file_path
            raise OfflineJournalSecurityError("Conflicting revision retry")
        validate_snapshot_record(record, expected_revision=self._head_revision + 1,
                                 previous_record=self._head_record,
                                 previous_sha256=self._head_sha256)
        path = _publish(self._directory, record["revision"], raw)
        self._head_record = parse_bounded_record_json(raw)
        self._head_revision = record["revision"]
        self._head_sha256 = hashlib.sha256(raw).hexdigest()
        return path


def create_journal(directory: Path, initial: Mapping[str, object]) -> OfflineJournal:
    """Create a fresh Windows fixture directory/lock and publish revision zero.

    Never opens an existing journal as a live owner or deletes failed evidence.
    Invalid data is rejected before I/O; allocation/publication/cleanup errors
    raise sanitized boundary errors while interrupts retain their types.
    """
    if os.name != "nt" or not isinstance(directory, Path):
        raise OfflineJournalError("Journal creation requires a Windows fixture path")
    validate_snapshot_record(initial, expected_revision=0)
    raw = canonical_json_bytes(initial)
    initial = parse_bounded_record_json(raw)
    validate_snapshot_record(initial, expected_revision=0)
    stream = None
    held = False
    try:
        directory.mkdir()
        stream = (directory / ".journal.lock").open("x+b")
        if stream.write(b"0") != 1:
            raise OSError("Short lock initialization write")
        stream.flush()
        stream.seek(0)
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        held = True
        _publish(directory, 0, raw)
        return OfflineJournal(directory, initial, stream)
    except BaseException as exc:
        if stream is not None:
            cleanup, _ = _release_lock(stream, held)
            if cleanup is not None:
                exc.add_note("Journal lock cleanup also failed")
        _reraise(exc, "Cannot create journal", OfflineJournalLockError)
