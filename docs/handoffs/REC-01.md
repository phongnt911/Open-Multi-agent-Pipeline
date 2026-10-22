# Handoff REC-01

- Trạng thái: REVIEW (đã hoàn thành toàn bộ AC1–AC9, khắc phục triệt để findings vòng r1 và ba findings vòng r2 REC01-R1a, REC01-R2a, REC01-R5 từ `docs/reviews/REC-01-r2.md`, sẵn sàng cho điều phối/reviewer nghiệm thu)
- Owner / revision hoặc diff:
  - Owner: AI thực thi REC-01 (Antigravity).
  - Base Git commit: `517f00e092201a08886eb30500cd21e648bc2c4b`.
  - Baseline bắt đầu: Working tree DAT-01 đã được reviewer độc lập ACCEPTED tại `docs/reviews/DAT-01-r3.md` (chưa commit).
  - Đối chiếu 4 mã băm SHA-256 của baseline DAT-01 trước khi thực hiện REC-01 (đã đối soát khớp 100%):
    - `omp_core/main.py`: `e9e493c2f6cf4d06c03ffb6b6563cb5da0b71566f4a0fd71221353d4a25a5d17`
    - `tests/test_validation.py`: `1d9bec19808dba8344b577c926501ef778bb1e024527f2acfda8df1ef31d20f9`
    - `tests/test_cli.py`: `328a400bebdfc386b61848deba50ed9a03d42dd6b598b09bc0c622523e38f22a`
    - `tests/test_pipeline.py`: `eed4b1d34f81c2a25af27c79ea0ad3a68d589185c7afd944844bfa6e066f451a`
  - Các thay đổi ngoài ownership (`docs/FND-*`, `docs/ROADMAP.md`, `docs/COORDINATION.md`, workspace/reviews của DAT-01, các review reports cũ) được bảo toàn nguyên trạng, không stash/reset/clean.

## SHA-256 mới của các file thuộc phạm vi REC-01

| File | SHA-256 | Trạng thái |
| --- | --- | --- |
| `omp_core/main.py` | `30126a216b8955cf1f5716d56841833aea143d5d7501054821995ad0e8df8196` | Modified |
| `tests/test_recovery.py` | `427f5c2123ff763d495c5ac0fdfd26ba3fce22f4a41cb50779dcabf24e4f209c` | Created mới (31 tests) |
| `tests/test_validation.py` | `775af961855ab0f973143dced95b04e8002663c7f79d8148ffc9aca451db722d` | Modified |
| `tests/test_cli.py` | `ce3b80f1e2ebca43f3db02a264451d9df81916d9abe73194752d6d0819ebef6a` | Modified |
| `tests/test_pipeline.py` | `c6a851a8872f24f436a833be681affb3278ff699183f075a860061fda6cc3aaa` | Modified |
| `docs/decisions/0002-recovery-evidence.md` | `298cc89a23004380134611d1f8dc170a8583ae9a17bf0f7724c455bf5cfb3981` | Modified |
| `docs/CONTRACTS.md` | `63738166cb92bae04172dbb83748289ce624851b5573d443e3c63be5cf47ded3` | Modified |
| `docs/SDD.md` | `1eac9aa7213582a568eeb9e405f883933afb0f50498459e3c22b52ae27d7282e` | Modified |
| `README.md` | `70159d471333c009c554fa79b41a9c9303ff8d87f9cf863ad9d0a6caffe2db1b` | Modified |
| `docs/tasks/REC-01.md` | `4aa8383cbb3d1d7174d05eb8fea9b22d3c4759671c113834ea51097a4b4b102b` | Modified |

## Hành vi thay đổi và khắc phục findings

### 1. Khắc phục 3 findings vòng r2 (`docs/reviews/REC-01-r2.md`)

- **REC01-R1a (Verification evidence gắn chặt settings ảnh hưởng kết quả: test_argv & timeout_seconds)**:
  - `compute_command_hash(test_argv, timeout_seconds)` băm canonical cả hai tham số xác thực: lệnh chạy và thời gian timeout cho phép.
  - `check_provenance()` kiểm tra khớp cả `test_argv` và `timeout_seconds` thông qua `compute_command_hash`. Bất kỳ thay đổi cấu hình timeout hay test_argv nào sau khi PASS đều làm mất hiệu lực evidence cũ và bị từ chối chuyển sang `committing` hoặc resume `complete`.
