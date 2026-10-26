"""Portable contract tests for the SEC-02 B0 integration proof.

These tests are deliberately offline: they exercise the evidence verifier, the
CLI, the bounded-stop helper and the fault-case recorder.  They do not launch a
lowbox child, so they run with any supported CPython on Windows.  The native
property matrix is produced by
``python -m tools.proofs.sec02_integration_windows run``.
"""
from __future__ import annotations

import copy
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any, Mapping
from unittest import mock

from tools.proofs import sec01_windows as sec01
from tools.proofs import sec02_integration_windows as proof

USER_SID = "S-1-5-21-2773687659-1529040039-3189417923-1001"
PROFILE_SID = (
    "S-1-15-2-356967789-1506470598-259493979-2836506689-3454094723-"
    "3915312043-2644612481"
)
MEDIUM = "S-1-16-8192"
LOW = "S-1-16-4096"
SHA256 = "a" * 64


def _runtime(ver: str) -> dict[str, Any]:
    return {
        "executable": f"C:\\fake\\runtime_{ver}\\python.exe",
        "sys_version": f"{ver}.0 (main, fake build)",
        "minor": ver,
        "isolated": 1,
        "safe_path": True,
        "exit_code": 0,
        "timed_out": False,
        "job_member": True,
        "token": {
            "is_app_container": True,
            "user_sid": USER_SID,
            "app_container_sid": PROFILE_SID,
            "integrity_sid": LOW,
            "integrity_level": "low",
            "elevated": False,
            "capability_count": 0,
            "privileges": [
                {"name": "SeChangeNotifyPrivilege", "attributes": "0x00000003"},
            ],
        },
        "env": {
            "fake_secret_key_present": False,
            "fake_secret_visible": False,
            "allowed_value_visible": True,
        },
        "io": {"read_ok": True, "write_ok": True},
        "checkpoint": {
            "saved_before_resume": True,
            "pid_matches": True,
            "ready_absent_before_resume": True,
            "ready_after_resume": True,
        },
        "stdout_marker_visible": True,
    }


def _case(ver: str, layout_name: str) -> dict[str, Any]:
    """One typed per-layout, per-runtime raw case."""
    runtime = _runtime(ver)
    return {
        "status": "PASS",
        "layout": layout_name,
        "runtime": ver,
        "workspace": f"C:\\fake\\{layout_name}\\ws_{ver.replace('.', '_')}",
        "executable": runtime["executable"],
        "sys_version": runtime["sys_version"],
        "isolated": 1,
        "safe_path": True,
        "exit_code": 0,
        "timed_out": False,
        "job_member": True,
        "active_after_resume": 0,
        "token": copy.deepcopy(runtime["token"]),
        "checkpoint": copy.deepcopy(runtime["checkpoint"]),
        "stdout_marker_visible": True,
        "stdio_inheritable": True,
        "handles_closed": True,
        "read_ok": True,
        "write_ok": True,
        "vault_read_denied": True,
        "vault_write_denied": True,
        "state_read_denied": True,
        "state_write_denied": True,
        "fake_secret_key_present": False,
        "fake_secret_visible": False,
        "allowed_value_visible": True,
        "protected_handle": {
            "value": 1234,
            "target": "C:\\fake\\vault\\vault_canary.txt",
            "inheritable": True,
            "usable": False,
            "reported": {"valid": False, "path": "", "error": 6},
        },
        "cleanup_errors": [],
    }


def _layout(kind: str, name: str) -> dict[str, Any]:
    return {
        "workspace_kind": kind,
        "workspace_read_ok": True,
        "workspace_write_ok": True,
        "vault_read_denied": True,
        "vault_write_denied": True,
        "state_read_denied": True,
        "state_write_denied": True,
        "hashes_unchanged": True,
        "security": {
            "dacl": {
                "aces": [
                    {"sid": USER_SID, "mask": "0x1f01ff", "type": "allow"},
                    {"sid": PROFILE_SID, "mask": "0x1f01ff", "type": "allow"},
                ],
                "query": "ok",
                "protected": True,
                "container_ace": True,
                "user_ace": True,
            },
            "label_sid": LOW if name == "L1" else None,
            "label_query": "ok" if name == "L1" else "absent",
            "error": "",
        },
        "per_runtime": {ver: _case(ver, name) for ver in ("3.12", "3.14")},
    }


