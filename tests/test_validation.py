"""Schema, version, and boundary validation tests for DAT-01."""
from contextlib import contextmanager, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core.main import InputError, OperationError


BASE_COMMIT = "a" * 40
SHA256_COMMIT = "b" * 64


def valid_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "worker_argv": ["worker"],
        "test_argv": ["tests"],
        "timeout_seconds": 10,
    }


def valid_task() -> dict[str, object]:
    return {
        "schema_version": 1,
        "specification": "Do the task",
        "allowed_files_to_modify": ["src/app.py"],
    }


def valid_state() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-1",
        "phase": "preparing",
        "base_commit": BASE_COMMIT,
        "config": valid_config(),
        "task": valid_task(),
        "child_pid": None,
    }


class ConfigTaskValidationTests(unittest.TestCase):
    def test_unknown_and_invalid_versions_are_rejected(self) -> None:
        config = valid_config()
        config["retry"] = 1
        with self.assertRaisesRegex(InputError, "unknown"):
            pipeline.validate(config, valid_task())

        for version in (2, True, "1", None, 1.0):
            with self.subTest(version=version):
                config = valid_config()
                config["schema_version"] = version
                with self.assertRaises(InputError):
                    pipeline.validate(config, valid_task())

        task = valid_task()
        task["extra"] = "value"
        with self.assertRaisesRegex(InputError, "unknown"):
            pipeline.validate(valid_config(), task)

        for version in (2, False, "1", None):
            with self.subTest(task_version=version):
                task = valid_task()
                task["schema_version"] = version
                with self.assertRaises(InputError):
                    pipeline.validate(valid_config(), task)

    def test_legacy_input_is_normalized_in_memory_only(self) -> None:
        config = valid_config()
        task = valid_task()
        del config["schema_version"]
        del task["schema_version"]

        normalized_config, normalized_task = pipeline.validate(config, task)

        self.assertEqual(normalized_config["schema_version"], 1)
        self.assertEqual(normalized_task["schema_version"], 1)
        self.assertNotIn("schema_version", config)
        self.assertNotIn("schema_version", task)

    def test_required_fields_and_types(self) -> None:
        for key in ("worker_argv", "test_argv", "timeout_seconds"):
            with self.subTest(missing=key):
                config = valid_config()
                del config[key]
                with self.assertRaisesRegex(InputError, key):
                    pipeline.validate(config, valid_task())

        for key in ("specification", "allowed_files_to_modify"):
            with self.subTest(missing=key):
                task = valid_task()
                del task[key]
                with self.assertRaisesRegex(InputError, key):
                    pipeline.validate(valid_config(), task)

        invalid_configs = [
            {"worker_argv": [], "test_argv": ["x"], "timeout_seconds": 10},
            {"worker_argv": ["x"], "test_argv": [""], "timeout_seconds": 10},
            {"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": True},
            {"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": 1.0},
            {"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": 0},
            {"worker_argv": ["x"], "test_argv": ["x"], "timeout_seconds": 86401},
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(InputError):
                    pipeline.validate(config, valid_task())

        invalid_tasks = [
            {"specification": "  ", "allowed_files_to_modify": ["x.py"]},
            {"specification": "x", "allowed_files_to_modify": []},
            {"specification": 1, "allowed_files_to_modify": ["x.py"]},
            {"specification": "x", "allowed_files_to_modify": "x.py"},
        ]
        for task in invalid_tasks:
            with self.subTest(task=task):
                with self.assertRaises(InputError):
                    pipeline.validate(valid_config(), task)

    def test_timeout_boundaries_are_inclusive(self) -> None:
        for timeout in (1, 86400):
            with self.subTest(timeout=timeout):
                config = valid_config()
                config["timeout_seconds"] = timeout
                pipeline.validate(config, valid_task())

    def test_example_task_uses_current_schema(self) -> None:
        example = Path(__file__).resolve().parents[1] / "examples/task.json"
        task = json.loads(example.read_text(encoding="utf-8"))
        self.assertEqual(task["schema_version"], 1)
        pipeline.validate_task(task)


class PathValidationTests(unittest.TestCase):
    def test_spaces_and_unicode_are_valid(self) -> None:
        for name in ("src/with space/file.py", "docs/naïve/ファイル.md"):
            with self.subTest(path=name):
                task = valid_task()
                task["allowed_files_to_modify"] = [name]
                normalized = pipeline.validate(valid_config(), task)[1]
                self.assertEqual(normalized["allowed_files_to_modify"], [name])

    def test_unsafe_paths_are_rejected(self) -> None:
        invalid = [
            "../outside.py",
            "a/../b.py",
            "a/./b.py",
            "/absolute.py",
            "C:/drive.py",
            "C:relative.py",
            "//server/share.py",
            "a\\b.py",
            "a:b.py",
            "a/*.py",
            "a/?.py",
            "a/[x].py",
            "a//b.py",
            "a/",
            ".git/config",
            ".GIT/config",
            "src/.omp/state.json",
            "src/.OMP/state.json",
            "a\x00b.py",
        ]
        for name in invalid:
            with self.subTest(path=name):
                task = valid_task()
                task["allowed_files_to_modify"] = [name]
                with self.assertRaises(InputError):
                    pipeline.validate(valid_config(), task)


class ReadBoundaryTests(unittest.TestCase):
    def test_invalid_json_and_non_object_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            cases = (
                (b'{"worker_argv": [}', "Invalid JSON"),
                (b"\xff", "Cannot decode UTF-8 JSON"),
                (b"null", "must contain a JSON object"),
                (b"[]", "must contain a JSON object"),
                (b"1", "must contain a JSON object"),
            )
            for raw, message in cases:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaisesRegex(InputError, message):
                        pipeline.read_object(path, InputError)
                    with self.assertRaisesRegex(OperationError, message):
                        pipeline.read_object(path, OperationError)

    def test_parser_valueerror_and_recursionerror_are_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"schema_version": 1}', encoding="utf-8")

            with patch.object(
                pipeline.json, "loads", side_effect=ValueError("digit limit")
            ):
                with self.assertRaises(InputError) as input_error:
                    pipeline.read_object(path, InputError)
                with self.assertRaises(OperationError) as operation_error:
                    pipeline.read_object(path, OperationError)
            self.assertNotIn("digit limit", str(input_error.exception))
            self.assertNotIn("digit limit", str(operation_error.exception))

            with patch.object(
                pipeline.json, "loads", side_effect=RecursionError("too deep")
            ):
                with self.assertRaisesRegex(InputError, "nesting"):
                    pipeline.read_object(path, InputError)
                with self.assertRaisesRegex(OperationError, "nesting"):
                    pipeline.read_object(path, OperationError)

            if hasattr(sys, "get_int_max_str_digits"):
                path.write_bytes(
                    b'{"schema_version": ' + b"9" * 5000 + b"}"
                )
                with self.assertRaises(InputError) as caught:
                    pipeline.read_object(path, InputError)
                self.assertNotIn("9" * 100, str(caught.exception))
                with self.assertRaises(OperationError):
                    pipeline.read_object(path, OperationError)

            path.write_text("[" * 20000 + "]" * 20000, encoding="utf-8")
            with self.assertRaisesRegex(InputError, "nesting"):
                pipeline.read_object(path, InputError)
            with self.assertRaisesRegex(OperationError, "nesting"):
                pipeline.read_object(path, OperationError)


