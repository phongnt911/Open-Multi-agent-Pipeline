# Bàn giao SEC-02-B1b: Layout L2, Trusted Inputs và Offline Artifact Reader

- **Task ID:** `SEC-02-B1b`
- **Trạng thái bàn giao:** **REVIEW** chờ integration; review r5 ACCEPTED trong scope offline ngày 2026-10-29. Không đánh dấu DONE, không đóng Gate G3, không mở B1c/B2.
- **Base commit:** `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`
- **Nhánh thực hiện:** `codex/sec-02-b1b-offline-layout`
- **Môi trường kiểm thử:** Windows 11 Build 26100 AMD64, CPython 3.12.7 (quyết định chủ dự án: B1b chỉ bắt buộc kiểm thử trên Windows Python 3.12).
- **Tổng số test:** **521 tests PASS** (457 baseline + 64 tests mới B1b), 0 failure, 0 error, 0 skip.

---

## 1. Bảng mã băm SHA-256 đúng 6 file ownership

Bảng đã cập nhật sau phần sửa r5 ngày 2026-10-29. Các kết quả 2026-10-28 bên dưới là lịch sử; evidence mới nhất ở phần bổ sung r5 cuối tài liệu.

| File | SHA-256 Checksum | Vai trò |
| --- | --- | --- |
| `omp_core/trusted_layout.py` | `07cb1596b649fd61959f89d31beaf9803a28f96ff2d203c5cb794af9437f254f` | Module triển khai layout L2, env allowlist, manifest schema và artifact reader |
| `tests/test_trusted_layout.py` | `7e7588c94023a0dc54f13131582224c62de2054b74d088aabdb052aeecff3bf3` | Unit, fault, negative controls, finding regressions (R1–R8, R2a–R8b, R3b–R8c, R5d–R8d) và native tests |
| `docs/CONTRACTS.md` | `45fbce8dee1294afb946a7f2176d4f1efb689c7b0d83fae2c553a6910223a620` | Hợp đồng kiến trúc B1b cập nhật theo findings R1–R9, R2a–R9a, R3b–R8c, và R5d, R6c, R8d |
| `docs/decisions/0010-offline-trusted-layout.md` | `9c75955b86aa0a6531c95c4f6bf601341ac58246c03065f83e98c46b848749f4` | Quyết định kiến trúc ADR-0010 cập nhật các quyết định khắc phục |
| `docs/tasks/SEC-02-B1b.md` | `8c5fa3ba8926def919bc45a099bd63ba7c0f36bd4ffac8114de67b919b415a4b` | Đặc tả task, kết quả thực thi và bằng chứng kiểm thử |
| `docs/handoffs/SEC-02-B1b.md` | *(tự tham chiếu — đo sau khi ghi)* | Báo cáo bàn giao nghiệm thu, AC mapping và prompt cho reviewer |

---

## 2. Đối chiếu chi tiết Acceptance Criteria (B1b-1 – B1b-8) và Khắc phục Findings