def _fault_site() -> dict[str, Any]:
    return {
        "reached": True,
        "error": "InjectedFault: injected fault",
        "replacement_blocked": True,
        "elapsed_ms": 25,
        "active_after_cleanup": 0,
        "cleanup_errors": [],
    }


def valid_evidence() -> dict[str, Any]:
    """Build a minimal evidence object that satisfies every B0 property."""
    l1 = _layout("profile", "L1")
    l2 = _layout("run_checkout", "L2")
    evidence: dict[str, Any] = {
        "schema": proof.SCHEMA,
        "verdict": "PASS",
        "generated_utc": "2026-10-26T00:00:00Z",
        "controller": {
            "platform": "Windows-11-10.0.26100-SP0",
            "python_version": "3.12.7",
            "user_sid": USER_SID,
            "integrity_sid": MEDIUM,
            "elevated": False,
            "native": True,
        },
        "profile": {
            "name": "OMP.SEC02.B0.test",
            "sid": PROFILE_SID,
            "folder": "C:\\fake\\profile",
        },
        "runtimes": {
            ver: proof._runtime_section(l2["per_runtime"][ver])
            for ver in ("3.12", "3.14")
        },
        "attributes": {
            "names": ["SECURITY_CAPABILITIES", "JOB_LIST", "HANDLE_LIST"],
            "attach_mode": "at_create",
            "job_handle_inheritable": False,
            "fallback_used": False,
            "job_member_before_resume": True,
        },
        "layout": {
            "selected": "L2",
            "L1": l1,
            "L2": l2,
            "same_user_control": {
                "vault_read_ok": True,
                "vault_write_ok": True,
                "state_read_ok": True,
                "state_write_ok": True,
            },
            "runtimes_observed": ["3.12", "3.14"],
            "canaries": {
                "vault": {"before": SHA256, "after": SHA256},
                "state": {"before": SHA256, "after": SHA256},
            },
        },
        "handles": {
            "allowlist": ["stdin", "stdout", "stderr"],
            "inheritable_flags": {
                "stdin": True,
                "stdout": True,
                "stderr": True,
                "protected": True,
                "job": False,
            },
            "protected_handle_usable": False,
            "fake_secret_visible": False,
            "enabled_privileges": ["SeChangeNotifyPrivilege"],
        },
        "faults": {
            "sites": {
                name: _fault_site()
                for name in ("attribute", "create", "save", "resume", "query")
            },
            "descendant": {
                "ready": True,
                "root_exited": True,
                "active_after_root": 1,
                "stop_ok": True,
                "stop_ms": 40,
                "active_after_stop": 0,
                "cleanup_errors": [],
            },
        },
        "cleanup": {
            "profile_deleted": True,
            "fixture_removed": True,
            "job_closed": True,
            "handles_closed": True,
            "errors": [],
            "stop_deadline_seconds": 5.0,
        },
        "source_hashes": {
            "tools/proofs/sec02_integration_windows.py": SHA256,
            "tools/proofs/sec02_windows.py": SHA256,
            "omp_core/main.py": SHA256,
            "omp_core/windows_job.py": SHA256,
        },
        "not_run": [],
        "not_proven": ["network isolation", "power-loss recovery"],
        "blocked": {},
        "verifier_negative_controls": [
            {"mutation": "missing_runtime", "rejected": True},
            {"mutation": "not_run_property", "rejected": True},
        ],
    }
    evidence["properties"] = proof.compute_properties(evidence)
    return evidence


