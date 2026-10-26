# ADR-0009: Điểm nối trusted verification với runner — SEC-02 B0

- Ngày: 2026-10-26. Trạng thái: **Proposed, đã có evidence B0 native, đã sửa R1–R3, chờ review lại**.
- Base: main `441fe67e381b5ea0967c99ea32ed4498e81d945f`.
- Quyết định điều phối: giao B0 thiết kế/probe; chưa phê duyệt implementation production.
- SEC-02 A đã ACCEPTED, merge PR #6; [CI main 40270938241](https://github.com/phongnt911/Open-Multi-agent-Pipeline/actions/runs/40270938241) đạt 321 tests và package smoke trên Windows Python 3.12/3.14. SEC-02 toàn task và G3 chưa DONE.

## Vấn đề và mục tiêu

Proof A chạy fake Python Worker trong fixture AppContainer. Runner thật vẫn chạy worker/test cùng user trong checkout của run. Không chuyển nhãn proof thành cam kết production bằng cách copy helper hoặc đổi một lời gọi spawn.

B0 phải đưa ra thiết kế B1 có thể giao implementation, dựa trên probe native của **điểm nối**. B0 không sửa production/config/checkpoint/CLI hiện tại và không gọi provider thật.

## Các hướng đã xét

| Hướng | Đánh đổi | Quyết định |
| --- | --- | --- |
| Ghép ngay helper proof A vào `advance` | Nhanh nhưng trộn layout, lifecycle, schema và trusted-input policy chưa kiểm chứng | Không chọn |
| B0 probe điểm nối, sau review mới giao B1 từng thay đổi production nhỏ | Có thêm checkpoint review, đổi lại có evidence cho layout và native process attributes | **Chọn bởi điều phối** theo quyền giao việc đã được chủ dự án giao |
| Container/VM/service hoặc plugin runner tổng quát | Thêm deployment/dependency/principal mới ngoài proof và roadmap hiện tại | Ngoài scope |

Đây là lựa chọn kỹ thuật của điều phối, không ghi thành quyết định kỹ thuật do chủ dự án trực tiếp chọn.

## Sự thật hiện tại cần giữ

- `main.py:_start_process` gọi `WindowsJobProcess.start` nhưng chưa truyền env allowlist; `windows_job.py:_environment_block(None)` dùng environment của orchestrator.
- `main.py:advance` lấy checkout `.omp/runs/<run_id>/checkout`; phase verifying chạy `test_argv` ngay trong checkout rồi tính candidate tree trước/sau.
- `WindowsJobProcess.start` tạo process suspended, Job Object gắn tại create, caller save child_pid trước resume. Attribute list hiện gồm JOB_LIST và HANDLE_LIST; chưa có SECURITY_CAPABILITIES.
- Config/task v1, checkpoint v2 và recovery/abort/provenance đã được nghiệm thu. Không thêm field hoặc thay layout mà bỏ version/compatibility tests.
- A không chứng minh arbitrary pytest/plugins hoặc Worker/provider CLI thật chạy an toàn. Không đưa credentials/provider access vào AppContainer để làm demo chạy được.

## Thiết kế được đề xuất để B0 kiểm chứng

### 1. Giữ mô hình điều khiển

Controller/trusted Oracle ở process trusted; Worker không nhận quyền ghi vault/checkpoint/audit. Một run chỉ có một Worker tại một thời điểm. Worker chưa được chạy trước khi lowbox + Job containment có hiệu lực và simulated child_pid checkpoint đã save thành công.

Probe local tạo attribute list chứa SECURITY_CAPABILITIES, JOB_LIST và HANDLE_LIST tại cùng CreateProcessW. Không create-then-attach hoặc fallback Popen/same-user nếu setup lỗi. Job handle không inherit; chỉ stdin/log handles của fixture được truyền có chủ đích.

Không import `tools.proofs` vào production. B0 có thể đọc/reuse helper standalone cho probe; proposal B1 phải chỉ rõ phần native tối thiểu cần chuyển vào runtime, có tests và không mang fake API fallback/global monkeypatch từ proof sang runtime.

### 2. Quyết định layout bằng evidence

So hai layout trong fixture sở hữu riêng:

- L1: workspace trong profile AppContainer, control đã khả thi ở A.
- L2: workspace mô phỏng `.omp/runs/<run_id>/checkout` ngoài profile; trusted vault/state/log bên cạnh nhưng ngoài quyền Worker. Thử DACL tối thiểu và ghi nhận integrity label/quyền thực tế; không suy DACL đơn lẻ là đủ.

Nếu cần thay mandatory label, chỉ được thử trên owned workspace/runtime fixture, ghi before/after và cleanup. Không đổi ACL/label repo thật, parent `.omp`, runtime cài đặt, account, registry policy hay toàn máy. Không thêm privilege để vượt failure.

Nếu L2 không khả thi, ghi BLOCKED property và phân tích hệ quả L1 đối với checkout identity/state/recovery. Không chuyển checkout hoặc hạ policy trong production để che probe thất bại.

### 3. Trusted verification contract

Giữ black-box ở bước đầu: approved manifest nằm trong vault ngoài quyền ghi Worker, bind task/run/nonce, Git candidate tree, content hash, invocation, timeout, spec và verifier version. Không coi arbitrary `test_argv` hoặc exit 0 tự nó là trusted Oracle.

Thiết kế B1 phải phân biệt cooperative legacy với chế độ trusted được chọn tường minh; secure request lỗi không được chạy lại theo cooperative fallback. Chưa chốt tên CLI/config field hoặc tăng version trước khi có compatibility matrix.

Quy tắc reader artifact cần được ghi rõ: root sở hữu, kích thước hữu hạn, malformed/replay refusal và cách tránh đọc nhầm reparse/redirect. B0 chỉ ghi hợp đồng; SEC-03 vẫn sở hữu enforcement đường dẫn/Git toàn cục và xuất patch.

### 4. Schema và recovery

B0 phải đề xuất chính xác version/field nào cần thay và matrix đọc/ghi/resume cho config v1, task v1, state v1/v2, state mới; không auto-migrate bytes cũ. Nếu cần config v2/state v3, đó vẫn chỉ là proposal để review, chưa reserve/triển khai âm thầm.

Proposal B1 phải giữ preflight validation trước lock, revalidation trong lock và status read-only; malformed approved input không được tạo state/log/clone hoặc spawn Worker. Không lấy việc thêm secure mode làm lý do bỏ các invariant DAT-01/REC/PROC đã nghiệm thu.

Phân biệt durable identity với live job/profile handles: PID-only không chứng minh ownership, profile SID không thay live Job handle. Crash/abort không tự xóa evidence; unknown process/profile state phải chặn replacement/complete. Không thay attestation thành OS proof.

### 5. Ranh giới B1 và điều kiện bật production

CONTRACTS hiện yêu cầu integration, negative proof tài sản/network và security review trước khi claim AppContainer production. B0 phải lập dependency table đối với SEC-03 và các property NOT PROVEN, không tự sửa điều kiện này.

Có thể đề xuất B1 xây primitives/integration offline có fake Worker và B2 activation sau dependency review; không gọi B1 là secure production khi điều kiện còn thiếu. Mỗi gói sau phải có ownership và acceptance riêng, được điều phối giao sau B0 review.

## Kết quả probe B0 (native, 2026-10-26)

Chạy trên Windows 11 10.0.26100, controller CPython 3.12.7 medium integrity, không elevated; Worker 3.12.7 và 3.14.8. Lệnh:

```powershell
python -m tools.proofs.sec02_integration_windows run --out .omp/sec02-b0-evidence-r4.json   # exit 0, verdict=PASS
python -m tools.proofs.sec02_integration_windows verify .omp/sec02-b0-evidence-r4.json      # exit 0
```

Evidence `.omp/sec02-b0-evidence-r4.json` sha256 `f64ce3fa62ca8368bec48116493e5bac51e7086aac11e4ad90d3b5c5616c5b6c`; probe sha256 `79047b00b179cb63efb5c9afd968f4f2c6a8c4210c15292df537b2ee59ef2815`. Property B0-2…B0-7 đều `PASS`; `not_run` rỗng; 19 verifier negative control đều bị reject. Verifier hiện đối soát nested raw handle report (target + `reported.valid/path/error` → `usable`) và derive ACE claim từ raw ACE record + SID principal; evidence `.omp/sec02-b0-evidence-r2.json` (`e70d232e…3912`) và `.omp/sec02-b0-evidence.json` (`0cc2b031…13b87`) vì vậy bị reject dưới verifier này (thiếu raw field/typed case) và chỉ còn giữ để đối chiếu.

