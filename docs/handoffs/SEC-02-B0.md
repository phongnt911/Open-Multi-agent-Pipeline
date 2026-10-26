# Handoff SEC-02-B0 — Thiết kế tích hợp + probe native

- Trạng thái: **REVIEW**. B0 đã có thiết kế B1 và native evidence; R1–R3 và R1a đã sửa, chờ review lại. Không phải activation production, không DONE, G3 vẫn mở.
- Base commit: `441fe67e381b5ea0967c99ea32ed4498e81d945f`.
- Nhánh: `codex/sec-02-b0-integration-design` (tạo từ đúng base).
- Owner: AI thực thi nhận prompt từ điều phối; một owner tuần tự, không subagent.
- Không commit/push/merge/stage; 75 dirty/untracked entry (5 file ownership B0 + 70 path ngoài scope giữ nguyên); evidence-only trong `.omp/sec02-b0-*`.

## 1. File ownership và SHA-256

| Đường dẫn | Trạng thái | SHA-256 |
| --- | --- | --- |
| `tools/proofs/sec02_integration_windows.py` | Tạo mới (probe/verifier/CLI) | `79047b00b179cb63efb5c9afd968f4f2c6a8c4210c15292df537b2ee59ef2815` |
| `tests/test_integration_design_proof.py` | Tạo mới (71 tests portable) | `f95962c1baa52ad4c348461fb980e57ccf29d0bde69e17591434de451dc23b72` |
| `docs/decisions/0009-sec02-integration-boundary.md` | Cập nhật evidence + proposal B1 | `eacdacc70dc397e64b9ae380ea7076408826c5e979bef3e89a1f002d8d583725` |
| `docs/tasks/SEC-02-B0.md` | Cập nhật REVIEW + evidence | `949680b308c9d4910e6e9e1d6ccec727e8c5522cdf59b6202dfb8c811d3df933` |
| `docs/handoffs/SEC-02-B0.md` | Tạo mới (file này) | *(self-reference, hash báo trong chat)* |

Evidence native sau sửa R1a: `.omp/sec02-b0-evidence-r4.json`, sha256 `f64ce3fa62ca8368bec48116493e5bac51e7086aac11e4ad90d3b5c5616c5b6c`. Hai evidence cũ `.omp/sec02-b0-evidence-r2.json` (`e70d232e…3912`) và `.omp/sec02-b0-evidence.json` (`0cc2b031…13b87`) được giữ để đối chiếu nhưng **không còn verify** dưới verifier chặt hơn (thiếu nested raw handle report/target, chưa derive ACE từ raw record); cả hai `verify` exit 1, đây là hành vi đúng. Không có runtime copy/log/token trong Git.

## 2. Native evidence

- Host: Windows 11 `10.0.26100`, controller CPython `3.12.7`, medium integrity, không elevated.
- Worker: CPython `3.12.7` và `3.14.8`, native, chạy dưới AppContainer profile `S-1-15-2-…`, low integrity `S-1-16-4096`, `capability_count=0`.
- Ba attribute `SECURITY_CAPABILITIES + JOB_LIST + HANDLE_LIST` trong một `CreateProcessW`, `attach_mode=at_create`, `fallback_used=false`, `job_handle_inheritable=false`; child suspended, đã ở Job trước checkpoint, readiness chỉ sau save+resume (cả hai runtime).
- Layout: L1 (profile workspace) và L2 (`runs/RUN-B0/checkout`) đều PASS; **chọn L2** vì khớp `.omp/runs/<run_id>/checkout`. Mỗi layout có **2 raw ACE** (user + container SID) và verifier derive lại `container_ace/user_ace` từ chính record đó; `dacl.protected=true` được đo bằng `GetSecurityDescriptorControl` (không ghi cứng). L1 `label_query=ok`, `label_sid=S-1-16-4096`; L2 `label_query=absent`, `label_sid=null` (truy vấn thành công, không có explicit label — không phải lỗi truy vấn). Vault/state bị OS từ chối đọc/ghi; `layout.canaries.{vault,state}.before == after`; same-user control đọc/ghi được.
- Handle: allowlist `stdin/stdout/stderr`; protected handle ngoài allowlist không dùng được (`reported.valid=false`, `path=""`, `error=OSError`); verifier derive lại `usable=false` từ `(valid, path, target)` cho **cả bốn case L1/L2 × 3.12/3.14**, không chỉ layout được chọn; fake secret vắng mặt; chỉ `SeChangeNotifyPrivilege` enabled.
- Fault: 5 site `attribute/create/save/resume/query` reached, replacement blocked, `active_after_cleanup=0`, không lỗi cleanup, `elapsed_ms<=15`; descendant ready, root exit 0, 1 process active sau root exit, stop bounded về 0, `descendant.cleanup_errors=[]`.
- Cleanup: profile deleted, fixture removed, job closed, `handles_closed` **suy ra** từ CloseHandle của job/protected/stdio từng case, `errors=[]`, deadline 5s. Không còn tài nguyên.
- Verifier: **19** mutation đều bị reject (missing runtime, not-run property, exit bool, raw/claim mismatch, cleanup error, verdict không PASS, denial/token/protected-handle contradiction, missing per_runtime, case/descendant cleanup error, canary mismatch, summary mismatch, label query failed, unselected raw handle report, missing handle report, DACL aces rỗng, container ACE bị xóa, `dacl.protected` false). Mỗi case phải có typed token/checkpoint/IO/denial/handle/cleanup; aggregate/summary đối soát lại từ raw case.