class EvidenceVerifierTests(unittest.TestCase):
    def test_valid_control(self) -> None:
        self.assertEqual(proof.verify_evidence(valid_evidence()), [])

    def test_missing_runtime_rejected(self) -> None:
        evidence = valid_evidence()
        del evidence["runtimes"]["3.14"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("3.14" in failure for failure in failures), failures)

    def test_not_run_cannot_pass(self) -> None:
        evidence = valid_evidence()
        evidence["properties"]["B0-3"] = "NOT RUN"
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("B0-3" in failure for failure in failures), failures)

    def test_raw_claim_mismatch_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["attributes"]["fallback_used"] = True
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("fallback" in failure.lower() for failure in failures), failures)
        self.assertTrue(any("B0-2" in failure for failure in failures), failures)

    def test_exit_bool_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["runtimes"]["3.12"]["exit_code"] = True
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("exit_code" in failure for failure in failures), failures)

    def test_nonzero_exit_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["runtimes"]["3.14"]["exit_code"] = 1
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("exit_code" in failure for failure in failures), failures)

    def test_timeout_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["runtimes"]["3.12"]["timed_out"] = True
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("time out" in failure for failure in failures), failures)

    def test_fake_readiness_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["runtimes"]["3.14"]["checkpoint"]["ready_after_resume"] = False
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("ready_after_resume" in failure for failure in failures), failures)

    def test_cleanup_unknown_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["cleanup"]["profile_deleted"] = False
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("profile_deleted" in failure for failure in failures), failures)

    def test_verdict_not_pass_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["verdict"] = "BLOCKED"
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("verdict" in failure.lower() for failure in failures), failures)

    def test_blocked_property_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["blocked"]["B0-4"] = "L2 checkout layout not reachable"
        evidence["properties"] = proof.compute_properties(evidence)
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("B0-4" in failure for failure in failures), failures)

    def test_cleanup_error_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["cleanup"]["errors"] = ["Job still has active processes after deadline"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("cleanup" in failure.lower() for failure in failures), failures)

    def test_not_run_list_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["not_run"] = ["native probe"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("not_run" in failure.lower() for failure in failures), failures)

    def test_not_proven_must_be_recorded(self) -> None:
        evidence = valid_evidence()
        evidence["not_proven"] = []
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("not_proven" in failure.lower() for failure in failures), failures)

    def test_malformed_no_traceback(self) -> None:
        cases: list[Any] = [
            None,
            [],
            {"schema": 1},
            {"schema": proof.SCHEMA, "runtimes": "x"},
            {"schema": proof.SCHEMA, "properties": []},
        ]
        for case in cases:
            failures = proof.verify_evidence(case)  # type: ignore[arg-type]
            self.assertIsInstance(failures, list)
            self.assertTrue(failures)

    def test_property_ids_are_fixed(self) -> None:
        self.assertEqual(
            proof.PROPERTY_IDS, ("B0-2", "B0-3", "B0-4", "B0-5", "B0-6", "B0-7")
        )
        evidence = valid_evidence()
        self.assertEqual(set(evidence["properties"]), set(proof.PROPERTY_IDS))

    def test_deep_copy_of_valid_evidence_is_independent(self) -> None:
        first = valid_evidence()
        second = copy.deepcopy(first)
        second["runtimes"]["3.12"]["isolated"] = 0
        self.assertEqual(proof.verify_evidence(first), [])
        self.assertTrue(proof.verify_evidence(second))

    def test_wrong_type_layout_case_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["per_runtime"]["3.12"] = "not-an-object"
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("per_runtime.3.12" in failure for failure in failures), failures
        )

    def test_wrong_type_cleanup_errors_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["per_runtime"]["3.12"]["cleanup_errors"] = ""
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("cleanup_errors" in failure for failure in failures), failures
        )

    def test_layout_case_denial_contradiction_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["per_runtime"]["3.14"]["vault_read_denied"] = False
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("per_runtime.3.14" in failure for failure in failures), failures
        )

    def test_layout_case_token_contradiction_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["per_runtime"]["3.12"]["token"][
            "is_app_container"
        ] = False
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("is_app_container" in failure for failure in failures), failures
        )

    def test_layout_case_protected_handle_contradiction_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["per_runtime"]["3.14"]["protected_handle"][
            "usable"
        ] = True
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("usable" in failure for failure in failures), failures)

    def test_missing_layout_cases_rejected(self) -> None:
        evidence = valid_evidence()
        del evidence["layout"]["L1"]["per_runtime"]
        del evidence["layout"]["L2"]["per_runtime"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("per_runtime" in failure for failure in failures), failures)

    def test_single_missing_layout_case_rejected(self) -> None:
        evidence = valid_evidence()
        del evidence["layout"]["L2"]["per_runtime"]["3.14"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("per_runtime" in failure for failure in failures), failures)

    def test_layout_case_cleanup_error_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["per_runtime"]["3.12"]["cleanup_errors"].append(
            "CloseHandle(stdout) returned FALSE"
        )
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("cleanup_errors" in failure for failure in failures), failures
        )

    def test_descendant_cleanup_error_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["faults"]["descendant"]["cleanup_errors"].append(
            "descendant remained active"
        )
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("descendant cleanup" in failure for failure in failures), failures
        )

    def test_canary_mismatch_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["canaries"]["vault"]["after"] = "b" * 64
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("canaries" in failure for failure in failures), failures)

    def test_summary_case_mismatch_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["runtimes"]["3.14"]["executable"] = "C:\\other\\python.exe"
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("does not match" in failure for failure in failures), failures)

    def test_label_query_failure_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["security"]["label_query"] = "failed"
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("label_query" in failure for failure in failures), failures)

    def test_absent_label_must_be_null(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["security"]["label_sid"] = LOW
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("label_sid" in failure for failure in failures), failures)

    def test_ok_label_must_carry_sid(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["security"]["label_sid"] = None
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("label_sid" in failure for failure in failures), failures)

    def test_successful_low_label_control_accepted(self) -> None:
        evidence = valid_evidence()
        self.assertEqual(evidence["layout"]["L1"]["security"]["label_query"], "ok")
        self.assertEqual(evidence["layout"]["L1"]["security"]["label_sid"], LOW)
        self.assertEqual(proof.verify_evidence(evidence), [])

    def test_unselected_layout_handle_report_contradiction_rejected(self) -> None:
        evidence = valid_evidence()
        protected = evidence["layout"]["L1"]["per_runtime"]["3.12"]["protected_handle"]
        protected["reported"]["valid"] = True
        protected["reported"]["path"] = protected["target"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("does not match the raw Worker report" in failure for failure in failures),
            failures,
        )

    def test_selected_layout_handle_report_contradiction_rejected(self) -> None:
        evidence = valid_evidence()
        protected = evidence["layout"]["L2"]["per_runtime"]["3.14"]["protected_handle"]
        protected["reported"]["valid"] = True
        protected["reported"]["path"] = protected["target"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("does not match the raw Worker report" in failure for failure in failures),
            failures,
        )

    def test_missing_handle_report_fields_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["per_runtime"]["3.12"]["protected_handle"][
            "reported"
        ] = {}
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("reported.valid" in failure for failure in failures), failures
        )

    def test_invalid_handle_report_must_record_error(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["per_runtime"]["3.12"]["protected_handle"][
            "reported"
        ]["error"] = 0
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("must record the failure" in failure for failure in failures), failures
        )

    def test_handle_target_missing_rejected(self) -> None:
        evidence = valid_evidence()
        del evidence["layout"]["L1"]["per_runtime"]["3.14"]["protected_handle"]["target"]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("target" in failure for failure in failures), failures)

    def test_selected_layout_empty_aces_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["security"]["dacl"]["aces"] = []
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("does not match the raw ACE records" in failure for failure in failures),
            failures,
        )

    def test_unselected_layout_empty_aces_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["security"]["dacl"]["aces"] = []
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("does not match the raw ACE records" in failure for failure in failures),
            failures,
        )

    def test_container_ace_must_match_profile_sid(self) -> None:
        evidence = valid_evidence()
        aces = evidence["layout"]["L2"]["security"]["dacl"]["aces"]
        aces[:] = [
            ace
            for ace in aces
            if ace["sid"] != evidence["profile"]["sid"]
        ]
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("container_ace" in failure for failure in failures), failures
        )

    def test_malformed_ace_rejected(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L1"]["security"]["dacl"]["aces"][0]["sid"] = 7
        failures = proof.verify_evidence(evidence)
        self.assertTrue(any("aces[0].sid" in failure for failure in failures), failures)

    def test_dacl_protected_must_be_observed_true(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["security"]["dacl"]["protected"] = False
        failures = proof.verify_evidence(evidence)
        self.assertTrue(
            any("dacl.protected" in failure for failure in failures), failures
        )


class CliCommandTests(unittest.TestCase):
    def _write(self, payload: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        with handle:
            handle.write(payload)
        return Path(handle.name)

    def test_verify_command_accepts_valid_evidence(self) -> None:
        path = self._write(json.dumps(valid_evidence()))
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 0)
        finally:
            path.unlink()

    def test_verify_command_rejects_invalid_evidence(self) -> None:
        evidence = valid_evidence()
        evidence["verdict"] = "FAIL"
        path = self._write(json.dumps(evidence))
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 1)
        finally:
            path.unlink()

    def test_verify_command_rejects_layout_contradiction(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["per_runtime"]["3.14"]["vault_read_denied"] = False
        path = self._write(json.dumps(evidence))
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 1)
        finally:
            path.unlink()

    def test_verify_command_rejects_unselected_raw_handle_contradiction(self) -> None:
        evidence = valid_evidence()
        protected = evidence["layout"]["L1"]["per_runtime"]["3.12"]["protected_handle"]
        protected["reported"]["valid"] = True
        protected["reported"]["path"] = protected["target"]
        path = self._write(json.dumps(evidence))
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 1)
        finally:
            path.unlink()

    def test_verify_command_rejects_empty_ace_claim(self) -> None:
        evidence = valid_evidence()
        evidence["layout"]["L2"]["security"]["dacl"]["aces"] = []
        path = self._write(json.dumps(evidence))
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 1)
        finally:
            path.unlink()

    def test_verify_command_rejects_missing_file(self) -> None:
        self.assertEqual(proof.main(["verify", "missing-b0-evidence-12345.json"]), 1)

    def test_verify_command_rejects_malformed_json(self) -> None:
        path = self._write("{not json")
        try:
            self.assertEqual(proof.main(["verify", str(path)]), 1)
        finally:
            path.unlink()


