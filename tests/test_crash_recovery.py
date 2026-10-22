"""Crash recovery and interruption matrix tests for REC-02."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core.main import (
    OperationError,
    advance,
    execute,
    git,
    save,
    stop,
    validate,
    validate_state,
)


class CrashRecoveryTestCase(unittest.TestCase):
    """Disposable Git repository fixture for crash matrix tests."""

    def setUp(self) -> None:
        scratch = Path(__file__).resolve().parents[1] / ".omp/test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@localhost")
        self.git("config", "user.name", "Test")
        (self.root / ".gitignore").write_text(".omp/\n__pycache__/\n", encoding="utf-8")
        (self.root / "app.py").write_text("value = 0\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()

    def prepare(
        self,
        worker_code: str,
        test_code: str = "import app; assert app.value == 1",
        schema_version: int = 2,
    ) -> Path:
        config = {
            "schema_version": 1,
            "worker_argv": [sys.executable, "-c", worker_code],
            "test_argv": [sys.executable, "-B", "-c", test_code],
            "timeout_seconds": 10,
        }
        task = {
            "schema_version": 1,
            "specification": "Set value to 1",
            "allowed_files_to_modify": ["app.py"],
        }
        validate(config, task)
        omp = self.root / ".omp"
        omp.mkdir(parents=True, exist_ok=True)
        (omp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        checkpoint = omp / "state.json"
        state = {
            "schema_version": schema_version,
            "run_id": "rec-run",
            "phase": "preparing",
            "base_commit": self.base,
            "config": config,
            "task": task,
            "child_pid": None,
        }
        save(checkpoint, state)
        return checkpoint

    def read_state(self) -> dict[str, object]:
        return json.loads((self.root / ".omp/state.json").read_text(encoding="utf-8"))


def force_rmtree(path: Path) -> None:
    """Recursively remove a directory tree, clearing read-only flags for Python >=3.10."""
    def _handle_remove_readonly(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_handle_remove_readonly)


class CloneAndCheckoutInterruptionTests(CrashRecoveryTestCase):
    """AC2: Clone and checkout interruptions are retained and rejected safely."""

    def test_interrupted_clone_is_retained_and_rejected_without_overwrite(self) -> None:
        """Matrix M1: Interrupted clone is retained for inspection and not overwritten."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        state = self.read_state()
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        checkout.mkdir(parents=True)
        sentinel_file = checkout / "interrupted_marker.txt"
        sentinel_file.write_text("sentinel content", encoding="utf-8")

        # Advance should fail-closed with clear error and not overwrite or delete checkout
        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("interrupted clone", str(caught.exception).lower())
        self.assertTrue(checkout.exists())
        self.assertTrue(sentinel_file.exists())
        self.assertEqual(sentinel_file.read_text(encoding="utf-8"), "sentinel content")

    def test_interrupted_checkout_is_retained_and_rejected(self) -> None:
        """Matrix M2: Clone exists but HEAD is not properly checked out; rejected safely."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        state = self.read_state()
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        # Create an incomplete Git repository clone without detached HEAD
        self.git("clone", "--local", "--no-checkout", str(self.root), str(checkout))
        self.assertTrue(checkout.exists())

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("interrupted clone", str(caught.exception).lower())
        self.assertTrue(checkout.exists())

    def test_missing_checkout_in_later_phases_is_rejected_safely(self) -> None:
        """Matrix M2 variation: Checkout missing in worker, verifying, or committing fails closed."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        # Run until complete, then rewind phase to worker while deleting checkout
        advance(self.root)
        state = self.read_state()
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        force_rmtree(checkout)

        state["phase"] = "worker"
        del state["result_commit"]
        del state["completion_kind"]
        state.pop("commit_intent", None)
        state.pop("verification_evidence", None)
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("checkout directory is missing", str(caught.exception).lower())


