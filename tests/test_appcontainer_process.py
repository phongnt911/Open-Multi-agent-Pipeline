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
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

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
HIGH_INTEGRITY_SID = "S-1-16-12288"
PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_USER = 1
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_IS_APP_CONTAINER = 29
TOKEN_CAPABILITIES = 30
TOKEN_APPCONTAINER_SID = 31
SE_PRIVILEGE_ENABLED = 0x00000002
SE_GROUP_INTEGRITY = 0x00000020
ERROR_NOT_ALL_ASSIGNED = 1300
MAXIMUM_ALLOWED = 0x02000000
CREATE_NO_WINDOW = 0x08000000
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
STILL_ACTIVE = 259
DESKTOP_ALL_ACCESS = 0x01FF
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
NATIVE_CONTROLLER_TIMEOUT = 600.0
NATIVE_CONTROLLER_CLEANUP_TIMEOUT = 10.0
HANDLE_FLAG_INHERIT = 0x00000001
FAILED = 0xFFFFFFFF
_IN_NATIVE_CONTROLLER = False
_UNPROVEN_CONTROLLER_STATE: str | None = None


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
    buf = ctypes.create_string_buffer(16)
    read_count = wintypes.DWORD()
    try:
        ok = bool(
            kernel32.ReadFile(
                wintypes.HANDLE(int(probe)),
                buf,
                9,
                ctypes.byref(read_count),
                None,
            )
        )
        inherited = ok and buf.raw[: int(read_count.value)] == b"protected"
    except OSError:
        inherited = False
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


def _desktop_dacl() -> str:
    """Return the SDDL DACL of the caller thread desktop."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
    ]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    if not desktop:
        raise OSError(ctypes.get_last_error(), "cannot open thread desktop")
    sd = ctypes.c_void_p()
    rc = int(
        advapi.GetSecurityInfo(
            desktop, 7, 4, None, None, None, None, ctypes.byref(sd)
        )
    )
    if rc != 0:
        raise OSError(rc, "cannot read thread desktop DACL")
    text = wintypes.LPWSTR()
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            sd, 1, 4, ctypes.byref(text), None
        ):
            raise OSError(
                ctypes.get_last_error(), "cannot format thread desktop DACL"
            )
        try:
            return text.value or ""
        finally:
            if text:
                kernel32.LocalFree(text)
    finally:
        if sd:
            kernel32.LocalFree(sd)


def _current_desktop_allows_appcontainer() -> bool:
    """Return True when the current thread desktop already grants AC access."""
    return "(A;;CCDCLCSWRPWPDTLOSDRCWDWO;;;AC)" in _desktop_dacl()


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _OwnedDesktop:
    """An exclusive WinSta0 desktop created and owned by the native fixture."""

    def __init__(self, name: str, handle: int, user32: object) -> None:
        self.name = name
        self.handle = handle
        self._user32 = user32
        self.closed = False
        self._close_attempted = False

    @classmethod
    def create(cls, user_sid: str) -> "_OwnedDesktop":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        user32.CreateDesktopW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ]
        user32.CreateDesktopW.restype = wintypes.HANDLE
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        name = "omp-b1a-desk-" + secrets.token_hex(6)
        sddl = (
            f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{user_sid})"
            "(A;;CCDCLCSWRPWPDTLOSDRCWDWO;;;AC)S:(ML;;NW;;;LW)"
        )
        sd = ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(sd), None
        ):
            raise OSError(
                ctypes.get_last_error(),
                "cannot build owned desktop security descriptor",
            )
        handle_val = 0
        try:
            sa = _SECURITY_ATTRIBUTES(
                ctypes.sizeof(_SECURITY_ATTRIBUTES), sd, False
            )
            ctypes.set_last_error(0)
            raw = user32.CreateDesktopW(
                name, None, None, 0, DESKTOP_ALL_ACCESS, ctypes.byref(sa)
            )
            handle_val = _handle_value(raw)
            if not handle_val:
                raise OSError(
                    ctypes.get_last_error(), "cannot create owned desktop"
                )
            return cls(name, handle_val, user32)
        except BaseException as primary:
            if handle_val:
                try:
                    if not user32.CloseDesktop(wintypes.HANDLE(handle_val)):
                        primary.add_note(
                            "CloseDesktop returned FALSE during create cleanup"
                        )
                except BaseException as cleanup_exc:
                    primary.add_note(
                        f"CloseDesktop raised {type(cleanup_exc).__name__}"
                    )
            raise
        finally:
            if sd:
                kernel32.LocalFree(sd)

    def close(self) -> None:
        if self.closed:
            return
        if self._close_attempted:
            raise RuntimeError(
                "CloseDesktop previously failed; refusing to double-close"
            )
        self._close_attempted = True
        ctypes.set_last_error(0)
        if not self._user32.CloseDesktop(wintypes.HANDLE(self.handle)):  # type: ignore[attr-defined]
            raise RuntimeError(
                "CloseDesktop returned FALSE "
                f"(winerror={ctypes.get_last_error()})"
            )
        self.handle = 0
        self.closed = True


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


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _enable_own_privileges(names: tuple[str, ...]) -> dict[str, bool]:
    """Try to enable *names* on the caller token; report what really stuck."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_LUID),
    ]
    advapi.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(_TOKEN_PRIVILEGES),
        wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.AdjustTokenPrivileges.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise OSError(ctypes.get_last_error(), "cannot open the caller token")
    enabled: dict[str, bool] = {}
    try:
        for name in names:
            luid = _LUID()
            if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                enabled[name] = False
                continue
            privileges = _TOKEN_PRIVILEGES()
            privileges.PrivilegeCount = 1
            privileges.Privileges[0].Luid = luid
            privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
            ctypes.set_last_error(0)
            if not advapi.AdjustTokenPrivileges(
                token, False, ctypes.byref(privileges), 0, None, None
            ):
                enabled[name] = False
                continue
            enabled[name] = ctypes.get_last_error() != ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(token)
    return enabled


