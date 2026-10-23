# PROC-02: Containment cây tiến trình trên Windows

- Trạng thái: REVIEW (revision R1–R3 và R1a sau review r3; reviewer yêu cầu CHANGES_REQUIRED; G2 vẫn mở).
- Gate: G2, vẫn mở; không đồng nghĩa sandbox/security gate đã đạt.
- Phụ thuộc: PROC-01 ACCEPTED r2, PR #1 đã merge; kiểm tra tree/test/CI sau merge theo báo cáo điều phối.
- Owner: AI thực thi PROC-02 (opencode, DeepSeek V4.1 Flash Free), nhận việc 2026-10-23; base main `12f2da99b8c983308adc4f4cbae782affa1ef749`, nhánh `codex/proc-02-containment`.
- Reviewer: AI điều phối trong chat chủ dự án.
- Base HEAD main: `12f2da99b8c983308adc4f4cbae782affa1ef749`.
- Nhánh đề nghị: `codex/proc-02-containment`; không tạo worktree/agent phụ nếu chưa được yêu cầu.

## Baseline phải bảo toàn

- Production main.py: `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0`.
- tests/test_process_lock.py: `7bc208c108de9e949560dbf7819f3a88395a90fa6249e5d56db809d82a7206a3`.
- tests/test_crash_recovery.py: `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820`.
- 117 tests baseline: 111 G1 + 6 PROC-01; không skip/nới expected để pass.

Đọc status/diff/hash trước sửa. Working tree có nhiều docs/review/workspace
chưa commit từ các công việc trước: giữ nguyên, không reset/clean/stash hoặc
coi tất cả diff là của mình. Hash/base lệch phải đối soát trước phần phụ thuộc.

## Vấn đề và kết quả mong muốn

OS lock serialize orchestrator nhưng không dừng Worker khi owner chết.
Hiện execute() dùng Popen rồi lưu PID; stop() dùng taskkill /T /F trên Windows,
chưa bound riêng lệnh taskkill. Root PID đã exit không chứng minh các con/cháu
đã dừng. PROC-01 cố ý giữ child_pid guard sau hard-kill, không tự clear để resume.

PROC-02 cần containment Windows bằng cơ chế OS được chứng minh (Job Object
hoặc tương đương), gồm Worker và verification process. Worker không được thực
thi task trước khi containment có hiệu lực. Mọi gap create/attach/start/save
phải có policy và evidence; không gọi spawn-then-attach là atomic hoặc an toàn
chỉ vì happy path pass. Owner chết ở boundary không được để code Worker/con
cháu tiếp tục chạy ngoài kiểm soát.

Giải quyết một lifecycle tối thiểu dùng stdlib/native APIs, không tạo provider
framework. Làm proof nhỏ trên Windows trước khi tích hợp; nếu cơ chế không
đạt trên nền tảng hỗ trợ, ghi BLOCKED với bằng chứng, không hạ acceptance.

## Ownership

- omp_core/main.py: execute/stop và integration liên quan trực tiếp.
- Có thể thêm một module Windows lifecycle thực sự cần trong omp_core/;
  không module rỗng/interface một implementation/registry. Native ctypes phải
  khai báo argtypes/restype/structures/handle lifetime rõ trên Windows 64-bit.
- tests/test_process_containment.py mới và fixture cục bộ cần thiết.
- docs/tasks/PROC-02.md, docs/handoffs/PROC-02.md; README.md, CONTRACTS.md, SDD.md
  cho behavior/limits thực sự đổi; docs/decisions/0004-windows-containment.md
  cho quyết định OS boundary và policy lỗi.
- Test cũ mặc định chỉ đọc; giữ assertions/cause/redaction/recovery. Nếu fixture
  cần đổi vì boundary mới, ghi rõ từng thay đổi và regression chứng minh không
  nới contract; báo điều phối nếu không thể giữ invariant.
- Không sửa AGENTS/COORDINATION/ROADMAP, workflow/checker/dependency, review
  history, workspace cá nhân hoặc task/handoff đã nghiệm thu của người khác.

## Ngoài phạm vi

Không làm recover/abort CLI PROC-03, retry/failover/provider thứ hai, sandbox
OS/network/credential, test vault, migration hoặc tự nâng checkpoint version.
Không tự claim Job Object là security sandbox cho Worker đối kháng. Không
triển khai Linux cgroups trong task Windows này; giữ POSIX behavior và mô tả
giới hạn trung thực. Không kill PID lạ chỉ từ checkpoint/PID number.

