"""SEC-02-B1c-1: Tests for offline journal and fixture expectation.

Implements acceptance tests for J1-J6 incorporating reviews r1 and r2 (J2-R1 to J2-R6):
- J2-R1: Happy cleaning only after verifying/Oracle pass, PID allocation only in worker,
  complete requires PID null, and sticky first failure reason.
- J2-R2: Record size limits <= 64KiB, bounded inspect reading, canonical 8-digit filename
  rejection of leading-zero/decimal aliases, and RecursionError wrapping.
- J2-R3: Constant sanitized boundary errors protecting arbitrary opaque sentinels
  in message, cause, context, and tracebacks; typed validation before membership.
- J2-R4: Bounded fault injection across write, flush, fsync, rename, open, fdopen, lock,
  unlock, close, and unlink; preserving interrupts, head evidence, and foreign files.
- J2-R5: FixtureExpectation frozen dataclass, real TrustedManifest binding, surrogate error handling.
- J2-R6: Negative controls with executed mutants for chain link, phase transitions, and Oracle checks,
  verifying acceptance assertions fail when guards are disabled.
- J-R4: Bounded child publication inside os.rename hook for exact vs conflicting rival targets.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Mapping
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import omp_core.offline_journal as offline_journal
from omp_core.offline_journal import (
    EXPECTATION_SCHEMA_VERSION,
    JOURNAL_SCHEMA_VERSION,
    MAX_RECORD_SIZE_BYTES,
    VALID_PHASE_TRANSITIONS,
    FixtureExpectation,
    JournalInspection,
    OfflineJournal,
    OfflineJournalCorruptionError,
    OfflineJournalError,
    OfflineJournalLockError,
    OfflineJournalSecurityError,
    canonical_json_bytes,
    create_journal,
    inspect_journal,
    validate_snapshot_record,
    verify_artifact,
)
from omp_core.trusted_layout import (
    TrustedLayoutSecurityError,
    TrustedManifest,
    read_trusted_artifact,
)
from omp_core.windows_job import WindowsJobProcess


class _DummyJobProcess(WindowsJobProcess):
    """Mock process exposing WindowsJobProcess properties for offline reader testing."""

    def __init__(self, *, tree_stopped: bool = True, active_processes: int = 0) -> None:
        self._tree_stopped = tree_stopped
        self._active_processes = active_processes

    @property
    def tree_stopped(self) -> bool:
        return self._tree_stopped

    @property
    def active_processes(self) -> int:
        return self._active_processes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_record(
    revision: int = 0,
    *,
    phase: str = "preparing",
    previous_sha256: str | None = None,
    run_id: str = "run-001",
    task_id: str = "task-001",
    nonce: str = "0" * 32,
    manifest_sha256: str = "1" * 64,
    expectation_sha256: str = "2" * 64,
    tree_algorithm: str = "sha1",
    candidate_tree: str = "3" * 40,
    candidate_sha256: str = "4" * 64,
    profile_name: str | None = None,
    profile_sid: str | None = None,
    child_pid: int | None = None,
    exit_code: int | None = None,
    tree_stopped: bool | None = None,
    oracle_pass: bool | None = None,
    process_closed: bool | None = None,
    profile_closed: bool | None = None,
    reason_code: str | None = None,
    schema_version: str = JOURNAL_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Central fixture record builder (J-R6) providing deep-copied records."""
    return {
        "schema_version": schema_version,
        "revision": revision,
        "previous_sha256": previous_sha256,
        "run_id": run_id,
        "task_id": task_id,
        "nonce": nonce,
        "phase": phase,
        "manifest_sha256": manifest_sha256,
        "expectation_sha256": expectation_sha256,
        "tree_algorithm": tree_algorithm,
        "candidate_tree": candidate_tree,
        "candidate_sha256": candidate_sha256,
        "resources": {
            "profile_name": profile_name,
            "profile_sid": profile_sid,
            "child_pid": child_pid,
        },
        "observations": {
            "exit_code": exit_code,
            "tree_stopped": tree_stopped,
            "oracle_pass": oracle_pass,
            "process_closed": process_closed,
            "profile_closed": profile_closed,
        },
        "reason_code": reason_code,
    }


