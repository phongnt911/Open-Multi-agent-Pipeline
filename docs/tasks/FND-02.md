# FND-02: Chuẩn hóa MVP và loại bỏ thành công giả khi không có test

- Trạng thái: REVIEW
- Gate: G0
- Owner: AI thực thi FND-02 (DeepSeek V4.1 Flash Free, phiên opencode)
- Reviewer: AI điều phối trong chat của chủ dự án
- Base: main chứa tài liệu này; ghi full HEAD SHA và git status vào handoff
- Phụ thuộc: FND-01 đã có bộ khung và local evidence; CI baseline phải được
  kiểm tra trước code. G0 vẫn mở cho đến khi đủ CI/review, không coi task này
  là quyền vượt G0.
- Handoff: `docs/handoffs/FND-02.md`

## Vấn đề

MVP thiếu type hints; lỗi dữ liệu và lỗi chạy lệnh đang dùng chung ValueError.
Runner mặc định dùng assert để chặn zero tests, có thể bị bỏ qua khi Python
chạy optimize. AI cần củng cố đúng phạm vi này trước DAT-01.

## File sở hữu

- omp_core/main.py
- tests/test_pipeline.py; có thể thêm tests/test_cli.py hoặc test runner riêng
- README.md, docs/CONTRACTS.md: chỉ đoạn mô tả hành vi liên quan
- docs/tasks/FND-02.md; docs/handoffs/FND-02.md

Chỉ tạo module production mới nếu cần tách trách nhiệm thực, giải thích trong
handoff; ưu tiên giữ thay đổi nhỏ. Không sửa AGENTS, ROADMAP, workflow, checker,
review của người khác hay dependency để làm tiêu chí dễ hơn. Cần mở scope thì
báo điều phối, tiếp tục phần độc lập và giữ nguyên các kiểm tra hiện hành.

## Yêu cầu triển khai

1. Thêm type hints cho các hàm production hiện có trong main.py, kể cả context
   manager/process; khai báo kiểu JSON thực tế, tránh Any tràn toàn module.
   Không tạo schema framework hoặc migration; DAT-01 chịu trách nhiệm phần đó.
2. Phân biệt lỗi input/config với lỗi operation/process bằng tên lỗi rõ ràng;
   CLI lỗi dự kiến trả nonzero và thông báo hữu ích, giữ nguyên nguyên nhân
   khi wrap exception. Không print traceback cho lỗi sử dụng dự kiến.
3. Thay assert trong lệnh runner mặc định bằng kiểm tra tường minh. Runner phải
   fail khi 0 tests, pass khi có test đạt, fail khi test lỗi. Không thêm pytest.
4. Kiểm thử argv thật do `omp init` sinh; không chỉ test một command tự dựng
   trong test. Thử cả chế độ thường và PYTHONOPTIMIZE=1 (hoặc tương đương).
5. Không thay quy trình checkpoint/commit, tự retry, tự schema migration hoặc
   subprocess containment. Phát hiện nguy cơ ngoài scope: ghi handoff.

## Acceptance bắt buộc

- [x] AC1: mọi hàm production trong main.py có annotation đầu vào và đầu ra;
  docstring public boundary mô tả side effect/lỗi, không chỉ lặp lại tên hàm.
- [x] AC2: lỗi validate và lỗi operation phân biệt được; test CLI cho input lỗi
  trong phạm vi hiện hành và process nonzero, thông báo không lộ secrets.
  Bao gồm regression FND02-R1 (cleanup timeout không lộ argv trong stderr,
  `last_error`, exception công khai và cause) và FND02-R2 (NUL argv trả nonzero
  không traceback; executable thiếu trả `OperationError` có cause).
  Không mở rộng thành validate toàn bộ state/schema trong task này.
- [x] AC3: chạy runner sinh từ init trong repo fixture: 0 tests → nonzero,
  một test đạt → 0, một test fail → nonzero; cả thường và optimize.
- [x] AC4: có evidence regression zero-tests thất bại trên baseline (ít nhất
  chế độ optimize) và đạt sau sửa; không sửa baseline nguồn để tạo evidence.
- [x] AC5: giữ nguyên 8 regression tests cũ, không skip/nới expected; toàn bộ
  `python tools/check.py` (23 tests) và `git diff --check` đạt trên Windows.
- [x] AC6: không thêm dependency runtime, không gọi AI thật, không sửa source
  repo ngoài task hoặc đưa runtime/log/credentials vào Git.
- [x] AC7: docs khớp code; handoff có diff/file list, base SHA, test OS/Python,
  số test, exit codes, AC mapping, check chưa chạy, evidence FND02-R1/R2 và rủi
  ro còn lại.

## Review follow-up FND02-R1 / FND02-R2

- FND02-R1: khi Worker timeout và cleanup cũng timeout, `stop` tạo
  `TimeoutExpired` chỉ chứa label/timeout; `execute` tách lỗi operation và
  cleanup, raise lỗi công khai ngoài `except` để không gắn raw argv vào
  `__cause__`/`__context__`, đồng thời chain timeout operation đã sanitize để
  giữ dấu vết. Không đổi cơ chế containment/recovery.
- FND02-R2: `validate` từ chối NUL trong `worker_argv`/`test_argv` trước side
  effect; `subprocess.Popen` tại boundary `execute` được wrap `OSError`/
  `ValueError` thành `OperationError` có cause, không echo argv. Không bắt
  `ValueError` toàn chương trình và không mở rộng sang schema DAT-01.
- Evidence: xem `docs/handoffs/FND-02.md`, mục regression FND02-R1/R2 và bảng
  AC. Task vẫn REVIEW; chưa DONE/ACCEPTED, chưa đóng G0.

## Trình tự và quyền

Đọc AGENTS + COORDINATION + CONTRIBUTING + CONTRACTS + SDD. Kiểm tra git status,
HEAD, CI baseline nếu có quyền đọc; baseline CI fail thì báo điều phối, không
âm thầm sửa workflow. Nếu chưa truy cập được CI, ghi rõ và vẫn có thể thực hiện
local; không tự đóng G0 hay tuyên bố CI đạt.

Chạy baseline check, viết regression zero-tests và xác nhận lỗi cũ, triển khai,
chạy checks và bàn giao REVIEW. Không tự commit/push/merge, không chuyển DONE,
không nhận DAT-01, không spawn agent phụ. Quyền cài build tools trong venv
riêng theo chính sách môi trường; runtime dependency mới không thuộc scope.

## Handoff

Tạo docs/handoffs/FND-02.md theo template. Reviewer điền review riêng sau khi
nhận kết quả; AI thực thi không tự tạo verdict ACCEPTED cho công việc của mình.

## Yêu cầu bàn giao prompt

Sau mỗi lần thực thi, AI thực thi MUST đăng prompt copy trực tiếp trong khung
chat cho bước kế tiếp; không lưu nội dung prompt trong file nhiệm vụ. Prompt
trong chat phải tự đủ ngữ cảnh, hash/revision, việc cần đọc/kiểm tra, quyền hạn
và ràng buộc không commit/push/merge, không DONE/G0/DAT-01.