- **REC01-R2a (Validate đầy đủ evidence/intent tại transition và schema checkpoint; không tự sinh intent sau boundary)**:
  - `transition()` sang `committing` và `complete` gọi đầy đủ các hàm kiểm định chuyên biệt `validate_verification_evidence` và `validate_commit_intent`. Chặn chặt chẽ kiểu boolean `exit_code: False` (chỉ chấp nhận số nguyên 0 chính xác thông qua `type(code) is int and code == 0`).
  - Enforce `intent["expected_tree"] == evidence["candidate_tree"]` cả ở `transition()` và `validate_state()`.
  - `validate_state()` bắt buộc phải có `commit_intent` hợp lệ trong cả hai phase `committing` và `complete`.
  - `advance()` loại bỏ hoàn toàn việc tự dựng lại `commit_intent` nếu state bị thiếu sau phase boundary; khi phát hiện thiếu intent hoặc intent không hợp lệ, hệ thống lập tức fail-closed (`OperationError`) để giữ nguyên hiện trạng checkout phục vụ điều tra.
- **REC01-R5 (Đường commit bình thường đối soát actual commit trên HEAD)**:
  - Tại nhánh first-run commit (`head == base_commit`), ngay sau khi chạy `git commit`, `advance()` đọc commit thực tế vừa tạo trên HEAD và đối soát:
    - Parent của HEAD (`HEAD^`) phải bằng `intent["base_commit"]`.
    - Tiêu đề commit (`git log -1 --format=%s`) phải khớp chính xác `intent["commit_marker"]`.
    - Git tree của HEAD (`HEAD^{tree}`) phải khớp với cả `intent["expected_tree"]` VÀ `evidence["candidate_tree"]`.
  - Nếu có Git hook, lọc tự động hoặc can thiệp ngoài làm thay đổi commit thực tế, `advance()` lập tức ném `OperationError("Created commit does not match commit intent or verification evidence")`.

### 2. Bảo toàn các khắc phục từ review r1 (`docs/reviews/REC-01.md`)

- **R1 (Task/Settings Identity & Provenance Binding)**: Trường `task_hash` gắn chặt specification và sorted allowed paths; `command_hash` gắn argv và timeout.
- **R2 (Enforce Intent & Evidence Consistency trước Committing và khi Recovery)**: Ghi đồng thời evidence và intent trước khi chuyển phase; crash recovery đối soát toàn diện commit trên HEAD trước khi công nhận.
- **R3 (Kiểm chứng đầy đủ khi resume Complete và chặn Legacy v1)**: Kiểm chứng checkout sạch, provenance, no_change vs committed; chặn vô điều kiện `schema_version == 1` trong `advance()`.
- **R4 (Đồng nhất quy tắc Staging và Candidate Tree Identity)**: Dùng chung `filter_existing_or_tracked_paths`, hỗ trợ hợp lệ creation, modification, deletion và absent allowed file.

## Tiêu chí nghiệm thu (Acceptance Mapping)

- [x] **AC1 đạt**: Bảng transition (`VALID_TRANSITIONS`) và helper `transition()` được dùng thực tế; transition sang `committing` enforce cả evidence exit_code 0 và matching intent; test fail không bao giờ vào committing hay complete. Kiểm thử tại `TransitionGuardTests`.
- [x] **AC2 đạt**: `compute_candidate_tree()` và staging dùng chung helper `filter_existing_or_tracked_paths`, hỗ trợ tracked modification, allowed new file, tracked deletion và absent allowed file; scope violations bị chặn trước; không làm bẩn repo nguồn. Kiểm thử tại `CandidateTreeIdentityTests`.
- [x] **AC3 đạt**: Verification evidence gắn chặt candidate tree, test command SHA-256 (argv + timeout), task SHA-256, run ID và exit code 0; test tự sửa file làm bằng chứng bị hủy bỏ; sửa task/command/timeout sau verification bị chặn. Kiểm thử tại `VerificationEvidenceTests`.
- [x] **AC4 đạt**: `commit_intent` ghi trước committing; resume sau crash đối soát HEAD parent, marker, expected tree và evidence candidate tree để hoàn tất idempotent, không tạo commit trùng; first-run commit đối soát actual HEAD commit; sai tree/intent bị fail-closed; không tự ý sinh lại intent bị mất. Kiểm thử tại `CommitIntentAndCrashRecoveryTests`.
- [x] **AC5 đạt**: No-op task hoàn tất với `completion_kind = "no_change"` và `result_commit = base_commit`, không tạo empty commit; resume complete kiểm tra sạch, provenance và tree khớp baseline. Kiểm thử tại `NoOpAndResumeCompleteTests`.
- [x] **AC6 đạt**: Checkpoint v2 strict; checkpoint v1 được đọc read-only ở `status`; resume v1 bị chặn trước mutation và giữ nguyên bytes; config/task schema version giữ 1. Kiểm thử tại `SchemaV2AndLegacyV1CompatibilityTests`.
- [x] **AC7 đạt**: Toàn bộ 59 hành vi baseline của DAT-01 giữ nguyên; các thay đổi assertion/fixture đều có giải thích cụ thể và chính đáng; error handling/sanitization giữ nguyên.
- [x] **AC8 đạt**: `python tools/check.py` đạt exit code 0 (90 tests PASS); `git diff --check` đạt exit code 0; fake providers cục bộ, không gọi AI thật/quota.
- [x] **AC9 đạt**: Lập biên bản bàn giao đầy đủ diff, SHA-256, AC mapping, rủi ro; cập nhật ADR `docs/decisions/0002-recovery-evidence.md`.

