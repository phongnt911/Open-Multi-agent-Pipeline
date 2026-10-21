# Thiết kế OMP — MVP 0.1

## Luồng

`preparing → worker → verifying → committing → complete`

1. Kiểm tra config/task và working tree sạch; chụp commit gốc, task và config.
2. Clone local với `--no-hardlinks`, checkout detached tại baseline, bỏ remote.
3. Gọi một CLI qua argv/stdin, ghi log và PID, giới hạn thời gian.
4. Kiểm tra HEAD, index, file tracked/untracked và phạm vi đường dẫn.
5. Chạy test từ config đã chụp, kiểm tra lại phạm vi, stage file cho phép,
   commit trong clone. Sai phạm giữ nguyên clone để kiểm tra.
6. Lưu commit kết quả và complete; không sửa repo nguồn hoặc push.

## Phục hồi

Checkpoint dùng file tạm cùng thư mục, fsync và os.replace. Khóa file OS giữ
suốt phiên điều phối. Filesystem/Git không có giao dịch chung: bước commit
đối soát HEAD, parent và thông điệp có run ID để không commit lặp.
HEAD còn tại baseline: reset index trong clone, kiểm tra và chạy lại test.
Commit đã có: kiểm tra checkout sạch rồi hoàn tất checkpoint.

PID còn lưu chặn resume. Sau crash cứng, người vận hành xác minh cây tiến trình
đã dừng trước khi bỏ chặn. Clone dở cần xử lý thủ công. Worker dở được gọi lại
với thay đổi hiện tại; test lỗi không tự gọi lại Worker hay đổi provider.

## Ranh giới tin cậy

MVP giả định Worker hợp tác và sandbox provider phù hợp. Clone cách ly thay đổi
Git, không cách ly quyền OS, mạng hay DB. Hook PowerShell, hash cạnh file và
whitelist sau thực thi không phải ranh giới bảo mật. Tests/config phải chuẩn
bị trước và nằm ngoài phạm vi task. File ignored và Git metadata chưa được
bảo vệ trước Worker đối kháng. Không dùng DB production.

## Tiếp theo

1. Cách ly quyền OS, test runner và vault ngoài quyền ghi của Worker.
2. Kiểm thử crash cứng và Windows Job Object cho cây tiến trình.
3. Retry/failover hữu hạn sau khi xác nhận provider cũ đã dừng.
4. Planner/reviewer có ngân sách gọi; diff tính từ baseline feature.
5. Migration trên DB phát triển riêng, nhật ký và quy tắc chạy lại.
6. Dashboard và đồng bộ Git khi luồng lõi đã ổn định.
