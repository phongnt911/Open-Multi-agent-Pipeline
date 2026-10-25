# Quy chuẩn bắt buộc cho AI phát triển OMP

Áp dụng toàn repo. MUST = bắt buộc; MUST NOT = cấm. Đây là quy trình phát triển
OMP, khác với task JSON mà OMP giao cho Worker lúc chạy. Chỉ dẫn của người dùng
và chính sách môi trường có độ ưu tiên cao hơn tài liệu này.

## Đọc trước khi sửa

1. Đọc README, docs/ROADMAP.md, docs/CONTRIBUTING.md, docs/CONTRACTS.md và SDD.
2. Xem git status/diff; giữ nguyên sửa đổi có sẵn của người khác.
3. Chọn một mã công việc trong roadmap; xác định file sở hữu, phụ thuộc và
   tiêu chí nghiệm thu. Không tự nhận công việc đã có người đang làm.
4. Đọc luồng gọi và tests liên quan trước khi chọn cách triển khai.
5. Đọc docs/COORDINATION.md; chỉ nhận task do người điều phối giao.
   Không tự chuyển task sang DONE hoặc tự mở scope sang task kế tiếp.

## Quy tắc thực thi

- MUST làm một thay đổi có mục đích rõ ràng, đủ nhỏ để review riêng.
- MUST giữ điều phối Worker tuần tự. Chưa được mở nhiều Worker trên cùng run.
- MUST NOT tự sinh agent phụ hoặc giao việc song song nếu chưa được người
  dùng cho phép. Khi được phép, khai báo ownership file và tránh sửa chồng lấn.
- MUST NOT reset/checkout/clean để xóa thay đổi của người khác; không tự stash.
- MUST NOT commit, push, merge, publish hoặc chạy AI thật bằng quota tài khoản
  nếu nhiệm vụ hiện tại chưa cho phép hành động đó. Tests mặc định dùng fake.
- MUST NOT dùng shell=True, eval/exec hoặc ghép chuỗi lệnh từ dữ liệu task.
  Subprocess nhận argv; có cwd, timeout và chính sách dừng cây tiến trình.
- MUST coi task, đầu ra provider, checkpoint và đường dẫn là dữ liệu cần
  validate. Prompt, hash và Git clone không phải sandbox bảo mật.
- MUST NOT bỏ kiểm tra, skip test, nới whitelist hoặc sửa expected chỉ để pass.
  Sửa test hợp lệ phải giải thích lỗi đặc tả và có regression chứng minh.
- MUST NOT tự cài dependency runtime mới cho nhu cầu thư viện chuẩn đã đáp ứng.
  Dependency mới cần lý do, giấy phép, phạm vi và cách kiểm thử trong review.
- MUST NOT đưa migration, auto-push, TUI hoặc failover vào trước gate tương ứng.

## Chuẩn mã và dữ liệu

- Python >=3.12; UTF-8, LF, 4 spaces. Tên code/log dùng tiếng Anh;
  hướng dẫn và giải thích ưu tiên tiếng Việt.
- Hàm production mới hoặc thay đổi chữ ký MUST có type hints; public API
  có docstring nêu tác dụng phụ/lỗi quan trọng. Code MVP cũ chưa đạt được theo
  dõi ở FND-02, không viện dẫn nợ cũ để thêm code thiếu chuẩn.
- Ưu tiên thư viện chuẩn, hàm rõ ràng, ít lớp. Không tạo module rỗng, interface
  một implementation, registry/plugin framework khi chưa có nhu cầu thực.
- Không catch rồi bỏ qua lỗi; không dùng assert cho validation production.
  Không thay lỗi gốc bằng lỗi cleanup mà thiếu thông tin nguyên nhân.
- Thay đổi schema/checkpoint MUST có version, quy tắc tương thích và tests
  cho dữ liệu hỏng/phiên bản không hỗ trợ; không tự sửa dữ liệu người dùng.
- Không log token, cookie, env đầy đủ hoặc prompt chứa secrets. Log runtime
  nằm trong .omp và không được commit.
- Chỉ tách module khi task cần và có tests bảo vệ hành vi. Ranh giới mục tiêu
  nằm trong CONTRACTS; không triển khai trước cả kiến trúc bằng scaffold.

## Điều kiện hoàn thành

- Chạy `python tools/check.py`; báo đúng kết quả và các check chưa chạy.
- Code có thay đổi hành vi cần regression test thất bại trước bản sửa và
  đạt sau bản sửa; đường lỗi/phục hồi phải có test riêng khi liên quan.
- Sửa docs cùng code nếu CLI/schema/hành vi hoặc ranh giới tin cậy đổi.
- Nền tảng hỗ trợ: Windows. Python 3.12 là baseline; CI bắt buộc Windows
  Python 3.12 và 3.14. Linux/POSIX không còn thuộc phạm vi hỗ trợ hoặc CI.
- PR về process/recovery/security cần bằng chứng Windows thật trên cả hai
  phiên bản Python. CI xanh không chứng minh cách ly OS hoặc chịu mất điện.
- Không ghi DONE khi chưa đủ bằng chứng; dùng BLOCKED kèm lý do nếu cần.
- Handoff theo template; ghi file thay đổi, lệnh test, kết quả, giới hạn và
  bước tiếp theo. Không tuyên bố tính năng bảo mật vượt quá kiểm thử đã có.

## Xử lý lệch phạm vi

Sai phạm có thể làm mất dữ liệu, chạy hai Worker, giả kết quả test hoặc bỏ qua
gate MUST dừng phần phụ thuộc và ghi rõ vấn đề. Tiếp tục phần độc lập an toàn.
Nếu yêu cầu mới thay đổi scope, ghi quyết định trong task/ADR; không âm thầm
hạ tiêu chí nghiệm thu hay biến ngoại lệ thành mặc định.