| Hypothesis | Kết quả | Raw evidence chính |
| --- | --- | --- |
| Ba attribute trong một `CreateProcessW`, child suspended, Job trước checkpoint | **PASS** | `attributes.names=[SECURITY_CAPABILITIES,JOB_LIST,HANDLE_LIST]`, `attach_mode=at_create`, `job_handle_inheritable=false`, `fallback_used=false`, `job_member_before_resume=true`; cả hai runtime `checkpoint.saved_before_resume=true`, `ready_absent_before_resume=true`, `ready_after_resume=true` |
| Hai Worker lowbox, isolated, env allowlist | **PASS** | `3.12.7`/`3.14.8`, `isolated=1`, `safe_path=true`, `is_app_container=true`, `integrity_sid=S-1-16-4096`, `capability_count=0`, AppContainer SID khớp profile; fake secret key/value vắng mặt, allowed value hiện diện; read/write fixture OK |
| L1 profile workspace | **PASS** | workspace đọc/ghi được; raw DACL 2 ACE khớp user + container SID, `dacl.protected=true` đo từ control flag; `label_query=ok`, `label_sid=S-1-16-4096`; vault/state read/write bị từ chối; same-user control đọc/ghi được; canary before/after hash khớp |
| L2 run/checkout workspace | **PASS** | workspace `runs/RUN-B0/checkout/ws_*` đọc/ghi được; raw DACL 2 ACE khớp user + container SID, `dacl.protected=true` đo từ control flag; `label_query=absent`, `label_sid=null` (truy vấn thành công, không có explicit label); vault/state bị từ chối; canary before/after hash khớp |
| Handle/env leak | **PASS** | allowlist `stdin/stdout/stderr`; protected handle kế thừa ở parent nhưng Worker nhận handle không hợp lệ (`reported.valid=false`, `path=""`, `error=OSError`); verifier derive lại `usable=false` từ raw report cho từng layout/runtime; job handle không inherit; aggregate lấy trên cả bốn case L1/L2 × 3.12/3.14; chỉ `SeChangeNotifyPrivilege` enabled |
| Fault và descendant cleanup | **PASS** | 5 site `attribute/create/save/resume/query` đều `reached=true`, `replacement_blocked=true`, `active_after_cleanup=0`, `cleanup_errors=[]`, `elapsed_ms<=15`; descendant `ready=true`, root exit 0, `active_after_root=1`, stop bounded (`stop_ms=0`, `active_after_stop=0`, `cleanup_errors=[]`) |
| Cleanup tổng | **PASS** | `profile_deleted/fixture_removed/job_closed/handles_closed=true`; `handles_closed` suy ra từ CloseHandle của job/protected/stdio từng case, không ghi cứng; `errors=[]`, `stop_deadline_seconds=5.0` |

Sửa review SEC02B0-R1–R3: (R1) verifier bắt buộc typed case cho cả hai runtime trong từng layout, validate token/checkpoint/IO/denial/handle/cleanup, đối soát aggregate/summary và lưu raw canary before/after SHA-256; reject cleanup error của case/descendant. (R2) ownership guard bắt đầu từ allocation đầu tiên, cleanup partial stdio/protected/attribute list, `FreeSid` non-NULL là failure, `handles_closed` suy ra từ kết quả CloseHandle. (R3) label observation tách status `ok`/`absent`/`failed`, fail-closed khi truy vấn lỗi; `null` chỉ dùng cho successful absent. Native L2 vì vậy là `absent` đã đo, không phải lỗi bị che.

Sửa SEC02B0-R1a: verifier bắt buộc nested `protected_handle.reported` (`valid` bool, `path` string, `error` int/tên lỗi, `error=0` khi valid) và derive `usable` từ `(valid, path, target)` bằng cùng hàm producer, áp cho cả bốn case L1/L2 × 3.12/3.14 — không chỉ layout được chọn. `dacl.aces` được validate từng record và container/user ACE claim được derive lại từ raw SID so với profile/controller SID; mảng rỗng, ACE malformed hoặc claim trái đều reject. Producer đo `SE_DACL_PROTECTED` thật bằng `GetSecurityDescriptorControl` (không ghi cứng từ thao tác setup) và query failure giữ `query=failed` + error. Verifier cũng không còn nhận report rỗng/thiếu field; fail-before/pass-after nằm trong `.omp/sec02-b0-r1a-probes.json` và regression `tests/test_integration_design_proof.py`.

