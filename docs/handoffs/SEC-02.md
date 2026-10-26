# Handoff SEC-02 (Giai đoạn A) — Sau đợt sửa SEC02A-R2c và R4c (Vòng r5)

- Trạng thái: REVIEW (chỉ giai đoạn A; đã sửa SEC02A-R2c và R4c theo docs/reviews/SEC-02-A-r4.md; chưa triển khai production, Gate G3 vẫn mở).
- Base commit: `ea9de5005883d7159352917ae2b43cd4d94243bb`.
- Nhánh làm việc: `codex/sec-02-python-proof`.
- Owner: AI thực thi SEC-02 (Antigravity pair-programmer).

## 1. Danh sách file và SHA-256

| Đường dẫn file | Trạng thái | SHA-256 |
| --- | --- | --- |
| `tools/proofs/sec02_windows.py` | Đã sửa (R2c, R4c) | `49d71d1d80d5601f36994d21b20a9cacef9f9381daf086c561f1e89b4c778eaf` |
| `tests/test_trusted_verification_proof.py` | Đã sửa (88 tests, R2c, R4c) | `a6f92eac494f1a5e430a3012ed86dadb6559e3376e54b7de0719e628c6d6ed69` |
| `docs/decisions/0008-trusted-verification.md` | Đã sửa (R2c, R4c) | `cb9572a4674d97b08a299b7696bb6425ba52436d1f396962c3da05acf88bcd91` |
| `docs/security/THREAT-MODEL.md` | Đã sửa (P18, P19) | `7c8cc854ca9fb00f38d70f7a305eb3bfce181bc272b44092023f2cbaf8686a5c` |
| `docs/tasks/SEC-02.md` | Cập nhật REVIEW, A5/A7/A8 | `7b5ad6c26a2107d3feb57e6ac3b2c7ec3350a76194e5a826b94b6fb786020bb9` |
| `docs/handoffs/SEC-02.md` | Cập nhật (file này) | *(self-reference)* |

*Lưu ý*: Không reset/checkout/clean/stash/stage. Working tree hiện có nhiều file dirty/untracked ngoài sáu ownership; tất cả được giữ nguyên. Review history r1–r4 chỉ đọc, không sửa.

## 2. Chi tiết khắc phục Finding Review SEC02A-R2c và R4c

### SEC02A-R2c (P2): Manifest approved fields quyết định identity và execution inputs
- **Manifest là nguồn approved duy nhất**:
  - Thêm `_validate_manifest` / `_load_approved_manifest` để đọc lại manifest trong vault sau khi fixture chốt hash.
  - Validate đủ `task_id`, `run_id`, `nonce`, canonical `tree_id`, `candidate_hash`, `approved_argv_pattern`, `approved_timeout_seconds`, `spec.a`, `spec.b`, `spec.expected`.
  - Từ chối chuỗi rỗng, hash sai dạng, tree không canonical, pattern không đúng 4 token `[python.exe, -I, -P, <script>.py]`, timeout không dương/hữu hạn, số dạng bool.
- **Dựng identity từ manifest**:
  - `run_proof` dùng `task_id`, `run_id`, `nonce`, `tree_id`, `spec`, `candidate_hash`, `approved_argv_pattern`, `approved_timeout_seconds` từ manifest thay vì hardcode.
  - `test_legitimate_task` ghi `task_input.json` theo `spec.a`/`spec.b`, dùng script và timeout từ approved invocation.
  - Manifest 3.12 và 3.14 phải khớp nhau ngoài `target`; lệch bị FAIL.
- **Đối soát trước khi đọc artifact**:
  - `_manifest_binding_error` so khớp toàn bộ approved fields với identity.
  - `approved_argv_pattern` phải bind đúng executable/script basename và đúng thứ tự `-I -P` của `identity["approved_argv"]`.
  - `spec.expected` phải khớp `identity["expected_result"]`; nếu identity có `spec`, toàn bộ spec phải khớp.
- **Regression tests**:
  - `ManifestBindingTests.test_bound_manifest_control_passes`.
  - `test_each_approved_field_mismatch_is_rejected`: task/run/nonce/timeout/argv/spec/candidate hash.
  - `test_validate_manifest_rejects_malformed_approved_fields` và `test_load_approved_manifest_rejects_malformed_json`.
  - Probe reviewer: `.omp/sec02-r5-fix-probes.py` — control pass; từng mismatch bị reject.
  - Native setup-inconsistency: `.omp/sec02-r5-native-manifest.py` — producer `exit 1`, verdict `FAIL`, verifier failures, cleanup sạch.

### SEC02A-R4c (P2): Verifier kiểm exact typed contract, malformed không traceback
- **Exact argv contract**:
  - `_valid_argv_pattern` / `_approved_argv_matches` yêu cầu đúng 4 token, đúng thứ tự `-I -P`, executable/script basename khớp pattern.
  - Từ chối `-c`, token thừa/thiếu, executable lạ, script khác.
- **Strict exit code**:
  - `_is_zero_exit` chỉ nhận `int` không phải `bool` và bằng `0`; `False` bị reject.
  - Áp dụng cho legitimate task và mọi `launch_result` của attack.
- **Required approved fields**:
  - `oracle.approved_argv_pattern` và `oracle.approved_timeout_seconds` bắt buộc; timeout strict number dương.
  - `actual_timeout_seconds` phải khớp approved timeout.
- **Malformed structure an toàn**:
  - `_as_mapping` guard cho `principal`, `profile`, `oracle`, `runtimes_tested`, `legitimate_task`, `token`, `os_denials.assets`, `same_user_control.assets`, `attacks`, `launch_result`, `solution_payload`, `cleanup`.
  - `verify_evidence` bọc lỗi cấu trúc thành failure `Malformed evidence structure: <Type>`; CLI `verify` không traceback.
