"""CLI and init-generated test runner checks without AI or network calls."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CliTestCase(unittest.TestCase):
    """Create a disposable Git repository with the standard ignore rules."""

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
        (self.root / ".gitignore").write_text(".omp/\n__pycache__/\n", encoding="utf-8")
        (self.root / "app.py").write_text("value = 0\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()

    def omp(self, *args: str, optimize: bool = False) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONOPTIMIZE", None)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        if optimize:
            env["PYTHONOPTIMIZE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "omp_core.main", "--root", str(self.root), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def runner_argv(self) -> list[str]:
        result = self.omp("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((self.root / ".omp/config.json").read_text(encoding="utf-8"))
        argv = config.get("test_argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            self.fail("omp init did not write a string test_argv array")
        return argv

    def write_tests(self, files: dict[str, str]) -> None:
        tests = self.root / "tests"
        if tests.exists():
            shutil.rmtree(tests)
        tests.mkdir()
        for name, content in files.items():
            (tests / name).write_text(content, encoding="utf-8")

    def run_runner(self, argv: list[str], optimize: bool) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONOPTIMIZE", None)
        env.pop("PYTHONPATH", None)
        if optimize:
            env["PYTHONOPTIMIZE"] = "1"
        return subprocess.run(
            argv, cwd=self.root, env=env, text=True, capture_output=True, timeout=60
        )


class InitRunnerTests(CliTestCase):
    PASS_TEST = (
        "import unittest\n\n\n"
        "class Ok(unittest.TestCase):\n"
        "    def test_ok(self) -> None:\n"
        "        self.assertTrue(True)\n"
    )
    FAIL_TEST = (
        "import unittest\n\n\n"
        "class Failing(unittest.TestCase):\n"
        "    def test_fail(self) -> None:\n"
        "        self.assertTrue(False)\n"
    )

    def test_zero_tests_fail_in_normal_and_optimized_modes(self) -> None:
        argv = self.runner_argv()
        self.write_tests({})
        for optimize in (False, True):
            with self.subTest(python_optimize=optimize):
                result = self.run_runner(argv, optimize)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("no tests discovered", result.stderr.lower())

    def test_passing_test_succeeds_in_normal_and_optimized_modes(self) -> None:
        argv = self.runner_argv()
        self.write_tests({"test_ok.py": self.PASS_TEST})
        for optimize in (False, True):
            with self.subTest(python_optimize=optimize):
                result = self.run_runner(argv, optimize)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failing_test_fails_in_normal_and_optimized_modes(self) -> None:
        argv = self.runner_argv()
        self.write_tests({"test_fail.py": self.FAIL_TEST})
        for optimize in (False, True):
            with self.subTest(python_optimize=optimize):
                result = self.run_runner(argv, optimize)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


class CliErrorTests(CliTestCase):
    def write_config(self, config: dict[str, object]) -> None:
        omp = self.root / ".omp"
        omp.mkdir(exist_ok=True)
        (omp / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def write_task(self) -> Path:
        task = {
            "specification": "Set value to 1",
            "allowed_files_to_modify": ["app.py"],
        }
        path = self.root.parent / "task.json"
        path.write_text(json.dumps(task), encoding="utf-8")
        return path

    def test_malformed_config_reports_input_error_without_secret(self) -> None:
        secret = "SECRET-CONFIG-VALUE"
        omp = self.root / ".omp"
        omp.mkdir(exist_ok=True)
        (omp / "config.json").write_text(
            '{"worker_argv": ["codex", "--token=' + secret + '"],}', encoding="utf-8"
        )
        result = self.omp("run", str(self.write_task()))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("OMP:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)
        self.assertFalse((self.root / ".omp/state.json").exists())

    def test_invalid_config_reports_usage_error_without_secret(self) -> None:
        secret = "SECRET-WORKER-TOKEN"
        self.write_config({
            "worker_argv": ["codex", f"--token={secret}"],
            "test_argv": [sys.executable, "-c", "pass"],
            "timeout_seconds": 0,
        })
        result = self.omp("run", str(self.write_task()))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timeout_seconds", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)
        self.assertFalse((self.root / ".omp/state.json").exists())

    def test_worker_failure_reports_operation_error_without_secret(self) -> None:
        secret = "SECRET-PROVIDER-TOKEN"
        self.write_config({
            "worker_argv": [
                sys.executable,
                "-c",
                "import sys; sys.exit(7)",
                f"--token={secret}",
            ],
            "test_argv": [sys.executable, "-B", "-c", "import sys; sys.exit(0)"],
            "timeout_seconds": 10,
        })
        result = self.omp("run", str(self.write_task()))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("worker exited 7", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)

    def test_worker_timeout_does_not_leak_arguments(self) -> None:
        secret = "SECRET-TIMEOUT-TOKEN"
        self.write_config({
            "worker_argv": [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                f"--token={secret}",
            ],
            "test_argv": [sys.executable, "-B", "-c", "import sys; sys.exit(0)"],
            "timeout_seconds": 1,
        })
        result = self.omp("run", str(self.write_task()))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)

    def test_nul_argv_reports_usage_error_without_traceback_or_secret(self) -> None:
        secret = "REVIEW-NUL-SECRET"
        self.write_config({
            "worker_argv": [
                sys.executable,
                "-c",
                "pass",
                f"--token={secret}\x00",
            ],
            "test_argv": [sys.executable, "-B", "-c", "import sys; sys.exit(0)"],
            "timeout_seconds": 10,
        })
        result = self.omp("run", str(self.write_task()))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("OMP:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)
        self.assertNotIn("--token", result.stderr + result.stdout)
        self.assertFalse((self.root / ".omp/state.json").exists())

    def test_cleanup_timeout_hides_argv_from_cli_and_last_error(self) -> None:
        secret = "REVIEW-FAKE-SECRET"
        self.write_config({
            "worker_argv": [
                sys.executable,
                "-c",
                "pass",
                f"--token={secret}",
            ],
            "test_argv": [sys.executable, "-B", "-c", "import sys; sys.exit(0)"],
            "timeout_seconds": 1,
        })
        task = self.write_task()
        from omp_core import main as pipeline

        class FakeProcess:
            pid = 4242
            returncode = None

            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def communicate(
                self, input: bytes | None = None, timeout: float | None = None
            ) -> tuple[bytes, bytes]:
                raise subprocess.TimeoutExpired(
                    ["worker", f"--token={secret}"], timeout or 1
                )

            def poll(self) -> int | None:
                return None

        def cleanup_timeout(
            process: subprocess.Popen[bytes], label: str = "child"
        ) -> None:
            raise subprocess.TimeoutExpired(["worker", f"--token={secret}"], 10)

        real_popen = pipeline.subprocess.Popen

        class RoutingPopen:
            def __new__(cls, argv: object, *args: object, **kwargs: object) -> object:
                if (isinstance(argv, (list, tuple)) and argv
                        and argv[0] == "git"):
                    return real_popen(argv, *args, **kwargs)
                return FakeProcess()

        stderr = io.StringIO()
        with patch.object(sys, "argv", [
                "omp", "--root", str(self.root), "run", str(task)
             ]), \
                patch.object(pipeline.subprocess, "Popen", RoutingPopen), \
                patch.object(pipeline, "stop", side_effect=cleanup_timeout), \
                redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                pipeline.main()

        self.assertEqual(raised.exception.code, 1)
        output = stderr.getvalue()
        self.assertIn("timed out", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("--token", output)
        state = json.loads(
            (self.root / ".omp/state.json").read_text(encoding="utf-8")
        )
        self.assertNotIn(secret, state["last_error"])
        self.assertNotIn("--token", state["last_error"])


if __name__ == "__main__":
    unittest.main()