class StateValidationTests(unittest.TestCase):
    def test_state_requires_version_and_rejects_unknown_fields(self) -> None:
        state = valid_state()
        del state["schema_version"]
        with self.assertRaisesRegex(OperationError, "schema_version"):
            pipeline.validate_state(state)

        for version in (3, 0, -1, True, "1", None, 1.0):
            with self.subTest(version=version):
                state = valid_state()
                state["schema_version"] = version
                with self.assertRaises(OperationError):
                    pipeline.validate_state(state)

        state = valid_state()
        state["schema_version"] = 2
        normalized = pipeline.validate_state(state)
        self.assertEqual(normalized["schema_version"], 2)

        state = valid_state()
        state["extra"] = True
        with self.assertRaisesRegex(OperationError, "unknown"):
            pipeline.validate_state(state)

        state = valid_state()
        state["config"]["extra"] = True
        with self.assertRaisesRegex(OperationError, "unknown"):
            pipeline.validate_state(state)

        state = valid_state()
        state["task"]["extra"] = True
        with self.assertRaisesRegex(OperationError, "unknown"):
            pipeline.validate_state(state)

        state = valid_state()
        del state["config"]["schema_version"]
        with self.assertRaises(OperationError):
            pipeline.validate_state(state)

    def test_state_required_fields_and_top_level_type(self) -> None:
        for key in (
            "run_id", "phase", "base_commit", "config", "task",
        ):
            with self.subTest(missing=key):
                state = valid_state()
                del state[key]
                with self.assertRaisesRegex(OperationError, key):
                    pipeline.validate_state(state)

        for value in (None, [], "state", 1):
            with self.subTest(value=value):
                with self.assertRaises(OperationError):
                    pipeline.validate_state(value)  # type: ignore[arg-type]

    def test_state_phase_and_complete_result(self) -> None:
        for phase in (
            "preparing", "worker", "verifying", "committing", "complete",
        ):
            with self.subTest(phase=phase):
                state = valid_state()
                state["phase"] = phase
                if phase == "complete":
                    state["result_commit"] = BASE_COMMIT
                pipeline.validate_state(state)

        state = valid_state()
        state["phase"] = "unknown"
        with self.assertRaisesRegex(OperationError, "phase"):
            pipeline.validate_state(state)

        state = valid_state()
        state["phase"] = "complete"
        with self.assertRaisesRegex(OperationError, "result_commit"):
            pipeline.validate_state(state)

        state = valid_state()
        state["phase"] = "complete"
        state["result_commit"] = "not-a-commit"
        with self.assertRaises(OperationError):
            pipeline.validate_state(state)

    def test_commit_oid_accepts_supported_git_lengths_only(self) -> None:
        for commit in (BASE_COMMIT, SHA256_COMMIT):
            with self.subTest(commit=commit):
                state = valid_state()
                state["base_commit"] = commit
                state["phase"] = "complete"
                state["result_commit"] = commit
                normalized = pipeline.validate_state(state)
                self.assertEqual(normalized["base_commit"], commit)
                self.assertEqual(normalized["result_commit"], commit)

        for commit in (
            "a" * 39, "a" * 41, "a" * 63, "a" * 65,
            "g" * 40, "", "a" * 39 + "-",
        ):
            with self.subTest(commit=commit):
                state = valid_state()
                state["base_commit"] = commit
                with self.assertRaises(OperationError):
                    pipeline.validate_state(state)

    def test_repository_oid_length_is_detected_and_enforced(self) -> None:
        with patch.object(pipeline, "git", return_value="sha256"):
            self.assertEqual(pipeline.git_object_id_length(Path(".")), 64)
        with patch.object(pipeline, "git", return_value="sha1"):
            self.assertEqual(pipeline.git_object_id_length(Path(".")), 40)
        metadata_error = OperationError("metadata permission failure")
        with patch.object(pipeline, "git", side_effect=metadata_error):
            with self.assertRaises(OperationError) as caught:
                pipeline.git_object_id_length(Path("."))
        self.assertIs(caught.exception, metadata_error)

        sha1_state = valid_state()
        sha1_state["phase"] = "complete"
        sha1_state["result_commit"] = BASE_COMMIT
        pipeline.validate_state(sha1_state, oid_length=40)
        with self.assertRaises(OperationError):
            pipeline.validate_state(sha1_state, oid_length=64)

        sha256_state = valid_state()
        sha256_state["base_commit"] = SHA256_COMMIT
        sha256_state["phase"] = "complete"
        sha256_state["result_commit"] = SHA256_COMMIT
        pipeline.validate_state(sha256_state, oid_length=64)
        with self.assertRaises(OperationError):
            pipeline.validate_state(sha256_state, oid_length=40)

    def test_git_and_oid_helpers_wrap_permission_and_oserror_with_cause(self) -> None:
        root = Path(".")
        with patch.object(subprocess, "check_output", side_effect=PermissionError("denied")):
            with self.assertRaises(OperationError) as caught_git:
                pipeline.git(root, "rev-parse", "--show-object-format")
            self.assertIsInstance(caught_git.exception, OperationError)
            self.assertIsInstance(caught_git.exception.__cause__, PermissionError)
            self.assertIn("PermissionError", str(caught_git.exception))
            self.assertNotIn("--show-object-format", str(caught_git.exception))

            with self.assertRaises(OperationError) as caught_oid:
                pipeline.git_object_id_length(root)
            self.assertIsInstance(caught_oid.exception, OperationError)
            self.assertIsInstance(caught_oid.exception.__cause__, PermissionError)
            self.assertIn("PermissionError", str(caught_oid.exception))

        with patch.object(subprocess, "check_output", side_effect=OSError("io error")):
            with self.assertRaises(OperationError) as caught_git:
                pipeline.git(root, "status")
            self.assertIsInstance(caught_git.exception, OperationError)
            self.assertIsInstance(caught_git.exception.__cause__, OSError)
            self.assertIn("OSError", str(caught_git.exception))
            self.assertNotIn("status", str(caught_git.exception))

            with self.assertRaises(OperationError) as caught_oid:
                pipeline.git_object_id_length(root)
            self.assertIsInstance(caught_oid.exception, OperationError)
            self.assertIsInstance(caught_oid.exception.__cause__, OSError)
            self.assertIn("OSError", str(caught_oid.exception))

        cpe = subprocess.CalledProcessError(128, ["git"], stderr=b"fatal: repo error\n")
        with patch.object(subprocess, "check_output", side_effect=cpe):
            with self.assertRaises(OperationError) as caught_git:
                pipeline.git(root, "status")
            self.assertIsInstance(caught_git.exception.__cause__, subprocess.CalledProcessError)
            self.assertIn("fatal: repo error", str(caught_git.exception))

            with self.assertRaises(OperationError) as caught_oid:
                pipeline.git_object_id_length(root)
            self.assertIsInstance(caught_oid.exception.__cause__, subprocess.CalledProcessError)
            self.assertIn("fatal: repo error", str(caught_oid.exception))

        fnf = FileNotFoundError("git not found")
        with patch.object(subprocess, "check_output", side_effect=fnf):
            with self.assertRaises(OperationError) as caught_git:
                pipeline.git(root, "status")
            self.assertIsInstance(caught_git.exception.__cause__, FileNotFoundError)
            self.assertEqual(str(caught_git.exception), "Git executable was not found")

            with self.assertRaises(OperationError) as caught_oid:
                pipeline.git_object_id_length(root)
            self.assertIsInstance(caught_oid.exception.__cause__, FileNotFoundError)
            self.assertEqual(str(caught_oid.exception), "Git executable was not found")

        with patch.object(subprocess, "check_output", return_value=b"unsupported_format\n"):
            with self.assertRaises(OperationError) as caught_oid:
                pipeline.git_object_id_length(root)
            self.assertEqual(str(caught_oid.exception), "Unsupported Git object format")

    def test_actual_sha256_repository_format_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = subprocess.run(
                ["git", "init", "--object-format=sha256", str(root)],
                capture_output=True,
            )
            if probe.returncode != 0:
                with patch.object(pipeline, "git", return_value="sha256"):
                    self.assertEqual(pipeline.git_object_id_length(root), 64)
                return
            self.assertEqual(pipeline.git_object_id_length(root), 64)
            state = valid_state()
            state["base_commit"] = SHA256_COMMIT
            state["phase"] = "complete"
            state["result_commit"] = SHA256_COMMIT
            pipeline.validate_state(state, oid_length=64)
            with self.assertRaises(OperationError):
                pipeline.validate_state(valid_state(), oid_length=64)

    def test_run_id_is_a_bounded_identifier(self) -> None:
        for run_id in ("a", "A0_-", "a" * 64):
            with self.subTest(run_id=run_id):
                state = valid_state()
                state["run_id"] = run_id
                pipeline.validate_state(state)

        for run_id in (
            "", "-a", "_a", "a.b", "a/b", "../a", "a b", "a" * 65,
            "run\r\n", "run\u00e9",
        ):
            with self.subTest(run_id=run_id):
                state = valid_state()
                state["run_id"] = run_id
                with self.assertRaises(OperationError):
                    pipeline.validate_state(state)

    def test_optional_state_fields_are_typed(self) -> None:
        for child_pid in (None, 1, 999999):
            with self.subTest(child_pid=child_pid):
                state = valid_state()
                state["child_pid"] = child_pid
                pipeline.validate_state(state)

        for child_pid in (0, -1, True, 1.0, "1"):
            with self.subTest(child_pid=child_pid):
                state = valid_state()
                state["child_pid"] = child_pid
                with self.assertRaises(OperationError):
                    pipeline.validate_state(state)

        for last_error in ("failure", ""):
            with self.subTest(last_error=last_error):
                state = valid_state()
                state["last_error"] = last_error
                pipeline.validate_state(state)

        state = valid_state()
        state["last_error"] = 7
        with self.assertRaises(OperationError):
            pipeline.validate_state(state)

    def test_validation_returns_a_copy(self) -> None:
        state = valid_state()
        normalized = pipeline.validate_state(state)
        normalized["run_id"] = "changed"
        self.assertEqual(state["run_id"], "run-1")