- **Regression tests**:
  - `test_actual_argv_code_mode_is_rejected`, `test_actual_argv_unapproved_executable_is_rejected`.
  - `test_legitimate_exit_code_bool_is_rejected`, `test_attack_exit_code_bool_is_rejected`.
  - `test_missing_approved_oracle_fields_is_rejected`.
  - `test_null_oracle_is_rejected_without_exception`, `test_null_launch_result_is_rejected_without_exception`, `test_null_denial_assets_is_rejected_without_exception`.
  - Probe reviewer `.omp/sec02-r5-fix-probes.py`: 7/7 mutations reject, không exception; CLI null oracle exit 1, `traceback=false`.

### A7: Negative control source-copy guard-removal
- Artifact `.omp/sec02-a7-guard-removal.py`:
  - Source hiện tại từ chối manifest sai (`Manifest approved_argv_pattern mismatch`).
  - Bản copy source đã gỡ guard (`_load_approved_manifest` permissive + `_manifest_binding_error` trả rỗng) chấp nhận cùng input — chứng minh guard là load-bearing (regression fail-before/pass-after).
  - Sentinel ngoài fixture giữ nguyên hash.
- Kết quả `.omp/sec02-a7-guard-removal.json`: `ok=true`, cả ba điều kiện đạt.

## 3. Tổng hợp kết quả kiểm thử

Môi trường:
- Windows 11 build `10.0.26100`, AMD64.
- Controller Python 3.12.7 (`C:\Users\POTA\AppData\Local\Programs\Python\Python312\python.exe`).
- Python 3.14.8: `.omp\python-runtimes\cpython-3.14.8-windows-x86_64-none\python.exe`.

### 3.1 Native proof và verifier
```powershell
python -m tools.proofs.sec02_windows run --out .omp/sec02-r5-evidence.json
python -m tools.proofs.sec02_windows verify .omp/sec02-r5-evidence.json
```
- Native proof: exit `0`; CPython 3.12 và 3.14 PASS; 4/4 sensitive assets bị OS từ chối; same-user control thành công; 7/7 attacks executed/detected; cleanup không lỗi.
- Verifier: exit `0`, `VERIFICATION PASSED`.

### 3.2 Probe reviewer sau sửa
```powershell
python .omp/sec02-r5-fix-probes.py
python .omp/sec02-r5-native-manifest.py
python .omp/sec02-a7-guard-removal.py
```
- `.omp/sec02-r5-fix-probes.json`: `fresh_verifier=[]`; argv code mode/extra prefix, exit bool (legit và attack), missing approved fields, null oracle, null launch đều reject; manifest field mismatches đều reject; control pass.
- `.omp/sec02-r5-native-manifest.json`: inconsistent native manifest → exit `1`, verdict `FAIL`, verifier failures, cleanup sạch; null-oracle CLI exit `1`, `traceback=false`.
- `.omp/sec02-a7-guard-removal.json`: `ok=true`.

### 3.3 Focused suite
```powershell
python -m unittest tests.test_trusted_verification_proof -v
.omp\python-runtimes\cpython-3.14.8-windows-x86_64-none\python.exe -m unittest tests.test_trusted_verification_proof -v
```
- Python 3.12.7: **88 tests OK**, `10.161s`, exit `0`, 0 skipped.
- Python 3.14.8: **88 tests OK**, `10.121s`, exit `0`, 0 skipped.

### 3.4 Full suite
```powershell
python tools/check.py
.omp\python-runtimes\cpython-3.14.8-windows-x86_64-none\python.exe tools/check.py
```
- Python 3.12.7: **321 tests OK** (233 baseline + 88 SEC-02), `116.393s`, exit `0`, log `.omp/sec02-r5-full-312.log`.
- Python 3.14.8: **321 tests OK** (233 baseline + 88 SEC-02), `121.944s`, exit `0`, log `.omp/sec02-r5-full-314.log`.

### 3.5 Định dạng và baseline controls
```powershell
git diff --check
git diff --exit-code ea9de50 -- omp_core tools/check.py tools/proofs/sec01_windows.py .github pyproject.toml AGENTS.md tests
```
- Cả hai exit `0`; baseline controls không đổi.

## 4. NOT RUN / NOT PROVEN

1. **Remote CI GitHub Actions**: chưa chạy trên exact revision vì working tree chưa commit/push; local Windows không thay CI Windows 3.12/3.14.
2. **Production integration (`omp_core`)**: giai đoạn A chỉ là proof fixture; runner/vault production thuộc giai đoạn B.
3. **Arbitrary test execution**: chưa chứng minh an toàn cho pytest/plugins bên thứ ba nạp code untrusted vào cùng interpreter.
4. **Owner hard-kill/power-loss**: chưa test crash cấp OS hoặc mất nguồn khi đang giữ AppContainer profile.
5. **Real assets/credentials/network/provider**: chỉ dùng fixture/canary giả; không claim bảo vệ tài sản thật.
6. **Linux/POSIX**: ngoài support policy hiện tại.

## 5. Trạng thái và bước tiếp theo

- Giữ SEC-02 giai đoạn A ở **REVIEW**; không commit/push/merge/DONE, không đóng G3, không giao B.
- Reviewer độc lập cần chạy lại native proof/verify, focused/full trên Windows 3.12 và 3.14, các probe R2c/R4c và artifact A7; chỉ ACCEPTED khi tất cả bằng chứng khớp.