| Tiêu chí | Nội dung yêu cầu & Khắc phục Findings | Bằng chứng kiểm chứng |
| --- | --- | --- |
| **B1b-1** | Offline & compatibility: API mới chỉ được tests gọi; giữ nguyên `main`, CLI, schema v1/v2; 457 tests baseline giữ assertion, không skip. | `python tools/check.py` đạt **521 tests OK** (457 baseline + 64 tests B1b). Không sửa bất kỳ baseline test nào. Zero diff trên các file protected (`main.py`, `windows_job.py`, `.github`, `tools`, `pyproject.toml`, `AGENTS.md`). |
| **B1b-2** | Owned L2 layout & ACL: Disposable Git repo root, cấu trúc `checkout`, `vault`, `state`, `log`, `tmp`. Protected DACL: user full, container traverse parent, full trên `checkout`/`tmp`, không có quyền trên `vault`/`state`/`log`. **R6, R6a, R6b & R6c**: `_query_dacl` trích xuất đầy đủ record ACE (`type`, `flags`, `mask`, `sid`), kiểm tra exact mask `0x1F01FF` và inheritance flags `3` trên `checkout`/`tmp`. Ancestor traverse DACL exact policy (R6b, R6c): đối soát đúng 2 ACEs, User mask `_FILE_ALL_ACCESS`, flags 0; Container mask `0x1200A0`, flags 0 (từ chối Container có mask `0x1200A9`, Full Access hay inheritance flags trên ancestor). Từ chối fail-closed nếu có extra ACEs (`Everyone` `S-1-1-0`, foreign SID) hay deny ACE. Low label `S-1-16-4096` trên `checkout`. **R5, R5a & R5c**: Cleanup chỉ đánh dấu `_cleaned=True` sau khi đã xác nhận toàn bộ cây thư mục không còn tồn tại trên đĩa (`not root.exists()`). `_reset_dacl_for_deletion` kiểm tra trực tiếp DWORD return code từ `SetNamedSecurityInfoW` (R5c); nếu mã lỗi khác `ERROR_SUCCESS` (0) hoặc rmtree lỗi, `_cleaned` giữ nguyên `False` và raise `TrustedLayoutError` (không báo sạch giả). | `test_create_layout_creates_required_directories`, `test_dacl_and_label_queries_succeed_on_owned_layout`, `test_r6_reparse_fail_closed_and_extra_everyone_ace_rejected`, `test_r6a_dacl_readback_enforces_mask_flags_and_type`, `test_r6b_ancestor_traverse_dacl_enforces_exact_mask_and_flags`, `test_r6c_ancestor_traverse_dacl_exact_mask_0x1200a0_and_reject_0x1200a9`, `test_r5_cleanup_false_success_interrupt_and_secret_redaction`, `test_r5a_exception_chain_sanitization_and_reset_dacl_error`, `test_r5c_native_acl_reset_failure_marks_cleaned_false_and_raises`. |
| **B1b-3** | Environment allowlist: Dựng mapping từ đầu, không rò rỉ env cha, không nhận task env. **R7 & R7a**: Kiểm tra format key Win32 regex `^[A-Za-z_][A-Za-z0-9_]*$`, từ chối ký tự `=`, cấm giá trị chuỗi rỗng; bắt buộc `temp_dir` và `runtime_path` là đường dẫn tuyệt đối, phải tồn tại thực tế và là thư mục (`is_dir()`), cấm chứa dấu chấm phẩy `;` (chặn PATH separator injection), bản thân và mọi tổ tiên không phải reparse point. Worker nhận cờ `-I -P`. **R9 & R9a**: Worker CPython 3.12 thực thi báo cáo flags `sys.flags.isolated == 1`, `sys.flags.safe_path == 1`, không rò rỉ biến nguy hiểm (`PYTHONPATH`, `PYTHONHOME`, `COMSPEC`, `SHELL`) và không rò rỉ biến môi trường bí mật của cha (`fake_secret_leaked == False`). | `EnvironmentAllowlistTests` (7 tests), `test_r7_env_validation_relative_paths_and_bad_keys`, `test_r7a_env_rejects_nonexistent_paths_and_path_injection`, `test_native_l2_layout_and_os_access_denial`. |
| **B1b-4** | Manifest độc lập `omp.sec02.b1b.manifest.v1`: Bind task_id, run_id, nonce, verifier_version, candidate SHA-256, tree OID, algorithm, approved argv/timeout, expected artifact relpath. **R3 & R3b**: Ràng buộc chặt chẽ `tree_algorithm` và độ dài OID (`sha1`: 40, `sha256`: 64). Canonical candidate identity phân giải tự động từ tham số script đầu tiên trong `manifest.approved_argv[1:]`; nếu caller truyền `candidate_relpath`, bắt buộc khớp với canonical candidate identity (từ chối mọi decoy override R3b). **R4 & R4a**: Xuất bản nguyên tử trong vault bằng temp file độc quyền (`os.O_CREAT | os.O_EXCL`), flush, fsync và commit bằng `os.rename` (MoveFileExW không `MOVEFILE_REPLACE_EXISTING`). No-clobber guarantee: từ chối ghi đè khi rival xuất hiện tại commit boundary, bảo toàn 100% bytes đích, dọn dẹp file temp; tái xuất bản cùng nội dung thành công idempotent. Đối soát candidate bytes và argv khi verify. | `ManifestSchemaAndBindingTests` (11 tests), `test_r3_manifest_oid_coupling_and_artifact_schema_strictness`, `test_r3b_candidate_decoy_override_and_missing_execution_bindings_rejected`, `test_r4_publication_race_no_clobber`, `test_r4a_publish_no_clobber_replaces_nothing`. |
| **B1b-5** | Artifact reader: Không import/execute code candidate. **R1**: Truy vấn Job Object OS proof bắt buộc `isinstance(job_process, WindowsJobProcess)`, `job_process.tree_stopped is True`, `type(job_process.active_processes) is int` và `job_process.active_processes == 0`; từ chối duck typing, dummy object (`SimpleNamespace`) và boolean process count. **R2 & R2a**: Neo root identity bằng directory handle (`FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`) không cấp `FILE_SHARE_DELETE`, giữ handle mở liên tục xuyên suốt quá trình mở và đọc artifact (loại bỏ TOCTOU / directory swap race), xác minh normalized final path của root không bị tráo đổi; chặn junction checkout root, ancestor reparse points, leaf symlink/junction, ADS (`:`), traversal. Giới hạn 64 KiB. **R3, R3a & R3b**: Bắt buộc xác minh candidate trên đĩa (tự động phân giải từ `approved_argv[1:]` hoặc `candidate_relpath`), đối soát SHA-256; từ chối decoy override. Bắt buộc đầy đủ execution inputs: `actual_argv`, `actual_timeout`, `actual_tree_oid`, `actual_spec_hash` đều không được None (R3b). Đối soát `actual_timeout` bằng so sánh đẳng thức số dương hữu hạn có kiểu (`actual_timeout == manifest.timeout_seconds`), từ chối NaN, bool, số âm, hoặc timeout bị đổi/hạ thấp. Schema artifact khắt khe: các trường `failures`, `errors` là int `>= 0`, status enum `PASS`/`FAIL`/`success` (yêu cầu 0 failures và 0 errors nếu pass), cấm trường lạ. **R5b & R5d**: Sanitize exception chain trên mọi đường truyền kể cả khi đóng handle thành công (R5d), đồng thời bảo toàn chính xác kiểu ngoại lệ ngắt (`KeyboardInterrupt`/`SystemExit`) với `__context__` an toàn (R5b, R5d). | `ArtifactReaderUnitTests` (9 tests), `test_r1_fake_job_and_duck_typing_rejected`, `test_r2_junction_checkout_root_rejected`, `test_r2a_root_handle_anchored_throughout_read`, `test_r3_manifest_oid_coupling_and_artifact_schema_strictness`, `test_r3a_missing_candidate_invalid_timeout_and_untyped_schema`, `test_r3b_candidate_decoy_override_and_missing_execution_bindings_rejected`, `test_r5b_bearer_and_interrupt_credential_sanitization_and_close_handle_preservation`, `test_r5d_reader_exception_sanitization_matrix_and_type_preservation`. |
| **B1b-6** | Native property: Windows Python 3.12 AppContainer Worker qua `WindowsJobProcess.start(profile)`. Token đọc độc lập: Container SID khớp profile, Low integrity `S-1-16-4096`, 0 capabilities, `in_any_job == True`. Worker đọc/ghi checkout thành công; OS thực sự deny truy cập vault và state canaries (`PermissionError`). **R9 & R9a**: Positive same-user control kiểm chứng trực tiếp trên chính các file `vault_canary` và `state_canary` (đọc, sửa, phục hồi nguyên vẹn và xác nhận SHA-256 trước/sau khớp tuyệt đối). **R9 & R9a**: Marker tiến trình con do chính child tự ghi (`child_ready.txt`); tiến trình con giữ Job sống và chặn reader cho tới khi `terminate_tree()`. Native test khởi tạo Git repo disposable thật (`git init`, tree OID thật từ `git write-tree`, candidate sha256 thật) đối soát với reader. | `TrustedLayoutNativeTests` (3 native tests): `test_native_l2_layout_and_os_access_denial`, `test_native_artifact_reader_with_job_proof`, `test_native_descendant_blocks_reader_until_stopped`. |
| **B1b-7** | Fault & cleanup: Bounded stop (10s), cleanup không che lỗi gốc. **R5, R5a, R5b, R5c & R5d**: Giữ nguyên kiểu ngoại lệ gián đoạn (`KeyboardInterrupt`/`SystemExit`), sanitize đệ quy toàn bộ chuỗi exception (`__cause__` và `__context__`) qua `_sanitize_exception_chain` để làm sạch tokens/passwords/secrets/bearer (`Bearer <credential>`) trong `.args`, `filename`, `filename2`, `strerror` không phụ thuộc test markers (R5b). Reader sanitize exception chain trên mọi đường truyền kể cả khi đóng handle thành công (R5d). `_reset_dacl_for_deletion` kiểm tra DWORD return code từ `SetNamedSecurityInfoW` (R5c); `_cleaned` giữ nguyên `False` nếu gặp lỗi. **R6**: `_is_reparse_point` fail-closed khi gặp `OSError`/`PermissionError`. **R8, R8a, R8b, R8c & R8d**: Delegation harness cho CI High runner: khởi tạo kernel32 trước spawn, giải phóng handle qua `_release_controller_handles` trong `finally`, đối soát khắt khe child test report (tên test, số test, 0 skips kiểu list, 0 errors, 0 failures, child integrity, PID). Domain-specific `raw_evidence` được trích xuất từ các native tests (R8c), runner fail-closed nếu thiếu observations (không fallback PASS giả). Validator kiểm tra per-test schema và typed invariants nghiêm ngặt (R8d): `bool` strict `True`, `capabilities` strict `int 0`, AppContainer SID đúng format, hex hashes 40/64, `artifact_status` `PASS`/`success`, loại bỏ triệt để các mâu thuẫn giữa summary report và raw evidence. Entrypoint CLI native-controller trích xuất danh sách test bất biến trước khi chạy suite. | `FaultAndCleanupTests` (3 tests), `test_r5_cleanup_false_success_interrupt_and_secret_redaction`, `test_r5a_exception_chain_sanitization_and_reset_dacl_error`, `test_r5b_bearer_and_interrupt_credential_sanitization_and_close_handle_preservation`, `test_r5c_native_acl_reset_failure_marks_cleaned_false_and_raises`, `test_r5d_reader_exception_sanitization_matrix_and_type_preservation`, `test_r6_reparse_fail_closed_and_extra_everyone_ace_rejected`, `test_r6c_ancestor_traverse_dacl_exact_mask_0x1200a0_and_reject_0x1200a9`, `test_r8_high_delegation_boundary_validation`, `test_r8a_parent_controller_delegation_validates_raw_evidence_and_test_list`, `test_r8b_native_controller_entrypoint_succeeds`, `test_r8c_controller_delegation_rejects_null_or_unvalidated_raw_evidence`, `test_r8d_controller_delegation_field_by_field_mutation_matrix`. |
| **B1b-8** | Negative controls & reviewer mutant suite: 4 source mutants độc lập trong `.omp/sec02-b1b-r2-run-mutants.py` (tree-stop guard, nonce binding, size bound, candidate hash) đều bị phát hiện bởi assertion (mutant exit 1, caught). Suite `FindingRegressionTests` gồm 24 tests riêng biệt bao phủ toàn diện các findings R1–R8, R2a–R8b, R3b–R8c, và R5d, R6c, R8d. 0 skips. | `NegativeControlTests` (4 mutant tests), `FindingRegressionTests` (24 tests), `.omp/sec02-b1b-r2-run-mutants.py` (4/4 mutants caught). |

