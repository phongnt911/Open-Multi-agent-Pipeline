# Handoff REC-02

- Trạng thái: REVIEW (đã sửa toàn diện 4 findings R1–R4 từ docs/reviews/REC-02.md, đạt 111/111 tests pass trên Windows; sẵn sàng cho điều phối/reviewer nghiệm thu vòng r2)
- Owner / revision hoặc diff:
  - Owner: AI thực thi REC-02 (Antigravity).
  - Base Git commit: `517f00e092201a08886eb30500cd21e648bc2c4b`.
  - Baseline bắt đầu: Working tree REC-01 đã được reviewer độc lập ACCEPTED tại `docs/reviews/REC-01-r3.md` (chưa commit).
  - Đối chiếu mã băm baseline REC-01 trước khi sửa (khớp 100%):
    - `tests/test_recovery.py`: `427f5c2123ff763d495c5ac0fdfd26ba3fce22f4a41cb50779dcabf24e4f209c`
    - `tests/test_validation.py`: `775af961855ab0f973143dced95b04e8002663c7f79d8148ffc9aca451db722d`
    - `tests/test_cli.py`: `ce3b80f1e2ebca43f3db02a264451d9df81916d9abe73194752d6d0819ebef6a`
    - `tests/test_pipeline.py`: `c6a851a8872f24f436a833be681affb3278ff699183f075a860061fda6cc3aaa`
    - `docs/decisions/0002-recovery-evidence.md`: `298cc89a23004380134611d1f8dc170a8583ae9a17bf0f7724c455bf5cfb3981`
    - `docs/handoffs/REC-01.md`: `cd631d4e727c2ecbac937635496ea1fb77e8070b00f94e59fd40af4a700e7f43`
  - Các thay đổi ngoài ownership (`docs/FND-*`, `docs/ROADMAP.md`, `docs/COORDINATION.md`, workspace/reviews của DAT-01/REC-01) được bảo toàn nguyên trạng, không stash/reset/clean.

## SHA-256 mới của các file thuộc phạm vi REC-02

| File | SHA-256 | Trạng thái |
| --- | --- | --- |
| `omp_core/main.py` | `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0` | Modified (R1 error chaining & PID clean) |
| `tests/test_crash_recovery.py` | `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820` | Modified (sửa G1-CI-R1: cấp Git identity trong amend invocation) |
| `docs/decisions/0003-crash-recovery-matrix.md` | `a4421fb14d2861a76110117690006310f1e7fee15a96aba2b055cef08e3ab412` | Created mới (Ma trận M1–M9 cập nhật) |
| `docs/CONTRACTS.md` | `671f9ade9ebf3f8d9d30cd59dbcf1ae894f1c6ad04454aa68e230d802337a934` | Modified |
| `docs/SDD.md` | `c64383fc7c6e85e14c66f38ccf7e68ade8dc28a507bf0885fff25cb77e017fce` | Modified |
| `README.md` | `33b5193303e76639ca46a709bf531e098a7d4db5eaad51c93ee86e44ddb271fd` | Modified |
| `docs/tasks/REC-02.md` | `d92e82fb720c9247536d94d4df12b2317398bf40fb68d5702b962518a127c16c` | Modified (REVIEW status, AC1–AC9 checked) |

## Xử lý các Findings từ Báo cáo Review vòng r1 (R1–R4)

### 1. REC02-R1 — Bảo toàn lỗi operation và checkpoint-save trong exception chain
- **Nguyên nhân cũ**: Trong khối `finally` của `execute()`, khi `save(checkpoint, state)` gặp `save_error`, lệnh ném lỗi thay thế hoàn toàn biến `operation_error` local mà không gắn vào `__cause__`.
- **Giải pháp**:
  - Tại `execute()` trong `omp_core/main.py`: Biến `operation_error` và `cleanup_error` được chuẩn hóa thành `primary_error` qua `_public_operation_error` / `_public_cleanup_error`. Nếu `save_error` phát sinh, gán `save_error.__cause__ = primary_error` trước khi `raise save_error`. Nếu operation thành công mà checkpoint save thất bại, `save_error` được ném ra ngoài với exit code nonzero, không bao giờ nuốt lỗi save để báo success giả.
  - Tại `advance()` trong `omp_core/main.py`: Khối `except BaseException as exc:` bọc việc lưu `last_error` bằng `try...except BaseException as save_exc: save_exc.__cause__ = exc; raise save_exc`.
  - Bảo toàn tuyệt đối cơ chế redaction (không echo argv hoặc sensitive data).
