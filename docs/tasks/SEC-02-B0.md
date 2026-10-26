# SEC-02-B0: Chốt thiết kế tích hợp và probe native

- Trạng thái: **REVIEW**; thiết kế/probe B0 đã bàn giao, R1–R3 và R1a đã sửa, chờ review lại. Không phải activation production, không DONE, G3 vẫn mở.
- Gate: G3 mở. Phụ thuộc SEC-02 A ACCEPTED + merged PR #6, main CI 40270938241 success 2/2.
- Người giao/reviewer: AI điều phối. Owner: AI thực thi nhận prompt; một owner tuần tự, không agent phụ.
- Base: main `441fe67e381b5ea0967c99ea32ed4498e81d945f`.
- Branch: `codex/sec-02-b0-integration-design`, đã tạo từ đúng base; working tree có docs/workspace/history bẩn ngoài scope, phải giữ nguyên.
- Design: `docs/decisions/0009-sec02-integration-boundary.md` (Proposed).
- Plan: `docs/superpowers/plans/2026-10-26-sec02-b0.md`.

## Kết quả cần bàn giao

Một thiết kế B1 có đủ quyết định để triển khai sau review, kèm probe Windows chứng minh hoặc từ chối rõ ràng các giả thuyết layout/native process. B0 không tích hợp production và không được tự gọi việc chưa khả thi là PASS.

## Đọc trước

AGENTS, COORDINATION, ROADMAP, README, CONTRACTS, SDD; ADR-0004/0006/0007/0008/0009; threat model; SEC-02 A task/handoff/review r5 và MERGE; luồng `_start_process` → `execute` → `advance`, windows_job start/attributes/stop, validation/evidence/provenance/recovery. Không bỏ qua việc A chỉ chứng minh fixture.

## Ownership — đúng năm file

1. Tạo `tools/proofs/sec02_integration_windows.py`: standalone probe/CLI, stdlib/native Windows APIs.
2. Tạo `tests/test_integration_design_proof.py`: verifier/contract/fault regressions portable Windows 3.12/3.14.
3. Cập nhật `docs/decisions/0009-sec02-integration-boundary.md`: đo/so sánh/chốt proposal B1 bằng evidence; trạng thái Proposed cho đến reviewer quyết định.
4. `docs/tasks/SEC-02-B0.md`: owner/trạng thái/evidence; không hạ acceptance hoặc tự DONE.
5. Tạo `docs/handoffs/SEC-02-B0.md`: handoff theo template, kèm prompt reviewer trên chat.

Evidence-only: `.omp/sec02-b0-*`. Không đưa runtime copies/log/token vào Git.

Chỉ đọc: toàn bộ `omp_core`, 321 baseline test behaviors và hai proof A/SEC-01, checker/workflow/dependency, sáu files accepted A, ROADMAP/COORDINATION/AGENTS, plan điều phối và review history. Không cài runtime/dependency hoặc đổi Git config. Nếu cần sửa ngoài ownership thì ghi dependency và dừng phần đó.

## Probe contract

- `python -m tools.proofs.sec02_integration_windows run --out <json>`: chạy owned native fixture; exit 0 chỉ khi các property bắt buộc có evidence PASS, exit nonzero với FAIL/BLOCKED khi thiếu prerequisite hoặc stop chưa chứng minh.
- `python -m tools.proofs.sec02_integration_windows verify <json>`: kiểm raw fields/claims/identity/schema; invalid trả nonzero không traceback, không chỉ kiểm boolean PASS.
- Evidence schema riêng `omp.sec02.b0.integration-proof.v1`; ghi controller/runtime versions, principal/token, layout, property results, exit/deadline, cleanup, source hashes, NOT RUN/NOT PROVEN. Không lạm dụng schema state production cho probe.
- Cả Worker 3.12 và 3.14 phải thực thi native; thiếu runtime là BLOCKED, không giả lập để PASS. Unit tests không cần chạy controller lowbox API bằng interpreter không đủ prerequisite.
- Runtime copy/ACL/label chỉ trong fixture được sở hữu, không machine-wide. Per-child execution timeout 10s, stop/reap deadline 5s trong probe; chưa thay timeout 10s của production Job cleanup.