def _controller_kernel32() -> object:
    """Bind Win32 process/handle primitives for the native controller harness."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, argtypes, restype in (
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD),
        ("TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        (
            "GetExitCodeProcess",
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
    ):
        fn = getattr(kernel32, name, None)
        if isinstance(fn, ctypes._CFuncPtr):
            fn.argtypes = argtypes
            fn.restype = restype
    return kernel32


def _ensure_controller_state_clean() -> None:
    if _UNPROVEN_CONTROLLER_STATE is not None:
        raise RuntimeError(
            "refusing to launch a replacement native controller after "
            f"unproven controller cleanup: {_UNPROVEN_CONTROLLER_STATE}"
        )


def _close_handle_checked(
    kernel32: object,
    handle: wintypes.HANDLE | int,
    name: str = "handle",
) -> None:
    """Close *handle* via kernel32.CloseHandle, verifying BOOL and non-zero."""
    val = _handle_value(handle)
    if not val:
        return
    ctypes.set_last_error(0)
    try:
        ok = bool(kernel32.CloseHandle(handle))  # type: ignore[attr-defined]
    except BaseException:
        raise
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(
            err, f"CloseHandle({name}={val}) returned FALSE (winerror={err})"
        )
    if hasattr(handle, "value"):
        try:
            handle.value = 0
        except Exception:
            pass


def _release_controller_handles(
    kernel32: object | None,
    info: _PROCESS_INFORMATION,
    *,
    terminate: bool,
    timeout: float = NATIVE_CONTROLLER_CLEANUP_TIMEOUT,
) -> None:
    """Close owned controller handles and desktop, verifying every WinAPI step."""
    global _UNPROVEN_CONTROLLER_STATE
    deadline = time.monotonic() + max(float(timeout), 0.01)
    problems: list[str] = []
    interrupt_exc: BaseException | None = None
    owned_desktop: _OwnedDesktop | None = getattr(info, "_owned_desktop", None)

    if kernel32 is None and (
        _handle_value(info.hThread) or _handle_value(info.hProcess)
    ):
        try:
            kernel32 = _controller_kernel32()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupt_exc = exc
            problems.append(
                f"controller kernel32 init failed ({type(exc).__name__})"
            )

    if _handle_value(info.hThread):
        if getattr(info, "_thread_close_attempted", False):
            problems.append(
                "CloseHandle(thread) previously failed; refusing to double-close"
            )
        elif kernel32 is None:
            problems.append(
                "thread handle retained because kernel32 is unavailable"
            )
        else:
            info._thread_close_attempted = True  # type: ignore[attr-defined]
            try:
                ctypes.set_last_error(0)
                closed_thread = bool(kernel32.CloseHandle(info.hThread))  # type: ignore[attr-defined]
            except BaseException as exc:
                if (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and interrupt_exc is None
                ):
                    interrupt_exc = exc
                problems.append(
                    f"CloseHandle(thread) raised {type(exc).__name__}"
                )
            else:
                if closed_thread:
                    info.hThread = wintypes.HANDLE(0)
                else:
                    problems.append(
                        "CloseHandle(thread) returned FALSE "
                        f"(winerror={ctypes.get_last_error()})"
                    )

    process_stopped = not terminate
    if _handle_value(info.hProcess):
        if kernel32 is None:
            process_stopped = False
            problems.append(
                "process handle retained because kernel32 is unavailable"
            )
        elif terminate:
            term_ok = False
            try:
                ctypes.set_last_error(0)
                term_ok = bool(kernel32.TerminateProcess(info.hProcess, 1))  # type: ignore[attr-defined]
            except BaseException as exc:
                if (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and interrupt_exc is None
                ):
                    interrupt_exc = exc
                problems.append(
                    f"TerminateProcess raised {type(exc).__name__}"
                )
            else:
                if not term_ok:
                    problems.append(
                        "TerminateProcess returned FALSE "
                        f"(winerror={ctypes.get_last_error()})"
                    )
            remaining_ms = max(
                1, int(round(max(0.0, deadline - time.monotonic()) * 1000))
            )
            wait_rc = WAIT_FAILED
            try:
                ctypes.set_last_error(0)
                wait_rc = int(
                    kernel32.WaitForSingleObject(info.hProcess, remaining_ms)  # type: ignore[attr-defined]
                )
            except BaseException as exc:
                if (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and interrupt_exc is None
                ):
                    interrupt_exc = exc
                problems.append(
                    f"WaitForSingleObject(process) raised {type(exc).__name__}"
                )
            else:
                if wait_rc == WAIT_TIMEOUT:
                    problems.append(
                        f"WaitForSingleObject(process) timed out ({WAIT_TIMEOUT})"
                    )
                elif wait_rc != WAIT_OBJECT_0:
                    problems.append(
                        f"WaitForSingleObject(process) returned {wait_rc} "
                        f"(winerror={ctypes.get_last_error()})"
                    )
            process_stopped = term_ok and (wait_rc == WAIT_OBJECT_0)

        if not process_stopped:
            if kernel32 is not None:
                problems.append(
                    "process handle retained because termination was not proven"
                )
        elif getattr(info, "_process_close_attempted", False):
            problems.append(
                "CloseHandle(process) previously failed; refusing to double-close"
            )
        else:
            info._process_close_attempted = True  # type: ignore[attr-defined]
            try:
                ctypes.set_last_error(0)
                closed_process = bool(kernel32.CloseHandle(info.hProcess))  # type: ignore[attr-defined]
            except BaseException as exc:
                if (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and interrupt_exc is None
                ):
                    interrupt_exc = exc
                problems.append(
                    f"CloseHandle(process) raised {type(exc).__name__}"
                )
            else:
                if closed_process:
                    info.hProcess = wintypes.HANDLE(0)
                else:
                    problems.append(
                        "CloseHandle(process) returned FALSE "
                        f"(winerror={ctypes.get_last_error()})"
                    )

    if owned_desktop is not None and not owned_desktop.closed:
        if _handle_value(info.hProcess) and not process_stopped:
            problems.append(
                "owned desktop retained because controller termination was not proven"
            )
        else:
            try:
                owned_desktop.close()
                if hasattr(owned_desktop, "closed"):
                    try:
                        owned_desktop.closed = True
                    except Exception:
                        pass
            except BaseException as exc:
                if (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and interrupt_exc is None
                ):
                    interrupt_exc = exc
                problems.append(f"owned desktop close failed: {exc}")

    if problems or interrupt_exc is not None:
        summary = (
            "; ".join(problems) if problems else type(interrupt_exc).__name__
        )
        _UNPROVEN_CONTROLLER_STATE = summary
        if interrupt_exc is not None:
            if problems:
                interrupt_exc.add_note(
                    "controller cleanup failed: " + "; ".join(problems)
                )
            raise interrupt_exc
        raise RuntimeError("controller cleanup failed: " + "; ".join(problems))


def _cleanup_controller_on_exception(
    kernel32: object | None,
    info: _PROCESS_INFORMATION,
    primary: BaseException,
    *,
    terminate: bool = True,
    timeout: float = NATIVE_CONTROLLER_CLEANUP_TIMEOUT,
) -> None:
    """Run controller cleanup when *primary* is active, preserving cause/type."""
    try:
        _release_controller_handles(
            kernel32, info, terminate=terminate, timeout=timeout
        )
    except BaseException as cleanup_exc:
        if isinstance(cleanup_exc, (KeyboardInterrupt, SystemExit)) and not (
            isinstance(primary, (KeyboardInterrupt, SystemExit))
        ):
            cleanup_exc.add_note(
                f"prior controller error: {type(primary).__name__}: {primary}"
            )
            raise cleanup_exc from None
        primary.add_note(f"controller cleanup failed: {cleanup_exc}")


def _spawn_with_token(
    token: wintypes.HANDLE,
    argv: list[str],
    cwd: Path,
    *,
    desktop: str | None = None,
    out_info: _PROCESS_INFORMATION | None = None,
) -> _PROCESS_INFORMATION:
    """Start *argv* suspended with *token*, or fail closed listing attempts."""
    _ensure_controller_state_clean()
    kernel32 = _controller_kernel32()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    advapi.CreateProcessAsUserW.restype = wintypes.BOOL
    advapi.CreateProcessWithTokenW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    advapi.CreateProcessWithTokenW.restype = wintypes.BOOL
    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(_STARTUPINFOW)
    if desktop:
        startup.lpDesktop = desktop
    flags = CREATE_NO_WINDOW | windows_job._CREATE_SUSPENDED
    enabled = _enable_own_privileges((
        "SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege",
    ))
    attempts: list[str] = []

    def attempt_as_user() -> _PROCESS_INFORMATION | None:
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        info = out_info if out_info is not None else _PROCESS_INFORMATION()
        info.hProcess = wintypes.HANDLE(0)
        info.hThread = wintypes.HANDLE(0)
        info.dwProcessId = 0
        info.dwThreadId = 0
        ctypes.set_last_error(0)
        try:
            started = bool(
                advapi.CreateProcessAsUserW(
                    token, None, command_line, None, None, False, flags, None,
                    str(cwd), ctypes.byref(startup), ctypes.byref(info),
                )
            )
            if started:
                return info
        except BaseException as primary:
            if _handle_value(info.hProcess) or _handle_value(info.hThread):
                _cleanup_controller_on_exception(
                    kernel32, info, primary, terminate=True
                )
            raise
        if _handle_value(info.hProcess) or _handle_value(info.hThread):
            _release_controller_handles(kernel32, info, terminate=True)
        attempts.append(f"CreateProcessAsUserW error {ctypes.get_last_error()}")
        return None

    def attempt_with_token() -> _PROCESS_INFORMATION | None:
        _ensure_controller_state_clean()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        info = out_info if out_info is not None else _PROCESS_INFORMATION()
        info.hProcess = wintypes.HANDLE(0)
        info.hThread = wintypes.HANDLE(0)
        info.dwProcessId = 0
        info.dwThreadId = 0
        ctypes.set_last_error(0)
        try:
            started = bool(
                advapi.CreateProcessWithTokenW(
                    token, 0, None, command_line, flags, None, str(cwd),
                    ctypes.byref(startup), ctypes.byref(info),
                )
            )
            if started:
                return info
        except BaseException as primary:
            if _handle_value(info.hProcess) or _handle_value(info.hThread):
                _cleanup_controller_on_exception(
                    kernel32, info, primary, terminate=True
                )
            raise
        if _handle_value(info.hProcess) or _handle_value(info.hThread):
            _release_controller_handles(kernel32, info, terminate=True)
        attempts.append(f"CreateProcessWithTokenW error {ctypes.get_last_error()}")
        return None

    info = attempt_as_user()
    if info is not None:
        return info
    enabled.update(_enable_own_privileges(("SeImpersonatePrivilege",)))
    info = attempt_with_token()
    if info is not None:
        return info
    raise RuntimeError(
        "cannot start a Medium native controller with a lowered token: "
        + "; ".join(attempts)
        + f"; privileges={enabled}. A High integrity runner needs "
        "CreateProcessAsUserW on its lowered duplicate token or "
        "SeImpersonatePrivilege (CreateProcessWithTokenW); refusing to reuse "
        "the High token."
    )


def _start_with_medium_token(
    argv: list[str],
    cwd: Path,
    *,
    out_info: _PROCESS_INFORMATION | None = None,
) -> _PROCESS_INFORMATION:
    """Start *argv* with an owned duplicate of the caller token at Medium."""
    global _UNPROVEN_CONTROLLER_STATE
    _ensure_controller_state_clean()
    kernel32 = _controller_kernel32()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]  # type: ignore[attr-defined]
    kernel32.LocalFree.restype = wintypes.HLOCAL  # type: ignore[attr-defined]
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.DuplicateTokenEx.restype = wintypes.BOOL
    advapi.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.SetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    advapi.SetTokenInformation.restype = wintypes.BOOL

    own = wintypes.HANDLE(0)
    primary = wintypes.HANDLE(0)
    sid = ctypes.c_void_p(0)
    owned_desktop: _OwnedDesktop | None = None
    info = out_info if out_info is not None else _PROCESS_INFORMATION()
    info.hProcess = wintypes.HANDLE(0)
    info.hThread = wintypes.HANDLE(0)
    info.dwProcessId = 0
    info.dwThreadId = 0
    info._cleaned_up = False  # type: ignore[attr-defined]

    if not advapi.OpenProcessToken(
        kernel32.GetCurrentProcess(),  # type: ignore[attr-defined]
        TOKEN_DUPLICATE | TOKEN_QUERY,
        ctypes.byref(own),
    ):
        raise OSError(ctypes.get_last_error(), "cannot open the caller token")
    try:
        try:
            if not advapi.DuplicateTokenEx(
                own, MAXIMUM_ALLOWED, None, SECURITY_IMPERSONATION, TOKEN_PRIMARY,
                ctypes.byref(primary),
            ):
                raise OSError(
                    ctypes.get_last_error(), "cannot duplicate the caller token"
                )
        finally:
            _close_handle_checked(kernel32, own, "own_token")
    except BaseException as own_exc:
        if _handle_value(primary):
            try:
                _close_handle_checked(kernel32, primary, "primary_token")
            except BaseException as prim_exc:
                _UNPROVEN_CONTROLLER_STATE = f"primary token close failed: {prim_exc}"
                if (
                    isinstance(prim_exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(own_exc, (KeyboardInterrupt, SystemExit))
                ):
                    prim_exc.add_note(f"own token error: {own_exc}")
                    raise prim_exc from None
                own_exc.add_note(f"primary token close failed: {prim_exc}")
        raise

    try:
        try:
            if not advapi.ConvertStringSidToSidW(
                MEDIUM_INTEGRITY_SID, ctypes.byref(sid)
            ):
                raise OSError(
                    ctypes.get_last_error(), "cannot build the medium integrity SID"
                )
            label = _TOKEN_MANDATORY_LABEL()
            label.Label.Sid = sid
            label.Label.Attributes = SE_GROUP_INTEGRITY
            if not advapi.SetTokenInformation(
                primary, TOKEN_INTEGRITY_LEVEL, ctypes.byref(label),
                ctypes.sizeof(label),
            ):
                raise OSError(
                    ctypes.get_last_error(),
                    "cannot lower the controller token to medium integrity",
                )
        finally:
            if sid and getattr(sid, "value", None):
                kernel32.LocalFree(sid)  # type: ignore[attr-defined]
                sid = ctypes.c_void_p(0)

        owned_desktop = _OwnedDesktop.create(_user_sid())
        info._owned_desktop = owned_desktop  # type: ignore[attr-defined]

        spawned = _spawn_with_token(
            primary, argv, cwd, desktop=owned_desktop.name, out_info=info
        )
        spawned._owned_desktop = owned_desktop  # type: ignore[attr-defined]
    except BaseException as body_exc:
        if _handle_value(info.hProcess) or _handle_value(info.hThread):
            info._cleaned_up = True  # type: ignore[attr-defined]
            _cleanup_controller_on_exception(
                kernel32, info, body_exc, terminate=True
            )
        elif owned_desktop is not None and not owned_desktop.closed:
            try:
                owned_desktop.close()
            except BaseException as desk_exc:
                _UNPROVEN_CONTROLLER_STATE = (
                    f"owned desktop close failed: {desk_exc}"
                )
                body_exc.add_note(f"controller cleanup failed: {desk_exc}")
        try:
            _close_handle_checked(kernel32, primary, "primary_token")
        except BaseException as token_close_exc:
            _UNPROVEN_CONTROLLER_STATE = (
                f"primary token close failed: {token_close_exc}"
            )
            if (
                isinstance(token_close_exc, (KeyboardInterrupt, SystemExit))
                and not isinstance(body_exc, (KeyboardInterrupt, SystemExit))
            ):
                token_close_exc.add_note(f"prior controller error: {body_exc}")
                raise token_close_exc from None
            body_exc.add_note(f"primary token close failed: {token_close_exc}")
        raise

    try:
        _close_handle_checked(kernel32, primary, "primary_token")
    except BaseException as token_close_exc:
        _UNPROVEN_CONTROLLER_STATE = (
            type(token_close_exc).__name__
            if isinstance(token_close_exc, (KeyboardInterrupt, SystemExit))
            else f"primary token close failed: {token_close_exc}"
        )
        info._cleaned_up = True  # type: ignore[attr-defined]
        _cleanup_controller_on_exception(
            kernel32, info, token_close_exc, terminate=True
        )
        raise

    return spawned


def _wait_for_exit(
    handle: wintypes.HANDLE,
    timeout: float,
    *,
    kernel32: object | None = None,
) -> tuple[int, bool]:
    """Wait for *handle* up to *timeout*; report ``(exit_code, timed_out)``."""
    api = kernel32 if kernel32 is not None else _controller_kernel32()
    wait_ms = max(1, int(round(max(0.0, float(timeout)) * 1000)))
    ctypes.set_last_error(0)
    wait = int(api.WaitForSingleObject(handle, wait_ms))  # type: ignore[attr-defined]
    if wait == WAIT_TIMEOUT:
        return STILL_ACTIVE, True
    if wait != WAIT_OBJECT_0:
        raise OSError(
            ctypes.get_last_error(),
            f"WaitForSingleObject failed for controller with status {wait}",
        )
    code = wintypes.DWORD()
    ctypes.set_last_error(0)
    if not api.GetExitCodeProcess(handle, ctypes.byref(code)):  # type: ignore[attr-defined]
        raise OSError(
            ctypes.get_last_error(), "cannot read the controller exit code"
        )
    if int(code.value) == STILL_ACTIVE:
        raise RuntimeError(
            "controller reported STILL_ACTIVE after WaitForSingleObject signaled"
        )
    return int(code.value), False


class _EvidenceResult(unittest.TextTestResult):
    """Remember the exact test ids that started and passed."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.ran: list[str] = []
        self.passed: list[str] = []

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self.ran.append(test.id())

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed.append(test.id())


