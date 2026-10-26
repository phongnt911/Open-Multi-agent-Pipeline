"""Offline tests and native negative controls for the SEC-02 trusted verification proof.

These tests prove that:
1. The evidence verifier strictly rejects reports with missing runtimes (Python 3.14 is mandatory per A2),
   mismatched SIDs, altered hashes, active descendants, fake claims, unexecuted attacks, or contradictory raw fields.
2. The Trusted Oracle independently rejects candidate solutions when descendants are still active in the Job,
   when candidate code bytes or invocation argv/timeout are mutated, when the vault manifest hash has been tampered with,
   or when nonce/task identity mismatches (replay attack).
3. The OwnedSession resource manager cleans up allocated profiles and jobs even on early setup failure,
   strictly checks WinAPI return codes, enforces deadlines, and propagates interrupts without leaking secrets.
"""
from __future__ import annotations

import copy

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
import unittest

from tools.proofs import sec02_windows as proof

USER_SID = "S-1-5-21-2773687659-1529040039-3189417923-1001"
PROFILE_SID = "S-1-15-2-356967789-1506470598-259493979-2836506689-3454094723-3915312043-2644612481"
VALID_SHA256 = "a" * 64
TASK_ID = "TASK-SEC02-VERIFY"
RUN_ID = "RUN-test1234"
NONCE = "NONCE-test1234"
TREE_ID = proof.make_canonical_tree_id(TASK_ID, proof.LEGIT_CANDIDATE_HASH)


def _write_bound_manifest(ws: Path, cand_file: Path, **overrides: Any) -> Path:
    manifest: dict[str, Any] = {
        "task_id": "TASK-1",
        "run_id": "RUN-1",
        "nonce": "NONCE-1",
        "tree_id": "TREE-1",
        "candidate_hash": proof._file_hash(cand_file),
        "approved_argv_pattern": ["python.exe", "-I", "-P", cand_file.name],
        "approved_timeout_seconds": 10.0,
        "spec": {"a": 40, "b": 2, "expected": 42},
    }
    manifest.update(overrides)
    path = ws / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _bound_identity(manifest_file: Path, cand_file: Path) -> dict[str, Any]:
    return {
        "task_id": "TASK-1",
        "run_id": "RUN-1",
        "nonce": "NONCE-1",
        "tree_id": "TREE-1",
        "expected_result": 42,
        "spec": {"a": 40, "b": 2, "expected": 42},
        "manifest_path": manifest_file,
        "manifest_hash": proof._file_hash(manifest_file),
        "candidate_hash": proof._file_hash(cand_file),
        "approved_argv": ["python.exe", "-I", "-P", str(cand_file)],
        "approved_timeout_seconds": 10.0,
    }


def _worker_token() -> dict[str, Any]:
    return {
        "is_app_container": True,
        "user_sid": USER_SID,
        "app_container_sid": PROFILE_SID,
        "integrity_sid": "S-1-16-4096",
        "integrity_level": "low",
        "elevated": False,
        "privileges": [
            {"name": "SeChangeNotifyPrivilege", "attributes": "0x00000003"},
            {"name": "SeIncreaseWorkingSetPrivilege", "attributes": "0x00000000"},
        ],
        "capability_count": 0,
    }


def _denials() -> dict[str, Any]:
    assets = {}
    for name in proof.CHECK_NAMES:
        assets[name] = {
            "path": f"C:\\fake\\{name}\\{name}_canary.txt",
            "read_denied": True,
            "read_exception": "PermissionError",
            "write_denied": True,
            "write_exception": "PermissionError",
            "hash_before": VALID_SHA256,
            "hash_after": VALID_SHA256,
            "hash_unchanged": True,
        }
    return {
        "all_denied": True,
        "assets": assets,
    }


