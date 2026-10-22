# ADR-0002: Chuyển trạng thái có đối soát, bằng chứng xác minh và phục hồi crash

- Ngày: 2026-10-21
- Trạng thái: Accepted (triển khai trong REC-01)

## Bối cảnh

Baseline DAT-01 hoàn thiện ranh giới validation và xử lý lỗi CLI/Git, nhưng
cơ chế chuyển trạng thái checkpoint v1 vẫn phân tán:
1. Thiếu guard chuyển trạng thái tập trung; phase có thể bị gán tự do.
2. Chưa gắn chặt kết quả kiểm thử với candidate Git tree cụ thể: nếu test tự
   sửa file candidate hoặc code bị sửa sau test, kết quả PASS cũ vẫn có thể
   dẫn đến commit.
3. Crash sau khi commit nhưng trước khi ghi checkpoint `complete` dễ gây
   commit lặp hoặc không đối soát được tính toàn vẹn của commit đã tạo.
4. No-op task (không có thay đổi file) chưa có ngữ nghĩa tường minh giữa
   `committed` và `no_change`.

## Quyết định

1. **State Machine và Invariant Guard**:
   - Duy trì đồ thị trạng thái tuyến tính bắt buộc:
     `preparing → worker → verifying → committing → complete`.
   - Mọi thay đổi phase phải qua `transition(state, target_phase)`. Bỏ mọi thao
     tác gán phase trực tiếp trong orchestrator.
   - `transition` kiểm tra nghiêm ngặt preconditions (không còn `child_pid`,
     phải có `verification_evidence` hợp lệ trước khi vào `committing`, phải
     có `result_commit` và `completion_kind` trước khi vào `complete`).

2. **Candidate Tree Identity và Staging Semantics**:
   - Dùng file Git index tạm thời (`candidate_<uuid>.index` trong `run_dir`)
     thông qua biến môi trường `GIT_INDEX_FILE` kết hợp `git read-tree HEAD`,
     `git add -A -- <allowed_files>` và `git write-tree`.
   - Dùng chung helper `filter_existing_or_tracked_paths` cho cả bước tính candidate
     tree và bước staging trước commit. Cho phép Worker tạo file mới, sửa file
     hoặc xóa file được phép; các file được cấp quyền nhưng không tồn tại trên
     disk và không tracked sẽ bị bỏ qua an toàn, không làm hỏng pathspec.

3. **Bằng chứng xác minh (Verification Evidence)**:
   - Trong phase `verifying`: kiểm tra scope, chụp `tree_before`, chạy test
     bằng `test_argv`, kiểm tra scope, chụp `tree_after`.
   - Nếu `tree_after != tree_before`: test đã làm bẩn candidate files; bằng
     chứng bị vô hiệu lập tức, raise `OperationError`.
   - Bằng chứng lưu gồm: `candidate_tree` (OID), `command_hash` (SHA-256 canonical
     của `test_argv` và `timeout_seconds` - REC01-R1a), `task_hash` (SHA-256 canonical
     của `task.specification` và `allowed_files_to_modify`), `run_id`, và `exit_code: 0`.

4. **Commit Intent, Boundary Enforcement và đối soát Commit**:
   - Trước khi chuyển sang `committing`, ghi nhận đồng thời cả `verification_evidence`
     (exit_code 0) và `commit_intent` vào checkpoint: `run_id`, `base_commit`,
     `expected_tree`, `commit_marker`. Enforce `expected_tree == candidate_tree`.
   - `transition` và `validate_state` (REC01-R2a): thực hiện validation đầy đủ và
     typed-check cho cả evidence và intent; bắt buộc phải có `commit_intent` hợp lệ
     ở cả hai phase `committing` và `complete`. Không bao giờ tự ý sinh lại intent
     bị thiếu sau phase boundary mà fail-closed để giữ nguyên hiện trạng.
   - Khi commit bình thường (`HEAD == base_commit` - REC01-R5): sau lệnh `git commit`,
     bắt buộc đối soát actual commit vừa tạo trên HEAD (`HEAD^ == base_commit`, commit
     subject == `commit_marker`, `HEAD^{tree} == expected_tree == candidate_tree`).
     Mọi mismatch do hook hay can thiệp bên ngoài đều lập tức raise `OperationError`.
   - Khi resume ở phase `committing`:
     - Kiểm tra toàn vẹn provenance (task_hash, command_hash gồm argv và timeout, run_id).
     - Nếu `HEAD == base_commit`: đối soát checkout tree với evidence; stage
       qua `filter_existing_or_tracked_paths`, kiểm tra staged tree khớp intent,
       commit và đối soát actual commit.
     - Nếu `HEAD != base_commit`: crash sau commit; đối soát HEAD parent,
       commit marker, và tree OID với cả intent VÀ evidence candidate_tree. Nếu
       khớp, hoàn tất idempotent không tạo commit trùng; nếu lệch, fail-closed
       (`OperationError`) để giữ nguyên checkout cho điều tra.
   - No-op: nếu `candidate_tree == base_tree`, test vẫn phải đạt; ghi nhận
     `completion_kind = "no_change"` và `result_commit = base_commit`, không
     tạo commit rỗng.
   - Task thay đổi thật ghi nhận `completion_kind = "committed"`.
   - Khi resume ở phase `complete`: kiểm tra đầy đủ provenance (task_hash,
     command_hash, run_id, intent expected_tree khớp evidence), checkout sạch,
     HEAD và tree khớp với `result_commit`, `base_tree` và evidence.

5. **Schema Checkpoint v2 và Tương thích ngược v1**:
   - Nâng `SCHEMA_VERSION` của checkpoint state lên 2. Config và task schema
     vẫn giữ version 1.
   - `validate_state` hỗ trợ cả schema v1 và v2 (fail-closed với version lạ).
   - `omp status` đọc cả state v1 và v2 ở chế độ read-only, tuyệt đối không
     rewrite checkpoint đĩa.
   - `omp resume` / `advance()` từ chối mọi state v1 vô điều kiện, không cho
     phép legacy v1 đi qua verified-completion path của v2. State v1 chỉ được
     giữ lại để kiểm tra clone khi bắt đầu run mới.

## Hệ quả

- Checkpoint v2 tự bảo vệ trước việc tái sử dụng test PASS cũ cho code mới.
- Không tự ý migrate ngầm dữ liệu v1 dở dang; người dùng cần giữ checkout cũ
  để điều tra và bắt đầu run mới.
- Task này không triển khai crash matrix REC-02, containment OS, sandbox hay
  failover; các bảo đảm giới hạn trong phạm vi Git checkout và logic kiểm soát.
