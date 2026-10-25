# Điều phối và review AI

## Vai trò và quyền

Theo yêu cầu chủ dự án ngày 2026-10-21, AI trong chat điều phối hiện tại làm
người giao việc và review. AI thực thi nhận một task cụ thể, triển khai và
gửi evidence; không tự chọn thêm task hay tự phê duyệt kết quả của mình.
Chủ dự án quyết định thay đổi mục tiêu, quyền remote, merge và phát hành.

Lần xuất bản bộ khung này lên main được chủ dự án cho phép riêng. Điều đó
không cấp cho mọi AI quyền push main trong các công việc sau.

## Nguồn sự thật

1. AGENTS.md: quy tắc toàn repo.
2. ROADMAP.md: gate và phụ thuộc.
3. docs/tasks/<ID>.md: giao việc, file sở hữu và acceptance cụ thể.
4. docs/handoffs/<ID>.md: evidence của AI thực thi.
5. docs/reviews/<ID>.md: kết luận của reviewer.

Nếu task và roadmap mâu thuẫn, báo người điều phối trước phần phụ thuộc;
không tự nới gate. Prompt là lối vào tài liệu, không thay acceptance.
Trạng thái trong task cụ thể là trạng thái hiện hành; roadmap chỉ tổng hợp.

## Chu kỳ giao việc

1. Điều phối xác định task đủ điều kiện, base, phạm vi, acceptance và quyền.
2. AI thực thi đọc tài liệu, báo baseline/status, ghi owner và IN_PROGRESS.
3. Thực hiện một task; vấn đề ngoài scope ghi phát hiện, không tiện tay sửa.
4. Chạy checks, đọc diff, ghi handoff và chuyển REVIEW; không ghi DONE.
5. Người điều phối review code + tests + docs + evidence và chạy lại checks
   phù hợp trên bản được bàn giao. Không chỉ tin nội dung báo cáo hoặc CI xanh.
6. Có lỗi: yêu cầu sửa theo finding ID; AI sửa đúng finding và bổ sung regression.
7. Đủ acceptance: reviewer ghi ACCEPTED, task mới được DONE. Điều này không
   tự đóng gate hoặc cho phép merge nếu còn thiếu CI/quyền/review độc lập.

Không cần tạo AI/chat mới hoặc gửi tin nhắn tự động. Chủ dự án chuyển prompt
và handoff giữa các AI; chỉ dùng công cụ giao tiếp khi được cho phép rõ ràng.

## Chuẩn review

- So sánh đúng base và toàn bộ file changed/untracked trong phạm vi bàn giao.
- Đọc đường gọi, exception/cleanup, side effects và recovery liên quan.
- Với mỗi acceptance ghi evidence file/test/lệnh hoặc ghi còn thiếu.
- Kiểm tra test có bắt được lỗi cũ, có bị skip/nới expected và có test rỗng.
- Kiểm tra compatibility, docs, đường dẫn Windows, secrets và scope creep.
- P0: nguy cơ mất dữ liệu nghiêm trọng/lộ secrets/thoát quyền đáng tin cậy.
- P1: sai tính đúng đắn, hoàn tất giả, Worker chạy trùng, recovery sai.
- P2: vi phạm acceptance, thiếu regression quan trọng, lỗi portability.
- P3: cải thiện nhỏ không ảnh hưởng acceptance; không dùng để ép refactor ngoài task.

Mỗi finding có ID, mức độ, file:dòng, trigger, hậu quả, đề nghị và cách xác minh.
P0/P1/P2 chưa xử lý hoặc acceptance thiếu evidence thì không ACCEPTED.
Verdict dùng ACCEPTED / CHANGES_REQUIRED / BLOCKED. Không có finding không
đồng nghĩa an toàn OS; ghi rõ giới hạn kiểm chứng và thiếu review độc lập.

## Quy tắc tích hợp

Mặc định không cho AI thực thi commit/push/merge. Nếu được cho phép tạo commit,
ghi SHA trong handoff; nếu chưa, giao diff và danh sách file để review tại chỗ.
Không giao main cho nhiều người cùng ghi. Không force-push và không dùng
reset/clean để giải quyết xung đột. Sau tích hợp phải chạy checks trên tree cuối.
Thay đổi làm invalid evidence phải chạy lại check liên quan trước nghiệm thu.

## Chính sách thực thi từ 2026-10-25

Theo chỉ đạo chủ dự án: bỏ Linux khỏi phạm vi hỗ trợ/CI, dùng Python 3.12
ngay; chỉ thêm Python 3.14 sau kiểm chứng Windows thực tế. Baseline package
>=3.12, .python-version=3.12; CI Windows 3.12/3.14 bắt buộc test, packaging và
entrypoint ngoài source trên exact SHA. Không dùng 3.14 để thay baseline 3.12.
Không sửa lại evidence Linux/3.10 trong review history; đó là bằng chứng lịch sử.
SEC-02 được giao giai đoạn A theo docs/tasks/SEC-02.md; chưa được triển khai
production boundary hoặc nhận SEC-03 trước checkpoint review quy định ở task.
