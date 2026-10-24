# PROC-03: Phục hồi/abort an toàn, có đối soát và audit

- Trạng thái: REVIEW (AI thực thi bàn giao 2026-10-23; chưa DONE/ACCEPTED, G2 mở).
- Gate: G2, vẫn mở đến khi PROC-03 được nghiệm thu và tích hợp.
- Owner: AI thực thi PROC-03 (opencode, DeepSeek V4.1 Flash Free), nhận việc
  2026-10-23; reviewer/điều phối: Codex trong chat chủ dự án.
- Base: main `873cae570e6ee4309676b49808e0cca051154292` (merge PROC-02).
- Nhánh: `codex/proc-03-recovery-cli`; HEAD vẫn là base, chưa commit/push/merge.

## Đọc trước và baseline

Đọc AGENTS, COORDINATION, ROADMAP, CONTRIBUTING, CONTRACTS, SDD, README,
ADR-0002/0003/0004, task/handoff PROC-02 và review r4. Xác nhận Git status,
base và hash code/tests; giữ nguyên mọi diff docs/workspace/review có sẵn.
Baseline Windows: 137 tests (117 trước containment + 16 native + 4 errors).
Linux: xác nhận số thực tế/skips từ integration evidence; không suy từ Windows.
Không reset/clean/stash hoặc nhận file đang do người khác sửa.

## Vấn đề và kết quả mong muốn

Sau crash, child_pid guard ngăn resume nhưng hướng dẫn hiện tại còn đòi sửa
JSON thủ công. Cần CLI recover/abort rõ mục đích, kiểm tra checkpoint/repo và
quyền sở hữu trước mutation, lưu audit và giữ evidence. PID number hoặc
process đã exit không tự chứng minh ownership hay cả tree đã dừng.

## Ownership

- omp_core/main.py: CLI, orchestration recovery/abort và state validation liên quan.
- omp_core/windows_job.py: mặc định chỉ đọc; thay đổi lifecycle chỉ khi có lý do
  trực tiếp và regression, không làm suy yếu atomic containment PROC-02.
- tests/test_recovery_cli.py mới; tests cũ chỉ đổi fixture thật sự cần thiết,
  ghi rõ lý do, giữ expected/invariant và redaction.
- docs/tasks/PROC-03.md, docs/handoffs/PROC-03.md, README, CONTRACTS, SDD;
  docs/decisions/0005-safe-recovery.md để ghi chính sách/compatibility.
- AGENTS/COORDINATION/ROADMAP/workflow/checker/dependency/review history và
  task/handoff đã nghiệm thu ngoài PROC-03: chỉ đọc.

## Phạm vi và quyết định thiết kế

Triển khai recover/abort tối thiểu, tuần tự, fake Workers. Trước code ghi rõ
CLI flags, điều kiện từng action/phase, audit persistence và crash boundaries
trong ADR. Chọn semantics cụ thể: recover không tự chạy Worker; abort không
được báo stopped nếu chưa chứng minh tree dừng. Không chỉ bỏ guard và gọi đó
là recovery an toàn. Không kill PID từ state/PID-only; nếu không có bằng chứng
ownership hoặc containment đáng tin cậy thì refuse và giữ evidence. Trường hợp
cần xác nhận người vận hành phải explicit, gắn run/state digest, ghi audit;
không biến xác nhận thành proof OS hoặc mặc định chạy tự động.

Không thêm provider failover/retry, queue, sandbox/vault/cgroups, migration
ngầm, auto-push, dependency runtime hoặc framework. Checkpoint thay đổi cần
version/strict validation và compatibility rõ; ưu tiên không thay schema nếu
hợp đồng hiện có đáp ứng. Không tự thêm phase/schema chỉ để né kiểm chứng.

## Acceptance bắt buộc

- [x] AC1: ADR/contract định nghĩa CLI recover/abort, preconditions/side effects,
  refusal policy, stale/unknown/live owner và OS evidence limits.
- [x] AC2: Validate input/state trước lock, revalidate trong exclusive OS lock;
  run/resume/recover/abort tranh chấp không tạo hai Worker hoặc mutation song song.
  State digest/run ID mismatch phải refuse, giữ bytes/evidence.
- [x] AC3: Owner còn sống/lock đang giữ, PID lạ/reused, process ownership unknown,
  checkpoint hỏng/version không hỗ trợ đều fail-closed; không kill PID lạ,
  không spawn replacement, không tự clear child_pid chỉ vì root không còn.
- [x] AC4: recover/abort có audit event gắn action/run ID/state digest trước-sau,
  kết quả và lý do; không secrets. Policy atomicity/partial audit/save failure
  rõ; lỗi audit/checkpoint không báo success giả, giữ cause chain đã sanitize.
- [x] AC5: Phủ interrupted preparing/clone dở, worker/verifying/committing,
  complete/no_change và checkpoint legacy v1. Không bỏ provenance/test evidence
  hoặc đổi phase tùy tiện để hoàn tất; complete vẫn đối soát intent/tree/commit.
- [x] AC6: abort không xóa clone/log/checkpoint/repo nguồn hoặc commits; idempotent
  trong semantics đã định nghĩa. recover không tự áp dụng/push sửa đổi nguồn.
- [x] AC7: Regression trước fail/sau pass; Windows real process readiness,
  owner alive/dead, contention, PID-only sentinel không bị kill, failure save/audit
  và interruption giữa các boundary; cleanup test hữu hạn và chỉ fixture-owned.
- [x] AC8: Giữ baseline tests, containment native, redaction/cause/context,
  KeyboardInterrupt, state/provenance strictness. Không skip/nới expected để pass.
- [x] AC9: python tools/check.py, focused recovery CLI suite, git diff --check
  đạt Windows; báo OS/Python/count/exit/skips và checks Linux/3.10 chưa chạy.
  Handoff đủ base/hash/file list/AC mapping/negative controls/failures/risks.
- [x] AC10: Bàn giao REVIEW; không DONE/đóng G2, commit/push/merge, gọi AI thật,
  sinh agent phụ hoặc nhận task khác. Gửi prompt reviewer trực tiếp trên chat.

## Điểm dừng

Nếu không thể chứng minh safe mutation/ownership trên nền tảng hiện có,
ghi BLOCKED cho action đó và bằng chứng, tiếp tục phần độc lập an toàn.
Không hạ acceptance thành “xóa PID để resume”. Nếu scope cần mở rộng, báo
điều phối trước phần phụ thuộc; không tự thêm security claim.

## Evidence base tích hợp

Merge PROC-02: `873cae570e6ee4309676b49808e0cca051154292`, PR #2.
PR CI 39947394148 đạt 4/4; Windows 137 OK, Ubuntu 137 discovered/16 skipped.
Main CI và post-merge check được ghi tại docs/reviews/PROC-02-MERGE.md trước giao.
Production main.py SHA-256: `eda722bd69328db368ae3cb61b97f2ac4174f5adc67a60f1cdf7d9b2b63ef8e2`.
windows_job.py: `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d`.

Main CI PROC-02 `39947810174`: completed/success, 4/4 jobs và package smoke.
Windows 137 tests OK, Ubuntu 137 discovered/16 native skips. Local post-merge
137 OK/91.151s, exit 0. Đây là base chuẩn để nhận PROC-03.
