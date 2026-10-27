"""SEC-02-B1a: owned AppContainer profile and optional native containment.

The unit tests use fake Win32 tables and never touch the operating system.
The native tests run real CPython Workers inside a real AppContainer and Job
Object, using only an owned temporary runtime/workspace/profile and owned
canaries.  They do not import ``tools.proofs`` or monkeypatch the production
API being measured.  They are evidence for B1a primitives on this host, not a
claim that the repository, a vault, or a real Worker layout is protected.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from omp_core import windows_job
from omp_core.windows_job import (
    WindowsAppContainerProfile,
    WindowsJobCleanupTimeout,
    WindowsJobError,
    WindowsJobProcess,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = REPO_ROOT / ".omp" / "test-tmp"
LOW_INTEGRITY_SID = "S-1-16-4096"
MEDIUM_INTEGRITY_SID = "S-1-16-8192"
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_IS_APP_CONTAINER = 29
TOKEN_CAPABILITIES = 30
TOKEN_APPCONTAINER_SID = 31
HANDLE_FLAG_INHERIT = 0x00000001
FAILED = 0xFFFFFFFF


def _handle_value(handle: object) -> int:
    return int(getattr(handle, "value", handle) or 0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_causes(error: BaseException) -> list[BaseException]:
    pending = [error]
    seen: set[int] = set()
    found: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return found


class _FakeUserenv:
    """Userenv table returning scripted HRESULTs and SID pointers."""

    def __init__(
        self,
        *,
        create_hr: int = 0,
        pointer: int = 0xB1A0,
        delete_hr: int = 0,
        interrupt: BaseException | None = None,
        interrupt_on_failure: bool = False,
        fill_pointer_on_failure: bool = False,
    ) -> None:
        self.create_hr = create_hr
        self.pointer = pointer
        self.delete_hr = delete_hr
        self.interrupt = interrupt
        self.interrupt_on_failure = interrupt_on_failure
        self.fill_pointer_on_failure = fill_pointer_on_failure
        self.created: list[tuple[str, str, str, object, int]] = []
        self.deleted: list[str] = []

    def CreateAppContainerProfile(
        self,
        name: str,
        display: str,
        description: str,
        capabilities: object,
        count: int,
        out: object,
    ) -> int:
        self.created.append((name, display, description, capabilities, count))
        if self.create_hr:
            if self.fill_pointer_on_failure:
                out._obj.value = self.pointer
            if self.interrupt is not None and self.interrupt_on_failure:
                raise self.interrupt
            return self.create_hr
        out._obj.value = self.pointer
        if self.interrupt is not None:
            raise self.interrupt
        return 0

    def DeleteAppContainerProfile(self, name: str) -> int:
        self.deleted.append(name)
        return self.delete_hr

    def GetAppContainerFolderPath(self, sid: str, out: object) -> int:
        return 0


class _FakeKernel:
    """Kernel32 table for lifecycle and start-failure unit tests."""

    def __init__(
        self,
        *,
        create_ok: bool = True,
        create_interrupt: bool = False,
        in_job: bool = True,
        in_job_ok: bool = True,
        query_ok: bool = True,
        active: int = 0,
        resume_ok: bool = True,
        terminate_ok: bool = True,
        wait_result: int = 0,
        process_id: int = 4242,
        fail_close: tuple[int, ...] = (),
        interrupt_close: int = 0,
        close_interrupt: BaseException | None = None,
        clear_on_terminate: bool = True,
    ) -> None:
        self.create_ok = create_ok
        self.create_interrupt = create_interrupt
        self.in_job = in_job
        self.in_job_ok = in_job_ok
        self.query_ok = query_ok
        self.active = active
        self.clear_on_terminate = clear_on_terminate
        self.resume_ok = resume_ok
        self.terminate_ok = terminate_ok
        self.wait_result = wait_result
        self.process_id = process_id
        self.fail_close = fail_close
        self.interrupt_close = interrupt_close
        self.close_interrupt = close_interrupt
        self.create_args: tuple[object, ...] | None = None
        self.resumed: list[int] = []
        self.terminated: list[int] = []
        self.closed: list[int] = []
        self.deleted_attributes = 0

    def CreateJobObjectW(self, attributes: object, name: object) -> object:
        return wintypes.HANDLE(101)

    def SetInformationJobObject(self, *args: object) -> bool:
        return True

    def QueryInformationJobObject(
        self, job: object, information_class: int, buffer: object,
        size: int, returned: object,
    ) -> bool:
        if information_class == 1:
            if not self.query_ok:
                ctypes.set_last_error(5)
                return False
            buffer._obj.ActiveProcesses = self.active
        return True

    def InitializeProcThreadAttributeList(self, *args: object) -> bool:
        return True

    def UpdateProcThreadAttribute(self, *args: object) -> bool:
        return True

    def DeleteProcThreadAttributeList(self, pointer: object) -> None:
        self.deleted_attributes += 1

    def CreateProcessW(self, *args: object) -> bool:
        self.create_args = args
        info = args[-1]._obj  # type: ignore[attr-defined]
        if self.create_interrupt:
            info.hProcess = wintypes.HANDLE(500)
            info.hThread = wintypes.HANDLE(501)
            info.dwProcessId = self.process_id
            raise KeyboardInterrupt
        if not self.create_ok:
            ctypes.set_last_error(5)
            return False
        info.hProcess = wintypes.HANDLE(500)
        info.hThread = wintypes.HANDLE(501)
        info.dwProcessId = self.process_id
        info.dwThreadId = 4243
        return True

    def ResumeThread(self, thread: object) -> int:
        self.resumed.append(_handle_value(thread))
        if not self.resume_ok:
            ctypes.set_last_error(5)
            return FAILED
        return 1

    def WaitForSingleObject(self, handle: object, milliseconds: int) -> int:
        return self.wait_result

    def GetExitCodeProcess(self, process: object, out: object) -> bool:
        out._obj.value = 0
        return True

    def IsProcessInJob(
        self, process: object, job: object, out: object
    ) -> bool:
        if not self.in_job_ok:
            ctypes.set_last_error(5)
            return False
        out._obj.value = 1 if self.in_job else 0
        return True

    def TerminateJobObject(self, job: object, code: int) -> bool:
        self.terminated.append(_handle_value(job))
        if not self.terminate_ok:
            ctypes.set_last_error(5)
            return False
        if self.clear_on_terminate:
            self.active = 0
        return True

    def TerminateProcess(self, process: object, code: int) -> bool:
        self.terminated.append(_handle_value(process))
        return self.terminate_ok

    def SetHandleInformation(
        self, handle: object, mask: int, flags: int
    ) -> bool:
        return True

    def CloseHandle(self, handle: object) -> bool:
        value = _handle_value(handle)
        self.closed.append(value)
        if value == self.interrupt_close and self.close_interrupt is not None:
            raise self.close_interrupt
        if value in self.fail_close:
            ctypes.set_last_error(5)
            return False
        return True

    def GetCurrentProcess(self) -> object:
        return wintypes.HANDLE(999)

    def LocalFree(self, value: object) -> None:
        return None


class _FakeAttributes:
    """Proc-thread attribute table recording each update."""

    def __init__(
        self, *, fail_index: int | None = None, init_ok: bool = True,
        size_error: int = 122,
    ) -> None:
        self.fail_index = fail_index
        self.init_ok = init_ok
        self.size_error = size_error
        self.sized_counts: list[int] = []
        self.initialized_count: int | None = None
        self.updated: list[tuple[int, int, object]] = []
        self.deleted = 0

    def InitializeProcThreadAttributeList(
        self, pointer: object, count: int, flags: int, size: object
    ) -> bool:
        if not getattr(pointer, "value", pointer):
            self.sized_counts.append(count)
            if self.size_error:
                ctypes.set_last_error(self.size_error)
            size._obj.value = 256
            return False
        if not self.init_ok:
            ctypes.set_last_error(5)
            return False
        self.initialized_count = count
        return True

    def UpdateProcThreadAttribute(
        self, pointer: object, flags: int, attribute: int,
        value: object, size: int, previous: object, returned: object,
    ) -> bool:
        if self.fail_index == len(self.updated):
            ctypes.set_last_error(5)
            return False
        self.updated.append((attribute, size, getattr(value, "_obj", value)))
        return True

    def DeleteProcThreadAttributeList(self, pointer: object) -> None:
        self.deleted += 1


def _make_profile(
    *,
    sid: str = "S-1-15-2-777",
    folder: Path = Path("C:/fake/profile"),
    userenv: _FakeUserenv | None = None,
) -> WindowsAppContainerProfile:
    table = userenv or _FakeUserenv()
    with (
        patch.object(windows_job, "_userenv", return_value=table),
        patch.object(windows_job, "_advapi32", return_value=object()),
        patch.object(windows_job, "_sid_string", return_value=sid),
        patch.object(windows_job, "_app_container_folder", return_value=folder),
    ):
        return WindowsAppContainerProfile.create("OMP.B1A.Unit")


def _make_process(
    api: _FakeKernel | None = None,
    *,
    app_container: WindowsAppContainerProfile | None = None,
    stdin_path: Path | None = None,
    cleanup_timeout: float = 1.0,
) -> WindowsJobProcess:
    return WindowsJobProcess(
        api=api or _FakeKernel(),
        job=wintypes.HANDLE(101),
        process=wintypes.HANDLE(102),
        thread=wintypes.HANDLE(103),
        pid=4242,
        stdin_path=stdin_path or Path(tempfile.gettempdir()) / "b1a-missing-stdin",
        cleanup_timeout=cleanup_timeout,
        in_job=True,
        app_container=app_container,
    )


class _BoundStub:
    """Minimal binding target exposing only ``tree_stopped``."""

    def __init__(self, stopped: bool) -> None:
        self.tree_stopped = stopped
        self._app_container: WindowsAppContainerProfile | None = None


class ProfileOwnershipTests(unittest.TestCase):
    """B1a-1: create/close ownership without adopting foreign resources."""

    def test_create_rejects_invalid_name_before_native_calls(self):
        with patch.object(
            windows_job, "_userenv", side_effect=AssertionError("native called")
        ):
            for value in ("", "bad\0name", 7, None):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        WindowsAppContainerProfile.create(value)  # type: ignore[arg-type]

    def test_existing_name_collision_is_not_adopted_or_deleted(self):
        table = _FakeUserenv(create_hr=0x800700B7)
        with (
            patch.object(windows_job, "_userenv", return_value=table),
            patch.object(windows_job, "_advapi32", return_value=object()),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=AssertionError("deleted foreign profile"),
            ),
            patch.object(
                windows_job, "_free_sid",
                side_effect=AssertionError("freed foreign SID"),
            ),
        ):
            with self.assertRaises(WindowsJobError) as caught:
                WindowsAppContainerProfile.create("OMP.B1A.Collision")
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(table.created[0][0], "OMP.B1A.Collision")
        self.assertEqual(table.deleted, [])

    def test_interrupt_after_allocation_releases_owned_profile(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(interrupt=type(interrupt).__name__):
                table = _FakeUserenv(pointer=0xFEED, interrupt=interrupt)
                freed: list[int] = []
                deleted: list[str] = []
                with (
                    patch.object(windows_job, "_userenv", return_value=table),
                    patch.object(windows_job, "_advapi32", return_value=object()),
                    patch.object(
                        windows_job, "_free_sid",
                        side_effect=lambda api, sid: freed.append(sid),
                    ),
                    patch.object(
                        windows_job, "_delete_profile",
                        side_effect=lambda api, name: deleted.append(name),
                    ),
                ):
                    with self.assertRaises(type(interrupt)):
                        WindowsAppContainerProfile.create("OMP.B1A.Interrupt")
                self.assertEqual(freed, [0xFEED])
                self.assertEqual(deleted, ["OMP.B1A.Interrupt"])

    def test_interrupt_during_owner_construction_releases_owned_profile(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(4)):
            with self.subTest(interrupt=type(interrupt).__name__):
                table = _FakeUserenv(pointer=0x1234)
                freed: list[int] = []
                deleted: list[str] = []
                with (
                    patch.object(windows_job, "_userenv", return_value=table),
                    patch.object(windows_job, "_advapi32", return_value=object()),
                    patch.object(windows_job, "_sid_string", return_value="S-1-15-2-8"),
                    patch.object(
                        windows_job, "_app_container_folder",
                        return_value=Path("C:/fake/profile"),
                    ),
                    patch.object(
                        windows_job, "_free_sid",
                        side_effect=lambda api, sid: freed.append(sid),
                    ),
                    patch.object(
                        windows_job, "_delete_profile",
                        side_effect=lambda api, name: deleted.append(name),
                    ),
                    patch.object(
                        WindowsAppContainerProfile, "__init__",
                        side_effect=interrupt,
                    ),
                ):
                    with self.assertRaises(type(interrupt)):
                        WindowsAppContainerProfile.create("OMP.B1A.Owner")
                self.assertEqual(freed, [0x1234])
                self.assertEqual(deleted, ["OMP.B1A.Owner"])

    def test_combined_cleanup_failures_preserve_primary_exception(self):
        for failure in (
            KeyboardInterrupt(),
            SystemExit(5),
            WindowsJobError("convert AppContainer SID to string failed"),
        ):
            with self.subTest(failure=type(failure).__name__):
                table = _FakeUserenv(pointer=0x5150)
                with (
                    patch.object(windows_job, "_userenv", return_value=table),
                    patch.object(windows_job, "_advapi32", return_value=object()),
                    patch.object(
                        windows_job, "_sid_string", side_effect=failure
                    ),
                    patch.object(
                        windows_job, "_free_sid",
                        side_effect=WindowsJobError("free SID exploded"),
                    ),
                    patch.object(
                        windows_job, "_delete_profile",
                        side_effect=WindowsJobError("delete profile exploded"),
                    ),
                ):
                    with self.assertRaises(type(failure)) as caught:
                        WindowsAppContainerProfile.create("OMP.B1A.Combined")
                self.assertIs(caught.exception, failure)
                notes = " ".join(getattr(failure, "__notes__", []))
                self.assertIn("SID release failed", notes)
                self.assertIn("profile deletion failed", notes)
                self.assertIsNone(failure.__cause__)

    def test_collision_never_frees_or_deletes_foreign_profile(self):
        tables = (
            (
                _FakeUserenv(
                    create_hr=0x800700B7, pointer=0xDEAD,
                    fill_pointer_on_failure=True,
                ),
                WindowsJobError,
            ),
            (
                _FakeUserenv(
                    create_hr=0x800700B7, interrupt=KeyboardInterrupt(),
                    interrupt_on_failure=True,
                ),
                KeyboardInterrupt,
            ),
        )
        for table, expected in tables:
            with self.subTest(expected=expected.__name__):
                freed: list[int] = []
                deleted: list[str] = []
                with (
                    patch.object(windows_job, "_userenv", return_value=table),
                    patch.object(windows_job, "_advapi32", return_value=object()),
                    patch.object(
                        windows_job, "_free_sid",
                        side_effect=lambda api, sid: freed.append(sid),
                    ),
                    patch.object(
                        windows_job, "_delete_profile",
                        side_effect=lambda api, name: deleted.append(name),
                    ),
                ):
                    with self.assertRaises(expected):
                        WindowsAppContainerProfile.create("OMP.B1A.Foreign")
                self.assertEqual(freed, [])
                self.assertEqual(deleted, [])

    def test_partial_allocation_releases_only_owned_resources(self):
        table = _FakeUserenv(pointer=0xCAFE)
        freed: list[int] = []
        deleted: list[str] = []
        failure = WindowsJobError("convert AppContainer SID to string failed")
        with (
            patch.object(windows_job, "_userenv", return_value=table),
            patch.object(windows_job, "_advapi32", return_value=object()),
            patch.object(windows_job, "_sid_string", side_effect=failure),
            patch.object(
                windows_job, "_free_sid", side_effect=lambda api, sid: freed.append(sid)
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=lambda api, name: deleted.append(name),
            ),
        ):
            with self.assertRaises(WindowsJobError) as caught:
                WindowsAppContainerProfile.create("OMP.B1A.Partial")
        self.assertIs(caught.exception, failure)
        self.assertEqual(freed, [0xCAFE])
        self.assertEqual(deleted, ["OMP.B1A.Partial"])
        self.assertIsNone(caught.exception.__cause__)

    def test_folder_failure_after_sid_conversion_releases_sid_and_profile(self):
        table = _FakeUserenv(pointer=0x1234)
        freed: list[int] = []
        deleted: list[str] = []
        with (
            patch.object(windows_job, "_userenv", return_value=table),
            patch.object(windows_job, "_advapi32", return_value=object()),
            patch.object(windows_job, "_sid_string", return_value="S-1-15-2-9"),
            patch.object(
                windows_job, "_app_container_folder",
                side_effect=WindowsJobError("profile folder missing"),
            ),
            patch.object(
                windows_job, "_free_sid", side_effect=lambda api, sid: freed.append(sid)
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=lambda api, name: deleted.append(name),
            ),
        ):
            with self.assertRaises(WindowsJobError):
                WindowsAppContainerProfile.create("OMP.B1A.Folder")
        self.assertEqual(freed, [0x1234])
        self.assertEqual(deleted, ["OMP.B1A.Folder"])

    def test_successful_close_is_idempotent_and_typed(self):
        profile = _make_profile()
        self.assertIsInstance(profile.name, str)
        self.assertIsInstance(profile.sid, str)
        self.assertIsInstance(profile.folder, Path)
        self.assertFalse(profile.closed)
        freed: list[int] = []
        deleted: list[str] = []
        with (
            patch.object(
                windows_job, "_free_sid", side_effect=lambda api, sid: freed.append(sid)
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=lambda api, name: deleted.append(name),
            ),
        ):
            profile.close()
            profile.close()
        self.assertTrue(profile.closed)
        self.assertEqual(freed, [0xB1A0])
        self.assertEqual(deleted, ["OMP.B1A.Unit"])

    def test_free_sid_failure_is_never_reported_as_closed(self):
        profile = _make_profile()
        deleted: list[str] = []
        with (
            patch.object(
                windows_job, "_free_sid",
                side_effect=WindowsJobError("free AppContainer SID failed"),
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=lambda api, name: deleted.append(name),
            ),
        ):
            with self.assertRaises(WindowsJobError):
                profile.close()
            self.assertFalse(profile.closed)
            with self.assertRaises(WindowsJobError) as caught:
                profile.close()
        self.assertIn("previously", str(caught.exception))
        self.assertEqual(deleted, [])

    def test_close_refuses_live_or_unverified_bound_tree(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                profile = _make_profile()
                stub = _BoundStub(stopped)
                profile._bind(stub)  # type: ignore[arg-type]
                freed: list[int] = []
                deleted: list[str] = []
                with (
                    patch.object(
                        windows_job, "_free_sid",
                        side_effect=lambda api, sid: freed.append(sid),
                    ),
                    patch.object(
                        windows_job, "_delete_profile",
                        side_effect=lambda api, name: deleted.append(name),
                    ),
                ):
                    if stopped:
                        profile.close()
                    else:
                        with self.assertRaises(WindowsJobError) as caught:
                            profile.close()
                        self.assertIn("live or unverified", str(caught.exception))
                self.assertEqual(freed, [0xB1A0] if stopped else [])
                self.assertEqual(deleted, ["OMP.B1A.Unit"] if stopped else [])
                self.assertEqual(profile.closed, stopped)


class AttributeListTests(unittest.TestCase):
    """B1a-2: legacy two attributes versus optional three attributes."""

    def test_legacy_path_keeps_exactly_two_attributes(self):
        api = _FakeAttributes()
        keepalive = windows_job._attribute_list(
            api, wintypes.HANDLE(11), (21, 22)
        )
        self.assertEqual(api.sized_counts, [2])
        self.assertEqual(api.initialized_count, 2)
        self.assertEqual(
            [item[0] for item in api.updated],
            [
                windows_job._PROC_THREAD_ATTRIBUTE_JOB_LIST,
                windows_job._PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ],
        )
        self.assertEqual(len(keepalive), 3)
        self.assertEqual(api.deleted, 0)

    def test_secure_path_adds_zero_capability_security_attribute(self):
        api = _FakeAttributes()
        sid = 0x5150
        keepalive = windows_job._attribute_list(
            api, wintypes.HANDLE(11), (21, 22), sid
        )
        self.assertEqual(api.sized_counts, [3])
        self.assertEqual(api.initialized_count, 3)
        self.assertEqual(
            [item[0] for item in api.updated],
            [
                windows_job._PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                windows_job._PROC_THREAD_ATTRIBUTE_JOB_LIST,
                windows_job._PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ],
        )
        capabilities = ctypes.cast(
            ctypes.byref(api.updated[0][2]),  # type: ignore[arg-type]
            ctypes.POINTER(windows_job._SECURITY_CAPABILITIES),
        ).contents
        self.assertEqual(capabilities.AppContainerSid, sid)
        self.assertEqual(capabilities.CapabilityCount, 0)
        self.assertFalse(capabilities.Capabilities)
        self.assertEqual(len(keepalive), 4)
        self.assertIs(keepalive[-1], api.updated[0][2])

    def test_each_attribute_update_failure_deletes_the_list(self):
        for index in (0, 1, 2):
            with self.subTest(index=index):
                api = _FakeAttributes(fail_index=index)
                with self.assertRaises(WindowsJobError) as caught:
                    windows_job._attribute_list(
                        api, wintypes.HANDLE(11), (21,), 0x5150
                    )
                self.assertIn("WinError 5", str(caught.exception))
                self.assertEqual(api.deleted, 1)

    def test_size_and_initialization_failures_are_not_silent(self):
        with self.subTest(stage="size"):
            api = _FakeAttributes(size_error=5)
            with self.assertRaises(WindowsJobError) as caught:
                windows_job._attribute_list(api, wintypes.HANDLE(11), (21,))
            self.assertIn("size process attribute list", str(caught.exception))
            self.assertEqual(api.deleted, 0)
        with self.subTest(stage="initialize"):
            api = _FakeAttributes(init_ok=False)
            with self.assertRaises(WindowsJobError) as caught:
                windows_job._attribute_list(api, wintypes.HANDLE(11), (21,))
            self.assertIn("initialize process attribute list", str(caught.exception))
            self.assertEqual(api.deleted, 0)


class StartValidationTests(unittest.TestCase):
    """B1a-3: invalid input is rejected before file/log/process side effects."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = _make_profile()

    def _guarded_kernel(self) -> object:
        return patch.object(
            windows_job, "_kernel32",
            side_effect=AssertionError("kernel touched before validation"),
        )

    def test_secure_start_requires_explicit_env(self):
        with self._guarded_kernel():
            with self.assertRaises(ValueError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env=None, app_container=self.profile,
                )
        self.assertIn("explicit env", str(caught.exception))

    def test_invalid_environment_mapping_is_rejected_before_side_effects(self):
        cases = [
            (["A=B"], "mapping"),
            ({1: "x"}, "text"),
            ({"A": 1}, "text"),
            ({"": "x"}, "empty"),
            ({"A=B": "x"}, "'='"),
            ({"A": "x\0y"}, "NUL"),
            ({"A\0B": "x"}, "NUL"),
        ]
        for env, message in cases:
            with self.subTest(env=env):
                with self._guarded_kernel():
                    with self.assertRaises(ValueError) as caught:
                        WindowsJobProcess.start(
                            ["worker.exe"], self.root, self.root / "log.txt",
                            temp_dir=self.root, env=env,  # type: ignore[arg-type]
                            app_container=self.profile,
                        )
                self.assertIn(message, str(caught.exception))

    def test_drive_environment_key_is_accepted(self):
        windows_job._validate_environment({"=C:": r"C:\Windows"})

    def test_secure_start_rejects_foreign_and_closed_profiles(self):
        with self._guarded_kernel():
            with self.assertRaises(ValueError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=object(),  # type: ignore[arg-type]
                )
        self.assertIn("WindowsAppContainerProfile", str(caught.exception))

        with (
            patch.object(windows_job, "_free_sid", return_value=None),
            patch.object(windows_job, "_delete_profile", return_value=None),
        ):
            self.profile.close()
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=self.profile,
                )
        self.assertIn("closed", str(caught.exception))

    def test_live_or_unverified_binding_blocks_another_start(self):
        stub = _BoundStub(False)
        self.profile._bind(stub)  # type: ignore[arg-type]
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=self.profile,
                )
        self.assertIn("live or unverified", str(caught.exception))

    def test_released_sid_blocks_start_and_close_before_native(self):
        self.profile._sid_pointer = None
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=self.profile,
                )
        self.assertIn("no longer owned", str(caught.exception))
        with (
            patch.object(
                windows_job, "_free_sid",
                side_effect=AssertionError("freed unowned SID"),
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=AssertionError("deleted profile without a SID"),
            ),
        ):
            with self.assertRaises(WindowsJobError) as caught:
                self.profile.close()
        self.assertIn("no longer owned", str(caught.exception))

    def test_failed_sid_release_blocks_start_before_native(self):
        with (
            patch.object(
                windows_job, "_free_sid",
                side_effect=WindowsJobError("free AppContainer SID failed"),
            ),
            patch.object(windows_job, "_delete_profile", return_value=None),
        ):
            with self.assertRaises(WindowsJobError):
                self.profile.close()
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=self.profile,
                )
        self.assertIn("SID release previously failed", str(caught.exception))

    def test_delete_profile_failure_blocks_reuse_before_native(self):
        profile = _make_profile()
        with (
            patch.object(windows_job, "_free_sid", return_value=None),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=WindowsJobError("delete AppContainer profile failed"),
            ),
        ):
            with self.assertRaises(WindowsJobError):
                profile.close()
        self.assertFalse(profile.closed)
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "log.txt",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=profile,
                )
        self.assertIn("no longer owned", str(caught.exception))
        with patch.object(
            windows_job, "_delete_profile",
            side_effect=AssertionError("retried deletion after losing the SID"),
        ):
            with self.assertRaises(WindowsJobError):
                profile.close()

    def test_empty_or_nul_argv_is_rejected_before_side_effects(self):
        for argv in ([], ["ok", "bad\0arg"]):
            with self.subTest(argv=argv):
                with self._guarded_kernel():
                    with self.assertRaises(ValueError):
                        WindowsJobProcess.start(
                            argv, self.root, self.root / "log.txt",
                            temp_dir=self.root, env={"A": "B"},
                            app_container=self.profile,
                        )


