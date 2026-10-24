"""Offline tests for the SEC-01 evidence verifier.

These tests do not create AppContainers or launch Workers.  They prove that the
verifier rejects a passing-looking report when an identity, denial, control,
environment, lifecycle, or cleanup claim is missing or contradictory.
"""
from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools.proofs import sec01_windows as proof


USER_SID = "S-1-5-21-2773687659-1529040039-3189417923-1001"
PROFILE_SID = "S-1-15-2-1-2-3-4-5-6-7-8"


def _allowed() -> dict[str, object]:
    return {
        "command": "type allowed-canary.txt",
        "exit_code": 0,
        "timed_out": False,
        "output": "FAKE-ALLOWED\n",
        "expected": "FAKE-ALLOWED",
        "content_matches": True,
        "ok": True,
    }


def _denied() -> dict[str, object]:
    return {
        "asset": "source",
        "path": "C:\\fake\\source\\source-canary.txt",
        "command": "type source-canary.txt",
        "exit_code": 1,
        "timed_out": False,
        "output": "Access is denied.",
        "access_denied": True,
        "access_denied_text": True,
        "canary_visible": False,
        "hash_before": "a" * 64,
        "hash_after": "a" * 64,
        "hash_unchanged": True,
    }


def _token() -> dict[str, object]:
    return {
        "is_app_container": True,
        "user_sid": USER_SID,
        "app_container_sid": PROFILE_SID,
        "integrity_level": "low",
        "integrity_sid": "S-1-16-4096",
        "elevated": False,
        "capability_count": 0,
        "privileges": [
            {"name": "SeChangeNotifyPrivilege", "attributes": "0x00000003"},
            {"name": "SeIncreaseWorkingSetPrivilege", "attributes": "0x00000000"},
        ],
    }


def _control_token() -> dict[str, object]:
    return {
        "is_app_container": False,
        "user_sid": USER_SID,
        "app_container_sid": None,
        "integrity_level": "other",
        "integrity_sid": "S-1-16-8192",
        "elevated": False,
        "capability_count": 0,
        "privileges": [],
    }


def valid_evidence() -> dict[str, object]:
    sensitive = {
        name: {"read": _denied(), "write": _denied(), "hash_unchanged": True}
        for name in proof.CHECK_NAMES
    }
    control_sensitive = {
        name: {"read": _allowed(), "write": _allowed()}
        for name in proof.CHECK_NAMES
    }
    return {
        "schema": proof.SCHEMA,
        "verdict": "PASS",
        "principal": {
            "user_sid": USER_SID,
            "integrity_sid": "S-1-16-8192",
            "elevated": False,
            "privileges": [],
        },
        "profile": {
            "name": "OMP.SEC01.Proof.test",
            "sid": PROFILE_SID,
            "folder": "C:\\fake\\Packages\\OMP.SEC01.Proof.test\\AC",
        },
        "workers": {
            "token": _token(),
            "identity": {
                "command": "whoami /user & whoami /groups",
                "exit_code": 0,
                "timed_out": False,
                "assigned_to_job": True,
                "output": USER_SID + " Low Mandatory Level",
                "user_sid_in_output": True,
                "low_integrity_in_output": True,
            },
            "allowed": {"read": _allowed(), "write": _allowed()},
            "ready": {
                **_allowed(),
                "assigned_to_job": True,
            },
            "sensitive": sensitive,
            "environment": {
                "command": "set",
                "exit_code": 0,
                "keys": ["OMP_SEC01_ALLOWED"],
                "parent_secret_name": "OMP_SEC01_PARENT_SECRET",
                "parent_secret_visible": False,
                "proof_value_visible": True,
                "explicit_block": True,
                "output": "OMP_SEC01_ALLOWED=FAKE-ALLOWED",
            },
            "network": {
                "status": "NOT PROVEN",
                "reason": "bounded loopback probe only",
                "external_endpoint_contacted": False,
            },
        },
        "controls": {
            "same_user": {
                "token": _control_token(),
                "allowed": {"read": _allowed(), "write": _allowed()},
                "sensitive": control_sensitive,
            }
        },
        "lifecycle": {
            "job_created": True,
            "main": {
                "assigned_before_resume": True,
                "active_processes_after_exit": 0,
            },
            "cleanup_kill": {
                "assigned_before_resume": True,
                "wait_within_timeout": True,
                "process_terminated": True,
                "wait_result": 0,
            },
        },
        "cleanup": {
            "fixture_removed": True,
            "profile_deleted": True,
            "ok": True,
            "errors": [],
            "profile_hresult": 0,
        },
        "properties": proof._properties(),
        "limitations": proof._limitations(),
    }


class EvidenceVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = valid_evidence()

    def assert_rejected(self, fragment: str) -> None:
        failures = proof.verify_evidence(self.evidence)
        self.assertTrue(
            any(fragment in failure for failure in failures),
            f"expected {fragment!r} in {failures!r}",
        )

    def test_valid_evidence_passes(self) -> None:
        self.assertEqual(proof.verify_evidence(self.evidence), [])

    def test_schema_mismatch_is_rejected(self) -> None:
        self.evidence["schema"] = "omp.sec01.windows-proof.v0"
        self.assert_rejected("schema must be")

    def test_non_pass_verdict_is_rejected(self) -> None:
        self.evidence["verdict"] = "FAILED"
        self.assert_rejected("verdict must be PASS")

    def test_worker_user_sid_mismatch_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["user_sid"] = "S-1-5-21-9-9-9-1001"
        self.assert_rejected("worker user SID does not match")

    def test_non_appcontainer_worker_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["is_app_container"] = False
        self.assert_rejected("not an AppContainer token")

    def test_appcontainer_sid_mismatch_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["app_container_sid"] = "S-1-15-2-9-9-9"
        self.assert_rejected("AppContainer SID does not match")

    def test_non_low_integrity_worker_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["integrity_level"] = "medium"
        self.assert_rejected("not at low integrity")

    def test_worker_capabilities_are_rejected(self) -> None:
        self.evidence["workers"]["token"]["capability_count"] = 1
        self.assert_rejected("unexpectedly has capabilities")

    def test_elevated_worker_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["elevated"] = True
        self.assert_rejected("worker token is elevated")

    def test_forbidden_worker_privilege_is_rejected(self) -> None:
        self.evidence["workers"]["token"]["privileges"].append(
            {"name": "SeImpersonatePrivilege", "attributes": "0x00000002"}
        )
        self.assert_rejected("forbidden privilege SeImpersonatePrivilege")

    def test_identity_failure_is_rejected(self) -> None:
        self.evidence["workers"]["identity"]["exit_code"] = 1
        self.assert_rejected("identity command did not exit 0")

    def test_identity_without_user_sid_is_rejected(self) -> None:
        self.evidence["workers"]["identity"]["user_sid_in_output"] = False
        self.assert_rejected("does not show the parent user SID")

    def test_identity_without_low_integrity_is_rejected(self) -> None:
        self.evidence["workers"]["identity"]["low_integrity_in_output"] = False
        self.assert_rejected("does not show low integrity")

    def test_worker_not_assigned_before_resume_is_rejected(self) -> None:
        self.evidence["workers"]["identity"]["assigned_to_job"] = False
        self.assert_rejected("was not assigned to the job")

    def test_allowed_fixture_read_failure_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["read"]["ok"] = False
        self.assert_rejected("allowed fixture read did not succeed")

    def test_allowed_fixture_write_failure_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["write"]["ok"] = False
        self.assert_rejected("allowed fixture write did not succeed")

    def test_allowed_exit_code_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["write"]["exit_code"] = 1
        self.assert_rejected("allowed fixture write exit code is not 0")

    def test_allowed_timeout_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["read"]["timed_out"] = True
        self.assert_rejected("allowed fixture read timed out")

    def test_allowed_content_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["read"]["content_matches"] = False
        self.assert_rejected("allowed fixture read content does not match expected")

    def test_allowed_output_text_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["allowed"]["read"]["output"] = "OTHER\n"
        self.assert_rejected("allowed fixture read output text does not match expected")

    def test_readiness_exit_code_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["ready"]["exit_code"] = 1
        self.assert_rejected("worker readiness marker exit code is not 0")

    def test_canary_hash_after_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["write"]["hash_after"] = "b" * 64
        self.assert_rejected("source write hash_after does not match hash_before")

    def test_canary_hash_shape_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["read"]["hash_before"] = "not-a-hash"
        self.assert_rejected("source read hash fields are not SHA-256")

    def test_canary_hash_binding_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["write"]["hash_before"] = "b" * 64
        self.evidence["workers"]["sensitive"]["source"]["write"]["hash_after"] = "b" * 64
        self.assert_rejected("source raw canary hashes disagree")

    def test_missing_readiness_marker_is_rejected(self) -> None:
        del self.evidence["workers"]["ready"]
        self.assert_rejected("worker readiness marker evidence is missing")

    def test_readiness_marker_without_job_is_rejected(self) -> None:
        self.evidence["workers"]["ready"]["assigned_to_job"] = False
        self.assert_rejected("readiness marker worker was not assigned to the job")

    def test_sensitive_read_not_denied_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["read"]["access_denied"] = False
        self.assert_rejected("source read was not denied")

    def test_sensitive_write_without_os_message_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["write"]["access_denied_text"] = False
        self.assert_rejected("source write did not capture an OS denial message")

    def test_sensitive_write_not_denied_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["vault"]["write"]["access_denied"] = False
        self.assert_rejected("vault write was not denied")

    def test_visible_canary_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["credentials"]["read"]["canary_visible"] = True
        self.assert_rejected("credentials read exposed the fake canary")

    def test_changed_canary_hash_is_rejected(self) -> None:
        self.evidence["workers"]["sensitive"]["source"]["hash_unchanged"] = False
        self.assert_rejected("source canary changed")

    def test_missing_sensitive_asset_is_rejected(self) -> None:
        del self.evidence["workers"]["sensitive"]["audit"]
        self.assert_rejected("sensitive fixture audit evidence is missing")

    def test_missing_same_user_control_is_rejected(self) -> None:
        del self.evidence["controls"]["same_user"]
        self.assert_rejected("same-user control evidence is missing")

    def test_appcontainer_control_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["token"]["is_app_container"] = True
        self.assert_rejected("missing a non-AppContainer token")

    def test_control_allowed_failure_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["allowed"]["read"]["ok"] = False
        self.assert_rejected("same-user control allowed fixture read failed")

    def test_control_sensitive_failure_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["sensitive"]["runtime"]["write"]["ok"] = False
        self.assert_rejected("same-user control runtime write failed")

    def test_control_user_sid_mismatch_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["token"]["user_sid"] = "S-1-5-18"
        self.assert_rejected("same-user control user SID does not match")

    def test_control_exit_code_contradiction_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["sensitive"]["source"]["write"]["exit_code"] = 1
        self.assert_rejected("same-user control source write exit code is not 0")

    def test_control_timeout_contradiction_is_rejected(self) -> None:
        self.evidence["controls"]["same_user"]["allowed"]["read"]["timed_out"] = True
        self.assert_rejected("same-user control allowed fixture read timed out")

    def test_parent_environment_leak_is_rejected(self) -> None:
        self.evidence["workers"]["environment"]["parent_secret_visible"] = True
        self.assert_rejected("parent environment secret leaked")

    def test_missing_explicit_environment_is_rejected(self) -> None:
        self.evidence["workers"]["environment"]["explicit_block"] = False
        self.assert_rejected("not built from the explicit block")

    def test_missing_proof_environment_value_is_rejected(self) -> None:
        self.evidence["workers"]["environment"]["proof_value_visible"] = False
        self.assert_rejected("explicit proof environment value was not visible")

    def test_environment_raw_output_contradiction_is_rejected(self) -> None:
        self.evidence["workers"]["environment"]["output"] = "NO-PROOF-VALUE"
        self.assert_rejected("proof environment value flag contradicts raw output")

    def test_network_proven_claim_is_rejected(self) -> None:
        self.evidence["workers"]["network"]["status"] = "PROVEN"
        self.assert_rejected("network must remain NOT PROVEN")

    def test_external_network_contact_is_rejected(self) -> None:
        self.evidence["workers"]["network"]["external_endpoint_contacted"] = True
        self.assert_rejected("contacted an external endpoint")

    def test_missing_job_is_rejected(self) -> None:
        self.evidence["lifecycle"]["job_created"] = False
        self.assert_rejected("proof-owned job was not created")

    def test_main_worker_not_assigned_is_rejected(self) -> None:
        self.evidence["lifecycle"]["main"]["assigned_before_resume"] = False
        self.assert_rejected("main worker was not assigned")

    def test_active_processes_after_exit_is_rejected(self) -> None:
        self.evidence["lifecycle"]["main"]["active_processes_after_exit"] = 1
        self.assert_rejected("job still has active processes")

    def test_cleanup_kill_without_termination_is_rejected(self) -> None:
        self.evidence["lifecycle"]["cleanup_kill"]["process_terminated"] = False
        self.assert_rejected("cleanup-kill child was not terminated")

    def test_cleanup_kill_without_bound_is_rejected(self) -> None:
        self.evidence["lifecycle"]["cleanup_kill"]["wait_within_timeout"] = False
        self.assert_rejected("did not terminate within the bound")

    def test_fixture_not_removed_is_rejected(self) -> None:
        self.evidence["cleanup"]["fixture_removed"] = False
        self.assert_rejected("fixture directory was not removed")

    def test_profile_not_deleted_is_rejected(self) -> None:
        self.evidence["cleanup"]["profile_deleted"] = False
        self.assert_rejected("AppContainer profile was not deleted")

    def test_cleanup_failure_is_rejected(self) -> None:
        self.evidence["cleanup"]["ok"] = False
        self.assert_rejected("cleanup did not complete cleanly")

    def test_cleanup_errors_contradiction_is_rejected(self) -> None:
        self.evidence["cleanup"]["errors"] = ["fixture removal failed"]
        self.assert_rejected("cleanup ok claim contradicts non-empty errors")

    def test_profile_hresult_contradiction_is_rejected(self) -> None:
        self.evidence["cleanup"]["profile_hresult"] = 1
        self.assert_rejected("profile_deleted claim contradicts profile_hresult")

    def test_cleanup_kill_wait_result_contradiction_is_rejected(self) -> None:
        self.evidence["lifecycle"]["cleanup_kill"]["wait_result"] = 258
        self.assert_rejected("terminated claim contradicts wait_result")

    def test_wrong_property_status_is_rejected(self) -> None:
        self.evidence["properties"]["network"]["status"] = "PROVEN"
        self.assert_rejected("property network must be NOT PROVEN")

    def test_limitation_must_remain_not_proven(self) -> None:
        self.evidence["limitations"][0]["status"] = "PROVEN"
        self.assert_rejected("must be NOT PROVEN")

    def test_missing_limitation_is_rejected(self) -> None:
        self.evidence["limitations"] = [
            item for item in self.evidence["limitations"] if item["id"] != "handles"
        ]
        self.assert_rejected("missing limitations: handles")

    def test_missing_properties_map_is_rejected(self) -> None:
        del self.evidence["properties"]
        self.assert_rejected("property status map is missing")

    def test_missing_limitations_list_is_rejected(self) -> None:
        del self.evidence["limitations"]
        self.assert_rejected("limitations list is missing")


