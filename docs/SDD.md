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

PID còn lưu chặn resume. CLI `recover RUN_ID --state-sha256 HEX` đọc/validate
checkpoint và lock; `HEX` là SHA-256 bytes thô `.omp/state.json`. Lệnh không
chạy Worker/test, không sửa checkout/commit. Nếu `child_pid` còn, PID sống hoặc
unknown thì refuse và không kill. Chỉ khi PID absent và người vận hành truyền
`--confirm-child-stopped` (sau khi tự kiểm tra cả cây) lệnh mới clear đúng
`child_pid`; audit ghi `operator_attestation_only`, không phải proof OS. Complete
state được đối soát intent/evidence/HEAD/tree nhưng không mutation; complete còn
`child_pid` bị coi là không nhất quán.

CLI `abort RUN_ID --state-sha256 HEX` không xóa checkpoint/clone/log/commit.
Nó archive exact bytes state vào `.omp/runs/<run_id>/aborted-state.json`, ghi
marker `aborted.json` và audit; idempotent, không kill, refuse nếu còn
`child_pid`. `resume`/`recover` từ chối run đã abort; `run` chỉ bắt đầu run mới
khi marker/archive khớp exact bytes cũ và `child_pid` null (kể cả khi checkpoint
ở phase complete, có marker thì bắt buộc đối soát exact bytes trước mọi shortcut).
Clone dở được giữ nguyên và xử lý bằng abort + run mới; recover không tự clone lại. Worker dở
được gọi lại với thay đổi hiện tại; test lỗi không tự gọi lại Worker hay đổi
provider. Resume/recover trên checkpoint v1 dở dang bị chặn trước mutation để
bảo toàn dữ liệu; v1 complete chỉ audit read-only.

Trên Windows, process runner tạo Worker/test suspended trong Job Object riêng
với `KILL_ON_JOB_CLOSE`, gắn job ngay bằng `PROC_THREAD_ATTRIBUTE_JOB_LIST` và
chỉ resume sau khi save `child_pid`. Root exit còn con/cháu, timeout, Ctrl+C
hoặc cleanup lỗi đều terminate job trước khi fail-closed. Chỉ xóa `child_pid`
khi job rỗng; owner hard-kill làm OS đóng job handle và dừng cây. Job Object
không phải sandbox quyền OS. Sau owner death, recovery không mở lại được job
handle cũ để query `ActiveProcesses`; vì vậy `recover` không suy diễn tree-stop
từ PID absent hay checkout và không tuyên bố containment proof. `KILL_ON_JOB_CLOSE`
vẫn là lifecycle containment hiện có, không thay thế audit/attestation của
PROC-03.

## Ranh giới tin cậy

MVP giả định Worker hợp tác và sandbox provider phù hợp. Clone cách ly thay đổi
Git, không cách ly quyền OS, mạng hay DB. Job Object trên Windows chỉ kiểm soát
vòng đời process, không hạn chế file/network/credential. Hook PowerShell, hash
cạnh file và whitelist sau thực thi không phải ranh giới bảo mật. Tests/config
phải chuẩn bị trước và nằm ngoài phạm vi task. File ignored và Git metadata
chưa được bảo vệ trước Worker đối kháng. Không dùng DB production.

SEC-01 đã chứng minh trên fixture rằng AppContainer/lowbox bị OS từ chối đọc/
ghi canary source/runtime/vault/config/audit/credentials, với same-user control
chứng minh denial không phải lỗi ACL toàn cục. Đây là candidate boundary, chưa
tích hợp runner; tài sản thật, credential, network, inherited handle và Worker
CLI/Python/provider thật vẫn NOT PROVEN. Xem
[threat model](security/THREAT-MODEL.md) và
[ADR-0006](decisions/0006-windows-isolation.md).

## Tiếp theo

1. Cách ly quyền OS, test runner và vault ngoài quyền ghi của Worker.
2. Nghiệm thu PROC-03 recovery/abort command có digest/lock/audit; G2 vẫn mở.
   thực hiện SEC-01 threat model/proof Windows trước khi triển khai security boundary G3.
3. Retry/failover hữu hạn sau khi xác nhận provider cũ đã dừng.
4. Planner/reviewer có ngân sách gọi; diff tính từ baseline feature.
5. Migration trên DB phát triển riêng, nhật ký và quy tắc chạy lại.
6. Dashboard và đồng bộ Git khi luồng lõi đã ổn định.
