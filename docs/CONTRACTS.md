# Hợp đồng kiến trúc và dữ liệu

## Hiện trạng so với mục tiêu

MVP đặt logic trong `omp_core/main.py`; config/task chưa versioned, state chỉ
ghi schema_version lúc tạo và chưa validate đầy đủ khi resume. Đây là nợ DAT-01,
không phải cam kết schema ổn định. Legacy JSON chỉ là tư liệu, không dùng chạy.

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

## Contract cần hoàn thiện ở DAT-01 / REC-01

- `schema_version`: số nguyên thực, kiểm tra version hỗ trợ; version lạ fail
  closed và giữ nguyên file. Migration cần explicit operation và bản sao.
- `run_id`: identifier theo format cố định, không đường dẫn; identity bất biến.
- `base_commit`: full object ID Git được đối soát trong repo phù hợp; không
  suy đoán mọi repo luôn dùng SHA-1 40 ký tự.
- `phase`: enum và bảng transition; unknown phase là lỗi, không exit 0 im lặng.
- `task`: specification, exact allowed paths, trusted test scope riêng; path
  validation có chính sách Windows, không chỉ kiểm tra chuỗi `..`.
- `process`: owner identity, PID + dấu nhận diện đời tiến trình/OS containment;
  PID đơn lẻ không đủ chứng minh ownership.
- `verification`: tree/command identity, test count khi runner hỗ trợ, exit,
  timestamps và log reference; kết quả lỗi/thiếu/mơ hồ không phải PASS.
- `limits`: attempt/call/time budgets hữu hạn; restart không reset budget.
- `error`: mã lỗi ổn định, phase/operation, retryability, message đã redaction;
  CLI trả nonzero cho invalid input, blocked và failed operation.

Không phát hành schema 1.0 cho đến khi DAT-01 có tests tương thích. Schema
phiên bản MVP phải có đường báo lỗi/hướng di chuyển, không âm thầm đổi nghĩa.
