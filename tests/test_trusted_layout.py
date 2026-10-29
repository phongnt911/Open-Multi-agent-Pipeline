"""SEC-02-B1b: Tests for offline L2 layout, environment sanitization, manifest, and artifact reader.

This file contains:
1. Environment allowlist unit tests (B1b-3)
2. Manifest schema, serialization, atomic publication, and candidate verification (B1b-4)
3. Owned L2 layout, directory creation, protected DACLs, and Low label verification (B1b-2)
4. Handle-based artifact reader unit tests (B1b-5)
5. Fault injection and cleanup tests (B1b-7)
6. Negative control tests verifying mutant detections (B1b-8)
7. Native Windows execution and isolation verification on Python 3.12 (B1b-6)

Does not import ``tools.proofs``.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

from omp_core import trusted_layout
from omp_core.trusted_layout import (
    LOW_INTEGRITY_SID,
    MANIFEST_SCHEMA_VERSION,
    MAX_ARTIFACT_SIZE_BYTES,
    OwnedL2Layout,
    TrustedLayoutBlockedError,
    TrustedLayoutError,
    TrustedLayoutSecurityError,
    TrustedManifest,
    build_trusted_environment,
    current_user_sid,
    read_trusted_artifact,
)
from omp_core import windows_job
from omp_core.windows_job import (
    WindowsAppContainerProfile,
    WindowsJobError,
    WindowsJobProcess,
)

if os.name == "nt":
    from tests.test_appcontainer_process import (
        _PROCESS_INFORMATION,
        _as_int,
        _controller_kernel32,
        _current_desktop_allows_appcontainer,
        _ensure_controller_state_clean,
        _release_controller_handles,
        _start_with_medium_token,
        _token_information,
        _user_sid,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = REPO_ROOT / ".omp" / "test-tmp"
MEDIUM_INTEGRITY_SID = "S-1-16-8192"
HIGH_INTEGRITY_SID = "S-1-16-12288"
_IN_NATIVE_CONTROLLER = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _icacls(target: str, *args: str) -> None:
    subprocess.run(
        ["icacls", target, *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _prepare_runtime(exe: Path, target: Path) -> Path:
    """Copy a minimal owned CPython runtime for the AppContainer Worker."""
    target.mkdir(parents=True, exist_ok=True)
    source = exe.parent
    lib = source / "Lib" if (source / "Lib").is_dir() else source.parent / "Lib"
    shutil.copy2(exe, target / "python.exe")
    for dll in source.glob("*.dll"):
        shutil.copy2(dll, target / dll.name)
    shutil.copytree(
        lib,
        target / "Lib",
        ignore=shutil.ignore_patterns(
            "site-packages", "test", "idlelib", "tkinter", "turtledemo",
            "ensurepip", "venv", "lib2to3", "__pycache__",
        ),
    )
    dlls = source / "DLLs"
    if dlls.is_dir():
        shutil.copytree(
            dlls, target / "DLLs",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return target / "python.exe"


class _DummyJobProcess(WindowsJobProcess):
    """Mock process exposing WindowsJobProcess properties for unit tests."""

    def __init__(self, *, tree_stopped: bool = True, active_processes: int = 0) -> None:
        self._tree_stopped = tree_stopped
        self._active_processes = active_processes

    @property
    def tree_stopped(self) -> bool:
        return self._tree_stopped

    @property
    def active_processes(self) -> int:
        return self._active_processes


class EnvironmentAllowlistTests(unittest.TestCase):
    """B1b-3: Environment allowlist validation and parent isolation."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_environment_contains_only_approved_keys(self) -> None:
        env = build_trusted_environment("run-123", "task-abc", temp_dir=self.temp_dir)
        self.assertEqual(env["OMP_RUN_ID"], "run-123")
        self.assertEqual(env["OMP_TASK_ID"], "task-abc")
        self.assertEqual(env["TEMP"], str(self.temp_dir))
        self.assertEqual(env["TMP"], str(self.temp_dir))
        self.assertIn("SystemRoot", env)
        self.assertIn("SystemDrive", env)
        self.assertIn("PATH", env)
        # Verify parent environment variables are NOT copied
        self.assertNotIn("USERNAME", env)
        self.assertNotIn("COMPUTERNAME", env)
        self.assertNotIn("USERPROFILE", env)

    def test_custom_runtime_path_is_prepended_to_path(self) -> None:
        custom_runtime = self.temp_dir / "Python312"
        custom_runtime.mkdir()
        env = build_trusted_environment(
            "run-1", "task-1", temp_dir=self.temp_dir, runtime_path=custom_runtime
        )
        self.assertTrue(env["PATH"].startswith(f"{custom_runtime};"))

    def test_extra_omp_keys_allowed_when_valid(self) -> None:
        extra = {"OMP_CANARY_VAULT": "C:/vault", "OMP_MODE": "test"}
        env = build_trusted_environment(
            "run-1", "task-1", temp_dir=self.temp_dir, extra_omp_keys=extra
        )
        self.assertEqual(env["OMP_CANARY_VAULT"], "C:/vault")
        self.assertEqual(env["OMP_MODE"], "test")

    def test_rejects_dangerous_overrides_case_insensitively(self) -> None:
        dangerous = ["PYTHONPATH", "pythonpath", "PythonHome", "COMSPEC", "SHELL", "LD_PRELOAD"]
        for key in dangerous:
            with self.subTest(key=key):
                with self.assertRaises(TrustedLayoutSecurityError):
                    build_trusted_environment(
                        "run-1", "task-1", temp_dir=self.temp_dir, extra_omp_keys={key: "bad"}
                    )

    def test_rejects_non_omp_extra_keys(self) -> None:
        with self.assertRaises(TrustedLayoutSecurityError):
            build_trusted_environment(
                "run-1", "task-1", temp_dir=self.temp_dir, extra_omp_keys={"SECRET_TOKEN": "123"}
            )

    def test_rejects_duplicate_keys_case_insensitively(self) -> None:
        with self.assertRaises(ValueError):
            build_trusted_environment(
                "run-1", "task-1", temp_dir=self.temp_dir, extra_omp_keys={"omp_run_id": "duplicate"}
            )

    def test_rejects_invalid_run_id(self) -> None:
        for bad_id in ("", "-bad", "bad/id", "bad\\id", "bad..id", "a" * 65):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    build_trusted_environment(bad_id, "task-1", temp_dir=self.temp_dir)

    def test_rejects_nul_in_arguments(self) -> None:
        with self.assertRaises(ValueError):
            build_trusted_environment("run-1", "task\0bad", temp_dir=self.temp_dir)
        with self.assertRaises(ValueError):
            build_trusted_environment("run-1", "task-1", temp_dir=Path("C:/tmp\0bad"))
        with self.assertRaises(ValueError):
            build_trusted_environment(
                "run-1", "task-1", temp_dir=self.temp_dir, extra_omp_keys={"OMP_KEY": "val\0bad"}
            )


class ManifestSchemaAndBindingTests(unittest.TestCase):
    """B1b-4: Manifest schema, strict typing, atomic publication, candidate verification."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.valid_data = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "task_id": "task-test-01",
            "run_id": "run-test-01",
            "nonce": secrets.token_hex(16),
            "verifier_version": "1.0.0",
            "candidate_bytes_sha256": hashlib.sha256(b"code").hexdigest(),
            "canonical_tree_oid": "a" * 40,
            "tree_algorithm": "sha1",
            "approved_argv": ["python", "-I", "-P", "worker.py"],
            "timeout_seconds": 30.0,
            "spec_hash": hashlib.sha256(b"spec").hexdigest(),
            "expected_artifact_relpath": "result.json",
        }

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_manifest_construction_and_roundtrip(self) -> None:
        manifest = TrustedManifest.from_dict(self.valid_data)
        self.assertEqual(manifest.schema_version, MANIFEST_SCHEMA_VERSION)
        self.assertEqual(manifest.task_id, "task-test-01")
        self.assertEqual(manifest.run_id, "run-test-01")
        self.assertEqual(manifest.approved_argv, ("python", "-I", "-P", "worker.py"))
        roundtrip = TrustedManifest.from_dict(manifest.to_dict())
        self.assertEqual(roundtrip.to_dict(), manifest.to_dict())

    def test_rejects_unknown_fields(self) -> None:
        bad = dict(self.valid_data)
        bad["unknown_field"] = "bad"
        with self.assertRaises(ValueError) as caught:
            TrustedManifest.from_dict(bad)
        self.assertIn("unknown fields", str(caught.exception))

    def test_rejects_missing_fields(self) -> None:
        bad = dict(self.valid_data)
        del bad["nonce"]
        with self.assertRaises(ValueError) as caught:
            TrustedManifest.from_dict(bad)
        self.assertIn("missing fields", str(caught.exception))

    def test_rejects_bool_for_timeout_seconds(self) -> None:
        bad = dict(self.valid_data)
        bad["timeout_seconds"] = True
        with self.assertRaises(ValueError):
            TrustedManifest.from_dict(bad)

    def test_rejects_negative_or_infinite_timeout(self) -> None:
        for val in (-1, 0, float("inf"), float("nan")):
            bad = dict(self.valid_data)
            bad["timeout_seconds"] = val
            with self.subTest(val=val):
                with self.assertRaises(ValueError):
                    TrustedManifest.from_dict(bad)

    def test_rejects_invalid_sha256_format(self) -> None:
        for bad_hash in ("bad", "A" * 64, "0" * 63, "0" * 65, "g" * 64):
            bad = dict(self.valid_data)
            bad["candidate_bytes_sha256"] = bad_hash
            with self.subTest(bad_hash=bad_hash):
                with self.assertRaises(ValueError):
                    TrustedManifest.from_dict(bad)

    def test_rejects_invalid_tree_oid(self) -> None:
        for bad_oid in ("bad", "A" * 40, "0" * 39, "0" * 41):
            bad = dict(self.valid_data)
            bad["canonical_tree_oid"] = bad_oid
            with self.subTest(bad_oid=bad_oid):
                with self.assertRaises(ValueError):
                    TrustedManifest.from_dict(bad)

    def test_rejects_invalid_expected_artifact_relpath(self) -> None:
        for bad_path in (
            "/abs/path", "../outside", "a/../b", "a\\b", "c:stream", "a//b", ""
        ):
            bad = dict(self.valid_data)
            bad["expected_artifact_relpath"] = bad_path
            with self.subTest(bad_path=bad_path):
                with self.assertRaises(ValueError):
                    TrustedManifest.from_dict(bad)

    def test_atomic_publication_and_refusal_to_overwrite_differing(self) -> None:
        manifest = TrustedManifest.from_dict(self.valid_data)
        pub_path = manifest.publish_atomic(self.temp_dir)
        self.assertTrue(pub_path.exists())
        self.assertEqual(pub_path.name, "approved_manifest.json")

        # Idempotent re-publish of same manifest succeeds
        self.assertEqual(manifest.publish_atomic(self.temp_dir), pub_path)

        # Re-publish of differing manifest is rejected
        differing_data = dict(self.valid_data)
        differing_data["task_id"] = "task-different"
        diff_manifest = TrustedManifest.from_dict(differing_data)
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            diff_manifest.publish_atomic(self.temp_dir)
        self.assertIn("refusing to overwrite", str(caught.exception))

    def test_verify_candidate_validates_bytes_and_argv(self) -> None:
        manifest = TrustedManifest.from_dict(self.valid_data)
        self.assertTrue(manifest.verify_candidate(b"code", ["python", "-I", "-P", "worker.py"]))

        # Drift in bytes
        with self.assertRaises(TrustedLayoutSecurityError):
            manifest.verify_candidate(b"different code", ["python", "-I", "-P", "worker.py"])

        # Drift in argv
        with self.assertRaises(TrustedLayoutSecurityError):
            manifest.verify_candidate(b"code", ["python", "worker.py"])


class LayoutAndSecurityObservationTests(unittest.TestCase):
    """B1b-2: Owned L2 layout, directory isolation, DACLs, and mandatory label."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_layout_rejects_reparse_parent(self) -> None:
        with patch("omp_core.trusted_layout._is_reparse_point", return_value=True):
            with self.assertRaises(TrustedLayoutSecurityError):
                OwnedL2Layout.create(
                    self.temp_dir, "run-1", user_sid="S-1-5-18", container_sid="S-1-15-2-1"
                )

    def test_create_layout_creates_required_directories(self) -> None:
        user_sid = current_user_sid() if os.name == "nt" else "S-1-5-18"
        container_sid = "S-1-15-2-1"
        layout = OwnedL2Layout.create(
            self.temp_dir, "run-abc", user_sid=user_sid, container_sid=container_sid
        )
        try:
            self.assertTrue(layout.root.is_dir())
            self.assertTrue(layout.checkout_dir.is_dir())
            self.assertTrue(layout.vault_dir.is_dir())
            self.assertTrue(layout.state_dir.is_dir())
            self.assertTrue(layout.log_dir.is_dir())
            self.assertTrue(layout.temp_dir.is_dir())
            self.assertEqual(layout.run_id, "run-abc")
        finally:
            layout.cleanup()
        self.assertFalse(layout.root.exists())

    @unittest.skipUnless(os.name == "nt", "Windows DACL and label verification requires Windows")
    def test_dacl_and_label_queries_succeed_on_owned_layout(self) -> None:
        user_sid = current_user_sid()
        container_sid = "S-1-15-2-1"
        layout = OwnedL2Layout.create(
            self.temp_dir, "run-sec", user_sid=user_sid, container_sid=container_sid
        )
        try:
            # Query checkout DACL
            checkout_dacl = trusted_layout._query_dacl(layout.checkout_dir, user_sid, container_sid)
            self.assertTrue(checkout_dacl["protected"])
            self.assertTrue(checkout_dacl["user_ace"])
            self.assertTrue(checkout_dacl["container_ace"])

            # Query vault DACL
            vault_dacl = trusted_layout._query_dacl(layout.vault_dir, user_sid, container_sid)
            self.assertTrue(vault_dacl["protected"])
            self.assertTrue(vault_dacl["user_ace"])
            self.assertFalse(vault_dacl["container_ace"])

            # Query mandatory label on checkout
            label = trusted_layout._query_mandatory_label(layout.checkout_dir)
            self.assertEqual(label, LOW_INTEGRITY_SID)
        finally:
            layout.cleanup()


