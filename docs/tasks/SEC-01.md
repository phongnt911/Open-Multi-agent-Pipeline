# SEC-01: Threat model và proof cách ly Windows

- Trạng thái: REVIEW; bàn giao 2026-10-24, chưa DONE, chưa đóng G3.
- Gate: G3, mở. Phụ thuộc G2 DONE theo G2-CLOSURE.md.
- Owner: AI thực thi SEC-01 (opencode, DeepSeek V4.1 Flash Free); reviewer:
  điều phối, cần review độc lập cho security claims.
- Base: main `6cae125c947ddeb90c611d921e23786e753d8a73` (PROC-03 merge).
- Nhánh đề nghị: `codex/sec-01-windows-isolation-proof`.

## Đọc trước

AGENTS, COORDINATION, ROADMAP, CONTRIBUTING, CONTRACTS, SDD, README;
ADR-0004/0005; reviews G2-CLOSURE/PROC-03-MERGE. Kiểm Git status và baseline:
160 tests Windows, Ubuntu 160 discovered/16 native skips. Working tree có
các docs/task/review/workspace điều phối chưa commit: không reset/clean/stash,
không nhận tất cả diff làm của mình. Ghi hash/base trước sửa.

## Kết quả mong muốn

Chốt threat model và một proof Windows tối thiểu cho cách ly quyền OS.
Job Object chỉ là lifecycle; clone/hash/whitelist không ngăn Worker cùng user
đọc/ghi repo nguồn, vault, config hoặc credentials. Cần chọn cơ chế dựa trên
thí nghiệm được kiểm, trước khi SEC-02/SEC-03 làm runner/vault/scope enforcement.
Nếu không chứng minh được property thì đánh dấu NOT PROVEN/BLOCKED property đó,
không gọi hợp tác cùng user là sandbox. Không xây framework trong SEC-01.

## Ownership

- docs/security/THREAT-MODEL.md mới.
- docs/decisions/0006-windows-isolation.md mới: lựa chọn, alternatives,
  OS/principal/privilege assumptions, deploy/recovery/cleanup constraints.
- tools/proofs/sec01_windows.py và fixture proof cần thiết, nhỏ và độc lập.
- tests/test_security_proof.py nếu cần kiểm verifier/negative controls.
- docs/tasks/SEC-01.md, docs/handoffs/SEC-01.md; README/CONTRACTS/SDD chỉ sửa
  claims hoặc limits liên quan bằng chứng thật.
- omp_core production, baseline tests, workflow, tools/check.py, dependency,
  AGENTS/COORDINATION/ROADMAP/review history và task/handoff khác: chỉ đọc.
  Nếu proof đòi đổi production phải báo điều phối trước phần phụ thuộc.

## Phạm vi

Threat model bao gồm principal/token, inherited handles, ACL read/write,
source checkout/runtime/audit/vault, environment/credential exposure, network,
process lifecycle và boundary recovery. Tách rõ quyền orchestrator, Worker,
trusted runner và người vận hành. Dùng fake secrets/canary fixture độc quyền;
không đọc credential thật hoặc dùng tài khoản/provider production.

Đánh giá cơ chế Windows native khả thi và chọn một candidate nhỏ để proof.
Chỉ dùng dependency đã có/stdlib/native APIs nếu đủ. Không cài SDK/driver,
container runtime, tạo tài khoản bền vững, sửa ACL/firewall/policy toàn máy
hoặc yêu cầu admin ngoài quyền task. Nếu prerequisite không có, ghi chính xác
bước bị chặn và tiếp tục phần read-only/fixture an toàn. Không hạ tiêu chí.

Không triển khai SEC-02 test runner/vault, SEC-03 path/Git enforcement, cgroups,
provider retry/failover, migration, queue, UI hoặc publish.

## Acceptance bắt buộc

- [ ] AC1: Threat-model matrix assets/actors/actions/trust boundaries/assumptions;
  mỗi security property có evidence hoặc NOT PROVEN, không dựa vào hash cùng quyền.
- [ ] AC2: ADR chọn candidate, có tài liệu Microsoft chính thức và minimum OS;
  phân biệt availability của API với runtime đã thử. So alternatives ngắn gọn.
- [ ] AC3: Proof process Windows thật dưới boundary dự kiến; chứng minh intended
  execution context/identity/rights. Có readiness marker và deadline, không AI thật.
- [ ] AC4: Positive control: Worker ghi đúng allowed fixture được. Negative
  controls: truy cập source/vault/config/audit canary bị từ chối bởi OS theo
  policy, không chỉ bị verifier phát hiện sau. Plain same-user control phải
  chứng minh có thể truy cập canary để tránh false proof do fixture sai.
- [ ] AC5: Matrix cho credentials/env/network/handles/lifecycle: kiểm property
  được claim; property chưa kiểm phải UNPROVEN/unsupported rõ, không dùng label
  network-isolated hoặc credential-isolated nếu chưa có negative proof.
- [ ] AC6: Setup/launch/cleanup failure fail-closed; không fallback silently về
  cùng-user unrestricted execution. Không để process/ACL/account/resource bền
  ngoài fixture; cleanup hữu hạn, chỉ resources fixture sở hữu.
- [ ] AC7: Không phá G2 checkpoint/lock/containment/recovery baseline. Không kill
  PID lạ, không mutate repo nguồn để thử attack; dùng canary/sacrificial fixtures.
- [ ] AC8: Evidence Windows có OS/Python/principal/command/exit, before-after
  fixture state, positive/negative controls và limits. Linux CI không thay Windows.
- [ ] AC9: python tools/check.py, focused verifier/proof checks và git diff --check;
  báo test/count/skips/failures/reruns thật. Không skip/nới expected để pass.
- [ ] AC10: Handoff REVIEW kèm hash/file list/AC matrix/proof commands/risks,
  prompt reviewer trực tiếp trên chat. Không DONE/đóng G3, commit/push/merge,
  gọi AI thật, sinh agent phụ hoặc nhận task kế tiếp.

## Điểm dừng và bàn giao

Bảo mật chưa chứng minh thì không ghi đã sandbox Worker. Có thể bàn giao
BLOCKED cho candidate thiếu prerequisite sau khi hoàn thành threat model và
proof độc lập an toàn; điều phối chọn bước tiếp theo, không tự nới gate.
G3 chỉ đóng sau cả SEC-01/SEC-02/SEC-03 và review độc lập tương ứng.