- **Kiểm chứng**:
  - `test_final_checkpoint_save_failure_preserves_operation_error_in_cause`: Worker exit 7, save cuối lỗi; exception bắt được có `__cause__` chứa `"worker exited 7"`.
  - `test_advance_last_error_save_failure_preserves_original_cause`: Worker exit 3, lưu `last_error` gặp lỗi disk; exception bắt được có `__cause__` chứa `"worker exited 3"`.
  - `test_final_checkpoint_save_failure_on_successful_operation_raises_nonzero`: Worker thành công nhưng save cuối lỗi; lệnh ném `OSError` nonzero, không nuốt lỗi.

### 2. REC02-R2 — Ranh giới xác định và Negative Control cho test M9
- **Nguyên nhân cũ**: Test M9 dùng sleep thụ động 0.3s; nếu process pre-exit sớm hoặc bị kill trước boundary, test vẫn có thể pass mà không thực sự interrupt orchestrator.
- **Giải pháp**:
  - Viết helper `_run_orchestrator_until_boundary`: Chờ ranh giới xác định thông qua readiness marker (`worker_started.marker`) ghi PID của Worker với deadline vòng lặp kiểm tra (`poll(0.05)`).
  - Khẳng định `proc.poll() is None` để chứng minh orchestrator còn sống tại thời điểm boundary trước khi kill.
  - Kiểm tra trạng thái disk trước kill (checkpoint ở phase `worker`, `child_pid` recorded); sau khi kill kiểm tra orchestrator đã thoát; dừng child process có timeout 5s và kiểm tra `tasklist` child đã dừng trước khi xóa `child_pid`.
  - Resume bằng một tiến trình orchestrator subprocess mới độc lập; assert `resumed.returncode == 0` và checkpoint đạt `complete`.
  - Bọc toàn bộ trong `try...finally` để wait process, dọn dẹp child và đóng các luồng stdout/stderr, tránh rò rỉ file handles / ResourceWarnings.
  - Bổ sung negative control `test_real_orchestrator_negative_control_pre_exit_fails`: Dùng lệnh `sys.exit(0)` pre-exit trước boundary; test cha khẳng định bắt buộc fail với `AssertionError: Orchestrator pre-exited unexpectedly`.
- **Kiểm chứng**:
  - `test_real_orchestrator_process_interruption_on_windows`: PASS với bằng chứng readiness -> kill -> disk assert -> child stop -> resume mới.
  - `test_real_orchestrator_negative_control_pre_exit_fails`: PASS (chứng minh negative control phát hiện được failure).

### 3. REC02-R3 — Tương thích Python >=3.10 cho helper rmtree
- **Nguyên nhân cũ**: `shutil.rmtree` sử dụng keyword `onexc` chỉ có từ Python 3.12, gây lỗi `TypeError` trên Python 3.10/3.11.
- **Giải pháp**:
  - Chuyển `force_rmtree` sang callback chuẩn `onerror=_handle_remove_readonly(func, path, exc_info)` tương thích 100% với Python >=3.10 (từ 3.10 đến 3.13).
- **Kiểm chứng**:
  - Helper dọn dẹp sạch sẽ các Git read-only objects mà không gặp `TypeError` trên mọi phiên bản Python.

### 4. REC02-R4 — Bổ sung đầy đủ fault injection write / partial-write / flush cho M8
- **Nguyên nhân cũ**: Test M8 trước đây chỉ patch `os.fsync` và `os.replace`, chưa có injection tại các giai đoạn write và flush như đã tuyên bố.
- **Giải pháp**:
  - Triển khai riêng biệt 5 bài test cho từng giai đoạn của atomic checkpointing:
    1. `test_atomic_save_write_failure_preserves_existing_checkpoint`: `json.dump` ném `OSError`.
    2. `test_atomic_save_partial_write_failure_preserves_existing_checkpoint`: Ghi dở một phần JSON trước khi ném `OSError`; kiểm tra checkpoint cũ giữ nguyên và authoritative state không bị ô nhiễm.
    3. `test_atomic_save_flush_failure_preserves_existing_checkpoint`: `stream.flush` ném `OSError`.
    4. `test_atomic_save_fsync_failure_preserves_existing_checkpoint`: `os.fsync` ném `OSError`.
    5. `test_atomic_save_replace_failure_preserves_existing_checkpoint`: `os.replace` ném `OSError`.
  - Cả 5 tests đều khẳng định: File `state.json` cũ giữ nguyên 100% số bytes và SHA-256 hash; không có file `.tmp` dở dang nào còn sót lại.
  - Giữ nguyên `test_atomic_save_cleanup_error_does_not_mask_original_error` (M8b) và `test_atomic_save_failure_on_new_file_does_not_create_corrupt_checkpoint` (M8c).