## Acceptance B0-1–B0-10

- [ ] **B0-1:** Mapping code hiện tại tới proposal B1: process attributes/env, checkout/vault layout, test authority, candidate/evidence/commit boundary, complete/no_change/recover/abort. Mọi thay đổi production tương lai có file/API và lý do, không chỉ sơ đồ chung.
- [ ] **B0-2:** Native combined SECURITY_CAPABILITIES + JOB_LIST + HANDLE_LIST; child suspended và đã ở Job trước simulated PID checkpoint; readiness chỉ xuất hiện sau save+resume. Job handle không inherit; setup lỗi không create/attach gap hoặc same-user fallback.
- [ ] **B0-3:** Hai CPython Worker có sys.version đúng minor, isolated=1, safe_path=true, AppContainer/profile SID khớp, 0 capabilities. Env fake-secret không truyền; allowed fixture read/write thành công.
- [ ] **B0-4:** So L1 profile workspace và L2 ordinary owned run/checkout layout. Controller vault/state canaries bị OS từ chối đọc/ghi, bytes/hash giữ nguyên; same-user control truy cập được. Ghi ACL/integrity observations và lỗi thật. Nếu một layout không khả thi, property đó BLOCKED và ADR chọn hoặc chặn phương án tương ứng; không đánh global PASS khi property bắt buộc thiếu.
- [ ] **B0-5:** Handle control: stdin/log handles được cấp tường minh, fake protected handle ngoài allowlist không sử dụng được. Không cấp thêm quyền/capability để làm test xanh, không thử credential/network thật.
- [ ] **B0-6:** Fault attribute/create/save/resume/query và root exit còn descendant có trigger xác nhận đã chạm; cleanup bounded/owned, không launch tiếp khi stop unknown, cause/context giữ stage an toàn, không leak argv/fake secret. Native descendant có readiness và finally cleanup độc lập.
- [ ] **B0-7:** Một source-copy guard-removal negative control thật: source hiện tại reject, bản bỏ guard làm regression fail; sentinel ngoài fixture không đổi. Verifier từ chối raw/claim mâu thuẫn, missing runtime, exit bool/nonzero, timeout, fake readiness, cleanup unknown; 0 tests không được coi đạt.
- [ ] **B0-8:** ADR có compatibility matrix và exact proposal config/task/state/evidence versions + legacy read/resume behavior; schema/layout chưa được triển khai. Dependency table chỉ ra các yêu cầu CONTRACTS/SEC-03/NOT PROVEN còn chặn production activation. Không auto-migrate hoặc biến attestation thành OS proof.
- [ ] **B0-9:** 321 baseline behaviors/files không đổi; focused/full Windows Python 3.12/3.14 đều chạy, 0 skip mới/nới assertion; `git diff --check` đạt. Tổng test là 321 + số test mới thực tế, không bịa count trước khi chạy. Native proof/controller evidence ghi riêng, không dùng CI/unit thay OS proof.
- [ ] **B0-10:** Handoff REVIEW hoặc BLOCKED ghi base/branch/source hashes/file list, AC mapping, fail-before/pass-after, OS/full Python/count/exit/skips, mọi check chưa chạy và tài nguyên còn lại; giữ G3 mở, không commit/push/merge/DONE hoặc tự giao B1.

## Lệnh kiểm chứng tối thiểu

```powershell
python -m tools.proofs.sec02_integration_windows run --out .omp/sec02-b0-evidence.json
python -m tools.proofs.sec02_integration_windows verify .omp/sec02-b0-evidence.json
python -m unittest tests.test_integration_design_proof -v
python tools/check.py
.omp/python-runtimes/cpython-3.14.8-windows-x86_64-none/python.exe -m unittest tests.test_integration_design_proof -v
.omp/python-runtimes/cpython-3.14.8-windows-x86_64-none/python.exe tools/check.py
git diff --check
```