class EarlySetupFailureTests(unittest.TestCase):
    def test_setup_failure_returns_nonzero_without_hiding_the_cause(self) -> None:
        runtimes = {
            ver: Path(f"C:\\fake\\runtime_{ver}\\python.exe")
            for ver in proof.RUNTIME_KEYS
        }
        with (
            mock.patch.object(proof, "_ensure_prototypes"),
            mock.patch.object(proof.sec02, "find_python_runtimes", return_value=runtimes),
            mock.patch.object(proof.sec01, "_current_user_sid", return_value=(b"", 0)),
            mock.patch.object(proof.sec01, "_sid_string", return_value=USER_SID),
            mock.patch.object(proof.sec01, "_current_token_summary", return_value={}),
            mock.patch.object(
                proof._OwnedFixture,
                "allocate",
                side_effect=proof.B0ProofError("injected setup failure"),
            ),
        ):
            evidence, exit_code = proof.run_probe()
        self.assertEqual(exit_code, 1)
        self.assertEqual(evidence.get("verdict"), "FAIL")
        self.assertIn("injected setup failure", evidence.get("reason", ""))


class BoundedStopTests(unittest.TestCase):
    def test_stop_succeeds_within_deadline(self) -> None:
        state = {"active": 1}

        def active() -> int:
            return state["active"]

        def terminate() -> bool:
            state["active"] = 0
            return True

        result = proof.bounded_stop(active, terminate, deadline_seconds=0.2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["errors"], [])

    def test_terminate_false_is_reported(self) -> None:
        result = proof.bounded_stop(lambda: 0, lambda: False, deadline_seconds=0.1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("FALSE" in error for error in result["errors"]), result)

    def test_active_after_deadline_is_reported(self) -> None:
        result = proof.bounded_stop(lambda: 1, lambda: True, deadline_seconds=0.02)
        self.assertFalse(result["ok"])
        self.assertTrue(any("deadline" in error for error in result["errors"]), result)

    def test_probe_deadline_is_five_seconds(self) -> None:
        self.assertEqual(proof.STOP_DEADLINE_SECONDS, 5.0)
        self.assertEqual(proof.CHILD_TIMEOUT_SECONDS, 10.0)