class ProcessBoundaryAndSpawnGapTests(CrashRecoveryTestCase):
    """AC3: Process boundaries, spawn gap cleanup, and recorded PID enforcement."""

    def test_spawn_gap_child_is_cleaned_up_on_failure_and_gap_acknowledged(self) -> None:
        """Matrix M3: Child process is stopped if save fails right after spawn; gap acknowledged."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        run_dir = self.root / ".omp/runs" / str(state["run_id"])
        checkout = run_dir / "checkout"
        checkpoint = self.root / ".omp/state.json"

        # Script that writes a PID file and sleeps for up to 3 seconds
        pid_file = run_dir / "child.pid"
        worker_script = (
            f"import os, time, pathlib; "
            f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid())); "
            f"time.sleep(3)"
        )
        argv = [sys.executable, "-c", worker_script]

        spawned_pids: list[int] = []

        def failing_save(p: Path, v: object) -> None:
            # When execute calls save with child_pid, record it and simulate IO error
            if isinstance(v, dict) and v.get("child_pid"):
                spawned_pids.append(v["child_pid"])
                raise OSError("Simulated disk error after spawn before save")
            save(p, v)

        with patch("omp_core.main.save", side_effect=failing_save):
            with self.assertRaises(OSError):
                execute(argv, checkout, run_dir, state, checkpoint, "worker_probe")

        # The child process must have been stopped by execute's finally/stop block
        self.assertEqual(len(spawned_pids), 1)
        child_pid = spawned_pids[0]

        # Give a moment to confirm process exited
        time.sleep(0.2)
        if os.name == "nt":
            check = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertNotIn(str(child_pid), check.stdout)
        else:
            try:
                os.kill(child_pid, 0)
                alive = True
            except OSError:
                alive = False
            self.assertFalse(alive)

    def test_interrupted_worker_with_pid_recorded_blocks_resume_safely(self) -> None:
        """Matrix M4: Recorded child_pid in state blocks advance until explicitly cleared."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()

        # Simulate crash while child was running: set phase=worker and child_pid
        state["phase"] = "worker"
        state["child_pid"] = 88888
        del state["result_commit"]
        del state["completion_kind"]
        state.pop("commit_intent", None)
        state.pop("verification_evidence", None)
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("previous child pid 88888", str(caught.exception).lower())

        # Clearing child_pid allows resumption
        state["child_pid"] = None
        save(self.root / ".omp/state.json", state)
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
        advance(self.root)
        completed = self.read_state()
        self.assertEqual(completed["phase"], "complete")

    def test_final_checkpoint_save_failure_preserves_operation_error_in_cause(self) -> None:
        """REC02-R1: Final save failure preserves worker nonzero error in cause chain."""
        # Worker command exits 7
        self.prepare("import sys; sys.exit(7)")
        state = self.read_state()
        real_save = pipeline.save
        worker_started = False
        failed_once = False
        def failing_final_save(p: Path, v: object) -> None:
            nonlocal worker_started, failed_once
            if isinstance(v, dict) and v.get("child_pid") is not None:
                worker_started = True
            elif worker_started and not failed_once and isinstance(v, dict) and v.get("child_pid") is None:
                failed_once = True
                raise OSError("CHECKPOINT_FINAL_SAVE_FAILURE")
            real_save(p, v)

        with patch("omp_core.main.save", side_effect=failing_final_save):
            with self.assertRaises(OSError) as caught:
                advance(self.root)

        # The raised error is the final save failure, and its __cause__ is the operation error
        self.assertIn("CHECKPOINT_FINAL_SAVE_FAILURE", str(caught.exception))
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIn("worker exited 7", str(caught.exception.__cause__).lower())

    def test_advance_last_error_save_failure_preserves_original_cause(self) -> None:
        """REC02-R1: Advance error handler preserves original exception if last_error save fails."""
        self.prepare("import sys; sys.exit(3)")
        checkpoint = self.root / ".omp/state.json"
        real_save = pipeline.save

        def fail_on_error_save(p: Path, v: object) -> None:
            if isinstance(v, dict) and "last_error" in v:
                raise OSError("DISK_FULL_ON_LAST_ERROR_SAVE")
            real_save(p, v)

        with patch("omp_core.main.save", side_effect=fail_on_error_save):
            with self.assertRaises(OSError) as caught:
                advance(self.root)

        self.assertIn("DISK_FULL_ON_LAST_ERROR_SAVE", str(caught.exception))
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIn("worker exited 3", str(caught.exception.__cause__).lower())

    def test_final_checkpoint_save_failure_on_successful_operation_raises_nonzero(self) -> None:
        """REC02-R1: Save failure when operation succeeded still raises nonzero, not masked."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        real_save = pipeline.save

        def fail_on_worker_complete_save(p: Path, v: object) -> None:
            if isinstance(v, dict) and v.get("phase") == "worker" and v.get("child_pid") is None:
                raise OSError("SAVE_FAILED_ON_SUCCESS")
            real_save(p, v)

        with patch("omp_core.main.save", side_effect=fail_on_worker_complete_save):
            with self.assertRaises(OSError) as caught:
                advance(self.root)
        self.assertIn("SAVE_FAILED_ON_SUCCESS", str(caught.exception))


class StagingAndCommitInterruptionTests(CrashRecoveryTestCase):
    """AC4: Staging and commit crash reconciliation, idempotency, and no duplicates."""

    def test_test_pass_before_checkpoint_reruns_verification_safely(self) -> None:
        """Matrix M5: Test passes but crash occurs before committing checkpoint; re-verifies."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Rewind to verifying phase without evidence/intent (simulating crash right after test run)
        state["phase"] = "verifying"
        del state["result_commit"]
        del state["completion_kind"]
        del state["commit_intent"]
        del state["verification_evidence"]
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
        save(self.root / ".omp/state.json", state)

        # Resuming re-runs verification and completes cleanly
        advance(self.root)
        completed = self.read_state()
        self.assertEqual(completed["phase"], "complete")
        self.assertIsNotNone(completed.get("verification_evidence"))
        self.assertIsNotNone(completed.get("commit_intent"))

    def test_interrupted_staging_resets_and_reconciles_cleanly(self) -> None:
        """Matrix M6: Staging interrupted with dirty index; advance resets and commits cleanly."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"

        # Rewind to committing phase; reset commit to baseline
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")

        # Create dirty staging (partial stage in index before commit)
        subprocess.check_call(["git", "-C", str(checkout), "add", "app.py"], stderr=subprocess.DEVNULL)
        save(self.root / ".omp/state.json", state)

        # advance resets index, stages allowed files according to intent, and completes
        advance(self.root)
        completed = self.read_state()
        self.assertEqual(completed["phase"], "complete")
        self.assertEqual(completed["completion_kind"], "committed")

    def test_commit_before_complete_checkpoint_resumes_idempotently_without_duplicate(self) -> None:
        """Matrix M7a: Commit succeeded but crash before complete checkpoint; resumes idempotently."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()
        first_commit = completed_state["result_commit"]

        # Simulate crash after commit: rewind state to committing with intent preserved
        simulated = dict(completed_state)
        simulated["phase"] = "committing"
        del simulated["result_commit"]
        del simulated["completion_kind"]
        save(self.root / ".omp/state.json", simulated)

        # Resume must recognize existing HEAD commit and complete without making a second commit
        advance(self.root)
        resumed_state = self.read_state()
        self.assertEqual(resumed_state["phase"], "complete")
        self.assertEqual(resumed_state["result_commit"], first_commit)

        # Verify only one commit was made on top of baseline
        checkout = self.root / ".omp/runs" / str(resumed_state["run_id"]) / "checkout"
        log = subprocess.check_output(
            ["git", "-C", str(checkout), "rev-list", f"{self.base}..HEAD"],
            text=True,
        ).strip().splitlines()
        self.assertEqual(len(log), 1)

    def test_commit_before_complete_with_corrupted_commit_is_rejected(self) -> None:
        """Matrix M7b: Unexpected commit during recovery with mismatched parent/marker/tree is rejected."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()

        # Rewind to committing, but tamper with HEAD commit subject
        simulated = dict(completed_state)
        simulated["phase"] = "committing"
        del simulated["result_commit"]
        del simulated["completion_kind"]
        save(self.root / ".omp/state.json", simulated)

        checkout = self.root / ".omp/runs" / str(simulated["run_id"]) / "checkout"
        subprocess.check_call(
            [
                "git",
                "-C",
                str(checkout),
                "-c",
                "user.name=OMP Test",
                "-c",
                "user.email=test@localhost",
                "commit",
                "--amend",
                "-m",
                "tampered subject",
                "--allow-empty",
            ],
            stderr=subprocess.DEVNULL,
        )

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("unexpected commit during recovery", str(caught.exception).lower())


class AtomicCheckpointSaveTests(CrashRecoveryTestCase):
    """AC5: Atomic checkpointing, durability simulation, and cleanup safety."""

    def test_atomic_save_write_failure_preserves_existing_checkpoint(self) -> None:
        """Matrix M8a: Failure during json write preserves existing checkpoint exactly."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        bytes_before = checkpoint.read_bytes()
        hash_before = hashlib.sha256(bytes_before).hexdigest()

        with patch("json.dump", side_effect=OSError("Disk write error during json.dump")):
            with self.assertRaises(OSError):
                save(checkpoint, {"corrupt": "state"})
        self.assertEqual(checkpoint.read_bytes(), bytes_before)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), hash_before)

        # Verify no temporary files remain in .omp
        tmp_files = list((self.root / ".omp").glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_atomic_save_partial_write_failure_preserves_existing_checkpoint(self) -> None:
        """Matrix M8a: Partial temp write preserves existing checkpoint and rejects corrupt state."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        bytes_before = checkpoint.read_bytes()
        hash_before = hashlib.sha256(bytes_before).hexdigest()

        def partial_dump(obj, fp, **kwargs):
            fp.write('{"partial": ')
            raise OSError("Disk full during write")

        with patch("json.dump", side_effect=partial_dump):
            with self.assertRaises(OSError):
                save(checkpoint, {"corrupt": "state"})

        self.assertEqual(checkpoint.read_bytes(), bytes_before)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), hash_before)
        # Authoritative state read must not read corrupt partial data
        state = self.read_state()
        self.assertIn("phase", state)
        self.assertEqual(len(list((self.root / ".omp").glob("*.tmp"))), 0)

    def test_atomic_save_flush_failure_preserves_existing_checkpoint(self) -> None:
        """Matrix M8a: Failure during stream.flush preserves existing checkpoint."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        bytes_before = checkpoint.read_bytes()
        hash_before = hashlib.sha256(bytes_before).hexdigest()

        real_fdopen = os.fdopen
        def failing_flush_fdopen(*args, **kwargs):
            stream = real_fdopen(*args, **kwargs)
            real_flush = stream.flush
            flushed = False
            def bad_flush():
                nonlocal flushed
                if not flushed:
                    flushed = True
                    raise OSError("Disk flush I/O failure")
                return real_flush()
            stream.flush = bad_flush
            return stream

        with patch("os.fdopen", side_effect=failing_flush_fdopen):
            with self.assertRaises(OSError):
                save(checkpoint, {"corrupt": "state"})
        self.assertEqual(checkpoint.read_bytes(), bytes_before)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), hash_before)
        self.assertEqual(len(list((self.root / ".omp").glob("*.tmp"))), 0)

    def test_atomic_save_fsync_failure_preserves_existing_checkpoint(self) -> None:
        """Matrix M8a: Failure during os.fsync preserves existing checkpoint."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        bytes_before = checkpoint.read_bytes()
        hash_before = hashlib.sha256(bytes_before).hexdigest()

        with patch("os.fsync", side_effect=OSError("Disk write error during fsync")):
            with self.assertRaises(OSError):
                save(checkpoint, {"corrupt": "state"})
        self.assertEqual(checkpoint.read_bytes(), bytes_before)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), hash_before)
        self.assertEqual(len(list((self.root / ".omp").glob("*.tmp"))), 0)

    def test_atomic_save_replace_failure_preserves_existing_checkpoint(self) -> None:
        """Matrix M8a: Failure during os.replace preserves existing checkpoint."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        checkpoint = self.root / ".omp/state.json"
        bytes_before = checkpoint.read_bytes()
        hash_before = hashlib.sha256(bytes_before).hexdigest()

        with patch("os.replace", side_effect=OSError("Replace atomic failure")):
            with self.assertRaises(OSError):
                save(checkpoint, {"corrupt": "state"})
        self.assertEqual(checkpoint.read_bytes(), bytes_before)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), hash_before)
        self.assertEqual(len(list((self.root / ".omp").glob("*.tmp"))), 0)

    def test_atomic_save_cleanup_error_does_not_mask_original_error(self) -> None:
        """Matrix M8b: Cleanup failure in finally block does not mask the original IO error."""
        checkpoint = self.root / ".omp/state.json"
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")

        with patch("os.replace", side_effect=OSError("Original disk error")):
            with patch("os.unlink", side_effect=OSError("Unlink permission error")):
                with self.assertRaises(OSError) as caught:
                    save(checkpoint, {"corrupt": "data"})
                # Original error message must be preserved
                self.assertIn("Original disk error", str(caught.exception))

    def test_atomic_save_failure_on_new_file_does_not_create_corrupt_checkpoint(self) -> None:
        """Matrix M8c: Failure when saving a new file does not leave a partial corrupt file."""
        new_path = self.root / ".omp/new_checkpoint.json"
        with patch("os.fsync", side_effect=OSError("Simulated fsync error on new file")):
            with self.assertRaises(OSError):
                save(new_path, {"some": "data"})
        self.assertFalse(new_path.exists())