## 3. AC mapping B0-1…B0-10

| AC | Kết quả | Bằng chứng |
| --- | --- | --- |
| B0-1 | PASS (docs) | ADR “Proposal B1 → API/file” map `execute`, `advance`, `transition`, `complete`, `no_change`, `recover`, `abort`, `_start_process`, `windows_job` |
| B0-2 | PASS | `attributes.*`, `checkpoint.*` của cả hai runtime trong evidence |
| B0-3 | PASS | `runtimes.*.token/env/io`, sys.version/isolated/safe_path |
| B0-4 | PASS | `layout.L1/L2.per_runtime` typed đủ 2 runtime, raw DACL 2 ACE + claim derive + `dacl.protected` đo, label status, denial, same-user control, canary before/after; chọn L2 + caveat label |
| B0-5 | PASS | `handles.*` aggregate trên cả bốn case, `usable` derive từ raw report từng case, protected handle unusable, stdio/handle cleanup, không thêm privilege |
| B0-6 | PASS | `faults.sites.*`, `faults.descendant` (gồm `cleanup_errors=[]`), `cleanup.*` suy ra |
| B0-7 | PASS | `GuardRemovalMutantTests` + 19 `verifier_negative_controls` + 13 regression R1a + sentinel/hash; raw handle/ACE/missing-field contradictions bị reject, CLI exit 1 |
| B0-8 | PASS (docs) | ADR compatibility matrix, dependency table, activation boundary; không triển khai schema |
| B0-9 | PASS | 321 baseline giữ nguyên; 71 focused + 392 full trên 3.12/3.14; `git diff --check` |
| B0-10 | PASS | Handoff này + prompt reviewer; G3 mở, không commit, không giao B1 |

## 4. Fail-before / pass-after

1. **Contract tests:** `python -m unittest tests.test_integration_design_proof -v` fail `ImportError: cannot import name 'sec02_integration_windows'` trước implementation; sau đó 71 tests OK trên cả 3.12/3.14.
2. **Native probe, bốn lỗi thật phát hiện khi chạy và đã sửa:**
   - Protected handle không inherit làm child raise `OSError 0xc0000008` thay vì trả FALSE → trước: B0-3/B0-5 FAIL; sau: PASS (`valid=false`).
   - Fault site `save` không bao giờ chạm vì fault matrix không truyền checkpoint path → trước: B0-6 FAIL (“save was not reached”); sau: PASS.
   - Descendant dùng `-c` inline bị mất quoting (`SyntaxError`) → trước: readiness absent, `active_after_root=0`; sau: dùng script file, readiness + 1 active process.
   - Verdict tạm `FAIL` làm self-check luôn fail → trước: run không thể PASS dù property đủ; sau: set PASS trước self-check rồi downgrade khi lỗi.
