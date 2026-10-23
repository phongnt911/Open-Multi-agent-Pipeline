"""Native Windows containment tests for PROC-02.

These tests use real Python stand-in Workers, not an AI provider.  They prove
job membership before resume, child/grandchild termination, owner hard-kill
without a cleanup path, timeout, root early-exit, breakaway denial, and the
save-before-resume gap.  Non-Windows runs skip the class; POSIX behavior is
covered by the existing suite.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Iterator
import unittest
from unittest.mock import patch

from omp_core import main as pipeline
from omp_core import windows_job
from omp_core.main import OperationError, execute
from omp_core.windows_job import (
    WindowsJobCleanupTimeout,
    WindowsJobError,
    WindowsJobProcess,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

TREE_CODE = r'''
import os
from pathlib import Path
import subprocess
import sys
import time

directory = Path(sys.argv[1])
depth = int(sys.argv[2])
slot = sys.argv[3]
script = Path(sys.argv[4])
action = sys.argv[5] if len(sys.argv) > 5 else "sleep"
if depth:
    child_slot = "child" if depth == 2 else "grandchild"
    subprocess.Popen([
        sys.executable,
        "-c",
        script.read_text(encoding="utf-8"),
        str(directory),
        str(depth - 1),
        child_slot,
        str(script),
        action,
    ])
(directory / f"{slot}.pid").write_text(str(os.getpid()), encoding="utf-8")
if slot == "root":
    expected = [
        directory / f"{name}.pid" for name in ("root", "child", "grandchild")
    ]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(path.exists() for path in expected):
            (directory / "ready").write_text("ready", encoding="utf-8")
            break
        time.sleep(0.01)
    else:
        raise SystemExit("tree readiness timeout")
    if action == "exit":
        raise SystemExit(0)
time.sleep(60)
'''

OWNER_CODE = r'''
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, sys.argv[1])
from omp_core.windows_job import WindowsJobProcess

directory = Path(sys.argv[2])
tree_script = Path(sys.argv[3])
mode = sys.argv[4]
code = tree_script.read_text(encoding="utf-8")
process = WindowsJobProcess.start(
    [sys.executable, "-c", code, str(directory), "2", "root",
     str(tree_script), "sleep"],
    directory,
    directory / "owner.log",
    temp_dir=directory,
)
(directory / "owner.pid").write_text(str(os.getpid()), encoding="utf-8")
(directory / "root.pid").write_text(str(process.pid), encoding="utf-8")
if mode in ("saved", "running"):
    (directory / "saved").write_text("saved", encoding="utf-8")
if mode == "running":
    process.resume()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (directory / "ready").exists():
            (directory / "running.ready").write_text(
                "running", encoding="utf-8"
            )
            break
        time.sleep(0.01)
    else:
        raise SystemExit("owner tree readiness timeout")
time.sleep(60)
'''

BREAKAWAY_CODE = r'''
import os
from pathlib import Path
import subprocess
import sys
import time

marker = Path(sys.argv[1])
try:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=0x01000000,
    )
except OSError as exc:
    marker.write_text(
        f"denied {getattr(exc, 'winerror', None)}", encoding="utf-8"
    )
else:
    marker.write_text(f"escaped {child.pid}", encoding="utf-8")
time.sleep(60)
'''

DETACHED_CODE = r'''
from pathlib import Path
import os
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    creationflags=0x00000008,
)
Path(sys.argv[1]).write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
'''


def _kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
    ]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def process_alive(pid: int) -> bool:
    """Return whether *pid* is alive, without treating reuse as ownership."""
    api = _kernel32()
    handle = api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        api.CloseHandle(handle)


def terminate_pid(pid: int) -> None:
    """Best-effort cleanup for a PID recorded by this test fixture."""
    api = _kernel32()
    handle = api.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return
    try:
        api.TerminateProcess(handle, 1)
    finally:
        api.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt", "Windows Job Object tests")
class ProcessContainmentTests(unittest.TestCase):
    """Real-process containment matrix for the Windows-only boundary."""

    def setUp(self) -> None:
        scratch = REPO_ROOT / ".omp/test-tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(
            dir=scratch, ignore_cleanup_errors=True
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.markers = self.root / "markers"
        self.markers.mkdir()
        self.tree_script = self.root / "tree.py"
        self.tree_script.write_text(TREE_CODE, encoding="utf-8")
        self.breakaway_script = self.root / "breakaway.py"
        self.breakaway_script.write_text(BREAKAWAY_CODE, encoding="utf-8")
        self.owner_script = self.root / "owner.py"
        self.owner_script.write_text(OWNER_CODE, encoding="utf-8")
        self._owners: list[subprocess.Popen[str]] = []
        self._pids: set[int] = set()
        self.addCleanup(self._cleanup_processes)

    def _cleanup_processes(self) -> None:
        for owner in self._owners:
            if owner.poll() is None:
                owner.kill()
                try:
                    owner.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            try:
                owner.communicate(timeout=1)
            except (subprocess.TimeoutExpired, ValueError):
                pass
        for pid in self._pids:
            if process_alive(pid):
                terminate_pid(pid)

    def _track(self, pid: int) -> None:
        self._pids.add(int(pid))

    def _wait_file(self, path: Path, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                return
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {path.name}")

    def _read_pid(self, name: str) -> int:
        path = self.markers / f"{name}.pid"
        self._wait_file(path)
        return int(path.read_text(encoding="utf-8").strip())

    def _wait_gone(self, pid: int, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not process_alive(pid):
                return
            time.sleep(0.02)
        raise AssertionError(f"pid {pid} is still alive")

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

    def _tree_argv(self, action: str = "sleep") -> list[str]:
        return [
            sys.executable,
            "-c",
            TREE_CODE,
            str(self.markers),
            "2",
            "root",
            str(self.tree_script),
            action,
        ]

    def _start_tree(self) -> WindowsJobProcess:
        return WindowsJobProcess.start(
            self._tree_argv(),
            self.root,
            self.root / "tree.log",
            temp_dir=self.markers,
        )

    def _stop_tree(self, process: WindowsJobProcess) -> None:
        try:
            if not process.tree_stopped:
                process.terminate_tree(5)
        finally:
            process.close()

    def _start_owner(self, mode: str) -> tuple[subprocess.Popen[str], list[int]]:
        owner = subprocess.Popen(
            [
                sys.executable,
                str(self.owner_script),
                str(REPO_ROOT),
                str(self.markers),
                str(self.tree_script),
                mode,
            ],
            cwd=str(self.root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._owners.append(owner)
        self._wait_file(self.markers / "owner.pid")
        self._wait_file(self.markers / "root.pid")
        if mode == "saved":
            self._wait_file(self.markers / "saved")
        if mode == "running":
            self._wait_file(self.markers / "running.ready")
            pids = [
                self._read_pid(name)
                for name in ("root", "child", "grandchild")
            ]
        else:
            pids = [self._read_pid("root")]
        for pid in pids:
            self._track(pid)
        return owner, pids

    def test_job_contains_worker_child_and_grandchild_before_termination(self):
        process = self._start_tree()
        pids: list[int] = []
        try:
            self.assertFalse(process.resumed)
            self.assertTrue(process.in_job)
            self.assertEqual(process.active_processes, 1)
            process.resume()
            self._wait_file(self.markers / "ready")
            pids = [
                self._read_pid(name)
                for name in ("root", "child", "grandchild")
            ]
            for pid in pids:
                self._track(pid)
                self.assertTrue(process_alive(pid), pid)
            self.assertEqual(process.active_processes, 3)
            process.terminate_tree(5)
            self.assertTrue(process.tree_stopped)
        finally:
            process.close()
        for pid in pids:
            self._wait_gone(pid)

    def test_worker_does_not_execute_before_resume(self):
        marker = self.markers / "started"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('started'); time.sleep(60)"
        )
        process = WindowsJobProcess.start(
            [sys.executable, "-c", code, str(marker)],
            self.root,
            self.root / "early.log",
            temp_dir=self.markers,
        )
        try:
            time.sleep(0.2)
            self.assertFalse(process.resumed)
            self.assertTrue(process.in_job)
            self.assertFalse(marker.exists())
            process.resume()
            self._wait_file(marker)
            self.assertTrue(process.tree_stopped is False)
            process.terminate_tree(5)
            self.assertTrue(process.tree_stopped)
        finally:
            process.close()

    def test_stdin_prompt_is_delivered_to_contained_worker(self):
        marker = self.markers / "prompt.txt"
        prompt = "line one\nline two\n"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text(sys.stdin.read(), encoding='utf-8'); "
            "time.sleep(60)"
        )
        process = WindowsJobProcess.start(
            [sys.executable, "-c", code, str(marker)],
            self.root,
            self.root / "prompt.log",
            stdin_text=prompt,
            temp_dir=self.markers,
        )
        try:
            process.resume()
            self._wait_file(marker)
            self.assertEqual(marker.read_text(encoding="utf-8"), prompt)
        finally:
            process.terminate_tree(5)
            process.close()

    def test_owner_hard_kill_stops_running_tree_without_cleanup(self):
        owner, pids = self._start_owner("running")
        for pid in pids:
            self.assertTrue(process_alive(pid), pid)
        owner.kill()
        owner.wait(timeout=10)
        for pid in pids:
            self._wait_gone(pid)

    def test_owner_hard_kill_stops_suspended_tree_before_save(self):
        owner, pids = self._start_owner("suspended")
        root = pids[0]
        self.assertTrue(process_alive(root))
        owner.kill()
        owner.wait(timeout=10)
        self._wait_gone(root)

    def test_owner_hard_kill_stops_suspended_tree_after_save_before_resume(self):
        owner, pids = self._start_owner("saved")
        root = pids[0]
        self.assertTrue((self.markers / "saved").exists())
        self.assertTrue(process_alive(root))
        owner.kill()
        owner.wait(timeout=10)
        self._wait_gone(root)

    def test_execute_timeout_stops_descendants_and_clears_child_pid(self):
        state = {"config": {"timeout_seconds": 1}, "child_pid": None}
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            execute(
                self._tree_argv(),
                self.root,
                self.root,
                state,
                self.root / "state.json",
                "worker",
            )
        self.assertIn("worker", str(caught.exception))
        self.assertIsNone(state["child_pid"])
        pids = [
            self._read_pid(name)
            for name in ("root", "child", "grandchild")
        ]
        for pid in pids:
            self._track(pid)
            self._wait_gone(pid)

    def test_root_early_exit_is_failure_and_stops_descendants(self):
        state = {"config": {"timeout_seconds": 3}, "child_pid": None}
        with self.assertRaises(OperationError) as caught:
            execute(
                self._tree_argv("exit"),
                self.root,
                self.root,
                state,
                self.root / "state.json",
                "worker",
            )
        self.assertIn("descendant", str(caught.exception))
        self.assertIsNone(state["child_pid"])
        pids = [
            self._read_pid(name)
            for name in ("root", "child", "grandchild")
        ]
        for pid in pids:
            self._track(pid)
            self._wait_gone(pid)

    def test_query_failure_after_root_exit_preserves_winerror_cause(self):
        state = {"config": {"timeout_seconds": 3}, "child_pid": None}
        real_active = windows_job._active_processes
        calls = 0

        def flaky_active(api, job):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise WindowsJobError(
                    "query job accounting failed "
                    "(WinError 5: injected access denied)"
                )
            return real_active(api, job)

        with patch.object(
            windows_job, "_active_processes", side_effect=flaky_active
        ):
            with self.assertRaises(OperationError) as caught:
                execute(
                    [sys.executable, "-c", "pass"],
                    self.root,
                    self.root,
                    state,
                    self.root / "state.json",
                    "worker",
                )
        self.assertIsNone(state["child_pid"])
        self.assertGreaterEqual(calls, 2)
        self.assertIsInstance(caught.exception.__cause__, WindowsJobError)
        outer = caught.exception.__cause__
        self.assertIsInstance(outer.__context__, WindowsJobError)
        self.assertIsInstance(outer.__cause__, WindowsJobError)
        self.assertIn("query job accounting failed", str(outer.__cause__))
        self.assertIn("WinError 5", str(outer.__cause__))
        messages = [str(item) for item in self._walk_causes(caught.exception)]
        self.assertTrue(
            any("query job accounting failed" in text for text in messages)
        )
        self.assertTrue(any("WinError 5" in text for text in messages))

    def test_ctrl_c_fault_injection_stops_tree_and_clears_child_pid(self):
        state = {"config": {"timeout_seconds": 5}, "child_pid": None}

        test = self

        def interrupted(process, input=None, timeout=None):
            del process, input, timeout
            test._wait_file(test.markers / "ready")
            raise KeyboardInterrupt

        with patch.object(WindowsJobProcess, "communicate", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                execute(
                    self._tree_argv(),
                    self.root,
                    self.root,
                    state,
                    self.root / "state.json",
                    "worker",
                )
        self.assertIsNone(state["child_pid"])
        pids = [
            self._read_pid(name)
            for name in ("root", "child", "grandchild")
        ]
        for pid in pids:
            self._track(pid)
            self._wait_gone(pid)

    def test_save_failure_before_resume_never_executes_worker(self):
        marker = self.markers / "worker-ran"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('ran'); time.sleep(60)"
        )
        state = {"config": {"timeout_seconds": 5}, "child_pid": None}
        recorded: list[int] = []
        real_save = pipeline.save

        def failing_save(path: Path, value: dict) -> None:
            if value.get("child_pid") is not None:
                recorded.append(int(value["child_pid"]))
                raise OSError("injected save gap")
            real_save(path, value)

        with patch.object(pipeline, "save", side_effect=failing_save):
            with self.assertRaises(OSError) as caught:
                execute(
                    [sys.executable, "-c", code, str(marker)],
                    self.root,
                    self.root,
                    state,
                    self.root / "state.json",
                    "worker",
                )
        self.assertIn("injected save gap", str(caught.exception))
        self.assertEqual(len(recorded), 1)
        self._track(recorded[0])
        self.assertFalse(marker.exists())
        self._wait_gone(recorded[0])
        self.assertIsNone(state["child_pid"])

    def test_resume_failure_stops_suspended_worker_before_execution(self):
        marker = self.markers / "resume-ran"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('ran'); time.sleep(60)"
        )
        state = {"config": {"timeout_seconds": 5}, "child_pid": None}

        def failing_resume(process):
            del process
            raise WindowsJobError("injected resume failure")

        with patch.object(WindowsJobProcess, "resume", failing_resume):
            with self.assertRaises(OperationError) as caught:
                execute(
                    [sys.executable, "-c", code, str(marker)],
                    self.root,
                    self.root,
                    state,
                    self.root / "state.json",
                    "worker",
                )
        self.assertIn("resume", str(caught.exception))
        self.assertFalse(marker.exists())
        self.assertIsNone(state["child_pid"])

    def test_attach_setup_failure_is_fail_closed_without_popen_fallback(self):
        marker = self.markers / "fallback-ran"
        code = (
            "from pathlib import Path; import sys, time; "
            "Path(sys.argv[1]).write_text('ran'); time.sleep(60)"
        )
        state = {"config": {"timeout_seconds": 5}, "child_pid": None}
        with patch(
            "omp_core.windows_job._attribute_list",
            side_effect=WindowsJobError("injected attach failure"),
        ):
            with self.assertRaises(OperationError) as caught:
                execute(
                    [sys.executable, "-c", code, str(marker)],
                    self.root,
                    self.root,
                    state,
                    self.root / "state.json",
                    "worker",
                )
        self.assertIn("Cannot start", str(caught.exception))
        self.assertFalse(marker.exists())
        self.assertIsNone(state["child_pid"])

    def test_breakaway_is_denied_and_tree_can_be_stopped(self):
        marker = self.markers / "breakaway"
        process = WindowsJobProcess.start(
            [sys.executable, "-c", BREAKAWAY_CODE, str(marker)],
            self.root,
            self.root / "breakaway.log",
            temp_dir=self.markers,
        )
        escaped: int | None = None
        try:
            process.resume()
            self._wait_file(marker)
            value = marker.read_text(encoding="utf-8").strip()
            if value.startswith("escaped "):
                escaped = int(value.split()[1])
                self._track(escaped)
            self.assertTrue(value.startswith("denied"), value)
            self.assertEqual(process.active_processes, 1)
        finally:
            process.terminate_tree(5)
            process.close()
        if escaped is not None:
            self._wait_gone(escaped)

    def test_detached_child_remains_in_job_and_is_stopped(self):
        marker = self.markers / "detached.pid"
        process = WindowsJobProcess.start(
            [sys.executable, "-c", DETACHED_CODE, str(marker)],
            self.root,
            self.root / "detached.log",
            temp_dir=self.markers,
        )
        child_pid = 0
        try:
            process.resume()
            self._wait_file(marker)
            child_pid = int(marker.read_text(encoding="utf-8").strip())
            self._track(child_pid)
            self.assertTrue(process_alive(child_pid))
            self.assertEqual(process.active_processes, 2)
        finally:
            process.terminate_tree(5)
            process.close()
        if child_pid:
            self._wait_gone(child_pid)

    def test_cleanup_deadline_is_bounded_and_reports_timeout(self):
        process = WindowsJobProcess.start(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            self.root,
            self.root / "deadline.log",
            temp_dir=self.markers,
        )
        started = time.monotonic()
        try:
            process.resume()
            with patch(
                "omp_core.windows_job._active_processes",
                return_value=1,
            ):
                with self.assertRaises(WindowsJobCleanupTimeout):
                    process.terminate_tree(0.2)
            self.assertLess(time.monotonic() - started, 2.0)
            process.terminate_tree(5)
            self.assertTrue(process.tree_stopped)
        finally:
            process.close()