class FailClosedTests(unittest.TestCase):
    """Exercise bounded failure paths that do not need a real AppContainer."""

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_launch_failure_raises_instead_of_falling_back(self) -> None:
        proof._prototypes()
        missing = str(Path(tempfile.gettempdir()) / "omp-sec01-missing.exe")
        with self.assertRaises(proof.ProofError):
            proof._create_process(
                application=missing,
                command_line=f'"{missing}"',
                current_directory=tempfile.gettempdir(),
                env_values={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")},
                attributes=None,
                job=None,
                suspended=True,
            )

    def test_cleanup_removes_only_the_owned_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            root.mkdir()
            (root / "canary.txt").write_text("fake\n", encoding="utf-8")
            result = proof._cleanup(
                fixture_root=root, profile_name="unused", profile_created=False
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["fixture_removed"])
            self.assertFalse(root.exists())

    def test_cleanup_without_fixture_is_ok(self) -> None:
        result = proof._cleanup(
            fixture_root=None, profile_name="unused", profile_created=False
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["fixture_removed"])


class OwnedProcessCleanupTests(unittest.TestCase):
    """Prove every post-CreateProcess failure path cleans the owned child."""

    def setUp(self) -> None:
        proof._prototypes()
        self.comspec = os.environ.get(
            "ComSpec",
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"),
        )
        self.captured: dict[str, int] = {}
        self.create_calls = 0
        self._original_create = proof.kernel32.CreateProcessW

        def wrapper(*args: object, **kwargs: object) -> object:
            self.create_calls += 1
            ok = self._original_create(*args, **kwargs)
            if ok:
                info = args[-1]._obj  # type: ignore[attr-defined]
                self.captured = {
                    "process": int(info.hProcess),
                    "thread": int(info.hThread),
                    "pid": int(info.dwProcessId),
                }
            return ok

        patcher = mock.patch.object(
            proof.kernel32, "CreateProcessW", side_effect=wrapper
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, *, job: int | None = None) -> dict[str, object]:
        return proof._create_process(
            application=self.comspec,
            command_line=f'"{self.comspec}" /d /c ping -n 30 127.0.0.1 >nul',
            current_directory=os.path.join(
                os.environ.get("SystemRoot", r"C:\Windows"), "System32"
            ),
            env_values={
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "ComSpec": self.comspec,
                "PATH": r"C:\Windows\System32;C:\Windows",
            },
            attributes=None,
            job=job,
            suspended=True,
        )

    def _handle_open(self, handle: int) -> bool:
        flags = wintypes.DWORD()
        return bool(
            proof.kernel32.GetHandleInformation(
                wintypes.HANDLE(handle), ctypes.byref(flags)
            )
        )

    def _assert_process_terminated(self, pid: int) -> None:
        handle = proof.kernel32.OpenProcess(
            proof.SYNCHRONIZE | proof.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return
        try:
            self.assertEqual(
                proof.kernel32.WaitForSingleObject(handle, 0),
                proof.WAIT_OBJECT_0,
                "owned child is still alive after the failure path",
            )
        finally:
            proof.kernel32.CloseHandle(handle)

    def _assert_cleanup(self) -> None:
        self.assertEqual(self.create_calls, 1, "proof must not retry or fall back")
        self.assertTrue(self.captured, "CreateProcessW did not record the owned child")
        self.assertFalse(
            self._handle_open(self.captured["thread"]),
            "owned thread handle was not closed",
        )
        self.assertFalse(
            self._handle_open(self.captured["process"]),
            "owned process handle was not closed",
        )
        self._assert_process_terminated(self.captured["pid"])

    def _pid_alive(self, pid: int) -> bool:
        """Return true only when the process object exists and is not signaled."""
        if not pid:
            return False
        handle = proof.kernel32.OpenProcess(
            proof.SYNCHRONIZE | proof.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            return proof.kernel32.WaitForSingleObject(handle, 0) != proof.WAIT_OBJECT_0
        finally:
            proof.kernel32.CloseHandle(handle)

    def _observe_then_cleanup_independently(self) -> None:
        """Record the failure state, then clean up without reusing proof handles.

        Observations are taken before the independent cleanup so a leaked child
        or handle still fails the assertions, while the harness finally block
        guarantees the negative control does not leave an orphan behind.
        """
        process = self.captured.get("process", 0)
        thread = self.captured.get("thread", 0)
        pid = self.captured.get("pid", 0)
        self.observed = {
            "process_handle_open": bool(process) and self._handle_open(process),
            "thread_handle_open": bool(thread) and self._handle_open(thread),
            "child_alive": self._pid_alive(pid),
        }
        if not pid:
            return
        handle = proof.kernel32.OpenProcess(
            proof.PROCESS_TERMINATE
            | proof.SYNCHRONIZE
            | proof.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            return
        try:
            proof.kernel32.TerminateProcess(handle, 1)
            proof.kernel32.WaitForSingleObject(
                handle, proof.OWNED_PROCESS_CLEANUP_TIMEOUT_MS
            )
        finally:
            proof.kernel32.CloseHandle(handle)

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_interrupt_at_native_return_cleans_owned_process(self) -> None:
        """Interrupt after the native call succeeds but before ok/result exist."""

        def interrupt_wrapper(*args: object, **kwargs: object) -> object:
            self.create_calls += 1
            ok = self._original_create(*args, **kwargs)
            info = args[-1]._obj  # type: ignore[attr-defined]
            self.captured = {
                "process": int(info.hProcess or 0),
                "thread": int(info.hThread or 0),
                "pid": int(info.dwProcessId),
            }
            if ok:
                raise KeyboardInterrupt()
            return ok

        proof.kernel32.CreateProcessW.side_effect = interrupt_wrapper
        try:
            with self.assertRaises(KeyboardInterrupt):
                self._create()
        finally:
            self._observe_then_cleanup_independently()
        self.assertEqual(self.create_calls, 1, "proof must not retry or fall back")
        self.assertTrue(self.captured, "CreateProcessW did not record the owned child")
        self.assertFalse(
            self.observed["child_alive"],
            "owned child is still alive after the failure path",
        )
        self.assertFalse(
            self.observed["thread_handle_open"],
            "owned thread handle was not closed",
        )
        self.assertFalse(
            self.observed["process_handle_open"],
            "owned process handle was not closed",
        )

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_keyboard_interrupt_after_create_cleans_owned_process(self) -> None:
        with mock.patch.object(proof, "_token_info", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self._create()
        self._assert_cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_system_exit_after_create_cleans_owned_process(self) -> None:
        with mock.patch.object(proof, "_token_info", side_effect=SystemExit(7)):
            with self.assertRaises(SystemExit):
                self._create()
        self._assert_cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_token_query_failure_cleans_owned_process(self) -> None:
        with mock.patch.object(
            proof, "_token_info", side_effect=proof.ProofError("token query failed")
        ):
            with self.assertRaises(proof.ProofError):
                self._create()
        self._assert_cleanup()

    @unittest.skipUnless(os.name == "nt", "Windows CreateProcessW is required")
    def test_job_attach_failure_cleans_owned_process(self) -> None:
        with mock.patch.object(
            proof, "_token_info", return_value={"is_app_container": True}
        ):
            with mock.patch.object(
                proof.kernel32, "AssignProcessToJobObject", return_value=False
            ):
                with self.assertRaises(proof.ProofError):
                    self._create(job=1)
        self._assert_cleanup()


class CleanupTimeoutPolicyTests(unittest.TestCase):
    """Keep the cleanup-timeout bound portable: no Win32 fixture setup."""

    def test_cleanup_timeout_is_bounded(self) -> None:
        self.assertLessEqual(proof.OWNED_PROCESS_CLEANUP_TIMEOUT_MS, 5000)


class VerifyCommandTests(unittest.TestCase):
    def test_verify_command_accepts_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(valid_evidence()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = proof.main(["--verify", str(path)])
        self.assertEqual(code, 0)
        self.assertIn('"ok": true', output.getvalue())

    def test_verify_command_rejects_invalid_file(self) -> None:
        evidence = valid_evidence()
        evidence["verdict"] = "FAILED"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(evidence), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = proof.main(["--verify", str(path)])
        self.assertEqual(code, 1)
        self.assertIn('"ok": false', output.getvalue())

    def test_verify_command_rejects_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text("{not json", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                code = proof.main(["--verify", str(path)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