class TestFixtureExpectationAndOracle(unittest.TestCase):
    """J1, J2, J2-R5: Expectation dataclass, canonical serialization, Oracle with real TrustedManifest."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.checkout_dir = Path(self.tmp.name) / "checkout"
        self.checkout_dir.mkdir(parents=True)

        self.worker_code = b"print('worker fixture')\n"
        self.worker_sha256 = _sha256(self.worker_code)
        (self.checkout_dir / "worker.py").write_bytes(self.worker_code)

        self.output_content = "Ran 5 tests in 0.02s\nOK\n"
        self.output_sha256 = _sha256(self.output_content.encode("utf-8"))

        self.expectation = FixtureExpectation(
            task_id="task-b1c-01",
            tests_run=5,
            output_sha256=self.output_sha256,
        )

        self.manifest = TrustedManifest(
            task_id="task-b1c-01",
            run_id="run-b1c-01",
            nonce="11223344556677889900aabbccddeeff",
            verifier_version="1.0.0",
            candidate_bytes_sha256=self.worker_sha256,
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "worker.py"],
            timeout_seconds=30.0,
            spec_hash=self.expectation.digest,
            expected_artifact_relpath="result.json",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_expectation_frozen_dataclass_and_digest(self) -> None:
        """J2-R5: Frozen dataclass with canonical bytes and digest."""
        exp = FixtureExpectation(task_id="task-01", tests_run=3, output_sha256="c" * 64)
        self.assertEqual(exp.schema_version, EXPECTATION_SCHEMA_VERSION)
        self.assertEqual(exp.task_id, "task-01")
        self.assertEqual(exp.tests_run, 3)
        self.assertEqual(exp.output_sha256, "c" * 64)

        # Frozen immutability check
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            setattr(exp, "tests_run", 4)

        raw = exp.canonical_bytes()
        self.assertEqual(raw, json.dumps({
            "output_sha256": "c" * 64,
            "schema_version": EXPECTATION_SCHEMA_VERSION,
            "task_id": "task-01",
            "tests_run": 3,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        self.assertEqual(exp.digest, _sha256(raw))

        # from_dict and validation
        exp2 = FixtureExpectation.from_dict({
            "schema_version": EXPECTATION_SCHEMA_VERSION,
            "task_id": "task-01",
            "tests_run": 3,
            "output_sha256": "c" * 64,
        })
        self.assertEqual(exp, exp2)

    def test_direct_b1b_reader_consumer_pass_and_refuse_wrong_shape(self) -> None:
        """J-R1: Direct consumer with real read_trusted_artifact and verify_artifact."""
        good_artifact_data = {
            "task_id": "task-b1c-01",
            "run_id": "run-b1c-01",
            "nonce": "11223344556677889900aabbccddeeff",
            "status": "PASS",
            "output": self.output_content,
            "tests_run": 5,
            "failures": 0,
            "errors": 0,
        }
        art_path = self.checkout_dir / "result.json"
        art_path.write_text(json.dumps(good_artifact_data), encoding="utf-8")

        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        read_art = read_trusted_artifact(
            self.checkout_dir,
            "result.json",
            job_process=job,
            manifest=self.manifest,
            candidate_relpath="worker.py",
            actual_argv=self.manifest.approved_argv,
            actual_timeout=self.manifest.timeout_seconds,
            actual_tree_oid=self.manifest.canonical_tree_oid,
            actual_spec_hash=self.manifest.spec_hash,
        )
        # Good path passes Oracle
        verify_artifact(read_art, expectation=self.expectation, manifest=self.manifest)

        # Wrong output with valid shape accepted by reader but refused by Oracle
        bad_out_data = dict(good_artifact_data, output="Different output\n")
        art_path.write_text(json.dumps(bad_out_data), encoding="utf-8")
        read_bad_out = read_trusted_artifact(
            self.checkout_dir,
            "result.json",
            job_process=job,
            manifest=self.manifest,
            candidate_relpath="worker.py",
            actual_argv=self.manifest.approved_argv,
            actual_timeout=self.manifest.timeout_seconds,
            actual_tree_oid=self.manifest.canonical_tree_oid,
            actual_spec_hash=self.manifest.spec_hash,
        )
        with self.assertRaises(OfflineJournalSecurityError):
            verify_artifact(read_bad_out, expectation=self.expectation, manifest=self.manifest)

        # Wrong count with valid shape accepted by reader but refused by Oracle
        bad_count_data = dict(good_artifact_data, tests_run=4)
        art_path.write_text(json.dumps(bad_count_data), encoding="utf-8")
        read_bad_count = read_trusted_artifact(
            self.checkout_dir,
            "result.json",
            job_process=job,
            manifest=self.manifest,
            candidate_relpath="worker.py",
            actual_argv=self.manifest.approved_argv,
            actual_timeout=self.manifest.timeout_seconds,
            actual_tree_oid=self.manifest.canonical_tree_oid,
            actual_spec_hash=self.manifest.spec_hash,
        )
        with self.assertRaises(OfflineJournalSecurityError):
            verify_artifact(read_bad_count, expectation=self.expectation, manifest=self.manifest)

    def test_oracle_manifest_binding_and_type_guards(self) -> None:
        """J2-R5: Manifest binding and strict typing before membership."""
        valid_art = {
            "task_id": "task-b1c-01",
            "run_id": "run-b1c-01",
            "nonce": "11223344556677889900aabbccddeeff",
            "status": "PASS",
            "output": self.output_content,
            "tests_run": 5,
            "failures": 0,
            "errors": 0,
        }

        # Manifest type guard
        with self.assertRaises(OfflineJournalSecurityError):
            verify_artifact(valid_art, expectation=self.expectation, manifest="not-a-manifest")

        # Expectation type guard
        with self.assertRaises(OfflineJournalSecurityError):
            verify_artifact(valid_art, expectation="not-an-expectation", manifest=self.manifest)  # type: ignore

        # Artifact type guard
        with self.assertRaises(OfflineJournalSecurityError):
            verify_artifact(["not", "a", "mapping"], expectation=self.expectation, manifest=self.manifest)  # type: ignore


    def test_oracle_closed_input_and_strict_result_negatives(self) -> None:
        good = dict(task_id=self.manifest.task_id, run_id=self.manifest.run_id,
                    nonce=self.manifest.nonce, status="PASS", output=self.output_content,
                    tests_run=5, failures=0, errors=0)
        variants = []
        for key in good:
            bad = dict(good)
            del bad[key]
            variants.append(bad)
        for key, value in (("run_id", "wrong"), ("nonce", "f" * 32),
                           ("task_id", "wrong"), ("status", "FAIL"),
                           ("tests_run", True), ("failures", False), ("errors", True),
                           ("errors", 1), ("output", 4), ("output", chr(0xD800))):
            variants.append(dict(good, **{key: value}))
        for index, bad in enumerate(variants):
            with self.subTest(case=index), self.assertRaises(OfflineJournalError):
                verify_artifact(bad, expectation=self.expectation, manifest=self.manifest)
        for expectation in (dataclasses.replace(self.expectation, task_id="other"),
                            dataclasses.replace(self.expectation, output_sha256="f" * 64)):
            with self.assertRaises(OfflineJournalError):
                verify_artifact(good, expectation=expectation, manifest=self.manifest)


class TestJournalDirectoryLockAndChildRival(unittest.TestCase):
    """J3, J4, J5, J2-R4: Directory creation, native byte lock, bounded child rival races."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.tmp.name) / "journal"
        self.initial_record = _make_record(0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_directory_creation_and_close_idempotent(self) -> None:
        with create_journal(self.journal_dir, self.initial_record) as journal:
            self.assertTrue(self.journal_dir.is_dir())
            self.assertEqual(journal.head_revision, 0)
        self.assertTrue(journal.closed)
        journal.close()  # Idempotent
        self.assertTrue(journal.closed)
        before = {p.name: p.read_bytes() for p in self.journal_dir.iterdir()}
        with self.assertRaises(OfflineJournalError):
            create_journal(self.journal_dir, self.initial_record)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.journal_dir.iterdir()})
        invalid_path = Path(self.tmp.name) / "invalid"
        with self.assertRaises(OfflineJournalError):
            create_journal(invalid_path, dict(self.initial_record, phase=[]))
        self.assertFalse(invalid_path.exists())
        with self.assertRaises(OfflineJournalError):
            journal.append_snapshot(self.initial_record)

    @unittest.skipUnless(sys.platform == "win32", "Native byte lock requires Windows")
    def test_native_windows_byte_lock_contention_and_release(self) -> None:
        """J-R4: Actual lock contention before and after close via child process."""
        test_script = str(Path(__file__).resolve())
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

        with create_journal(self.journal_dir, self.initial_record) as journal:
            lock_path = journal.lock_file_path
            # Child must be blocked while owner holds lock
            cmd = [sys.executable, test_script, "--child-lock", str(lock_path)]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("BUSY", proc.stdout)

        # After owner closed, child can acquire lock
        proc2 = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc2.returncode, 0)
        self.assertIn("LOCKED", proc2.stdout)

    def test_rival_publication_inside_rename_hook_exact_bytes(self) -> None:
        """J2-R4, J-R4: Rival child publishes exact bytes between temp write and os.rename (fresh fixture)."""
        fresh_dir = Path(self.tmp.name) / "j_exact"
        test_script = str(Path(__file__).resolve())
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

        with create_journal(fresh_dir, self.initial_record) as journal:
            rec1 = _make_record(1, phase="worker", previous_sha256=journal.head_sha256)
            canonical_rec1 = canonical_json_bytes(rec1)

            payload_file = Path(self.tmp.name) / "exact_payload.json"
            payload_file.write_bytes(canonical_rec1)

            orig_rename = os.rename

            def rival_rename_hook(src: str | Path, dst: str | Path) -> None:
                # Spawn child helper to write exact bytes to dst before rename
                cmd = [sys.executable, test_script, "--child-rival-create", str(dst), str(payload_file)]
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0)
                orig_rename(src, dst)

            with patch("os.rename", side_effect=rival_rename_hook):
                path = journal.append_snapshot(rec1)

            self.assertEqual(path, fresh_dir / "00000001.json")
            self.assertEqual(journal.head_revision, 1)
            self.assertEqual(journal.head_file_path.read_bytes(), canonical_rec1)

    def test_rival_publication_inside_rename_hook_conflicting_bytes(self) -> None:
        """J2-R4, J-R4: Rival child publishes conflicting bytes between temp write and os.rename (fresh fixture)."""
        fresh_dir = Path(self.tmp.name) / "j_conflict"
        test_script = str(Path(__file__).resolve())
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))

        with create_journal(fresh_dir, self.initial_record) as journal:
            rec1 = _make_record(1, phase="worker", previous_sha256=journal.head_sha256)

            conflicting_payload = Path(self.tmp.name) / "conflict_payload.json"
            conflicting_bytes = b'{"schema_version":"omp.sec02.b1c.journal.v1","revision":1,"conflict":true}'
            conflicting_payload.write_bytes(conflicting_bytes)

            orig_rename = os.rename

            def rival_rename_hook(src: str | Path, dst: str | Path) -> None:
                cmd = [sys.executable, test_script, "--child-rival-create", str(dst), str(conflicting_payload)]
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0)
                orig_rename(src, dst)

            with patch("os.rename", side_effect=rival_rename_hook):
                with self.assertRaises(OfflineJournalError):
                    journal.append_snapshot(rec1)

            # Target preserved with rival's exact conflicting bytes
            target = fresh_dir / "00000001.json"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), conflicting_bytes)
            # Head revision remains 0 in owner
            self.assertEqual(journal.head_revision, 0)