def _attacks() -> dict[str, Any]:
    return {
        "tamper_expected": {
            "description": "Attempt to overwrite expected solution in vault",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "write_blocked": True,
            "hash_unchanged": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "result mismatch: expected 42, got 999",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "import_injection": {
            "description": "Drop sitecustomize.py in workspace to intercept runner",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "sitecustomize_ignored": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "result mismatch: expected 42, got 999",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "tamper_runner_config": {
            "description": "Attempt to edit runner config policy",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "write_blocked": True,
            "hash_unchanged": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "result mismatch: expected 42, got 999",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "fake_pass_stdout": {
            "description": "Worker prints PASS on stdout with invalid solution",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "result mismatch: expected 42, got 999",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "early_exit_zero": {
            "description": "Worker exits 0 immediately without writing artifacts",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "Missing solution.json artifact",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "replay_old_results": {
            "description": "Worker writes solution from an earlier run_id/nonce",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "nonce mismatch",
            "attack_detected": True,
            "oracle_fooled": False,
        },
        "worker_monkeypatching": {
            "description": "Worker attempts to monkeypatch testing framework",
            "launch_result": {"exit_code": 0, "timed_out": False},
            "attack_executed": True,
            "oracle_verdict": "FAIL",
            "oracle_reason": "result mismatch: expected 42, got 999",
            "attack_detected": True,
            "oracle_fooled": False,
        },
    }


def _same_user_control() -> dict[str, Any]:
    assets = {}
    for name in proof.CHECK_NAMES:
        assets[name] = {"read_ok": True, "write_ok": True}
    return {
        "all_accessible": True,
        "assets": assets,
    }


def _runtime_evidence() -> dict[str, Any]:
    return {
        "status": "PASS",
        "executable": "C:\\fake\\runtime\\python.exe",
        "legitimate_task": {
            "exit_code": 0,
            "timed_out": False,
            "descendants_active": False,
            "active_processes_after_root": 0,
            "actual_argv": ["C:\\fake\\runtime\\python.exe", "-I", "-P", "C:\\fake\\workspace\\worker_legit.py"],
            "actual_timeout_seconds": 10.0,
            "candidate_hash": proof.LEGIT_CANDIDATE_HASH,
            "token": _worker_token(),
            "solution_exists": True,
            "oracle_passed": True,
            "oracle_reason": "Valid solution artifact verified",
            "solution_payload": {
                "task_id": TASK_ID,
                "run_id": RUN_ID,
                "nonce": NONCE,
                "tree_id": TREE_ID,
                "result": 42,
            },
        },
        "os_denials": _denials(),
        "attacks": _attacks(),
        "same_user_control": _same_user_control(),
    }


def valid_evidence() -> dict[str, Any]:
    return {
        "schema": proof.SCHEMA,
        "verdict": "PASS",
        "principal": {
            "user_sid": USER_SID,
            "integrity_sid": "S-1-16-8192",
            "elevated": False,
        },
        "profile": {
            "name": "OMP.SEC02.Proof.test1234",
            "sid": PROFILE_SID,
            "folder": "C:\\fake\\Packages\\OMP.SEC02.Proof.test1234\\AC",
        },
        "runtimes_tested": {
            "3.12": _runtime_evidence(),
            "3.14": _runtime_evidence(),
        },
        "oracle": {
            "process_separation": True,
            "black_box_evaluation": True,
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "nonce": NONCE,
            "tree_id": TREE_ID,
            "candidate_hash": proof.LEGIT_CANDIDATE_HASH,
            "approved_argv_pattern": ["python.exe", "-I", "-P", "worker_legit.py"],
            "approved_timeout_seconds": 10.0,
            "expected_result": 42,
            "task_identity_bound": True,
            "run_identity_bound": True,
            "manifest_identity_bound": True,
            "manifest_hash": VALID_SHA256,
        },
        "cleanup": {
            "profile_deleted": True,
            "fixture_removed": True,
            "errors": [],
        },
    }


class EvidenceVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = valid_evidence()

    def assert_rejected(self, fragment: str) -> None:
        failures = proof.verify_evidence(self.evidence)
        self.assertTrue(
            any(fragment in failure for failure in failures),
            f"Expected failure containing '{fragment}', but got: {failures}",
        )

    def test_valid_evidence_passes(self) -> None:
        failures = proof.verify_evidence(self.evidence)
        self.assertEqual(failures, [])

    def test_schema_mismatch_is_rejected(self) -> None:
        self.evidence["schema"] = "wrong.schema"
        self.assert_rejected("Invalid schema")

    def test_non_pass_verdict_is_rejected(self) -> None:
        for bad_verdict in ["FAIL", "BLOCKED", "UNSUPPORTED"]:
            with self.subTest(verdict=bad_verdict):
                self.evidence["verdict"] = bad_verdict
                self.assert_rejected("Verdict is not PASS")

    def test_principal_elevation_is_rejected(self) -> None:
        self.evidence["principal"]["elevated"] = True
        self.assert_rejected("Principal must not be elevated")

    def test_principal_non_medium_integrity_is_rejected(self) -> None:
        self.evidence["principal"]["integrity_sid"] = "S-1-16-4096"
        self.assert_rejected("Principal integrity must be medium")

    def test_principal_invalid_sid_is_rejected(self) -> None:
        self.evidence["principal"]["user_sid"] = "INVALID_SID"
        self.assert_rejected("Missing or invalid user_sid")

    def test_profile_invalid_sid_is_rejected(self) -> None:
        self.evidence["profile"]["sid"] = "S-1-5-21-999"
        self.assert_rejected("Missing or invalid AppContainer SID")

    def test_profile_missing_folder_is_rejected(self) -> None:
        self.evidence["profile"]["folder"] = ""
        self.assert_rejected("Missing profile folder")

    def test_python_312_not_pass_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["status"] = "FAIL"
        self.assert_rejected("Python 3.12 test status must be PASS")

    def test_python_312_missing_is_rejected(self) -> None:
        del self.evidence["runtimes_tested"]["3.12"]
        self.assert_rejected("Python 3.12 runtime test is required but missing")

    def test_python_314_missing_is_rejected(self) -> None:
        del self.evidence["runtimes_tested"]["3.14"]
        self.assert_rejected("Python 3.14 runtime test is required but missing")

    def test_python_314_not_run_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.14"] = {
            "status": "NOT RUN",
            "reason": "Host lacks Python 3.14",
        }
        self.assert_rejected("Python 3.14 test status must be PASS")

    def test_python_314_fail_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.14"]["status"] = "FAIL"
        self.assert_rejected("Python 3.14 test status must be PASS")

    def test_worker_not_appcontainer_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["token"]["is_app_container"] = False
        self.assert_rejected("Worker token is not AppContainer")

    def test_worker_not_low_integrity_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["token"]["integrity_sid"] = "S-1-16-8192"
        self.assert_rejected("Worker token integrity must be low")

    def test_worker_capability_count_nonzero_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["token"]["capability_count"] = 1
        self.assert_rejected("capability_count must be 0")

    def test_worker_user_sid_mismatch_principal_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["token"]["user_sid"] = "S-1-5-18"
        self.assert_rejected("does not match principal")

    def test_worker_appcontainer_sid_mismatch_profile_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["token"]["app_container_sid"] = "S-1-15-2-9999"
        self.assert_rejected("does not match profile")

    def test_oracle_failed_legitimate_task_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["oracle_passed"] = False
        self.assert_rejected("legitimate task oracle check failed")

    def test_legitimate_task_exit_code_nonzero_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["exit_code"] = 99
        self.assert_rejected("legitimate task exit_code must be 0")

    def test_legitimate_task_timed_out_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["timed_out"] = True
        self.assert_rejected("legitimate task must not time out")

    def test_legitimate_task_descendants_active_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["descendants_active"] = True
        self.assert_rejected("had active descendants")

    def test_descendants_active_contradiction_is_rejected(self) -> None:
        """active_processes_after_root > 0 but descendants_active is False must be rejected."""
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["active_processes_after_root"] = 1
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["descendants_active"] = False
        self.assert_rejected("contradictory descendant state")

    def test_oracle_nonce_mismatch_payload_is_rejected(self) -> None:
        self.evidence["oracle"]["nonce"] = "DIFFERENT_NONCE"
        self.assert_rejected("payload nonce")

    def test_oracle_tree_id_mismatch_payload_is_rejected(self) -> None:
        self.evidence["oracle"]["tree_id"] = "DIFFERENT_TREE"
        self.assert_rejected("payload tree_id")

    def test_legitimate_payload_result_mismatch_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["solution_payload"]["result"] = 999
        self.assert_rejected("payload result '999' does not match expected '42'")

    def test_read_denial_missing_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"]["vault"]["read_denied"] = False
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["all_denied"] = False
        self.assert_rejected("read not denied for asset 'vault'")

    def test_write_denial_missing_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"]["manifest"]["write_denied"] = False
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["all_denied"] = False
        self.assert_rejected("write not denied for asset 'manifest'")

    def test_hash_mutation_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"]["runner_config"]["hash_unchanged"] = False
        self.assert_rejected("hash changed for asset 'runner_config'")

    def test_hash_after_mismatch_before_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"]["vault"]["hash_after"] = "b" * 64
        self.assert_rejected("hash_before != hash_after")

    def test_hash_not_sha256_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"]["vault"]["hash_before"] = "not_a_sha256"
        self.assert_rejected("not valid SHA-256 digests")

    def test_same_user_control_failure_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["same_user_control"]["all_accessible"] = False
        self.assert_rejected("same-user control failed to access assets")

    def test_empty_attacks_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"] = {}
        self.assert_rejected("attacks dictionary must not be empty")

    def test_missing_required_attack_is_rejected(self) -> None:
        del self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]
        self.assert_rejected("missing required attack: 'tamper_expected'")

    def test_attack_launch_timed_out_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["launch_result"]["timed_out"] = True
        self.assert_rejected("timed out during execution")

    def test_attack_launch_exit_code_99_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["launch_result"]["exit_code"] = 99
        self.assert_rejected("exit_code must be 0")

    def test_attack_not_executed_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["attack_executed"] = False
        self.assert_rejected("was not actually executed")

    def test_attack_verdict_pass_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["oracle_verdict"] = "PASS"
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["oracle_fooled"] = True
        self.assert_rejected("oracle_verdict must be FAIL")

    def test_attack_verdict_pass_fooled_false_contradiction_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["oracle_verdict"] = "PASS"
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["oracle_fooled"] = False
        self.assert_rejected("contradictory: verdict is PASS but oracle_fooled is False")

    def test_tamper_expected_oracle_fooled_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["tamper_expected"]["oracle_fooled"] = True
        self.assert_rejected("oracle was fooled by attack 'tamper_expected'")

    def test_import_injection_not_detected_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["import_injection"]["attack_detected"] = False
        self.assert_rejected("attack 'import_injection' was not detected")

    def test_fake_pass_stdout_oracle_fooled_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["fake_pass_stdout"]["oracle_fooled"] = True
        self.assert_rejected("oracle was fooled by attack 'fake_pass_stdout'")

    def test_early_exit_zero_oracle_fooled_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["early_exit_zero"]["oracle_fooled"] = True
        self.assert_rejected("oracle was fooled by attack 'early_exit_zero'")

    def test_replay_attack_oracle_fooled_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["replay_old_results"]["oracle_fooled"] = True
        self.assert_rejected("oracle was fooled by attack 'replay_old_results'")

    def test_worker_monkeypatching_oracle_fooled_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["worker_monkeypatching"]["oracle_fooled"] = True
        self.assert_rejected("oracle was fooled by attack 'worker_monkeypatching'")

    def test_oracle_missing_process_separation_is_rejected(self) -> None:
        self.evidence["oracle"]["process_separation"] = False
        self.assert_rejected("Oracle must maintain process separation")

    def test_oracle_missing_identity_binding_is_rejected(self) -> None:
        self.evidence["oracle"]["task_identity_bound"] = False
        self.assert_rejected("Oracle must bind task, run, and manifest identity")

    def test_oracle_missing_manifest_hash_is_rejected(self) -> None:
        self.evidence["oracle"]["manifest_hash"] = "invalid_hash"
        self.assert_rejected("Oracle missing valid manifest_hash SHA-256 digest")

    def test_attack_exit_code_nonzero_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["fake_pass_stdout"]["launch_result"]["exit_code"] = 7
        self.assert_rejected("launch exit_code must be 0")

    def test_attack_missing_launch_result_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["fake_pass_stdout"].pop("launch_result")
        self.assert_rejected("missing launch_result dictionary")

    def test_provenance_removed_is_rejected(self) -> None:
        for k in ["candidate_hash", "actual_argv", "actual_timeout_seconds"]:
            self.evidence["runtimes_tested"]["3.12"]["legitimate_task"].pop(k)
        self.assert_rejected("missing or malformed candidate_hash")

    def test_candidate_hash_malformed_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["candidate_hash"] = "not-a-hash"
        self.assert_rejected("missing or malformed candidate_hash")

    def test_actual_argv_unsafe_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["actual_argv"] = ["python.exe", "unsafe.py"]
        self.assert_rejected("actual_argv")

    def test_actual_timeout_unapproved_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["actual_timeout_seconds"] = 999.0
        self.assert_rejected("actual_timeout_seconds")

    def test_oracle_expected_result_tampered_is_rejected(self) -> None:
        self.evidence["oracle"]["expected_result"] = 999
        self.assert_rejected("Oracle expected_result must match approved spec expected value 42")

    def test_oracle_candidate_hash_missing_is_rejected(self) -> None:
        self.evidence["oracle"]["candidate_hash"] = "not-a-hash"
        self.assert_rejected("Oracle missing valid candidate_hash")

    def test_oracle_tree_id_not_canonical_is_rejected(self) -> None:
        self.evidence["oracle"]["tree_id"] = "TREE-non-canonical"
        self.assert_rejected("does not match canonical tree identity")

    def test_cleanup_profile_not_deleted_is_rejected(self) -> None:
        self.evidence["cleanup"]["profile_deleted"] = False
        self.assert_rejected("AppContainer profile was not deleted")

    def test_cleanup_fixture_not_removed_is_rejected(self) -> None:
        self.evidence["cleanup"]["fixture_removed"] = False
        self.assert_rejected("Fixture was not removed")

    def test_cleanup_errors_is_rejected(self) -> None:
        self.evidence["cleanup"]["errors"] = ["Some cleanup failed"]
        self.assert_rejected("Cleanup reported errors")

    def test_actual_argv_code_mode_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["actual_argv"] = [
            "C:\\fake\\runtime\\python.exe", "-c", "print(999)", "-I", "-P", "worker_legit.py",
        ]
        self.assert_rejected("actual_argv")

    def test_actual_argv_unapproved_executable_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["actual_argv"] = [
            "unapproved.exe", "evil.py", "-I", "-P", "worker_legit.py",
        ]
        self.assert_rejected("actual_argv")

    def test_legitimate_exit_code_bool_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["legitimate_task"]["exit_code"] = False
        self.assert_rejected("exit_code must be 0 (strict int)")

    def test_attack_exit_code_bool_is_rejected(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["fake_pass_stdout"]["launch_result"]["exit_code"] = False
        self.assert_rejected("exit_code must be 0 (strict int)")

    def test_missing_approved_oracle_fields_is_rejected(self) -> None:
        self.evidence["oracle"].pop("approved_argv_pattern")
        self.evidence["oracle"].pop("approved_timeout_seconds")
        self.assert_rejected("approved_argv_pattern")
        self.assert_rejected("approved_timeout_seconds")

    def test_null_oracle_is_rejected_without_exception(self) -> None:
        self.evidence["oracle"] = None
        self.assert_rejected("Oracle must be a JSON object")

    def test_null_launch_result_is_rejected_without_exception(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["attacks"]["fake_pass_stdout"]["launch_result"] = None
        self.assert_rejected("missing launch_result dictionary")

    def test_null_denial_assets_is_rejected_without_exception(self) -> None:
        self.evidence["runtimes_tested"]["3.12"]["os_denials"]["assets"] = None
        self.assert_rejected("os_denials.assets must be a JSON object")


class CliCommandTests(unittest.TestCase):
    def test_verify_command_accepts_valid_evidence(self) -> None:
        evidence = valid_evidence()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(evidence))
            f_path = Path(f.name)
        try:
            exit_code = proof.main(["verify", str(f_path)])
            self.assertEqual(exit_code, 0)
        finally:
            f_path.unlink()

    def test_verify_command_rejects_invalid_evidence(self) -> None:
        evidence = valid_evidence()
        evidence["verdict"] = "FAIL"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(evidence))
            f_path = Path(f.name)
        try:
            exit_code = proof.main(["verify", str(f_path)])
            self.assertEqual(exit_code, 1)
        finally:
            f_path.unlink()

    def test_verify_command_rejects_missing_file(self) -> None:
        exit_code = proof.main(["verify", "non_existent_file_12345.json"])
        self.assertEqual(exit_code, 1)

    def test_verify_command_rejects_malformed_json(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{malformed json")
            f_path = Path(f.name)
        try:
            exit_code = proof.main(["verify", str(f_path)])
            self.assertEqual(exit_code, 1)
        finally:
            f_path.unlink()


class NativeTreeAndOracleNegativeControlTests(unittest.TestCase):
    """Negative controls demonstrating fail-closed Oracle and cleanup behavior on Windows."""

    def test_oracle_fails_when_descendants_are_active(self) -> None:
        """When root exits but a descendant is still active in the Job, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1", "result": 42
            }))
            manifest_file = ws / "manifest.json"
            manifest_file.write_text("MANIFEST_CONTENT", encoding="utf-8")
            identity = {
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1",
                "expected_result": 42, "manifest_path": manifest_file,
                "manifest_hash": proof._file_hash(manifest_file),
            }
            worker_result = {
                "exit_code": 0, "timed_out": False, "descendants_active": True, "active_processes_after_root": 1
            }
            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("Descendants still active", reason)

    def test_oracle_fails_when_candidate_bytes_mutated(self) -> None:
        """When candidate script bytes on disk or in worker result are mutated, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cand_file = ws / "worker_legit.py"
            cand_file.write_text("print(42)\n", encoding="utf-8")
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1", "result": 42
            }))
            manifest_file = _write_bound_manifest(ws, cand_file)
            identity = _bound_identity(manifest_file, cand_file)
            # Mutate candidate script on disk before Oracle inspection
            cand_file.write_text("print(999)\n", encoding="utf-8")
            worker_result = {
                "exit_code": 0, "timed_out": False, "descendants_active": False,
                "candidate_hash": identity["candidate_hash"],
                "candidate_file": str(cand_file),
                "actual_argv": ["python.exe", "-I", "-P", str(cand_file)],
                "actual_timeout_seconds": 10.0,
            }
            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("Candidate disk hash mismatch", reason)

    def test_oracle_fails_when_argv_mutated(self) -> None:
        """When invocation argv lacks required flags like -P, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cand_file = ws / "worker_legit.py"
            cand_file.write_text("print(42)\n", encoding="utf-8")
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1", "result": 42
            }))
            manifest_file = _write_bound_manifest(ws, cand_file)
            identity = _bound_identity(manifest_file, cand_file)
            # Missing -P in actual argv
            worker_result = {
                "exit_code": 0, "timed_out": False, "descendants_active": False,
                "candidate_hash": identity["candidate_hash"],
                "candidate_file": str(cand_file),
                "actual_argv": ["python.exe", "-I", str(cand_file)],
            }
            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("Invocation argv mismatch", reason)

    def test_oracle_fails_when_timeout_mutated(self) -> None:
        """When invocation timeout differs from approved timeout, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            cand_file = ws / "worker_legit.py"
            cand_file.write_text("print(42)\n", encoding="utf-8")
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1", "result": 42
            }))
            manifest_file = _write_bound_manifest(ws, cand_file)
            identity = _bound_identity(manifest_file, cand_file)
            worker_result = {
                "exit_code": 0, "timed_out": False, "descendants_active": False,
                "candidate_hash": identity["candidate_hash"],
                "candidate_file": str(cand_file),
                "actual_argv": ["python.exe", "-I", "-P", str(cand_file)],
                "actual_timeout_seconds": 11.0,
            }
            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("Invocation timeout mismatch", reason)

    def test_oracle_fails_when_manifest_is_tampered(self) -> None:
        """When vault manifest is tampered after identity binding, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1", "result": 42
            }))
            manifest_file = ws / "manifest.json"
            manifest_file.write_text("ORIGINAL_MANIFEST", encoding="utf-8")
            orig_hash = proof._file_hash(manifest_file)
            identity = {
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1", "tree_id": "TREE-1",
                "expected_result": 42, "manifest_path": manifest_file,
                "manifest_hash": orig_hash,
            }
            manifest_file.write_text("TAMPERED_MANIFEST", encoding="utf-8")
            worker_result = {"exit_code": 0, "timed_out": False, "descendants_active": False}

            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("Manifest hash mismatch", reason)

    def test_oracle_fails_when_nonce_mismatches_replay(self) -> None:
        """When solution contains replayed stale nonce, Oracle must reject."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "solution.json").write_text(json.dumps({
                "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "STALE_NONCE", "tree_id": "TREE-1", "result": 42
            }))
            cand_file = ws / "worker_legit.py"
            cand_file.write_text("print(42)\n", encoding="utf-8")
            manifest_file = _write_bound_manifest(ws, cand_file, nonce="FRESH_NONCE")
            identity = _bound_identity(manifest_file, cand_file)
            identity["nonce"] = "FRESH_NONCE"
            worker_result = {
                "exit_code": 0, "timed_out": False, "descendants_active": False,
                "candidate_hash": identity["candidate_hash"],
                "candidate_file": str(cand_file),
                "actual_argv": ["python.exe", "-I", "-P", str(cand_file)],
                "actual_timeout_seconds": 10.0,
            }

            passed, reason, _ = proof.verify_solution_artifact(
                workspace=ws, identity=identity, worker_result=worker_result
            )
            self.assertFalse(passed)
            self.assertIn("nonce mismatch", reason)

    def test_run_worker_fails_closed_when_terminate_job_fails(self) -> None:
        """When TerminateJobObject fails on active descendants, run_worker must raise Sec02ProofError."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            script = ws / "worker.py"
            script.write_text("print(42)\n", encoding="utf-8")

            orig_attr = proof.sec01._attribute_list
            orig_env = proof.sec01._environment_values
            orig_launch = proof.sec01._launch
            orig_active = proof.sec01._job_active_processes
            orig_term = proof.kernel32.TerminateJobObject
            orig_del_attr = getattr(proof.kernel32, "DeleteProcThreadAttributeList", None)

            try:
                proof.sec01._attribute_list = lambda sid: (None, None)  # type: ignore[assignment]
                proof.sec01._environment_values = lambda **kw: {}  # type: ignore[assignment]
                proof.sec01._launch = lambda **kw: {"exit_code": 0, "timed_out": False, "command": kw.get("command_line", "")}  # type: ignore[assignment]
                proof.sec01._job_active_processes = lambda job: 1  # type: ignore[assignment]
                proof.kernel32.TerminateJobObject = lambda job, code: 0  # type: ignore[assignment]
                proof.kernel32.DeleteProcThreadAttributeList = lambda attr: None  # type: ignore[assignment]

                with self.assertRaises(proof.Sec02ProofError) as ctx:
                    proof.run_worker_in_appcontainer(
                        python_exe=Path(sys.executable),
                        script_path=script,
                        workspace=ws,
                        profile_folder=ws,
                        container_sid=0,
                        job=12345,
                    )
                self.assertIn("Job termination failed", str(ctx.exception))
            finally:
                proof.sec01._attribute_list = orig_attr
                proof.sec01._environment_values = orig_env
                proof.sec01._launch = orig_launch
                proof.sec01._job_active_processes = orig_active
                proof.kernel32.TerminateJobObject = orig_term
                if orig_del_attr is not None:
                    proof.kernel32.DeleteProcThreadAttributeList = orig_del_attr

    def test_run_worker_fails_closed_when_deadline_exceeded(self) -> None:
        """When descendants remain active past deadline, run_worker must raise Sec02ProofError."""
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            script = ws / "worker.py"
            script.write_text("print(42)\n", encoding="utf-8")

            orig_attr = proof.sec01._attribute_list
            orig_env = proof.sec01._environment_values
            orig_launch = proof.sec01._launch
            orig_active = proof.sec01._job_active_processes
            orig_term = proof.kernel32.TerminateJobObject
            orig_sleep = time.sleep
            orig_mono = time.monotonic

            try:
                proof.sec01._attribute_list = lambda sid: (None, None)  # type: ignore[assignment]
                proof.sec01._environment_values = lambda **kw: {}  # type: ignore[assignment]
                proof.sec01._launch = lambda **kw: {"exit_code": 0, "timed_out": False, "command": kw.get("command_line", "")}  # type: ignore[assignment]
                proof.sec01._job_active_processes = lambda job: 1  # type: ignore[assignment]
                proof.kernel32.TerminateJobObject = lambda job, code: 1  # type: ignore[assignment]
                time.sleep = lambda s: None
                curr_t = [0.0]

                def _step_mono() -> float:
                    curr_t[0] += 6.0
                    return curr_t[0]

                time.monotonic = _step_mono

                with self.assertRaises(proof.Sec02ProofError) as ctx:
                    proof.run_worker_in_appcontainer(
                        python_exe=Path(sys.executable),
                        script_path=script,
                        workspace=ws,
                        profile_folder=ws,
                        container_sid=0,
                        job=12345,
                    )
                self.assertIn("Job termination failed or processes remained active", str(ctx.exception))
            finally:
                proof.sec01._attribute_list = orig_attr
                proof.sec01._environment_values = orig_env
                proof.sec01._launch = orig_launch
                proof.sec01._job_active_processes = orig_active
                proof.kernel32.TerminateJobObject = orig_term
                time.sleep = orig_sleep
                time.monotonic = orig_mono

    def test_owned_session_proves_profile_allocated_before_job_failure(self) -> None:
        """Ensure profile was actually allocated before job failure, and cleanup removes it."""
        session = proof.OwnedSession(prefix="OMP.SEC02.FaultTest.")
        fake_sid_ptr = 999999
        profile_created_name = None
        profile_deleted_name = None

        orig_create_profile = getattr(proof.sec01.userenv, "CreateAppContainerProfile", None)
        orig_get_folder = getattr(proof.sec01.userenv, "GetAppContainerFolderPath", None)
        orig_delete_profile = getattr(proof.sec01.userenv, "DeleteAppContainerProfile", None)
        orig_free_sid = getattr(proof.advapi32, "FreeSid", None)
        orig_create_job = proof.sec01._create_job
        orig_sid_string = getattr(proof.sec01, "_sid_string", None)
        orig_cotask = getattr(proof.sec01.ole32, "CoTaskMemFree", None) if hasattr(proof.sec01, "ole32") else None

        def mock_create_profile(appContainerName: Any, displayName: Any, description: Any, capabilities: Any, capabilityCount: Any, sid: Any) -> int:
            if hasattr(sid, "_obj"):
                sid._obj.value = fake_sid_ptr
            return 0

        def mock_get_folder(appContainerSid: Any, path_ptr: Any) -> int:
            if hasattr(path_ptr, "_obj"):
                path_ptr._obj.value = str(Path(tempfile.gettempdir()))
            return 0

        def mock_delete_profile(appContainerName: Any) -> int:
            nonlocal profile_deleted_name
            profile_deleted_name = appContainerName
            return 0

        def mock_free_sid(sid_ptr: Any) -> int:
            return 0

        def failing_create_job() -> int:
            nonlocal profile_created_name
            profile_created_name = session.profile_name
            self.assertIsNotNone(profile_created_name)
            self.assertIsNotNone(session.container_sid_ptr)
            raise RuntimeError("Injected fault at _create_job")

        try:
            proof.sec01.userenv.CreateAppContainerProfile = mock_create_profile
            proof.sec01.userenv.GetAppContainerFolderPath = mock_get_folder
            proof.sec01.userenv.DeleteAppContainerProfile = mock_delete_profile
            if hasattr(proof.userenv, "DeleteAppContainerProfile"):
                proof.userenv.DeleteAppContainerProfile = mock_delete_profile
            proof.advapi32.FreeSid = mock_free_sid
            if hasattr(proof.sec01, "ole32"):
                proof.sec01.ole32.CoTaskMemFree = lambda p: None
            proof.sec01._sid_string = lambda p: "S-1-15-2-12345"
            proof.sec01._create_job = failing_create_job

            with self.assertRaises(RuntimeError):
                try:
                    session.allocate()
                finally:
                    session.cleanup()

            self.assertIsNotNone(profile_created_name)
            self.assertEqual(profile_deleted_name, profile_created_name)
            self.assertIsNone(session.profile_name)
            self.assertIsNone(session.container_sid_ptr)
            self.assertIsNone(session.job)
        finally:
            if orig_create_profile is not None:
                proof.sec01.userenv.CreateAppContainerProfile = orig_create_profile
            if orig_get_folder is not None:
                proof.sec01.userenv.GetAppContainerFolderPath = orig_get_folder
            if orig_delete_profile is not None:
                proof.sec01.userenv.DeleteAppContainerProfile = orig_delete_profile
            if orig_free_sid is not None:
                proof.advapi32.FreeSid = orig_free_sid
            if orig_sid_string is not None:
                proof.sec01._sid_string = orig_sid_string
            if orig_cotask is not None:
                proof.sec01.ole32.CoTaskMemFree = orig_cotask
            proof.sec01._create_job = orig_create_job
            session.cleanup()

    def test_owned_session_cleanup_checks_api_return_codes(self) -> None:
        """If CloseHandle returns FALSE, cleanup records the failure and preserves reference."""
        session = proof.OwnedSession(prefix="OMP.SEC02.CloseFail.")
        session.job = 12345
        orig_close = proof.kernel32.CloseHandle
        try:
            proof.kernel32.CloseHandle = lambda handle: 0
            session.cleanup()
            self.assertTrue(any("CloseHandle(job) returned FALSE" in err for err in session.cleanup_errors))
            self.assertEqual(session.job, 12345)
        finally:
            proof.kernel32.CloseHandle = orig_close

    def test_owned_session_cleanup_enforces_process_deadline(self) -> None:
        """If processes remain active past deadline, cleanup records error."""
        session = proof.OwnedSession(prefix="OMP.SEC02.DeadFail.")
        session.job = 12345
        orig_active = proof.sec01._job_active_processes
        orig_term = proof.kernel32.TerminateJobObject
        orig_close = proof.kernel32.CloseHandle
        try:
            proof.sec01._job_active_processes = lambda job: 1
            proof.kernel32.TerminateJobObject = lambda job, code: 1
            proof.kernel32.CloseHandle = lambda handle: 1

            orig_sleep = time.sleep
            time.sleep = lambda s: None
            session.cleanup()
            time.sleep = orig_sleep

            self.assertTrue(any("Job still has active processes after deadline" in err for err in session.cleanup_errors))
        finally:
            proof.sec01._job_active_processes = orig_active
            proof.kernel32.TerminateJobObject = orig_term
            proof.kernel32.CloseHandle = orig_close

    def test_run_proof_re_raises_interrupt_without_leaking_secrets(self) -> None:
        """Interrupts (KeyboardInterrupt) after allocation must be re-raised and tokens sanitized."""
        secret_token = "REVIEW_FAKE_TOKEN_ONLY"
        orig_allocate = proof.OwnedSession.allocate
        orig_setup = proof.setup_fixture
        orig_cleanup = proof.OwnedSession.cleanup
        orig_find = proof.find_python_runtimes
        cleanup_called = False

        def mock_allocate(s: proof.OwnedSession) -> None:
            s.profile_name = "OMP.SEC02.InterruptTest.FakeProfile"
            s.container_sid_ptr = 12345
            s.container_sid_str = "S-1-15-2-fake"
            s.profile_folder = Path(tempfile.gettempdir())
            s.job = 12345
            s.fixture_root = Path(tempfile.gettempdir())

        def mock_setup(**kwargs: Any) -> Any:
            raise KeyboardInterrupt(secret_token)

        def tracked_cleanup(s: proof.OwnedSession) -> None:
            nonlocal cleanup_called
            cleanup_called = True
            s.profile_name = None
            s.container_sid_ptr = None
            s.job = None

        try:
            proof.OwnedSession.allocate = mock_allocate  # type: ignore[assignment]
            proof.setup_fixture = mock_setup  # type: ignore[assignment]
            proof.OwnedSession.cleanup = tracked_cleanup  # type: ignore[assignment]
            proof.find_python_runtimes = lambda: {"3.12": Path(sys.executable), "3.14": Path(sys.executable)}  # type: ignore[assignment]

            with self.assertRaises(KeyboardInterrupt) as ctx:
                proof.run_proof()

            self.assertIn(secret_token, str(ctx.exception))
            self.assertTrue(cleanup_called)
        finally:
            proof.OwnedSession.allocate = orig_allocate  # type: ignore[assignment]
            proof.setup_fixture = orig_setup  # type: ignore[assignment]
            proof.OwnedSession.cleanup = orig_cleanup  # type: ignore[assignment]
            proof.find_python_runtimes = orig_find  # type: ignore[assignment]


class ManifestBindingTests(unittest.TestCase):
    """Regression controls for SEC02A-R2c approved-manifest binding."""

    def _verify(self, **manifest_overrides: Any) -> tuple[bool, str, dict[str, Any]]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        ws = Path(td.name)
        cand_file = ws / "worker_legit.py"
        cand_file.write_bytes(proof.LEGIT_CANDIDATE_CODE.encode("utf-8"))
        (ws / "solution.json").write_text(json.dumps({
            "task_id": "TASK-1", "run_id": "RUN-1", "nonce": "NONCE-1",
            "tree_id": "TREE-1", "result": 42,
        }), encoding="utf-8")
        manifest_file = _write_bound_manifest(ws, cand_file, **manifest_overrides)
        identity = _bound_identity(manifest_file, cand_file)
        worker_result = {
            "exit_code": 0,
            "timed_out": False,
            "descendants_active": False,
            "active_processes_after_root": 0,
            "candidate_hash": identity["candidate_hash"],
            "candidate_file": str(cand_file),
            "actual_argv": ["python.exe", "-I", "-P", str(cand_file)],
            "actual_timeout_seconds": 10.0,
        }
        return proof.verify_solution_artifact(
            workspace=ws, identity=identity, worker_result=worker_result
        )

    def test_bound_manifest_control_passes(self) -> None:
        passed, reason, _ = self._verify()
        self.assertTrue(passed, reason)

    def test_each_approved_field_mismatch_is_rejected(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "task_id": {"task_id": "OTHER-TASK"},
            "run_id": {"run_id": "OTHER-RUN"},
            "nonce": {"nonce": "OTHER-NONCE"},
            "approved_timeout_seconds": {"approved_timeout_seconds": 1.0},
            "approved_argv_pattern": {"approved_argv_pattern": ["unsafe.exe", "-c", "print(999)"]},
            "spec": {"spec": {"a": 40, "b": 2, "expected": 999}},
            "candidate_hash": {"candidate_hash": "b" * 64},
        }
        for name, overrides in cases.items():
            with self.subTest(field=name):
                passed, reason, _ = self._verify(**overrides)
                self.assertFalse(passed, f"{name} unexpectedly passed")
                self.assertIn("Manifest", reason)

    def test_validate_manifest_rejects_malformed_approved_fields(self) -> None:
        cand_hash = proof.LEGIT_CANDIDATE_HASH
        tree_id = proof.make_canonical_tree_id("TASK-1", cand_hash)
        valid: dict[str, Any] = {
            "task_id": "TASK-1",
            "run_id": "RUN-1",
            "nonce": "NONCE-1",
            "tree_id": tree_id,
            "candidate_hash": cand_hash,
            "approved_argv_pattern": ["python.exe", "-I", "-P", "worker_legit.py"],
            "approved_timeout_seconds": 10.0,
            "spec": {"a": 40, "b": 2, "expected": 42},
        }
        self.assertIsNotNone(proof._validate_manifest(valid)[0])
        mutations: dict[str, dict[str, Any]] = {
            "missing_nonce": {**valid},
            "bad_tree": {**valid, "tree_id": "TREE-other"},
            "bad_pattern": {**valid, "approved_argv_pattern": ["python.exe", "-c", "print(999)"]},
            "bool_timeout": {**valid, "approved_timeout_seconds": True},
            "bool_spec": {**valid, "spec": {"a": True, "b": 2, "expected": 42}},
        }
        del mutations["missing_nonce"]["nonce"]
        for name, body in mutations.items():
            with self.subTest(case=name):
                manifest, err = proof._validate_manifest(body)
                self.assertIsNone(manifest)
                self.assertTrue(err)

    def test_load_approved_manifest_rejects_malformed_json(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "manifest.json"
        path.write_text("{not json", encoding="utf-8")
        manifest, err = proof._load_approved_manifest(path)
        self.assertIsNone(manifest)
        self.assertIn("Malformed manifest JSON", err)


if __name__ == "__main__":
    unittest.main()