class RealProcessInterruptionOnWindowsTests(CrashRecoveryTestCase):
    """AC6: Real orchestrator subprocess interruption on Windows with bounded child cleanup."""

    def _run_orchestrator_until_boundary(
        self,
        worker_code: str,
        *,
        pre_exit_probe: bool = False,
        timeout: float = 8.0,
    ) -> tuple[subprocess.Popen[str], int, Path]:
        """Run real orchestrator and wait deterministically for worker boundary readiness."""
        self.prepare(worker_code)
        marker_file = self.root / ".omp/worker_started.marker"
        if marker_file.exists():
            marker_file.unlink()

        if pre_exit_probe:
            # Negative control: command that pre-exits immediately before boundary
            cmd = [sys.executable, "-c", "import sys; sys.exit(0)"]
        else:
            cmd = [
                sys.executable,
                "-m",
                "omp_core.main",
                "--root",
                str(self.root),
                "resume",
            ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            deadline = time.monotonic() + timeout
            child_pid: int | None = None
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if marker_file.exists():
                    try:
                        content = marker_file.read_text(encoding="utf-8").strip()
                        if content:
                            child_pid = int(content)
                            break
                    except (ValueError, OSError):
                        pass
                time.sleep(0.05)

            if proc.poll() is not None:
                raise AssertionError(
                    f"Orchestrator pre-exited unexpectedly with code {proc.returncode} before reaching worker boundary."
                )
            if child_pid is None:
                raise AssertionError("Worker boundary marker was not produced before deadline")

            return proc, child_pid, marker_file
        except BaseException:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        timeout=5,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    proc.kill()
                proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            raise

    def test_real_orchestrator_process_interruption_on_windows(self) -> None:
        """Matrix M9: Interrupt running orchestrator on Windows, verify disk state, resume cleanly."""
        marker_path = self.root / ".omp/worker_started.marker"
        worker_code = (
            f"import os, time, pathlib; "
            f"pathlib.Path(r'{marker_path}').write_text(str(os.getpid())); "
            f"time.sleep(5); "
            f"pathlib.Path('app.py').write_text('value = 1\\n')"
        )

        proc: subprocess.Popen[str] | None = None
        child_pid: int | None = None
        try:
            proc, child_pid, marker_file = self._run_orchestrator_until_boundary(worker_code)
            self.assertIsNone(proc.poll(), "Orchestrator must be alive at boundary")

            # Checkpoint on disk must be in worker phase with child_pid recorded
            checkpoint = self.root / ".omp/state.json"
            self.assertTrue(checkpoint.exists())
            state = self.read_state()
            self.assertEqual(state["phase"], "worker")
            self.assertEqual(state["child_pid"], child_pid)

            # Interrupt orchestrator process from test runner
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                proc.terminate()
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.poll(), "Orchestrator must have exited after kill")

            # Stop child process if still alive and confirm it has stopped
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.1)
                check = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {child_pid}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertNotIn(str(child_pid), check.stdout)

            # Clear child_pid in state according to documented manual recovery policy
            state["child_pid"] = None
            save(checkpoint, state)
            checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
            subprocess.run(
                ["git", "-C", str(checkout), "reset", "--hard", self.base],
                timeout=5,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")

            # Resuming with a new orchestrator process completes cleanly
            resumed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "omp_core.main",
                    "--root",
                    str(self.root),
                    "resume",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(resumed.returncode, 0, f"Resume failed: {resumed.stderr}")
            final_state = self.read_state()
            self.assertEqual(final_state["phase"], "complete")
        finally:
            if proc is not None and proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        timeout=5,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    proc.kill()
                proc.wait(timeout=5)
            if proc is not None:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
            if child_pid is not None and os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    def test_real_orchestrator_negative_control_pre_exit_fails(self) -> None:
        """REC02-R2 negative control: If orchestrator pre-exits before boundary, test fails."""
        with self.assertRaises(AssertionError) as caught:
            self._run_orchestrator_until_boundary("pass", pre_exit_probe=True, timeout=2.0)
        self.assertIn("pre-exited unexpectedly", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