class TestJournalSchemaMatrixAndTransitions(unittest.TestCase):
    """J2, J3, J2-R1, J2-R2: Invariants, happy cleaning, PID allocation, size/filename limits."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.tmp.name) / "journal"
        self.init_rec = _make_record(0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_matrix_negatives(self) -> None:
        """J-R5, J2-R2: Table-driven matrix for schema validations and type guards."""
        cases: list[tuple[str, dict[str, Any], type[Exception]]] = [
            ("missing_schema_version", {k: v for k, v in self.init_rec.items() if k != "schema_version"}, OfflineJournalSecurityError),
            ("unknown_field", dict(self.init_rec, extra_field="bad"), OfflineJournalSecurityError),
            ("bool_for_revision", dict(self.init_rec, revision=True), OfflineJournalSecurityError),
            ("bool_for_pid", _make_record(0, child_pid=True), OfflineJournalSecurityError),
            ("int_for_tree_stopped", _make_record(0, tree_stopped=1), OfflineJournalSecurityError),
            ("bad_schema_version", dict(self.init_rec, schema_version="omp.other.v1"), OfflineJournalSecurityError),
            ("bad_tree_oid_sha1_len", dict(self.init_rec, tree_algorithm="sha1", candidate_tree="a" * 64), OfflineJournalSecurityError),
            ("bad_tree_oid_sha256_len", dict(self.init_rec, tree_algorithm="sha256", candidate_tree="a" * 40), OfflineJournalSecurityError),
            ("bad_nonce_len", dict(self.init_rec, nonce="0" * 31), OfflineJournalSecurityError),
            ("rev0_not_preparing", dict(self.init_rec, phase="worker"), OfflineJournalSecurityError),
            ("rev0_with_prev_hash", dict(self.init_rec, previous_sha256="0" * 64), OfflineJournalSecurityError),
            ("rev_exceeds_max_int", dict(self.init_rec, revision=2**31), OfflineJournalSecurityError),
        ]
        for name, rec, exc in cases:
            with self.subTest(name=name):
                with self.assertRaises(exc):
                    validate_snapshot_record(rec, expected_revision=rec.get("revision", 0))

    def test_j2_r1_happy_cleaning_pid_allocation_and_complete_invariants(self) -> None:
        """J2-R1: Happy cleaning only after verifying/Oracle, PID allocation only in worker, complete PID null."""
        with create_journal(self.journal_dir, self.init_rec) as journal:
            h0 = journal.head_sha256

            # J2-R1: preparing -> cleaning with reason_code null must be rejected!
            bad_prep_cleaning = _make_record(1, phase="cleaning", previous_sha256=h0, reason_code=None)
            with self.assertRaises(OfflineJournalSecurityError):
                journal.append_snapshot(bad_prep_cleaning)

            # J2-R1: PID allocation outside worker (in preparing) must be rejected!
            bad_alloc_prep = _make_record(1, phase="preparing", previous_sha256=h0, child_pid=555)
            with self.assertRaises(OfflineJournalSecurityError):
                journal.append_snapshot(bad_alloc_prep)

            # Valid: preparing -> worker with PID allocation
            r1 = _make_record(1, phase="worker", previous_sha256=h0, child_pid=1001)
            journal.append_snapshot(r1)
            h1 = journal.head_sha256

            # J2-R1: PID allocation in verifying phase must be rejected!
            bad_alloc_verifying = _make_record(
                2,
                phase="verifying",
                previous_sha256=h1,
                child_pid=1002,
                exit_code=0,
                tree_stopped=True,
            )
            with self.assertRaises(OfflineJournalSecurityError):
                journal.append_snapshot(bad_alloc_verifying)

            # Valid: worker -> verifying (PID cleared with tree_stopped=True, exit_code=0, oracle_pass=True)
            r2 = _make_record(
                2,
                phase="verifying",
                previous_sha256=h1,
                child_pid=None,
                exit_code=0,
                tree_stopped=True,
                oracle_pass=True,
            )
            journal.append_snapshot(r2)
            h2 = journal.head_sha256

            # Valid: verifying -> cleaning (happy path from verifying with oracle_pass=True)
            r3 = _make_record(
                3,
                phase="cleaning",
                previous_sha256=h2,
                exit_code=0,
                tree_stopped=True,
                oracle_pass=True,
                process_closed=True,
                profile_closed=True,
            )
            journal.append_snapshot(r3)
            h3 = journal.head_sha256

            # J2-R1: complete with PID remaining must be rejected!
            bad_complete_pid = _make_record(
                4,
                phase="complete",
                previous_sha256=h3,
                child_pid=999,
                exit_code=0,
                tree_stopped=True,
                oracle_pass=True,
                process_closed=True,
                profile_closed=True,
            )
            with self.assertRaises(OfflineJournalSecurityError):
                journal.append_snapshot(bad_complete_pid)

            # Valid: cleaning -> complete
            r4 = _make_record(
                4,
                phase="complete",
                previous_sha256=h3,
                child_pid=None,
                exit_code=0,
                tree_stopped=True,
                oracle_pass=True,
                process_closed=True,
                profile_closed=True,
            )
            journal.append_snapshot(r4)

    def test_j2_r2_bounded_read_canonical_filenames_and_recursion(self) -> None:
        """J2-R2: Reject oversize record, reject non-canonical filename aliases, wrap RecursionError."""
        with create_journal(self.journal_dir, self.init_rec):
            pass

        target = self.journal_dir / "00000000.json"

        # Record > 65536 bytes rejected
        oversize = b" " * (MAX_RECORD_SIZE_BYTES + 1)
        target.write_bytes(oversize)
        with self.assertRaises(OfflineJournalCorruptionError):
            inspect_journal(self.journal_dir)

        # Restore valid 00000000.json
        target.write_bytes(canonical_json_bytes(self.init_rec))

        # Non-canonical filename aliases like 7 digits or decimal aliases (e.g. 0000000.json or +00000000.json)
        alias_file = self.journal_dir / "0000000.json"
        alias_file.write_bytes(canonical_json_bytes(self.init_rec))
        with self.assertRaises(OfflineJournalCorruptionError):
            inspect_journal(self.journal_dir)
        alias_file.unlink()

        # RecursionError wrapping on deep JSON
        deep_obj: dict[str, Any] = {}
        cur = deep_obj
        for _ in range(50):
            cur["nested"] = {}
            cur = cur["nested"]
        target.write_bytes(json.dumps(deep_obj).encode("utf-8"))
        with self.assertRaises(OfflineJournalCorruptionError):
            inspect_journal(self.journal_dir)


class TestFaultInjectionAndSanitization(unittest.TestCase):
    """J4, J2-R3, J2-R4: Fault injection on I/O boundaries, opaque sentinel sanitization, interrupts."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.tmp.name) / "journal"
        self.init_rec = _make_record(0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_j2_r4_fault_injection_table(self) -> None:
        """J2-R4: Bounded faults across write, flush, fsync, rename; evidence and foreign files preserved."""
        with create_journal(self.journal_dir, self.init_rec) as journal:
            h0 = journal.head_sha256
            rec1 = _make_record(1, phase="worker", previous_sha256=h0)

            foreign_file = self.journal_dir / "foreign.tmp"
            foreign_file.write_bytes(b"foreign data")

            # 1. Fault on fsync
            with patch("os.fsync", side_effect=OSError("Disk fsync error")):
                with self.assertRaises(OfflineJournalError):
                    journal.append_snapshot(rec1)
            self.assertEqual(journal.head_revision, 0)
            self.assertTrue(foreign_file.is_file())

            # 2. Fault on rename
            with patch("os.rename", side_effect=OSError("Rename permission denied")):
                with self.assertRaises(OfflineJournalError):
                    journal.append_snapshot(rec1)
            self.assertEqual(journal.head_revision, 0)
            self.assertTrue(foreign_file.is_file())

            # 3. Fault on open/fdopen during publication
            with patch("os.open", side_effect=OSError("FD limit reached")):
                with self.assertRaises(OfflineJournalError):
                    journal.append_snapshot(rec1)
            self.assertEqual(journal.head_revision, 0)
            self.assertTrue(foreign_file.is_file())

    def test_j2_r3_opaque_sentinel_sanitization_in_traceback_and_cause(self) -> None:
        """J2-R3: Non-pattern opaque sentinel is not echoed in message, cause, context, or traceback."""
        opaque_sentinel = "OPAQUE_SECRET_SENTINEL_NON_PATTERN_xyz999"
        bad_rec = dict(_make_record(0), **{opaque_sentinel: "opaque input"})

        with self.assertRaises(OfflineJournalSecurityError) as caught:
            create_journal(self.journal_dir, bad_rec)

        err_msg = str(caught.exception)
        tb_str = "".join(traceback.format_exception(caught.exception))

        self.assertNotIn(opaque_sentinel, err_msg)
        self.assertNotIn(opaque_sentinel, tb_str)

    def test_interrupt_preservation(self) -> None:
        """J2-R4: KeyboardInterrupt and SystemExit are preserved without being masked."""
        with self.assertRaises(KeyboardInterrupt):
            with create_journal(self.journal_dir, self.init_rec):
                raise KeyboardInterrupt("Simulated interrupt")

        with self.assertRaises(SystemExit):
            with create_journal(Path(self.tmp.name) / "j2", self.init_rec):
                raise SystemExit(123)


class TestNegativeControlsAndMutants(unittest.TestCase):
    """Run the same rejection assertions against real guards and four mutants."""

    def test_mutant_oracle_meta_assertions(self) -> None:
        good = "approved fixture output"
        exp = FixtureExpectation(task_id="t1", tests_run=2,
                                 output_sha256=_sha256(good.encode()))
        manifest = TrustedManifest(
            task_id="t1", run_id="r1", nonce="0" * 32, verifier_version="1",
            candidate_bytes_sha256="c" * 64, canonical_tree_oid="b" * 40,
            tree_algorithm="sha1", approved_argv=["python", "-I", "-P", "w.py"],
            timeout_seconds=10, spec_hash=exp.digest, expected_artifact_relpath="res.json")
        base = dict(task_id="t1", run_id="r1", nonce="0" * 32, status="PASS",
                    output=good, tests_run=2, failures=0, errors=0)
        verify_artifact(base, expectation=exp, manifest=manifest)

        for field, wrong in (("output", "wrong"), ("tests_run", 1)):
            with self.subTest(field=field):
                bad = dict(base, **{field: wrong})

                def acceptance(verifier: Any) -> None:
                    with self.assertRaises(OfflineJournalSecurityError):
                        verifier(bad, expectation=exp, manifest=manifest)

                # The mutant discards only the disputed result field.
                def mutant(artifact: Mapping[str, Any], **kwargs: Any) -> None:
                    verify_artifact(dict(artifact, **{field: base[field]}), **kwargs)

                acceptance(verify_artifact)
                with self.assertRaises(AssertionError):
                    acceptance(mutant)
                print("Mutation detected by acceptance assertion:", field)

    def test_mutant_phase_transition_meta_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with create_journal(Path(tmp) / "journal", _make_record()) as owner:
                owner.append_snapshot(_make_record(
                    1, phase="worker", previous_sha256=owner.head_sha256))
                illegal = _make_record(2, phase="preparing",
                                       previous_sha256=owner.head_sha256)

                def acceptance() -> None:
                    with self.assertRaises(OfflineJournalSecurityError):
                        owner.append_snapshot(illegal)

                acceptance()
                with patch.dict(VALID_PHASE_TRANSITIONS, {"worker": frozenset({"preparing"})}):
                    with self.assertRaises(AssertionError):
                        acceptance()
                print("Mutation detected by acceptance assertion: phase")

    def test_mutant_chain_link_meta_assertions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with create_journal(Path(tmp) / "journal", _make_record()) as owner:
                bad = _make_record(1, phase="worker", previous_sha256="f" * 64)

                def acceptance() -> None:
                    with self.assertRaises(OfflineJournalCorruptionError):
                        owner.append_snapshot(bad)

                acceptance()
                with patch.object(offline_journal, "_check_chain_link", return_value=None):
                    with self.assertRaises(AssertionError):
                        acceptance()
                print("Mutation detected by acceptance assertion: chain")


class _FaultStream:
    """One-shot stream fault, delegating real descriptor ownership and cleanup."""

    def __init__(self, stream: Any, operation: str, failure: BaseException) -> None:
        self.stream, self.operation, self.failure = stream, operation, failure
        self.fired = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)

    def _check(self, operation: str) -> None:
        if operation == self.operation and not self.fired:
            self.fired = True
            raise self.failure

    def write(self, data: bytes) -> int:
        self._check("write")
        if self.operation == "short_write":
            return self.stream.write(data[:1])
        return self.stream.write(data)

    def flush(self) -> None:
        self._check("flush")
        self.stream.flush()

    def close(self) -> None:
        self._check("close")
        self.stream.close()