class ArtifactReaderUnitTests(unittest.TestCase):
    """B1b-5: Secure handle-based artifact reader with Job containment proof."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.manifest = TrustedManifest(
            task_id="task-1",
            run_id="run-1",
            nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"code").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "w.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        self.valid_artifact = {
            "task_id": "task-1",
            "run_id": "run-1",
            "nonce": "0123456789abcdef0123456789abcdef",
            "status": "success",
            "output": "sample output",
        }
        (self.temp_dir / "w.py").write_bytes(b"code")

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_refuses_when_job_tree_not_stopped(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=False, active_processes=0)
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(self.temp_dir, "result.json", job_process=job, manifest=self.manifest)
        self.assertIn("process tree has not stopped", str(caught.exception))

    def test_refuses_when_job_has_active_processes(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=1)
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(self.temp_dir, "result.json", job_process=job, manifest=self.manifest)
        self.assertIn("job still contains 1 active process", str(caught.exception))

    def test_refuses_caller_boolean_instead_of_job_process(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=True, manifest=self.manifest)

    def _read_artifact(self, rel_path: str = "result.json", *, job_process=None, manifest=None, **kwargs) -> dict[str, Any]:
        if job_process is None:
            job_process = _DummyJobProcess(tree_stopped=True, active_processes=0)
        m = manifest or self.manifest
        params = {
            "actual_argv": m.approved_argv,
            "actual_timeout": m.timeout_seconds,
            "actual_tree_oid": m.canonical_tree_oid,
            "actual_spec_hash": m.spec_hash,
        }
        params.update(kwargs)
        return read_trusted_artifact(self.temp_dir, rel_path, job_process=job_process, manifest=m, **params)

    def test_refuses_artifact_exceeding_64kib(self) -> None:
        target = self.temp_dir / "result.json"
        oversized = dict(self.valid_artifact)
        oversized["padding"] = "x" * (65 * 1024)
        target.write_text(json.dumps(oversized), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            self._read_artifact("result.json", job_process=job)
        self.assertIn("exceeds limit", str(caught.exception))

    def test_refuses_malformed_json_or_invalid_utf8(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_bytes(b"\xff\xfe malformed")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        with self.assertRaises(TrustedLayoutError):
            self._read_artifact("result.json", job_process=job)

    def test_refuses_binding_mismatch(self) -> None:
        for field, bad_val in (
            ("task_id", "task-wrong"),
            ("run_id", "run-wrong"),
            ("nonce", "wrong-nonce"),
        ):
            target = self.temp_dir / "result.json"
            bad = dict(self.valid_artifact)
            bad[field] = bad_val
            target.write_text(json.dumps(bad), encoding="utf-8")
            job = _DummyJobProcess(tree_stopped=True, active_processes=0)
            with self.subTest(field=field):
                with self.assertRaises(TrustedLayoutSecurityError):
                    self._read_artifact("result.json", job_process=job)

    def test_refuses_path_mismatch_with_manifest(self) -> None:
        target = self.temp_dir / "other.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        with self.assertRaises(TrustedLayoutSecurityError):
            self._read_artifact("other.json", job_process=job)

    def test_successful_artifact_read_when_all_invariants_hold(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        result = self._read_artifact("result.json", job_process=job)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_id"], "task-1")

    @unittest.skipUnless(os.name == "nt", "Handle-based reparse test requires Windows")
    def test_refuses_hardlink_pointing_to_artifact(self) -> None:
        orig = self.temp_dir / "orig.json"
        orig.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        link = self.temp_dir / "result.json"
        try:
            os.link(orig, link)
        except OSError:
            return  # skip if link privilege is absent
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            self._read_artifact("result.json", job_process=job)
        self.assertIn("multiple hard links detected", str(caught.exception))


class FaultAndCleanupTests(unittest.TestCase):
    """B1b-7: Fault injection, bounded stop, and cleanup exception preservation."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_layout_allocation_failure_cleans_up_partial_resources(self) -> None:
        with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
            with self.assertRaises(TrustedLayoutSecurityError):
                OwnedL2Layout.create(
                    self.temp_dir, "run-fail", user_sid="S-1-5-18", container_sid="S-1-15-2-1"
                )

    def test_manifest_publish_failure_cleans_temp_file(self) -> None:
        manifest = TrustedManifest(
            task_id="t1", run_id="r1", nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0", candidate_bytes_sha256="0" * 64,
            canonical_tree_oid="0" * 40, tree_algorithm="sha1",
            approved_argv=["python"], timeout_seconds=1.0,
            spec_hash="0" * 64, expected_artifact_relpath="res.json"
        )
        with patch("os.rename", side_effect=OSError("rename locked")):
            with self.assertRaises(TrustedLayoutError):
                manifest.publish_atomic(self.temp_dir)
        temp_files = list(self.temp_dir.glob(".tmp_manifest_*"))
        self.assertEqual(temp_files, [])

    def test_cleanup_preserves_keyboard_interrupt_and_system_exit(self) -> None:
        manifest = TrustedManifest(
            task_id="t1", run_id="r1", nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0", candidate_bytes_sha256="0" * 64,
            canonical_tree_oid="0" * 40, tree_algorithm="sha1",
            approved_argv=["python"], timeout_seconds=1.0,
            spec_hash="0" * 64, expected_artifact_relpath="res.json"
        )
        for interrupt in (KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(interrupt=type(interrupt).__name__):
                with patch("os.fsync", side_effect=interrupt):
                    with self.assertRaises(type(interrupt)):
                        manifest.publish_atomic(self.temp_dir)


class NegativeControlTests(unittest.TestCase):
    """B1b-8: Negative controls proving mutants fail security verification."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.manifest = TrustedManifest(
            task_id="task-nc",
            run_id="run-nc",
            nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"code").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "w.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        self.valid_artifact = {
            "task_id": "task-nc",
            "run_id": "run-nc",
            "nonce": "0123456789abcdef0123456789abcdef",
            "status": "PASS",
        }
        (self.temp_dir / "w.py").write_bytes(b"code")
        (self.temp_dir / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_mutant_bypassing_tree_stop_guard_is_detected(self) -> None:
        target = self.temp_dir / "result.json"
        target.write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        live_job = _DummyJobProcess(tree_stopped=False, active_processes=2)
        bindings = {
            "actual_argv": self.manifest.approved_argv,
            "actual_timeout": self.manifest.timeout_seconds,
            "actual_tree_oid": self.manifest.canonical_tree_oid,
            "actual_spec_hash": self.manifest.spec_hash,
        }
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=live_job, manifest=self.manifest, **bindings)

    def test_mutant_relaxing_manifest_binding_is_detected(self) -> None:
        bad_artifact = dict(self.valid_artifact)
        bad_artifact["nonce"] = "mutated-nonce"
        (self.temp_dir / "result.json").write_text(json.dumps(bad_artifact), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        bindings = {
            "actual_argv": self.manifest.approved_argv,
            "actual_timeout": self.manifest.timeout_seconds,
            "actual_tree_oid": self.manifest.canonical_tree_oid,
            "actual_spec_hash": self.manifest.spec_hash,
        }
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=job, manifest=self.manifest, **bindings)

    def test_mutant_enlarging_artifact_size_limit_is_detected(self) -> None:
        oversized = dict(self.valid_artifact)
        oversized["details"] = "y" * (65 * 1024)
        (self.temp_dir / "result.json").write_text(json.dumps(oversized), encoding="utf-8")
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        bindings = {
            "actual_argv": self.manifest.approved_argv,
            "actual_timeout": self.manifest.timeout_seconds,
            "actual_tree_oid": self.manifest.canonical_tree_oid,
            "actual_spec_hash": self.manifest.spec_hash,
        }
        # Production reader MUST reject > 64 KiB
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=job, manifest=self.manifest, **bindings)

    def test_mutant_skipping_candidate_verification_is_detected(self) -> None:
        tampered_bytes = b"tampered worker candidate bytes"
        with self.assertRaises(TrustedLayoutSecurityError):
            self.manifest.verify_candidate(tampered_bytes, self.manifest.approved_argv)


class FindingRegressionTests(unittest.TestCase):
    """B1b-8: Regressions reproducing and proving resolution of findings SEC02B1B-R1 through R8."""

    def setUp(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        self.temp_dir = Path(tempfile.mkdtemp(prefix="omp-b1b-reg-"))
        self.manifest = TrustedManifest(
            task_id="task-reg",
            run_id="run-reg",
            nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"code").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "worker.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        (self.temp_dir / "worker.py").write_bytes(b"code")
        self.valid_artifact = {
            "task_id": "task-reg",
            "run_id": "run-reg",
            "nonce": "0123456789abcdef0123456789abcdef",
            "status": "PASS",
        }
        (self.temp_dir / "w.py").write_bytes(b"code")

    def _read_artifact(self, rel_path: str = "result.json", *, checkout_dir=None, job_process=None, manifest=None, **kwargs) -> dict[str, Any]:
        if checkout_dir is None:
            checkout_dir = self.temp_dir
        if job_process is None:
            job_process = _DummyJobProcess(tree_stopped=True, active_processes=0)
        m = manifest or self.manifest
        params = {
            "actual_argv": m.approved_argv,
            "actual_timeout": m.timeout_seconds,
            "actual_tree_oid": m.canonical_tree_oid,
            "actual_spec_hash": m.spec_hash,
        }
        params.update(kwargs)
        return read_trusted_artifact(checkout_dir, rel_path, job_process=job_process, manifest=m, **params)
        (self.temp_dir / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")

    def tearDown(self) -> None:
        if os.name == "nt":
            import tests.test_appcontainer_process as tap
            tap._UNPROVEN_CONTROLLER_STATE = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_r1_fake_job_and_duck_typing_rejected(self) -> None:
        """R1: Arbitrary dummy objects and boolean process counts must be refused."""
        # SimpleNamespace with duck typed attributes
        fake_job = types.SimpleNamespace(tree_stopped=True, active_processes=False)
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=fake_job, manifest=self.manifest)

        fake_job_zero = types.SimpleNamespace(tree_stopped=True, active_processes=0)
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=fake_job_zero, manifest=self.manifest)

        # Non-WindowsJobProcess mock or caller boolean
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=True, manifest=self.manifest)

        # Valid mock with boolean active_processes
        bool_job = _DummyJobProcess(tree_stopped=True, active_processes=False)  # type: ignore[arg-type]
        with self.assertRaises(TrustedLayoutSecurityError):
            read_trusted_artifact(self.temp_dir, "result.json", job_process=bool_job, manifest=self.manifest)

    def test_r2_junction_checkout_root_rejected(self) -> None:
        """R2: Junction at checkout root must not be resolved away to legitimize escape."""
        if os.name != "nt":
            return
        import _winapi
        outside = self.temp_dir / "outside"
        outside.mkdir()
        (outside / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        (outside / "worker.py").write_bytes(b"code")
        redirected = self.temp_dir / "redirected-checkout"
        _winapi.CreateJunction(str(outside), str(redirected))
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        try:
            with self.assertRaises(TrustedLayoutSecurityError):
                self._read_artifact(checkout_dir=redirected, rel_path="result.json", job_process=job)
        finally:
            redirected.rmdir()

    def test_r3_manifest_oid_coupling_and_artifact_schema_strictness(self) -> None:
        """R3: Manifest validates algorithm/OID coupling; reader validates candidate and schema."""
        # Mismatched tree_algorithm and OID length
        with self.assertRaises(ValueError):
            TrustedManifest(
                task_id="t", run_id="r", nonce="0123456789abcdef", verifier_version="1",
                candidate_bytes_sha256="a" * 64, canonical_tree_oid="a" * 40, tree_algorithm="sha256",
                approved_argv=["python", "w.py"], timeout_seconds=10, spec_hash="b" * 64,
                expected_artifact_relpath="result.json",
            )
        with self.assertRaises(ValueError):
            TrustedManifest(
                task_id="t", run_id="r", nonce="0123456789abcdef", verifier_version="1",
                candidate_bytes_sha256="a" * 64, canonical_tree_oid="a" * 64, tree_algorithm="sha1",
                approved_argv=["python", "w.py"], timeout_seconds=10, spec_hash="b" * 64,
                expected_artifact_relpath="result.json",
            )

        job = _DummyJobProcess(tree_stopped=True, active_processes=0)

        # Candidate drift on disk
        (self.temp_dir / "worker.py").write_bytes(b"mutated unapproved bytes")
        with self.assertRaises(TrustedLayoutSecurityError):
            self._read_artifact("result.json", job_process=job)
        (self.temp_dir / "worker.py").write_bytes(b"code")

        # Untyped / unapproved fields in artifact
        untyped = dict(self.valid_artifact)
        untyped["extra_unapproved"] = True
        (self.temp_dir / "result.json").write_text(json.dumps(untyped), encoding="utf-8")
        with self.assertRaises(TrustedLayoutSecurityError):
            self._read_artifact("result.json", job_process=job)

        # Unapproved status
        bad_status = dict(self.valid_artifact)
        bad_status["status"] = "PENDING"
        (self.temp_dir / "result.json").write_text(json.dumps(bad_status), encoding="utf-8")
        with self.assertRaises(TrustedLayoutSecurityError):
            self._read_artifact("result.json", job_process=job)

    def test_r4_publication_race_no_clobber(self) -> None:
        """R4: Concurrent publication must not overwrite an existing differing manifest."""
        vault = self.temp_dir / "vault"
        vault.mkdir()
        self.manifest.publish_atomic(vault)
        self.assertTrue((vault / "approved_manifest.json").exists())

        # Rival manifest with differing nonce
        rival = TrustedManifest(
            task_id="task-reg",
            run_id="run-reg",
            nonce="rival-0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"code").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "worker.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        with self.assertRaises(TrustedLayoutSecurityError):
            rival.publish_atomic(vault)

        # Original manifest unchanged
        loaded = json.loads((vault / "approved_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["nonce"], self.manifest.nonce)

    def test_r5_cleanup_false_success_interrupt_and_secret_redaction(self) -> None:
        """R5: Cleanup reports verified success only, preserves interrupts, and redacts secrets."""
        user_sid = current_user_sid() if os.name == "nt" else "S-1-5-18"
        layout = OwnedL2Layout(root=self.temp_dir, run_id="run-reg", user_sid=user_sid, container_sid="S-1-15-2-1")

        # False success prevented: failure to remove root raises TrustedLayoutError and leaves _cleaned False
        with patch.object(trusted_layout, "_reset_dacl_for_deletion", side_effect=OSError("dacl error")), \
             patch.object(trusted_layout.shutil, "rmtree", side_effect=PermissionError("locked")):
            with self.assertRaises(TrustedLayoutError):
                layout.cleanup()
            self.assertFalse(layout._cleaned)
            self.assertTrue(self.temp_dir.exists())

        # Setup KeyboardInterrupt preserved when cleanup raises SystemExit
        with patch.object(OwnedL2Layout, "_setup_directories_and_security", side_effect=KeyboardInterrupt("primary")), \
             patch.object(OwnedL2Layout, "cleanup", side_effect=SystemExit("cleanup")):
            with self.assertRaises(KeyboardInterrupt):
                OwnedL2Layout.create(self.temp_dir, "run-int", user_sid=user_sid, container_sid="S-1-15-2-1")

        # Secret redaction and cleanup error reporting in publish_atomic
        secret = "REVIEW_FAKE_TOKEN_9a4c"
        with patch.object(trusted_layout.os, "rename", side_effect=OSError("op " + secret)), \
             patch.object(Path, "unlink", side_effect=PermissionError("cleanup " + secret)):
            v_dir = self.temp_dir / "vault_err"
            v_dir.mkdir()
            with self.assertRaises(TrustedLayoutError) as caught:
                self.manifest.publish_atomic(v_dir)
            err_str = str(caught.exception)
            self.assertNotIn(secret, err_str)
            self.assertIn("[REDACTED]", err_str)
            self.assertIn("cleanup failed:", err_str)

    def test_r6_reparse_fail_closed_and_extra_everyone_ace_rejected(self) -> None:
        """R6: Reparse query failure fails closed; extra Everyone ACE on vault is rejected."""
        with patch.object(trusted_layout.os, "lstat", side_effect=PermissionError("query denied")):
            with self.assertRaises(TrustedLayoutSecurityError):
                trusted_layout._is_reparse_point(self.temp_dir)

        if os.name == "nt":
            user_sid = current_user_sid()
            layout = OwnedL2Layout.create(
                self.temp_dir, "run-r6", user_sid=user_sid, container_sid="S-1-15-2-12345"
            )
            try:
                # Add unauthorized Everyone Full Control ACE to vault
                trusted_layout._set_dacl_entries(
                    layout.vault_dir,
                    [(user_sid, trusted_layout._FILE_ALL_ACCESS, 3), ("S-1-1-0", trusted_layout._FILE_ALL_ACCESS, 3)],
                )
                with self.assertRaises(TrustedLayoutSecurityError):
                    layout._verify_security_configuration()
            finally:
                layout.cleanup()

    def test_r7_env_validation_relative_paths_and_bad_keys(self) -> None:
        """R7: Non-absolute paths, invalid key identifiers, and empty values are rejected."""
        with self.assertRaises(ValueError):
            build_trusted_environment("run-r7", "task-r7", temp_dir=Path("."))

        with self.assertRaises(ValueError):
            build_trusted_environment("run-r7", "task-r7", temp_dir=self.temp_dir, runtime_path=Path("rel-rt"))

        with self.assertRaises(ValueError):
            build_trusted_environment(
                "run-r7", "task-r7", temp_dir=self.temp_dir, extra_omp_keys={"OMP_BAD=EXTRA": "val"}
            )

        with self.assertRaises(ValueError):
            build_trusted_environment(
                "run-r7", "task-r7", temp_dir=self.temp_dir, extra_omp_keys={"OMP_EMPTY": ""}
            )

    def test_r8_high_delegation_boundary_validation(self) -> None:
        """R8: Parent delegation rejects failed native APIs and contradictory zero-test reports."""
        def fake_start(argv: list[str], cwd: Path, *, out_info: Any) -> Any:
            out_info.hProcess = 101
            out_info.hThread = 102
            out_info.dwProcessId = 999999
            Path(argv[-2]).write_text(
                json.dumps({
                    "success": True,
                    "tests_run": 0,
                    "failures": 7,
                    "errors": 8,
                    "integrity": "wrong",
                    "pid": 1,
                }),
                encoding="utf-8",
            )
            return out_info

        class FailedApi:
            def ResumeThread(self, thread: Any) -> int:
                return 0xFFFFFFFF
            def WaitForSingleObject(self, process: Any, timeout: int) -> int:
                return 258
            def GetExitCodeProcess(self, process: Any, output: Any) -> int:
                return 0

        mod = sys.modules[__name__]
        with patch.object(mod, "_ensure_controller_state_clean"), \
             patch.object(mod, "_user_sid", return_value="S-1-5-18"), \
             patch.object(mod, "_icacls"), \
             patch.object(mod, "_start_with_medium_token", side_effect=fake_start), \
             patch.object(mod, "_controller_kernel32", return_value=FailedApi()), \
             patch.object(mod, "_token_information", return_value={"integrity": MEDIUM_INTEGRITY_SID, "user": "S-1-5-18"}), \
             patch.object(mod, "_release_controller_handles"):
            with self.assertRaises((RuntimeError, OSError)):
                _delegate_native_controller()

    def test_r2a_root_handle_anchored_throughout_read(self) -> None:
        """R2a: Root handle identity is held open throughout read, blocking rename/swap races."""
        if os.name != "nt":
            return
        checkout = self.temp_dir / "checkout"
        other = self.temp_dir / "other"
        checkout.mkdir()
        other.mkdir()
        (checkout / "worker.py").write_bytes(b"code")
        (other / "worker.py").write_bytes(b"code")
        (checkout / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")
        (other / "result.json").write_text(json.dumps(dict(self.valid_artifact, status="REPLACED")), encoding="utf-8")

        # Verify that attempting to rename checkout while anchored fails or is detected
        k32 = trusted_layout._kernel32()
        h_dir = k32.CreateFileW(
            str(checkout),
            trusted_layout._GENERIC_READ,
            trusted_layout._FILE_SHARE_READ | trusted_layout._FILE_SHARE_WRITE,  # no FILE_SHARE_DELETE
            None,
            3,  # OPEN_EXISTING
            trusted_layout._FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        self.assertNotEqual(h_dir, trusted_layout._INVALID_HANDLE_VALUE)
        try:
            # While directory handle is open without FILE_SHARE_DELETE, Windows blocks rename/deletion
            with self.assertRaises(OSError):
                checkout.rename(self.temp_dir / "renamed_checkout")
        finally:
            k32.CloseHandle(h_dir)

    def test_r3a_missing_candidate_invalid_timeout_and_untyped_schema(self) -> None:
        """R3a: Missing candidate, NaN/bool/drift timeout, and untyped schema fields are rejected."""
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        # 1. Missing candidate file on disk
        manifest_missing = TrustedManifest(
            task_id="task-reg",
            run_id="run-reg",
            nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"missing").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "nonexistent_script.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            self._read_artifact("result.json", job_process=job, manifest=manifest_missing)
        self.assertIn("missing", str(caught.exception))

        # 2. Timeout validations: NaN, bool False, negative, changed lower
        for bad_t in (float("nan"), False, -1, 1.0):
            with self.subTest(bad_timeout=bad_t):
                with self.assertRaises(TrustedLayoutSecurityError):
                    self._read_artifact("result.json", job_process=job, actual_timeout=bad_t)

        # 3. Schema untyped failures / errors
        for bad_field, bad_val in (
            ("failures", "not-an-int"),
            ("errors", True),
            ("status", "PASS_WITH_FAILURES"),
        ):
            bad_art = dict(self.valid_artifact)
            bad_art[bad_field] = bad_val
            (self.temp_dir / "result.json").write_text(json.dumps(bad_art), encoding="utf-8")
            with self.subTest(bad_field=bad_field):
                with self.assertRaises(TrustedLayoutSecurityError):
                    self._read_artifact("result.json", job_process=job)
        (self.temp_dir / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")

    def test_r4a_publish_no_clobber_replaces_nothing(self) -> None:
        """R4a: Atomic publish never clobbers rival manifest and cleans up temp files."""
        vault = self.temp_dir / "vault_r4a"
        vault.mkdir()
        self.manifest.publish_atomic(vault)
        target = vault / "approved_manifest.json"
        original_bytes = target.read_bytes()

        rival = TrustedManifest(
            task_id="task-reg",
            run_id="run-reg",
            nonce="rival-nonce-0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"code").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "worker.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        with self.assertRaises(TrustedLayoutSecurityError):
            rival.publish_atomic(vault)

        # Target bytes strictly preserved
        self.assertEqual(target.read_bytes(), original_bytes)
        # Temp files cleaned up
        self.assertEqual(list(vault.glob(".tmp_manifest_*")), [])

        # Idempotent republish of same content succeeds
        same = self.manifest.publish_atomic(vault)
        self.assertEqual(same, target)
        self.assertEqual(target.read_bytes(), original_bytes)

    def test_r5a_exception_chain_sanitization_and_reset_dacl_error(self) -> None:
        """R5a: Cause/context chains are sanitized across all nodes; reset DACL errors fail-closed."""
        # 1. Chain sanitization across cause and context
        marker = "token=REVIEW_FAKE_SECRET_r5a"
        v_err = self.temp_dir / "vault_r5a_err"
        v_err.mkdir()
        with patch.object(trusted_layout.os, "rename", side_effect=OSError(marker)), \
             patch.object(Path, "unlink", side_effect=PermissionError("cleanup " + marker)):
            with self.assertRaises(TrustedLayoutError) as caught:
                self.manifest.publish_atomic(v_err)
            exc = caught.exception
            # Traverse chain
            stack = [exc]
            visited = set()
            all_strings = []
            while stack:
                node = stack.pop()
                if node is None or id(node) in visited:
                    continue
                visited.add(id(node))
                all_strings.append(str(node))
                stack.extend((node.__cause__, node.__context__))
            for s in all_strings:
                self.assertNotIn(marker, s)

        # 2. Reset DACL error leaves _cleaned False and raises TrustedLayoutError
        user_sid = current_user_sid() if os.name == "nt" else "S-1-5-18"
        layout = OwnedL2Layout(root=self.temp_dir / "layout_r5a", run_id="r5a", user_sid=user_sid, container_sid="S-1-15-2-1")
        (self.temp_dir / "layout_r5a").mkdir()
        with patch.object(trusted_layout, "_reset_dacl_for_deletion", side_effect=PermissionError("reset denied")):
            with self.assertRaises(TrustedLayoutError):
                layout.cleanup()
            self.assertFalse(layout._cleaned)

    def test_r6a_dacl_readback_enforces_mask_flags_and_type(self) -> None:
        """R6a: DACL verification enforces exact masks, inheritance flags, and allowed types."""
        if os.name != "nt":
            return
        user_sid = current_user_sid()
        layout = OwnedL2Layout.create(
            self.temp_dir, "run-r6a", user_sid=user_sid, container_sid="S-1-15-2-12345"
        )
        try:
            # 1. Modify container ACE on checkout to read-only (mask mismatch)
            trusted_layout._set_dacl_entries(
                layout.checkout_dir,
                [(user_sid, trusted_layout._FILE_ALL_ACCESS, 3), (layout.container_sid, trusted_layout._FILE_GENERIC_READ, 3)],
            )
            with self.assertRaises(TrustedLayoutSecurityError):
                layout._verify_security_configuration()

            # 2. Modify container ACE on checkout to remove inheritance flags (flags mismatch)
            trusted_layout._set_dacl_entries(
                layout.checkout_dir,
                [(user_sid, trusted_layout._FILE_ALL_ACCESS, 3), (layout.container_sid, trusted_layout._FILE_ALL_ACCESS, 0)],
            )
            with self.assertRaises(TrustedLayoutSecurityError):
                layout._verify_security_configuration()
        finally:
            layout.cleanup()

    def test_r7a_env_rejects_nonexistent_paths_and_path_injection(self) -> None:
        """R7a: Nonexistent temp_dir/runtime_path and semicolon injection are rejected."""
        # Nonexistent temp_dir
        with self.assertRaises(ValueError):
            build_trusted_environment("run-r7a", "task-r7a", temp_dir=self.temp_dir / "nonexistent-temp")

        # Nonexistent runtime_path
        with self.assertRaises(ValueError):
            build_trusted_environment(
                "run-r7a", "task-r7a", temp_dir=self.temp_dir, runtime_path=self.temp_dir / "nonexistent-runtime"
            )

        # Semicolon in runtime_path (PATH injection attempt)
        bad_rt = self.temp_dir / "approved;INJECTED"
        bad_rt.mkdir()
        with self.assertRaises(ValueError):
            build_trusted_environment("run-r7a", "task-r7a", temp_dir=self.temp_dir, runtime_path=bad_rt)

    def test_r8a_parent_controller_delegation_validates_raw_evidence_and_test_list(self) -> None:
        """R8a: Controller delegation rejects missing tests, missing skips, bool counters, or missing raw evidence."""
        def fake_start_missing_raw(argv: list[str], cwd: Path, *, out_info: Any) -> Any:
            out_info.hProcess, out_info.hThread, out_info.dwProcessId = 101, 102, 999999
            Path(argv[-2]).write_text(
                json.dumps({
                    "pid": 999999,
                    "integrity": MEDIUM_INTEGRITY_SID,
                    "success": True,
                    "tests_run": 3,
                    "failures": 0,
                    "errors": 0,
                    "skipped": [],
                    "user": "S-1-5-18",
                    "tests": _native_test_names(),
                    # missing "raw_evidence"
                }),
                encoding="utf-8",
            )
            return out_info

        class GoodApi:
            def ResumeThread(self, thread: Any) -> int:
                return 1
            def WaitForSingleObject(self, process: Any, timeout: int) -> int:
                return 0
            def GetExitCodeProcess(self, process: Any, output: Any) -> int:
                return 1

        mod = sys.modules[__name__]
        with patch.object(mod, "_ensure_controller_state_clean"), \
             patch.object(mod, "_user_sid", return_value="S-1-5-18"), \
             patch.object(mod, "_icacls"), \
             patch.object(mod, "_start_with_medium_token", side_effect=fake_start_missing_raw), \
             patch.object(mod, "_controller_kernel32", return_value=GoodApi()), \
             patch.object(mod, "_token_information", return_value={"integrity": MEDIUM_INTEGRITY_SID, "user": "S-1-5-18"}), \
             patch.object(mod, "_release_controller_handles"):
            with self.assertRaises(RuntimeError) as caught:
                _delegate_native_controller()
            self.assertIn("raw evidence", str(caught.exception))

    def test_r8b_native_controller_entrypoint_succeeds(self) -> None:
        """R8b: Native controller CLI entrypoint records exact tests, zero skips, and raw evidence without crash."""
        report_file = self.temp_dir / "ctrl_report.json"
        log_file = self.temp_dir / "ctrl.log"
        integrity = _token_information(os.getpid())["integrity"]
        if integrity == HIGH_INTEGRITY_SID:
            data = _delegate_native_controller()
        else:
            code = _run_native_controller(report_file, log_file)
            self.assertEqual(code, 0)
            self.assertTrue(report_file.exists())
            data = json.loads(report_file.read_text(encoding="utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(data["failures"], 0)
        self.assertEqual(data["errors"], 0)
        self.assertEqual(data["skipped"], [])
        self.assertEqual(sorted(data["tests"]), sorted(_native_test_names()))
        self.assertIn("raw_evidence", data)
        for tname in _native_test_names():
            self.assertIn(tname, data["raw_evidence"])

    def test_r3b_candidate_decoy_override_and_missing_execution_bindings_rejected(self) -> None:
        """R3b: Candidate decoy override and omitted execution bindings must be refused."""
        job = _DummyJobProcess(tree_stopped=True, active_processes=0)
        (self.temp_dir / "worker.py").write_bytes(b"APPROVED_WORKER_CODE")
        (self.temp_dir / "decoy.py").write_bytes(b"APPROVED_WORKER_CODE")
        (self.temp_dir / "result.json").write_text(json.dumps(self.valid_artifact), encoding="utf-8")

        manifest = TrustedManifest(
            task_id="task-reg",
            run_id="run-reg",
            nonce="0123456789abcdef0123456789abcdef",
            verifier_version="1.0.0",
            candidate_bytes_sha256=hashlib.sha256(b"APPROVED_WORKER_CODE").hexdigest(),
            canonical_tree_oid="a" * 40,
            tree_algorithm="sha1",
            approved_argv=["python", "-I", "-P", "worker.py"],
            timeout_seconds=10.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )

        valid_bindings = {
            "actual_argv": manifest.approved_argv,
            "actual_timeout": manifest.timeout_seconds,
            "actual_tree_oid": manifest.canonical_tree_oid,
            "actual_spec_hash": manifest.spec_hash,
        }

        # 1. Mutate worker.py on disk and attempt candidate_relpath override to decoy.py
        (self.temp_dir / "worker.py").write_bytes(b"MUTATED_UNAPPROVED_SCRIPT")
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(
                self.temp_dir, "result.json",
                job_process=job, manifest=manifest,
                candidate_relpath="decoy.py",
                **valid_bindings,
            )
        self.assertIn("does not match approved script", str(caught.exception))

        # Restore approved script
        (self.temp_dir / "worker.py").write_bytes(b"APPROVED_WORKER_CODE")

        # 2. Omitted execution bindings must be refused
        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(
                self.temp_dir, "result.json",
                job_process=job, manifest=manifest,
                actual_argv=None,
                actual_timeout=manifest.timeout_seconds,
                actual_tree_oid=manifest.canonical_tree_oid,
                actual_spec_hash=manifest.spec_hash,
            )
        self.assertIn("missing required execution binding: actual_argv", str(caught.exception))

        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(
                self.temp_dir, "result.json",
                job_process=job, manifest=manifest,
                actual_argv=manifest.approved_argv,
                actual_timeout=None,
                actual_tree_oid=manifest.canonical_tree_oid,
                actual_spec_hash=manifest.spec_hash,
            )
        self.assertIn("missing required execution binding: actual_timeout", str(caught.exception))

        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(
                self.temp_dir, "result.json",
                job_process=job, manifest=manifest,
                actual_argv=manifest.approved_argv,
                actual_timeout=manifest.timeout_seconds,
                actual_tree_oid=None,
                actual_spec_hash=manifest.spec_hash,
            )
        self.assertIn("missing required execution binding: actual_tree_oid", str(caught.exception))

        with self.assertRaises(TrustedLayoutSecurityError) as caught:
            read_trusted_artifact(
                self.temp_dir, "result.json",
                job_process=job, manifest=manifest,
                actual_argv=manifest.approved_argv,
                actual_timeout=manifest.timeout_seconds,
                actual_tree_oid=manifest.canonical_tree_oid,
                actual_spec_hash=None,
            )
        self.assertIn("missing required execution binding: actual_spec_hash", str(caught.exception))

        # 3. Successful read when all matching
        result = read_trusted_artifact(
            self.temp_dir, "result.json",
            job_process=job, manifest=manifest,
            **valid_bindings,
        )
        self.assertEqual(result["status"], "PASS")

    def test_r5b_bearer_and_interrupt_credential_sanitization_and_close_handle_preservation(self) -> None:
        """R5b: Bearer tokens and interrupts sanitized; close failures preserve interrupt type."""
        # 1. Bearer credential in publish exception chain
        vault = self.temp_dir / "vault_r5b"
        vault.mkdir()
        marker = "zQ7mP9rS2vN8xK4d"
        with patch.object(trusted_layout.os, "rename", side_effect=OSError("Authorization: Bearer " + marker)):
            with self.assertRaises(TrustedLayoutError) as caught:
                self.manifest.publish_atomic(vault)
            # Traverse chain and verify marker is absent
            stack = [caught.exception]
            seen = set()
            while stack:
                node = stack.pop()
                if node is None or id(node) in seen:
                    continue
                seen.add(id(node))
                for attr_val in (str(node), repr(getattr(node, "args", ())), str(getattr(node, "filename", None)), str(getattr(node, "strerror", None))):
                    self.assertNotIn(marker, attr_val)
                stack.extend((node.__cause__, node.__context__))

        # 2. KeyboardInterrupt with secret in publish
        with patch.object(trusted_layout.os, "rename", side_effect=KeyboardInterrupt("token=" + marker)):
            with self.assertRaises(KeyboardInterrupt) as caught_ki:
                self.manifest.publish_atomic(vault)
            self.assertNotIn(marker, str(caught_ki.exception))
            self.assertNotIn(marker, repr(caught_ki.exception.args))

        # 3. CloseHandle failure during KeyboardInterrupt preserves interrupt type on Windows
        if os.name == "nt":
            native = trusted_layout._kernel32()
            class InterruptedReadApi:
                failed_close = False
                def __getattr__(self, name: str) -> Any:
                    return getattr(native, name)
                def ReadFile(self, *args: Any) -> int:
                    raise KeyboardInterrupt("primary-interrupt")
                def CloseHandle(self, handle: Any) -> int:
                    actual = native.CloseHandle(handle)
                    if not self.failed_close:
                        self.failed_close = True
                        return 0
                    return actual

            checkout = self.temp_dir / "co_r5b"
            checkout.mkdir()
            (checkout / "a.json").write_bytes(b"DATA")
            with patch.object(trusted_layout, "_kernel32", return_value=InterruptedReadApi()):
                with self.assertRaises(KeyboardInterrupt) as caught_reader_ki:
                    trusted_layout._read_file_handle_windows(checkout / "a.json", checkout)
                self.assertIsInstance(caught_reader_ki.exception, KeyboardInterrupt)
                self.assertIsNotNone(caught_reader_ki.exception.__context__)
                self.assertIn("CloseHandle", str(caught_reader_ki.exception.__context__))

    def test_r5c_native_acl_reset_failure_marks_cleaned_false_and_raises(self) -> None:
        """R5c: Native DACL reset failure with error 5 leaves cleaned=False and raises."""
        if os.name != "nt":
            return
        native_acl = trusted_layout._advapi32()
        class FailedResetApi:
            def __getattr__(self, name: str) -> Any:
                return getattr(native_acl, name)
            def SetNamedSecurityInfoW(self, *args: Any) -> int:
                return 5  # ERROR_ACCESS_DENIED

        owned = self.temp_dir / "cleanup_r5c"
        owned.mkdir()
        layout = OwnedL2Layout(
            root=owned, run_id="r5c",
            user_sid=trusted_layout.current_user_sid(),
            container_sid="S-1-15-2-12345",
        )
        with patch.object(trusted_layout, "_advapi32", return_value=FailedResetApi()):
            with self.assertRaises(TrustedLayoutError) as caught:
                layout.cleanup()
            self.assertFalse(layout._cleaned)
            self.assertIn("code 5", str(caught.exception))

    def test_r6b_ancestor_traverse_dacl_enforces_exact_mask_and_flags(self) -> None:
        """R6b: Ancestor DACL must have traverse mask and flags 0; Full access is rejected."""
        if os.name != "nt":
            return
        layout = OwnedL2Layout.create(
            self.temp_dir, "run-r6b",
            user_sid=trusted_layout.current_user_sid(),
            container_sid="S-1-15-2-12345",
        )
        try:
            # Modify run_dir container ACE to FILE_ALL_ACCESS (not traverse)
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                    (layout.container_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                ],
            )
            with self.assertRaises(TrustedLayoutSecurityError) as caught:
                layout._verify_security_configuration()
            self.assertIn("container traverse ACE invalid", str(caught.exception))

            # Modify user ACE to have inheritance flags 3 instead of 0 on run_dir
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 3),
                    (layout.container_sid, trusted_layout._FILE_GENERIC_EXECUTE, 0),
                ],
            )
            with self.assertRaises(TrustedLayoutSecurityError) as caught:
                layout._verify_security_configuration()
            self.assertIn("user traverse ACE invalid", str(caught.exception))
        finally:
            layout.cleanup()

    def test_r8c_controller_delegation_rejects_null_or_unvalidated_raw_evidence(self) -> None:
        """R8c: Controller delegation rejects null or unvalidated per-test raw evidence."""
        expected = _native_test_names()
        caller_user = _user_sid()

        def fake_start_null_evidence(argv: Any, cwd: Any, *, out_info: Any) -> Any:
            out_info.hProcess, out_info.hThread, out_info.dwProcessId = 101, 102, 999999
            report = {
                "pid": 999999,
                "integrity": MEDIUM_INTEGRITY_SID,
                "user": caller_user,
                "success": True,
                "tests_run": len(expected),
                "failures": 0,
                "errors": 0,
                "skipped": [],
                "tests": expected,
                "raw_evidence": {name: None for name in expected},
            }
            Path(argv[-2]).write_text(json.dumps(report), encoding="utf-8")
            return out_info

        class GoodApi:
            def ResumeThread(self, thread: Any) -> int:
                return 1
            def WaitForSingleObject(self, process: Any, timeout: int) -> int:
                return 0
            def GetExitCodeProcess(self, process: Any, output: Any) -> int:
                return 1

        mod = sys.modules[__name__]
        with patch.object(mod, "_ensure_controller_state_clean"), \
             patch.object(mod, "_user_sid", return_value=caller_user), \
             patch.object(mod, "_icacls"), \
             patch.object(mod, "_start_with_medium_token", side_effect=fake_start_null_evidence), \
             patch.object(mod, "_controller_kernel32", return_value=GoodApi()), \
             patch.object(mod, "_token_information", return_value={"integrity": MEDIUM_INTEGRITY_SID, "user": caller_user}), \
             patch.object(mod, "_release_controller_handles"):
            with self.assertRaises(RuntimeError) as caught:
                _delegate_native_controller()
            self.assertIn("raw evidence is null", str(caught.exception))

    def test_r5d_reader_exception_sanitization_matrix_and_type_preservation(self) -> None:
        """R5d: Matrix of primary exceptions * close failures all preserve exception type and sanitize credentials."""
        if os.name != "nt":
            return
        native = trusted_layout._kernel32()
        checkout = self.temp_dir / "co_r5d"
        checkout.mkdir(exist_ok=True)
        target_file = checkout / "result.json"
        target_file.write_bytes(b"DATA")

        exceptions_matrix = [
            (KeyboardInterrupt, KeyboardInterrupt("Authorization: Bearer secret-ki-r5d-9876543210"), "secret-ki-r5d-9876543210"),
            (SystemExit, SystemExit("token=secret-se-r5d-9876543210"), "secret-se-r5d-9876543210"),
            (OSError, OSError("password: secret-os-r5d-9876543210"), "secret-os-r5d-9876543210"),
        ]
        close_modes = [
            ("close_success", False, False),
            ("file_close_fail", True, False),
            ("dir_close_fail", False, True),
            ("both_close_fail", True, True),
        ]

        for expected_type, primary_exc, marker in exceptions_matrix:
            for mode_name, fail_file, fail_dir in close_modes:
                with self.subTest(exc=expected_type.__name__, close_mode=mode_name):
                    file_handle_val = None
                    class MatrixApi:
                        def __getattr__(self, name: str) -> Any:
                            return getattr(native, name)
                        def ReadFile(self, *args: Any) -> int:
                            raise primary_exc
                        def CreateFileW(self, path_str: str, *args: Any) -> int:
                            nonlocal file_handle_val
                            h = native.CreateFileW(path_str, *args)
                            if str(target_file) in path_str and h != trusted_layout._INVALID_HANDLE_VALUE:
                                file_handle_val = h
                            return h
                        def CloseHandle(self, handle: Any) -> int:
                            is_file = (file_handle_val is not None and handle == file_handle_val)
                            if is_file and fail_file:
                                native.CloseHandle(handle)
                                return 0
                            if not is_file and fail_dir:
                                native.CloseHandle(handle)
                                return 0
                            return native.CloseHandle(handle)

                    with patch.object(trusted_layout, "_kernel32", return_value=MatrixApi()):
                        with self.assertRaises(expected_type) as caught:
                            trusted_layout._read_file_handle_windows(target_file, checkout)
                        self.assertIs(type(caught.exception), expected_type)
                        stack = [caught.exception]
                        seen = set()
                        while stack:
                            node = stack.pop()
                            if node is None or id(node) in seen:
                                continue
                            seen.add(id(node))
                            for attr_val in (
                                str(node),
                                repr(getattr(node, "args", ())),
                                str(getattr(node, "filename", None)),
                                str(getattr(node, "filename2", None)),
                                str(getattr(node, "strerror", None)),
                            ):
                                self.assertNotIn(marker, attr_val, f"Secret leaked in node {node!r} attribute {attr_val!r}")
                            stack.extend((node.__cause__, node.__context__))

    def test_r6c_ancestor_traverse_dacl_exact_mask_0x1200a0_and_reject_0x1200a9(self) -> None:
        """R6c: Ancestor container ACE must have exact mask 0x1200A0 and flags 0; 0x1200A9 and Full are rejected."""
        if os.name != "nt":
            return
        layout = OwnedL2Layout.create(
            self.temp_dir, "run-r6c",
            user_sid=trusted_layout.current_user_sid(),
            container_sid="S-1-15-2-12345",
        )
        try:
            # 1. Native query confirms exact mask 0x1200A0 and flags 0
            dacl = trusted_layout._query_dacl(layout.run_dir, layout.user_sid, layout.container_sid)
            container_aces = [ace for ace in dacl["aces"] if ace["sid"] == layout.container_sid]
            self.assertEqual(len(container_aces), 1)
            self.assertEqual(container_aces[0]["mask"], 0x1200A0)
            self.assertEqual(container_aces[0]["flags"], 0)

            # 2. Reject 0x1200A9 (_FILE_GENERIC_READ | _FILE_GENERIC_EXECUTE)
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                    (layout.container_sid, 0x1200A9, 0),
                ],
            )
            with self.assertRaises(TrustedLayoutSecurityError) as caught:
                layout._verify_security_configuration()
            self.assertIn("container traverse ACE invalid", str(caught.exception))

            # 3. Reject Full access 0x1F01FF
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                    (layout.container_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                ],
            )
            with self.assertRaises(TrustedLayoutSecurityError) as caught:
                layout._verify_security_configuration()
            self.assertIn("container traverse ACE invalid", str(caught.exception))

            # 4. Reject inheritance flags on ancestor container ACE
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                    (layout.container_sid, 0x1200A0, 1),
                ],
            )
            with self.assertRaises(TrustedLayoutSecurityError) as caught:
                layout._verify_security_configuration()
            self.assertIn("container traverse ACE invalid", str(caught.exception))

            # 5. Restoring exact 0x1200A0 with flags 0 passes
            trusted_layout._set_dacl_entries(
                layout.run_dir,
                [
                    (layout.user_sid, trusted_layout._FILE_ALL_ACCESS, 0),
                    (layout.container_sid, 0x1200A0, 0),
                ],
            )
            layout._verify_security_configuration()
        finally:
            layout.cleanup()

    def test_r8d_controller_delegation_field_by_field_mutation_matrix(self) -> None:
        """R8d: Field-by-field mutation matrix of raw evidence validates typed properties and invariants."""
        expected = _native_test_names()
        caller_user = _user_sid()

        valid_raw = {
            "test_native_l2_layout_and_os_access_denial": {
                "profile_sid": "S-1-15-2-123456789-0123456789",
                "status": "PASS",
                "test": "test_native_l2_layout_and_os_access_denial",
                "token_props": {
                    "in_any_job": True,
                    "capabilities": 0,
                    "appcontainer_sid": "S-1-15-2-123456789-0123456789",
                },
                "os_denials": {
                    "vault_read_denied": True,
                    "vault_write_denied": True,
                    "state_read_denied": True,
                    "state_write_denied": True,
                },
                "canary_hashes_matched": True,
            },
            "test_native_artifact_reader_with_job_proof": {
                "status": "PASS",
                "test": "test_native_artifact_reader_with_job_proof",
                "running_reader_refused": True,
                "stopped_reader_passed": True,
                "drift_reader_refused": True,
                "tree_oid": "a" * 40,
                "candidate_sha": "b" * 64,
                "manifest_identity": {
                    "tree_algorithm": "sha1",
                    "canonical_tree_oid": "a" * 40,
                    "candidate_bytes_sha256": "b" * 64,
                },
                "artifact_status": "PASS",
            },
            "test_native_descendant_blocks_reader_until_stopped": {
                "status": "PASS",
                "test": "test_native_descendant_blocks_reader_until_stopped",
                "child_marker_verified": True,
                "active_processes_alive": True,
                "reader_refused_while_descendant_active": True,
                "reader_passed_after_terminate_tree": True,
            },
        }

        class GoodApi:
            def ResumeThread(self, thread: Any) -> int:
                return 1
            def WaitForSingleObject(self, process: Any, timeout: int) -> int:
                return 0
            def GetExitCodeProcess(self, process: Any, output: Any) -> int:
                return 1

        mutations = [
            ("l2.sid newline", expected[0], ("token_props", "appcontainer_sid"), valid_raw[expected[0]]["profile_sid"] + "\n"),
            ("l2.sid mismatch", expected[0], ("token_props", "appcontainer_sid"), "S-1-15-2-987654321"),
            ("l2.profile missing", expected[0], ("profile_sid",), None),
            ("reader.tree newline", expected[1], ("tree_oid",), "a" * 40 + "\n"),
            ("reader.sha newline", expected[1], ("candidate_sha",), "b" * 64 + "\n"),
            ("reader.tree mismatch", expected[1], ("tree_oid",), "c" * 40),
            ("reader.sha mismatch", expected[1], ("candidate_sha",), "d" * 64),
            ("reader.manifest missing", expected[1], ("manifest_identity",), None),
            ("reader.algorithm mismatch", expected[1], ("manifest_identity", "tree_algorithm"), "sha256"),
            # L2 mutations
            ("l2.in_any_job string 'NO'", "test_native_l2_layout_and_os_access_denial", ("token_props", "in_any_job"), "NO"),
            ("l2.in_any_job bool False", "test_native_l2_layout_and_os_access_denial", ("token_props", "in_any_job"), False),
            ("l2.in_any_job int 1", "test_native_l2_layout_and_os_access_denial", ("token_props", "in_any_job"), 1),
            ("l2.capabilities bool False", "test_native_l2_layout_and_os_access_denial", ("token_props", "capabilities"), False),
            ("l2.capabilities int 1", "test_native_l2_layout_and_os_access_denial", ("token_props", "capabilities"), 1),
            ("l2.capabilities None", "test_native_l2_layout_and_os_access_denial", ("token_props", "capabilities"), None),
            ("l2.appcontainer_sid malformed", "test_native_l2_layout_and_os_access_denial", ("token_props", "appcontainer_sid"), "not-a-sid"),
            ("l2.appcontainer_sid standard user SID", "test_native_l2_layout_and_os_access_denial", ("token_props", "appcontainer_sid"), "S-1-5-18"),
            ("l2.vault_read_denied False", "test_native_l2_layout_and_os_access_denial", ("os_denials", "vault_read_denied"), False),
            ("l2.vault_write_denied False", "test_native_l2_layout_and_os_access_denial", ("os_denials", "vault_write_denied"), False),
            ("l2.state_read_denied False", "test_native_l2_layout_and_os_access_denial", ("os_denials", "state_read_denied"), False),
            ("l2.state_write_denied False", "test_native_l2_layout_and_os_access_denial", ("os_denials", "state_write_denied"), False),
            ("l2.canary_hashes_matched False", "test_native_l2_layout_and_os_access_denial", ("canary_hashes_matched",), False),
            # Reader mutations
            ("reader.running_reader_refused 'NO'", "test_native_artifact_reader_with_job_proof", ("running_reader_refused",), "NO"),
            ("reader.stopped_reader_passed False", "test_native_artifact_reader_with_job_proof", ("stopped_reader_passed",), False),
            ("reader.drift_reader_refused False", "test_native_artifact_reader_with_job_proof", ("drift_reader_refused",), False),
            ("reader.tree_oid invalid", "test_native_artifact_reader_with_job_proof", ("tree_oid",), "invalid"),
            ("reader.tree_oid 39 chars", "test_native_artifact_reader_with_job_proof", ("tree_oid",), "a" * 39),
            ("reader.candidate_sha invalid", "test_native_artifact_reader_with_job_proof", ("candidate_sha",), "invalid"),
            ("reader.candidate_sha 63 chars", "test_native_artifact_reader_with_job_proof", ("candidate_sha",), "b" * 63),
            ("reader.artifact_status 'FAIL'", "test_native_artifact_reader_with_job_proof", ("artifact_status",), "FAIL"),
            # Descendant mutations
            ("descendant.child_marker_verified False", "test_native_descendant_blocks_reader_until_stopped", ("child_marker_verified",), False),
            ("descendant.active_processes_alive 'NO'", "test_native_descendant_blocks_reader_until_stopped", ("active_processes_alive",), "NO"),
            ("descendant.reader_refused_while_descendant_active False", "test_native_descendant_blocks_reader_until_stopped", ("reader_refused_while_descendant_active",), False),
            ("descendant.reader_passed_after_terminate_tree False", "test_native_descendant_blocks_reader_until_stopped", ("reader_passed_after_terminate_tree",), False),
        ]

        for desc, test_key, path_keys, mutated_val in mutations:
            with self.subTest(mutation=desc):
                # Deep copy raw
                mutated_raw = json.loads(json.dumps(valid_raw))
                target_dict = mutated_raw[test_key]
                for p in path_keys[:-1]:
                    target_dict = target_dict[p]
                target_dict[path_keys[-1]] = mutated_val

                def make_fake_start(raw_dict: dict[str, Any]) -> Any:
                    def fake_start(argv: Any, cwd: Any, *, out_info: Any) -> Any:
                        out_info.hProcess, out_info.hThread, out_info.dwProcessId = 101, 102, 999999
                        report = {
                            "pid": 999999,
                            "integrity": MEDIUM_INTEGRITY_SID,
                            "user": caller_user,
                            "success": True,
                            "tests_run": len(expected),
                            "failures": 0,
                            "errors": 0,
                            "skipped": [],
                            "tests": expected,
                            "raw_evidence": raw_dict,
                        }
                        Path(argv[-2]).write_text(json.dumps(report), encoding="utf-8")
                        return out_info
                    return fake_start

                mod = sys.modules[__name__]
                with patch.object(mod, "_ensure_controller_state_clean"), \
                     patch.object(mod, "_user_sid", return_value=caller_user), \
                     patch.object(mod, "_icacls"), \
                     patch.object(mod, "_start_with_medium_token", side_effect=make_fake_start(mutated_raw)), \
                     patch.object(mod, "_controller_kernel32", return_value=GoodApi()), \
                     patch.object(mod, "_token_information", return_value={"integrity": MEDIUM_INTEGRITY_SID, "user": caller_user}), \
                     patch.object(mod, "_release_controller_handles"):
                    with self.assertRaises(RuntimeError):
                        _delegate_native_controller()

        # Valid report passes
        def fake_start_valid(argv: Any, cwd: Any, *, out_info: Any) -> Any:
            out_info.hProcess, out_info.hThread, out_info.dwProcessId = 101, 102, 999999
            report = {
                "pid": 999999,
                "integrity": MEDIUM_INTEGRITY_SID,
                "user": caller_user,
                "success": True,
                "tests_run": len(expected),
                "failures": 0,
                "errors": 0,
                "skipped": [],
                "tests": expected,
                "raw_evidence": valid_raw,
            }
            Path(argv[-2]).write_text(json.dumps(report), encoding="utf-8")
            return out_info

        mod = sys.modules[__name__]
        with patch.object(mod, "_ensure_controller_state_clean"), \
             patch.object(mod, "_user_sid", return_value=caller_user), \
             patch.object(mod, "_icacls"), \
             patch.object(mod, "_start_with_medium_token", side_effect=fake_start_valid), \
             patch.object(mod, "_controller_kernel32", return_value=GoodApi()), \
             patch.object(mod, "_token_information", return_value={"integrity": MEDIUM_INTEGRITY_SID, "user": caller_user}), \
             patch.object(mod, "_release_controller_handles") as release:
            rep = _delegate_native_controller()
            self.assertTrue(rep["success"])
            self.assertIs(release.call_args.kwargs["terminate"], False)
            release.reset_mock()
            with patch.object(GoodApi, "WaitForSingleObject", return_value=258):
                with self.assertRaisesRegex(RuntimeError, "wait failed or timed out"):
                    _delegate_native_controller()
            self.assertIs(release.call_args.kwargs["terminate"], True)
            release.reset_mock()
            with patch.object(mod, "_token_information", side_effect=OSError("token query failed")):
                with self.assertRaisesRegex(OSError, "token query failed"):
                    _delegate_native_controller()
            self.assertIs(release.call_args.kwargs["terminate"], True)


def _native_test_names() -> list[str]:
    return [
        "test_native_l2_layout_and_os_access_denial",
        "test_native_artifact_reader_with_job_proof",
        "test_native_descendant_blocks_reader_until_stopped",
    ]


def _run_native_controller(report_path: Path, log_path: Path) -> int:
    """Entry point for the delegated Medium integrity controller."""
    global _IN_NATIVE_CONTROLLER
    identity = _token_information(os.getpid())
    integrity = str(identity["integrity"])
    if integrity != MEDIUM_INTEGRITY_SID:
        raise RuntimeError(f"native controller needs medium integrity, got {integrity}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous = _IN_NATIVE_CONTROLLER
    _IN_NATIVE_CONTROLLER = True
    test_names = list(_native_test_names())
    try:
        with log_path.open("w", encoding="utf-8") as stream:
            suite = unittest.TestSuite()
            for name in test_names:
                suite.addTest(TrustedLayoutNativeTests(name))
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    finally:
        _IN_NATIVE_CONTROLLER = previous

    raw_evidence: dict[str, Any] = {}
    for name in test_names:
        evidence = getattr(TrustedLayoutNativeTests, f"_evidence_{name}", None)
        if evidence is None:
            raise RuntimeError(f"missing raw evidence observation for test {name}")
        raw_evidence[name] = evidence

    report = {
        "pid": int(os.getpid()),
        "integrity": integrity,
        "user": current_user_sid(),
        "tests_run": int(result.testsRun),
        "failures": int(len(result.failures)),
        "errors": int(len(result.errors)),
        "skipped": [str(t) for t, _ in result.skipped],
        "tests": test_names,
        "raw_evidence": raw_evidence,
        "success": bool(result.wasSuccessful()),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def _delegate_native_controller() -> dict[str, object]:
    """Run native tests in an owned Medium child when current process is High."""
    _ensure_controller_state_clean()
    expected = _native_test_names()
    caller_user = _user_sid()
    kernel32 = _controller_kernel32()
    with tempfile.TemporaryDirectory(prefix="omp-b1b-controller-") as directory:
        _icacls(
            str(directory), "/inheritance:r",
            "/grant:r", f"*{caller_user}:(OI)(CI)(F)",
        )
        report_path = Path(directory) / "native-report.json"
        log_path = Path(directory) / "native-controller.log"
        info = _PROCESS_INFORMATION()
        pid = 0
        process_exited = False
        try:
            info = _start_with_medium_token(
                [
                    sys.executable, "-B", "-m", "tests.test_trusted_layout",
                    "--native-controller", str(report_path), str(log_path),
                ],
                REPO_ROOT,
                out_info=info,
            )
            pid = int(info.dwProcessId)
            token = _token_information(pid)
            if str(token.get("integrity")) != MEDIUM_INTEGRITY_SID:
                raise RuntimeError(f"controller token is not medium: {token}")
            if str(token.get("user")) != caller_user:
                raise RuntimeError(f"controller token user {token.get('user')} != {caller_user}")

            # Resume child controller
            res = kernel32.ResumeThread(info.hThread)
            if res == 0xFFFFFFFF or res == -1:
                raise OSError(ctypes.get_last_error(), "cannot resume the medium native controller")

            wait_res = kernel32.WaitForSingleObject(info.hProcess, 60000)
            if wait_res != 0:
                raise RuntimeError(f"medium native controller wait failed or timed out: {wait_res}")
            process_exited = True

            exit_code = wintypes.DWORD()
            get_exit_ok = kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
            if not get_exit_ok:
                raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
            if exit_code.value != 0:
                raise RuntimeError(f"medium controller failed with exit code {exit_code.value}")
            if not report_path.exists():
                raise RuntimeError("medium controller did not produce report")

            report = json.loads(report_path.read_text(encoding="utf-8"))
            problems: list[str] = []
            if report.get("pid") != pid:
                problems.append(f"report pid {report.get('pid')} != {pid}")
            if str(report.get("integrity")) != MEDIUM_INTEGRITY_SID:
                problems.append(f"report integrity {report.get('integrity')}")
            if report.get("user") != caller_user:
                problems.append(f"report user {report.get('user')} != {caller_user}")
            if report.get("success") is not True:
                problems.append("report success is not True")
            if type(report.get("tests_run")) is not int or report.get("tests_run") != len(expected):
                problems.append(f"tests_run {report.get('tests_run')} != expected {len(expected)}")
            if type(report.get("failures")) is not int or report.get("failures") != 0:
                problems.append(f"failures {report.get('failures')}")
            if type(report.get("errors")) is not int or report.get("errors") != 0:
                problems.append(f"errors {report.get('errors')}")
            if not isinstance(report.get("skipped"), list) or len(report.get("skipped")) != 0:
                problems.append(f"skipped {report.get('skipped')}")
            if not isinstance(report.get("tests"), list) or sorted(report.get("tests")) != sorted(expected):
                problems.append(f"tests list {report.get('tests')} != expected {expected}")
            if not isinstance(report.get("raw_evidence"), dict):
                problems.append("report missing raw evidence dict")
            else:
                raw_ev = report["raw_evidence"]
                for name in expected:
                    entry = raw_ev.get(name)
                    if entry is None or not isinstance(entry, dict):
                        problems.append(f"test {name} raw evidence is null or not a dict")
                        continue
                    if entry.get("status") != "PASS" or entry.get("test") != name:
                        problems.append(f"test {name} invalid status/test in raw evidence: {entry}")
                    if name == "test_native_l2_layout_and_os_access_denial":
                        token_p = entry.get("token_props")
                        if not isinstance(token_p, dict):
                            problems.append("l2 test missing token_props dict")
                        else:
                            if token_p.get("in_any_job") is not True:
                                problems.append(f"l2 test in_any_job is not True: {token_p.get('in_any_job')!r}")
                            cap = token_p.get("capabilities")
                            if type(cap) is not int or cap != 0:
                                problems.append(f"l2 test capabilities {cap!r} is not strict int 0")
                            sid = token_p.get("appcontainer_sid")
                            if not isinstance(sid, str) or not re.fullmatch(r"S-1-15-2(?:-[0-9]+)+", sid):
                                problems.append(f"l2 test appcontainer_sid invalid format: {sid!r}")
                            profile_sid = entry.get("profile_sid")
                            if not isinstance(profile_sid, str) or sid != profile_sid:
                                problems.append("l2 test token SID does not match owned profile SID")
                        denials = entry.get("os_denials")
                        if not isinstance(denials, dict):
                            problems.append("l2 test missing os_denials dict")
                        else:
                            for k in ("vault_read_denied", "vault_write_denied", "state_read_denied", "state_write_denied"):
                                if denials.get(k) is not True:
                                    problems.append(f"l2 test OS denial {k} is not True: {denials.get(k)!r}")
                        if entry.get("canary_hashes_matched") is not True:
                            problems.append(f"l2 test canary_hashes_matched is not True: {entry.get('canary_hashes_matched')!r}")
                    elif name == "test_native_artifact_reader_with_job_proof":
                        for flag in ("running_reader_refused", "stopped_reader_passed", "drift_reader_refused"):
                            if entry.get(flag) is not True:
                                problems.append(f"artifact reader test flag {flag} is not True: {entry.get(flag)!r}")
                        tree_oid = entry.get("tree_oid")
                        if not isinstance(tree_oid, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", tree_oid):
                            problems.append(f"artifact reader test tree_oid invalid format: {tree_oid!r}")
                        cand_sha = entry.get("candidate_sha")
                        if not isinstance(cand_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", cand_sha):
                            problems.append(f"artifact reader test candidate_sha invalid format: {cand_sha!r}")
                        identity = entry.get("manifest_identity")
                        if not isinstance(identity, dict):
                            problems.append("artifact reader test missing approved manifest identity")
                        else:
                            algorithm = identity.get("tree_algorithm")
                            oid_length = {"sha1": 40, "sha256": 64}.get(algorithm) if isinstance(algorithm, str) else None
                            if oid_length is None or not isinstance(tree_oid, str) or len(tree_oid) != oid_length:
                                problems.append("artifact reader test tree algorithm/length mismatch")
                            if tree_oid != identity.get("canonical_tree_oid"):
                                problems.append("artifact reader test tree does not match approved manifest")
                            if cand_sha != identity.get("candidate_bytes_sha256"):
                                problems.append("artifact reader test candidate does not match approved manifest")
                        art_status = entry.get("artifact_status")
                        if art_status not in ("PASS", "success"):
                            problems.append(f"artifact reader test artifact_status invalid: {art_status!r}")
                    elif name == "test_native_descendant_blocks_reader_until_stopped":
                        for flag in (
                            "child_marker_verified",
                            "active_processes_alive",
                            "reader_refused_while_descendant_active",
                            "reader_passed_after_terminate_tree",
                        ):
                            if entry.get(flag) is not True:
                                problems.append(f"descendant test flag {flag} is not True: {entry.get(flag)!r}")
            if problems:
                raise RuntimeError(
                    "medium native controller did not produce valid evidence: " + "; ".join(problems)
                )
            return report
        finally:
            _release_controller_handles(kernel32, info, terminate=not process_exited)


@unittest.skipUnless(os.name == "nt", "Native execution requires Windows")
class TrustedLayoutNativeTests(unittest.TestCase):
    """B1b-6: Native Windows execution, AppContainer isolation, and OS access denial."""

    runtime: Path | None = None
    fixture_dir: tempfile.TemporaryDirectory | None = None
    user_sid: str = ""
    delegated: bool = False
    child_verified: bool = False

    @classmethod
    def setUpClass(cls) -> None:
        integrity = str(_token_information(os.getpid())["integrity"])
        if integrity == HIGH_INTEGRITY_SID:
            report = _delegate_native_controller()
            cls.delegated = True
            cls.child_verified = bool(report.get("success", False))
            return
        if integrity != MEDIUM_INTEGRITY_SID:
            raise RuntimeError(f"native tests require medium integrity, got {integrity}")

        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.fixture_dir = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT, prefix="b1b_rt_")
        cls.user_sid = current_user_sid()
        _icacls(
            cls.fixture_dir.name, "/inheritance:r",
            "/grant:r", f"*{cls.user_sid}:(OI)(CI)(F)",
        )
        cls.runtime = _prepare_runtime(Path(sys.executable), Path(cls.fixture_dir.name) / "runtime")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.fixture_dir is not None:
            cls.fixture_dir.cleanup()

    def _delegated(self) -> bool:
        cls = type(self)
        if not cls.delegated:
            return False
        self.assertTrue(cls.child_verified, "Medium controller evidence was not verified")
        return True

    def setUp(self) -> None:
        if type(self).delegated:
            return
        self.scratch = SCRATCH_ROOT / f"native_{secrets.token_hex(4)}"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.profile: WindowsAppContainerProfile | None = None
        self.layout: OwnedL2Layout | None = None

    def tearDown(self) -> None:
        if type(self).delegated:
            return
        if self.profile is not None and not self.profile.closed:
            try:
                self.profile.close()
            except Exception:
                pass
        if self.layout is not None:
            try:
                self.layout.cleanup()
            except Exception:
                pass
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_native_l2_layout_and_os_access_denial(self) -> None:
        """Verify real Worker running in AppContainer cannot read/write vault or state."""
        if self._delegated():
            return
        profile_name = f"OMP.B1B.{secrets.token_hex(4)}"
        self.profile = WindowsAppContainerProfile.create(profile_name)

        # Grant container read on runtime fixture
        assert self.runtime is not None
        assert self.fixture_dir is not None
        _icacls(self.fixture_dir.name, "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")
        _icacls(str(self.runtime.parent), "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")

        self.layout = OwnedL2Layout.create(
            self.scratch, "run-nat-01", user_sid=self.user_sid, container_sid=self.profile.sid
        )

        # Place canaries in vault and state
        vault_canary = self.layout.vault_dir / "vault_canary.txt"
        state_canary = self.layout.state_dir / "state_canary.txt"
        vault_canary.write_text("VAULT_SECRET_CANARY_01\n", encoding="utf-8")
        state_canary.write_text("STATE_SECRET_CANARY_01\n", encoding="utf-8")

        vault_hash_before = _sha256(vault_canary)
        state_hash_before = _sha256(state_canary)

        # Create worker script in checkout
        worker_script = self.layout.checkout_dir / "worker.py"
        worker_script.write_text(
            """
import json, os, sys

def try_read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {"ok": True, "data": f.read()}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}

