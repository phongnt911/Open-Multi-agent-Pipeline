"""Single-task MVP. The checkout isolates Git changes, not OS permissions."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    """Replace a checkpoint only after its complete contents reach disk."""
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
def lock(path):
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
            raise ValueError("Another OMP operation holds the project lock") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def git(root, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.decode("utf-8", errors="replace").strip()) from exc


def validate(config, task):
    for key in ("worker_argv", "test_argv"):
        value = config.get(key)
        if not isinstance(value, list) or not value or not all(
            isinstance(arg, str) and arg for arg in value
        ):
            raise ValueError(f"{key} must be a nonempty argument array")
    timeout = config.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    if not isinstance(task.get("specification"), str) or not task["specification"].strip():
        raise ValueError("Task needs specification")
    paths = task.get("allowed_files_to_modify")
    if not isinstance(paths, list) or not paths:
        raise ValueError("Task needs exact allowed_files_to_modify paths")
    for name in paths:
        if (not isinstance(name, str) or not name or "\\" in name
                or ":" in name or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
                or name.split("/")[0].lower() in (".git", ".omp")):
            raise ValueError(f"Unsafe allowed path: {name!r}")


def stop(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def execute(argv, checkout, run_dir, state, checkpoint, label, prompt=None):
    with (run_dir / f"{label}.log").open("ab") as log:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, cwd=checkout, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, **options)
        state["child_pid"] = process.pid
        try:
            save(checkpoint, state)
            process.communicate((prompt or "").encode("utf-8"), timeout=state["config"]["timeout_seconds"])
            if process.returncode:
                raise ValueError(f"{label} exited {process.returncode}; see {run_dir / (label + '.log')}")
        except BaseException:
            stop(process)
            raise
        finally:
            if process.poll() is not None:
                state["child_pid"] = None
            save(checkpoint, state)


def check_scope(checkout, state):
    if git(checkout, "rev-parse", "HEAD") != state["base_commit"]:
        raise ValueError("Worker changed Git HEAD")
    if git(checkout, "diff", "--cached", "--name-only"):
        raise ValueError("Worker staged files; review checkout before resuming")
    changed = subprocess.check_output([
        "git", "-C", str(checkout), "diff", "--name-only", "-z", state["base_commit"]
    ]).decode("utf-8").split("\0")
    untracked = subprocess.check_output([
        "git", "-C", str(checkout), "ls-files", "--others", "--exclude-standard", "-z"
    ]).decode("utf-8").split("\0")
    outside = (set(changed + untracked) - {""}) - set(state["task"]["allowed_files_to_modify"])
    if outside:
        raise ValueError(f"Changes outside task scope: {sorted(outside)}")
    for name in state["task"]["allowed_files_to_modify"]:
        path = checkout / name
        if path.is_symlink() or not path.resolve().is_relative_to(checkout.resolve()):
            raise ValueError(f"Path escapes checkout: {name}")


def advance(root):
    checkpoint = root / ".omp/state.json"
    state = read(checkpoint)
    run_dir = root / ".omp/runs" / state["run_id"]
    checkout = run_dir / "checkout"
    if state.get("child_pid"):
        raise ValueError(f"Previous child PID {state['child_pid']} may still be running. Confirm it and its descendants have stopped, then clear child_pid in state.json.")
    if state["phase"] == "complete":
        print(f"Completed {state['result_commit']} in {checkout}")
        return
    try:
        if state["phase"] == "preparing":
            if checkout.exists():
                raise ValueError("Interrupted clone: retain it for inspection; start a new run")
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
            execute(state["config"]["worker_argv"], checkout, run_dir, state, checkpoint, "worker", prompt)
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
                execute(state["config"]["test_argv"], checkout, run_dir, state, checkpoint, "tests")
                check_scope(checkout, state)
                git(checkout, "add", "--", *state["task"]["allowed_files_to_modify"])
                if git(checkout, "diff", "--cached", "--name-only"):
                    git(checkout, "-c", "user.name=OMP", "-c", "user.email=omp@localhost", "commit", "-m", message)
            elif (git(checkout, "log", "-1", "--format=%s") != message
                  or git(checkout, "rev-parse", "HEAD^") != state["base_commit"]):
                raise ValueError("Unexpected commit during recovery")
            if git(checkout, "status", "--porcelain"):
                raise ValueError("Checkout is dirty after commit; inspect before resuming")
            state["result_commit"] = git(checkout, "rev-parse", "HEAD")
            state["phase"] = "complete"
            state.pop("last_error", None)
            save(checkpoint, state)
            print(f"Completed {state['result_commit']} in {checkout}")
    except BaseException as exc:
        state["last_error"] = str(exc) or type(exc).__name__
        save(checkpoint, state)
        raise


def main():
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
            print(json.dumps(read(root / ".omp/state.json"), ensure_ascii=False, indent=2))
            return
        with lock(root / ".omp/lock"):
            if args.command == "init":
                path = root / ".omp/config.json"
                if path.exists():
                    raise ValueError("Configuration already exists")
                if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
                    raise ValueError("--root must be the Git repository root")
                save(path, {"worker_argv": ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-"],
                            "test_argv": [sys.executable, "-B", "-c",
                                          "import sys, unittest; "
                                          "suite = unittest.defaultTestLoader.discover('tests'); "
                                          "assert suite.countTestCases(), 'No tests discovered'; "
                                          "sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())"],
                            "timeout_seconds": 600})
                print(f"Created {path}; review worker and test commands before running")
                return
            if args.command == "run":
                if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
                    raise ValueError("--root must be the Git repository root")
                checkpoint = root / ".omp/state.json"
                if checkpoint.exists() and read(checkpoint)["phase"] != "complete":
                    raise ValueError("Unfinished run exists; use resume")
                config, task = read(root / ".omp/config.json"), read(args.task)
                validate(config, task)
                if git(root, "status", "--porcelain"):
                    raise ValueError("Commit or stash project changes before running")
                state = {"schema_version": 1, "run_id": uuid.uuid4().hex,
                         "phase": "preparing", "base_commit": git(root, "rev-parse", "HEAD"),
                         "config": config, "task": task, "child_pid": None}
                save(checkpoint, state)
            advance(root)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"OMP: {exc}\n")


if __name__ == "__main__":
    main()