def _native_test_names() -> list[str]:
    return sorted(
        unittest.defaultTestLoader.getTestCaseNames(AppContainerNativeTests)
    )


def _short_test_names(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(name).rsplit(".", 1)[-1] for name in values]


def _run_native_controller(report_path: Path, log_path: Path) -> int:
    """Child entry: prove this interpreter is Medium, run native tests, report."""
    global _IN_NATIVE_CONTROLLER
    identity = _token_information(os.getpid())
    integrity = str(identity["integrity"])
    if integrity != MEDIUM_INTEGRITY_SID:
        raise RuntimeError(
            f"native controller needs medium integrity, got {integrity}"
        )
    AppContainerNativeTests.recorded_evidence = {}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous = _IN_NATIVE_CONTROLLER
    _IN_NATIVE_CONTROLLER = True
    try:
        with log_path.open("w", encoding="utf-8") as stream:
            result = unittest.TextTestRunner(
                stream=stream, verbosity=2, resultclass=_EvidenceResult
            ).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    AppContainerNativeTests
                )
            )
    finally:
        _IN_NATIVE_CONTROLLER = previous
    report = {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "integrity": integrity,
        "user": str(identity["user"]),
        "is_container": int(identity["is_container"]),
        "tests": list(result.ran),
        "passed": list(result.passed),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": [test.id() for test, _ in result.skipped],
        "evidence": dict(AppContainerNativeTests.recorded_evidence),
        "success": result.wasSuccessful(),
    }
    report_path.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if result.wasSuccessful() else 1


