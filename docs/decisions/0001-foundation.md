# ADR-0001: Củng cố lõi trước khi mở rộng

- Ngày: 2026-10-21
- Trạng thái: Accepted cho hướng phát triển; gates chưa được nghiệm thu.

## Bối cảnh

MVP chứng minh task → Worker → test → commit trong clone. Chưa có đủ bằng
chứng cho crash cứng, containment Windows hoặc bảo vệ trước Worker đối kháng.

## Quyết định

Giữ Python stdlib cho runtime; unittest/fake providers cho test mặc định.
Đặt contracts, state/recovery và containment trước multi-provider và UI.
Đóng gate bằng evidence, không bằng số module hoặc số dòng code.
Không scaffold framework, thêm dependency hoặc làm dashboard để thay tiến độ lõi.

CI dùng Windows/Linux với Python 3.10/3.12, cài package và smoke entrypoint.
CI không tự publish và không gọi AI thật. Required checks trên remote do quản
trị viên cấu hình riêng; workflow trong repo không tự bật branch protection.

## Hệ quả

MVP chỉ được mô tả là chạy với Worker hợp tác. Cần thử nghiệm quyền OS nhỏ ở
SEC-01 trước khi chọn thiết kế vault/sandbox. Tests cũ tiếp tục bảo vệ hành vi;
type hints/validation và các khoảng trống process được ghi thành task cụ thể.
Đổi quyết định này cần ADR thay thế có bằng chứng và đánh giá migration.