---

## 3. Lệnh kiểm thử và kết quả thực tế

### 3.1 Focused Test Suite
```powershell
python -m unittest tests.test_trusted_layout -v
```
**Kết quả:**
- `Ran 64 tests in 3.393s`
- `OK` (Exit code: 0, 0 failure, 0 error, 0 skip).

### 3.2 Reviewer Mutant Suite
```powershell
python .omp/sec02-b1b-r2-run-mutants.py
```
**Kết quả:**
```json
{
  "tree_stop_guard": {
    "original_exit": 0,
    "mutant_exit": 1,
    "mutant_caught_by_assertion": true
  },
  "nonce_binding": {
    "original_exit": 0,
    "mutant_exit": 1,
    "mutant_caught_by_assertion": true
  },
  "size_bound": {
    "original_exit": 0,
    "mutant_exit": 1,
    "mutant_caught_by_assertion": true
  },
  "candidate_hash": {
    "original_exit": 0,
    "mutant_exit": 1,
    "mutant_caught_by_assertion": true
  }
}
```
- Exit code: 0 (toàn bộ 4/4 mutants đều bị assertion phát hiện).

### 3.3 Full Repository Check
```powershell
python tools/check.py
```
**Kết quả:**
- `Source checks passed; running 521 tests`
- `Ran 521 tests in 126.500s`
- `OK` (Exit code: 0, 0 failure, 0 error, 0 skip).
- Toàn bộ 457 tests baseline + 64 tests mới của B1b đều PASS tuyệt đối.