class NoMutationTests(unittest.TestCase):
    def test_invalid_state_is_not_rewritten_and_has_no_runtime_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / ".omp/state.json"
            checkpoint.parent.mkdir()
            state = valid_state()
            state["phase"] = "unknown"
            raw = json.dumps(state).encode("utf-8")
            checkpoint.write_bytes(raw)

            with patch.object(pipeline, "save") as save_mock, \
                    patch.object(pipeline, "execute") as execute_mock, \
                    patch.object(pipeline, "git") as git_mock:
                with self.assertRaises(OperationError):
                    pipeline.advance(root)

            self.assertEqual(checkpoint.read_bytes(), raw)
            save_mock.assert_not_called()
            execute_mock.assert_not_called()
            git_mock.assert_not_called()

    def test_invalid_run_id_does_not_create_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / ".omp/state.json"
            checkpoint.parent.mkdir()
            state = valid_state()
            state["run_id"] = "../escape"
            checkpoint.write_text(json.dumps(state), encoding="utf-8")

            with self.assertRaises(OperationError):
                pipeline.advance(root)

            self.assertFalse((root / ".omp/runs").exists())

    def test_wrong_repository_oid_format_is_rejected_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init"], cwd=root, check=True,
                           capture_output=True)
            checkpoint = root / ".omp/state.json"
            checkpoint.parent.mkdir()
            state = valid_state()
            state["base_commit"] = SHA256_COMMIT
            raw = json.dumps(state).encode("utf-8")
            checkpoint.write_bytes(raw)

            with patch.object(pipeline, "save") as save_mock, \
                    patch.object(pipeline, "execute") as execute_mock:
                with self.assertRaises(OperationError):
                    pipeline.advance(root)

            self.assertEqual(checkpoint.read_bytes(), raw)
            save_mock.assert_not_called()
            execute_mock.assert_not_called()
            self.assertFalse((root / ".omp/runs").exists())

    def test_invalid_state_variants_have_no_runtime_side_effect(self) -> None:
        cases = (
            ("phase", lambda state: state.__setitem__("phase", "unknown")),
            ("run-id", lambda state: state.__setitem__("run_id", "../escape")),
            ("base-commit", lambda state: state.__setitem__(
                "base_commit", "not-an-oid"
            )),
            ("task-path", lambda state: state["task"].__setitem__(
                "allowed_files_to_modify", ["../outside.py"]
            )),
            ("nested-unknown", lambda state: state["config"].__setitem__(
                "extra", 1
            )),
            ("missing-version", lambda state: state.pop("schema_version")),
        )
        for label, mutate in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                checkpoint = root / ".omp/state.json"
                checkpoint.parent.mkdir()
                state = valid_state()
                mutate(state)
                raw = json.dumps(state).encode("utf-8")
                checkpoint.write_bytes(raw)

                with patch.object(pipeline, "save") as save_mock, \
                        patch.object(pipeline, "execute") as execute_mock, \
                        patch.object(pipeline, "git") as git_mock:
                    with self.assertRaises(OperationError):
                        pipeline.advance(root)

                self.assertEqual(checkpoint.read_bytes(), raw)
                save_mock.assert_not_called()
                execute_mock.assert_not_called()
                git_mock.assert_not_called()

    def test_metadata_failure_blocks_run_before_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "repo"
            root.mkdir()
            subprocess.run(["git", "init"], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@localhost"],
                           cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=root, check=True, capture_output=True)
            (root / ".gitignore").write_text(".omp/\n", encoding="utf-8")
            (root / "app.py").write_text("value = 0\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=root,
                           check=True, capture_output=True)

            omp = root / ".omp"
            omp.mkdir()
            (omp / "config.json").write_text(
                json.dumps(valid_config()), encoding="utf-8"
            )
            task_path = parent / "task.json"
            task_path.write_text(json.dumps(valid_task()), encoding="utf-8")

            real_git = pipeline.git

            def metadata_failure(root_path: Path, *args: str) -> str:
                if args == ("rev-parse", "--show-object-format"):
                    raise OperationError("metadata permission failure")
                return real_git(root_path, *args)

            with patch.object(pipeline, "git", side_effect=metadata_failure), \
                    patch.object(pipeline, "advance") as advance_mock, \
                    patch.object(sys, "argv", [
                        "omp", "--root", str(root), "run", str(task_path),
                    ]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    pipeline.main()

            self.assertEqual(raised.exception.code, 1)
            advance_mock.assert_not_called()
            self.assertFalse((omp / "lock").exists())
            self.assertFalse((omp / "state.json").exists())

            state_path = omp / "state.json"
            raw = json.dumps(valid_state()).encode("utf-8")
            state_path.write_bytes(raw)
            for command in ("resume", "status"):
                with self.subTest(command=command):
                    with patch.object(
                        pipeline, "git", side_effect=metadata_failure
                    ), patch.object(pipeline, "advance") as blocked_advance, \
                            patch.object(sys, "argv", [
                                "omp", "--root", str(root), command,
                            ]), redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as blocked:
                            pipeline.main()
                    self.assertEqual(blocked.exception.code, 1)
                    blocked_advance.assert_not_called()
                    self.assertEqual(state_path.read_bytes(), raw)
                    self.assertFalse((omp / "lock").exists())

    def test_git_oserror_and_permission_error_block_cli_before_lock_and_preserve_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "repo"
            root.mkdir()
            subprocess.run(["git", "init"], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@localhost"],
                           cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=root, check=True, capture_output=True)
            (root / ".gitignore").write_text(".omp/\n", encoding="utf-8")
            (root / "app.py").write_text("value = 0\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=root,
                           check=True, capture_output=True)

            omp = root / ".omp"
            omp.mkdir()
            (omp / "config.json").write_text(
                json.dumps(valid_config()), encoding="utf-8"
            )
            task_path = parent / "task.json"
            task_path.write_text(json.dumps(valid_task()), encoding="utf-8")

            real_check_output = subprocess.check_output

            for injected in (
                PermissionError("git execution permission denied"),
                OSError("git execution os failure"),
            ):
                with self.subTest(error_type=type(injected).__name__):
                    state_path = omp / "state.json"
                    if state_path.exists():
                        state_path.unlink()

                    def check_output_mock(cmd: list[str], *args: object, **kwargs: object) -> bytes:
                        if "--show-object-format" in cmd:
                            raise injected
                        return real_check_output(cmd, *args, **kwargs)

                    with patch.object(subprocess, "check_output", side_effect=check_output_mock), \
                            patch.object(pipeline, "advance") as advance_mock, \
                            patch.object(pipeline, "save") as save_mock, \
                            patch.object(pipeline, "execute") as execute_mock, \
                            patch.object(sys, "argv", [
                                "omp", "--root", str(root), "run", str(task_path),
                            ]), redirect_stderr(io.StringIO()) as stderr:
                        with self.assertRaises(SystemExit) as raised:
                            pipeline.main()

                    self.assertEqual(raised.exception.code, 1)
                    self.assertNotIn("Traceback", stderr.getvalue())
                    self.assertIn(type(injected).__name__, stderr.getvalue())
                    self.assertNotIn(str(task_path), stderr.getvalue())
                    advance_mock.assert_not_called()
                    save_mock.assert_not_called()
                    execute_mock.assert_not_called()
                    self.assertFalse((omp / "lock").exists())
                    self.assertFalse((omp / "state.json").exists())

                    state_path = omp / "state.json"
                    raw = json.dumps(valid_state()).encode("utf-8")
                    state_path.write_bytes(raw)
                    for command in ("resume", "status"):
                        with self.subTest(command=command, error_type=type(injected).__name__):
                            with patch.object(
                                subprocess, "check_output", side_effect=check_output_mock
                            ), patch.object(pipeline, "advance") as blocked_advance, \
                                    patch.object(pipeline, "save") as blocked_save, \
                                    patch.object(pipeline, "execute") as blocked_execute, \
                                    patch.object(sys, "argv", [
                                        "omp", "--root", str(root), command,
                                    ]), redirect_stderr(io.StringIO()) as blocked_stderr:
                                with self.assertRaises(SystemExit) as blocked:
                                    pipeline.main()
                            self.assertEqual(blocked.exception.code, 1)
                            self.assertNotIn("Traceback", blocked_stderr.getvalue())
                            self.assertIn(type(injected).__name__, blocked_stderr.getvalue())
                            blocked_advance.assert_not_called()
                            blocked_save.assert_not_called()
                            blocked_execute.assert_not_called()
                            self.assertEqual(state_path.read_bytes(), raw)
                            self.assertFalse((omp / "lock").exists())
                    state_path.unlink()

    def test_config_change_between_preflight_and_lock_is_rejected(self) -> None:
        self._exercise_config_change_fixture()

    def _exercise_config_change_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "repo"
            root.mkdir()
            subprocess.run(["git", "init"], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@localhost"],
                           cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=root, check=True, capture_output=True)
            (root / ".gitignore").write_text(".omp/\n", encoding="utf-8")
            (root / "app.py").write_text("value = 0\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=root,
                           check=True, capture_output=True)

            omp = root / ".omp"
            omp.mkdir()
            config_path = omp / "config.json"
            config_path.write_text(json.dumps(valid_config()), encoding="utf-8")
            task_path = parent / "task.json"
            task_path.write_text(json.dumps(valid_task()), encoding="utf-8")
            self.assertEqual(task_path.parent, parent)

            invalid_config = valid_config()
            invalid_config["schema_version"] = 2

            @contextmanager
            def change_config_before_lock(path: Path):
                config_path.write_text(
                    json.dumps(invalid_config), encoding="utf-8"
                )
                yield

            with patch.object(pipeline, "lock", change_config_before_lock), \
                    patch.object(pipeline, "advance") as advance_mock, \
                    patch.object(sys, "argv", [
                        "omp", "--root", str(root), "run", str(task_path),
                    ]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    pipeline.main()

            self.assertEqual(raised.exception.code, 1)
            advance_mock.assert_not_called()
            self.assertFalse((root / ".omp/state.json").exists())
            self.assertEqual(
                config_path.read_text(encoding="utf-8"),
                json.dumps(invalid_config),
            )

    def test_config_change_fixture_is_isolated_from_parent_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory)
            sentinel = outer / "task.json"
            sentinel.write_text("sentinel", encoding="utf-8")

            @contextmanager
            def controlled_temp():
                fixture = outer / "fixture"
                fixture.mkdir()
                yield str(fixture)

            with patch.object(
                tempfile, "TemporaryDirectory", side_effect=controlled_temp
            ):
                self._exercise_config_change_fixture()

            self.assertEqual(
                sentinel.read_text(encoding="utf-8"), "sentinel"
            )


if __name__ == "__main__":
    unittest.main()
