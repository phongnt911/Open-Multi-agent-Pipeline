# SEC-02: Trusted verification — giao giai đoạn A

- Trạng thái: READY (chỉ giai đoạn A; chưa triển khai production).
- Gate: G3 mở. Phụ thuộc SEC-01 DONE và ADR-0007 Windows/Python đã deploy.
- Người giao/reviewer: AI điều phối; owner: AI thực thi nhận task, ghi tên lúc bắt đầu.
- Base: origin/main chứa ADR-0007 sau CI xanh; ghi exact SHA thực tế trước sửa.
- Nhánh: codex/sec-02-python-proof. Không nhận SEC-03 hoặc giai đoạn B ngầm.

## Mục tiêu và checkpoint bắt buộc

SEC-01 mới chứng minh cmd/curl trên fixture. Chưa chứng minh CPython chạy được
dưới AppContainer hoặc trusted tests/runner inputs đứng ngoài quyền ghi Worker.
Giai đoạn A chỉ xây proof Windows thật dùng fake Python Worker và một oracle
black-box tối thiểu ở process trusted riêng. Review A xong mới chốt thiết kế
giai đoạn B (runner/vault tích hợp OMP). A đạt không đồng nghĩa SEC-02/G3 DONE.

Không gọi unittest/pytest có import code đối kháng trong cùng interpreter rồi
coi exit 0 là proof an toàn: code có thể monkeypatch runner hoặc os._exit(0).
ADR phải ghi rõ giới hạn này; oracle trusted không chạy chung process Worker.
Không hứa an toàn cho arbitrary project tests/plugins nếu chưa chứng minh.

## Đọc trước

AGENTS, COORDINATION, ROADMAP, README, CONTRACTS, SDD; ADR-0004/0006/0007;
THREAT-MODEL; tools/proofs/sec01_windows.py; tests/test_security_proof.py;
docs/reviews/SEC-01-MERGE.md. Kiểm git status/diff, giữ file bẩn có sẵn.
Không reset/clean/stash hoặc stage all. Không sinh agent phụ.

## Ownership giai đoạn A

Được tạo/sửa:
- tools/proofs/sec02_windows.py: proof độc lập, stdlib/native APIs.
- tests/test_trusted_verification_proof.py: regression và verifier.
- docs/decisions/0008-trusted-verification.md: lựa chọn/process/principal/oracle.
- docs/handoffs/SEC-02.md; task này chỉ trạng thái/evidence; THREAT-MODEL chỉ
  thêm property evidence/NOT PROVEN mới.

Chỉ đọc: omp_core, SEC-01 proof/tests, mọi baseline tests, pyproject, workflow,
checker, AGENTS/COORDINATION/ROADMAP, các review history. Reuse helpers proof
SEC-01 nếu đủ; không tạo framework hoặc sửa helper accepted để tiện code.
Nếu cần đổi production/policy/schema thì dừng phần phụ thuộc và báo reviewer.

## Phạm vi tối thiểu

1. Copy CPython runtime/stdlib cần thiết vào fixture sở hữu; grant read/execute
   cho AppContainer, không sửa ACL runtime thật hoặc policy toàn máy. Fake
   Worker Python được ghi workspace riêng, không ghi source/vault/runner/config.
2. Trusted manifest/test inputs/expected results nằm trong fixture vault
   ngoài quyền ghi Worker. Process trusted riêng kiểm artifact/run/tree/command
   identity; không nhận Worker tự khai PASS/exit/output làm chứng cứ kiểm thử.
3. Runner dùng argv, cwd/environment tường minh, Python isolation flags khi
   phù hợp; chứng minh sitecustomize/PYTHONPATH/config từ workspace không lọt.
4. Stop/reap fake Worker trước trusted verification; Job Object lifecycle và
   deadline rõ. Không chạy hai Worker hoặc sửa scope containment/recovery cũ.
5. Chỉ fake data/canaries, không AI thật, credentials thật hoặc Internet probe.
   Không cài account/driver/SDK/runtime dependency mới, không machine-wide ACL.

## Acceptance A1–A9

- [ ] A1: ADR-0008 có trust/process/principal matrix, lựa chọn tối thiểu và
  oracle riêng; phân biệt fixture proof với OMP integration/arbitrary tests.
- [ ] A2: Native CPython fake Worker chạy thực sự dưới AppContainer trên
  Windows/Python 3.12 và 3.14, có readiness/deadline/token/profile SID; không
  fallback cùng user khi prerequisite thiếu. Không chỉ mock native launch.
- [ ] A3: Allowed workspace read/write thành công; OS từ chối ghi vault/runner/
  manifest/config canaries, bytes/hash không đổi; same-user control truy cập
  được để loại trừ fixture ACL sai. Không biến hash thành OS boundary.
- [ ] A4: Các attack riêng: sửa test/expected, conftest/sitecustomize/import
  hooks, runner config, fake PASS stdout, exit 0 sớm, replay kết quả cũ,
  monkeypatch unittest/runner trong Worker. Oracle trusted vẫn phát hiện
  candidate sai, không tự complete từ claim Worker.
- [ ] A5: Task/run/tree/command/timeout identity bound trong evidence trusted;
  mutation sau PASS hoặc đổi manifest bị từ chối. Oracle không import code
  đối kháng vào chính interpreter đang quyết định PASS.
- [ ] A6: Failure/timeout/interrupt/setup/query/attach/save errors cleanup
  bounded, giữ cause/type đã sanitize, chỉ owned resources. Không orphan,
  không retry/fallback; không claim owner hard-kill/power-loss nếu chưa test.
- [ ] A7: Negative control trên source copy phá guard phải fail trước/pass
  sau; readiness thật, child còn sống trước kill khi cần, finally cleanup
  độc lập và sentinel ngoài fixture không bị chạm.
- [ ] A8: Giữ 233 baseline behaviors, không thêm skip Windows/nới assertion.
  python tools/check.py và focused tests trên Windows 3.12/3.14; package/
  outside-source smoke nếu có liên quan. Ghi count/exit/version/time/NOT RUN.
- [ ] A9: Handoff REVIEW, hash/file list/AC mapping/commands/limits + prompt
  reviewer trên chat. Không DONE, commit/push/merge hoặc đóng G3. Nếu
  prerequisite không đạt, ghi BLOCKED property đó; không hạ chuẩn để pass.

## Handoff và điểm dừng

Bàn giao giai đoạn A ở REVIEW. Reviewer kiểm code/tests/raw native evidence và
tự chạy lại; nếu đạt mới giao B bằng task/ADR cập nhật. Không triển khai
production runner/vault trong A, không gọi provider thật và không claim generic
Python tests đã được bảo vệ. SEC-03 path/Git enforcement vẫn ngoài scope.

## Prerequisite runtime hiện biết

Xem ADR-0007: bản uv Python 3.14 trên host điều phối hiện chạy low integrity,
không tự tạo AppContainer profile được (HRESULT 0x80070005). Không tắt policy
hoặc tăng quyền để che lỗi. Dùng controller Python 3.12 medium-integrity tạo
fixture/profile rồi kiểm fake Worker Python 3.12/3.14 dưới AppContainer. Nếu
không chứng minh được đúng principal/runtime, dừng property đó ở BLOCKED.
Không dùng kết quả unit/CI compatibility thay raw native proof.