class StartFailureTests(unittest.TestCase):
    """B1a-6: each native setup/query/return fault is fail-closed."""

    def setUp(self) -> None:
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = _make_profile()

    def _guarded_kernel(self) -> object:
        return patch.object(
            windows_job, "_kernel32",
            side_effect=AssertionError("kernel touched before validation"),
        )

    def _start(
        self,
        api: _FakeKernel,
        *,
        env: dict[str, str] | None = None,
        verify: BaseException | None = None,
        argv: list[str] | None = None,
        cleanup_timeout: float = 10.0,
    ) -> WindowsJobProcess:
        buffer = ctypes.create_string_buffer(32)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(windows_job, "_kernel32", return_value=api))
        stack.enter_context(patch.object(windows_job, "_advapi32", return_value=object()))
        stack.enter_context(
            patch.object(
                windows_job, "_current_user_sid", return_value="S-1-5-21-1001"
            )
        )
        stack.enter_context(
            patch.object(windows_job, "_attribute_list", return_value=(buffer,))
        )
        stack.enter_context(
            patch.object(
                windows_job, "_verify_container_token",
                side_effect=verify if verify is not None else None,
                return_value=None if verify is None else None,
            )
        )
        return WindowsJobProcess.start(
            argv or ["worker.exe"],
            self.root,
            self.root / "worker.log",
            stdin_text="prompt",
            temp_dir=self.root,
            env=env or {"OMP_B1A_SECRET": "fake-secret-value"},
            app_container=self.profile,
            cleanup_timeout=cleanup_timeout,
        )

    def test_success_uses_three_attributes_and_returns_suspended_child(self):
        api = _FakeKernel()
        started = self._start(api, argv=["C:\\secret\\worker.exe"])
        self.assertEqual(started.pid, 4242)
        self.assertTrue(started.in_job)
        self.assertFalse(started.resumed)
        self.assertIs(self.profile._bound, started)
        assert api.create_args is not None
        self.assertTrue(api.create_args[4])
        flags = int(api.create_args[5])  # type: ignore[arg-type]
        self.assertTrue(flags & windows_job._CREATE_SUSPENDED)
        self.assertTrue(flags & windows_job._EXTENDED_STARTUPINFO_PRESENT)
        startup = api.create_args[8]._obj  # type: ignore[attr-defined]
        self.assertTrue(startup.lpAttributeList)
        self.assertTrue(startup.StartupInfo.dwFlags & windows_job._STARTF_USESTDHANDLES)
        self.assertTrue(startup.StartupInfo.hStdInput)
        self.assertTrue(startup.StartupInfo.hStdOutput)
        self.assertEqual(api.resumed, [])
        started.close()

    def test_createprocess_false_cleans_up_without_fallback(self):
        api = _FakeKernel(create_ok=False)
        with patch("subprocess.Popen", side_effect=AssertionError("fallback")):
            with self.assertRaises(WindowsJobError) as caught:
                self._start(api)
        self.assertIn("create contained process failed", str(caught.exception))
        self.assertIn("WinError 5", str(caught.exception))
        self.assertIn(101, api.closed)
        self.assertEqual(api.terminated, [])
        self.assertEqual(api.deleted_attributes, 1)
        self.assertEqual(list(self.root.glob(".omp-stdin-*")), [])

    def test_interrupt_at_native_return_still_releases_raw_handles(self):
        api = _FakeKernel(create_interrupt=True)
        with self.assertRaises(KeyboardInterrupt):
            self._start(api)
        self.assertIn(500, api.terminated)
        self.assertIn(500, api.closed)
        self.assertIn(501, api.closed)
        self.assertIn(101, api.closed)
        self.assertEqual(api.resumed, [])
        self.assertEqual(list(self.root.glob(".omp-stdin-*")), [])

    def test_membership_false_or_query_failure_is_fail_closed(self):
        for api, message in (
            (_FakeKernel(in_job=False), "not in the job"),
            (_FakeKernel(in_job_ok=False), "verify job membership failed"),
        ):
            with self.subTest(message=message):
                with self.assertRaises(WindowsJobError) as caught:
                    self._start(api)
                self.assertIn(message, str(caught.exception))
                self.assertIn(500, api.terminated)
                self.assertEqual(api.resumed, [])

    def test_unproven_failed_start_poisons_profile_and_blocks_reuse(self):
        api = _FakeKernel(
            in_job=False,
            terminate_ok=False,
            wait_result=258,
            active=1,
            clear_on_terminate=False,
            fail_close=(101,),
        )
        with self.assertRaises(WindowsJobError) as caught:
            self._start(api, cleanup_timeout=0.2)
        notes = " ".join(getattr(caught.exception, "__notes__", []))
        self.assertIn("terminate failed", notes)
        self.assertIn("wait failed", notes)
        self.assertIn("terminate job failed", notes)
        self.assertIn("job not empty", notes)
        self.assertIn("close job failed", notes)
        self.assertIsInstance(self.profile._failed_start, str)
        with self._guarded_kernel():
            with self.assertRaises(WindowsJobError) as caught:
                WindowsJobProcess.start(
                    ["worker.exe"], self.root, self.root / "worker.log",
                    temp_dir=self.root, env={"A": "B"},
                    app_container=self.profile,
                )
        self.assertIn("unproven", str(caught.exception))
        with (
            patch.object(
                windows_job, "_free_sid",
                side_effect=AssertionError("freed SID after unproven cleanup"),
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=AssertionError("deleted profile after unproven cleanup"),
            ),
        ):
            with self.assertRaises(WindowsJobError) as caught:
                self.profile.close()
        self.assertIn("unproven", str(caught.exception))

    def test_proven_failed_start_does_not_poison_profile(self):
        api = _FakeKernel(in_job=False)
        with self.assertRaises(WindowsJobError):
            self._start(api)
        self.assertIsNone(self.profile._failed_start)
        with (
            patch.object(windows_job, "_free_sid", return_value=None),
            patch.object(windows_job, "_delete_profile", return_value=None),
        ):
            self.profile.close()
        self.assertTrue(self.profile.closed)

    def test_interrupt_at_native_return_with_failed_cleanup_poisons_profile(self):
        api = _FakeKernel(
            create_interrupt=True, terminate_ok=False, wait_result=258,
        )
        with self.assertRaises(KeyboardInterrupt):
            self._start(api)
        self.assertIsInstance(self.profile._failed_start, str)

    def test_start_failure_cleanup_does_not_mask_primary_error(self):
        api = _FakeKernel(
            in_job=False, terminate_ok=False, wait_result=258,
        )
        with self.assertRaises(WindowsJobError) as caught:
            self._start(api, cleanup_timeout=0.2)
        self.assertIn("not in the job", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        notes = " ".join(getattr(caught.exception, "__notes__", []))
        self.assertIn("terminate failed", notes)
        self.assertIn("wait failed", notes)

    def test_token_verification_failure_keeps_stage_and_hides_inputs(self):
        api = _FakeKernel()
        failure = WindowsJobError(
            "contained process is not at low mandatory integrity"
        )
        with self.assertRaises(WindowsJobError) as caught:
            self._start(
                api,
                env={"OMP_B1A_SECRET": "fake-secret-value"},
                verify=failure,
                argv=["C:\\secret\\worker.exe"],
            )
        self.assertIs(caught.exception, failure)
        messages = " ".join(str(item) for item in _walk_causes(caught.exception))
        self.assertNotIn("fake-secret-value", messages)
        self.assertNotIn("C:\\secret\\worker.exe", messages)
        self.assertIn(500, api.terminated)
        self.assertEqual(api.resumed, [])

    def test_unexpected_verify_exception_propagates_after_cleanup(self):
        api = _FakeKernel()
        failure = RuntimeError("injected verification fault")
        with self.assertRaises(RuntimeError) as caught:
            self._start(api, verify=failure)
        self.assertIs(caught.exception, failure)
        self.assertIn(500, api.terminated)
        self.assertEqual(api.resumed, [])


class ProcessLifecycleFaultTests(unittest.TestCase):
    """B1a-6/B1a-7: stop, timeout, release, and cause-chain behaviour."""

    def test_resume_failure_does_not_mark_process_resumed(self):
        api = _FakeKernel(resume_ok=False)
        process = _make_process(api)
        with self.assertRaises(WindowsJobError) as caught:
            process.resume()
        self.assertIn("resume contained process", str(caught.exception))
        self.assertFalse(process.resumed)
        self.assertIn(103, api.resumed)

    def test_terminate_query_failure_keeps_guard(self):
        api = _FakeKernel(query_ok=False)
        process = _make_process(api, cleanup_timeout=0.2)
        with self.assertRaises(WindowsJobError) as caught:
            process.terminate_tree(0.2)
        self.assertIn("query job accounting failed", str(caught.exception))
        self.assertIn("WinError 5", str(caught.exception))
        self.assertFalse(process.tree_stopped)

    def test_cleanup_deadline_is_bounded(self):
        api = _FakeKernel(active=1, clear_on_terminate=False)
        process = _make_process(api, cleanup_timeout=0.2)
        started = time.monotonic()
        with self.assertRaises(WindowsJobCleanupTimeout):
            process.terminate_tree(0.2)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(process.tree_stopped)

    def test_wait_wraps_cleanup_failure_with_sanitized_cause(self):
        api = _FakeKernel(active=1)
        process = _make_process(api)
        process.returncode = 0

        def failing_terminate(timeout=None):
            raise WindowsJobError("terminate job object failed (WinError 5)")

        with patch.object(process, "terminate_tree", side_effect=failing_terminate):
            with self.assertRaises(WindowsJobError) as caught:
                process.wait(0)
        self.assertIn("descendant", str(caught.exception))
        self.assertIn("cleanup failed", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, WindowsJobError)
        self.assertIn("WinError 5", str(caught.exception.__cause__))
        self.assertNotIn("worker.exe", str(caught.exception))
        self.assertFalse(process.tree_stopped)

    def test_close_handle_failure_is_sticky_and_keeps_profile_binding(self):
        api = _FakeKernel(fail_close=(101,))
        process = _make_process(api, app_container=None)
        profile = _make_profile()
        process._app_container = profile
        profile._bind(process)  # type: ignore[arg-type]
        with self.assertRaises(WindowsJobError) as caught:
            process.close()
        self.assertIn("job", str(caught.exception))
        self.assertTrue(process._close_failed)
        with self.assertRaises(WindowsJobError) as caught:
            process.close()
        self.assertIn("previously failed", str(caught.exception))
        self.assertIs(profile._bound, process)
        with (
            patch.object(
                windows_job, "_free_sid",
                side_effect=AssertionError("profile closed on unknown tree"),
            ),
            patch.object(
                windows_job, "_delete_profile",
                side_effect=AssertionError("profile deleted on unknown tree"),
            ),
        ):
            with self.assertRaises(WindowsJobError):
                profile.close()

    def test_close_handle_interrupt_keeps_type_after_best_effort_cleanup(self):
        for interrupt in (KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(interrupt=type(interrupt).__name__):
                api = _FakeKernel(
                    fail_close=(101,),
                    interrupt_close=103,
                    close_interrupt=interrupt,
                )
                profile = _make_profile()
                process = _make_process(api, app_container=profile)
                profile._bind(process)  # type: ignore[arg-type]
                process._stopped = True
                with patch.object(
                    windows_job, "_unlink_with_retry", return_value=False
                ):
                    with self.assertRaises(type(interrupt)) as caught:
                        process.close()
                self.assertIs(caught.exception, interrupt)
                # Remaining handles were still attempted once, never twice.
                self.assertEqual(api.closed, [103, 102, 101])
                self.assertTrue(process._close_failed)
                self.assertIs(profile._bound, process)
                self.assertIs(process._app_container, profile)
                notes = " ".join(getattr(interrupt, "__notes__", []))
                self.assertIn("thread", notes)
                self.assertIn("job", notes)
                self.assertIn("stdin", notes)
                self.assertNotIn("worker.exe", notes)
                self.assertNotIn("OMP_B1A_SECRET", notes)
                self.assertIsNone(interrupt.__cause__)
                self.assertIsNone(interrupt.__context__)
                with self.assertRaises(WindowsJobError) as again:
                    process.close()
                self.assertIn("previously failed", str(again.exception))

    def test_interrupt_during_close_is_sticky_and_keeps_binding(self):
        profile = _make_profile()
        process = _make_process(app_container=profile)
        profile._bind(process)  # type: ignore[arg-type]
        process._stopped = True
        with patch.object(
            windows_job, "_unlink_with_retry", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                process.close()
        self.assertTrue(process._close_failed)
        self.assertIs(profile._bound, process)
        with self.assertRaises(WindowsJobError) as caught:
            process.close()
        self.assertIn("previously failed", str(caught.exception))

    def test_unlink_and_handle_failures_are_reported_together(self):
        api = _FakeKernel(fail_close=(101,))
        profile = _make_profile()
        process = _make_process(api, app_container=profile)
        profile._bind(process)  # type: ignore[arg-type]
        process._stopped = True
        with patch.object(windows_job, "_unlink_with_retry", return_value=False):
            with self.assertRaises(WindowsJobError) as caught:
                process.close()
        message = str(caught.exception)
        self.assertIn("job", message)
        self.assertIn("stdin", message)
        self.assertTrue(process._close_failed)
        self.assertIs(profile._bound, process)

    def test_close_after_proved_stop_releases_profile_binding(self):
        api = _FakeKernel()
        profile = _make_profile()
        process = _make_process(api, app_container=profile)
        profile._bind(process)  # type: ignore[arg-type]
        process._stopped = True
        process.close()
        self.assertIsNone(profile._bound)
        self.assertIsNone(process._app_container)
        with (
            patch.object(windows_job, "_free_sid", return_value=None),
            patch.object(windows_job, "_delete_profile", return_value=None),
        ):
            profile.close()
        self.assertTrue(profile.closed)

    def test_default_cleanup_timeout_is_still_ten_seconds(self):
        import inspect

        signature = inspect.signature(WindowsJobProcess.start)
        default = signature.parameters["cleanup_timeout"].default
        self.assertEqual(default, 10.0)


# --- Native evidence -------------------------------------------------------

SECURE_WORKER = r'''
import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
canary = Path(sys.argv[2])
marker = Path(sys.argv[3])
report = {}
report["version"] = sys.version.split()[0]
report["allowed"] = os.environ.get("OMP_B1A_ALLOWED", "<missing>")
report["secret"] = "yes" if "OMP_B1A_SECRET" in os.environ else "no"
report["stdin"] = sys.stdin.read().strip()
for label, action in (("read", canary.read_bytes), ("append", None)):
    try:
        if action is None:
            with canary.open("ab") as handle:
                handle.write(b"x")
        else:
            action()
        report["canary_" + label] = "allowed"
    except OSError as exc:
        report["canary_" + label] = "denied:" + type(exc).__name__
try:
    (workspace / "worker-write.txt").write_text("ok", encoding="utf-8")
    report["workspace_write"] = "ok"
except OSError as exc:
    report["workspace_write"] = "denied:" + type(exc).__name__
probe = os.environ.get("OMP_B1A_PROBE_HANDLE", "")
if probe:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flags = wintypes.DWORD()
    try:
        inherited = kernel32.GetHandleInformation(
            wintypes.HANDLE(int(probe)), ctypes.byref(flags)
        )
    except OSError:
        inherited = 0
    report["foreign_handle"] = "inherited" if inherited else "denied"
marker.write_text("\n".join(f"{k}={v}" for k, v in report.items()), encoding="utf-8")
'''

CONTROL_WORKER = r'''
from pathlib import Path
import sys

canary = Path(sys.argv[1])
marker = Path(sys.argv[2])
report = {}
try:
    report["read"] = canary.read_text(encoding="utf-8")
    with canary.open("a", encoding="utf-8") as handle:
        handle.write("+")
    report["append"] = "ok"
except OSError as exc:
    report["error"] = type(exc).__name__ + ":" + str(getattr(exc, "winerror", ""))
marker.write_text("\n".join(f"{k}={v}" for k, v in report.items()), encoding="utf-8")
'''

DESCENDANT_WORKER = r'''
import os
from pathlib import Path
import sys
import time

workspace = Path(sys.argv[1])
(workspace / "descendant.pid").write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
'''

ROOT_EARLY_EXIT_WORKER = r'''
import subprocess
from pathlib import Path
import sys
import time

workspace = Path(sys.argv[1])
script = Path(sys.argv[2])
subprocess.Popen([sys.executable, "-I", "-B", "-S", str(script), str(workspace)])
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    if (workspace / "descendant.pid").exists():
        (workspace / "root-exit.marker").write_text("exiting", encoding="utf-8")
        raise SystemExit(0)
    time.sleep(0.01)
raise SystemExit("descendant readiness timeout")
'''


def _user_sid() -> str:
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return result.stdout.strip().strip('"').split('","')[1]


def _icacls(*args: str) -> None:
    result = subprocess.run(
        ["icacls", *args], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError("icacls failed: " + " ".join(args[:1]))


def _prepare_runtime(exe: Path, target: Path) -> Path:
    """Copy a minimal owned CPython runtime for the AppContainer Worker."""
    target.mkdir(parents=True, exist_ok=True)
    source = exe.parent
    lib = source / "Lib" if (source / "Lib").is_dir() else source.parent / "Lib"
    shutil.copy2(exe, target / "python.exe")
    for dll in source.glob("*.dll"):
        shutil.copy2(dll, target / dll.name)
    shutil.copytree(
        lib,
        target / "Lib",
        ignore=shutil.ignore_patterns(
            "site-packages", "test", "idlelib", "tkinter", "turtledemo",
            "ensurepip", "venv", "lib2to3", "__pycache__",
        ),
    )
    dlls = source / "DLLs"
    if dlls.is_dir():
        shutil.copytree(
            dlls, target / "DLLs",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return target / "python.exe"


def _token_information(pid: int) -> dict[str, object]:
    """Read the child token independently of the production helpers."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL

    process = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not process:
        raise OSError("cannot open contained process")
    token = wintypes.HANDLE()
    try:
        if not advapi.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
            raise OSError("cannot open contained token")

        def blob(information_class: int) -> ctypes.Array[ctypes.c_char]:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(
                token, information_class, None, 0, ctypes.byref(size)
            )
            data = ctypes.create_string_buffer(size.value)
            returned = wintypes.DWORD()
            if not advapi.GetTokenInformation(
                token, information_class, data, size, ctypes.byref(returned)
            ):
                raise OSError("token query failed")
            return data

        def sid_text(data: ctypes.Array[ctypes.c_char]) -> str:
            sid = ctypes.cast(
                data, ctypes.POINTER(ctypes.c_void_p)
            ).contents.value
            text = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(
                ctypes.c_void_p(sid), ctypes.byref(text)
            ):
                raise OSError("sid conversion failed")
            try:
                return text.value or ""
            finally:
                kernel32.LocalFree(text)

        uint = ctypes.cast(blob(TOKEN_IS_APP_CONTAINER),
                           ctypes.POINTER(wintypes.DWORD)).contents.value
        capabilities = ctypes.cast(
            blob(TOKEN_CAPABILITIES), ctypes.POINTER(wintypes.DWORD)
        ).contents.value
        try:
            appcontainer_sid = sid_text(blob(TOKEN_APPCONTAINER_SID))
        except OSError:
            appcontainer_sid = ""  # regular tokens have no AppContainer SID
        return {
            "is_container": int(uint),
            "appcontainer_sid": appcontainer_sid,
            "integrity": sid_text(blob(TOKEN_INTEGRITY_LEVEL)),
            "capabilities": int(capabilities),
            "user": sid_text(blob(TOKEN_USER)),
            "in_any_job": _in_any_job(pid),
        }
    finally:
        if token:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)


def _in_any_job(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise OSError("cannot open contained process")
    try:
        inside = wintypes.BOOL()
        if not kernel32.IsProcessInJob(process, None, ctypes.byref(inside)):
            raise OSError("job membership query failed")
        return bool(inside.value)
    finally:
        kernel32.CloseHandle(process)


def _process_alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _stop_job_independently(process: WindowsJobProcess) -> str | None:
    """Terminate and drain a job without the production stop path.

    Returns ``None`` when the job became empty, otherwise a description of the
    remaining state for the fixture to report.
    """
    api = process._api
    job = process._job  # type: ignore[attr-defined]
    if not _handle_value(job):
        return "job handle unavailable"
    try:
        api.TerminateJobObject(job, 1)
    except BaseException as exc:
        return f"TerminateJobObject raised {type(exc).__name__}"
    deadline = time.monotonic() + max(process.cleanup_timeout, 0.01)
    while True:
        try:
            active = windows_job._active_processes(api, job)
        except BaseException as exc:
            return f"job query failed: {exc}"
        if active == 0:
            return None
        if time.monotonic() >= deadline:
            return f"{active} process(es) still active"
        time.sleep(0.01)


def _profile_sid(name: str) -> int:
    """Derive an existing profile SID with raw WinAPI, or return 0."""
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    sid = ctypes.c_void_p()
    result = int(userenv.DeriveAppContainerSidFromAppContainerName(
        name, ctypes.byref(sid)
    ))
    return int(sid.value or 0) if result == 0 else 0


def _profile_absent(name: str, timeout: float = 0.0) -> bool:
    """Probe profile absence with raw WinAPI, independent of production.

    Profile deletion can complete shortly after ``DeleteAppContainerProfile``
    returns, so *timeout* allows bounded re-checks.
    """
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.FreeSid.argtypes = [ctypes.c_void_p]
    advapi.FreeSid.restype = ctypes.c_void_p
    deadline = time.monotonic() + timeout
    while True:
        sid = ctypes.c_void_p()
        result = int(userenv.CreateAppContainerProfile(
            name, name, name, None, 0, ctypes.byref(sid)
        ))
        if result == 0:
            if sid.value:
                advapi.FreeSid(ctypes.c_void_p(int(sid.value)))
            userenv.DeleteAppContainerProfile(name)
            return True
        if (result & 0xFFFF) != windows_job._ERROR_ALREADY_EXISTS:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _delete_native_profile(name: str) -> None:
    """Remove a leaked profile with raw WinAPI, independent of production."""
    sid = _profile_sid(name)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile(name)
    if sid:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.FreeSid.argtypes = [ctypes.c_void_p]
        advapi.FreeSid.restype = ctypes.c_void_p
        advapi.FreeSid(ctypes.c_void_p(sid))


@unittest.skipUnless(os.name == "nt", "Windows AppContainer tests")
class AppContainerNativeTests(unittest.TestCase):
    """Real Worker evidence under an owned AppContainer and Job Object."""

    @classmethod
    def setUpClass(cls) -> None:
        integrity = str(_token_information(os.getpid())["integrity"])
        if integrity != MEDIUM_INTEGRITY_SID:
            raise RuntimeError(
                "native AppContainer evidence needs a medium integrity "
                f"interpreter, got {integrity}; the low-labeled local 3.14 "
                "runtime must be run from a Medium-labeled copy"
            )
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        cls.root = Path(cls.temp.name) / "repo"
        cls.root.mkdir()
        cls.runtime = _prepare_runtime(Path(sys.executable), cls.root / "runtime")
        cls.user_sid = _user_sid()
        _icacls(
            str(cls.root), "/inheritance:r",
            "/grant:r", f"*{cls.user_sid}:(F)",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def setUp(self) -> None:
        self.scratch = Path(tempfile.mkdtemp(dir=self.root, prefix="case-"))
        self.addCleanup(self._cleanup)
        self.processes: list[WindowsJobProcess] = []
        self.profile: WindowsAppContainerProfile | None = None
        self.workspace = self.scratch / "workspace"
        self.workspace.mkdir()
        self.profile = WindowsAppContainerProfile.create(
            "OMP.B1A." + secrets.token_hex(6)
        )
        _icacls(
            str(self.scratch), "/inheritance:r",
            "/grant:r", f"*{self.user_sid}:(OI)(CI)(F)",
            f"*{self.profile.sid}:(OI)(CI)(F)",
        )
        _icacls(
            str(self.root), "/grant:r",
            f"*{self.profile.sid}:(OI)(CI)(RX)",
        )
        _icacls(
            str(self.workspace), "/grant:r",
            f"*{self.user_sid}:(OI)(CI)(F)",
            f"*{self.profile.sid}:(OI)(CI)(F)",
            "/T", "/C",
        )
        _icacls(
            str(self.runtime.parent), "/grant:r",
            f"*{self.user_sid}:(F)", f"*{self.profile.sid}:(RX)",
            "/T", "/C",
        )
        _icacls(
            str(self.workspace), "/setintegritylevel", "(OI)(CI)Low",
            "/T", "/C",
        )

    def _cleanup(self) -> None:
        errors: list[str] = []
        for process in reversed(self.processes):
            if not process.tree_stopped:
                try:
                    process.terminate_tree(5)
                except BaseException as exc:
                    errors.append(f"terminate_tree: {exc}")
                    remaining = _stop_job_independently(process)
                    if remaining is not None:
                        errors.append(f"fallback terminate: {remaining}")
                if not process.tree_stopped:
                    errors.append("tree not stopped")
            try:
                process.close()
            except BaseException as exc:
                errors.append(f"close process: {exc}")
        if self.profile is not None:
            try:
                self.profile.close()
            except BaseException as exc:
                errors.append(f"close profile: {exc}")
        try:
            shutil.rmtree(self.scratch)
        except OSError as exc:
            errors.append(f"remove scratch: {exc}")
        if errors:
            raise AssertionError(
                "native fixture cleanup failed: " + "; ".join(errors)
            )

    def _env(self, **extra: str) -> dict[str, str]:
        env = {
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "windir": os.environ.get("windir", r"C:\Windows"),
            "PATH": r"C:\Windows\System32;C:\Windows",
            "TEMP": str(self.workspace),
            "TMP": str(self.workspace),
            "LOCALAPPDATA": str(self.profile.folder),
            "OMP_B1A_ALLOWED": "visible",
            "=C:": r"C:\Windows\System32",
        }
        env.update(extra)
        return env

    def _wait_file(self, path: Path, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.stat().st_size:
                return
            time.sleep(0.02)
        raise AssertionError(f"timed out waiting for {path.name}")

    def _read_report(self, path: Path) -> dict[str, str]:
        return dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

    def test_interrupted_allocation_releases_real_profile(self):
        name = "OMP.B1A.INT." + secrets.token_hex(6)
        self.addCleanup(_delete_native_profile, name)
        real_userenv = windows_job._userenv()
        real_advapi = windows_job._advapi32()
        real_free_sid = windows_job._free_sid
        real_delete_profile = windows_job._delete_profile
        created: list[int] = []
        freed: list[int] = []
        deleted: list[str] = []

        class _InterruptingUserenv:
            def CreateAppContainerProfile(self, *args: object) -> int:
                result = int(real_userenv.CreateAppContainerProfile(*args))
                created.append(result)
                if result == 0:
                    raise KeyboardInterrupt
                return result

            def DeleteAppContainerProfile(self, profile: str) -> int:
                return int(real_userenv.DeleteAppContainerProfile(profile))

            def GetAppContainerFolderPath(self, sid: str, out: object) -> int:
                return int(real_userenv.GetAppContainerFolderPath(sid, out))

        def free(api: object, sid: int) -> None:
            freed.append(int(sid))
            real_free_sid(api, sid)  # type: ignore[arg-type]

        def delete(api: object, profile: str) -> None:
            deleted.append(profile)
            real_delete_profile(api, profile)  # type: ignore[arg-type]

        with (
            patch.object(
                windows_job, "_userenv", return_value=_InterruptingUserenv()
            ),
            patch.object(windows_job, "_advapi32", return_value=real_advapi),
            patch.object(windows_job, "_free_sid", side_effect=free),
            patch.object(windows_job, "_delete_profile", side_effect=delete),
        ):
            with self.assertRaises(KeyboardInterrupt):
                WindowsAppContainerProfile.create(name)
        self.assertEqual(created, [0])
        self.assertEqual(len(freed), 1)
        self.assertEqual(deleted, [name])
        self.assertTrue(_profile_absent(name, 5))

    def test_real_collision_never_deletes_existing_profile(self):
        name = "OMP.B1A.COL." + secrets.token_hex(6)
        owner = WindowsAppContainerProfile.create(name)
        self.addCleanup(owner.close)
        real_free_sid = windows_job._free_sid
        real_delete_profile = windows_job._delete_profile
        freed: list[int] = []
        deleted: list[str] = []

        def free(api: object, sid: int) -> None:
            freed.append(int(sid))
            real_free_sid(api, sid)  # type: ignore[arg-type]

        def delete(api: object, profile: str) -> None:
            deleted.append(profile)
            real_delete_profile(api, profile)  # type: ignore[arg-type]

        with (
            patch.object(windows_job, "_free_sid", side_effect=free),
            patch.object(windows_job, "_delete_profile", side_effect=delete),
        ):
            with self.assertRaises(WindowsJobError) as caught:
                WindowsAppContainerProfile.create(name)
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(freed, [])
        self.assertEqual(deleted, [])
        self.assertFalse(_profile_absent(name))
        owner.close()

    def test_secure_worker_token_identity_and_owned_isolation(self):
        script = self.workspace / "secure_worker.py"
        script.write_text(SECURE_WORKER, encoding="utf-8")
        canary = self.scratch / "canary.txt"
        canary.write_text("protected", encoding="utf-8")
        digest = _sha256(canary)
        _icacls(
            str(canary), "/inheritance:r",
            "/grant:r", f"*{self.user_sid}:(F)",
        )
        probe_fd = os.open(canary, os.O_RDONLY)
        probe_handle = int(__import__("msvcrt").get_osfhandle(probe_fd))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetHandleInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ]
        kernel32.SetHandleInformation.restype = wintypes.BOOL
        self.assertTrue(
            kernel32.SetHandleInformation(
                wintypes.HANDLE(probe_handle),
                HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT,
            )
        )
        marker = self.workspace / "marker.txt"
        fake_secret = "B1A-FAKE-SECRET-" + secrets.token_hex(4)
        try:
            with patch.dict(os.environ, {"OMP_B1A_SECRET": fake_secret}):
                process = WindowsJobProcess.start(
                    [
                        str(self.runtime), "-I", "-B", "-S", str(script),
                        str(self.workspace), str(canary), str(marker),
                    ],
                    self.workspace,
                    self.workspace / "worker.log",
                    stdin_text="hello-stdin",
                    temp_dir=self.workspace,
                    env=self._env(OMP_B1A_PROBE_HANDLE=str(probe_handle)),
                    app_container=self.profile,
                )
        finally:
            os.close(probe_fd)
        self.processes.append(process)
        self.assertFalse(process.resumed)
        self.assertTrue(process.in_job)
        self.assertEqual(process.active_processes, 1)
        self.assertFalse(marker.exists())
        token = _token_information(process.pid)
        self.assertEqual(token["is_container"], 1)
        self.assertEqual(token["appcontainer_sid"], self.profile.sid)
        self.assertEqual(token["integrity"], LOW_INTEGRITY_SID)
        self.assertEqual(token["capabilities"], 0)
        self.assertEqual(token["user"], self.user_sid)
        self.assertTrue(token["in_any_job"])

        process.resume()
        self._wait_file(marker)
        report = self._read_report(marker)
        self.assertEqual(report["version"], sys.version.split()[0])
        self.assertEqual(report["allowed"], "visible")
        self.assertEqual(report["secret"], "no")
        self.assertEqual(report["stdin"], "hello-stdin")
        self.assertEqual(report["workspace_write"], "ok")
        self.assertTrue(report["canary_read"].startswith("denied:"))
        self.assertTrue(report["canary_append"].startswith("denied:"))
        self.assertEqual(report["foreign_handle"], "denied")
        self.assertEqual(_sha256(canary), digest)
        self.assertEqual(canary.read_text(encoding="utf-8"), "protected")

        process.terminate_tree(5)
        self.assertTrue(process.tree_stopped)
        process.close()
        self.profile.close()
        self.assertTrue(self.profile.closed)
        self.profile.close()
        self.assertNotIn(fake_secret, marker.read_text(encoding="utf-8"))

    def test_same_user_control_reads_and_writes_the_same_canary(self):
        script = self.workspace / "control_worker.py"
        script.write_text(CONTROL_WORKER, encoding="utf-8")
        canary = self.scratch / "control-canary.txt"
        canary.write_text("protected", encoding="utf-8")
        digest = _sha256(canary)
        _icacls(
            str(canary), "/inheritance:r",
            "/grant:r", f"*{self.user_sid}:(F)",
        )
        marker = self.workspace / "control-marker.txt"
        process = WindowsJobProcess.start(
            [sys.executable, "-I", "-B", "-S", str(script), str(canary), str(marker)],
            self.workspace,
            self.workspace / "control.log",
            temp_dir=self.workspace,
        )
        self.processes.append(process)
        process.resume()
        self._wait_file(marker)
        report = self._read_report(marker)
        self.assertEqual(report["read"], "protected")
        self.assertEqual(report["append"], "ok")
        self.assertNotEqual(_sha256(canary), digest)
        canary.write_text("protected", encoding="utf-8")
        process.terminate_tree(5)
        process.close()

    def test_root_early_exit_with_live_descendant_is_failure(self):
        root_script = self.workspace / "root.py"
        root_script.write_text(ROOT_EARLY_EXIT_WORKER, encoding="utf-8")
        descendant_script = self.workspace / "descendant.py"
        descendant_script.write_text(DESCENDANT_WORKER, encoding="utf-8")
        process = WindowsJobProcess.start(
            [
                str(self.runtime), "-I", "-B", "-S", str(root_script),
                str(self.workspace), str(descendant_script),
            ],
            self.workspace,
            self.workspace / "root.log",
            temp_dir=self.workspace,
            env=self._env(),
            app_container=self.profile,
        )
        self.processes.append(process)
        process.resume()
        self._wait_file(self.workspace / "root-exit.marker")
        with self.assertRaises(WindowsJobError) as caught:
            process.wait(10)
        self.assertIn("descendant", str(caught.exception))
        self.assertTrue(process.tree_stopped)
        descendant = int(
            (self.workspace / "descendant.pid").read_text(encoding="utf-8")
        )
        self.assertFalse(_process_alive(descendant))
        process.close()
        self.profile.close()

    def test_profile_close_refuses_live_tree_then_allows_sequential_reuse(self):
        script = self.workspace / "sleep_worker.py"
        script.write_text(
            "import time\ntime.sleep(60)\n", encoding="utf-8"
        )
        argv = [
            str(self.runtime), "-I", "-B", "-S", str(script),
        ]
        process = WindowsJobProcess.start(
            argv,
            self.workspace,
            self.workspace / "sleep.log",
            temp_dir=self.workspace,
            env=self._env(),
            app_container=self.profile,
        )
        self.processes.append(process)
        process.resume()
        with self.assertRaises(WindowsJobError) as caught:
            self.profile.close()
        self.assertIn("live or unverified", str(caught.exception))
        process.terminate_tree(5)
        self.assertTrue(process.tree_stopped)
        process.close()

        second = WindowsJobProcess.start(
            argv,
            self.workspace,
            self.workspace / "sleep-2.log",
            temp_dir=self.workspace,
            env=self._env(),
            app_container=self.profile,
        )
        self.processes.append(second)
        second.resume()
        second.terminate_tree(5)
        second.close()
        self.profile.close()
        self.assertTrue(self.profile.closed)


class NativeFixtureCleanupTests(unittest.TestCase):
    """B1a-R5: native fixture cleanup reports every failure and never hides it."""

    def test_cleanup_collects_all_failures_and_raises(self):
        scratch = Path(tempfile.mkdtemp(dir=SCRATCH_ROOT, prefix="cleanup-"))
        profile = _make_profile()
        process = _make_process(
            _FakeKernel(active=1, clear_on_terminate=False),
            app_container=profile,
            cleanup_timeout=0.2,
        )
        case = AppContainerNativeTests(methodName="runTest")
        case.scratch = scratch
        case.profile = profile
        case.processes = [process]
        with (
            patch.object(
                process, "terminate_tree",
                side_effect=WindowsJobError("terminate failed"),
            ),
            patch.object(
                process, "close",
                side_effect=WindowsJobError("close failed"),
            ),
            patch.object(
                profile, "close",
                side_effect=WindowsJobError("profile failed"),
            ),
        ):
            with self.assertRaises(AssertionError) as caught:
                case._cleanup()
        message = str(caught.exception)
        self.assertIn("terminate failed", message)
        self.assertIn("1 process(es) still active", message)
        self.assertIn("close failed", message)
        self.assertIn("profile failed", message)
        self.assertFalse(scratch.exists())


if __name__ == "__main__":
    unittest.main()