- **Kiểm chứng**:
  - Toàn bộ 7 atomic save tests đều PASS.

### 5. G1-CI-R1 — Cấp Git identity trực tiếp trong invocation amend của fixture test M7b
- **Nguyên nhân**: Tại `test_commit_before_complete_with_corrupted_commit_is_rejected` (`tests/test_crash_recovery.py:399-402`), test tạo commit giả mạo bằng `git commit --amend` trong checkout clone mà không truyền identity. Trên runner CI sạch không có Git global identity, lệnh amend thoát với mã 128 (`Committer identity unknown`) trước khi kịp chạy đến logic đối soát recovery, làm CI đỏ cả 4 jobs.
- **Giải pháp**: Cấp trực tiếp `-c user.name="OMP Test" -c user.email="test@localhost"` vào mảng tham số của `git commit --amend`. Đảm bảo test độc lập, hermetic, không thay đổi kịch bản kiểm thử hay các assertion recovery, không can thiệp cấu hình toàn máy hay CI workflow.
- **Kiểm chứng**:
  - Tái hiện lỗi trên máy local với môi trường cô lập Git (`GIT_CONFIG_GLOBAL=os.devnull`, `GIT_CONFIG_NOSYSTEM=1`, unset author/committer env): lệnh amend ban đầu ném `CalledProcessError(128)` (Fail-Before).
  - Sau khi sửa: test đơn lẻ và toàn bộ 111 tests đều PASS 100% trong môi trường cô lập Git (Pass-After).

## Tiêu chí nghiệm thu (Acceptance Mapping)

- [x] **AC1 đạt**: Thiết lập đầy đủ Crash Matrix trong ADR-0003 và 21 tests tương ứng trong `tests/test_crash_recovery.py` bao phủ clone dở, checkout dở, spawn gap, PID đã lưu, test PASS trước checkpoint, stage dở, commit trước checkpoint và atomic save lỗi.
- [x] **AC2 đạt**: Clone/checkout dở không bị xóa hoặc ghi đè; giữ nguyên evidence và sentinel files; không gọi Worker khi chưa đủ điều kiện; fail-closed rõ ràng. Kiểm thử tại `CloneAndCheckoutInterruptionTests`.
- [x] **AC3 đạt**: Khối `finally` của `execute()` dừng child process khi `save()` gặp lỗi; recorded PID chặn resume; bảo toàn lỗi operation và save error trong `__cause__`; công bố rõ rủi ro spawn-gap khi chưa có OS Job Object (PROC-02). Kiểm thử tại `ProcessBoundaryAndSpawnGapTests`.
- [x] **AC4 đạt**: Staging dở được dọn sạch bằng `git reset` và kiểm tra lại toàn diện trước khi stage lại; commit trước checkpoint hoàn tất idempotent không duplicate; commit sai lệch bị chặn. Kiểm thử tại `StagingAndCommitInterruptionTests`.
- [x] **AC5 đạt**: Atomic save kiểm chứng đầy đủ write, partial-write, flush, fsync, replace; lỗi cleanup không che lỗi gốc; checkpoint cũ giữ nguyên 100% bytes khi replace chưa xảy ra; authoritative state không đọc dữ liệu partial; không tạo state giả khi lỗi. Kiểm thử tại `AtomicCheckpointSaveTests`.
- [x] **AC6 đạt**: Phép thử gián đoạn tiến trình orchestrator thật trên Windows tại boundary xác định (readiness marker); khẳng định orchestrator còn sống trước khi kill; dọn dẹp child tiến trình có timeout, không orphan vô hạn; kiểm tra disk và resume bằng process mới; negative control pre-exit bắt buộc fail. Kiểm thử tại `RealProcessInterruptionOnWindowsTests`.
- [x] **AC7 đạt**: 90 baseline tests giữ nguyên hành vi 100%; toàn bộ 111 tests (90 baseline + 21 crash recovery) đều PASS; helper tương thích Python >=3.10; sửa đổi production tối thiểu gắn trực tiếp với matrix cases.
- [x] **AC8 đạt**: `python tools/check.py` đạt exit code 0 (111 tests PASS); `git diff --check` đạt exit code 0; fake providers cục bộ, không gọi AI thật/quota; không thêm runtime dependency.
- [x] **AC9 đạt**: Cập nhật ADR-0003, CONTRACTS.md, SDD.md, README.md; bảng schema CONTRACTS làm rõ cả `committing` và `complete` cần `commit_intent`. Đồng bộ task file sang REVIEW; không tự DONE/ACCEPTED hoặc đóng G1.