class CoordinatorRegressionTests(unittest.TestCase):
    """Independent behavior, fault and format coverage added after review r2."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def next_record(self, owner: OfflineJournal, **changes: Any) -> dict[str, Any]:
        record = copy.deepcopy(inspect_journal(owner.directory).snapshots[-1])
        record.update(revision=owner.head_revision + 1,
                      previous_sha256=owner.head_sha256, **changes)
        return record

    def assert_lock_free(self, owner: OfflineJournal) -> None:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--child-lock",
             str(owner.lock_file_path)], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCKED", result.stdout)

    def test_expectation_closed_typed_matrix(self) -> None:
        good = FixtureExpectation(task_id="t", tests_run=1, output_sha256="a" * 64).to_dict()
        for key in good:
            bad = dict(good)
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(OfflineJournalError):
                FixtureExpectation.from_dict(bad)
        for key, values in {
                "schema_version": [None, True, 1, "future"],
                "task_id": ["", "../t", "é", "a" * 65, None, 1],
                "tests_run": [False, True, 0, -1, 2**31, 1.0, "1"],
                "output_sha256": ["A" * 64, "a" * 63, None, "g" * 64]}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(OfflineJournalError):
                    FixtureExpectation.from_dict(dict(good, **{key: value}))
        with self.assertRaises(OfflineJournalError):
            FixtureExpectation.from_dict(dict(good, unknown="input"))

    def test_pid_above_signed_int_range_remains_valid_positive_inventory(self) -> None:
        # ADR-0011 says positive int, not signed int32; Windows PID is a DWORD.
        validate_snapshot_record(_make_record(
            1, phase="worker", previous_sha256="a" * 64, child_pid=2**31))

    def test_schema_missing_nested_and_unhashable_values(self) -> None:
        base = _make_record()
        for key in base:
            bad = copy.deepcopy(base)
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(OfflineJournalError):
                validate_snapshot_record(bad)
        for group in ("resources", "observations"):
            for key in base[group]:
                bad = copy.deepcopy(base)
                del bad[group][key]
                with self.subTest(group=group, missing=key), self.assertRaises(OfflineJournalError):
                    validate_snapshot_record(bad)
            bad = copy.deepcopy(base)
            bad[group]["unexpected"] = "input"
            with self.assertRaises(OfflineJournalError):
                validate_snapshot_record(bad)
        for key, value in (("phase", []), ("reason_code", {}), ("revision", False),
                           ("revision", -1), ("nonce", "A" * 32),
                           ("run_id", "../run"), ("tree_algorithm", []),
                           ("manifest_sha256", "a" * 63)):
            with self.subTest(key=key), self.assertRaises(OfflineJournalError):
                validate_snapshot_record(dict(base, **{key: value}))
        for group, key, value in (("resources", "child_pid", False),
                                  ("resources", "child_pid", 0),
                                  ("observations", "exit_code", True),
                                  ("observations", "exit_code", 2**31),
                                  ("observations", "oracle_pass", 1)):
            bad = copy.deepcopy(base)
            bad[group][key] = value
            with self.subTest(group=group, key=key), self.assertRaises(OfflineJournalError):
                validate_snapshot_record(bad)
        validate_snapshot_record(_make_record(tree_algorithm="sha256", candidate_tree="a" * 64))

    def test_all_phase_edges_and_failure_reason(self) -> None:
        expected = {
            "preparing": {"preparing", "worker", "cleaning", "blocked"},
            "worker": {"worker", "verifying", "cleaning", "blocked"},
            "verifying": {"verifying", "cleaning", "blocked"},
            "cleaning": {"cleaning", "complete", "blocked"},
            "complete": set(), "blocked": set()}
        for source in expected:
            for target in expected:
                with self.subTest(source=source, target=target):
                    previous = _make_record(1, phase=source, previous_sha256="a" * 64)
                    if source in ("verifying", "cleaning", "complete"):
                        previous["observations"] = dict(exit_code=0, tree_stopped=True,
                            oracle_pass=True, process_closed=True, profile_closed=True)
                    if source == "blocked":
                        previous["reason_code"] = "interrupted"
                    validate_snapshot_record(previous)
                    record = copy.deepcopy(previous)
                    record.update(revision=2, previous_sha256="b" * 64, phase=target)
                    if target in ("verifying", "cleaning", "complete"):
                        record["observations"] = dict(exit_code=0, tree_stopped=True,
                            oracle_pass=True, process_closed=True, profile_closed=True)
                    if target == "preparing":
                        record["observations"] = _make_record()["observations"]
                    if target == "blocked" or (target == "cleaning" and source not in ("verifying", "cleaning")):
                        record["reason_code"] = "interrupted"
                    if target in expected[source]:
                        validate_snapshot_record(record, previous_record=previous, previous_sha256="b" * 64)
                    else:
                        with self.assertRaises(OfflineJournalError):
                            validate_snapshot_record(record, previous_record=previous, previous_sha256="b" * 64)

    def test_identity_pid_observations_reason_and_terminal_guards(self) -> None:
        with create_journal(self.base / "journal", _make_record()) as owner:
            inventory = self.next_record(owner)
            inventory["resources"].update(profile_name="owned", profile_sid="S-1-15-2-1")
            owner.append_snapshot(inventory)
            for field in ("profile_name", "profile_sid"):
                for value in (None, "changed"):
                    record = self.next_record(owner)
                    record["resources"][field] = value
                    with self.subTest(field=field, value=value), self.assertRaises(OfflineJournalError):
                        owner.append_snapshot(record)
            for key in offline_journal._IDENTITY_FIELDS:
                record = self.next_record(owner, phase="worker")
                record[key] = ({"nonce": "f" * 32, "tree_algorithm": "sha256",
                                "candidate_tree": "f" * 40}.get(key)
                               or ("f" * 64 if key.endswith("sha256") else "different"))
                if key == "tree_algorithm":
                    record["candidate_tree"] = "f" * 64
                with self.subTest(identity=key), self.assertRaises(OfflineJournalError):
                    owner.append_snapshot(record)
            worker = self.next_record(owner, phase="worker")
            worker["resources"]["child_pid"] = 123
            owner.append_snapshot(worker)
            for pid in (None, 124):
                bad = self.next_record(owner)
                bad["resources"]["child_pid"] = pid
                with self.subTest(pid=pid), self.assertRaises(OfflineJournalError):
                    owner.append_snapshot(bad)
            failed = self.next_record(owner, phase="cleaning", reason_code="worker_failed")
            failed["observations"].update(tree_stopped=True, exit_code=1)
            failed["resources"]["child_pid"] = None
            owner.append_snapshot(failed)
            for reason in (None, "interrupted"):
                with self.subTest(reason=reason), self.assertRaises(OfflineJournalError):
                    owner.append_snapshot(self.next_record(owner, reason_code=reason))
            regressed = self.next_record(owner)
            regressed["observations"]["tree_stopped"] = False
            with self.assertRaises(OfflineJournalError):
                owner.append_snapshot(regressed)
            owner.append_snapshot(self.next_record(owner, phase="blocked"))
            with self.assertRaises(OfflineJournalError):
                owner.append_snapshot(self.next_record(owner))
            owner.append_snapshot(inspect_journal(owner.directory).snapshots[-1])

    def test_oversized_append_rejected_before_side_effect(self) -> None:
        with create_journal(self.base / "journal", _make_record()) as owner:
            record = self.next_record(owner)
            record["resources"]["profile_name"] = "x" * 65536
            before = {p.name: p.read_bytes() for p in owner.directory.glob("*.json")}
            with self.assertRaises(OfflineJournalError):
                owner.append_snapshot(record)
            self.assertEqual(before, {p.name: p.read_bytes() for p in owner.directory.glob("*.json")})

    def test_inspect_is_bounded_readonly_and_detached(self) -> None:
        directory = self.base / "journal"
        with create_journal(directory, _make_record()) as owner:
            owner.append_snapshot(self.next_record(owner, phase="worker"))
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        original_open = Path.open
        sizes = []

        class Reader:
            def __init__(self, stream: Any) -> None:
                self.stream = stream
            def __enter__(self) -> Any:
                return self
            def __exit__(self, *args: Any) -> None:
                self.stream.close()
            def read(self, size: int = -1) -> bytes:
                sizes.append(size)
                return self.stream.read(size)

        def opened(path: Path, *args: Any, **kwargs: Any) -> Any:
            return Reader(original_open(path, *args, **kwargs))

        with patch.object(Path, "open", opened):
            result = inspect_journal(directory)
        self.assertEqual(sizes, [65537, 65537])
        result.snapshots[0]["run_id"] = "mutated"
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(inspect_journal(directory).snapshots[0]["run_id"], "run-001")
        with patch.object(Path, "mkdir", side_effect=AssertionError("inspect wrote")):
            with self.assertRaises(OfflineJournalError):
                inspect_journal(self.base / "absent")
        self.assertFalse((self.base / "absent").exists())

    def test_malformed_bytes_gaps_aliases_and_tamper_preserve_data(self) -> None:
        for index, raw in enumerate((b"\xff", b"{", b"[]", b"null",
                                     b'{"x":NaN}', b'{"x":Infinity}',
                                     b'{"x":1,"x":2}', b'{"x":' + b"["*1100 + b"0" + b"]"*1100 + b"}")):
            directory = self.base / str(index)
            with create_journal(directory, _make_record()):
                pass
            path = directory / "00000000.json"
            path.write_bytes(raw)
            with self.subTest(raw=index), self.assertRaises(OfflineJournalError):
                inspect_journal(directory)
            self.assertEqual(path.read_bytes(), raw)
        with create_journal(self.base / "live", _make_record()) as owner:
            before = owner.head_file_path.read_bytes()
            owner.head_file_path.write_bytes(before + b" ")
            with self.assertRaises(OfflineJournalError):
                owner.append_snapshot(_make_record(1, phase="worker", previous_sha256=owner.head_sha256))
            self.assertEqual(owner.head_file_path.read_bytes(), before + b" ")
        with create_journal(self.base / "gap", _make_record()) as owner:
            gap = owner.directory / "00000002.json"
            gap.write_bytes(canonical_json_bytes(_make_record(2, previous_sha256=owner.head_sha256)))
            with self.assertRaises(OfflineJournalError):
                inspect_journal(owner.directory)

    def test_publication_stream_faults_and_short_write(self) -> None:
        original = os.fdopen
        for operation in ("write", "short_write", "flush", "close"):
            with self.subTest(operation=operation):
                with create_journal(self.base / operation, _make_record()) as owner:
                    record = self.next_record(owner, phase="worker")
                    old = owner.head_file_path.read_bytes()
                    foreign = owner.directory / "foreign.tmp"
                    foreign.write_bytes(b"keep")
                    def wrapped(fd: int, *args: Any, **kwargs: Any) -> Any:
                        return _FaultStream(original(fd, *args, **kwargs), operation, OSError("opaque input"))
                    with patch("os.fdopen", side_effect=wrapped):
                        with self.assertRaises(OfflineJournalError):
                            owner.append_snapshot(record)
                    self.assertEqual(owner.head_revision, 0)
                    self.assertEqual(owner.head_file_path.read_bytes(), old)
                    self.assertEqual(foreign.read_bytes(), b"keep")
                    self.assertEqual(list(owner.directory.glob(".journal-*.tmp")), [])

        with create_journal(self.base / "collision", _make_record()) as owner:
            record = self.next_record(owner, phase="worker")
            foreign = owner.directory / ".journal-fixed.tmp"
            foreign.write_bytes(b"owned by another operation")
            with patch.object(offline_journal.secrets, "token_hex", return_value="fixed"):
                with self.assertRaises(OfflineJournalError):
                    owner.append_snapshot(record)
            self.assertEqual(foreign.read_bytes(), b"owned by another operation")
            self.assertEqual(owner.head_revision, 0)

    def test_fdopen_failure_closes_allocated_fd(self) -> None:
        with create_journal(self.base / "journal", _make_record()) as owner:
            record = self.next_record(owner, phase="worker")
            descriptors = []
            original = os.open
            def opened(*args: Any, **kwargs: Any) -> int:
                fd = original(*args, **kwargs)
                descriptors.append(fd)
                return fd
            with patch("os.open", side_effect=opened), patch("os.fdopen", side_effect=OSError("opaque input")):
                with self.assertRaises(OfflineJournalError):
                    owner.append_snapshot(record)
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_cleanup_failure_preserves_primary_and_foreign_evidence(self) -> None:
        with create_journal(self.base / "journal", _make_record()) as owner:
            record = self.next_record(owner, phase="worker")
            sentinel = "opaque value qz91"
            failure = OSError(5, sentinel, sentinel)
            failure.add_note(sentinel)
            with patch("os.fsync", side_effect=failure), patch.object(Path, "unlink", side_effect=OSError(sentinel)):
                with self.assertRaises(OfflineJournalError) as caught:
                    owner.append_snapshot(record)
            self.assertIs(caught.exception.__cause__, failure)
            self.assertNotIn(sentinel, "".join(traceback.format_exception(caught.exception)))
            self.assertTrue(any("cleanup" in note for note in failure.__notes__))
            self.assertEqual(owner.head_revision, 0)
            self.assertFalse((owner.directory / "00000001.json").exists())
            self.assertEqual(len(list(owner.directory.glob(".journal-*.tmp"))), 1)

    def test_interrupt_during_publication_preserves_type_and_head(self) -> None:
        for kind in (KeyboardInterrupt, SystemExit):
            with self.subTest(kind=kind):
                with create_journal(self.base / kind.__name__, _make_record()) as owner:
                    record = self.next_record(owner, phase="worker")
                    with patch("os.fsync", side_effect=kind("opaque input")):
                        with self.assertRaises(kind):
                            owner.append_snapshot(record)
                    self.assertEqual(owner.head_revision, 0)
                    self.assertFalse((owner.directory / "00000001.json").exists())

    def test_lock_acquire_failure_and_unlock_error_release_native_handle(self) -> None:
        import msvcrt
        with patch("msvcrt.locking", side_effect=OSError("opaque input")):
            with self.assertRaises(OfflineJournalError):
                create_journal(self.base / "failed", _make_record())
        owner = create_journal(self.base / "live", _make_record())
        real_lock = msvcrt.locking
        def locking(fd: int, mode: int, size: int) -> None:
            if mode == msvcrt.LK_UNLCK:
                raise OSError("opaque input")
            real_lock(fd, mode, size)
        with patch("msvcrt.locking", side_effect=locking):
            with self.assertRaises(OfflineJournalError):
                owner.close()
        self.assertTrue(owner.closed)
        self.assert_lock_free(owner)
        owner.close()

    def test_body_error_not_masked_by_cleanup(self) -> None:
        owner = create_journal(self.base / "journal", _make_record())
        original = owner._lock_stream
        owner._lock_stream = _FaultStream(original, "close", OSError("opaque cleanup"))
        failure = RuntimeError("opaque body")
        with self.assertRaises(RuntimeError) as caught:
            with owner:
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertTrue(any("cleanup" in note for note in failure.__notes__))
        owner.close()
        self.assert_lock_free(owner)

    def test_snapshot_copy_survives_caller_mutation_at_publication(self) -> None:
        initial = _make_record()
        real = os.rename
        def mutate_initial(src: Any, dst: Any) -> None:
            initial["run_id"] = "mutated"
            real(src, dst)
        with patch("os.rename", side_effect=mutate_initial):
            owner = create_journal(self.base / "journal", initial)
        with owner:
            record = _make_record(1, phase="worker", previous_sha256=owner.head_sha256)
            def mutate_record(src: Any, dst: Any) -> None:
                record["revision"] = 99
                real(src, dst)
            with patch("os.rename", side_effect=mutate_record):
                owner.append_snapshot(record)
            self.assertEqual(owner.head_revision, 1)
            self.assertEqual(inspect_journal(owner.directory).snapshots[-1]["revision"], 1)


# Native child helper entrypoint
def _run_child_lock(lock_file_path: str) -> None:
    path = Path(lock_file_path)
    if not path.exists():
        sys.exit(2)
    with path.open("r+b") as stream:
        try:
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("BUSY", flush=True)
            sys.exit(1)
        try:
            print("LOCKED", flush=True)
            time.sleep(0.5)
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    sys.exit(0)


def _run_child_rival_create(target_path_str: str, payload_path_str: str) -> None:
    target = Path(target_path_str)
    payload = Path(payload_path_str)
    target.write_bytes(payload.read_bytes())
    print("CREATED", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child-lock":
        _run_child_lock(sys.argv[2])
    elif len(sys.argv) > 3 and sys.argv[1] == "--child-rival-create":
        _run_child_rival_create(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