3. **Guard-removal mutant (B0-7):** source thật reject evidence với `protected_handle_usable=true`; bản copy bỏ guard `if handles.get("protected_handle_usable") is not False:` chấp nhận chính evidence đó. Sentinel ngoài fixture và hash source thật không đổi sau mutant.
4. **SEC02B0-R1:** trước sửa, `verify_evidence` trả `[]` cho evidence đã bàn giao sau khi đổi `layout.L2.per_runtime["3.14"].vault_read_denied=false`, token `is_app_container=false`, protected handle `usable=true`, xóa cả hai `per_runtime`, hoặc thêm cleanup error vào case/descendant. Sau sửa, mọi mutation trên (và canary mismatch, summary mismatch, label query failed) đều trả failures; CLI trả exit 1. Regression: `.omp/sec02-b0-r1-probes.py` → `.omp/sec02-b0-r1-probes.json` (9/9 rejected) + các test `test_layout_case_*`, `test_missing_layout_cases_rejected`, `test_canary_mismatch_rejected`, `test_summary_case_mismatch_rejected`, `test_descendant_cleanup_error_rejected`.
5. **SEC02B0-R2:** trước sửa, mock `DuplicateHandle` FALSE sau hai `_create_file` để cả hai native handle mở; `FreeSid` non-NULL vẫn `errors=[]`; `cleanup.handles_closed` ghi cứng `true`. Sau sửa, `_open_stdio_handles`/`_open_protected_handle` đóng partial handle, `_combined_attribute_list` Delete list khi Update lỗi, `FreeSid` non-NULL ghi lỗi, `handles_closed` suy ra từ CloseHandle; regression `OwnershipGuardTests` kiểm handle còn hợp lệ trước và không còn sau cleanup.
6. **SEC02B0-R3:** trước sửa, fault-inject `GetFileSecurityW(LABEL)` FALSE WinError 5 cho `label_sid=null`, `error=""`. Sau sửa, `_security_observation` trả `label_query="failed"` + error, verifier reject; `null` chỉ hợp lệ với `label_query="absent"`; regression `LabelObservationTests` + `test_label_query_failure_rejected`, `test_absent_label_must_be_null`, `test_ok_label_must_carry_sid`.
7. **SEC02B0-R1a (P2), nested raw handle/ACE:** trước sửa, evidence L1 với `protected_handle.reported={valid:true, path:<vault canary>, error:0}` và claim `usable=false` vẫn được `verify_evidence` accept (trả `[]`), CLI exit 0; `reported={}` cũng được accept; `dacl.aces=[]` trong khi `container_ace/user_ace=true` cũng được accept. Sau sửa:
   - `reported` phải typed đủ ba field; `valid=true` buộc `error=0` + `path` khác rỗng, `valid=false` buộc `path=""` + error; thiếu field hoặc sai kiểu bị reject.
   - `usable` được derive bằng `_protected_usable(raw, target)` (chuẩn hóa `\\?\`, slash, case) và so với claim trên **cả bốn case**; sửa `reported` của L1 (layout không được chọn) cũng bị reject, không chỉ L2.
   - `dacl.aces` được validate từng record (`type`, `mask`, `sid`); `container_ace`/`user_ace` được derive lại từ SID so với profile/controller SID; rỗng/xóa ACE/malformed/SID sai đều reject.
   - Producer `_dacl_observation` đo `SE_DACL_PROTECTED` thật qua `GetSecurityDescriptorControl`; query failure giữ `query="failed"` + error, không tạo observation hợp lệ.
   - Regression: `test_unselected_layout_handle_report_contradiction_rejected`, `test_selected_layout_handle_report_contradiction_rejected`, `test_missing_handle_report_fields_rejected`, `test_invalid_handle_report_must_record_error`, `test_handle_target_missing_rejected`, `test_selected_layout_empty_aces_rejected`, `test_unselected_layout_empty_aces_rejected`, `test_container_ace_must_match_profile_sid`, `test_malformed_ace_rejected`, `test_dacl_protected_must_be_observed_true`, `test_verify_command_rejects_unselected_raw_handle_contradiction`, `test_verify_command_rejects_empty_ace_claim`.
   - Artifact tái hiện: `.omp/sec02-b0-r1a-probes.py` → `.omp/sec02-b0-r1a-probes.json`: 9/9 mutation bị reject, CLI contradiction exit 1, evidence r2 cũ bị reject; reviewer artifacts `.omp/sec02-b0-r2-review-l1-contradiction.json` và `.omp/sec02-b0-r2-review-handle-contradiction.json` (trước exit 0) nay `verify` exit 1.
   - Phụ: `run_probe` bind `cases` trước setup; trước sửa setup lỗi sớm ném `UnboundLocalError` che cause, sau sửa trả evidence FAIL exit 1 kèm reason (regression `EarlySetupFailureTests`).

## 5. Lệnh và kết quả

| Lệnh | Kết quả |
| --- | --- |
| `python -m tools.proofs.sec02_integration_windows run --out .omp/sec02-b0-evidence-r4.json` | `verdict=PASS`, exit 0 |
| `python -m tools.proofs.sec02_integration_windows verify .omp/sec02-b0-evidence-r4.json` | exit 0, “all SEC-02 B0 properties are proven” |
| `... verify .omp/sec02-b0-evidence-r2.json` | exit 1 (thiếu nested raw report/target/ACE derivation — giữ để đối chiếu) |
| `... verify .omp/sec02-b0-evidence.json` | exit 1 (evidence gốc thiếu typed case/canary/label status) |
| `... verify .omp/sec02-b0-r2-review-l1-contradiction.json` | exit 1 |
| `... verify .omp/sec02-b0-r2-review-handle-contradiction.json` | exit 1 |
| `python .omp/sec02-b0-r1a-probes.py` | 9/9 mutation bị reject, CLI contradiction exit 1, evidence r2 rejected, exit 0 |
| `python .omp/sec02-b0-r1-probes.py` | 9/9 mutation R1/R3 bị reject, exit 0 |
| `python -m unittest tests.test_integration_design_proof -q` (3.12.7) | 71 tests OK, exit 0, 0 skip |
| `... 3.14.8 -m unittest tests.test_integration_design_proof -q` | 71 tests OK, exit 0, 0 skip |
| `python tools/check.py` (3.12.7) | 392 tests OK = 321 baseline + 71 mới, 127.101s, exit 0, 0 skip |
| `... 3.14.8 tools/check.py` | 392 tests OK = 321 baseline + 71 mới, 130.262s, exit 0, 0 skip |
| `git diff --check` | exit 0 |
| `git diff --exit-code 441fe67e -- omp_core tests tools/check.py tools/proofs/sec01_windows.py tools/proofs/sec02_windows.py pyproject.toml .github AGENTS.md` | exit 0; tracked baseline không đổi |

## 6. NOT RUN / NOT PROVEN / giới hạn

- Network isolation; owner hard-kill và power-loss recovery; provider/credential thật; arbitrary pytest/plugin trong lowbox; activation production; implementation B1.
- L2 trong fixture **không có mandatory label riêng**, nhưng đây là kết quả đo thành công (`label_query=absent`, `label_sid=null`), không phải lỗi truy vấn. B1 phải đặt/kiểm tra label `S-1-16-4096` tường minh trên checkout thật và chạy lại probe trên parent `.omp/runs` thật.
- Unit test cho successful no-label dùng mock ba bước Win32 (size/read/SACL) để không phụ thuộc label kế thừa của host; bằng chứng native cho L2 absent vẫn do controller 3.12 đo thật. Runtime 3.14 copy nằm dưới `.omp` thừa hưởng low label nên `%TEMP%` medium không ghi được; đây là thuộc tính host, không phải claim proof. Vì vậy focused/full 3.14 được chạy với `TEMP/TMP` trỏ vào `.omp/test-tmp`.
- Evidence cũ `0cc2b031…13b87` và `e70d232e…3912` được giữ nhưng không còn verify dưới verifier chặt hơn; evidence hiện hành là `.omp/sec02-b0-evidence-r4.json` (`f64ce3fa…5b6c`).
- Vault/state denial chứng minh bằng DACL user-only + low integrity; không chống process cùng user ngoài containment (vai trò trusted controller).
- Verifier chỉ chặn contradiction/thiếu field trong artifact được kiểm; không chứng minh chống tác giả giả mạo đồng bộ toàn bộ evidence. Không claim production sandbox/activation.
- Không sửa production/`omp_core`, không đổi 321 baseline behavior, không cài dependency/runtime, không đổi Git config.
- Không còn profile/fixture/job/handle sau run; evidence nằm trong `.omp/sec02-b0-*`.

## 7. Prompt reviewer (đăng trên chat)

> Review SEC-02-B0 trên nhánh `codex/sec-02-b0-integration-design` (base `441fe67e`). Đọc `docs/tasks/SEC-02-B0.md`, `docs/decisions/0009-sec02-integration-boundary.md`, `docs/handoffs/SEC-02-B0.md`, `tools/proofs/sec02_integration_windows.py`, `tests/test_integration_design_proof.py`.
>
> Kiểm độc lập: (1) evidence `.omp/sec02-b0-evidence-r4.json` — hash, token/SID, attributes, checkpoint ordering, typed `layout.*.per_runtime` đủ 2 runtime, aggregate/canary cross-check, L1/L2 access/denial/label, raw DACL `protected` + ACE derivation, raw protected-handle report → `usable` cho cả bốn case; (2) `run`/`verify` exit 0 chỉ khi property đủ, không fallback PASS; (3) guard-removal mutant thật sự làm verifier mất khả năng reject; (4) xác nhận R1–R3 và **R1a** đã đóng: raw handle `valid/path/error` typed + `usable` derive, ACE claim derive từ raw record/SID, empty/malformed ACE reject, mọi layout/runtime kể cả layout không được chọn, CLI nonzero; (5) partial allocation/FreeSid/handles_closed và label-query fail-closed vẫn giữ; (6) compatibility/dependency/activation proposal không vượt scope, không auto-migrate; (7) ownership đúng năm file, 321 baseline và dirty tree ngoài scope không đổi, không commit/push/DONE/B1.
>
> Tái hiện nhanh: `.omp/sec02-b0-r1a-probes.py` (9/9 rejected, CLI exit 1) và hai artifact contradiction của reviewer cũ (nay verify exit 1). Trả lời REVIEW (accept/block) kèm finding có file/field cụ thể; không tự chuyển DONE hoặc giao B1.
