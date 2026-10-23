"""Real multi-process OS lock checks for PROC-01."""
import ctypes
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from omp_core.main import save, validate, validate_state


ROOT = Path(__file__).resolve().parents[1]
STILL_ACTIVE = 259


def process_alive(pid: int) -> bool:
    """Return whether *pid* is alive without treating reuse as ownership."""
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _TimeoutProcess:
    """Fake Popen whose two bounded communicate calls both time out."""

    def __init__(self) -> None:
        self.pid = 4242
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.communicate_calls: list[float | None] = []
        self.kill_calls = 0

    def poll(self) -> int | None:
        return None

    def kill(self) -> None:
        self.kill_calls += 1

    def communicate(
        self, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self.communicate_calls.append(timeout)
        raise subprocess.TimeoutExpired(["fake"], timeout)


class ProcessLockTests(unittest.TestCase):
    """Exercise the existing OS lock with real OMP processes."""

    def setUp(self) -> None:
        scratch = ROOT / ".omp/test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "repo"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "test@localhost")
        self.git("config", "user.name", "Test")
        self.git("config", "core.autocrlf", "false")
        (self.root / ".gitignore").write_text(
            ".omp/\n__pycache__/\n", encoding="utf-8", newline="\n"
        )
        (self.root / "app.py").write_text(
            "value = 0\n", encoding="utf-8", newline="\n"
        )
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.worker_pid: int | None = None
        self.ready = self.parent / "worker-ready.txt"
        self.release = self.parent / "worker-release.txt"
        self.starts = self.parent / "worker-starts.log"
        self.exited = self.parent / "worker-exited.txt"
        self.task_path = self.parent / "task.json"
        self.script = self.parent / "fake_worker.py"
        self._write_worker_script()
        self.addCleanup(self._cleanup_worker)

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.PIPE
        ).decode("utf-8").strip()

    def _write_worker_script(self) -> None:
        self.script.write_text(
            "from pathlib import Path\n"
            "import os\n"
            "import sys\n"
            "import time\n"
            "\n"
            "ready = Path(sys.argv[1])\n"
            "release = Path(sys.argv[2])\n"
            "starts = Path(sys.argv[3])\n"
            "exited = Path(sys.argv[4])\n"
            "with starts.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(f'{os.getpid()}\\n')\n"
            "ready.write_text(str(os.getpid()), encoding='utf-8')\n"
            "deadline = time.monotonic() + 30\n"
            "try:\n"
            "    while time.monotonic() < deadline:\n"
            "        if release.exists():\n"
            "            break\n"
            "        time.sleep(0.02)\n"
            "finally:\n"
            "    exited.write_text(str(os.getpid()), encoding='utf-8')\n",
            encoding="utf-8",
            newline="\n",
        )

    def prepare(self) -> None:
        config = {
            "schema_version": 1,
            "worker_argv": [
                sys.executable,
                str(self.script),
                str(self.ready),
                str(self.release),
                str(self.starts),
                str(self.exited),
            ],
            "test_argv": [
                sys.executable,
                "-B",
                "-c",
                "import app; assert app.value == 0",
            ],
            "timeout_seconds": 30,
        }
        task = {
            "schema_version": 1,
            "specification": "Wait for release without changing files",
            "allowed_files_to_modify": ["app.py"],
        }
        validate(config, task)
        omp = self.root / ".omp"
        omp.mkdir(parents=True, exist_ok=True)
        (omp / "config.json").write_text(
            json.dumps(config, indent=2), encoding="utf-8", newline="\n"
        )
        self.task_path.write_text(
            json.dumps(task, indent=2), encoding="utf-8", newline="\n"
        )

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(ROOT) if not current else str(ROOT) + os.pathsep + current
        )
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env.pop("PYTHONOPTIMIZE", None)
        for key in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            env.pop(key, None)
        return env

    def omp_command(self, *args: str) -> list[str]:
        return [
            sys.executable,
            "-m",
            "omp_core.main",
            "--root",
            str(self.root),
            *args,
        ]

    def run_omp(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.omp_command(*args),
            cwd=self.root,
            env=self.env(),
            text=True,
            capture_output=True,
            timeout=60,
        )

    def start_run(self) -> subprocess.Popen:
        process = subprocess.Popen(
            self.omp_command("run", str(self.task_path)),
            cwd=self.root,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self._terminate, process)
        return process

    TERMINATE_TIMEOUT = 10

    def _terminate(self, process: subprocess.Popen) -> None:
        """Clean up a child with bounded waits and best-effort pipe closure."""
        if process.poll() is None:
            process.kill()
        try:
            try:
                process.communicate(timeout=self.TERMINATE_TIMEOUT)
                return
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=self.TERMINATE_TIMEOUT)
                except subprocess.TimeoutExpired as exc:
                    raise AssertionError(
                        f"cleanup failed for PID {process.pid}: "
                        "process did not exit; communicate timed out twice "
                        f"({self.TERMINATE_TIMEOUT}s each)"
                    ) from exc
        finally:
            self._close_pipes(process)

    @staticmethod
    def _close_pipes(process: subprocess.Popen) -> None:
        """Close each available pipe independently, even after a failed wait."""
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass

    def _cleanup_worker(self) -> None:
        pid = self.worker_pid
        if pid is None or not process_alive(pid):
            return
        self.release.write_text("release", encoding="utf-8")
        if self._wait_pid_gone(pid, 10):
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        self._wait_pid_gone(pid, 5)

    def wait_ready(self, processes: list[subprocess.Popen]) -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.ready.exists():
                text = self.ready.read_text(encoding="utf-8").strip()
                if text:
                    self.worker_pid = int(text)
                    return
            if all(process.poll() is not None for process in processes):
                details = []
                for process in processes:
                    stdout, stderr = process.communicate()
                    details.append(
                        f"rc={process.returncode} out={stdout!r} "
                        f"err={stderr!r}"
                    )
                self.fail("Owner exited before worker readiness: " + "; ".join(details))
            time.sleep(0.02)
        self.fail("Worker readiness timed out")

    def wait_any_exit(self, processes: list[subprocess.Popen]) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                return
            time.sleep(0.02)
        self.fail("No contender exited while the owner held the lock")

    def release_worker(self) -> None:
        self.release.write_text("release", encoding="utf-8")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.exited.exists():
                break
            time.sleep(0.02)
        else:
            self.fail("Worker did not report exit after release")
        if self.worker_pid is not None:
            self.assertTrue(
                self._wait_pid_gone(self.worker_pid, 10),
                "Worker process is still alive after release",
            )

    def _wait_pid_alive(self, pid: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process_alive(pid):
                return True
            time.sleep(0.02)
        return process_alive(pid)

    def _wait_pid_gone(self, pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not process_alive(pid):
                return True
            time.sleep(0.02)
        return not process_alive(pid)

    def state_bytes(self) -> bytes:
        return (self.root / ".omp/state.json").read_bytes()

    def state(self) -> dict[str, object]:
        return json.loads(self.state_bytes().decode("utf-8"))

    def worker_starts(self) -> list[int]:
        if not self.starts.exists():
            return []
        return [
            int(line)
            for line in self.starts.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_owner_blocks_resume_and_run_status_is_read_only(self) -> None:
        self.prepare()
        owner = self.start_run()
        self.wait_ready([owner])
        state_before = self.state_bytes()
        pid = self.worker_pid
        self.assertIsNotNone(pid)
        self.assertEqual(self.worker_starts(), [pid])
        self.assertTrue((self.root / ".omp/lock").exists())

        resume = self.run_omp("resume")
        self.assertNotEqual(resume.returncode, 0, resume.stderr)
        self.assertNotIn("Traceback", resume.stderr)
        self.assertIn("holds the project lock", resume.stderr)

        new_run = self.run_omp("run", str(self.task_path))
        self.assertNotEqual(new_run.returncode, 0, new_run.stderr)
        self.assertNotIn("Traceback", new_run.stderr)

        status = self.run_omp("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["phase"], "worker")
        self.assertEqual(self.state_bytes(), state_before)
        self.assertEqual(self.worker_starts(), [pid])
        self.assertTrue((self.root / ".omp/lock").exists())

        self.release_worker()
        stdout, stderr = owner.communicate(timeout=60)
        self.assertEqual(owner.returncode, 0, stdout + stderr)
        self.assertEqual(self.state()["phase"], "complete")
        self.assertEqual(self.worker_starts(), [pid])

    def test_simultaneous_runs_have_one_owner_and_one_worker(self) -> None:
        self.prepare()
        processes = [self.start_run(), self.start_run()]
        self.wait_ready(processes)
        self.wait_any_exit(processes)
        pid = self.worker_pid
        self.assertIsNotNone(pid)
        self.assertEqual(self.worker_starts(), [pid])

        self.release_worker()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            results.append((process.returncode, stdout, stderr))
        self.assertEqual(
            sum(returncode == 0 for returncode, _, _ in results),
            1,
            results,
        )
        for returncode, _, stderr in results:
            self.assertNotIn("Traceback", stderr)
            if returncode != 0:
                self.assertTrue(stderr.strip(), "contender produced no error")
        self.assertEqual(self.state()["phase"], "complete")
        self.assertEqual(self.worker_starts(), [pid])

    def test_hard_kill_releases_lock_but_child_pid_blocks_resume(self) -> None:
        self.prepare()
        owner = self.start_run()
        self.wait_ready([owner])
        state_before = self.state_bytes()
        pid = self.worker_pid
        self.assertIsNotNone(pid)
        self.assertTrue(process_alive(pid))

        owner.kill()
        owner.wait(timeout=10)
        self.assertIsNotNone(owner.poll())
        self.assertTrue((self.root / ".omp/lock").exists())

        resume = self.run_omp("resume")
        self.assertNotEqual(resume.returncode, 0, resume.stderr)
        self.assertIn("Previous child PID", resume.stderr)
        self.assertNotIn("holds the project lock", resume.stderr)
        self.assertEqual(self.state_bytes(), state_before)
        self.assertEqual(self.worker_starts(), [pid])
        self.assertTrue(process_alive(pid))

        self.release_worker()
        self.assertEqual(self.state_bytes(), state_before)

    def test_pid_only_metadata_does_not_grant_lock_or_kill(self) -> None:
        """PID-only metadata is not ownership; no incarnation registry is used."""
        self.prepare()
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=self.root,
            env=self.env(),
            text=True,
        )
        self.addCleanup(self._terminate, sleeper)
        self.assertTrue(
            self._wait_pid_alive(sleeper.pid),
            "Sleeper did not start",
        )
        state = {
            "schema_version": 2,
            "run_id": "pid-only-run",
            "phase": "worker",
            "base_commit": self.base,
            "config": json.loads(
                (self.root / ".omp/config.json").read_text(encoding="utf-8")
            ),
            "task": json.loads(self.task_path.read_text(encoding="utf-8")),
            "child_pid": sleeper.pid,
        }
        validate_state(state)
        state_path = self.root / ".omp/state.json"
        save(state_path, state)
        state_before = state_path.read_bytes()

        resume = self.run_omp("resume")
        self.assertNotEqual(resume.returncode, 0, resume.stderr)
        self.assertIn("Previous child PID", resume.stderr)
        self.assertNotIn("holds the project lock", resume.stderr)
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(self.worker_starts(), [])
        self.assertTrue(process_alive(sleeper.pid))

    def test_terminate_timeout_fallback_is_bounded_and_reports(self) -> None:
        fake = _TimeoutProcess()
        with self.assertRaisesRegex(
            AssertionError, r"cleanup failed for PID 4242.*timed out twice"
        ):
            self._terminate(fake)  # type: ignore[arg-type]
        self.assertEqual(
            fake.communicate_calls,
            [self.TERMINATE_TIMEOUT, self.TERMINATE_TIMEOUT],
        )
        self.assertEqual(fake.kill_calls, 2)
        self.assertTrue(fake.stdin.closed)
        self.assertTrue(fake.stdout.closed)
        self.assertTrue(fake.stderr.closed)

    def test_negative_control_resume_without_state_exits_before_lock(self) -> None:
        self.prepare()
        resume = self.run_omp("resume")
        self.assertNotEqual(resume.returncode, 0)
        self.assertIn("state.json", resume.stderr)
        self.assertFalse((self.root / ".omp/lock").exists())
        self.assertEqual(self.worker_starts(), [])


if __name__ == "__main__":
    unittest.main()
