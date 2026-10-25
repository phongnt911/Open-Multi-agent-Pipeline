# Lộ trình OMP

Ngày lập: 2026-10-21. Lộ trình theo gate, không theo ngày hứa hoàn thành.
Mỗi gate chỉ đóng khi có bằng chứng test/review. Trạng thái ban đầu của các
công việc dưới đây là TODO, trừ FND-01 đang REVIEW trong thay đổi hiện tại.
Người nhận việc ghi owner trong bản task dùng template; chưa có owner mặc định.

## Baseline và thứ tự

Baseline là MVP được xuất bản cùng bộ khung; mỗi task ghi SHA thực tế lúc nhận.
Có 8 integration tests
dùng fake provider; chưa có live-provider test, crash-kill thật, kiểm chứng
Windows Job Object, immutable vault hoặc sandbox OS.

`G0 → G1 → G2 → G3 → G4 → G5 → G6`

Không mở rộng scope để vượt gate. Có thể chuẩn bị tài liệu của gate sau nhưng
không kích hoạt tính năng phụ thuộc khi gate trước chưa đạt.

| Gate | Mục tiêu | Công việc và thứ tự | Điều kiện ra gate |
| --- | --- | --- | --- |
| G0 — nền phát triển | Quy trình dùng được trên máy mới | FND-01 quy chuẩn/CI/templates; FND-02 chuẩn hóa code và lỗi | Check local đạt; CI Windows/Linux Python 3.10/3.12 đạt; package cài được và entrypoint chạy từ ngoài repo; review scope |
| G1 — hợp đồng dữ liệu | Không chạy từ state/task mơ hồ | DAT-01 schema; REC-01 chuyển trạng thái; REC-02 crash/recovery | Input hỏng bị từ chối trước side effect; schema lạ không bị ghi đè; crash matrix có kết quả và resume không commit trùng |
| G2 — vòng đời tiến trình | Một run, một Worker thực sự | PROC-01 khóa liên tiến trình; PROC-02 containment Windows; PROC-03 lệnh phục hồi an toàn | Test hai OMP process; kill cha/timeout/Ctrl+C không để Worker tiếp tục ngoài kiểm soát; không cần sửa JSON thủ công |
| G3 — cách ly kiểm thử | Bảo vệ kết quả và dữ liệu nguồn | SEC-01 threat model + thử nghiệm OS; SEC-02 validator/test runner; SEC-03 scope/Git tampering | Worker bị từ chối ghi vào vùng bảo vệ ở cấp quyền; test kết quả gắn đúng tree; phá hoại có test âm; review độc lập |
| G4 — pipeline hữu hạn | Nhiều task có điểm dừng rõ | PIPE-01 hàng đợi; PROV-01 adapter + doctor; PROV-02 retry/failover | Thứ tự ổn định, budget tổng hữu hạn, provider cũ đã dừng trước provider mới; output sai không thành công |
| G5 — lập kế hoạch và DB | Tự động hóa có kiểm soát | PLAN-01 planner/reviewer; ENV-01 actions/DB | Review diff từ feature baseline; request hành động có ID/hash/phạm vi/approval; DB dev riêng và replay không lặp side effect |
| G6 — trải nghiệm và phát hành | Người khác cài và vận hành được | UX-01 status/log; REL-01 package/docs/release | Hướng dẫn máy sạch chạy được, smoke có người cho phép, policy secrets, review phát hành; push là lựa chọn tường minh |

## Backlog có tiêu chí nghiệm thu

### G0

- **FND-01 [REVIEW]** — AGENTS, contributing, contracts, ADR, task/handoff,
  check local và CI. Evidence: lệnh check/package smoke và CI URL khi có.
  CI chưa chạy trên server thì không ghi G0 DONE.
- **FND-02 [TODO, sau FND-01]** — thêm type hints cho code hiện có; tách CLI,
  lỗi dữ liệu và lỗi thực thi; bỏ assert trong command runner mặc định;
  test phát hiện 0 tests vẫn fail dưới Python optimize. Không refactor hành
  vi cùng lúc. Phạm vi: main.py, tests, config/docs khi liên quan.
  Gói giao việc hiện hành: [FND-02](tasks/FND-02.md), trạng thái READY.

### G1

- **DAT-01** — validate top-level type, unknown fields, version, enum phase,
  run_id, PID, số retry/budget, argv và đường dẫn trước mọi side effect;
  phân biệt missing/invalid/unsupported. Config/task/state đều versioned.
  Test JSON hỏng, list/null thay object, bool thay int, traversal và path Windows.
- **REC-01** — bảng transition và invariants thực thi được. Ghi nhận kết quả
  test bằng exit code, tree identity và command identity; thay đổi tree/config
  làm mất hiệu lực kết quả cũ. Giải quyết no-op task tường minh.
- **REC-02** — fault injection tại clone dở, checkout, spawn trước ghi PID,
  ghi PID, test đạt trước checkpoint, stage trước commit, commit trước state
  và state replace lỗi. Mỗi case có expected: tự resume, chặn an toàn hoặc
  cần recovery command. Không gọi "mất điện an toàn" chỉ từ mocked exception.

