"""Recovery and transition tests for REC-01."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core.main import (
    InputError,
    OperationError,
    advance,
    git,
    git_object_id_length,
    save,
    validate,
    validate_state,
)

BASE_COMMIT = "a" * 40
SHA256_COMMIT = "b" * 64


def valid_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "worker_argv": [sys.executable, "-c", "pass"],
        "test_argv": [sys.executable, "-B", "-c", "import app; assert app.value == 1"],
        "timeout_seconds": 10,
    }


def valid_task() -> dict[str, object]:
    return {
        "schema_version": 1,
        "specification": "Set value to 1",
        "allowed_files_to_modify": ["app.py"],
    }


def valid_state_v2() -> dict[str, object]:
    return {
        "schema_version": 2,
        "run_id": "run-rec-1",
        "phase": "preparing",
        "base_commit": BASE_COMMIT,
        "config": valid_config(),
        "task": valid_task(),
        "child_pid": None,
    }


class RecoveryTestCase(unittest.TestCase):
    """Disposable Git repository for recovery checks."""

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


class TransitionGuardTests(unittest.TestCase):
    """AC1: Transition table and invariant enforcement."""

    def test_invalid_transitions_are_rejected(self) -> None:
        state = valid_state_v2()
        # preparing can only transition to worker
        for target in ("verifying", "committing", "complete", "preparing"):
            with self.subTest(source="preparing", target=target):
                s = dict(state)
                with self.assertRaises(OperationError):
                    pipeline.transition(s, target)

        state["phase"] = "worker"
        # worker can only transition to verifying
        for target in ("preparing", "committing", "complete", "worker"):
            with self.subTest(source="worker", target=target):
                s = dict(state)
                with self.assertRaises(OperationError):
                    pipeline.transition(s, target)

    def test_transition_to_verifying_requires_cleared_child_pid(self) -> None:
        state = valid_state_v2()
        state["phase"] = "worker"
        state["child_pid"] = 1234
        with self.assertRaises(OperationError):
            pipeline.transition(state, "verifying")

    def test_transition_to_committing_requires_verification_evidence(self) -> None:
        state = valid_state_v2()
        state["phase"] = "verifying"
        with self.assertRaises(OperationError):
            pipeline.transition(state, "committing")

    def test_transition_to_complete_requires_valid_evidence_and_result(self) -> None:
        state = valid_state_v2()
        state["phase"] = "committing"
        # missing result_commit
        with self.assertRaises(OperationError):
            pipeline.transition(state, "complete")

        state["result_commit"] = BASE_COMMIT
        # missing completion_kind
        with self.assertRaises(OperationError):
            pipeline.transition(state, "complete")

        state["completion_kind"] = "committed"
        # missing verification_evidence
        with self.assertRaises(OperationError):
            pipeline.transition(state, "complete")

        state["verification_evidence"] = {
            "candidate_tree": "c" * 40,
            "command_hash": "d" * 64,
            "task_hash": "e" * 64,
            "run_id": state["run_id"],
            "exit_code": 0,
        }
        # missing commit_intent
        with self.assertRaises(OperationError):
            pipeline.transition(state, "complete")

        state["commit_intent"] = {
            "run_id": state["run_id"],
            "base_commit": state["base_commit"],
            "expected_tree": "c" * 40,
            "commit_marker": "marker",
        }
        # valid transition succeeds
        pipeline.transition(state, "complete")
        self.assertEqual(state["phase"], "complete")

        # no_change with mismatched result_commit is rejected
        state["phase"] = "committing"
        state["completion_kind"] = "no_change"
        state["result_commit"] = "e" * 40
        with self.assertRaises(OperationError):
            pipeline.transition(state, "complete")


class CandidateTreeIdentityTests(RecoveryTestCase):
    """AC2: Candidate tree identity and scope."""

    def test_candidate_tree_reflects_changes_without_mutating_source_index(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        # advance past preparing to worker
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")
        self.assertIn("verification_evidence", state)
        self.assertEqual(state["completion_kind"], "committed")

        # source index must remain untouched at base
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 0\n")

    def test_candidate_tree_with_allowed_new_file(self) -> None:
        # Worker creates a new file allowed by task
        config = {
            "schema_version": 1,
            "worker_argv": [sys.executable, "-c", "from pathlib import Path; Path('new_feat.py').write_text('feat = True\\n')"],
            "test_argv": [sys.executable, "-B", "-c", "import new_feat; assert new_feat.feat"],
            "timeout_seconds": 10,
        }
        task = {
            "schema_version": 1,
            "specification": "Add new_feat.py",
            "allowed_files_to_modify": ["new_feat.py"],
        }
        validate(config, task)
        omp = self.root / ".omp"
        omp.mkdir(parents=True, exist_ok=True)
        (omp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        save(omp / "state.json", {
            "schema_version": 2,
            "run_id": "new-file-run",
            "phase": "preparing",
            "base_commit": self.base,
            "config": config,
            "task": task,
            "child_pid": None,
        })
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["completion_kind"], "committed")
        checkout = self.root / ".omp/runs/new-file-run/checkout"
        self.assertTrue((checkout / "new_feat.py").exists())

    def test_candidate_tree_with_tracked_deletion(self) -> None:
        # Worker deletes a tracked file
        config = {
            "schema_version": 1,
            "worker_argv": [sys.executable, "-c", "from pathlib import Path; Path('app.py').unlink()"],
            "test_argv": [sys.executable, "-B", "-c", "from pathlib import Path; assert not Path('app.py').exists()"],
            "timeout_seconds": 10,
        }
        task = {
            "schema_version": 1,
            "specification": "Remove app.py",
            "allowed_files_to_modify": ["app.py"],
        }
        validate(config, task)
        omp = self.root / ".omp"
        omp.mkdir(parents=True, exist_ok=True)
        (omp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        save(omp / "state.json", {
            "schema_version": 2,
            "run_id": "deletion-run",
            "phase": "preparing",
            "base_commit": self.base,
            "config": config,
            "task": task,
            "child_pid": None,
        })
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["completion_kind"], "committed")
        checkout = self.root / ".omp/runs/deletion-run/checkout"
        self.assertFalse((checkout / "app.py").exists())


class VerificationEvidenceTests(RecoveryTestCase):
    """AC3: Verification evidence binding and invalidation."""

    def test_test_modifying_candidate_invalidates_evidence(self) -> None:
        # Worker modifies app.py, but test_argv further modifies app.py
        worker = "from pathlib import Path; Path('app.py').write_text('value = 1\\n')"
        sneaky_test = (
            "from pathlib import Path; "
            "import app; assert app.value == 1; "
            "Path('app.py').write_text('value = 2\\n')"
        )
        self.prepare(worker, test_code=sneaky_test)
        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("modified", str(caught.exception).lower())
        state = self.read_state()
        self.assertNotEqual(state.get("phase"), "complete")

    def test_modified_code_after_verification_blocks_commit(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        # Run until verifying completes (manually simulate stopping before committing)
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Rewind to committing phase
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]

        # Mutate app.py in checkout so candidate tree changes
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        # Reset checkout to base_commit first to simulate interrupted state before commit
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 999\n", encoding="utf-8")
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match verification evidence", str(caught.exception).lower())

    def test_modified_test_argv_after_verification_blocks_commit(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()

        # Rewind to committing phase and alter config test_argv
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]
        state["config"]["test_argv"] = [sys.executable, "-c", "import sys; sys.exit(0)"]
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("test command does not match", str(caught.exception).lower())


class CommitIntentAndCrashRecoveryTests(RecoveryTestCase):
    """AC4: Commit intent, crash reconciliation, and no duplicate commits."""

    def test_crash_after_commit_before_complete_is_idempotent_on_resume(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        # Advance until run completes
        advance(self.root)
        completed_state = self.read_state()
        self.assertEqual(completed_state["phase"], "complete")
        commit_oid = completed_state["result_commit"]

        # Simulate crash after commit: rewind state phase to committing with commit_intent preserved
        simulated_state = dict(completed_state)
        simulated_state["phase"] = "committing"
        del simulated_state["result_commit"]
        del simulated_state["completion_kind"]
        save(self.root / ".omp/state.json", simulated_state)

        # Resume should detect existing commit matching intent and complete cleanly without creating duplicate
        advance(self.root)
        resumed_state = self.read_state()
        self.assertEqual(resumed_state["phase"], "complete")
        self.assertEqual(resumed_state["result_commit"], commit_oid)
        self.assertEqual(resumed_state["completion_kind"], "committed")

    def test_unexpected_commit_during_recovery_is_rejected(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()

        # Rewind to committing with commit_intent pointing to a different expected tree
        simulated_state = dict(completed_state)
        simulated_state["phase"] = "committing"
        del simulated_state["result_commit"]
        del simulated_state["completion_kind"]
        simulated_state["commit_intent"]["expected_tree"] = "0" * 40
        save(self.root / ".omp/state.json", simulated_state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match commit intent", str(caught.exception).lower())

    def test_missing_commit_intent_during_recovery_is_rejected(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()

        # Rewind to committing but strip commit_intent
        simulated_state = dict(completed_state)
        simulated_state["phase"] = "committing"
        del simulated_state["result_commit"]
        del simulated_state["completion_kind"]
        del simulated_state["commit_intent"]
        save(self.root / ".omp/state.json", simulated_state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("without commit intent", str(caught.exception).lower())


class NoOpAndResumeCompleteTests(RecoveryTestCase):
    """AC5: No-op completion and complete state verification."""

    def test_noop_task_completes_as_no_change_without_empty_commit(self) -> None:
        # Worker makes no changes to app.py
        self.prepare("pass", test_code="import app; assert app.value == 0")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["completion_kind"], "no_change")
        self.assertEqual(state["result_commit"], self.base)

        # Idempotent resume
        advance(self.root)
        self.assertEqual(self.read_state()["phase"], "complete")

    def test_resume_complete_rejects_dirty_checkout(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Dirty the checkout in .omp/runs/<run_id>/checkout
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        (checkout / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(OperationError):
            advance(self.root)

    def test_resume_complete_rejects_head_mismatch(self) -> None:
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        # Reset checkout to base commit (causing HEAD mismatch with result_commit)
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match result_commit", str(caught.exception).lower())


class SchemaV2AndLegacyV1CompatibilityTests(RecoveryTestCase):
    """AC6: Checkpoint v2 strictness and v1 rejection on resume."""

    def test_legacy_v1_unfinished_state_cannot_be_resumed(self) -> None:
        # Prepare state with schema_version 1
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')", schema_version=1)
        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("schema_version 1", str(caught.exception))
        # state bytes must be preserved
        raw = (self.root / ".omp/state.json").read_bytes()
        self.assertIn(b'"schema_version": 1', raw)

    def test_legacy_v1_complete_state_is_readable_by_status(self) -> None:
        # Prepare state with schema_version 1 and phase complete
        state = {
            "schema_version": 1,
            "run_id": "legacy-done",
            "phase": "complete",
            "base_commit": self.base,
            "result_commit": self.base,
            "config": valid_config(),
            "task": valid_task(),
            "child_pid": None,
        }
        save(self.root / ".omp/state.json", state)
        bytes_before = (self.root / ".omp/state.json").read_bytes()
        # _read_state accepts v1 complete without rewriting
        read_back = pipeline._read_state(self.root, 40)
        self.assertEqual(read_back["schema_version"], 1)
        self.assertEqual((self.root / ".omp/state.json").read_bytes(), bytes_before)

    def test_validate_state_rejects_corrupted_evidence_and_intent(self) -> None:
        base = valid_state_v2()
        base["phase"] = "committing"
        base["commit_intent"] = {
            "run_id": base["run_id"],
            "base_commit": base["base_commit"],
            "expected_tree": "a" * 40,
            "commit_marker": "marker",
        }

        # Evidence with exit code != 0
        s = dict(base)
        s["verification_evidence"] = {
            "candidate_tree": "a" * 40,
            "command_hash": "b" * 64,
            "task_hash": "c" * 64,
            "run_id": s["run_id"],
            "exit_code": 1,
        }
        with self.assertRaises(OperationError):
            validate_state(s)

        # Evidence with invalid command_hash length
        s = dict(base)
        s["verification_evidence"] = {
            "candidate_tree": "a" * 40,
            "command_hash": "short",
            "task_hash": "c" * 64,
            "run_id": s["run_id"],
            "exit_code": 0,
        }
        with self.assertRaises(OperationError):
            validate_state(s)

        # Intent with mismatched run_id
        s = dict(base)
        s["verification_evidence"] = {
            "candidate_tree": "a" * 40,
            "command_hash": "b" * 64,
            "task_hash": "c" * 64,
            "run_id": s["run_id"],
            "exit_code": 0,
        }
        s["commit_intent"] = {
            "run_id": "other-run",
            "base_commit": s["base_commit"],
            "expected_tree": "a" * 40,
            "commit_marker": "marker",
        }
        with self.assertRaises(OperationError):
            validate_state(s)


    def test_modified_task_specification_after_verification_blocks_commit(self) -> None:
        """REC01-R1: Altering task specification after verification invalidates evidence."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Rewind to committing phase and alter task specification
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]
        state["task"]["specification"] = "Malicious altered task spec"
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("task does not match", str(caught.exception).lower())

    def test_commit_intent_must_match_evidence_tree(self) -> None:
        """REC01-R2: commit_intent expected_tree must match verification_evidence candidate_tree."""
        base = valid_state_v2()
        base["phase"] = "committing"
        base["verification_evidence"] = {
            "candidate_tree": "c" * 40,
            "command_hash": "d" * 64,
            "task_hash": "e" * 64,
            "run_id": base["run_id"],
            "exit_code": 0,
        }
        base["commit_intent"] = {
            "run_id": base["run_id"],
            "base_commit": base["base_commit"],
            "expected_tree": "f" * 40,  # Mismatched tree!
            "commit_marker": "marker",
        }
        with self.assertRaises(OperationError):
            validate_state(base)

    def test_transition_to_committing_requires_intent_and_matching_evidence(self) -> None:
        """REC01-R2: transition to committing requires valid verification evidence AND intent."""
        state = valid_state_v2()
        state["phase"] = "verifying"
        # Only truthy evidence with nonzero exit code
        state["verification_evidence"] = {
            "candidate_tree": "c" * 40,
            "command_hash": "d" * 64,
            "task_hash": "e" * 64,
            "run_id": state["run_id"],
            "exit_code": 1,
        }
        with self.assertRaises(OperationError):
            pipeline.transition(state, "committing")

    def test_recovery_rejects_commit_if_not_matching_evidence_tree(self) -> None:
        """REC01-R2: crash recovery commit matching intent but differing from evidence tree is rejected."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()

        # Rewind to committing, keep intent pointing to commit tree, but set evidence tree to baseline
        simulated_state = dict(completed_state)
        simulated_state["phase"] = "committing"
        del simulated_state["result_commit"]
        del simulated_state["completion_kind"]
        # Tamper evidence candidate_tree
        simulated_state["verification_evidence"]["candidate_tree"] = self.git("rev-parse", f"{self.base}^{{tree}}")
        save(self.root / ".omp/state.json", simulated_state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match", str(caught.exception).lower())

    def test_complete_resumed_with_stale_command_is_rejected(self) -> None:
        """REC01-R3: complete resume validates command and task identity against evidence."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Tamper config test_argv in complete state
        state["config"]["test_argv"] = [sys.executable, "-c", "import sys; sys.exit(1)"]
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match", str(caught.exception).lower())

    def test_complete_no_change_with_corrupt_evidence_tree_is_rejected(self) -> None:
        """REC01-R3: no_change complete resume validates candidate_tree against baseline tree."""
        self.prepare("pass", test_code="import app; assert app.value == 0")
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["completion_kind"], "no_change")

        # Tamper evidence candidate_tree to valid syntax but wrong tree
        state["verification_evidence"]["candidate_tree"] = "0" * 40
        if "commit_intent" in state:
            state["commit_intent"]["expected_tree"] = "0" * 40
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match", str(caught.exception).lower())

    def test_legacy_v1_complete_cannot_be_resumed_to_verified_completion(self) -> None:
        """REC01-R3: legacy v1 complete state cannot be resumed via advance()."""
        state = {
            "schema_version": 1,
            "run_id": "legacy-done",
            "phase": "complete",
            "base_commit": self.base,
            "result_commit": self.base,
            "config": valid_config(),
            "task": valid_task(),
            "child_pid": None,
        }
        checkout = self.root / ".omp/runs/legacy-done/checkout"
        checkout.parent.mkdir(parents=True, exist_ok=True)
        # Clone checkout cleanly
        subprocess.check_call(["git", "clone", "--local", "--no-hardlinks", str(self.root), str(checkout)], stderr=subprocess.DEVNULL)
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("schema_version 1", str(caught.exception))

    def test_allowed_files_with_absent_optional_file_commits_successfully(self) -> None:
        """REC01-R4: allowed files that were never created by worker do not fail git add."""
        config = {
            "schema_version": 1,
            "worker_argv": [sys.executable, "-c", "from pathlib import Path; Path('app.py').write_text('value = 1\\n')"],
            "test_argv": [sys.executable, "-B", "-c", "import app; assert app.value == 1"],
            "timeout_seconds": 10,
        }
        task = {
            "schema_version": 1,
            "specification": "Modify app.py, optional.py is optional",
            "allowed_files_to_modify": ["app.py", "optional.py"],
        }
        validate(config, task)
        omp = self.root / ".omp"
        omp.mkdir(parents=True, exist_ok=True)
        (omp / "config.json").write_text(json.dumps(config), encoding="utf-8")
        save(omp / "state.json", {
            "schema_version": 2,
            "run_id": "absent-opt-run",
            "phase": "preparing",
            "base_commit": self.base,
            "config": config,
            "task": task,
            "child_pid": None,
        })
        # Before fix, advance will fail with "fatal: pathspec 'optional.py' did not match any files"
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")
        self.assertEqual(state["completion_kind"], "committed")

    def test_modified_timeout_after_verification_blocks_commit_and_complete(self) -> None:
        """REC01-R1a: altering timeout_seconds after verification invalidates evidence."""
        self.prepare(
            "from pathlib import Path; Path('app.py').write_text('value = 1\\n')",
            test_code="import time, app; time.sleep(0.01); assert app.value == 1",
        )
        advance(self.root)
        state = self.read_state()
        self.assertEqual(state["phase"], "complete")

        # Probe 1: alter timeout_seconds in complete run
        state["config"]["timeout_seconds"] = 1
        save(self.root / ".omp/state.json", state)
        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match verification evidence", str(caught.exception).lower())

        # Probe 2: rewind to committing with altered timeout
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
        save(self.root / ".omp/state.json", state)

        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("does not match verification evidence", str(caught.exception).lower())

    def test_transition_rejects_malformed_evidence_and_missing_or_bad_intent(self) -> None:
        """REC01-R2a: transition to committing/complete requires strict evidence and intent validation."""
        state = valid_state_v2()
        state["phase"] = "verifying"

        # Case 1: bool exit_code (False == 0) and intent missing expected_tree
        state["verification_evidence"] = {
            "candidate_tree": "a" * 40,
            "command_hash": "b" * 64,
            "task_hash": "c" * 64,
            "run_id": state["run_id"],
            "exit_code": False,
        }
        state["commit_intent"] = {
            "run_id": state["run_id"],
            "base_commit": state["base_commit"],
            "expected_tree": None,
            "commit_marker": "marker",
        }
        with self.assertRaises(OperationError):
            pipeline.transition(state, "committing")

        # Case 2: missing candidate_tree in evidence
        del state["verification_evidence"]["candidate_tree"]
        state["verification_evidence"]["exit_code"] = 0
        state["commit_intent"]["expected_tree"] = "a" * 40
        with self.assertRaises(OperationError):
            pipeline.transition(state, "committing")

    def test_committing_phase_without_intent_is_rejected_without_reconstruction(self) -> None:
        """REC01-R2a: committing phase without commit_intent is rejected, not reconstructed."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        completed_state = self.read_state()
        self.assertEqual(completed_state["phase"], "complete")

        # Rewind to committing, reset index/HEAD to baseline, but strip commit_intent
        state = dict(completed_state)
        state["phase"] = "committing"
        del state["result_commit"]
        del state["completion_kind"]
        del state["commit_intent"]
        checkout = self.root / ".omp/runs" / str(state["run_id"]) / "checkout"
        subprocess.check_call(["git", "-C", str(checkout), "reset", "--hard", self.base], stderr=subprocess.DEVNULL)
        (self.root / ".omp/state.json").write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(OperationError):
            validate_state(state)

        # Before fix, advance() reconstructed missing intent and completed. Must fail closed.
        with self.assertRaises(OperationError) as caught:
            advance(self.root)
        self.assertIn("commit_intent", str(caught.exception).lower())

    def test_first_run_commit_verifies_actual_commit_tree_parent_and_marker(self) -> None:
        """REC01-R5: first-run commit validates actual HEAD tree/parent/marker against intent/evidence."""
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")

        real_git = pipeline.git
        def injected_git(repo, *args, **kwargs):
            res = real_git(repo, *args, **kwargs)
            # Inject commit-time corruption (simulating hook tampering)
            if args and "commit" in args and "-m" in args:
                real_git(repo, "-c", "user.name=OMP", "-c", "user.email=omp@localhost",
                         "commit", "--amend", "-m", "corrupted commit subject", "--allow-empty")
            return res

        with unittest.mock.patch("omp_core.main.git", side_effect=injected_git):
            with self.assertRaises(OperationError) as caught:
                advance(self.root)
            self.assertIn("does not match commit intent or verification evidence", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
