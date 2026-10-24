"""Recovery/abort CLI, audit, and lock-boundary tests for PROC-03."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core.main import (
    InputError,
    OperationError,
    _check_existing_run,
    _pid_state,
    abort,
    advance,
    git_object_id_length,
    recover,
    save,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "proc03-run"


class RecoveryCliTestCase(unittest.TestCase):
    """Disposable Git repository with valid v1/v2 checkpoints."""

    def setUp(self) -> None:
        scratch = ROOT / ".omp/test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@localhost")
        self.git("config", "user.name", "Test")
        (self.root / ".gitignore").write_text(
            ".omp/\n__pycache__/\n", encoding="utf-8"
        )
        (self.root / "app.py").write_text("value = 0\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.checkpoint = self.root / ".omp/state.json"

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()

    def prepare(
        self,
        *,
        phase: str = "preparing",
        child_pid: int | None = None,
        schema_version: int = 2,
        checkout: bool = False,
        worker_code: str = "pass",
        test_code: str = "pass",
    ) -> None:
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
        (omp / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        run_dir = omp / "runs" / RUN_ID
        if checkout:
            (run_dir / "checkout").mkdir(parents=True, exist_ok=True)
        save(self.checkpoint, {
            "schema_version": schema_version,
            "run_id": RUN_ID,
            "phase": phase,
            "base_commit": self.base,
            "config": config,
            "task": task,
            "child_pid": child_pid,
        })

    def read_state(self) -> dict[str, object]:
        return json.loads(self.checkpoint.read_text(encoding="utf-8"))

    def update_state(self, **changes: object) -> dict[str, object]:
        state = self.read_state()
        state.update(changes)
        save(self.checkpoint, state)
        return state

    def digest(self) -> str:
        return hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()

    def audit_events(self, run_id: str = RUN_ID) -> list[dict[str, object]]:
        directory = self.root / ".omp/audit" / run_id
        if not directory.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]

    def complete_run(self, *, no_change: bool = False) -> None:
        worker = "pass" if no_change else (
            "from pathlib import Path; "
            "Path('app.py').write_text('value = 1\\n')"
        )
        test = "pass" if no_change else "import app; assert app.value == 1"
        self.prepare(worker_code=worker, test_code=test)
        with redirect_stdout(io.StringIO()):
            advance(self.root)

    def dead_pid(self) -> int:
        for _ in range(20):
            process = subprocess.Popen([sys.executable, "-c", "pass"])
            process.wait(timeout=10)
            if _pid_state(process.pid) == "absent":
                return process.pid
        self.skipTest("could not obtain an absent PID without reuse")
        raise AssertionError("unreachable")

    def start_sleeper(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        self.addCleanup(self.stop_process, process)
        return process

    @staticmethod
    def stop_process(process: subprocess.Popen[object]) -> None:
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def hold_lock(self) -> subprocess.Popen[str]:
        ready = self.root.parent / "lock.ready"
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from omp_core.main import lock\n"
            "with lock(Path(sys.argv[1])):\n"
            "    Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
            "    time.sleep(30)\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(self.root / ".omp/lock"),
                str(ready),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_process, process)  # type: ignore[arg-type]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if ready.exists():
                return process
            if process.poll() is not None:
                self.fail(
                    "lock holder exited: "
                    + (process.stderr.read() if process.stderr else "")
                )
            time.sleep(0.05)
        self.fail("lock holder did not become ready")

    def omp(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "omp_core.main", "--root", str(self.root), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def test_recover_refuses_run_and_digest_mismatch_without_mutation(self) -> None:
        self.prepare(phase="worker", checkout=True)
        before = self.checkpoint.read_bytes()
        digest = self.digest()

        with self.assertRaises(InputError):
            recover(self.root, "other-run", digest, False)
        with self.assertRaises(InputError):
            recover(self.root, RUN_ID, "0" * 64, False)

        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.audit_events(), [])

        result = self.omp("recover", RUN_ID, "--state-sha256", "0" * 64)
        self.assertEqual(result.returncode, 1)
        self.assertIn("OMP:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_recover_refuses_contended_lock_without_mutation(self) -> None:
        self.prepare(phase="worker", checkout=True)
        before = self.checkpoint.read_bytes()
        self.hold_lock()

        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), False)

        self.assertIn("lock", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.audit_events(), [])

    def test_recover_never_kills_alive_or_unknown_pid(self) -> None:
        self.prepare(phase="worker", checkout=True)
        process = self.start_sleeper()
        self.update_state(child_pid=process.pid)
        before = self.checkpoint.read_bytes()

        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), True)
        self.assertIn("alive", str(caught.exception).lower())
        self.assertIsNone(process.poll())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"], "child_pid_alive"
        )

        with patch("omp_core.main._pid_state", return_value="unknown"):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, self.digest(), True)
        self.assertIn("ownership", str(caught.exception).lower())
        self.assertIsNone(process.poll())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "child_pid_ownership_unknown",
        )

    def test_recover_requires_confirmation_and_clears_only_child_pid(self) -> None:
        worker_sentinel = self.root / "worker-ran.txt"
        test_sentinel = self.root / "test-ran.txt"
        self.prepare(
            phase="worker",
            checkout=True,
            child_pid=self.dead_pid(),
            worker_code=(
                "from pathlib import Path; "
                f"Path(r'{worker_sentinel}').write_text('x')"
            ),
            test_code=(
                "from pathlib import Path; "
                f"Path(r'{test_sentinel}').write_text('x')"
            ),
        )
        before_state = self.read_state()
        before_bytes = self.checkpoint.read_bytes()
        before_digest = self.digest()

        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, before_digest, False)
        self.assertIn("confirm", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before_bytes)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "child_pid_absent_unconfirmed",
        )

        output = io.StringIO()
        with redirect_stdout(output):
            recover(self.root, RUN_ID, before_digest, True)

        after = self.read_state()
        self.assertIsNone(after["child_pid"])
        self.assertEqual(after["phase"], before_state["phase"])
        self.assertEqual(after["run_id"], before_state["run_id"])
        self.assertEqual(after["config"], before_state["config"])
        self.assertNotEqual(self.checkpoint.read_bytes(), before_bytes)
        self.assertNotEqual(self.digest(), before_digest)
        self.assertIn("cleared child_pid", output.getvalue())
        self.assertFalse(worker_sentinel.exists())
        self.assertFalse(test_sentinel.exists())
        event = self.audit_events()[-1]
        self.assertEqual(event["result"], "success")
        self.assertEqual(
            event["reason_code"], "child_pid_absent_operator_attested"
        )
        self.assertEqual(event["before_sha256"], before_digest)
        self.assertEqual(event["after_sha256"], self.digest())
        self.assertEqual(
            event["containment_proof"], "operator_attestation_only"
        )

    def test_recover_refuses_interrupted_clone_and_missing_checkout(self) -> None:
        self.prepare()
        checkout = self.root / ".omp/runs" / RUN_ID / "checkout"
        checkout.mkdir(parents=True)
        sentinel = checkout / "interrupted.txt"
        sentinel.write_text("keep", encoding="utf-8")
        before = self.checkpoint.read_bytes()

        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("interrupted clone", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertTrue(sentinel.exists())
        self.assertEqual(
            self.audit_events()[-1]["reason_code"], "checkout_unavailable"
        )

        shutil.rmtree(checkout)
        self.prepare(phase="worker", checkout=False)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("checkout directory is missing", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_recover_complete_is_read_only(self) -> None:
        self.complete_run()
        before = self.checkpoint.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("already complete", output.getvalue())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "complete_verified",
        )

    def test_recover_no_change_is_read_only(self) -> None:
        self.complete_run(no_change=True)
        before = self.checkpoint.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("already complete", output.getvalue())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "complete_verified",
        )

    def test_recover_worker_verifying_committing_are_ready(self) -> None:
        self.complete_run()
        baseline_state = self.read_state()
        for phase in ("worker", "verifying", "committing"):
            with self.subTest(phase=phase):
                state = json.loads(json.dumps(baseline_state))
                state["phase"] = phase
                if phase in ("worker", "verifying"):
                    state.pop("result_commit", None)
                    state.pop("completion_kind", None)
                if phase == "worker":
                    state.pop("verification_evidence", None)
                    state.pop("commit_intent", None)
                save(self.checkpoint, state)
                before = self.checkpoint.read_bytes()
                output = io.StringIO()
                with redirect_stdout(output):
                    recover(self.root, RUN_ID, self.digest(), False)
                self.assertIn("ready for resume", output.getvalue())
                self.assertEqual(self.checkpoint.read_bytes(), before)
                self.assertEqual(
                    self.audit_events()[-1]["reason_code"], "checkpoint_ready"
                )

    def test_complete_child_pid_is_refused_by_recover_abort_and_run_check(self) -> None:
        self.complete_run()
        self.update_state(child_pid=self.dead_pid())
        before = self.checkpoint.read_bytes()

        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), True)
        self.assertIn("complete", str(caught.exception).lower())
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "complete_child_pid_recorded",
        )
        self.assertEqual(self.checkpoint.read_bytes(), before)

        with self.assertRaises(OperationError) as caught:
            abort(self.root, RUN_ID, self.digest())
        self.assertIn("child_pid", str(caught.exception).lower())
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "child_pid_still_recorded",
        )
        self.assertEqual(self.checkpoint.read_bytes(), before)

        with self.assertRaises(OperationError) as caught:
            _check_existing_run(self.root, git_object_id_length(self.root))
        self.assertIn("complete run", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_recover_refuses_legacy_v1_without_rewrite(self) -> None:
        self.prepare(phase="worker", checkout=True, schema_version=1)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(OperationError) as caught:
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("schema_version 1", str(caught.exception))
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"], "unsupported_schema"
        )

        self.update_state(phase="complete", result_commit=self.base)
        before = self.checkpoint.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("already complete", output.getvalue())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"], "legacy_complete"
        )

        self.update_state(child_pid=self.dead_pid())
        before = self.checkpoint.read_bytes()
        with self.assertRaises(OperationError):
            recover(self.root, RUN_ID, self.digest(), True)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "complete_child_pid_recorded",
        )

    def test_recover_checkpoint_failure_does_not_report_success(self) -> None:
        self.prepare(
            phase="worker", checkout=True, child_pid=self.dead_pid()
        )
        before = self.checkpoint.read_bytes()
        digest = self.digest()
        real_save = pipeline.save

        def fail_checkpoint(path: Path, value: object) -> None:
            if Path(path) == self.checkpoint:
                raise OSError("CHECKPOINT_SAVE_FAILURE")
            real_save(path, value)

        output = io.StringIO()
        with patch("omp_core.main.save", side_effect=fail_checkpoint):
            with redirect_stdout(output):
                with self.assertRaises(OperationError) as caught:
                    recover(self.root, RUN_ID, digest, True)

        self.assertNotIn("Recovered", output.getvalue())
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        event = self.audit_events()[-1]
        self.assertEqual(event["result"], "error")
        self.assertEqual(event["reason_code"], "checkpoint_save_failed")

    def test_recover_audit_failure_does_not_report_success(self) -> None:
        self.prepare(
            phase="worker", checkout=True, child_pid=self.dead_pid()
        )
        digest = self.digest()
        real_save = pipeline.save

        def fail_audit(path: Path, value: object) -> None:
            if "audit" in Path(path).parts:
                raise OSError("AUDIT_SAVE_FAILURE")
            real_save(path, value)

        output = io.StringIO()
        with patch("omp_core.main.save", side_effect=fail_audit):
            with redirect_stdout(output):
                with self.assertRaises(OperationError) as caught:
                    recover(self.root, RUN_ID, digest, True)

        self.assertNotIn("Recovered", output.getvalue())
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertIsNone(self.read_state()["child_pid"])
        self.assertEqual(self.audit_events(), [])

        # Retry observes the already-cleared checkpoint and persists the audit.
        output = io.StringIO()
        with redirect_stdout(output):
            recover(self.root, RUN_ID, self.digest(), False)
        self.assertIn("ready for resume", output.getvalue())
        self.assertEqual(
            self.audit_events()[-1]["reason_code"], "checkpoint_ready"
        )

    def test_refusal_audit_failure_preserves_refusal_and_io_cause(self) -> None:
        self.prepare(
            phase="worker", checkout=True, child_pid=self.dead_pid()
        )
        before = self.checkpoint.read_bytes()
        real_save = pipeline.save

        def fail_audit(path: Path, value: object) -> None:
            if "audit" in Path(path).parts:
                raise OSError("AUDIT_SAVE_FAILURE")
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_audit):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, self.digest(), False)

        self.assertIn("audit", str(caught.exception).lower())
        refusal = caught.exception.__cause__
        self.assertIsInstance(refusal, OperationError)
        self.assertIn("confirm", str(refusal).lower())
        self.assertIsInstance(refusal.__cause__, OSError)
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_abort_is_idempotent_and_preserves_evidence(self) -> None:
        self.prepare(phase="worker", checkout=True)
        run_dir = self.root / ".omp/runs" / RUN_ID
        log = run_dir / "worker.log"
        log.write_text("worker evidence", encoding="utf-8")
        checkout_marker = run_dir / "checkout" / "keep.txt"
        checkout_marker.write_text("checkout evidence", encoding="utf-8")
        before = self.checkpoint.read_bytes()
        digest = self.digest()

        output = io.StringIO()
        with redirect_stdout(output):
            abort(self.root, RUN_ID, digest)
        self.assertIn("Aborted run", output.getvalue())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        archive = run_dir / "aborted-state.json"
        self.assertEqual(archive.read_bytes(), before)
        self.assertTrue((run_dir / "aborted.json").exists())
        self.assertEqual(log.read_text(encoding="utf-8"), "worker evidence")
        self.assertEqual(
            checkout_marker.read_text(encoding="utf-8"), "checkout evidence"
        )
        first_event = self.audit_events()[-1]
        self.assertEqual(first_event["result"], "success")
        self.assertEqual(first_event["reason_code"], "aborted")
        self.assertFalse(first_event["checkpoint_changed"])

        marker_before = (run_dir / "aborted.json").read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            abort(self.root, RUN_ID, digest)
        self.assertIn("already aborted", output.getvalue())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual((run_dir / "aborted.json").read_bytes(), marker_before)
        self.assertEqual(self.audit_events()[-1]["result"], "no_change")

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("aborted", str(caught.exception).lower())
        self.assertEqual(self.checkpoint.read_bytes(), before)

        # A new run is allowed only after the marker/archive reconcile safely.
        _check_existing_run(self.root, git_object_id_length(self.root))

    def test_abort_refuses_recorded_child_pid_without_killing_it(self) -> None:
        self.prepare(phase="worker", checkout=True)
        process = self.start_sleeper()
        self.update_state(child_pid=process.pid)
        before = self.checkpoint.read_bytes()

        with self.assertRaises(OperationError) as caught:
            abort(self.root, RUN_ID, self.digest())
        self.assertIn("child_pid", str(caught.exception).lower())
        self.assertIsNone(process.poll())
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertFalse(
            (self.root / ".omp/runs" / RUN_ID / "aborted.json").exists()
        )
        self.assertEqual(
            self.audit_events()[-1]["reason_code"],
            "child_pid_still_recorded",
        )

    def test_abort_archive_failure_does_not_report_success(self) -> None:
        self.prepare(phase="worker", checkout=True)
        before = self.checkpoint.read_bytes()
        digest = self.digest()
        output = io.StringIO()
        with patch(
            "omp_core.main._write_bytes_atomic",
            side_effect=OSError("ARCHIVE_FAILURE"),
        ):
            with redirect_stdout(output):
                with self.assertRaises(OperationError) as caught:
                    abort(self.root, RUN_ID, digest)
        self.assertNotIn("Aborted run", output.getvalue())
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertFalse(
            (self.root / ".omp/runs" / RUN_ID / "aborted.json").exists()
        )
        event = self.audit_events()[-1]
        self.assertEqual(event["result"], "error")
        self.assertEqual(event["reason_code"], "aborted_state_save_failed")

    def test_abort_marker_failure_then_idempotent_retry(self) -> None:
        self.prepare(phase="worker", checkout=True)
        digest = self.digest()
        real_save = pipeline.save
        marker = self.root / ".omp/runs" / RUN_ID / "aborted.json"

        def fail_marker(path: Path, value: object) -> None:
            if Path(path) == marker:
                raise OSError("MARKER_FAILURE")
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_marker):
            with self.assertRaises(OperationError):
                abort(self.root, RUN_ID, digest)
        self.assertFalse(marker.exists())
        self.assertEqual(
            (self.root / ".omp/runs" / RUN_ID / "aborted-state.json").read_bytes(),
            self.checkpoint.read_bytes(),
        )
        self.assertEqual(self.audit_events()[-1]["result"], "error")

        output = io.StringIO()
        with redirect_stdout(output):
            abort(self.root, RUN_ID, digest)
        self.assertIn("Aborted run", output.getvalue())
        self.assertTrue(marker.exists())

    def test_abort_audit_failure_then_idempotent_retry(self) -> None:
        self.prepare(phase="worker", checkout=True)
        digest = self.digest()
        real_save = pipeline.save

        def fail_audit(path: Path, value: object) -> None:
            if "audit" in Path(path).parts:
                raise OSError("AUDIT_SAVE_FAILURE")
            real_save(path, value)

        output = io.StringIO()
        with patch("omp_core.main.save", side_effect=fail_audit):
            with redirect_stdout(output):
                with self.assertRaises(OperationError):
                    abort(self.root, RUN_ID, digest)
        self.assertNotIn("Aborted run", output.getvalue())
        marker = self.root / ".omp/runs" / RUN_ID / "aborted.json"
        self.assertTrue(marker.exists())
        self.assertEqual(self.audit_events(), [])

        output = io.StringIO()
        with redirect_stdout(output):
            abort(self.root, RUN_ID, digest)
        self.assertIn("already aborted", output.getvalue())
        self.assertEqual(self.audit_events()[-1]["result"], "no_change")

    def test_cli_recover_abort_and_resume_refusal(self) -> None:
        self.prepare(phase="worker", checkout=True)
        digest = self.digest()

        result = self.omp("recover", RUN_ID, "--state-sha256", digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready for resume", result.stdout)

        result = self.omp("abort", RUN_ID, "--state-sha256", digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Aborted run", result.stdout)

        result = self.omp("resume")
        self.assertEqual(result.returncode, 1)
        self.assertIn("aborted", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_new_run_check_rejects_modified_aborted_state(self) -> None:
        self.prepare(phase="worker", checkout=True)
        with redirect_stdout(io.StringIO()):
            abort(self.root, RUN_ID, self.digest())
        _check_existing_run(self.root, git_object_id_length(self.root))

        state = self.read_state()
        state["last_error"] = "changed after abort"
        save(self.checkpoint, state)
        with self.assertRaises(OperationError) as caught:
            _check_existing_run(self.root, git_object_id_length(self.root))
        self.assertIn("marker", str(caught.exception).lower())


    def assert_no_secrets(self, exc: BaseException, *secrets: str) -> None:
        stack = [exc]
        visited: set[int] = set()
        while stack:
            current = stack.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            s = str(current)
            r = repr(current)
            fn = getattr(current, "filename", None)
            fn2 = getattr(current, "filename2", None)
            for secret in secrets:
                self.assertNotIn(secret, s)
                self.assertNotIn(secret, r)
                if fn is not None:
                    self.assertNotIn(secret, str(fn))
                if fn2 is not None:
                    self.assertNotIn(secret, str(fn2))
            if current.__cause__ is not None:
                stack.append(current.__cause__)
            if current.__context__ is not None:
                stack.append(current.__context__)

    def test_r1_recovery_persistence_error_chain_is_sanitized_against_secrets(self) -> None:
        secret = "SECRET_TOKEN_XYZ_12345"
        real_save = pipeline.save

        # 1. Refusal + audit failure with injected secret in message and filename.
        self.prepare(phase="worker", checkout=True, child_pid=self.dead_pid())
        before = self.checkpoint.read_bytes()

        def fail_audit(path: Path, value: object) -> None:
            if "audit" in Path(path).parts:
                err = OSError(f"audit failure --token={secret}")
                err.filename = f"/tmp/{secret}.json"
                raise err
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_audit):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, self.digest(), False)

        self.assert_no_secrets(caught.exception, secret)
        self.assertIn("audit", str(caught.exception).lower())
        refusal = caught.exception.__cause__
        self.assertIsInstance(refusal, OperationError)
        self.assertIn("confirm", str(refusal).lower())
        self.assertIsInstance(refusal.__cause__, OSError)
        self.assertEqual(self.checkpoint.read_bytes(), before)

        # 2. Checkpoint save failure with injected secret.
        def fail_checkpoint(path: Path, value: object) -> None:
            if Path(path) == self.checkpoint:
                err = OSError(f"checkpoint save failure --secret={secret}")
                err.filename = f"/tmp/{secret}.json"
                raise err
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_checkpoint):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, self.digest(), True)

        self.assert_no_secrets(caught.exception, secret)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(self.checkpoint.read_bytes(), before)

        # 3. Double failure: checkpoint save fails AND audit save fails.
        secret_audit = "SECRET_AUDIT_FAIL_999"

        def fail_double(path: Path, value: object) -> None:
            if Path(path) == self.checkpoint:
                err = OSError(f"checkpoint failure --secret={secret}")
                err.filename = f"/tmp/{secret}.json"
                raise err
            if "audit" in Path(path).parts:
                err = OSError(f"audit failure --secret={secret_audit}")
                err.filename = f"/tmp/{secret_audit}.json"
                raise err
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_double):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, self.digest(), True)

        self.assert_no_secrets(caught.exception, secret, secret_audit)
        self.assertIn("audit", str(caught.exception).lower())
        self.assertIsInstance(caught.exception.__cause__, OSError)

        # 4. Abort archive failure with injected secret.
        self.prepare(phase="worker", checkout=True)
        secret_arch = "SECRET_ARCH_FAIL_777"
        arch_err = OSError(f"archive failure --secret={secret_arch}")
        arch_err.filename = f"/tmp/{secret_arch}.json"
        with patch("omp_core.main._write_bytes_atomic", side_effect=arch_err):
            with self.assertRaises(OperationError) as caught:
                abort(self.root, RUN_ID, self.digest())
        self.assert_no_secrets(caught.exception, secret_arch)
        self.assertIsInstance(caught.exception.__cause__, OSError)

        # 5. Abort marker failure with injected secret.
        self.prepare(phase="worker", checkout=True)
        secret_marker = "SECRET_MARKER_FAIL_888"
        marker_path = self.root / ".omp/runs" / RUN_ID / "aborted.json"

        def fail_marker(path: Path, value: object) -> None:
            if Path(path) == marker_path:
                err = OSError(f"marker failure --secret={secret_marker}")
                err.filename = f"/tmp/{secret_marker}.json"
                raise err
            real_save(path, value)

        with patch("omp_core.main.save", side_effect=fail_marker):
            with self.assertRaises(OperationError) as caught:
                abort(self.root, RUN_ID, self.digest())
        self.assert_no_secrets(caught.exception, secret_marker)
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_r2_run_rejects_abort_marker_archive_mismatch_before_complete_shortcut(self) -> None:
        # Prepare valid complete run
        self.complete_run(no_change=True)
        complete_bytes = self.checkpoint.read_bytes()

        # Update to worker phase and abort, creating valid marker & archive for worker phase
        self.update_state(phase="worker", child_pid=None)
        worker_digest = self.digest()
        with redirect_stdout(io.StringIO()):
            abort(self.root, RUN_ID, worker_digest)

        # Probe 1: Restore exact complete bytes; marker and archive now mismatch
        self.checkpoint.write_bytes(complete_bytes)
        task_path = Path(self.temp.name) / "task.json"
        task_path.write_text(json.dumps({
            "schema_version": 1,
            "specification": "New task",
            "allowed_files_to_modify": ["app.py"],
        }), encoding="utf-8")

        result = self.omp("run", str(task_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OMP:", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), complete_bytes)

        # Probe 2: Malformed marker {}
        marker_file = self.root / ".omp/runs" / RUN_ID / "aborted.json"
        marker_file.write_text("{}", encoding="utf-8")
        result = self.omp("run", str(task_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OMP:", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), complete_bytes)

        # Probe 3: Missing archive
        archive_file = self.root / ".omp/runs" / RUN_ID / "aborted-state.json"
        if archive_file.exists():
            archive_file.unlink()
        # Restore valid marker matching complete bytes
        marker_data = {
            "schema_version": 1,
            "action": "abort",
            "run_id": RUN_ID,
            "state_sha256": self.digest(),
            "child_pid": None,
            "created_utc": "2026-10-23T00:00:00.000000Z",
        }
        marker_file.write_text(json.dumps(marker_data), encoding="utf-8")
        result = self.omp("run", str(task_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OMP:", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), complete_bytes)

        # Probe 4: Archive digest mismatch
        archive_file.write_text('{"different": true}', encoding="utf-8")
        result = self.omp("run", str(task_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OMP:", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), complete_bytes)

        # Probe 5: Abort marker records child_pid
        archive_file.write_bytes(complete_bytes)
        marker_data["child_pid"] = 99999
        marker_file.write_text(json.dumps(marker_data), encoding="utf-8")
        result = self.omp("run", str(task_path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OMP:", result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), complete_bytes)

    def test_r3_audit_records_unknown_after_digest_when_digest_read_fails(self) -> None:
        dead = self.dead_pid()
        self.prepare(phase="worker", checkout=True, child_pid=dead)
        before_bytes = self.checkpoint.read_bytes()
        before_digest = self.digest()

        with patch(
            "omp_core.main._read_state_digest",
            side_effect=OperationError("faulted digest read after save"),
        ):
            with self.assertRaises(OperationError) as caught:
                recover(self.root, RUN_ID, before_digest, True)

        self.assertIn("digest could not be read", str(caught.exception).lower())
        self.assertIsInstance(caught.exception.__cause__, OperationError)

        # Exact checkpoint on disk: child_pid was cleared!
        state_after = self.read_state()
        self.assertIsNone(state_after["child_pid"])
        self.assertNotEqual(self.checkpoint.read_bytes(), before_bytes)

        # Audit event verification:
        events = self.audit_events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["result"], "error")
        self.assertEqual(event["reason_code"], "checkpoint_digest_failed")
        self.assertTrue(event["checkpoint_changed"])
        self.assertEqual(event["child_pid_before"], dead)
        self.assertIsNone(event["child_pid_after"])
        self.assertEqual(event["before_sha256"], before_digest)
        # R3 requirement: after_sha256 must distinguish known from unknown, not reuse before hash!
        self.assertEqual(event["after_sha256"], "unknown")
        self.assertNotEqual(event["after_sha256"], before_digest)


if __name__ == "__main__":
    unittest.main()