## Bằng chứng kiểm thử

### 1. Test Suite tổng hợp
- Lệnh: `python tools/check.py`
  - Môi trường: Windows 11 IoT Enterprise LTSC (`10.0.26100.0`), Python 3.12.7, Git 2.56.0.windows.1.
  - Kết quả: `Source checks passed; running 90 tests`, `Ran 90 tests in 54.333s`, `OK`, exit code: `0`.
  - Phân rã 90 tests:
    - 27 validation tests (`tests/test_validation.py`)
    - 18 CLI boundary tests (`tests/test_cli.py`)
    - 6 error classification & redaction tests (`tests/test_errors.py`)
    - 8 pipeline integration tests (`tests/test_pipeline.py`)
    - 31 recovery & transition tests (`tests/test_recovery.py` - bao gồm đầy đủ regression tests cho R1–R4 và R1a, R2a, R5)
- Lệnh: `git diff --check` -> exit code: `0` (không có lỗi khoảng trắng/dòng mới).

### 2. Bằng chứng TDD Regression (Fail trước sửa / Pass sau sửa)
- 4 regression test methods mới bổ sung trong `tests/test_recovery.py`:
  1. `test_modified_timeout_after_verification_blocks_commit_and_complete` (REC01-R1a):
     - Trước sửa: `FAIL` (sửa `timeout_seconds` không làm thay đổi `command_hash`, `advance()` tiến hành commit/complete bình thường).
     - Sau sửa: `PASS` (từ chối ngay lập tức với `OperationError`).
  2. `test_transition_rejects_malformed_evidence_and_missing_or_bad_intent` (REC01-R2a):
     - Trước sửa: `FAIL` (cho phép `exit_code: False` do `False == 0` trong Python, cho phép `expected_tree: None`).
     - Sau sửa: `PASS` (bị chặn tại `transition()` bởi validator chuyên trách).
  3. `test_committing_phase_without_intent_is_rejected_without_reconstruction` (REC01-R2a):
     - Trước sửa: `FAIL` (`advance()` tự tiện tái tạo `commit_intent` khi phát hiện thiếu và commit hoàn tất).
     - Sau sửa: `PASS` (`validate_state` và `advance()` từ chối fail-closed với `OperationError` yêu cầu `commit_intent`).
  4. `test_first_run_commit_verifies_actual_commit_tree_parent_and_marker` (REC01-R5):
     - Trước sửa: `FAIL` (`advance()` ở nhánh `head == base_commit` không kiểm tra commit thực tế trên HEAD sau lệnh commit; hook sửa subject vẫn lọt qua).
     - Sau sửa: `PASS` (`advance()` đối soát HEAD commit thực tế và quăng `OperationError` khi phát hiện mismatch).

## Kiểm thử chưa chạy và nguyên nhân

- Chưa chạy CI matrix remote (Windows/Linux × Python 3.10/3.12): Tuân thủ quy tắc không tự ý commit/push lên GitHub.
- Chưa kiểm tra crash ma trận vật lý, mất điện nguồn đột ngột, atomic write cross-device, và phục hồi clone dở: Thuộc phạm vi của task kế tiếp **REC-02**.
- Chưa triển khai process containment (Job Object Windows, PID recycling guard): Thuộc phạm vi của các task PROC-01 và PROC-02.

## Rủi ro và ghi nhận

- Checkpoint v1 dở dang không được tự động nâng cấp sang v2: Quyết định có chủ đích nhằm fail-closed, tránh suy diễn bằng chứng giả mạo cho các run cũ.
- `command_hash` thay đổi cấu trúc băm (bổ sung `timeout_seconds`): Bất kỳ checkpoint v2 dở dang nào được tạo trước bản sửa này sẽ bị từ chối tái dùng evidence và cần chạy lại từ đầu một cách an toàn.

## Bước tiếp theo

- Đề xuất reviewer thực hiện nghiệm thu REC-01 vòng r3 theo checklist tại `docs/tasks/REC-01.md` và `docs/reviews/REC-01-r2.md`.
- Sau khi REC-01 được ACCEPTED, tiếp tục chuyển sang task **REC-02** ("Kiểm thử crash ma trận và phục hồi atomic").
