# FND-02: Chuẩn hóa MVP và loại bỏ thành công giả khi không có test

- Trạng thái: READY
- Gate: G0
- Owner: AI thực thi tiếp theo, ghi tên khi bắt đầu
- Reviewer: AI điều phối trong chat của chủ dự án
- Base: main chứa tài liệu này; ghi full HEAD SHA và git status vào handoff
- Phụ thuộc: FND-01 đã có bộ khung và local evidence; CI baseline phải được
  kiểm tra trước code. G0 vẫn mở cho đến khi đủ CI/review, không coi task này
  là quyền vượt G0.

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

- [ ] AC1: mọi hàm production trong main.py có annotation đầu vào và đầu ra;
  docstring public boundary mô tả side effect/lỗi, không chỉ lặp lại tên hàm.
- [ ] AC2: lỗi validate và lỗi operation phân biệt được; test CLI cho input lỗi
  trong phạm vi hiện hành và process nonzero, thông báo không lộ secrets.
  Không mở rộng thành validate toàn bộ state/schema trong task này.
- [ ] AC3: chạy runner sinh từ init trong repo fixture: 0 tests → nonzero,
  một test đạt → 0, một test fail → nonzero; cả thường và optimize.
- [ ] AC4: có evidence regression zero-tests thất bại trên baseline (ít nhất
  chế độ optimize) và đạt sau sửa; không sửa baseline nguồn để tạo evidence.
- [ ] AC5: giữ nguyên 8 regression tests cũ, không skip/nới expected; toàn bộ
  `python tools/check.py` và `git diff --check` đạt trên Windows.
- [ ] AC6: không thêm dependency runtime, không gọi AI thật, không sửa source
  repo ngoài task hoặc đưa runtime/log/credentials vào Git.
- [ ] AC7: docs khớp code; handoff có diff/file list, base SHA, test OS/Python,
  số test, exit codes, AC mapping, check chưa chạy và rủi ro còn lại.

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