### G2

- **PROC-01** — hai process tranh cùng lock: chỉ một process được spawn Worker;
  test lock tự nhả khi owner bị kill và PID tái sử dụng không bị nhận nhầm.
- **PROC-02** — thử Windows Job Object hoặc cơ chế OS tương đương; giải quyết
  khoảng trống spawn/attach/checkpoint, con/cháu tách phiên và tiến trình cha
  chết. Timeout cleanup hữu hạn; lỗi cleanup giữ trạng thái cần điều tra.
  Không có bằng chứng kill descendants thì không chuyển provider.
- **PROC-03** — `recover`/`abort` có đối soát và audit event, không hướng dẫn
  người dùng tùy ý xóa child_pid/đổi phase. Test owner đang sống, PID lạ,
  checkout dở và run đã xong. Không tự xóa chứng cứ hoặc sửa repo nguồn.

### G3

- **SEC-01 [DONE]** — ADR quyết định cơ chế cách ly khả thi trên Windows: principal,
  quyền đọc/ghi, credentials, network, vùng runtime/vault và process model.
  Làm thử nghiệm nhỏ trước khi xây framework. Nếu chưa đạt thì chỉ công bố
  chế độ Worker hợp tác và chặn các tuyên bố bảo mật tương ứng.
- **SEC-02** — test runner ngoài quyền Worker, inputs/config/import hooks
  được kiểm soát, manifest nằm ngoài quyền ghi. Test sửa test, conftest,
  sitecustomize, runner config, fake output/exit và kết quả cũ. Hash cạnh file
  không được nghiệm thu là immutable vault.
- **SEC-03** — path canonicalization, symlink/junction/reparse, ADS, casing,
  reserved names; tracked/untracked/ignored; sửa HEAD/index/config/hooks và
  Git object; tests có dấu cách/Unicode. Xác định policy và chặn trước khi
  đưa patch ra khỏi clone. Không chạy hooks Worker thay đổi khi commit.

### G4–G6

- **PIPE-01** — nhiều task, thứ tự/dependency/cycle, checkpoint mỗi task,
  feature baseline bất biến, test task fail giữa hàng đợi, resume không bỏ task.
- **PROV-01** — adapter tối thiểu sau provider thứ hai; `doctor` kiểm tra version,
  executable, headless/stdin và auth availability không đọc/in token. Contract
  tests với fake cho success/error/timeout/malformed; live smoke opt-in.
- **PROV-02** — phân loại lỗi có cấu trúc; chuỗi "429" trong code không đủ
  kết luận quota. Retry/cooldown/call budget/wall time hữu hạn và lưu state.
  Hết provider phải BLOCKED, không lặp vô hạn.
- **PLAN-01** — plan output validate, tests có review, budget gọi tổng;
  kiến trúc review từ baseline feature, verdict có cấu trúc, số vòng sửa hữu hạn.
- **ENV-01** — action request không cho thực thi shell/SQL tùy ý từ Worker;
  approval gắn nội dung bất biến, dependency policy, DB dev/backup/replay và
  sự cố giữa migration. Không dùng LLM verdict làm ranh giới quyền OS.
- **UX-01** — status JSON ổn định, event/log có redaction, giới hạn dung lượng;
  TUI/Oh-My-Posh là consumer, không điều khiển state trực tiếp.
- **REL-01** — build/install/smoke từ artifact trên Windows, license do chủ
  dự án chọn, changelog, hướng dẫn vận hành/recovery và release checklist.
  Không tự tuyên bố license khi chưa có quyết định của chủ dự án.

## Gói công việc tiếp theo

1. Review FND-01 và chạy CI khi thay đổi được đưa lên remote theo quyền được cấp.
2. FND-02: chuẩn code/lỗi và regression cho zero-tests; giữ 8 test baseline.
3. DAT-01: schema và validation; sau đó REC-01, REC-02.
4. Chỉ bắt đầu PROC-01 khi G1 có evidence đủ; không nhảy sang dashboard.

Mỗi công việc qua `TODO → IN_PROGRESS → REVIEW → DONE`; `BLOCKED` ghi dependency,
lỗi và điều kiện gỡ chặn. Handoff không tự chuyển REVIEW thành DONE.

## Quyết định điều phối 2026-10-25 sau SEC-01

SEC-01 DONE tại merge `8afc0f750705a5b23e769d481106c28f89f9a9c7`;
[CI main 40154052968](https://github.com/phongnt911/Open-Multi-agent-Pipeline/actions/runs/40154052968)
4/4 success (Windows/Linux x Python 3.10/3.12), package smoke đạt.
Chỉ nghiệm thu threat model và standalone AppContainer fixture proof; chưa
cách ly Worker production. [Evidence merge](reviews/SEC-01-MERGE.md).
G3 vẫn mở, còn SEC-02/SEC-03; không tự đóng gate hoặc mở task tiếp theo.