Dùng interpreter 3.14 hiện có tương đương nếu path trên không tồn tại, ghi path/version; không tự tải/cài. Native controller 3.12 chạy cả hai Worker. Thiếu prerequisite ghi BLOCKED property, không sửa policy toàn máy.

## Điểm dừng và bước tiếp theo

Không có đường chạy mới trong `omp_core`; không schema/CLI migration, activation, provider thật, sandbox claim production, containment/recovery refactor, SEC-03/G4/UI. Nếu probe phát hiện blocker, giữ evidence và đưa phương án cụ thể vào ADR; reviewer quyết định trước khi giao B1.

B0 là công việc thiết kế/probe do điều phối giao tiếp sau merge A. Sáu accepted hashes A là baseline bất biến của task này; điều phối chỉ cập nhật ROADMAP/gói giao việc, không sửa lịch sử nghiệm thu A.

## Evidence chuẩn bị giao việc — điều phối, 2026-10-26

| Check | Kết quả |
| --- | --- |
| Branch/base | codex/sec-02-b0-integration-design @ 441fe67e381b5ea0967c99ea32ed4498e81d945f |
| python tools/check.py, Windows Python 3.12.7 | 321 tests OK, 106.875s, exit 0, 0 skipped; log .omp/sec02-b0-coordinator-check.log |
| git diff --check | exit 0 |
| Protected production/tests/tools/accepted-A diff vs HEAD | exit 0 |
| Preserve snapshot | 129 file có sẵn: 128 giữ nguyên, chỉ ROADMAP được điều phối cập nhật có chủ đích; không drift ngoài scope |

Đã self-review task/design/plan: plan Task 1 bao phủ schema/raw evidence, Task 2 native layout/process/failure controls, Task 3 compatibility/dependencies/A7/handoff. Đây là kiểm tra tài liệu của điều phối, không phải nghiệm thu độc lập kết quả B0.

Chưa chạy native B0 hoặc focused B0 vì code chưa được viết; chưa chạy lại local Python 3.14 trong lượt chỉ sửa tài liệu này. Evidence main A trên hai phiên bản nằm ở CI 40270938241, không được ghi thành B0 đã PASS. Tài liệu mới chưa commit/push; AI thực thi phải giữ chúng và toàn bộ dirty files ngoài ownership.

## Evidence thực thi B0 — owner, 2026-10-26

- Owner: AI thực thi nhận prompt từ điều phối; một owner tuần tự, không subagent. Base/branch giữ nguyên `441fe67e381b5ea0967c99ea32ed4498e81d945f` / `codex/sec-02-b0-integration-design`; 70 dirty/untracked path ngoài ownership giữ nguyên.
- File ownership: `tools/proofs/sec02_integration_windows.py`, `tests/test_integration_design_proof.py`, `docs/decisions/0009-sec02-integration-boundary.md`, `docs/tasks/SEC-02-B0.md`, `docs/handoffs/SEC-02-B0.md`. Evidence-only trong `.omp/sec02-b0-*`.
- Native: `run --out .omp/sec02-b0-evidence.json` exit 0, `verdict=PASS`; `verify` exit 0. Evidence sha256 `0cc2b031e59e50747d5407c58c0772b84b8140fe9dd5c1a4991fe15510013b87`; probe sha256 `19a1f952441f473f788980bca83e4635da49d438aabf3c35f864614f2d0b31ad`. Controller CPython 3.12.7; Worker 3.12.7 + 3.14.8 native.
- Property B0-2…B0-7 `PASS`; `not_run=[]`; sáu verifier negative control bị reject; cleanup không lỗi, không tài nguyên còn lại.
- Focused `tests.test_integration_design_proof`: 32 tests OK trên 3.12.7 và 3.14.8, exit 0. Full `python tools/check.py`: 353 tests (321 baseline + 32 mới) OK trên cả hai interpreter, exit 0, 0 skip.
- `git diff --check` exit 0. Không commit/push/merge, không DONE, G3 mở, không giao B1.
- NOT RUN/NOT PROVEN: network isolation, owner hard-kill/power-loss, provider/credential thật, arbitrary pytest/plugin trong lowbox, activation production. L2 fixture không có mandatory label riêng; B1 phải đặt label tường minh và đo lại trên `.omp/runs` thật.
- AC mapping, fail-before/pass-after và prompt reviewer: `docs/handoffs/SEC-02-B0.md`.