### 3.4 Bytecode Compilation Check
```powershell
python -m py_compile omp_core/trusted_layout.py tests/test_trusted_layout.py
```
**Kết quả:** Exit code: 0.

### 3.5 Git Diff Whitespace & Formatting Check
```powershell
git diff --check
```
**Kết quả:** Exit code: 0.

### 3.6 Protected Files Immutability Check
```powershell
git diff --exit-code 8e33dad -- omp_core/main.py omp_core/windows_job.py .github tools pyproject.toml AGENTS.md
```
**Kết quả:** Exit code: 0 (hoàn toàn không có thay đổi trên các file cốt lõi được bảo vệ).

### 3.7 Baseline Tests Immutability Check
```powershell
git diff --exit-code 8e33dad -- tests/test_appcontainer_process.py tests/test_cli.py tests/test_recovery.py tests/test_task_schema.py tests/test_manifest_schema.py
```
**Kết quả:** Exit code: 0 (toàn bộ 457 baseline tests được giữ nguyên vẹn 100%).

---

## 4. Bảo toàn trạng thái và tài nguyên ngoài scope

1. **Snapshot ban đầu:** Được đối soát đầy đủ với `.omp/sec02-b1b-snapshot.json`.
2. **17 file dirty có sẵn:** Toàn bộ 17 file tracked dirty thuộc các stream trước và các file untracked được giữ nguyên vẹn, không bị xóa, reset hay stash.
3. **Không import tools.proofs:** Cả `omp_core/trusted_layout.py` và `tests/test_trusted_layout.py` đều không import `tools.proofs`.
4. **Không sửa review history:** `docs/reviews/SEC-02-B1b.md`, `docs/reviews/SEC-02-B1b-r2.md`, `docs/reviews/SEC-02-B1b-r3.md`, `docs/reviews/SEC-02-B1b-r4.md` và các file `.omp/sec02-b1b-r*` được bảo toàn nguyên trạng.

