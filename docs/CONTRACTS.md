# Hợp đồng kiến trúc và dữ liệu

## Hiện trạng so với mục tiêu

DAT-01 đã khóa schema dữ liệu MVP; REC-01 nâng cấp checkpoint lên
`schema_version: 2` có đối soát chuyển trạng thái, bằng chứng kiểm thử
(`verification_evidence`), ý định commit (`commit_intent`) và kết quả no-op
tường minh (`completion_kind`). Config/task vẫn giữ `schema_version: 1`;
input cũ không có version được nhận như legacy v0 và chuẩn hóa trong bộ nhớ.
State v1 cũ được hỗ trợ đọc/status ở chế độ read-only không rewrite đĩa; resume
trên state v1 chưa hoàn tất bị chặn trước mutation. Migration tự động, crash
matrix sâu và process containment thuộc REC-02/PROC.

## Schema dữ liệu REC-01

| Dữ liệu | Version | Required | Optional | Ghi chú |
| --- | --- | --- | --- | --- |
| Config | 1; thiếu version là legacy v0 | `worker_argv`, `test_argv`, `timeout_seconds` | - | Legacy được normalize trong memory; argv nonempty string, không NUL; timeout integer `1..86400` |
| Task | 1; thiếu version là legacy v0 | `specification`, `allowed_files_to_modify` | - | Specification nonempty; allowed path là file path tương đối chính xác dùng `/` |
| State | 2 (hỗ trợ đọc v1 read-only) | `run_id`, `phase`, `base_commit`, `config`, `task` | `child_pid`, `last_error`, `result_commit`, `verification_evidence`, `commit_intent`, `completion_kind` | `committing`/`complete` bắt buộc `verification_evidence` (`candidate_tree`, `command_hash`, `task_hash`, `run_id`, `exit_code: 0`) và `commit_intent` (`run_id`, `base_commit`, `expected_tree`, `commit_marker`); `complete` bắt buộc thêm `result_commit` và `completion_kind`. Bắt buộc `intent.expected_tree == evidence.candidate_tree`. |

- Unknown field bị từ chối ở mọi object config/task/state, kể cả object lồng
  trong state. Không có field retry/budget giả khi chưa có consumer.
