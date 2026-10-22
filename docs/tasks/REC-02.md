# REC-02: Ma trận gián đoạn và đối soát checkpoint

- Trạng thái: DONE
- Gate: G1, vẫn mở.
- Phụ thuộc: DAT-01 DONE; REC-01 DONE, review r3 ACCEPTED.
- Owner: AI thực thi REC-02 (Antigravity).
- Reviewer: AI điều phối trong chat chủ dự án.
- Base HEAD: `517f00e092201a08886eb30500cd21e648bc2c4b`; baseline thực là working tree REC-01 được nghiệm thu trong `docs/reviews/REC-01-r3.md`, không phải source HEAD.

## Quyết định nghiệm thu của điều phối

Ngày 2026-10-22: ACCEPTED tại `docs/reviews/REC-02-r2.md`, đúng hash bàn giao;
reviewer chạy 111/111 tests và probes độc lập R1–R4 đạt. DONE về nội dung,
chưa commit/integration/packaging/CI revision cuối. G1 vẫn mở; không giao PROC-01
hoặc cấp quyền commit/push/merge từ nghiệm thu này.

## Baseline và ownership

Trước sửa đối chiếu code/test/docs hash trong review REC-01-r3. Task REC-01 và
ROADMAP đã được điều phối cập nhật sau nghiệm thu nên hash trạng thái docs đổi
là có chủ đích; không nhận hash code/test lệch mà không báo điều phối.
Chạy baseline 90 tests và diff check, ghi status và file ngoài ownership.
Không reset/clean/stash để làm sạch working tree.

Sở hữu: tests/test_crash_recovery.py mới; omp_core/main.py chỉ cho sửa lỗi logic
được ma trận tái hiện hoặc seam fault injection tối thiểu thực sự cần;
docs/tasks/REC-02.md, docs/handoffs/REC-02.md, docs/CONTRACTS.md, docs/SDD.md,
README.md và ADR mới nếu có quyết định policy. Có thể thêm regression vào
tests/test_recovery.py nếu đúng boundary liên quan, không thay/nới test cũ.
Tests CLI/validation/pipeline/errors, checker, workflow, dependency, AGENTS,
ROADMAP, review history và handoff/task DAT-01/REC-01 chỉ đọc.

## Kết quả mong muốn và giới hạn

Lập ma trận rõ kết quả cho mỗi điểm gián đoạn: resume tự động có đối soát,
chặn an toàn, hoặc giữ run để xử lý thủ công. Không bắt buộc tự phục hồi mọi
case. Không xóa clone/checkpoint để biến lỗi thành thành công; không suy diễn
PASS hoặc commit intent. Giữ v1 read-only và v2 strict.

Trong scope là logic recovery, atomic checkpoint và fault injection hữu hạn.
Ngoài scope: PROC lock/Job Object/PID ownership, recovery CLI mới, sandbox,
retry/failover, migration, remote push. Spawn gap chưa có containment phải
ghi rõ rủi ro và chặn phần phụ thuộc; không tuyên bố đã chứng minh single
Worker sau kill khi chưa có cơ chế OS. Không dùng process kill như bằng chứng
mất điện an toàn.

## Acceptance

- [x] AC1: Ma trận có pre-state, injection point, bytes/HEAD/index/process đã
  quan sát, expected policy, actual result và test tương ứng cho clone dở,
  checkout dở, spawn trước lưu PID, sau lưu PID, test PASS trước checkpoint,
  stage trước commit, commit trước complete checkpoint và state replace lỗi.
- [x] AC2: Clone/checkout dở không bị xóa hoặc đè; giữ evidence, không gọi thêm
  Worker khi chưa đủ điều kiện. Case không resume được phải có lỗi rõ ràng và
  hướng xử lý giữ dữ liệu, không tự thêm command recovery thuộc PROC-03.
- [x] AC3: Inject lỗi save tại các boundary process/PID và verification; không
  complete giả, không tự dùng PASS thiếu evidence hoặc bỏ PID không đối soát.
  Probe spawn-gap dùng fake child hữu hạn và cleanup chắc chắn; công bố rõ
  khoảng trống quan sát hiện tại thay vì âm thầm coi là an toàn OS.
- [x] AC4: Stage dở chỉ được xử lý trong clone đúng run/scope/intent; commit
  trước checkpoint resume một lần, không duplicate; tree/parent/marker sai bị
  chặn. Giữ no-op và first-run actual commit validation REC-01.
- [x] AC5: Inject lỗi write/flush/fsync/replace: checkpoint đã tồn tại giữ bytes
  hợp lệ khi replace chưa xảy ra, file temp không bị hiểu thành authoritative;
  chưa có checkpoint không được tự tạo state giả. Lỗi cleanup không che lỗi
  gốc; ghi nhận policy file tạm và OS/filesystem limits đúng evidence.
- [x] AC6: Có ít nhất một phép thử gián đoạn process orchestrator thật trên
  Windows tại boundary kiểm soát, dùng fake Worker hữu hạn; kiểm tra disk và
  resume bằng process mới, cleanup child xác định. Không để orphan vô hạn;
  không khẳng định test này chứng minh kill descendants/mất điện.
- [x] AC7: 90 tests baseline giữ hành vi, không skip/nới expected; lỗi mới có
  regression fail trước/pass sau. Không sửa production chỉ để dễ mock hoặc
  tạo framework fault injection. Mỗi sửa ngoài seam phải gắn matrix case.
- [x] AC8: python tools/check.py, git diff --check đạt trên Windows; không gọi
  AI thật, không dependency runtime mới. Handoff ghi OS/Python/count/exit,
  SHA-256 mới, file list, AC mapping, cases chưa chạy và các rủi ro còn lại.
- [x] AC9: Docs mô tả policy thực tế, giới hạn clone/spawn/atomic durability;
  làm rõ trong bảng CONTRACTS rằng cả committing và complete cần intent.
  Bàn giao REVIEW; không tự DONE/ACCEPTED, đóng G1 hoặc nhận PROC-01.

## Trình tự kiểm chứng và bàn giao

Đọc AGENTS, COORDINATION, CONTRIBUTING, CONTRACTS, SDD, ROADMAP, task và review
REC-01-r3. Thiết kế ma trận trước khi đổi production. Fixture trong temp root
độc quyền; mọi task.json trong parent riêng; sentinel/source bytes/index/HEAD
được kiểm chứng không bị thay ngoài clone. Chạy test riêng rồi full suite
tuần tự; nếu flake phải ghi nhận lần fail và retry, không bỏ failure evidence.

Không commit/push/merge, agent phụ hoặc AI thật. Nếu phát hiện invariant cần
containment OS để đạt, báo điều phối và giữ phần đó BLOCKED có evidence; tiếp
tục cases độc lập, không tự nới acceptance hoặc mở PROC. Cung cấp prompt review
trực tiếp trong chat và handoff khi hoàn tất.