## Acceptance

- [ ] AC1: ADR/proof Windows xác định process model, OS assumptions, ownership
  handles, activation và matrix lỗi. Có nguồn API chính thức khi chọn cơ chế;
  không dựa vào private CPython handles không có compatibility contract.
- [ ] AC2: Containment hiệu lực trước task execution; process launch/attach/start
  failure và owner hard-kill tại từng gap có fault/real-process evidence. Chỉ
  CREATE_SUSPENDED rồi attach không đủ nếu gap owner-death vẫn để orphan không
  được quản lý; phải giải quyết hoặc BLOCKED rõ. Không fallback âm thầm sang
  Popen không containment khi OS setup lỗi. Handle policy không cho child giữ
  sống containment khi owner chết.
- [ ] AC3: Timeout/Ctrl+C/operation failure dừng đúng Worker và descendants trong
  bound cấu hình rõ; root exit trước con/cháu cũng phải được đối soát/dừng trước
  verification hoặc complete. Không coi process.poll()!=None là tree đã dừng.
- [ ] AC4: Test Windows real process với Worker tạo con và cháu; owner hard-kill,
  timeout, Ctrl+C, root early-exit và child thử tách session/breakaway có expected
  policy. Sau cleanup kiểm process/marker không còn chạy trong quan sát hữu hạn,
  không chỉ tin return code taskkill; không kill process không thuộc fixture.
- [ ] AC5: Cleanup/close/query/wait đều bounded; lỗi gốc + cleanup/save error có
  cause truy nguyên, không argv/token trong public chain/last_error. Nếu không
  chứng minh tree dừng, fail closed: không spawn replacement, không auto-clear
  guard hoặc ghi complete. Giữ clone/checkpoint evidence để PROC-03 đối soát.
- [ ] AC6: Normal success/worker fail/test fail/no-op/crash resume vẫn đúng;
  config/task v1 và checkpoint v2 giữ contract strict, không migration ngầm.
  OS lock/child_pid semantics PROC-01 và FND-02 redaction/cause không hồi quy.
- [ ] AC7: 117 baseline behaviors giữ nguyên; có regression lỗi trước/sau cho
  behavior sửa. Windows-native tests dùng fake Worker, readiness/deadline,
  negative control và finally cleanup; không test chạy AI thật hoặc orphan vô
  hạn. Nếu chỉ fault injection một case, ghi rõ không phải hard-kill thật.
- [ ] AC8: Python >=3.10; import/behavior POSIX không bị Windows API làm hỏng.
  Native tests có platform scope minh bạch; không skip Windows cases để pass.
  python tools/check.py, test containment riêng và git diff --check đạt Windows;
  báo số tests/skips theo OS và các checks chưa chạy.
- [ ] AC9: Handoff đủ base/hash/file list/AC matrix/commands/OS/count/exit và
  các lần fail/rerun; docs khớp guarantee thực tế. Chưa có proof containment
  thì không ghi đã ngăn orphan, không tuyên bố mất điện/OS sandbox an toàn.
- [ ] AC10: Bàn giao REVIEW, không tự DONE/ACCEPTED/đóng G2; không nhận PROC-03.
  Không commit/push/merge, dependency runtime mới, agent phụ hoặc AI quota.

## Trình tự và kiểm chứng

1. Đọc AGENTS, COORDINATION, CONTRIBUTING, ROADMAP, CONTRACTS, README, SDD,
   PROC-01-r2/PROC-01-MERGE và task này. Ghi owner/base/status/hash, chạy baseline.
2. Làm proof native với fixture riêng và ma trận gaps trước khi sửa production.
   Không ném toàn bộ main.py vào refactor; chỉ tích hợp lifecycle đã có proof.
3. TDD cho đường lỗi/cleanup, test Windows real processes tuần tự có timeout.
   Process count/exit evidence phải ngoài allowed task files và có cleanup.
4. Chạy kiểm tra cần thiết, đọc diff và cập nhật docs/handoff. Nếu WinError 5
   os.replace tái diễn, giữ log/traceback; không tự kết luận scanner hoặc gọi
   retry pass là sửa root cause.
5. Gửi prompt reviewer trực tiếp trong chat cùng handoff; giữ REVIEW.