- `run_id` khớp `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; không dùng làm đường dẫn tự
  do. Phase chỉ thuộc `preparing`, `worker`, `verifying`, `committing`,
  `complete`.
- Commit ID là full hex OID đúng object format repo: 40 ký tự SHA-1 hoặc 64 ký
  tự SHA-256. Boundary đọc `git rev-parse --show-object-format` (read-only) và
  kiểm tra đúng độ dài; không ép mọi repo là SHA-1. Git không hỗ trợ option
  này hoặc metadata lỗi bị từ chối với cause; không fallback/đoán SHA-1.
- `read` bọc `JSONDecodeError`, `ValueError` do integer vượt giới hạn parser
  và `RecursionError` do JSON lồng quá sâu thành lỗi boundary, giữ `__cause__`,
  không echo payload; lỗi config/task là `InputError`, lỗi state là
  `OperationError`.
- Allowed path từ chối empty segment, `.`/`..`, backslash, drive/UNC/ADS,
  glob, NUL và segment `.git`/`.omp` không phân biệt hoa thường. Space/Unicode
  hợp lệ. Đây là policy MVP; symlink/junction/ignored/Git tampering sâu thuộc
  SEC-03.
- `child_pid` là `null` hoặc integer `>0`; PID hợp lệ không chứng minh process
  ownership. `last_error` là string; `result_commit` được validate như
  `base_commit`.
- `run`/`resume` validate trước lock và đọc/validate lại dữ liệu authoritative
  trong lock trước clone/process/checkpoint mutation. `status` chỉ đọc, không
  ghi state. Lỗi config/task là `InputError`; lỗi state là `OperationError`;
  CLI trả nonzero, không traceback cho lỗi dự kiến.

## Bất biến bắt buộc

1. Repo nguồn: OMP không tự sửa, stage, reset hoặc commit nội dung người dùng.
2. Mỗi project/run chỉ một owner; không spawn Worker mới khi owner cũ chưa được
   chứng minh đã dừng. Khóa file không thay thế containment process tree.
3. Không complete khi test fail, không tìm thấy test, scope sai hoặc checkpoint
   chưa đối soát. Test result phải thuộc đúng tree và command được phê chuẩn.
4. Không tin task/provider để chọn đường dẫn checkpoint, sửa baseline hoặc
   thay lệnh test. Sai schema phải từ chối trước side effect.
5. Side effect không tự replay nếu chưa có quy tắc idempotency/đối soát.
6. Không nâng cấp cam kết từ "phát hiện sau chạy" thành "ngăn trước chạy".

## Ranh giới module mục tiêu

| Trách nhiệm | Đầu vào/đầu ra | Quyền side effect |
| --- | --- | --- |
| CLI | argv → request/exit code | In thông báo; gọi service |
| Models/validation | JSON không tin cậy → dữ liệu hợp lệ hoặc lỗi | Không |
| State store | snapshot/event → checkpoint versioned | Chỉ runtime, khóa/atomic write |
| Process runner | argv/cwd/budget → kết quả có cấu trúc | Spawn/stop/log có containment |
| Git workspace | base commit + scope → checkout/patch/commit | Chỉ clone của run |
| Verification | tree + trusted runner → bằng chứng test | Môi trường test kiểm soát |
| Orchestrator | state + result → transition hợp lệ | Gọi các lớp trên tuần tự |
| Provider adapter | task/context → request/result chuẩn | Qua process runner |

Đây là hướng tách khi task yêu cầu, không phải danh sách file phải scaffold.
Không import CLI từ lớp thấp; không để provider tự ghi state hoặc commit.

## Hợp đồng hoàn tất tại REC-01 và các gate sau

- `phase`: REC-01 đã triển khai bảng `VALID_TRANSITIONS` và helper `transition`
  kiểm tra invariant tại production; cấm gán phase tự do.
- `candidate_tree`: REC-01 tính candidate tree OID qua index Git cô lập trong
  checkout, xác thực chính xác modification, new file và deletion.
- `verification`: REC-01 gắn verification evidence (candidate tree, command hash gồm
  test_argv và timeout_seconds, task hash, run ID, exit code 0); phát hiện test tự sửa
  candidate files và hủy bỏ evidence; thay đổi settings sau verification bị từ chối tái dùng evidence.
- `commit_intent`: REC-01 lưu intent trước khi committing, validate đầy đủ tại transition,
  đối soát commit recovery và đối soát actual commit ở first-run (parent, marker, tree) khớp
  intent/evidence, tránh commit trùng hoặc sai lệch; xử lý no-op tường minh thành `no_change`.
- `crash matrix`: REC-02 triển khai ma trận gián đoạn (Crash Matrix ADR-0003),
  atomic checkpoint (write/flush/fsync/replace, cleanup không che lỗi gốc), đối soát
  staging dở bằng git reset, commit idempotent không duplicate, fail-closed an toàn
  khi clone/checkout dở; ghi nhận giới hạn spawn-gap và bằng chứng Windows.
- `process`: owner identity, PID + dấu nhận diện đời tiến trình/OS containment;
  PID đơn lẻ không đủ chứng minh ownership (PROC-01/PROC-02).
- `process lock`: `run`/`resume`/`init` giữ OS lock độc quyền non-blocking;
  lock tự nhả khi process chết, không unlink lock file để phá stale; `status`
  chỉ đọc và không lấy lock. OS lock chỉ serialize orchestrator, không thay
  `child_pid` guard hoặc containment process tree (PROC-01/PROC-02).
- `limits`: attempt/call/time budgets hữu hạn; restart không reset budget
  (PROV-02/PIPE-01).
- `error`: mã lỗi ổn định, phase/operation, retryability, message đã redaction;
  CLI trả nonzero cho invalid input, blocked và failed operation (PROV-02/UX-01).