---

## 5. Giới hạn và các bước chưa chạy

- **Giới hạn hiện tại:** Module `omp_core/trusted_layout.py` là primitive offline phục vụ verification độc lập, chưa được nối vào CLI hoặc orchestrator `main.py` (nối orchestration thuộc phạm vi task tiếp theo SEC-02-B1c).
- **Checks chưa chạy:** Packaging smoke và CI GitHub Actions exact commit trên Windows Python 3.12 sẽ được nghiệm thu tại bước review tích hợp (integration) sau khi reviewer hoàn tất review bản bàn giao này.
- **Ranh giới:** Không tự đánh dấu `DONE`, không đóng Gate G3, không mở task B1c hoặc B2.

---

## 6. Prompt cho Reviewer độc lập

Chủ dự án hoặc reviewer có thể dùng prompt sau để thực hiện review độc lập vòng r5:

```text
Bạn là Reviewer độc lập cho nhiệm vụ SEC-02-B1b (vòng r5) của Open-Multi-agent-Pipeline.
Base commit: 8e33dadaf257e14bb641a0cf0b0327e7c28f45ef
Nhánh: codex/sec-02-b1b-offline-layout
Tài liệu bàn giao: docs/handoffs/SEC-02-B1b.md và docs/tasks/SEC-02-B1b.md

Quy định phạm vi:
1. Xác minh đúng 6 file ownership:
   - omp_core/trusted_layout.py
   - tests/test_trusted_layout.py
   - docs/CONTRACTS.md
   - docs/decisions/0010-offline-trusted-layout.md
   - docs/tasks/SEC-02-B1b.md
   - docs/handoffs/SEC-02-B1b.md
2. Đối soát mã băm SHA-256 của các file ownership.
3. Chạy các lệnh kiểm thử trên Windows Python 3.12:
   python -m unittest tests.test_trusted_layout -v
   python .omp/sec02-b1b-r2-run-mutants.py
   python tools/check.py
   python -m py_compile omp_core/trusted_layout.py tests/test_trusted_layout.py
   git diff --check
   git diff --exit-code 8e33dad -- omp_core/main.py omp_core/windows_job.py .github tools pyproject.toml AGENTS.md
   git diff --exit-code 8e33dad -- tests/test_appcontainer_process.py tests/test_cli.py tests/test_recovery.py tests/test_task_schema.py tests/test_manifest_schema.py
4. Xác minh khắc phục hoàn tất 3 findings trong docs/reviews/SEC-02-B1b-r4.md:
   - SEC02B1B-R5d: Sanitize exception chain trên mọi đường truyền kể cả khi đóng handle thành công, bảo toàn chính xác kiểu ngoại lệ ngắt (KeyboardInterrupt/SystemExit) với __context__ an toàn.
   - SEC02B1B-R6c: Ancestor container traverse exact mask 0x1200A0 và flags 0; từ chối 0x1200A9, Full access và inheritance flags trên ancestor.
   - SEC02B1B-R8d: Field-by-field mutation matrix của raw evidence: kiểm tra strict bool True, capabilities strict int 0, AppContainer SID format, hex hashes 40/64, artifact_status PASS/success, loại bỏ triệt để các mâu thuẫn giữa summary report và raw evidence.
5. Lập báo cáo review tại docs/reviews/SEC-02-B1b-r5.md. Không tự ý merge, commit hay đóng Gate G3.
```