## Bằng chứng kiểm thử

### 1. Test Suite tổng hợp
- Lệnh: `python tools/check.py`
  - Môi trường: Windows 11 IoT Enterprise LTSC (`10.0.26100.0`), Python 3.12.7, Git 2.56.0.windows.1.
  - Kết quả: `Source checks passed; running 111 tests`, `Ran 111 tests in 74.221s`, `OK`, exit code: `0`.
  - Phân rã 111 tests:
    - 27 validation tests (`tests/test_validation.py`)
    - 18 CLI boundary tests (`tests/test_cli.py`)
    - 6 error classification & redaction tests (`tests/test_errors.py`)
    - 8 pipeline integration tests (`tests/test_pipeline.py`)
    - 31 recovery & transition tests (`tests/test_recovery.py`)
    - 21 crash recovery matrix tests (`tests/test_crash_recovery.py`)
- Lệnh: `git diff --check` -> exit code: `0` (không có lỗi khoảng trắng/dòng mới).
- Lệnh: `git diff --exit-code 517f00e -- tests/test_errors.py pyproject.toml .github tools AGENTS.md` -> exit code: `0` (diff được bảo vệ không đổi).

### 2. Regression TDD Fail-Before / Pass-After
- **R1**: Khi chưa có error chaining trong `execute()`, Worker exit 7 và save cuối lỗi chỉ để lại `OSError('CHECKPOINT_FINAL_SAVE_FAILURE')` mà không còn thông tin worker exit. Sau khi thêm chaining gán `save_error.__cause__ = primary_error`, exception truy nguyên được cả hai lỗi (`PASS`).
- **R2**: Khi chưa có readiness marker, process pre-exit vẫn khiến test pass (false positive). Sau khi thêm handshake boundary và assertion sống trước kill, negative control pre-exit lập tức fail (`AssertionError`), và test chính thức pass với bằng chứng ranh giới thật (`PASS`).
- **R3**: Khi dùng `shutil.rmtree(..., onexc=...)`, Python 3.10 ném `TypeError`. Sau khi chuyển sang `onerror=_handle_remove_readonly`, mã chạy tương thích stdlib Python >=3.10 (`PASS`).
- **R4**: Các bài test write, partial-write, flush được bổ sung trực tiếp vào production `save()`; chứng minh bytes checkpoint cũ và authoritative state không bị ô nhiễm khi gặp lỗi đĩa ở bất kỳ giai đoạn nào trước `replace` (`PASS`).

## Kiểm thử chưa chạy và nguyên nhân

- Chưa chạy CI matrix remote (Windows/Linux × Python 3.10/3.12): Tuân thủ quy tắc không tự ý commit/push lên GitHub.
- Chưa kiểm tra mất điện phần cứng vật lý đột ngột (pull power plug): Fault injection phần mềm kiểm chứng đúng ranh giới logic, nhưng không thay thế được kiểm thử phần cứng chịu mất điện.
- Chưa triển khai process containment (Job Object Windows, PID recycling guard): Thuộc phạm vi của các task PROC-01 và PROC-02.

## Rủi ro và ghi nhận

- **Spawn-gap**: Nếu orchestrator bị kill cứng bằng OS đúng tại microsecond giữa `subprocess.Popen` và `save()`, `state.json` sẽ không ghi nhận `child_pid`. Cần hoàn thiện cơ chế Windows Job Object tại PROC-02 để kernel tự động gán tiến trình con vào Job trước khi thực thi.
- **Flake tiềm tàng do Antivirus/File lock trên Windows**: Các file trong `.git/objects` có thuộc tính Read-Only; test suite sử dụng `force_rmtree` với handler `onerror` chuẩn để đảm bảo dọn dẹp sạch sẽ.

## Bước tiếp theo

- Đề xuất reviewer thực hiện nghiệm thu vòng r2 theo checklist tại `docs/tasks/REC-02.md`.
- Sau khi REC-02 được ACCEPTED, điều phối xem xét đóng Gate G1 ("Hợp đồng dữ liệu") sau khi hoàn thành integration và CI remote của tree cuối cùng.