def _as_int(value: object, default: int = -1) -> int:
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _is_valid_pid(value: object, *, forbidden: set[int] | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return False
    return forbidden is None or value not in forbidden


def _is_nonempty_str(value: object, *, prefix: str = "") -> bool:
    return isinstance(value, str) and bool(value) and value.startswith(prefix)


def _validate_child_evidence(
    evidence: object, expected: list[str], controller_pid: int, caller_user: str
) -> list[str]:
    """Verify per-test native measurements reported by the Medium controller."""
    if not isinstance(evidence, dict):
        return ["missing per-test evidence mapping"]
    problems: list[str] = []
    missing = [name for name in expected if name not in evidence]
    if missing:
        problems.append(f"missing evidence for {missing}")
    extra = sorted(set(evidence) - set(expected))
    if extra:
        problems.append(f"unexpected evidence keys {extra}")
    for name in expected:
        item = evidence.get(name)
        if not isinstance(item, dict) or not item:
            problems.append(f"empty or non-dict evidence for {name}")

    interrupted = evidence.get("test_interrupted_allocation_releases_real_profile")
    if isinstance(interrupted, dict):
        int_name = interrupted.get("profile_name")
        if not _is_nonempty_str(int_name, prefix="OMP.B1A.INT.") or len(str(int_name)) <= len("OMP.B1A.INT."):
            problems.append(f"invalid interrupted profile_name {int_name!r}")
        created = interrupted.get("created")
        if (
            not isinstance(created, list)
            or len(created) != 1
            or isinstance(created[0], bool)
            or created[0] != 0
        ):
            problems.append(f"invalid interrupted created list {created!r}")
        if _as_int(interrupted.get("freed_count")) != 1:
            problems.append(
                f"invalid interrupted freed_count {interrupted.get('freed_count')!r}"
            )
        if interrupted.get("deleted") != [int_name]:
            problems.append(
                f"invalid interrupted deleted list {interrupted.get('deleted')!r}"
            )
        if interrupted.get("profile_absent") is not True:
            problems.append("interrupted profile_absent not proven")

    collision = evidence.get("test_real_collision_never_deletes_existing_profile")
    if isinstance(collision, dict):
        col_name = collision.get("profile_name")
        if not _is_nonempty_str(col_name, prefix="OMP.B1A.COL.") or len(str(col_name)) <= len("OMP.B1A.COL."):
            problems.append(f"invalid collision profile_name {col_name!r}")
        if not isinstance(collision.get("freed"), list) or collision.get("freed") != []:
            problems.append(f"collision freed list not empty: {collision.get('freed')!r}")
        if not isinstance(collision.get("deleted"), list) or collision.get("deleted") != []:
            problems.append(
                f"collision deleted list not empty: {collision.get('deleted')!r}"
            )
        if collision.get("owner_closed") is not True:
            problems.append("collision owner_closed not proven")

    secure = evidence.get("test_secure_worker_token_identity_and_owned_isolation")
    if isinstance(secure, dict):
        worker_pid = secure.get("worker_pid")
        if not _is_valid_pid(worker_pid, forbidden={controller_pid}):
            problems.append(f"invalid worker pid {worker_pid!r}")
        profile_sid = secure.get("profile_sid")
        if not _is_nonempty_str(profile_sid, prefix="S-1-15-2-"):
            problems.append(f"invalid worker profile_sid {profile_sid!r}")
        if secure.get("user_sid") != caller_user:
            problems.append(f"worker user_sid {secure.get('user_sid')!r} != caller")
        token = secure.get("token")
        if not isinstance(token, dict):
            problems.append("missing secure worker token")
        else:
            if _as_int(token.get("is_container")) != 1:
                problems.append(f"worker is_container={token.get('is_container')!r}")
            if token.get("appcontainer_sid") != profile_sid:
                problems.append("worker AppContainer SID mismatch")
            if token.get("integrity") != LOW_INTEGRITY_SID:
                problems.append(f"worker integrity {token.get('integrity')!r}")
            if _as_int(token.get("capabilities")) != 0:
                problems.append(f"worker capabilities {token.get('capabilities')!r}")
            if token.get("user") != caller_user:
                problems.append(f"worker user {token.get('user')!r}")
            if token.get("in_any_job") is not True:
                problems.append("worker not in job")
        sec_report = secure.get("report")
        if not isinstance(sec_report, dict):
            problems.append("missing secure worker report")
        else:
            if not _is_nonempty_str(sec_report.get("version")):
                problems.append("missing secure worker report version")
            if sec_report.get("allowed") != "visible":
                problems.append(
                    f"secure worker allowed={sec_report.get('allowed')!r}"
                )
            if sec_report.get("secret") != "no":
                problems.append(
                    f"secure worker secret={sec_report.get('secret')!r}"
                )
            if sec_report.get("stdin") != "hello-stdin":
                problems.append(
                    f"secure worker stdin={sec_report.get('stdin')!r}"
                )
            if sec_report.get("workspace_write") != "ok":
                problems.append(
                    "secure worker workspace_write="
                    f"{sec_report.get('workspace_write')!r}"
                )
            if not _is_nonempty_str(sec_report.get("canary_read"), prefix="denied:"):
                problems.append(
                    f"secure worker canary_read not denied: {sec_report.get('canary_read')!r}"
                )
            if not _is_nonempty_str(sec_report.get("canary_append"), prefix="denied:"):
                problems.append(
                    "secure worker canary_append not denied: "
                    f"{sec_report.get('canary_append')!r}"
                )
            if sec_report.get("foreign_handle") != "denied":
                problems.append(
                    "secure worker foreign_handle not denied: "
                    f"{sec_report.get('foreign_handle')!r}"
                )
        before = secure.get("canary_sha256_before")
        after = secure.get("canary_sha256_after")
        if not _is_sha256_hex(before) or not _is_sha256_hex(after):
            problems.append("secure worker canary digest is not valid SHA-256")
        elif before != after:
            problems.append("secure worker canary digest changed")
        if (
            secure.get("tree_stopped") is not True
            or secure.get("profile_closed") is not True
        ):
            problems.append("secure worker cleanup flags not proven")

    control = evidence.get("test_same_user_control_reads_and_writes_the_same_canary")
    if isinstance(control, dict):
        control_pid = control.get("control_pid")
        if not _is_valid_pid(control_pid, forbidden={controller_pid}):
            problems.append(f"invalid same-user control pid {control_pid!r}")
        ctrl_report = control.get("report")
        if not isinstance(ctrl_report, dict):
            problems.append("missing same-user control report")
        else:
            if ctrl_report.get("read") != "protected":
                problems.append(
                    f"same-user control read={ctrl_report.get('read')!r}"
                )
            if ctrl_report.get("append") != "ok":
                problems.append(
                    f"same-user control append={ctrl_report.get('append')!r}"
                )
            if "error" in ctrl_report:
                problems.append(
                    f"same-user control reported error {ctrl_report.get('error')!r}"
                )
        ctrl_before = control.get("canary_sha256_before")
        ctrl_after = control.get("canary_sha256_after")
        if not _is_sha256_hex(ctrl_before) or not _is_sha256_hex(ctrl_after):
            problems.append("same-user control canary digest is not valid SHA-256")
        elif ctrl_before == ctrl_after:
            problems.append("same-user control did not modify canary")
        if control.get("tree_stopped") is not True:
            problems.append("same-user control tree_stopped not proven")

    early_exit = evidence.get("test_root_early_exit_with_live_descendant_is_failure")
    if isinstance(early_exit, dict):
        root_pid = early_exit.get("root_pid")
        descendant_pid = early_exit.get("descendant_pid")
        if not _is_valid_pid(root_pid, forbidden={controller_pid}):
            problems.append(f"invalid early-exit root_pid {root_pid!r}")
        forbidden_desc = {controller_pid}
        if isinstance(root_pid, int):
            forbidden_desc.add(root_pid)
        if not _is_valid_pid(descendant_pid, forbidden=forbidden_desc):
            problems.append(
                f"invalid early-exit descendant_pid {descendant_pid!r}"
            )
        if early_exit.get("descendant_alive") is not False:
            problems.append("early-exit descendant_alive is not False")
        if early_exit.get("tree_stopped") is not True:
            problems.append("early-exit tree_stopped not proven")
        if early_exit.get("profile_closed") is not True:
            problems.append("early-exit profile_closed not proven")

    reuse = evidence.get(
        "test_profile_close_refuses_live_tree_then_allows_sequential_reuse"
    )
    if isinstance(reuse, dict):
        first_pid = reuse.get("first_pid")
        second_pid = reuse.get("second_pid")
        if not _is_valid_pid(first_pid, forbidden={controller_pid}):
            problems.append(f"invalid reuse first_pid {first_pid!r}")
        forbidden_second = {controller_pid}
        if isinstance(first_pid, int):
            forbidden_second.add(first_pid)
        if not _is_valid_pid(second_pid, forbidden=forbidden_second):
            problems.append(f"invalid reuse second_pid {second_pid!r}")
        if reuse.get("first_stopped") is not True:
            problems.append("reuse first_stopped not proven")
        if reuse.get("second_stopped") is not True:
            problems.append("reuse second_stopped not proven")
        if reuse.get("profile_closed") is not True:
            problems.append("reuse profile_closed not proven")

    return problems


def _delegate_native_controller() -> dict[str, object]:
    """Run the native class in an owned Medium child and verify its evidence."""
    _ensure_controller_state_clean()
    expected = _native_test_names()
    version = sys.version.split()[0]
    caller_user = _user_sid()
    with tempfile.TemporaryDirectory(prefix="omp-b1a-controller-") as directory:
        _icacls(
            str(directory), "/inheritance:r",
            "/grant:r", f"*{caller_user}:(OI)(CI)(F)",
        )
        report_path = Path(directory) / "native-report.json"
        log_path = Path(directory) / "native-controller.log"
        info = _PROCESS_INFORMATION()
        kernel32: object | None = None
        pid = 0
        completed = False
        body_error: BaseException | None = None
        try:
            info = _start_with_medium_token(
                [
                    sys.executable, "-B", "-m", "tests.test_appcontainer_process",
                    "--native-controller", str(report_path), str(log_path),
                ],
                REPO_ROOT,
                out_info=info,
            )
            kernel32 = _controller_kernel32()
            pid = int(info.dwProcessId)
            try:
                token = _token_information(pid)
            except OSError as exc:
                token = {
                    "integrity": f"unreadable: {exc}", "user": "",
                    "is_container": -1,
                }
            pre_problems: list[str] = []
            if str(token.get("integrity")) != MEDIUM_INTEGRITY_SID:
                pre_problems.append(f"token integrity {token.get('integrity')}")
            if _as_int(token.get("is_container")) != 0:
                pre_problems.append("token is an AppContainer token")
            if str(token.get("user")) != caller_user:
                pre_problems.append(
                    f"token user {token.get('user')} is not the caller"
                )
            if pre_problems:
                raise RuntimeError(
                    "medium native controller failed pre-resume token check: "
                    + "; ".join(pre_problems)
                )
            if kernel32.ResumeThread(info.hThread) == FAILED:  # type: ignore[attr-defined]
                raise OSError(
                    ctypes.get_last_error(),
                    "cannot resume the medium native controller",
                )
            exit_code, timed_out = _wait_for_exit(
                info.hProcess, NATIVE_CONTROLLER_TIMEOUT, kernel32=kernel32
            )
            completed = not timed_out
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            if not getattr(info, "_cleaned_up", False) and (
                _handle_value(info.hProcess)
                or _handle_value(info.hThread)
                or (
                    getattr(info, "_owned_desktop", None) is not None
                    and not getattr(info._owned_desktop, "closed", False)
                )
            ):
                info._cleaned_up = True  # type: ignore[attr-defined]
                if body_error is None:
                    _release_controller_handles(
                        kernel32, info, terminate=not completed
                    )
                else:
                    _cleanup_controller_on_exception(
                        kernel32, info, body_error, terminate=not completed
                    )
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        report: dict[str, object] = {}
        if report_path.exists():
            try:
                loaded = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    report = loaded
            except ValueError as exc:
                report = {"error": f"invalid JSON report: {exc}"}

        problems: list[str] = []
        if timed_out:
            problems.append(
                f"timed out after {NATIVE_CONTROLLER_TIMEOUT}s and was terminated"
            )
        if exit_code != 0:
            problems.append(f"exit code {exit_code}")
        if _as_int(report.get("pid"), 0) != pid:
            problems.append(f"report pid {report.get('pid')} is not {pid}")
        if report.get("python") != version:
            problems.append(f"report python {report.get('python')} is not {version}")
        if str(report.get("integrity")) != MEDIUM_INTEGRITY_SID:
            problems.append(
                f"self-reported integrity {report.get('integrity')}"
            )
        if str(report.get("user")) != caller_user:
            problems.append(
                f"self-reported user {report.get('user')} is not the caller"
            )
        if _as_int(report.get("is_container")) != 0:
            problems.append(
                f"self-reported is_container {report.get('is_container')}"
            )
        if _as_int(report.get("tests_run")) != len(expected):
            problems.append(
                f"ran {report.get('tests_run')} tests, expected {len(expected)}"
            )
        ran = _short_test_names(report.get("tests"))
        if sorted(ran) != expected:
            problems.append(f"test set {sorted(ran)} is not {expected}")
        passed = _short_test_names(report.get("passed"))
        if sorted(passed) != expected:
            problems.append(f"passed set {sorted(passed)} is not {expected}")
        if report.get("skipped"):
            problems.append(f"skipped {report.get('skipped')}")
        if _as_int(report.get("failures"), -1) != 0:
            problems.append(f"failures={report.get('failures')}")
        if _as_int(report.get("errors"), -1) != 0:
            problems.append(f"errors={report.get('errors')}")
        if report.get("success") is not True:
            problems.append(
                f"reported failure: {report.get('error', 'see controller log')}"
            )
        problems.extend(
            _validate_child_evidence(
                report.get("evidence"), expected, pid, caller_user
            )
        )
        if not log.splitlines() or log.splitlines()[-1].strip() != "OK":
            problems.append("controller log does not end with OK")
        if problems:
            raise RuntimeError(
                "medium native controller did not produce valid evidence: "
                + "; ".join(problems)
                + "\n--- controller log ---\n" + log[-4000:]
                + "\n--- controller report ---\n"
                + json.dumps(report, indent=2)
            )
        return {
            "pid": pid,
            "python": version,
            "integrity": str(token["integrity"]),
            "user": str(token["user"]),
            "is_container": int(token["is_container"]),
            "tests": expected,
            "passed": sorted(passed),
            "evidence": dict(report["evidence"]),  # type: ignore[arg-type]
            "log": log,
        }


@unittest.skipUnless(os.name == "nt", "Windows AppContainer tests")
class AppContainerNativeTests(unittest.TestCase):
    """Real Worker evidence under an owned AppContainer and Job Object.

    A Medium interpreter on an AppContainer-capable desktop runs the cases
    in-process.  A High integrity interpreter (such as the mandatory CI runner)
    or a Medium interpreter on a restricted desktop owns a duplicate of its
    token lowered to Medium, creates an exclusive owned desktop, starts this
    module suspended as a Medium child on that owned desktop, verifies the
    child OS token before resuming it, and only accepts the child's evidence
    when the token, identity, exact test set, zero skips, and per-test Worker
    measurements are verified; the High token is never reused and the caller's
    existing desktop DACL is never modified.
    """

    temp: tempfile.TemporaryDirectory[str] | None = None
    delegated: bool = False
    child_verified: bool = False
    child_evidence: dict[str, object] = {}
    recorded_evidence: dict[str, dict[str, object]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = None
        cls.delegated = False
        cls.child_verified = False
        cls.child_evidence = {}
        cls.recorded_evidence = {}
        integrity = str(_token_information(os.getpid())["integrity"])
        if integrity == HIGH_INTEGRITY_SID:
            cls.child_evidence = _delegate_native_controller()
            cls.delegated = True
            cls.child_verified = True
            return
        if integrity != MEDIUM_INTEGRITY_SID:
            raise RuntimeError(
                "native AppContainer evidence needs a medium integrity "
                f"interpreter, got {integrity}; High CI runners delegate to an "
                "owned Medium controller, and the low-labeled local 3.14 "
                "runtime must be run from a Medium-labeled copy"
            )
        if not _IN_NATIVE_CONTROLLER and not _current_desktop_allows_appcontainer():
            cls.child_evidence = _delegate_native_controller()
            cls.delegated = True
            cls.child_verified = True
            return
        if not _current_desktop_allows_appcontainer():
            raise RuntimeError(
                "native AppContainer controller desktop does not grant "
                "AppContainer access"
            )
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        cls.user_sid = _user_sid()
        _icacls(
            str(cls.temp.name), "/inheritance:r",
            "/grant:r", f"*{cls.user_sid}:(OI)(CI)(F)",
        )
        cls.root = Path(cls.temp.name) / "repo"
        cls.root.mkdir()
        cls.runtime = _prepare_runtime(Path(sys.executable), cls.root / "runtime")
        _icacls(
            str(cls.root), "/inheritance:r",
            "/grant:r", f"*{cls.user_sid}:(F)",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.temp is not None:
            cls.temp.cleanup()

    def _delegated(self) -> bool:
        cls = type(self)
        if not cls.delegated:
            return False
        self.assertTrue(
            cls.child_verified,
            "Medium controller evidence was not verified",
        )
        self.assertEqual(
            cls.child_evidence.get("integrity"), MEDIUM_INTEGRITY_SID
        )
        self.assertEqual(cls.child_evidence.get("is_container"), 0)
        self.assertIn(
            self._testMethodName, cls.child_evidence.get("passed", [])  # type: ignore[arg-type]
        )
        evidence_map = cls.child_evidence.get("evidence")
        self.assertIsInstance(evidence_map, dict)
        assert isinstance(evidence_map, dict)
        self.assertIn(self._testMethodName, evidence_map)
        self.assertTrue(evidence_map[self._testMethodName])
        self.assertEqual(
            _validate_child_evidence(
                evidence_map,
                _native_test_names(),
                _as_int(cls.child_evidence.get("pid"), 0),
                str(cls.child_evidence.get("user", "")),
            ),
            [],
        )
        return True

    def setUp(self) -> None:
        if type(self).delegated:
            return
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
        if self._delegated():
            return
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
        type(self).recorded_evidence[self._testMethodName] = {
            "profile_name": name,
            "created": list(created),
            "freed_count": len(freed),
            "deleted": list(deleted),
            "profile_absent": True,
        }

    def test_real_collision_never_deletes_existing_profile(self):
        if self._delegated():
            return
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
        type(self).recorded_evidence[self._testMethodName] = {
            "profile_name": name,
            "freed": list(freed),
            "deleted": list(deleted),
            "owner_closed": owner.closed,
        }

    def test_secure_worker_token_identity_and_owned_isolation(self):
        if self._delegated():
            return
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
        after_digest = _sha256(canary)
        self.assertEqual(after_digest, digest)
        self.assertEqual(canary.read_text(encoding="utf-8"), "protected")

        process.terminate_tree(5)
        self.assertTrue(process.tree_stopped)
        process.close()
        self.profile.close()
        self.assertTrue(self.profile.closed)
        self.profile.close()
        self.assertNotIn(fake_secret, marker.read_text(encoding="utf-8"))
        type(self).recorded_evidence[self._testMethodName] = {
            "worker_pid": process.pid,
            "profile_sid": self.profile.sid,
            "user_sid": self.user_sid,
            "token": dict(token),
            "report": dict(report),
            "canary_sha256_before": digest,
            "canary_sha256_after": after_digest,
            "tree_stopped": process.tree_stopped,
            "profile_closed": self.profile.closed,
        }

    def test_same_user_control_reads_and_writes_the_same_canary(self):
        if self._delegated():
            return
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
        modified_digest = _sha256(canary)
        self.assertNotEqual(modified_digest, digest)
        canary.write_text("protected", encoding="utf-8")
        process.terminate_tree(5)
        process.close()
        type(self).recorded_evidence[self._testMethodName] = {
            "control_pid": process.pid,
            "report": dict(report),
            "canary_sha256_before": digest,
            "canary_sha256_after": modified_digest,
            "tree_stopped": process.tree_stopped,
        }

    def test_root_early_exit_with_live_descendant_is_failure(self):
        if self._delegated():
            return
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
        type(self).recorded_evidence[self._testMethodName] = {
            "root_pid": process.pid,
            "descendant_pid": descendant,
            "descendant_alive": False,
            "tree_stopped": process.tree_stopped,
            "profile_closed": self.profile.closed,
        }

    def test_profile_close_refuses_live_tree_then_allows_sequential_reuse(self):
        if self._delegated():
            return
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
        type(self).recorded_evidence[self._testMethodName] = {
            "first_pid": process.pid,
            "second_pid": second.pid,
            "first_stopped": process.tree_stopped,
            "second_stopped": second.tree_stopped,
            "profile_closed": self.profile.closed,
        }


class NativeFixtureCleanupTests(unittest.TestCase):
    """B1a-R5 / CI-R2: native fixture cleanup owns its scratch root."""

    def test_cleanup_collects_all_failures_and_raises(self):
        root = tempfile.TemporaryDirectory(prefix="omp-b1a-cleanup-")
        self.addCleanup(root.cleanup)
        missing_global_root = Path(root.name) / "missing-scratch-root"
        owned_parent = Path(root.name) / "owned-parent"
        owned_parent.mkdir()
        scratch = Path(tempfile.mkdtemp(dir=owned_parent, prefix="cleanup-"))
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
                sys.modules[__name__], "SCRATCH_ROOT", missing_global_root
            ),
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
        self.assertFalse(missing_global_root.exists())


class NativeControllerDelegationTests(unittest.TestCase):
    """SEC02B1A-CI-R1 / R1a / R1b / R1c: controller delegation guards."""

    def setUp(self) -> None:
        global _UNPROVEN_CONTROLLER_STATE
        saved_poison = _UNPROVEN_CONTROLLER_STATE
        _UNPROVEN_CONTROLLER_STATE = None

        def restore_poison() -> None:
            global _UNPROVEN_CONTROLLER_STATE
            _UNPROVEN_CONTROLLER_STATE = saved_poison

        self.addCleanup(restore_poison)

    def _restore_native_class_state(self) -> None:
        saved = (
            AppContainerNativeTests.temp,
            AppContainerNativeTests.delegated,
            AppContainerNativeTests.child_verified,
            dict(AppContainerNativeTests.child_evidence),
            dict(AppContainerNativeTests.recorded_evidence),
        )

        def restore() -> None:
            (
                AppContainerNativeTests.temp,
                AppContainerNativeTests.delegated,
                AppContainerNativeTests.child_verified,
                AppContainerNativeTests.child_evidence,
                AppContainerNativeTests.recorded_evidence,
            ) = saved

        self.addCleanup(restore)

    def _valid_evidence_map(self, user_sid: str = "S-1-5-21-1001") -> dict[str, object]:
        digest_a = "a" * 64
        digest_b = "b" * 64
        return {
            "test_interrupted_allocation_releases_real_profile": {
                "profile_name": "OMP.B1A.INT.a1b2c3d4e5f6",
                "created": [0],
                "freed_count": 1,
                "deleted": ["OMP.B1A.INT.a1b2c3d4e5f6"],
                "profile_absent": True,
            },
            "test_real_collision_never_deletes_existing_profile": {
                "profile_name": "OMP.B1A.COL.f6e5d4c3b2a1",
                "freed": [],
                "deleted": [],
                "owner_closed": True,
            },
            "test_secure_worker_token_identity_and_owned_isolation": {
                "worker_pid": 9002,
                "profile_sid": "S-1-15-2-111-222",
                "user_sid": user_sid,
                "token": {
                    "is_container": 1,
                    "appcontainer_sid": "S-1-15-2-111-222",
                    "integrity": LOW_INTEGRITY_SID,
                    "capabilities": 0,
                    "user": user_sid,
                    "in_any_job": True,
                },
                "report": {
                    "version": sys.version.split()[0],
                    "allowed": "visible",
                    "secret": "no",
                    "stdin": "hello-stdin",
                    "workspace_write": "ok",
                    "canary_read": "denied:PermissionError",
                    "canary_append": "denied:PermissionError",
                    "foreign_handle": "denied",
                },
                "canary_sha256_before": digest_a,
                "canary_sha256_after": digest_a,
                "tree_stopped": True,
                "profile_closed": True,
            },
            "test_same_user_control_reads_and_writes_the_same_canary": {
                "control_pid": 9003,
                "report": {
                    "read": "protected",
                    "append": "ok",
                },
                "canary_sha256_before": digest_a,
                "canary_sha256_after": digest_b,
                "tree_stopped": True,
            },
            "test_root_early_exit_with_live_descendant_is_failure": {
                "root_pid": 9004,
                "descendant_pid": 9005,
                "descendant_alive": False,
                "tree_stopped": True,
                "profile_closed": True,
            },
            "test_profile_close_refuses_live_tree_then_allows_sequential_reuse": {
                "first_pid": 9006,
                "second_pid": 9007,
                "first_stopped": True,
                "second_stopped": True,
                "profile_closed": True,
            },
        }

    def test_high_integrity_setup_delegates_without_parent_allocation(self):
        self._restore_native_class_state()
        names = _native_test_names()
        delegated_payload = {
            "pid": 8001,
            "python": sys.version.split()[0],
            "integrity": MEDIUM_INTEGRITY_SID,
            "user": "S-1-5-21-1001",
            "is_container": 0,
            "tests": names,
            "passed": names,
            "evidence": self._valid_evidence_map(),
            "log": "OK\n",
        }
        with (
            patch.object(
                sys.modules[__name__], "_token_information",
                return_value={"integrity": HIGH_INTEGRITY_SID},
            ),
            patch.object(
                sys.modules[__name__], "_delegate_native_controller",
                return_value=delegated_payload,
            ) as delegate,
            patch.object(
                sys.modules[__name__], "_prepare_runtime",
                side_effect=AssertionError("parent allocated runtime"),
            ) as prepare,
        ):
            AppContainerNativeTests.setUpClass()
            try:
                for name in names:
                    case = AppContainerNativeTests(methodName=name)
                    case.setUp()
                    getattr(case, name)()
            finally:
                AppContainerNativeTests.tearDownClass()
        self.assertEqual(delegate.call_count, 1)
        self.assertEqual(prepare.call_count, 0)

    def test_low_integrity_setup_still_fails_closed_before_allocation(self):
        self._restore_native_class_state()
        with (
            patch.object(
                sys.modules[__name__], "_token_information",
                return_value={"integrity": LOW_INTEGRITY_SID},
            ),
            patch.object(
                sys.modules[__name__], "_delegate_native_controller",
                side_effect=AssertionError("delegated from Low integrity"),
            ),
            patch.object(
                sys.modules[__name__], "_prepare_runtime",
                side_effect=AssertionError("allocated runtime at Low"),
            ),
        ):
            with self.assertRaises(RuntimeError) as caught:
                AppContainerNativeTests.setUpClass()
        self.assertIn(LOW_INTEGRITY_SID, str(caught.exception))

    def test_delegate_rejects_unverified_pre_resume_token_and_terminates_child(self):
        resumed: list[int] = []
        terminated: list[int] = []
        closed: list[int] = []

        class _FakeControllerKernel:
            CloseHandle = staticmethod(lambda h: closed.append(_handle_value(h)) or True)
            ResumeThread = staticmethod(lambda h: resumed.append(_handle_value(h)) or 1)
            TerminateProcess = staticmethod(
                lambda h, code: terminated.append(_handle_value(h)) or True
            )
            WaitForSingleObject = staticmethod(lambda h, ms: 0)

        info = _PROCESS_INFORMATION(
            hProcess=wintypes.HANDLE(701),
            hThread=wintypes.HANDLE(702),
            dwProcessId=8080,
            dwThreadId=8081,
        )
        with (
            patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
            patch.object(sys.modules[__name__], "_icacls", return_value=None),
            patch.object(
                sys.modules[__name__], "_start_with_medium_token",
                return_value=info,
            ),
            patch.object(
                sys.modules[__name__], "_token_information",
                return_value={
                    "integrity": HIGH_INTEGRITY_SID,
                    "is_container": 0,
                    "user": "S-1-5-21-1001",
                },
            ),
            patch.object(ctypes, "WinDLL", return_value=_FakeControllerKernel()),
        ):
            with self.assertRaises(RuntimeError) as caught:
                _delegate_native_controller()
        self.assertIn("pre-resume token check", str(caught.exception))
        self.assertIn(HIGH_INTEGRITY_SID, str(caught.exception))
        self.assertEqual(resumed, [])
        self.assertEqual(terminated, [701])
        self.assertEqual(closed, [702, 701])

    def test_delegate_rejects_skipped_or_unproven_child_report(self):
        names = _native_test_names()
        bad_reports = [
            {
                "pid": 8080,
                "python": sys.version.split()[0],
                "integrity": MEDIUM_INTEGRITY_SID,
                "user": "S-1-5-21-1001",
                "is_container": 0,
                "tests": names,
                "passed": names[:-1],
                "tests_run": len(names),
                "failures": 0,
                "errors": 0,
                "skipped": [names[-1]],
                "evidence": self._valid_evidence_map(),
                "success": True,
            },
            {
                "pid": 8080,
                "python": sys.version.split()[0],
                "integrity": MEDIUM_INTEGRITY_SID,
                "user": "S-1-5-21-1001",
                "is_container": 0,
                "tests": names,
                "passed": names,
                "tests_run": len(names),
                "failures": 0,
                "errors": 0,
                "skipped": [],
                "evidence": {},
                "success": True,
            },
        ]
        for bad_report in bad_reports:
            with self.subTest(skipped=bad_report["skipped"]):
                class _FakeControllerKernel:
                    CloseHandle = staticmethod(lambda h: True)
                    ResumeThread = staticmethod(lambda h: 1)
                    TerminateProcess = staticmethod(lambda h, code: True)
                    WaitForSingleObject = staticmethod(lambda h, ms: 0)

                def fake_start(
                    argv: list[str],
                    cwd: Path,
                    *,
                    out_info: _PROCESS_INFORMATION | None = None,
                ) -> _PROCESS_INFORMATION:
                    Path(argv[-2]).write_text(
                        json.dumps(bad_report), encoding="utf-8"
                    )
                    Path(argv[-1]).write_text("OK\n", encoding="utf-8")
                    if out_info is not None:
                        out_info.hProcess = wintypes.HANDLE(711)
                        out_info.hThread = wintypes.HANDLE(712)
                        out_info.dwProcessId = 8080
                        out_info.dwThreadId = 8081
                        return out_info
                    return _PROCESS_INFORMATION(
                        hProcess=wintypes.HANDLE(711),
                        hThread=wintypes.HANDLE(712),
                        dwProcessId=8080,
                        dwThreadId=8081,
                    )

                with (
                    patch.object(
                        sys.modules[__name__], "_user_sid",
                        return_value="S-1-5-21-1001",
                    ),
                    patch.object(sys.modules[__name__], "_icacls", return_value=None),
                    patch.object(
                        sys.modules[__name__], "_start_with_medium_token",
                        side_effect=fake_start,
                    ),
                    patch.object(
                        sys.modules[__name__], "_token_information",
                        return_value={
                            "integrity": MEDIUM_INTEGRITY_SID,
                            "is_container": 0,
                            "user": "S-1-5-21-1001",
                        },
                    ),
                    patch.object(
                        sys.modules[__name__], "_wait_for_exit",
                        return_value=(0, False),
                    ),
                    patch.object(
                        ctypes, "WinDLL", return_value=_FakeControllerKernel()
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        _delegate_native_controller()

    def test_delegate_interrupt_terminates_child_and_closes_handles(self):
        terminated: list[int] = []
        closed: list[int] = []

        class _FakeControllerKernel:
            CloseHandle = staticmethod(lambda h: closed.append(_handle_value(h)) or True)
            ResumeThread = staticmethod(lambda h: 1)
            TerminateProcess = staticmethod(
                lambda h, code: terminated.append(_handle_value(h)) or True
            )
            WaitForSingleObject = staticmethod(lambda h, ms: 0)

        info = _PROCESS_INFORMATION(
            hProcess=wintypes.HANDLE(721),
            hThread=wintypes.HANDLE(722),
            dwProcessId=8090,
            dwThreadId=8091,
        )
        with (
            patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
            patch.object(sys.modules[__name__], "_icacls", return_value=None),
            patch.object(
                sys.modules[__name__], "_start_with_medium_token",
                return_value=info,
            ),
            patch.object(
                sys.modules[__name__], "_token_information",
                return_value={
                    "integrity": MEDIUM_INTEGRITY_SID,
                    "is_container": 0,
                    "user": "S-1-5-21-1001",
                },
            ),
            patch.object(
                sys.modules[__name__], "_wait_for_exit",
                side_effect=KeyboardInterrupt,
            ),
            patch.object(ctypes, "WinDLL", return_value=_FakeControllerKernel()),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _delegate_native_controller()
        self.assertEqual(terminated, [721])
        self.assertEqual(closed, [722, 721])

    def test_spawn_with_token_fails_closed_without_fallback_when_apis_fail(self):
        class _FakeSpawnDll:
            CloseHandle = staticmethod(lambda h: True)
            TerminateProcess = staticmethod(lambda h, code: True)
            WaitForSingleObject = staticmethod(lambda h, ms: 0)

            @staticmethod
            def CreateProcessAsUserW(*args: object) -> bool:
                ctypes.set_last_error(1314)
                return False

            @staticmethod
            def CreateProcessWithTokenW(*args: object) -> bool:
                ctypes.set_last_error(1314)
                return False

        with (
            patch.object(
                sys.modules[__name__], "_enable_own_privileges",
                return_value={"SeAssignPrimaryTokenPrivilege": False},
            ),
            patch.object(ctypes, "WinDLL", return_value=_FakeSpawnDll()),
            patch("subprocess.Popen", side_effect=AssertionError("fallback")),
        ):
            with self.assertRaises(RuntimeError) as caught:
                _spawn_with_token(wintypes.HANDLE(99), ["worker.exe"], REPO_ROOT)
        message = str(caught.exception)
        self.assertIn("CreateProcessAsUserW error 1314", message)
        self.assertIn("CreateProcessWithTokenW error 1314", message)
        self.assertIn("refusing to reuse the High token", message)

    def test_delegate_ownership_guard_covers_kernel_init_and_native_return(self):
        global _UNPROVEN_CONTROLLER_STATE
        info = _PROCESS_INFORMATION(
            hProcess=wintypes.HANDLE(721),
            hThread=wintypes.HANDLE(722),
            dwProcessId=8090,
            dwThreadId=8091,
        )
        with (
            patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
            patch.object(sys.modules[__name__], "_icacls", return_value=None),
            patch.object(
                sys.modules[__name__], "_start_with_medium_token",
                return_value=info,
            ),
            patch.object(
                ctypes, "WinDLL",
                side_effect=OSError("review kernel initialization failure"),
            ),
        ):
            with self.assertRaises(OSError) as caught:
                _delegate_native_controller()
        self.assertIn("review kernel initialization failure", str(caught.exception))
        notes = "\n".join(getattr(caught.exception, "__notes__", []))
        self.assertIn("controller cleanup failed", notes)
        self.assertEqual(_handle_value(info.hProcess), 721)
        self.assertEqual(_handle_value(info.hThread), 722)
        self.assertIsNotNone(_UNPROVEN_CONTROLLER_STATE)
        with self.assertRaises(RuntimeError) as blocked:
            _delegate_native_controller()
        self.assertIn("refusing to launch a replacement", str(blocked.exception))
        _UNPROVEN_CONTROLLER_STATE = None

        terminated: list[int] = []
        closed: list[int] = []
        with_token_calls: list[int] = []

        class _InterruptingSpawnDll:
            CloseHandle = staticmethod(lambda h: closed.append(_handle_value(h)) or True)
            TerminateProcess = staticmethod(
                lambda h, code: terminated.append(_handle_value(h)) or True
            )
            WaitForSingleObject = staticmethod(lambda h, ms: WAIT_OBJECT_0)

            @staticmethod
            def CreateProcessAsUserW(*args: object) -> bool:
                raw_info = ctypes.cast(
                    args[-1], ctypes.POINTER(_PROCESS_INFORMATION)  # type: ignore[arg-type]
                ).contents
                raw_info.hProcess = wintypes.HANDLE(741)
                raw_info.hThread = wintypes.HANDLE(742)
                raw_info.dwProcessId = 8140
                raw_info.dwThreadId = 8141
                raise KeyboardInterrupt("interrupt at native return")

            @staticmethod
            def CreateProcessWithTokenW(*args: object) -> bool:
                with_token_calls.append(1)
                return False

        out_info = _PROCESS_INFORMATION()
        with (
            patch.object(
                sys.modules[__name__], "_enable_own_privileges",
                return_value={"SeAssignPrimaryTokenPrivilege": True},
            ),
            patch.object(ctypes, "WinDLL", return_value=_InterruptingSpawnDll()),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _spawn_with_token(
                    wintypes.HANDLE(99), ["worker.exe"], REPO_ROOT,
                    out_info=out_info,
                )
        self.assertEqual(terminated, [741])
        self.assertEqual(closed, [742, 741])
        self.assertEqual(_handle_value(out_info.hProcess), 0)
        self.assertEqual(_handle_value(out_info.hThread), 0)
        self.assertEqual(with_token_calls, [])

    def test_release_controller_handles_checks_results_and_preserves_interrupts(self):
        global _UNPROVEN_CONTROLLER_STATE
        close_calls: list[int] = []
        info = _PROCESS_INFORMATION(
            hProcess=wintypes.HANDLE(731),
            hThread=wintypes.HANDLE(732),
            dwProcessId=8100,
            dwThreadId=8101,
        )

        class _FailingCleanupKernel:
            CloseHandle = staticmethod(
                lambda h: close_calls.append(_handle_value(h)) and False
            )
            TerminateProcess = staticmethod(lambda h, code: False)
            WaitForSingleObject = staticmethod(lambda h, ms: WAIT_TIMEOUT)

        with self.assertRaises(RuntimeError) as caught:
            _release_controller_handles(
                _FailingCleanupKernel(), info, terminate=True, timeout=0.05
            )
        msg = str(caught.exception)
        self.assertIn("CloseHandle(thread) returned FALSE", msg)
        self.assertIn("TerminateProcess returned FALSE", msg)
        self.assertIn("WaitForSingleObject(process) timed out", msg)
        self.assertIn("process handle retained", msg)
        self.assertEqual(_handle_value(info.hProcess), 731)
        self.assertEqual(_handle_value(info.hThread), 732)
        self.assertEqual(close_calls, [732])
        self.assertIsNotNone(_UNPROVEN_CONTROLLER_STATE)

        with self.assertRaises(RuntimeError) as second_caught:
            _release_controller_handles(
                _FailingCleanupKernel(), info, terminate=True, timeout=0.05
            )
        self.assertIn("refusing to double-close", str(second_caught.exception))
        self.assertEqual(close_calls, [732])

        with self.assertRaises(RuntimeError) as replacement_caught:
            _start_with_medium_token(["worker.exe"], REPO_ROOT)
        self.assertIn("refusing to launch a replacement", str(replacement_caught.exception))
        _UNPROVEN_CONTROLLER_STATE = None

        for interrupt_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(interrupt=interrupt_type.__name__):
                _UNPROVEN_CONTROLLER_STATE = None
                interrupt_instance = interrupt_type("stop cleanup")
                step_calls: list[str] = []
                info_int = _PROCESS_INFORMATION(
                    hProcess=wintypes.HANDLE(751),
                    hThread=wintypes.HANDLE(752),
                    dwProcessId=8150,
                    dwThreadId=8151,
                )

                class _InterruptCleanupKernel:
                    @staticmethod
                    def CloseHandle(h: object) -> bool:
                        val = _handle_value(h)
                        step_calls.append(f"close:{val}")
                        if val == 752:
                            raise interrupt_instance
                        return False

                    @staticmethod
                    def TerminateProcess(h: object, code: int) -> bool:
                        step_calls.append(f"term:{_handle_value(h)}")
                        return True

                    @staticmethod
                    def WaitForSingleObject(h: object, ms: int) -> int:
                        step_calls.append(f"wait:{_handle_value(h)}")
                        return WAIT_OBJECT_0

                with self.assertRaises(interrupt_type) as int_caught:
                    _release_controller_handles(
                        _InterruptCleanupKernel(), info_int, terminate=True, timeout=0.05
                    )
                self.assertIs(int_caught.exception, interrupt_instance)
                self.assertEqual(
                    step_calls,
                    ["close:752", "term:751", "wait:751", "close:751"],
                )
                self.assertEqual(_handle_value(info_int.hThread), 752)
                self.assertEqual(_handle_value(info_int.hProcess), 751)
                notes = "\n".join(getattr(interrupt_instance, "__notes__", []))
                self.assertIn("CloseHandle(thread) raised", notes)
                self.assertIn("CloseHandle(process) returned FALSE", notes)
        _UNPROVEN_CONTROLLER_STATE = None

        class _WaitFailedKernel:
            WaitForSingleObject = staticmethod(lambda h, ms: WAIT_FAILED)
            GetExitCodeProcess = staticmethod(lambda h, out: True)

        with self.assertRaises(OSError):
            _wait_for_exit(
                wintypes.HANDLE(761), 0.01, kernel32=_WaitFailedKernel()
            )

    def test_native_fixture_never_modifies_existing_desktop_dacl(self):
        self.assertFalse(hasattr(sys.modules[__name__], "_set_desktop_dacl"))
        real_win_dll = ctypes.WinDLL

        def guarded_win_dll(name: str, *args: object, **kwargs: object) -> object:
            dll = real_win_dll(name, *args, **kwargs)  # type: ignore[arg-type]
            if name.lower() == "advapi32":
                class _GuardedAdvapi:
                    def __getattr__(self, attr: str) -> object:
                        if attr == "SetSecurityInfo":
                            raise AssertionError(
                                "SetSecurityInfo on caller desktop is forbidden"
                            )
                        return getattr(dll, attr)
                return _GuardedAdvapi()
            return dll

        before_sddl = _desktop_dacl()
        with tempfile.TemporaryDirectory(prefix="omp-b1a-desk-guard-") as folder:
            root = Path(folder)
            native = AppContainerNativeTests(
                "test_secure_worker_token_identity_and_owned_isolation"
            )
            native.root = root / "fixture-root"
            native.root.mkdir()
            native.runtime = native.root / "runtime" / "python.exe"
            native.user_sid = "S-1-5-21-1001"
            fake_profile = type(
                "_FakeProfile", (),
                {"sid": "S-1-15-2-100-200", "close": staticmethod(lambda: None)},
            )()
            with (
                patch.object(AppContainerNativeTests, "delegated", False),
                patch.object(
                    WindowsAppContainerProfile, "create",
                    return_value=fake_profile,
                ),
                patch.object(sys.modules[__name__], "_icacls", return_value=None),
                patch.object(ctypes, "WinDLL", side_effect=guarded_win_dll),
            ):
                native.setUp()
                native.doCleanups()
                desktop = _OwnedDesktop.create(_user_sid())
                self.assertTrue(desktop.name.startswith("omp-b1a-desk-"))
                self.assertGreater(desktop.handle, 0)
                desktop.close()
                self.assertTrue(desktop.closed)
                self.assertEqual(desktop.handle, 0)
        after_sddl = _desktop_dacl()
        self.assertEqual(before_sddl, after_sddl)

    def test_validator_and_delegate_reject_contradictory_or_incomplete_evidence(self):
        import copy

        expected = _native_test_names()
        user = "S-1-5-21-1001"
        base = self._valid_evidence_map(user)
        self.assertEqual(
            _validate_child_evidence(base, expected, 8080, user), []
        )
        secure_name = "test_secure_worker_token_identity_and_owned_isolation"
        control_name = "test_same_user_control_reads_and_writes_the_same_canary"
        early_name = "test_root_early_exit_with_live_descendant_is_failure"
        reuse_name = (
            "test_profile_close_refuses_live_tree_then_allows_sequential_reuse"
        )
        mutations = {
            "canary_access_allowed": lambda d: d[secure_name]["report"].update(
                canary_read="allowed", canary_append="allowed"
            ),
            "foreign_handle_usable": lambda d: d[secure_name]["report"].update(
                foreign_handle="allowed"
            ),
            "invalid_digest": lambda d: d[secure_name].update(
                canary_sha256_before="not-a-sha256",
                canary_sha256_after="not-a-sha256",
            ),
            "other_four_evidence_empty": lambda d: d.update(
                {
                    name: {}
                    for name in expected
                    if name not in (secure_name, control_name)
                }
            ),
            "control_cleanup_unproven": lambda d: d[control_name].update(
                tree_stopped=False, control_pid=8080
            ),
            "early_exit_descendant_alive_or_same_pid": lambda d: d[
                early_name
            ].update(descendant_alive=True, descendant_pid=d[early_name]["root_pid"]),
            "reuse_duplicate_pids_or_unclosed": lambda d: d[reuse_name].update(
                second_pid=d[reuse_name]["first_pid"], profile_closed=False
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                bad = copy.deepcopy(base)
                mutate(bad)
                problems = _validate_child_evidence(bad, expected, 8080, user)
                self.assertTrue(
                    problems, f"validator accepted mutation {label}"
                )
                report = {
                    "pid": 8080,
                    "python": sys.version.split()[0],
                    "integrity": MEDIUM_INTEGRITY_SID,
                    "user": user,
                    "is_container": 0,
                    "tests": expected,
                    "passed": expected,
                    "tests_run": len(expected),
                    "failures": 0,
                    "errors": 0,
                    "skipped": [],
                    "success": True,
                    "evidence": bad,
                }

                class _FakeControllerKernel:
                    CloseHandle = staticmethod(lambda h: True)
                    ResumeThread = staticmethod(lambda h: 1)
                    TerminateProcess = staticmethod(lambda h, code: True)
                    WaitForSingleObject = staticmethod(lambda h, ms: 0)

                def start(
                    argv: list[str],
                    cwd: Path,
                    *,
                    out_info: _PROCESS_INFORMATION | None = None,
                ) -> _PROCESS_INFORMATION:
                    Path(argv[-2]).write_text(
                        json.dumps(report), encoding="utf-8"
                    )
                    Path(argv[-1]).write_text("OK\n", encoding="utf-8")
                    if out_info is not None:
                        out_info.hProcess = wintypes.HANDLE(711)
                        out_info.hThread = wintypes.HANDLE(712)
                        out_info.dwProcessId = 8080
                        out_info.dwThreadId = 8081
                        return out_info
                    return _PROCESS_INFORMATION(
                        hProcess=wintypes.HANDLE(711),
                        hThread=wintypes.HANDLE(712),
                        dwProcessId=8080,
                        dwThreadId=8081,
                    )

                with (
                    patch.object(
                        sys.modules[__name__], "_user_sid", return_value=user
                    ),
                    patch.object(
                        sys.modules[__name__], "_icacls", return_value=None
                    ),
                    patch.object(
                        sys.modules[__name__],
                        "_start_with_medium_token",
                        side_effect=start,
                    ),
                    patch.object(
                        sys.modules[__name__],
                        "_token_information",
                        return_value={
                            "integrity": MEDIUM_INTEGRITY_SID,
                            "user": user,
                            "is_container": 0,
                        },
                    ),
                    patch.object(
                        sys.modules[__name__],
                        "_wait_for_exit",
                        return_value=(0, False),
                    ),
                    patch.object(
                        ctypes, "WinDLL", return_value=_FakeControllerKernel()
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        _delegate_native_controller()

    def test_token_close_interrupt_after_spawn_terminates_and_cleans_controller(self):
        global _UNPROVEN_CONTROLLER_STATE
        for interrupt_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(interrupt=interrupt_type.__name__):
                _UNPROVEN_CONTROLLER_STATE = None
                interrupt_inst = interrupt_type("review primary token interrupt")
                closes: list[int] = []
                desk = types.SimpleNamespace(
                    name="review-owned-desktop", closed=False, close=Mock()
                )

                def close_cb(handle: object) -> bool:
                    val = _handle_value(handle)
                    closes.append(val)
                    if val == 888:
                        raise interrupt_inst
                    return True

                kernel = types.SimpleNamespace(
                    GetCurrentProcess=Mock(return_value=11),
                    CloseHandle=Mock(side_effect=close_cb),
                    LocalFree=Mock(),
                    TerminateProcess=Mock(return_value=True),
                    WaitForSingleObject=Mock(return_value=WAIT_OBJECT_0),
                )

                def open_token(*args: object) -> bool:
                    args[-1]._obj.value = 777  # type: ignore[attr-defined]
                    return True

                def duplicate(*args: object) -> bool:
                    args[-1]._obj.value = 888  # type: ignore[attr-defined]
                    return True

                def sid_cb(*args: object) -> bool:
                    args[-1]._obj.value = 999  # type: ignore[attr-defined]
                    return True

                advapi = types.SimpleNamespace(
                    OpenProcessToken=Mock(side_effect=open_token),
                    DuplicateTokenEx=Mock(side_effect=duplicate),
                    ConvertStringSidToSidW=Mock(side_effect=sid_cb),
                    SetTokenInformation=Mock(return_value=True),
                )

                def spawn(token: object, argv: object, cwd: object, **kwargs: object) -> _PROCESS_INFORMATION:
                    info = kwargs["out_info"]
                    info.hProcess = wintypes.HANDLE(901)
                    info.hThread = wintypes.HANDLE(902)
                    info.dwProcessId = 9001
                    info.dwThreadId = 9002
                    return info  # type: ignore[return-value]

                with (
                    patch.object(sys.modules[__name__], "_controller_kernel32", return_value=kernel),
                    patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
                    patch.object(sys.modules[__name__], "_icacls", return_value=None),
                    patch.object(sys.modules[__name__], "_spawn_with_token", side_effect=spawn),
                    patch.object(_OwnedDesktop, "create", return_value=desk),
                    patch.object(ctypes, "WinDLL", return_value=advapi),
                ):
                    with self.assertRaises(interrupt_type) as caught:
                        _delegate_native_controller()
                    self.assertIs(caught.exception, interrupt_inst)

                self.assertEqual(kernel.TerminateProcess.call_count, 1)
                self.assertEqual(kernel.WaitForSingleObject.call_count, 1)
                self.assertEqual(desk.close.call_count, 1)
                self.assertIn(777, closes)
                self.assertIn(888, closes)
                self.assertIn(901, closes)
                self.assertIn(902, closes)
                self.assertEqual(_UNPROVEN_CONTROLLER_STATE, interrupt_type.__name__)
                with self.assertRaises(RuntimeError) as blocked:
                    _ensure_controller_state_clean()
                self.assertIn("refusing to launch a replacement", str(blocked.exception))
                _UNPROVEN_CONTROLLER_STATE = None

    def test_desktop_create_failure_closes_primary_token(self):
        global _UNPROVEN_CONTROLLER_STATE
        closes: list[int] = []

        def close_cb(handle: object) -> bool:
            closes.append(_handle_value(handle))
            return True

        kernel = types.SimpleNamespace(
            GetCurrentProcess=Mock(return_value=11),
            CloseHandle=Mock(side_effect=close_cb),
            LocalFree=Mock(),
        )

        def open_token(*args: object) -> bool:
            args[-1]._obj.value = 777  # type: ignore[attr-defined]
            return True

        def duplicate(*args: object) -> bool:
            args[-1]._obj.value = 888  # type: ignore[attr-defined]
            return True

        def sid_cb(*args: object) -> bool:
            args[-1]._obj.value = 999  # type: ignore[attr-defined]
            return True

        advapi = types.SimpleNamespace(
            OpenProcessToken=Mock(side_effect=open_token),
            DuplicateTokenEx=Mock(side_effect=duplicate),
            ConvertStringSidToSidW=Mock(side_effect=sid_cb),
            SetTokenInformation=Mock(return_value=True),
        )

        with (
            patch.object(sys.modules[__name__], "_controller_kernel32", return_value=kernel),
            patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
            patch.object(_OwnedDesktop, "create", side_effect=OSError("review desktop creation failure")),
            patch.object(ctypes, "WinDLL", return_value=advapi),
        ):
            with self.assertRaises(OSError) as caught:
                _start_with_medium_token(["fixture-controller.exe"], REPO_ROOT)
            self.assertIn("review desktop creation failure", str(caught.exception))

        self.assertIn(777, closes)
        self.assertIn(888, closes)
        self.assertIsNone(_UNPROVEN_CONTROLLER_STATE)

    def test_token_close_failure_or_raise_cleans_controller_and_poisons_state(self):
        global _UNPROVEN_CONTROLLER_STATE
        for mode in ("false", "raise"):
            with self.subTest(mode=mode):
                _UNPROVEN_CONTROLLER_STATE = None
                closes: list[int] = []
                desk = types.SimpleNamespace(
                    name="review-owned-desktop", closed=False, close=Mock()
                )

                def close_cb(handle: object) -> bool:
                    val = _handle_value(handle)
                    closes.append(val)
                    if val == 888:
                        if mode == "false":
                            return False
                        raise OSError("simulated token CloseHandle error")
                    return True

                kernel = types.SimpleNamespace(
                    GetCurrentProcess=Mock(return_value=11),
                    CloseHandle=Mock(side_effect=close_cb),
                    LocalFree=Mock(),
                    TerminateProcess=Mock(return_value=True),
                    WaitForSingleObject=Mock(return_value=WAIT_OBJECT_0),
                )

                def open_token(*args: object) -> bool:
                    args[-1]._obj.value = 777  # type: ignore[attr-defined]
                    return True

                def duplicate(*args: object) -> bool:
                    args[-1]._obj.value = 888  # type: ignore[attr-defined]
                    return True

                def sid_cb(*args: object) -> bool:
                    args[-1]._obj.value = 999  # type: ignore[attr-defined]
                    return True

                advapi = types.SimpleNamespace(
                    OpenProcessToken=Mock(side_effect=open_token),
                    DuplicateTokenEx=Mock(side_effect=duplicate),
                    ConvertStringSidToSidW=Mock(side_effect=sid_cb),
                    SetTokenInformation=Mock(return_value=True),
                )

                def spawn(token: object, argv: object, cwd: object, **kwargs: object) -> _PROCESS_INFORMATION:
                    info = kwargs["out_info"]
                    info.hProcess = wintypes.HANDLE(901)
                    info.hThread = wintypes.HANDLE(902)
                    info.dwProcessId = 9001
                    info.dwThreadId = 9002
                    return info  # type: ignore[return-value]

                with (
                    patch.object(sys.modules[__name__], "_controller_kernel32", return_value=kernel),
                    patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
                    patch.object(sys.modules[__name__], "_icacls", return_value=None),
                    patch.object(sys.modules[__name__], "_spawn_with_token", side_effect=spawn),
                    patch.object(_OwnedDesktop, "create", return_value=desk),
                    patch.object(ctypes, "WinDLL", return_value=advapi),
                ):
                    with self.assertRaises(OSError) as caught:
                        _delegate_native_controller()
                    if mode == "false":
                        self.assertIn("CloseHandle(primary_token=888) returned FALSE", str(caught.exception))
                    else:
                        self.assertIn("simulated token CloseHandle error", str(caught.exception))

                self.assertEqual(kernel.TerminateProcess.call_count, 1)
                self.assertEqual(kernel.WaitForSingleObject.call_count, 1)
                self.assertEqual(desk.close.call_count, 1)
                self.assertIn(777, closes)
                self.assertIn(888, closes)
                self.assertIn(901, closes)
                self.assertIn(902, closes)
                self.assertIsNotNone(_UNPROVEN_CONTROLLER_STATE)
                self.assertIn("primary token close failed", _UNPROVEN_CONTROLLER_STATE)
                with self.assertRaises(RuntimeError) as blocked:
                    _ensure_controller_state_clean()
                self.assertIn("refusing to launch a replacement", str(blocked.exception))
                _UNPROVEN_CONTROLLER_STATE = None

    def test_delegate_internal_type_error_propagates_without_retry_or_swallowing(self):
        calls: list[dict[str, bool]] = []
        terminated: list[int] = []
        closed: list[int] = []

        class _FakeControllerKernel:
            CloseHandle = staticmethod(lambda h: closed.append(_handle_value(h)) or True)
            ResumeThread = staticmethod(lambda h: 1)
            TerminateProcess = staticmethod(
                lambda h, code: terminated.append(_handle_value(h)) or True
            )
            WaitForSingleObject = staticmethod(lambda h, ms: WAIT_OBJECT_0)

        def faulty_start(
            argv: list[str],
            cwd: Path,
            *,
            out_info: _PROCESS_INFORMATION | None = None,
        ) -> _PROCESS_INFORMATION:
            calls.append({"has_out_info": out_info is not None})
            if out_info is not None:
                out_info.hProcess = wintypes.HANDLE(781)
                out_info.hThread = wintypes.HANDLE(782)
                out_info.dwProcessId = 8880
                out_info.dwThreadId = 8881
            if len(calls) == 1:
                raise TypeError("internal controller factory bug; signature accepts out_info")
            Path(argv[-2]).write_text(
                json.dumps(
                    {
                        "pid": 8880,
                        "python": sys.version.split()[0],
                        "integrity": MEDIUM_INTEGRITY_SID,
                        "user": "S-1-5-21-1001",
                        "is_container": 0,
                        "tests": _native_test_names(),
                        "passed": _native_test_names(),
                        "tests_run": len(_native_test_names()),
                        "failures": 0,
                        "errors": 0,
                        "skipped": [],
                        "evidence": self._valid_evidence_map("S-1-5-21-1001"),
                        "success": True,
                    }
                ),
                encoding="utf-8",
            )
            Path(argv[-1]).write_text("OK\n", encoding="utf-8")
            return out_info or _PROCESS_INFORMATION(
                hProcess=wintypes.HANDLE(781),
                hThread=wintypes.HANDLE(782),
                dwProcessId=8880,
                dwThreadId=8881,
            )

        with (
            patch.object(sys.modules[__name__], "_user_sid", return_value="S-1-5-21-1001"),
            patch.object(sys.modules[__name__], "_icacls", return_value=None),
            patch.object(sys.modules[__name__], "_start_with_medium_token", side_effect=faulty_start),
            patch.object(sys.modules[__name__], "_controller_kernel32", return_value=_FakeControllerKernel()),
            patch.object(
                sys.modules[__name__], "_token_information",
                return_value={
                    "integrity": MEDIUM_INTEGRITY_SID,
                    "user": "S-1-5-21-1001",
                    "is_container": 0,
                },
            ),
            patch.object(
                sys.modules[__name__], "_wait_for_exit",
                return_value=(0, False),
            ),
        ):
            with self.assertRaises(TypeError) as caught:
                _delegate_native_controller()
            self.assertIn("internal controller factory bug", str(caught.exception))

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["has_out_info"])
        self.assertEqual(terminated, [781])
        self.assertEqual(closed, [782, 781])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--native-controller":
        _report, _log = Path(sys.argv[2]), Path(sys.argv[3])
        try:
            _status = _run_native_controller(_report, _log)
        except BaseException as _error:
            _report.write_text(
                json.dumps(
                    {"error": f"{type(_error).__name__}: {_error}",
                     "success": False},
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            raise
        raise SystemExit(_status)
    unittest.main()