## Bổ sung điều phối r5 — 2026-10-29

- Trạng thái: REVIEW chờ integration riêng; review r5 ACCEPTED trong scope implementation offline. Owner phần sửa cuối: AI điều phối; Antigravity review nguồn độc lập, chỉ đọc, không sửa chung file.
- File thay đổi trong lượt này: `tests/test_trusted_layout.py`, section B1b của CONTRACTS, ADR-0010, task và handoff này; thêm báo cáo review r5. Module production giữ nguyên bytes. Không sửa các tài liệu/history ngoài scope.
- Lý do: R8d mới chỉ kiểm format, còn nhận newline cuối và identity khác. Thêm 9 mutation subcases (RED cả 9 trước fix), fullmatch và đối soát token SID quan sát/profile SID, Git tree/candidate SHA với approved manifest identity/algorithm; thiếu reference bị refuse. Raw token và artifact status lấy từ observation, không gán hằng PASS.
- High controller thật chạy bản trước sửa cho kết quả full 518 tests/2 errors và focused 61 tests/2 errors. Sửa R8b để đi qua Medium delegation thật; cleanup chỉ không terminate sau wait báo exit. Regression timeout/token-query error vẫn kiểm terminate=True; helper baseline không đổi.
- Kiểm chứng mới: Windows 11 10.0.26100, CPython 3.12.7 tại Python312/python.exe, controller High `S-1-16-12288`; focused 64 OK/3.571s; full cuối 521 OK/101.924s, exit 0, 0 skip (gồm cleanup assertions cuối). 457 baseline + 64 B1b; số method không tăng, thêm subcases vào matrix hiện có.
- `python .omp/sec02-b1b-r2-run-mutants.py`: 4/4 original0/mutant1 caught by assertion; py_compile, git diff --check và protected diff đạt. Audit git-show bytes toàn bộ 13 baseline test files: 0 drift; index rỗng.
- Audit mới `.omp/sec02-b1b-r5-audit.json` đối soát snapshot đầu r5 trên các file git liệt kê, gồm untracked không ignored; không dùng snapshot cũ để suy bảo toàn thời gian trước lượt này. Sau tài liệu cuối chạy lại source_checks; không diễn giải tài liệu 2026-10-28 là evidence cho patch r5.
- Antigravity lần đầu trả response rỗng + denied command không có target; lần đọc-file-only tiếp theo đã xác nhận R8d. Final delta review timeout đã resume đúng conversation `aa2520d8-746f-4bf3-8fb2-dd478b438715`, không khởi tạo review trùng; kết quả cuối: no findings within inspected scope. Archive `e6a924017d384c61`. Đây là static review độc lập, không chạy tests, không xác nhận whole-G3 safety. Mục denied_actions trong lời văn agent mô tả ràng buộc nhiệm vụ, JSON cuối không ghi denial mới.
- Chưa chạy packaging và exact-commit CI của patch r5; Python 3.14 NOT RUN, không phải gate B1b theo quyết định hiện hành. Không commit/push/merge hoặc kích hoạt main/CLI. Không mở B1c/B2, G3 vẫn mở.
- Rủi ro/giới hạn: report references chỉ kiểm mâu thuẫn fixture trusted, không phải authority đối kháng; không claim network/credentials/owner-crash/power-loss hoặc production vault. Hai lượt High lỗi đã kết thúc; không xóa evidence hay kill PID tùy ý. Kiểm chứng native xanh cuối không được dùng để xóa lịch sử lỗi.
- Bước tiếp theo: reviewer kết luận đúng diff và hashes trong audit; sau acceptance chỉ chuẩn bị integration trong phạm vi quyền được cấp. Không tự chuyển DONE khi thiếu evidence/quyền của bước đó.