class FaultCaseTests(unittest.TestCase):
    def _launch(self, stop_state: proof.StopState, failing_site: str):
        def launch(site: str) -> None:
            if stop_state.stopped:
                raise proof.LaunchBlocked("probe stopped after fault")
            if site == failing_site:
                raise proof.InjectedFault(f"injected fault at {site}")

        return launch

    def test_fault_site_reached_and_replacement_blocked(self) -> None:
        stop_state = proof.StopState()
        record = proof.run_fault_case(
            site="save",
            launch=self._launch(stop_state, "save"),
            cleanup=lambda: {"active_after_cleanup": 0, "cleanup_errors": []},
            stop_state=stop_state,
        )
        self.assertTrue(record["reached"], record)
        self.assertTrue(record["replacement_blocked"], record)
        self.assertEqual(record["active_after_cleanup"], 0)
        self.assertEqual(record["cleanup_errors"], [])
        self.assertTrue(stop_state.stopped)

    def test_unreached_site_is_recorded_honestly(self) -> None:
        stop_state = proof.StopState()
        record = proof.run_fault_case(
            site="resume",
            launch=self._launch(stop_state, "none"),
            cleanup=lambda: {"active_after_cleanup": 0, "cleanup_errors": []},
            stop_state=stop_state,
        )
        self.assertFalse(record["reached"], record)

    def test_cleanup_errors_are_preserved(self) -> None:
        stop_state = proof.StopState()
        record = proof.run_fault_case(
            site="query",
            launch=self._launch(stop_state, "query"),
            cleanup=lambda: {
                "active_after_cleanup": 1,
                "cleanup_errors": ["Job still has active processes after deadline"],
            },
            stop_state=stop_state,
        )
        self.assertEqual(record["active_after_cleanup"], 1)
        self.assertTrue(record["cleanup_errors"])

    def test_diagnostic_is_redacted(self) -> None:
        secret = "FAKE-SECRET-VALUE-1234"
        path = "C:\\fake\\vault\\vault_canary.txt"
        stop_state = proof.StopState()

        def launch(site: str) -> None:
            if stop_state.stopped:
                raise proof.LaunchBlocked("stopped")
            raise proof.InjectedFault(f"failed for {secret} at {path}")

        record = proof.run_fault_case(
            site="attribute",
            launch=launch,
            cleanup=lambda: {"active_after_cleanup": 0, "cleanup_errors": []},
            stop_state=stop_state,
            secrets=[secret],
            paths=[path],
        )
        self.assertTrue(record["reached"])
        self.assertNotIn(secret, record["error"])
        self.assertNotIn(path, record["error"])
        self.assertIn("<redacted>", record["error"])