## Evidence sửa review SEC02B0-R1–R3 — owner, 2026-10-26

- Phạm vi: chỉ năm file ownership B0; evidence-only `.omp/sec02-b0-*`. Không sửa review history, không commit/push/merge/DONE, không đóng G3, không triển khai B1.
- **R1 (P1):** verifier bắt buộc `layout.L1/L2.per_runtime` typed đủ `3.12`/`3.14`; validate từng case (status/token/checkpoint/IO/denial/handle/cleanup); đối soát aggregate với case; `runtimes` summary phải khớp case của layout được chọn; protected-handle/stdio/privilege aggregate lấy trên cả bốn case; reject cleanup error của case và descendant; lưu `layout.canaries.{vault,state}.before/after` SHA-256 và đối soát `hashes_unchanged` thay vì chỉ boolean. Mutations: denial/token/protected contradiction, mất `per_runtime`, case/descendant cleanup error, canary mismatch, summary mismatch — tất cả bị reject.
- **R2 (P2):** ownership guard bắt đầu từ allocation đầu tiên: partial stdio (`DuplicateHandle`/`SetHandleInformation`) đóng handle đã tạo; protected handle đóng nếu inherit setup lỗi; `UpdateProcThreadAttribute` lỗi thì `DeleteProcThreadAttributeList`; `_OwnedFixture.allocate` cleanup phần đã tạo; `FreeSid` non-NULL ghi failure; `cleanup.handles_closed` suy ra từ CloseHandle thay vì ghi cứng.
- **R3 (P2):** `_security_observation` tách DACL/label; mỗi bước native `_check`; `label_query` chỉ `ok`/`absent`, `failed` + error khi truy vấn lỗi; `label_sid=null` chỉ với `label_query=absent`. Native L1 `ok:S-1-16-4096`, L2 `absent:null`.
- Native: `run --out .omp/sec02-b0-evidence-r2.json` exit 0, `verdict=PASS`; `verify` exit 0. Evidence sha256 `e70d232e566a2a1945f174695acd5943b225fe96a1932cc6e4f862dc74333912`; probe sha256 `680eb4f635bcc7cfdbf8a746efec977dd3a6cc506d1cb176599e55982b9837e9`; tests sha256 `a119b2a24441843aa2ed9c57a362574ba11981f73397aae1d0315e2f766dee63`. Evidence gốc `0cc2b031…13b87` giữ nguyên nhưng không còn verify dưới verifier chặt hơn.
- 14 verifier negative controls trong evidence đều rejected; `.omp/sec02-b0-r1-probes.py` xác nhận 9/9 mutation R1/R3 bị reject trên evidence native mới.
- Focused `tests.test_integration_design_proof`: 58 tests OK trên 3.12.7 và 3.14.8, exit 0, 0 skip. Full `python tools/check.py`: 379 tests (321 baseline + 58 mới) OK trên cả hai interpreter, exit 0, 0 skip.
- `git diff --check` exit 0; preservation audit: 128 file giữ nguyên, chỉ `docs/ROADMAP.md` thay đổi có chủ đích, `protected_diff_exit=0`.
- NOT RUN/NOT PROVEN giữ nguyên: network isolation, owner hard-kill/power-loss, provider/credential thật, arbitrary pytest/plugin trong lowbox, activation production, B1. L2 không có explicit label là quan sát thành công (`absent`), không phải lỗi truy vấn; B1 vẫn phải đặt label tường minh trên `.omp/runs` thật.

