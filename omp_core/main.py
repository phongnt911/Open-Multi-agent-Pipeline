"""Single-task MVP. The checkout isolates Git changes, not OS permissions."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator, TypeAlias, TypedDict, cast
import uuid


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class Config(TypedDict):
    """Worker and verification settings stored in .omp/config.json."""

    worker_argv: list[str]
    test_argv: list[str]
    timeout_seconds: int


class Task(TypedDict):
    """Task specification and exact files the Worker may change."""

    specification: str
    allowed_files_to_modify: list[str]


class State(TypedDict, total=False):
    """Checkpoint fields used by the MVP.

    Version and nested schema validation belong to DAT-01; this annotation only
    describes the JSON object written by this module.
    """

    schema_version: int
    run_id: str
    phase: str
    base_commit: str
    config: Config
    task: Task
    child_pid: int | None
    last_error: str
    result_commit: str


class OmpError(ValueError):
    """Expected OMP failure reported without a traceback.

    ``ValueError`` remains the base type for compatibility with the original
    MVP API. Use ``InputError`` or ``OperationError`` to distinguish invalid
    input from a failed operation.
    """


class InputError(OmpError):
    """Invalid CLI, config, or task input detected before side effects."""


class OperationError(OmpError):
    """A validated Git, filesystem, lock, or child-process operation failed."""


DEFAULT_TEST_SCRIPT = (
    "import sys, unittest\n"
    "suite = unittest.defaultTestLoader.discover('tests')\n"
    "count = suite.countTestCases()\n"
    "print(f'Discovered {count} test(s)')\n"
    "if count == 0:\n"
    "    print('FAIL: no tests discovered', file=sys.stderr)\n"
    "    sys.exit(1)\n"
    "sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())"
)


def read(path: Path, error: type[OmpError] = InputError) -> JsonValue:
    """Read UTF-8 JSON from *path*.

    Raises:
        OmpError: the requested subclass when the file cannot be read, is not
            valid UTF-8, or does not contain valid JSON. The original error is
            preserved as ``__cause__``.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error(f"Cannot read JSON from {path}: {exc}") from exc


def read_object(path: Path, error: type[OmpError] = InputError) -> JsonObject:
    """Read a JSON object and reject other top-level types.

    This is not full schema validation; DAT-01 owns version and field checks.
    It prevents the MVP from failing later with ``AttributeError`` on a list or
    scalar document.

    Raises:
        OmpError: the requested subclass for unreadable, invalid, or non-object
            JSON.
    """
    value = read(path, error)
    if not isinstance(value, dict):
        raise error(f"{path} must contain a JSON object")
    return cast(JsonObject, value)