class OwnershipGuardTests(unittest.TestCase):
    """SEC02B0-R2: partial allocation must not leak or fake a clean release."""

    def setUp(self) -> None:
        proof._ensure_prototypes()

    def _assert_closed(self, handle: int) -> None:
        flags = wintypes.DWORD()
        self.assertFalse(
            sec01.kernel32.GetHandleInformation(
                wintypes.HANDLE(handle), ctypes.byref(flags)
            ),
            f"handle {handle} must be closed",
        )

    def test_partial_stdio_duplicate_failure_closes_created_handles(self) -> None:
        created: list[int] = []
        real_create = proof._create_file

        def record(path: str, access: int, creation: int) -> int:
            handle = real_create(path, access, creation)
            created.append(handle)
            return handle

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(proof, "_create_file", side_effect=record), \
                 mock.patch.object(
                     proof,
                     "_duplicate_handle",
                     side_effect=proof.B0ProofError("duplicate failed"),
                 ):
                with self.assertRaises(proof.B0ProofError):
                    proof._open_stdio_handles(Path(td) / "stdio.log")
        self.assertEqual(len(created), 2)
        for handle in created:
            self._assert_closed(handle)

    def test_inherit_failure_closes_all_stdio_handles(self) -> None:
        created: list[int] = []
        real_create = proof._create_file
        real_duplicate = proof._duplicate_handle

        def record(path: str, access: int, creation: int) -> int:
            handle = real_create(path, access, creation)
            created.append(handle)
            return handle

        def record_duplicate(handle: int) -> int:
            duplicated = real_duplicate(handle)
            created.append(duplicated)
            return duplicated

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(proof, "_create_file", side_effect=record), \
                 mock.patch.object(
                     proof, "_duplicate_handle", side_effect=record_duplicate
                 ), \
                 mock.patch.object(
                     proof,
                     "_set_handle_inherit",
                     side_effect=proof.B0ProofError("inherit failed"),
                 ):
                with self.assertRaises(proof.B0ProofError):
                    proof._open_stdio_handles(Path(td) / "stdio.log")
        self.assertEqual(len(created), 3)
        for handle in created:
            self._assert_closed(handle)

    def test_protected_handle_closed_when_inherit_fails(self) -> None:
        created: list[int] = []
        real_create = proof._create_file

        def record(path: str, access: int, creation: int) -> int:
            handle = real_create(path, access, creation)
            created.append(handle)
            return handle

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "vault.txt"
            target.write_text("x", encoding="utf-8")
            with mock.patch.object(proof, "_create_file", side_effect=record), \
                 mock.patch.object(
                     proof,
                     "_set_handle_inherit",
                     side_effect=proof.B0ProofError("inherit failed"),
                 ):
                with self.assertRaises(proof.B0ProofError):
                    proof._open_protected_handle(target)
        self.assertEqual(len(created), 1)
        self._assert_closed(created[0])

    def test_attribute_update_failure_deletes_list(self) -> None:
        deleted: list[bool] = []
        real_delete = sec01.kernel32.DeleteProcThreadAttributeList

        def spy(buffer: Any) -> None:
            deleted.append(True)
            return real_delete(buffer)

        with mock.patch.object(
            sec01.kernel32, "UpdateProcThreadAttribute", return_value=False
        ), mock.patch.object(
            sec01.kernel32, "DeleteProcThreadAttributeList", side_effect=spy
        ):
            with self.assertRaises(proof.B0ProofError):
                proof._combined_attribute_list(0, 0, [])
        self.assertTrue(deleted)

    def test_free_sid_non_null_is_failure(self) -> None:
        fixture = proof._OwnedFixture()
        fixture.container_sid_ptr = 12345
        with mock.patch.object(sec01.advapi32, "FreeSid", return_value=1):
            fixture.cleanup()
        self.assertTrue(
            any("FreeSid" in error for error in fixture.cleanup_errors),
            fixture.cleanup_errors,
        )
        self.assertIsNone(fixture.container_sid_ptr)

    def test_job_close_failure_marks_handles_open(self) -> None:
        fixture = proof._OwnedFixture()
        fixture.job = 1234
        with mock.patch.object(
            proof, "bounded_stop", return_value={"ok": True, "errors": []}
        ), mock.patch.object(sec01.kernel32, "CloseHandle", return_value=False):
            fixture.cleanup()
        self.assertFalse(fixture.handles_closed)
        self.assertIsNone(fixture.job)
        self.assertTrue(
            any("CloseHandle(job)" in error for error in fixture.cleanup_errors),
            fixture.cleanup_errors,
        )

    def test_allocate_partial_failure_removes_profile(self) -> None:
        fixture = proof._OwnedFixture()
        deleted: list[str] = []

        def create_profile(
            name: str, display: str, description: str, caps: Any, count: int,
            sid_ptr: Any,
        ) -> int:
            sid_ptr._obj.value = 0
            return 0

        def delete_profile(name: str) -> int:
            deleted.append(name)
            return 0

        with mock.patch.object(proof, "_ensure_prototypes"), \
             mock.patch.object(sec01, "_sid_string", return_value="S-1-15-2-fake"), \
             mock.patch.object(
                 sec01.userenv,
                 "CreateAppContainerProfile",
                 side_effect=create_profile,
             ), \
             mock.patch.object(
                 sec01.userenv, "GetAppContainerFolderPath", return_value=1
             ), \
             mock.patch.object(sec01.advapi32, "FreeSid", return_value=0), \
             mock.patch.object(
                 sec01.userenv,
                 "DeleteAppContainerProfile",
                 side_effect=delete_profile,
             ):
            with self.assertRaises(proof.B0ProofError):
                fixture.allocate()
        self.assertEqual(len(deleted), 1)
        self.assertIsNone(fixture.profile_name)
        self.assertEqual(fixture.cleanup_errors, [])