Kết luận layout: **chọn L2** (`layout.selected=L2`) vì khớp `.omp/runs/<run_id>/checkout` hiện có và đã chứng minh truy cập/denial native. L1 vẫn PASS nhưng làm workspace nằm trong profile, lệch checkout identity/recovery hiện tại. Lưu ý bắt buộc cho B1: L2 trong fixture **không có mandatory label riêng** (`label_query=absent`, `label_sid=null`) mà vẫn ghi được; B1 phải đặt/kiểm tra label `S-1-16-4096` tường minh trên checkout thật và chạy lại probe trên parent `.omp/runs` thật, không suy từ fixture. `absent` ở đây là truy vấn label thành công không có explicit label; lỗi truy vấn được ghi `failed` và làm property FAIL.

## Proposal B1 (chưa triển khai)

### API/file cần đổi (B0-1)

| Vị trí hiện tại | Thay đổi B1 | Lý do |
| --- | --- | --- |
| `omp_core/windows_job.py:_attribute_list(api, job, handles)` | thêm `container_sid`; dựng 3 attribute trong một list, giữ backing objects tới khi `CreateProcessW` trả về | B0-2 |
| `WindowsJobProcess.start(argv, cwd, log_path, *, stdin_text, temp_dir, env, cleanup_timeout)` | thêm `container_sid: int \| None`; `env` là allowlist bắt buộc khi secure; setup lỗi phải raise, không fallback `Popen`/same-user | B0-2/B0-3 |
| `omp_core/windows_job.py` (cùng file) | thêm `create_app_container_profile(name) -> (sid_ptr, sid_str, folder)` và `delete_app_container_profile(name)`; không tạo module rỗng mới | Profile ownership |
| `omp_core/main.py:_start_process(argv, checkout, log_path, prompt, run_dir)` | thêm `env`/`container_sid` và truyền xuống `WindowsJobProcess.start`; từ chối secure nếu thiếu profile/SID | B0-3 |
| `omp_core/main.py:execute` | tạo profile thuộc run trước spawn, dựng env allowlist (`SystemRoot`, `PATH` tối thiểu, `TEMP` trong run, `OMP_*` cho phép), lưu containment + `child_pid` vào checkpoint trước `resume` | B0-2 |
| `omp_core/main.py:advance` (phase verifying) | chạy `test_argv` trong cùng containment; Oracle vẫn ở process trusted và chỉ đọc artifact sau khi `tree_stopped` | B0-1/B0-4 |
| `omp_core/main.py:compute_candidate_tree`, `check_provenance` | chỉ tính/kiểm sau khi cây tiến trình dừng; secure mode từ chối nếu containment chưa đóng | B0-1 |
| `omp_core/main.py:validate_verification_evidence`, `validate_commit_intent` | evidence v2 thêm `containment`; commit-intent chỉ hợp lệ khi profile đã xóa và Job đã đóng | B0-1/B0-8 |
| `omp_core/main.py:stop`, `_tree_stopped` | giữ bounded cleanup 10s hiện có; sau khi job closed mới xóa profile | B0-6 |
| `omp_core/main.py:transition`, `complete`, `no_change` | chỉ ghi complete/no_change khi profile đã xóa và job closed; lỗi cleanup giữ nguyên cause | B0-1 |
| `omp_core/main.py:recover`, `abort` | chỉ xóa profile có tên trong state; profile lạ/không rõ → chặn replacement/complete, không auto-delete | B0-8 |

Không import `tools.proofs` vào runtime; phần native tối thiểu được viết lại trong `windows_job.py` kèm test, không chép fake fallback/monkeypatch.

### Compatibility matrix (proposal, chưa reserve)