def try_write(path, data):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(data)
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}

vault_path = os.environ["OMP_CANARY_VAULT"]
state_path = os.environ["OMP_CANARY_STATE"]

# Check runtime flags
isolated_flag = getattr(sys.flags, "isolated", 0)
safe_path_flag = getattr(sys.flags, "safe_path", 0)

# Check dangerous env keys and parent secrets absent
dangerous_leaks = [k for k in ("PYTHONPATH", "PYTHONHOME", "COMSPEC", "SHELL", "LD_PRELOAD") if k in os.environ]
fake_secret_leaked = "FAKE_PARENT_SECRET_987" in os.environ

# 1. Allowed I/O in checkout
checkout_write = try_write("output.txt", "WORKER_DATA")
checkout_read = try_read("output.txt")

# 2. Denied I/O on vault and state
vault_r = try_read(vault_path)
vault_w = try_write(vault_path, "TAMPER")
state_r = try_read(state_path)
state_w = try_write(state_path, "TAMPER")

report = {
    "sys_flags": {"isolated": isolated_flag, "safe_path": safe_path_flag},
    "dangerous_leaks": dangerous_leaks,
    "fake_secret_leaked": fake_secret_leaked,
    "checkout_write": checkout_write,
    "checkout_read": checkout_read,
    "vault_read": vault_r,
    "vault_write": vault_w,
    "state_read": state_r,
    "state_write": state_w,
}