class LabelObservationTests(unittest.TestCase):
    """SEC02B0-R3: a failed label query must never look like an absent label."""

    def setUp(self) -> None:
        proof._ensure_prototypes()

    def test_failed_label_query_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "canary.txt"
            path.write_text("x", encoding="utf-8")
            real = sec01.advapi32.GetFileSecurityW

            def fake(
                handle: Any, info: int, descriptor: Any, length: int,
                needed: Any,
            ) -> bool:
                if info & proof.LABEL_SECURITY_INFORMATION:
                    ctypes.set_last_error(5)
                    return False
                return real(handle, info, descriptor, length, needed)

            with mock.patch.object(
                sec01.advapi32, "GetFileSecurityW", side_effect=fake
            ):
                observation = proof._security_observation(
                    path, user_sid=USER_SID, container_sid=PROFILE_SID
                )
        self.assertEqual(observation["label_query"], "failed")
        self.assertIsNone(observation["label_sid"])
        self.assertTrue(observation["error"])
        self.assertEqual(observation["dacl"]["query"], "ok")

    def test_sacl_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "canary.txt"
            path.write_text("x", encoding="utf-8")
            with mock.patch.object(
                sec01.advapi32, "GetSecurityDescriptorSacl", return_value=False
            ):
                observation = proof._security_observation(
                    path, user_sid=USER_SID, container_sid=PROFILE_SID
                )
        self.assertEqual(observation["label_query"], "failed")
        self.assertTrue(observation["error"])

    def test_successful_no_label_query_is_absent(self) -> None:
        def fake_get(
            handle: Any, info: int, descriptor: Any, length: int, size: Any
        ) -> bool:
            self.assertTrue(info & proof.LABEL_SECURITY_INFORMATION)
            if not descriptor:
                size._obj.value = 20
                ctypes.set_last_error(proof.ERROR_INSUFFICIENT_BUFFER)
                return False
            return True

        def fake_sacl(
            buffer: Any, present: Any, sacl: Any, defaulted: Any
        ) -> bool:
            present._obj.value = 0
            sacl._obj.value = None
            defaulted._obj.value = 0
            return True

        with mock.patch.object(
            sec01.advapi32, "GetFileSecurityW", side_effect=fake_get
        ), mock.patch.object(
            sec01.advapi32, "GetSecurityDescriptorSacl", side_effect=fake_sacl
        ):
            label_sid, status = proof._label_observation(
                Path("C:\\fake\\canary.txt")
            )
        self.assertIsNone(label_sid)
        self.assertEqual(status, "absent")


