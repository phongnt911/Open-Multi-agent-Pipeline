"""Error classification tests for the MVP boundary."""
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator
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
from omp_core.windows_job import WindowsJobCleanupTimeout, WindowsJobError


class ErrorClassificationTests(unittest.TestCase):
    def _walk_causes(self, error: BaseException) -> Iterator[BaseException]:
        pending = [error]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            yield current
            if current.__context__ is not None:
                pending.append(current.__context__)
            if current.__cause__ is not None:
                pending.append(current.__cause__)

    def _assert_chain_clean(self, error: BaseException, secret: str) -> None:
        for item in self._walk_causes(error):
            self.assertNotIn(secret, str(item))
            self.assertNotIn(secret, repr(item))
            self.assertNotIn("--token", str(item))
            self.assertNotIn("--token", repr(item))

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
            with patch.object(
                    pipeline, "_start_process",
                    return_value=(FakeProcess(), None)), \
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


    def test_combined_operation_and_cleanup_timeout_preserves_both_diagnostics(self) -> None:
        secret = "REVIEW-COMBINED-TIMEOUT-SECRET"

        class FakeProcess:
            pid = 4242
            returncode = None

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
            del process, label
            raise subprocess.TimeoutExpired(
                ["worker", f"--token={secret}"], 10
            ) from WindowsJobCleanupTimeout(
                "job still has 1 active process(es) after 10s"
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with patch.object(
                    pipeline, "_start_process",
                    return_value=(FakeProcess(), None)), \
                    patch.object(pipeline, "stop", side_effect=cleanup_timeout):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    execute(
                        [sys.executable, "-c", "pass", f"--token={secret}"],
                        root, root, state, root / "state.json", "worker",
                    )
        public = caught.exception
        self.assertIsInstance(public, subprocess.TimeoutExpired)
        self.assertIn("worker cleanup", str(public))
        cleanup_root = public.__cause__
        self.assertIsInstance(cleanup_root, WindowsJobCleanupTimeout)
        self.assertIsNotNone(cleanup_root.__cause__)
        self.assertIsInstance(cleanup_root.__cause__, subprocess.TimeoutExpired)
        messages = [str(item) for item in self._walk_causes(public)]
        self.assertTrue(any("job still has" in text for text in messages))
        self.assertTrue(
            any("timed out" in text and "worker" in text for text in messages)
        )
        self.assertEqual(state["child_pid"], 4242)
        self._assert_chain_clean(public, secret)

    def test_combined_operation_error_and_win32_cleanup_failure_keeps_both(self) -> None:
        secret = "REVIEW-COMBINED-WIN32-SECRET"

        class FakeProcess:
            pid = 4242
            returncode = None

            def communicate(
                self, input: bytes | None = None, timeout: float | None = None
            ) -> tuple[bytes, bytes]:
                del input, timeout
                raise OperationError(f"worker failed {secret}")

            def poll(self) -> int | None:
                return None

        def cleanup_failure(
            process: subprocess.Popen[bytes], label: str = "child"
        ) -> None:
            del process, label
            raise WindowsJobError(f"cleanup failed {secret}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with patch.object(
                    pipeline, "_start_process",
                    return_value=(FakeProcess(), None)), \
                    patch.object(pipeline, "stop", side_effect=cleanup_failure):
                with self.assertRaises(OperationError) as caught:
                    execute(
                        [sys.executable, "-c", "pass", f"--token={secret}"],
                        root, root, state, root / "state.json", "worker",
                    )
        public = caught.exception
        self.assertIn("worker cleanup failed", str(public))
        cleanup_root = public.__cause__
        self.assertIsInstance(cleanup_root, WindowsJobError)
        self.assertIsInstance(cleanup_root.__cause__, OperationError)
        messages = [str(item) for item in self._walk_causes(public)]
        self.assertTrue(any("cleanup failed" in text for text in messages))
        self.assertTrue(any("worker failed" in text for text in messages))
        self.assertEqual(state["child_pid"], 4242)
        self._assert_chain_clean(public, secret)

    def test_final_save_failure_keeps_operation_and_cleanup_chain(self) -> None:
        secret = "REVIEW-COMBINED-SAVE-SECRET"

        class FakeProcess:
            pid = 4242
            returncode = None

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
            del process, label
            raise subprocess.TimeoutExpired(
                ["worker", f"--token={secret}"], 10
            ) from WindowsJobCleanupTimeout(
                "job still has 1 active process(es) after 10s"
            )

        real_save = pipeline.save
        calls = 0

        def failing_save(path: Path, value: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(f"final save failed {secret}")
            real_save(path, value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with patch.object(
                    pipeline, "_start_process",
                    return_value=(FakeProcess(), None)), \
                    patch.object(pipeline, "stop", side_effect=cleanup_timeout), \
                    patch.object(pipeline, "save", side_effect=failing_save):
                with self.assertRaises(OSError) as caught:
                    execute(
                        [sys.executable, "-c", "pass", f"--token={secret}"],
                        root, root, state, root / "state.json", "worker",
                    )
        public = caught.exception
        self.assertIn("final save failed", str(public))
        cleanup_root = public.__cause__
        self.assertIsInstance(cleanup_root, subprocess.TimeoutExpired)
        self.assertIsInstance(cleanup_root.__cause__, WindowsJobCleanupTimeout)
        self.assertIsInstance(
            cleanup_root.__cause__.__cause__, subprocess.TimeoutExpired
        )
        messages = [str(item) for item in self._walk_causes(public)]
        self.assertTrue(any("job still has" in text for text in messages))
        self.assertTrue(any("timed out" in text for text in messages))
        self.assertEqual(state["child_pid"], 4242)
        self._assert_chain_clean(public, secret)

    def test_operation_windows_error_keeps_nested_winerror_across_cleanup_and_save(self) -> None:
        secret = "REVIEW-NESTED-WINERROR-SECRET"

        class FakeProcess:
            pid = 4242
            returncode = None

            def communicate(
                self, input: bytes | None = None, timeout: float | None = None
            ) -> tuple[bytes, bytes]:
                del input, timeout
                inner = WindowsJobError(
                    "query job accounting failed "
                    f"(WinError 5: injected access denied) {secret}"
                )
                outer = WindowsJobError(
                    "cannot verify contained process tree is empty"
                )
                outer.__cause__ = inner
                outer.__context__ = inner
                raise outer

            def poll(self) -> int | None:
                return None

        def cleanup_failure(
            process: subprocess.Popen[bytes], label: str = "child"
        ) -> None:
            del process, label
            raise WindowsJobError(f"cleanup failed {secret}")

        real_save = pipeline.save
        calls = 0

        def failing_save(path: Path, value: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(f"final save failed {secret}")
            real_save(path, value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {"config": {"timeout_seconds": 1}, "child_pid": None}
            with patch.object(
                    pipeline, "_start_process",
                    return_value=(FakeProcess(), None)), \
                    patch.object(pipeline, "stop", side_effect=cleanup_failure), \
                    patch.object(pipeline, "save", side_effect=failing_save):
                with self.assertRaises(OSError) as caught:
                    execute(
                        [sys.executable, "-c", "pass", f"--token={secret}"],
                        root, root, state, root / "state.json", "worker",
                    )
        public = caught.exception
        self.assertIn("final save failed", str(public))
        cleanup_public = public.__cause__
        self.assertIsInstance(cleanup_public, OperationError)
        self.assertIsInstance(cleanup_public.__cause__, WindowsJobError)
        operation_public = cleanup_public.__cause__.__cause__
        self.assertIsInstance(operation_public, OperationError)
        operation_root = operation_public.__cause__
        self.assertIsInstance(operation_root, WindowsJobError)
        self.assertIsInstance(operation_root.__cause__, WindowsJobError)
        self.assertIn(
            "cannot verify contained process tree is empty",
            str(operation_root),
        )
        self.assertIn(
            "query job accounting failed",
            str(operation_root.__cause__),
        )
        self.assertIn("WinError 5", str(operation_root.__cause__))
        messages = [str(item) for item in self._walk_causes(public)]
        self.assertTrue(any("query job accounting failed" in text
                            for text in messages))
        self.assertTrue(any("WinError 5" in text for text in messages))
        self.assertEqual(state["child_pid"], 4242)
        self._assert_chain_clean(public, secret)


if __name__ == "__main__":
    unittest.main()