| Input | Đọc | Resume | Ghi |
| --- | --- | --- | --- |
| config v1 (không có `containment`) | chấp nhận, hiểu là cooperative legacy | chỉ khi state không secure | giữ v1, không tự nâng |
| config v2 (`containment.mode="appcontainer"`, `capabilities=[]`) | chấp nhận | cần state v3 + profile thuộc run | v2 |
| task v1 | không đổi | không đổi | v1 |
| state v1 | chỉ đọc/recovery thủ công | không | không bao giờ ghi đè |
| state v2 | đọc; resume chỉ cho run legacy | không cho secure resume | không auto-migrate |
| state v3 (`containment` + `worker_env_policy="allowlist-v1"`) | chấp nhận | có, sau revalidation | v3 |
| verification evidence v1 | chỉ cho run legacy | — | — |
| verification evidence v2 (thêm `containment` observation) | bắt buộc ở secure mode | — | — |

Malformed/unsupported version vẫn fail-closed trước lock/spawn; không sửa bytes state cũ. Probe schema `omp.sec02.b0.integration-proof.v1` tách khỏi schema production.

### Lifecycle, cleanup và recovery ordering

1. Preflight: validate config/task, khóa run, tạo run dir; chưa tạo profile.
2. Tạo AppContainer profile + Job; nếu bất kỳ bước nào lỗi, xóa phần đã tạo theo thứ tự ngược và không spawn.
3. `CreateProcessW` suspended với 3 attribute + handle allowlist; đọc token/Job membership; lưu `child_pid` + `containment` vào checkpoint (state v3) trước `resume`.
4. Resume; chạy Worker/test; Oracle trusted đọc artifact sau khi toàn bộ cây tiến trình dừng.
5. Stop/reap bounded (10s production), đóng Job, xóa profile, rồi mới ghi complete/no_change.
6. Crash/abort: state v3 là nguồn duy nhất cho tên profile; profile không có trong state bị coi là unknown và chặn thao tác, không xóa mù.

### Dependency table

| Phụ thuộc | Trạng thái | Chặn gì |
| --- | --- | --- |
| CONTRACTS: integration + negative asset/network proof + security review | chưa đủ | claim AppContainer production/activation |
| SEC-03: enforcement đường dẫn/Git toàn cục | chưa | secure mode trên repo thật |
| Arbitrary pytest/plugin trong lowbox | NOT PROVEN | claim test authority/sandbox |
| Owner hard-kill, power-loss recovery | NOT PROVEN | claim crash recovery |
| Network isolation | NOT PROVEN | claim production containment |
| B0 review độc lập | chờ | giao B1 |

### Activation boundary

B1 chỉ giao primitive/integration offline với fake Worker và test tương ứng; không đổi default production, không gọi provider thật, không mở SEC-03/G4. B2 (activation) chỉ được giao sau khi dependency table trên được đóng bởi reviewer/security; mỗi gói có ownership và acceptance riêng.

## Giới hạn

B0 không mở SEC-03, không sửa production, không claim owner hard-kill/power-loss/network/real credentials đã PROVEN. Controller native 3.12; Worker cả 3.12/3.14 bắt buộc. CI compatibility không thay native principal/layout evidence. L2 fixture không có mandatory label riêng (`label_query=absent`); B1 phải đặt label tường minh và đo lại trên `.omp/runs` thật. Vault/state denial được chứng minh bằng DACL user-only + low integrity; không chứng minh chống một process cùng user nằm ngoài containment (đó là vai trò trusted controller). Evidence gốc `0cc2b031…13b87` và evidence r2 (`e70d232e…3912`) được giữ làm lịch sử nhưng không còn verify dưới verifier chặt hơn; evidence hiện hành là `.omp/sec02-b0-evidence-r4.json` (`f64ce3fa…5b6c`).

## Nguồn và cách dùng

- [Microsoft UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute): mô tả security capabilities, job-list và handle-list; tài liệu API không thay probe kết hợp trên host.
- [Microsoft AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation): quyền tài nguyên theo boundary.
- [Microsoft Mandatory Integrity Control](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control): integrity label là một phần riêng của access model.
- Repo: ADR-0004/0006/0007/0008, CONTRACTS, task SEC-02 A và review r5/merge. Khả thi của L1/L2 giờ là **kết quả native đã đo** (mục “Kết quả probe B0”); phần proposal B1 vẫn là đề xuất chờ review, không phải implementation đã có.
