"""Error classification tests for the MVP boundary."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core.main import (
    InputError,
    OmpError,
    OperationError,
    execute,
    git,
    validate,
)


class ErrorClassificationTests(unittest.TestCase):
    def test_validate_raises_input_error(self) -> None:
        config = {"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": 0}
        task = {"specification": "x", "allowed_files_to_modify": ["ok.py"]}
        with self.assertRaises(InputError) as caught:
            validate(config, task)
        self.assertIsInstance(caught.exception, OmpError)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertNotIsInstance(caught.exception, OperationError)

    def test_git_failure_raises_operation_error(self) -> None:
        missing = Path("missing-directory-for-omp-error-test")
        with self.assertRaises(OperationError) as caught:
            git(missing, "status")
        self.assertIsInstance(caught.exception, OmpError)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertNotIsInstance(caught.exception, InputError)

    def test_missing_executable_raises_operation_error_with_cause(self) -> None:
        missing = "missing-omp-executable-for-test"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with self.assertRaises(OperationError) as caught:
                execute([missing], root, root, state, root / "state.json", "worker")
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertNotIn(missing, str(caught.exception))

    def test_execute_nul_argument_raises_operation_error_without_echo(self) -> None:
        secret = "REVIEW-NUL-SECRET"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            argv = [sys.executable, "-c", "pass", f"--token={secret}\x00"]
            with self.assertRaises(OperationError) as caught:
                execute(argv, root, root, state, root / "state.json", "worker")
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("--token", str(caught.exception))

    def test_stop_timeout_hides_argv(self) -> None:
        secret = "REVIEW-FAKE-SECRET"

        class FakeProcess:
            pid = 4242

            def wait(self, timeout: float | None = None) -> None:
                raise subprocess.TimeoutExpired(
                    ["worker", f"--token={secret}"], timeout or 1
                )

        with patch.object(pipeline.subprocess, "run"), \
                patch.object(pipeline.os, "killpg", create=True):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                pipeline.stop(FakeProcess(), "worker")
        public = caught.exception
        self.assertNotIn(secret, str(public))
        self.assertNotIn("--token", str(public))
        error: BaseException | None = public
        seen: set[int] = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            error = error.__cause__ or error.__context__

    def test_cleanup_timeout_hides_argv_from_public_exception(self) -> None:
        secret = "REVIEW-FAKE-SECRET"

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

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with patch.object(pipeline.subprocess, "Popen", FakeProcess), \
                    patch.object(pipeline, "stop", side_effect=cleanup_timeout):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    execute(
                        [sys.executable, "-c", "pass"], root, root, state,
                        root / "state.json", "worker",
                    )
        public = caught.exception
        self.assertNotIn(secret, str(public))
        self.assertNotIn("--token", str(public))
        cause = public.__cause__
        self.assertIsNotNone(cause)
        self.assertIsInstance(cause, subprocess.TimeoutExpired)
        self.assertIn("worker", str(cause))
        self.assertIn("timed out", str(cause))
        self.assertNotIn(secret, str(cause))
        self.assertNotIn("--token", str(cause))
        error: BaseException | None = public
        seen: set[int] = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            error = error.__cause__ or error.__context__


if __name__ == "__main__":
    unittest.main()