## Evidence sửa review SEC02B0-R1a — owner, 2026-10-26

- Phạm vi: chỉ năm file ownership B0; evidence-only `.omp/sec02-b0-*`. Không sửa review history, không commit/push/merge/DONE, không đóng G3, không triển khai B1. Giữ nguyên các sửa R1–R3 và 321 baseline.
- **R1a (P2) — raw handle:** verifier bắt buộc nested `protected_handle.reported` có `valid` bool, `path` string, `error` int hoặc tên lỗi; `valid=true` buộc `error=0` + path không rỗng, `valid=false` buộc path rỗng + error có giá trị. `usable` được derive lại bằng `_protected_usable(raw, target)` (chuẩn hóa `\\?\`/slash/case) và so với claim cho **mọi** layout/runtime, không chỉ summary của layout được chọn. Trước sửa, đặt `valid=true, path=<vault canary>, usable=false` tại L1 hoặc `reported={}` vẫn được accept; sau sửa cả hai bị reject và CLI exit 1.
- **R1a (P2) — raw ACE:** `dacl.aces` phải là list record typed (`type` allow/deny, `mask` hex, `sid` S-1-…); container/user ACE claim được derive từ raw SID so với profile/controller SID. Mảng ACE rỗng, xóa container ACE hoặc SID malformed đều reject dù claim `container_ace/user_ace` vẫn true. Producer đo `SE_DACL_PROTECTED` thật qua `GetSecurityDescriptorControl`; verifier yêu cầu `dacl.query=ok` + `protected=true`, nên query failure không thể ghi thành observation hợp lệ.
- **Regression:** thêm 13 test (`EarlySetupFailureTests` + selected/unselected handle report contradiction, missing report fields, invalid error, missing target, selected/unselected empty ACEs, container ACE mismatch, malformed ACE, `dacl.protected`, CLI nonzero cho unselected handle + empty ACE). Trước sửa các test contradiction/missing-field fail (verifier trả `[]`); sau sửa pass. Đồng thời vá `cases` chưa bind khi setup lỗi sớm: trước sửa `run_probe` ném `UnboundLocalError` che cause, sau sửa trả evidence FAIL exit 1 kèm reason.
- **Fail-before/pass-after:** `.omp/sec02-b0-r1a-probes.py` → `.omp/sec02-b0-r1a-probes.json`: 9/9 mutation nested raw/ACE bị reject trên evidence mới, CLI contradiction exit 1, evidence r2 cũ bị reject. Reviewer artifacts `.omp/sec02-b0-r2-review-l1-contradiction.json` và `...-handle-contradiction.json` (trước được accept exit 0) nay verify exit 1.
- **Native:** `run --out .omp/sec02-b0-evidence-r4.json` exit 0, `verdict=PASS`; `verify` exit 0. Evidence sha256 `f64ce3fa62ca8368bec48116493e5bac51e7086aac11e4ad90d3b5c5616c5b6c`; probe sha256 `79047b00b179cb63efb5c9afd968f4f2c6a8c4210c15292df537b2ee59ef2815`; tests sha256 `f95962c1baa52ad4c348461fb980e57ccf29d0bde69e17591434de451dc23b72`. 19 verifier negative controls đều rejected (thêm unselected handle report, handle report missing, DACL aces emptied, container ACE removed, `dacl.protected` false).
- **Focused/full:** focused 71 tests OK trên 3.12.7 và 3.14.8, exit 0, 0 skip; full `tools/check.py` 392 tests (321 baseline + 71 mới) OK trên cả hai, exit 0, 0 skip. `git diff --check` exit 0; protected diff vs `441fe67e` exit 0.
- NOT RUN/NOT PROVEN giữ nguyên như mục R1–R3; không claim raw artifact chống được tác giả giả mạo đồng bộ toàn bộ evidence, chỉ chặn contradiction/thiếu field trong artifact được kiểm.