with open("worker_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f)
""",
            encoding="utf-8",
        )

        env = build_trusted_environment(
            "run-nat-01",
            "task-nat-01",
            temp_dir=self.layout.temp_dir,
            runtime_path=self.runtime.parent,
            local_app_data=self.profile.folder,
            extra_omp_keys={
                "OMP_CANARY_VAULT": str(vault_canary),
                "OMP_CANARY_STATE": str(state_canary),
            },
        )

        argv = [str(self.runtime), "-I", "-P", "worker.py"]
        log_path = self.layout.log_dir / "worker.log"

        os.environ["FAKE_PARENT_SECRET_987"] = "leak_check_999"
        try:
            proc = WindowsJobProcess.start(
                argv=argv,
                cwd=self.layout.checkout_dir,
                log_path=log_path,
                env=env,
                app_container=self.profile,
                temp_dir=self.layout.temp_dir,
            )
            try:
                # Query worker token while process is live
                worker_token = _token_information(proc.pid)
                self.assertEqual(worker_token.get("integrity"), LOW_INTEGRITY_SID)
                self.assertEqual(worker_token.get("is_container"), 1)
                self.assertEqual(worker_token.get("appcontainer_sid"), self.profile.sid)
                self.assertEqual(worker_token.get("capabilities"), 0)
                self.assertTrue(worker_token.get("in_any_job"))

                proc.resume()
                exit_code = proc.wait(timeout=15.0)
                self.assertEqual(exit_code, 0)
                self.assertTrue(proc.tree_stopped)
            finally:
                proc.close()
        finally:
            os.environ.pop("FAKE_PARENT_SECRET_987", None)

        # Verify worker report
        report_file = self.layout.checkout_dir / "worker_report.json"
        self.assertTrue(report_file.exists())
        report = json.loads(report_file.read_text(encoding="utf-8"))

        # Verify worker runtime flags and clean environment
        self.assertEqual(report["sys_flags"]["isolated"], 1)
        self.assertEqual(report["sys_flags"]["safe_path"], 1)
        self.assertEqual(report["dangerous_leaks"], [])
        self.assertFalse(report["fake_secret_leaked"])

        # Checkout access succeeded
        self.assertTrue(report["checkout_write"]["ok"])
        self.assertTrue(report["checkout_read"]["ok"])

        # Vault and state access strictly DENIED by the OS!
        self.assertFalse(report["vault_read"]["ok"])
        self.assertEqual(report["vault_read"]["error"], "PermissionError")
        self.assertFalse(report["vault_write"]["ok"])
        self.assertEqual(report["vault_write"]["error"], "PermissionError")
        self.assertFalse(report["state_read"]["ok"])
        self.assertEqual(report["state_read"]["error"], "PermissionError")
        self.assertFalse(report["state_write"]["ok"])
        self.assertEqual(report["state_write"]["error"], "PermissionError")

        # Verify positive same-user control on the exact canaries: controller reads, modifies, restores
        vault_orig = vault_canary.read_text(encoding="utf-8")
        self.assertIn("VAULT_SECRET_CANARY", vault_orig)
        vault_canary.write_text(vault_orig + "CONTROLLER_WRITE\n", encoding="utf-8")
        self.assertIn("CONTROLLER_WRITE", vault_canary.read_text(encoding="utf-8"))
        vault_canary.write_text(vault_orig, encoding="utf-8")

        state_orig = state_canary.read_text(encoding="utf-8")
        self.assertIn("STATE_SECRET_CANARY", state_orig)
        state_canary.write_text(state_orig + "CONTROLLER_WRITE\n", encoding="utf-8")
        self.assertIn("CONTROLLER_WRITE", state_canary.read_text(encoding="utf-8"))
        state_canary.write_text(state_orig, encoding="utf-8")

        # Verify canary hashes remain strictly unchanged
        self.assertEqual(_sha256(vault_canary), vault_hash_before)
        self.assertEqual(_sha256(state_canary), state_hash_before)

        TrustedLayoutNativeTests._evidence_test_native_l2_layout_and_os_access_denial = {
            "status": "PASS",
            "test": "test_native_l2_layout_and_os_access_denial",
            "profile_sid": self.profile.sid,
            "token_props": {
                "appcontainer_sid": worker_token["appcontainer_sid"],
                "capabilities": worker_token["capabilities"],
                "in_any_job": worker_token["in_any_job"],
            },
            "os_denials": {
                "vault_read_denied": True,
                "vault_write_denied": True,
                "state_read_denied": True,
                "state_write_denied": True,
            },
            "canary_hashes_matched": True,
        }

    def test_native_artifact_reader_with_job_proof(self) -> None:
        """Verify real artifact reader reading worker output after job stop."""
        if self._delegated():
            return
        profile_name = f"OMP.B1B.{secrets.token_hex(4)}"
        self.profile = WindowsAppContainerProfile.create(profile_name)

        assert self.runtime is not None
        assert self.fixture_dir is not None
        _icacls(self.fixture_dir.name, "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")
        _icacls(str(self.runtime.parent), "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")

        self.layout = OwnedL2Layout.create(
            self.scratch, "run-nat-02", user_sid=self.user_sid, container_sid=self.profile.sid
        )

        # Initialize real disposable Git repository in checkout_dir
        subprocess.run(["git", "init"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "OMP Test"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@omp.internal"], cwd=self.layout.checkout_dir, check=True, capture_output=True)

        nonce = secrets.token_hex(16)
        # Worker script writes valid artifact
        worker_script = self.layout.checkout_dir / "worker.py"
        worker_script.write_text(
            f"""
import json
artifact = {{
    "task_id": "task-nat-02",
    "run_id": "run-nat-02",
    "nonce": "{nonce}",
    "status": "PASS",
    "tests_run": 10
}}
with open("result.json", "w", encoding="utf-8") as f:
    json.dump(artifact, f)
""",
            encoding="utf-8",
        )

        # Compute real candidate hash and real Git tree OID
        subprocess.run(["git", "add", "worker.py"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        tree_oid = subprocess.check_output(["git", "write-tree"], cwd=self.layout.checkout_dir, text=True).strip()
        cand_bytes = worker_script.read_bytes()
        cand_sha = hashlib.sha256(cand_bytes).hexdigest()

        manifest = TrustedManifest(
            task_id="task-nat-02",
            run_id="run-nat-02",
            nonce=nonce,
            verifier_version="1.0.0",
            candidate_bytes_sha256=cand_sha,
            canonical_tree_oid=tree_oid,
            tree_algorithm="sha1",
            approved_argv=[str(self.runtime), "-I", "-P", "worker.py"],
            timeout_seconds=15.0,
            spec_hash=hashlib.sha256(b"spec").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        manifest.publish_atomic(self.layout.vault_dir)

        env = build_trusted_environment(
            "run-nat-02", "task-nat-02",
            temp_dir=self.layout.temp_dir,
            runtime_path=self.runtime.parent,
            local_app_data=self.profile.folder,
        )
        log_path = self.layout.log_dir / "worker.log"

        proc = WindowsJobProcess.start(
            argv=[str(self.runtime), "-I", "-P", "worker.py"],
            cwd=self.layout.checkout_dir,
            log_path=log_path,
            env=env,
            app_container=self.profile,
            temp_dir=self.layout.temp_dir,
        )
        bindings = {
            "actual_argv": manifest.approved_argv,
            "actual_timeout": manifest.timeout_seconds,
            "actual_tree_oid": manifest.canonical_tree_oid,
            "actual_spec_hash": manifest.spec_hash,
        }
        try:
            # While process is running (not stopped), reader MUST refuse
            with self.assertRaises(TrustedLayoutSecurityError):
                read_trusted_artifact(
                    self.layout.checkout_dir, "result.json", job_process=proc, manifest=manifest, **bindings
                )
            proc.resume()
            proc.wait(timeout=10.0)
            self.assertTrue(proc.tree_stopped)

            # After tree stopped, reader succeeds
            artifact = read_trusted_artifact(
                self.layout.checkout_dir, "result.json", job_process=proc, manifest=manifest, **bindings
            )
            self.assertEqual(artifact["status"], "PASS")
            self.assertEqual(artifact["tests_run"], 10)

            # Candidate drift verification: mutating candidate on disk must be rejected
            worker_script.write_bytes(b"MUTATED_UNAPPROVED_CANDIDATE")
            with self.assertRaises(TrustedLayoutSecurityError):
                read_trusted_artifact(
                    self.layout.checkout_dir, "result.json", job_process=proc, manifest=manifest, **bindings
                )

            TrustedLayoutNativeTests._evidence_test_native_artifact_reader_with_job_proof = {
                "status": "PASS",
                "test": "test_native_artifact_reader_with_job_proof",
                "tree_oid": tree_oid,
                "candidate_sha": cand_sha,
                "manifest_identity": {
                    "tree_algorithm": manifest.tree_algorithm,
                    "canonical_tree_oid": manifest.canonical_tree_oid,
                    "candidate_bytes_sha256": manifest.candidate_bytes_sha256,
                },
                "running_reader_refused": True,
                "stopped_reader_passed": True,
                "drift_reader_refused": True,
                "artifact_status": artifact["status"],
            }
        finally:
            proc.close()

    def test_native_descendant_blocks_reader_until_stopped(self) -> None:
        """Verify background descendant keeps Job alive and blocks artifact reading."""
        if self._delegated():
            return
        profile_name = f"OMP.B1B.DESC.{secrets.token_hex(4)}"
        self.profile = WindowsAppContainerProfile.create(profile_name)

        assert self.runtime is not None
        assert self.fixture_dir is not None
        _icacls(self.fixture_dir.name, "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")
        _icacls(str(self.runtime.parent), "/grant:r", f"*{self.profile.sid}:(OI)(CI)(RX)")

        self.layout = OwnedL2Layout.create(
            self.scratch, "run-nat-03", user_sid=self.user_sid, container_sid=self.profile.sid
        )

        subprocess.run(["git", "init"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "OMP Test"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@omp.internal"], cwd=self.layout.checkout_dir, check=True, capture_output=True)

        nonce = secrets.token_hex(16)
        worker_script = self.layout.checkout_dir / "worker.py"
        worker_script.write_text(
            f"""
import json, os, subprocess, sys, time
artifact = {{
    "task_id": "task-nat-03",
    "run_id": "run-nat-03",
    "nonce": "{nonce}",
    "status": "PASS",
    "tests_run": 5
}}
with open("result.json", "w", encoding="utf-8") as f:
    json.dump(artifact, f)

# Spawn descendant child process in same Job Object that writes its own readiness marker and sleeps
child_code = "import time; open('child_ready.txt', 'w', encoding='utf-8').write('READY'); time.sleep(60)"
child = subprocess.Popen([sys.executable, "-c", child_code])

# Wait until child has written its readiness marker to prove descendant is running
start_t = time.monotonic()
while not os.path.exists("child_ready.txt") and time.monotonic() - start_t < 10.0:
    time.sleep(0.05)

# Signal root ready and sleep until job tree termination
with open("root_ready.txt", "w", encoding="utf-8") as f:
    f.write("READY")

time.sleep(60)
""",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "worker.py"], cwd=self.layout.checkout_dir, check=True, capture_output=True)
        tree_oid = subprocess.check_output(["git", "write-tree"], cwd=self.layout.checkout_dir, text=True).strip()
        cand_sha = hashlib.sha256(worker_script.read_bytes()).hexdigest()

        manifest = TrustedManifest(
            task_id="task-nat-03",
            run_id="run-nat-03",
            nonce=nonce,
            verifier_version="1.0.0",
            candidate_bytes_sha256=cand_sha,
            canonical_tree_oid=tree_oid,
            tree_algorithm="sha1",
            approved_argv=[str(self.runtime), "-I", "-P", "worker.py"],
            timeout_seconds=15.0,
            spec_hash=hashlib.sha256(b"spec03").hexdigest(),
            expected_artifact_relpath="result.json",
        )
        manifest.publish_atomic(self.layout.vault_dir)

        env = build_trusted_environment(
            "run-nat-03", "task-nat-03",
            temp_dir=self.layout.temp_dir,
            runtime_path=self.runtime.parent,
            local_app_data=self.profile.folder,
        )
        log_path = self.layout.log_dir / "worker.log"
        proc = WindowsJobProcess.start(
            argv=[str(self.runtime), "-I", "-P", "worker.py"],
            cwd=self.layout.checkout_dir,
            log_path=log_path,
            env=env,
            app_container=self.profile,
            temp_dir=self.layout.temp_dir,
        )
        try:
            proc.resume()
            # Wait until readiness signals appear (child written by descendant, root by worker)
            child_ready = self.layout.checkout_dir / "child_ready.txt"
            root_ready = self.layout.checkout_dir / "root_ready.txt"
            start_t = time.monotonic()
            while not (child_ready.exists() and root_ready.exists()) and time.monotonic() - start_t < 10.0:
                time.sleep(0.05)
            self.assertTrue(child_ready.exists(), "child did not create child_ready.txt in time")
            self.assertTrue(root_ready.exists(), "root did not create root_ready.txt in time")

            # At this point, job has active processes running in Job Object
            self.assertGreaterEqual(proc.active_processes, 2)
            self.assertFalse(proc.tree_stopped)

            bindings = {
                "actual_argv": manifest.approved_argv,
                "actual_timeout": manifest.timeout_seconds,
                "actual_tree_oid": manifest.canonical_tree_oid,
                "actual_spec_hash": manifest.spec_hash,
            }
            # Reader MUST refuse while descendant child is still active in Job!
            with self.assertRaises(TrustedLayoutSecurityError):
                read_trusted_artifact(
                    self.layout.checkout_dir, "result.json", job_process=proc, manifest=manifest, **bindings
                )

            # Terminate tree
            proc.terminate_tree()
            self.assertTrue(proc.tree_stopped)
            self.assertEqual(proc.active_processes, 0)

            # After tree termination, reader succeeds
            artifact = read_trusted_artifact(
                self.layout.checkout_dir, "result.json", job_process=proc, manifest=manifest, **bindings
            )
            self.assertEqual(artifact["status"], "PASS")
            self.assertEqual(artifact["tests_run"], 5)

            TrustedLayoutNativeTests._evidence_test_native_descendant_blocks_reader_until_stopped = {
                "status": "PASS",
                "test": "test_native_descendant_blocks_reader_until_stopped",
                "child_marker_verified": True,
                "active_processes_alive": True,
                "reader_refused_while_descendant_active": True,
                "reader_passed_after_terminate_tree": True,
            }
        finally:
            proc.close()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--native-controller":
        _report, _log = Path(sys.argv[2]), Path(sys.argv[3])
        try:
            _status = _run_native_controller(_report, _log)
        except BaseException as _error:
            _report.write_text(
                json.dumps(
                    {"error": f"{type(_error).__name__}: {_error}", "success": False},
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            raise
        raise SystemExit(_status)
    unittest.main()