class RedactionTests(unittest.TestCase):
    def test_redact_removes_secret_and_paths(self) -> None:
        text = "argv=C:\\work\\worker.py secret=TOP-SECRET"
        cleaned = proof.redact(
            text, secrets=["TOP-SECRET"], paths=["C:\\work\\worker.py"]
        )
        self.assertNotIn("TOP-SECRET", cleaned)
        self.assertNotIn("C:\\work\\worker.py", cleaned)

    def test_redact_keeps_safe_text(self) -> None:
        self.assertEqual(proof.redact("InjectedFault: save", [], []), "InjectedFault: save")


class GuardRemovalMutantTests(unittest.TestCase):
    """B0-7: a source copy with one guard removed must stop rejecting evidence.

    The real module rejects forged evidence; the mutant copy accepts the same
    forged evidence, proving the removed guard is load-bearing rather than
    decorative.  A sentinel file outside the probe fixture and the real source
    hash both stay unchanged.
    """

    GUARD = 'if handles.get("protected_handle_usable") is not False:'

    def test_guard_removal_breaks_verifier_and_leaves_source_untouched(self) -> None:
        source_path = Path(proof.__file__)
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        source = source_path.read_text(encoding="utf-8")
        self.assertIn(self.GUARD, source)
        mutant_source = source.replace(self.GUARD, "if False:")

        forged = valid_evidence()
        forged["handles"]["protected_handle_usable"] = True
        forged["properties"] = proof.compute_properties(forged)
        self.assertTrue(proof.verify_evidence(forged), "real source must reject forged evidence")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("SENTINEL-UNCHANGED\n", encoding="utf-8")
            mutant_path = root / "sec02_integration_mutant.py"
            mutant_path.write_text(mutant_source, encoding="utf-8")
            spec = importlib.util.spec_from_file_location(
                "sec02_integration_mutant", mutant_path
            )
            self.assertIsNotNone(spec)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            sys.modules["sec02_integration_mutant"] = module
            try:
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                forged["properties"] = module.compute_properties(forged)
                self.assertEqual(
                    module.verify_evidence(forged),
                    [],
                    "removing the handle guard must let forged evidence through",
                )
            finally:
                sys.modules.pop("sec02_integration_mutant", None)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "SENTINEL-UNCHANGED\n")

        self.assertEqual(
            hashlib.sha256(source_path.read_bytes()).hexdigest(),
            source_hash,
            "the mutant must never modify the real source",
        )
        after = valid_evidence()
        after["handles"]["protected_handle_usable"] = True
        after["properties"] = proof.compute_properties(after)
        self.assertTrue(
            proof.verify_evidence(after),
            "fixture sanity: the forged helper is still rejected after the mutant run",
        )


if __name__ == "__main__":
    unittest.main()
