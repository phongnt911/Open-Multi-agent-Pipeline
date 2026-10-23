# Thiết kế OMP — MVP 0.1

## Luồng

`preparing → worker → verifying → committing → complete`

1. Kiểm tra config/task và working tree sạch; chụp commit gốc, task và config.
2. Clone local với `--no-hardlinks`, checkout detached tại baseline, bỏ remote.
3. Gọi một CLI qua argv/stdin, ghi log và PID, giới hạn thời gian.
4. `verifying`: kiểm tra scope; tính candidate tree OID trước test; chạy test từ
   config đã chụp; kiểm tra lại scope và candidate tree sau test (nếu test làm
   thay đổi candidate tree thì evidence bị hủy bỏ); lưu `verification_evidence`.
5. `committing`: đối soát candidate tree với evidence; nếu no-op (tree bằng
   baseline) thì ghi nhận `no_change` và `result_commit = base_commit` không
   tạo empty commit; nếu có thay đổi, lưu `commit_intent`, stage file cho phép,
   commit trong clone. Sai phạm giữ nguyên clone để kiểm tra.
6. `complete`: đối soát HEAD và candidate tree khớp evidence và commit kết quả;
   không sửa repo nguồn hoặc push.

## Phục hồi

Checkpoint v2 dùng file tạm cùng thư mục, fsync và os.replace; lỗi cleanup không
che lỗi gốc (REC-02). Khóa file OS giữ suốt phiên điều phối. Filesystem/Git không có
giao dịch chung: bước committing lưu `commit_intent` (run ID, base commit,
expected tree OID, marker) và `verification_evidence` trước khi commit để đối soát khi resume.
HEAD còn tại baseline: git reset unstage index dở (REC-02), kiểm tra lại checkout
và candidate tree khớp evidence, rồi mới tiến hành commit.
Commit đã có: đối soát HEAD, parent, marker và tree OID với intent/evidence.
Khớp thì hoàn tất idempotent không tạo commit trùng; lệch thì fail-closed.
No-op task hoàn tất idempotent với `completion_kind = no_change`.

PID còn lưu chặn resume. Sau crash cứng, người vận hành xác minh cây tiến trình
đã dừng trước khi bỏ chặn. Clone dở cần xử lý thủ công. Worker dở được gọi lại
với thay đổi hiện tại; test lỗi không tự gọi lại Worker hay đổi provider.
Resume trên checkpoint v1 dở dang bị chặn trước mutation để bảo toàn dữ liệu.

Trên Windows, process runner tạo Worker/test suspended trong Job Object riêng
với `KILL_ON_JOB_CLOSE`, gắn job ngay bằng `PROC_THREAD_ATTRIBUTE_JOB_LIST` và
chỉ resume sau khi save `child_pid`. Root exit còn con/cháu, timeout, Ctrl+C
hoặc cleanup lỗi đều terminate job trước khi fail-closed. Chỉ xóa `child_pid`
khi job rỗng; owner hard-kill làm OS đóng job handle và dừng cây. Job Object
không phải sandbox quyền OS.

## Ranh giới tin cậy

MVP giả định Worker hợp tác và sandbox provider phù hợp. Clone cách ly thay đổi
Git, không cách ly quyền OS, mạng hay DB. Job Object trên Windows chỉ kiểm soát
vòng đời process, không hạn chế file/network/credential. Hook PowerShell, hash
cạnh file và whitelist sau thực thi không phải ranh giới bảo mật. Tests/config
phải chuẩn bị trước và nằm ngoài phạm vi task. File ignored và Git metadata
chưa được bảo vệ trước Worker đối kháng. Không dùng DB production.

## Tiếp theo

1. Cách ly quyền OS, test runner và vault ngoài quyền ghi của Worker.
2. Nghiệm thu PROC-02 (container Windows native), rồi PROC-03 recovery command
   có đối soát thay vì sửa `child_pid`/phase thủ công.
3. Retry/failover hữu hạn sau khi xác nhận provider cũ đã dừng.
4. Planner/reviewer có ngân sách gọi; diff tính từ baseline feature.
5. Migration trên DB phát triển riêng, nhật ký và quy tắc chạy lại.
6. Dashboard và đồng bộ Git khi luồng lõi đã ổn định.
