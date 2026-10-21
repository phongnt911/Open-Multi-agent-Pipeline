"""Integration checks without AI calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omp_core.main import advance, lock, save, validate


class PipelineTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".omp/test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@localhost")
        self.git("config", "user.name", "Test")
        (self.root / ".gitignore").write_text(".omp/\n__pycache__/\n")
        (self.root / "app.py").write_text("value = 0\n")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def prepare(self, worker, test="import app; assert app.value == 1"):
        config = {"worker_argv": [sys.executable, "-c", worker],
                  "test_argv": [sys.executable, "-B", "-c", test], "timeout_seconds": 10}
        task = {"specification": "Set value to 1", "allowed_files_to_modify": ["app.py"]}
        validate(config, task)
        self.checkpoint = self.root / ".omp/state.json"
        save(self.checkpoint, {"run_id": "integration", "phase": "preparing",
                              "base_commit": self.base, "config": config,
                              "task": task, "child_pid": None})

    def state(self):
        return json.loads(self.checkpoint.read_text())

    def test_success_and_resume_preserve_original(self):
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        advance(self.root)
        result = self.state()
        self.assertEqual(result["phase"], "complete")
        self.assertEqual((self.root / "app.py").read_text(), "value = 0\n")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        result["phase"] = "committing"
        save(self.checkpoint, result)
        advance(self.root)
        self.assertEqual(self.state()["result_commit"], result["result_commit"])

    def test_failed_tests_never_complete(self):
        self.prepare("pass")
        with self.assertRaises(ValueError):
            advance(self.root)
        self.assertNotEqual(self.state()["phase"], "complete")
        self.assertIsNone(self.state()["child_pid"])

    def test_out_of_scope_changes_are_retained_and_rejected(self):
        self.prepare("from pathlib import Path; Path('extra.txt').write_text('oops')")
        with self.assertRaisesRegex(ValueError, "outside task scope"):
            advance(self.root)
        self.assertFalse((self.root / "extra.txt").exists())
        self.assertTrue((self.root / ".omp/runs/integration/checkout/extra.txt").exists())

    def test_uncertain_child_blocks_resume(self):
        self.prepare("pass")
        state = self.state()
        state["child_pid"] = 12345
        save(self.checkpoint, state)
        with self.assertRaisesRegex(ValueError, "may still be running"):
            advance(self.root)

    def test_timeout_stops_worker_and_keeps_checkpoint(self):
        self.prepare("import time; time.sleep(30)")
        state = self.state()
        state["config"]["timeout_seconds"] = 1
        save(self.checkpoint, state)
        with self.assertRaises(subprocess.TimeoutExpired):
            advance(self.root)
        self.assertEqual(self.state()["phase"], "worker")
        self.assertIsNone(self.state()["child_pid"])

    def test_interrupted_worker_resumes_with_existing_changes(self):
        self.prepare("from pathlib import Path; Path('app.py').write_text('value = 1\\n')")
        from omp_core import main as pipeline
        original_execute = pipeline.execute

        def interrupt_after_worker(*args, **kwargs):
            original_execute(*args, **kwargs)
            raise KeyboardInterrupt()

        with patch.object(pipeline, "execute", side_effect=interrupt_after_worker):
            with self.assertRaises(KeyboardInterrupt):
                advance(self.root)
        self.assertEqual(self.state()["phase"], "worker")
        (self.root / "app.py").write_text("user change\n")
        advance(self.root)
        self.assertEqual(self.state()["phase"], "complete")
        self.assertEqual((self.root / "app.py").read_text(), "user change\n")

    def test_exclusive_project_lock(self):
        with lock(self.root / ".omp/lock"):
            with self.assertRaises(ValueError):
                with lock(self.root / ".omp/lock"):
                    self.fail("Second lock acquired")

    def test_reject_traversal(self):
        with self.assertRaises(ValueError):
            validate({"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": 1},
                     {"specification": "x", "allowed_files_to_modify": ["../outside"]})


if __name__ == "__main__":
    unittest.main()