def save(path: Path, value: JsonValue) -> None:
    """Replace a checkpoint only after its complete contents reach disk.

    Side effects:
        Creates *path*'s parent directory, writes a sibling temporary file,
        flushes and fsyncs it, then replaces *path* atomically.

    Raises:
        OSError: directory creation, write, fsync, or replace failed. A
            leftover temporary file is removed when possible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def lock(path: Path) -> Iterator[None]:
    """Hold an exclusive non-blocking project lock for the context lifetime.

    Side effects:
        Creates *path* and its parent directory and keeps the file open until
        the context exits.

    Raises:
        OperationError: another process holds the lock or the OS lock call
            fails. The original ``OSError`` is chained.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OperationError(
                "Another OMP operation holds the project lock"
            ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def git(root: Path, *args: str) -> str:
    """Run Git in *root* and return stripped UTF-8 stdout.

    Raises:
        OperationError: Git is unavailable or exits nonzero. Git's stderr is
            used as the message and the original error is chained.
    """
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(detail or "Git command failed") from exc
    except FileNotFoundError as exc:
        raise OperationError("Git executable was not found") from exc
    except UnicodeDecodeError as exc:
        raise OperationError("Git output is not valid UTF-8") from exc


def validate(config: JsonObject, task: JsonObject) -> None:
    """Validate MVP config and task fields before any side effect.

    This covers only the fields consumed by the MVP. It does not validate
    checkpoints or the full schema; DAT-01 owns that work.

    Raises:
        InputError: a required field is missing or has an unsafe type/value.
    """
    for key in ("worker_argv", "test_argv"):
        value = config.get(key)
        if not isinstance(value, list) or not value or not all(
            isinstance(arg, str) and arg for arg in value
        ):
            raise InputError(f"{key} must be a nonempty argument array")
        if any("\x00" in arg for arg in value):
            raise InputError(f"{key} must not contain NUL bytes")
    timeout = config.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise InputError("timeout_seconds must be a positive integer")
    specification = task.get("specification")
    if not isinstance(specification, str) or not specification.strip():
        raise InputError("Task needs specification")
    paths = task.get("allowed_files_to_modify")
    if not isinstance(paths, list) or not paths:
        raise InputError("Task needs exact allowed_files_to_modify paths")
    for name in paths:
        if (not isinstance(name, str) or not name or "\\" in name
                or ":" in name or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or name.split("/")[0].lower() in (".git", ".omp")):
            raise InputError(f"Unsafe allowed path: {name!r}")


def _timeout_error(
    label: str, exc: subprocess.TimeoutExpired
) -> subprocess.TimeoutExpired:
    """Return a timeout that exposes only *label* and the timeout value.

    The original ``TimeoutExpired`` command field can contain worker argv and
    provider tokens. Callers may chain this sanitized error instead.
    """
    return subprocess.TimeoutExpired(label, exc.timeout)


def stop(process: subprocess.Popen[bytes], label: str = "child") -> None:
    """Stop the child process tree and wait for the child to exit.

    Side effects:
        Sends SIGKILL to the process group on POSIX or runs ``taskkill /T /F``
        on Windows. Waits at most 10 seconds.

    Raises:
        subprocess.TimeoutExpired: the child did not exit within 10 seconds.
            The exception contains *label* and the timeout, not the original
            argv.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    cleanup_timeout: subprocess.TimeoutExpired | None = None
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        cleanup_timeout = _timeout_error(f"{label} cleanup", exc)
    if cleanup_timeout is not None:
        raise cleanup_timeout


def _public_operation_error(label: str, exc: BaseException) -> BaseException:
    """Return an argv-free public form of an operation error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(label, exc)
    return exc


def _public_cleanup_error(label: str, exc: BaseException) -> BaseException:
    """Return an argv-free public form of a cleanup error."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return _timeout_error(f"{label} cleanup", exc)
    return OperationError(f"{label} cleanup failed: {type(exc).__name__}")


def execute(
    argv: list[str],
    checkout: Path,
    run_dir: Path,
    state: State,
    checkpoint: Path,
    label: str,
    prompt: str | None = None,
) -> None:
    """Run one Worker or test command inside *checkout*.

    Side effects:
        Appends stdout/stderr to ``<label>.log`` under *run_dir*, records
        ``child_pid`` in *state*/checkpoint before sending input, and clears it
        after the process exits. On any exception the child tree is stopped.

    Raises:
        OperationError: the command cannot be started or exits nonzero. The
            log path or failure class is included without echoing argv.
        subprocess.TimeoutExpired: the command or its cleanup exceeds the
            configured timeout. The exception is sanitized to a label.
        KeyboardInterrupt/SystemExit: propagated after stopping the child.
    """
    with (run_dir / f"{label}.log").open("ab") as log:
        options: dict[str, object] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=checkout,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                **options,
            )
        except OSError as exc:
            raise OperationError(
                f"Cannot start {label}: {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            raise OperationError(
                f"Cannot start {label}: invalid argument"
            ) from exc
        state["child_pid"] = process.pid
        operation_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            try:
                save(checkpoint, state)
                process.communicate(
                    (prompt or "").encode("utf-8"),
                    timeout=state["config"]["timeout_seconds"],
                )
                if process.returncode:
                    raise OperationError(
                        f"{label} exited {process.returncode}; "
                        f"see {run_dir / (label + '.log')}"
                    )
            except BaseException as exc:
                operation_error = exc
            if operation_error is not None:
                try:
                    stop(process, label)
                except BaseException as exc:
                    cleanup_error = exc
        finally:
            if process.poll() is not None:
                state["child_pid"] = None
            save(checkpoint, state)
        if operation_error is not None:
            public_operation = _public_operation_error(label, operation_error)
            if cleanup_error is not None:
                raise _public_cleanup_error(
                    label, cleanup_error
                ) from public_operation
            raise public_operation


def check_scope(checkout: Path, state: State) -> None:
    """Verify HEAD, index, and changed paths still match the task scope.

    Side effects:
        None; the checkout is left untouched for inspection on failure.

    Raises:
        OperationError: HEAD changed, files were staged, paths fall outside
            ``allowed_files_to_modify``, or a path is a symlink/escapes the
            checkout.
    """
    if git(checkout, "rev-parse", "HEAD") != state["base_commit"]:
        raise OperationError("Worker changed Git HEAD")
    if git(checkout, "diff", "--cached", "--name-only"):
        raise OperationError("Worker staged files; review checkout before resuming")
    try:
        changed = subprocess.check_output([
            "git", "-C", str(checkout), "diff", "--name-only", "-z",
            state["base_commit"]
        ], stderr=subprocess.PIPE).decode("utf-8").split("\0")
        untracked = subprocess.check_output([
            "git", "-C", str(checkout), "ls-files", "--others",
            "--exclude-standard", "-z"
        ], stderr=subprocess.PIPE).decode("utf-8").split("\0")
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise OperationError(detail or "Cannot inspect checkout changes") from exc
    outside = (set(changed + untracked) - {""}) - set(
        state["task"]["allowed_files_to_modify"]
    )
    if outside:
        raise OperationError(f"Changes outside task scope: {sorted(outside)}")
    for name in state["task"]["allowed_files_to_modify"]:
        path = checkout / name
        if path.is_symlink() or not path.resolve().is_relative_to(checkout.resolve()):
            raise OperationError(f"Path escapes checkout: {name}")


def advance(root: Path) -> None:
    """Advance the checkpoint for *root* by one or more phases.

    Side effects:
        Clones the local repository into ``.omp/runs/<run_id>/checkout``, runs
        the configured Worker/test commands, writes logs, and creates a commit
        inside that checkout. It never changes the source repository or pushes.

    Raises:
        OperationError: the checkpoint, Git state, child process, test result,
            or task scope is invalid.
        subprocess.TimeoutExpired: a child command exceeds its timeout.
        KeyboardInterrupt/SystemExit: propagated after the child is stopped and
            the checkpoint is updated.
    """
    checkpoint = root / ".omp/state.json"
    state: State = cast(State, read_object(checkpoint, OperationError))
    run_dir = root / ".omp/runs" / state["run_id"]
    checkout = run_dir / "checkout"
    if state.get("child_pid"):
        raise OperationError(
            f"Previous child PID {state['child_pid']} may still be running. "
            "Confirm it and its descendants have stopped, then clear child_pid "
            "in state.json."
        )
    if state["phase"] == "complete":
        print(f"Completed {state['result_commit']} in {checkout}")
        return
    try:
        if state["phase"] == "preparing":
            if checkout.exists():
                raise OperationError(
                    "Interrupted clone: retain it for inspection; start a new run"
                )
            run_dir.mkdir(parents=True, exist_ok=True)
            git(root, "clone", "--local", "--no-hardlinks", "--no-checkout",
                root.as_posix(), checkout.as_posix())
            git(checkout, "checkout", "--detach", state["base_commit"])
            git(checkout, "remote", "remove", "origin")
            state["phase"] = "worker"
            save(checkpoint, state)
        if state["phase"] == "worker":
            prompt = (state["task"]["specification"] + "\nOnly modify these files: "
                      + ", ".join(state["task"]["allowed_files_to_modify"])
                      + "\nDo not commit, stage files, install packages, change tests, or run migrations."
                      + "\nThis may be a resumed task. Inspect existing changes first.")
            execute(state["config"]["worker_argv"], checkout, run_dir, state,
                    checkpoint, "worker", prompt)
            state["phase"] = "verifying"
            save(checkpoint, state)
        if state["phase"] == "verifying":
            check_scope(checkout, state)
            state["phase"] = "committing"
            save(checkpoint, state)
        if state["phase"] == "committing":
            # Revalidate after restart before trusting the prior verification.
            head = git(checkout, "rev-parse", "HEAD")
            message = f"OMP task {state['run_id']}"
            if head == state["base_commit"]:
                git(checkout, "reset")
                check_scope(checkout, state)
                execute(state["config"]["test_argv"], checkout, run_dir, state,
                        checkpoint, "tests")
                check_scope(checkout, state)
                git(checkout, "add", "--", *state["task"]["allowed_files_to_modify"])
                if git(checkout, "diff", "--cached", "--name-only"):
                    git(checkout, "-c", "user.name=OMP", "-c",
                        "user.email=omp@localhost", "commit", "-m", message)
            elif (git(checkout, "log", "-1", "--format=%s") != message
                  or git(checkout, "rev-parse", "HEAD^") != state["base_commit"]):
                raise OperationError("Unexpected commit during recovery")
            if git(checkout, "status", "--porcelain"):
                raise OperationError(
                    "Checkout is dirty after commit; inspect before resuming"
                )
            state["result_commit"] = git(checkout, "rev-parse", "HEAD")
            state["phase"] = "complete"
            state.pop("last_error", None)
            save(checkpoint, state)
            print(f"Completed {state['result_commit']} in {checkout}")
    except BaseException as exc:
        state["last_error"] = str(exc) or type(exc).__name__
        save(checkpoint, state)
        raise


def main() -> None:
    """Run the ``omp`` command line interface.

    Expected input and operation failures print ``OMP: <message>`` to stderr
    and exit 1 without a traceback. Successful commands return normally.

    Side effects:
        Dispatches to state, lock, Git, process, and filesystem operations
        under ``--root``.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    run = sub.add_parser("run")
    run.add_argument("task", type=Path)
    sub.add_parser("resume")
    sub.add_parser("status")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == "status":
            state = read_object(root / ".omp/state.json", OperationError)
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return
        with lock(root / ".omp/lock"):
            if args.command == "init":
                path = root / ".omp/config.json"
                if path.exists():
                    raise InputError("Configuration already exists")
                if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
                    raise InputError("--root must be the Git repository root")
                save(path, {
                    "worker_argv": ["codex", "exec", "--ephemeral", "--sandbox",
                                    "workspace-write", "-"],
                    "test_argv": [sys.executable, "-B", "-c", DEFAULT_TEST_SCRIPT],
                    "timeout_seconds": 600,
                })
                print(f"Created {path}; review worker and test commands before running")
                return
            if args.command == "run":
                if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
                    raise InputError("--root must be the Git repository root")
                checkpoint = root / ".omp/state.json"
                if (checkpoint.exists()
                        and read_object(checkpoint, OperationError)["phase"] != "complete"):
                    raise OperationError("Unfinished run exists; use resume")
                config = read_object(root / ".omp/config.json")
                task = read_object(args.task)
                validate(config, task)
                if git(root, "status", "--porcelain"):
                    raise OperationError("Commit or stash project changes before running")
                state: State = {
                    "schema_version": 1, "run_id": uuid.uuid4().hex,
                    "phase": "preparing", "base_commit": git(root, "rev-parse", "HEAD"),
                    "config": cast(Config, config), "task": cast(Task, task),
                    "child_pid": None,
                }
                save(checkpoint, state)
            advance(root)
    except (OmpError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"OMP: {exc}\n")


if __name__ == "__main__":
    main()
