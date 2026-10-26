# Threat model cách ly quyền OS trên Windows (SEC-01)

- Ngày: 2026-10-24
- Trạng thái: REVIEW; chưa DONE, chưa đóng G3.
- Base: main `6cae125c947ddeb90c611d921e23786e753d8a73`.
- Phạm vi: threat model cho Worker OMP trên Windows và một proof tối thiểu,
  độc lập với production, về cách ly quyền OS bằng AppContainer/lowbox.
- Tài liệu liên quan: [ADR-0006](../decisions/0006-windows-isolation.md),
  [handoff SEC-01](../handoffs/SEC-01.md), `tools/proofs/sec01_windows.py`,
  `tests/test_security_proof.py`.

## 1. Kết luận ngắn

Trên host thử nghiệm, một process Windows thật chạy dưới AppContainer/lowbox
đã bị OS từ chối đọc và ghi sáu thư mục canary (source, runtime, vault, config,
audit, credentials), trong khi cùng process đó đọc/ghi được thư mục được grant
và một process cùng user nhưng không AppContainer đọc/ghi được cả sáu canary.
Đây là bằng chứng cho **candidate boundary** AppContainer trên fixture, không
phải bằng chứng rằng OMP đã cách ly Worker.

Các điểm vẫn **NOT PROVEN**: bảo vệ repo/runtime/vault thật, credential thật,
network isolation tổng quát, inherited-handle isolation, tương thích Worker CLI
thật, cài đặt/duy trì ACL, recovery khi owner chết dưới boundary, và tích hợp
vào `omp_core`. Vì vậy OMP hiện tại vẫn **không phải sandbox** và không được
dùng cho Worker đối kháng.

## 2. Câu hỏi bảo mật

1. Worker chạy task do provider/task JSON điều khiển có đọc được source
   checkout, runtime, vault, config, audit hoặc credentials không?
2. Worker có sửa/xóa được các tài sản đó không?
3. Worker có mang theo secret từ môi trường của orchestrator không?
4. Worker có dùng network để exfiltrate hoặc gọi dịch vụ ngoài policy không?
5. Worker có thừa hưởng handle quyền cao từ orchestrator không?
6. Worker có để lại process/daemon hoặc tài nguyên ngoài fixture sau khi run
   kết thúc hoặc owner chết không?
7. Khi setup/launch/cleanup lỗi, OMP có fail-closed hay âm thầm chạy cùng-user
   không giới hạn?

Câu 1–2 có negative proof trên fixture. Câu 3 có proof một phần (environment
block). Câu 4–7 chưa có proof đầy đủ; chi tiết ở mục 7.

## 3. Tài sản

| ID | Tài sản | Vì sao quan trọng | Boundary mong muốn |
| --- | --- | --- | --- |
| A1 | Source checkout của repo nguồn | Chứa code, lịch sử Git, file ignored; sửa được là phá integrity | Worker chỉ thấy clone của run, không đọc/ghi repo nguồn |
| A2 | Runtime OMP (`.omp/`, checkpoint, lock, log) | Điều khiển phase, recovery, audit | Worker không đọc/ghi; chỉ orchestrator |
| A3 | Vault/secret store | Token provider, khóa ký, credential | Worker không đọc; chỉ trusted runner |
| A4 | Config của OMP/host | Có thể chứa endpoint, policy, đường dẫn tin cậy | Worker không sửa; đọc tối thiểu |
| A5 | Audit/provenance | Bằng chứng nghiệm thu, chống sửa evidence | Worker không ghi/xóa |
| A6 | Credentials/DSN/API key | Lộ là chiếm tài khoản hoặc DB production | Không vào Worker, không vào log/prompt |
| A7 | Environment của orchestrator | Có thể chứa secret ngoài ý muốn | Worker chỉ nhận block tường minh |
| A8 | Network | Exfiltration, gọi provider/DB ngoài policy | Worker chỉ có capability được cấp |
| A9 | Process tree/lifecycle | Daemon sót lại, chạy trùng Worker | Job Object + recovery nhất quán |
| A10 | Quota/tài khoản provider | Tiêu quota, hành động ngoài phạm vi | Chỉ trusted runner giữ credential |

## 4. Actor và principal

| Actor | Quyền hiện tại | Tin cậy? |
| --- | --- | --- |
| Người vận hành | Có thể chạy OMP, đọc state, attestation | Trusted nhưng có thể sai thao tác |
| Orchestrator/trusted runner | Cùng user OS, medium integrity, không admin; tạo clone, job, checkpoint | Trusted cho run |
| Worker | Cùng user OS; hiện chạy không boundary OS | **Không tin** về đọc/ghi/network |
| Provider/LLM output | Nội dung không tin cậy, có thể chứa chỉ dẫn độc hại | Không tin |
| Task JSON/checkpoint | Dữ liệu cần validate, không phải code | Không tin |
| Process cùng user khác | Có thể đọc file của user nếu ACL cho phép | Không tin cho mục tiêu cách ly |
| Administrator/SYSTEM/kernel | Toàn quyền host | Ngoài mô hình |

Giả định: attacker không có admin, không sửa kernel/driver, không kiểm soát
hypervisor hay phần cứng; không tấn công side-channel vật lý. Attacker có thể
điều khiển nội dung task/prompt và chạy code tuỳ ý trong Worker.

## 5. Ranh giới tin cậy

1. **Orchestrator → Worker**: token/identity, quyền OS, handle kế thừa,
   environment, command line, stdin.
2. **Worker → filesystem**: ACL của source/runtime/vault/config/audit/
   credentials; quyền đọc, ghi, xóa, đổi tên, thay đổi ACL.
3. **Worker → network**: loopback, LAN, Internet, DNS; capability của
   AppContainer.
4. **Worker → process tree**: process con/cháu, daemon, breakaway, owner death.
5. **Worker → evidence/audit**: sửa log, checkpoint, canary hash.
6. **Task/provider → orchestrator**: prompt injection, đường dẫn, command,
   JSON schema.

## 6. Giả định và ngoài phạm vi

- MVP hiện giả định Worker hợp tác; SEC-01 chỉ chứng minh một phần boundary
  cho Worker không hợp tác.
- Proof dùng fixture tạm do proof sở hữu. Không đọc credential thật, không
  dùng tài khoản/provider production, không sửa ACL toàn máy.
- AppContainer là candidate, chưa tích hợp vào `omp_core`. Job Object chỉ là
  lifecycle, không phải security boundary.
- Không chứng minh chống administrator/SYSTEM, kernel exploit, malware cùng
  user có quyền debug, hay tấn công chuỗi cung ứng.
- Không chứng minh cách ly giữa hai Worker chạy đồng thời; OMP vẫn tuần tự.
- Không chứng minh bảo vệ DB production, migration, publish, hay provider
  failover.
- Linux CI không thay thế bằng chứng Windows.

## 7. Ma trận thuộc tính bảo mật

Trạng thái `PROVEN` chỉ áp dụng cho fixture và host đã ghi trong evidence.
`NOT PROVEN` không được đọc thành "an toàn".

| ID | Thuộc tính | Bằng chứng | Trạng thái |
| --- | --- | --- | --- |
| P1 | Worker là process Windows thật dưới AppContainer, cùng user SID, low integrity, 0 capability | Token của child: `is_app_container=true`, `user_sid` khớp parent, `app_container_sid` khớp profile, `integrity_level=low`, `capability_count=0`; `whoami /user` + `/groups` trong Worker | PROVEN |
| P2 | Worker đọc và ghi được tài nguyên được grant | Đọc/ghi `allowed` fixture trả exit 0, nội dung khớp | PROVEN |
| P3 | OS từ chối Worker đọc sáu canary nhạy cảm | 6/6 `type` trả exit 1, output `Access is denied.`, canary không xuất hiện | PROVEN |
| P4 | OS từ chối Worker ghi/sửa sáu canary; hash không đổi | 6/6 `echo` trả exit 1, `Access is denied.` trong stderr, SHA-256 trước/sau giống nhau | PROVEN |
| P5 | Denial không phải lỗi fixture toàn cục | Process cùng user, không AppContainer đọc/ghi được cả sáu canary và `allowed` | PROVEN |
| P6 | Environment của Worker đến từ block tường minh; secret parent không lọt | `set` chỉ thấy các key dự kiến; `OMP_SEC01_PARENT_SECRET` không xuất hiện; `OMP_SEC01_ALLOWED` xuất hiện | PROVEN (chỉ environment block) |
| P7 | Worker được gắn Job Object trước resume; `KILL_ON_JOB_CLOSE` dừng child suspended | `assigned_before_resume=true`; readiness marker `SEC01-READY` do Worker ghi trong `allowed`; `ActiveProcesses=0` sau exit; child bị kill trong bound 5 s | PROVEN (lifecycle, không phải security boundary) |
| P7a | Mọi exit sau `CreateProcessW` đều cleanup owned process/handle trong deadline trước khi propagate | Ownership guard bắt đầu **trước** lời gọi native; nhánh lỗi đọc raw `PROCESS_INFORMATION` (được native điền dù chưa có `ok`/`result`), terminate + wait bound 5000 ms + đóng handle; regression thật trên Windows cho interrupt tại native-return boundary, `KeyboardInterrupt`, `SystemExit`, token-query failure, job-attach failure; không retry/fallback | PROVEN (chỉ process do proof tạo) |
| P8 | Network isolation tổng quát | Chỉ thử loopback `127.0.0.1`; curl timeout sau ~3 s, parent thấy 0 request; không thử Internet/LAN/DNS, không cấu hình capability policy | NOT PROVEN |
| P9 | Inherited-handle isolation | Child tạo với `bInheritHandles=FALSE`, nhưng không kiểm tra handle list/leakage chủ động | NOT PROVEN |
| P10 | Bảo vệ source/runtime/vault thật | Chỉ dùng fixture tạm; không chạm repo, `.omp`, vault thật | NOT PROVEN |
| P11 | Bảo vệ credential thật | Chỉ canary giả trong thư mục `credentials` | NOT PROVEN |
| P12 | Worker CLI/Python/provider thật chạy được dưới boundary | Proof chạy `cmd.exe`, `whoami`, `curl.exe`; không chạy OMP Worker hay Python runtime dưới AppContainer | NOT PROVEN |
| P13 | OMP tích hợp boundary | `omp_core` không đổi; runner vẫn cùng-user | NOT PROVEN |
| P14 | OMP cài đặt/duy trì ACL cho tài sản thật | Proof tự đặt DACL cho fixture; không có code production làm việc này | NOT PROVEN |
| P15 | Recovery/cleanup khi owner chết hoặc crash dưới boundary | OwnedSession fail-closed nếu WinAPI trả FALSE; kiểm tra deadline tiến trình trong Job Object; bảo lưu reference khi lỗi; re-raise interrupt không leak secret; run_worker_in_appcontainer fail-closed chặn mọi launch kế tiếp nếu không dừng được Job; chưa test hard-kill owner | NOT PROVEN |
| P16 | Audit/checkpoint không bị Worker sửa | Không đưa `.omp` thật vào fixture; chỉ suy ra từ P3/P4 trên fixture | NOT PROVEN |
| P17 | Fake CPython Worker (3.12 và 3.14) chạy thực sự dưới AppContainer trên Windows; low integrity, 0 capability | Token của CPython child: `is_app_container=true`, `integrity_level=low`, 0 capability, user_sid khớp principal, AppContainer SID khớp profile; CPython code thực thi tính toán và ghi artifact trong `workspace` được grant | PROVEN (trên fixture SEC-02) |
| P18 | Trusted Oracle chạy black-box ở process riêng, không import candidate code, xác minh artifact và bound identity (candidate/argv/timeout/spec) | Oracle đối soát toàn bộ cây tiến trình dừng hẳn trong Job Object (`ActiveProcesses == 0`) trước khi đọc artifact; manifest là nguồn approved duy nhất: validate `task_id`, `run_id`, `nonce`, canonical `tree_id`, `candidate_hash`, `approved_argv_pattern`, `approved_timeout_seconds`, `spec` rồi dựng identity/invocation; manifest 3.12/3.14 phải khớp; đối soát `actual_argv` exact 4-token `-I -P` và executable/script basename, timeout, kết quả và SHA-256 hash của manifest trong vault; độc lập với stdout/claim | PROVEN (trên fixture SEC-02) |
| P19 | Phát hiện và từ chối 7 kịch bản tấn công của adversary (tamper expected, import injection, config tampering, fake PASS stdout, early exit 0, replay, monkeypatching) | 7/7 attack scenarios được thực thi native thực sự dưới AppContainer (marker file, `attack_executed=True`, bắt buộc hoàn thành bình thường `exit_code` strict int `0`, không timeout) và đều bị Oracle chung từ chối; ATK-7 thực sự monkeypatch `unittest.TestCase.assertEqual`; OS từ chối ghi vault/runner/config; replay/nonce mismatch bị phát hiện; monkeypatch/stdout fake không ảnh hưởng Oracle. Verifier từ chối `-c`/token thừa/executable lạ, exit code bool, thiếu approved fields và object null/malformed mà không traceback. Cờ `-I -P` chỉ ngăn workspace hooks, không ngăn hook trong Lib runtime | PROVEN (trên fixture SEC-02) |
| P20 | Tích hợp Trusted verification và CPython Worker vào runner production OMP | Giai đoạn A chỉ xây proof độc lập trên fixture tạm; `omp_core` chưa tích hợp | NOT PROVEN |
| P21 | Thực thi an toàn arbitrary test suite / plugins của bên thứ ba không có black-box verification | Không chứng minh an toàn nếu import code untrusted vào cùng interpreter với test suite | NOT PROVEN |

## 8. Ma trận threat → kiểm soát

| Threat | Kịch bản | Hiện trạng MVP | Kiểm soát cần | Trạng thái |
| --- | --- | --- | --- | --- |
| T1 | Worker đọc source/vault/config | Cùng user, ACL user cho phép | AppContainer + DACL chỉ grant AppContainer SID cho phần được phép | Chứng minh trên fixture; production NOT PROVEN |
| T2 | Worker sửa/xóa source, audit, checkpoint | Cùng user, ghi được | ACL deny + boundary token; audit ngoài quyền Worker | Chứng minh trên fixture; production NOT PROVEN |
| T3 | Worker đọc credential/env | Env kế thừa mặc định; credential trên đĩa | Env block tường minh; vault ngoài boundary | Env block PROVEN; credential NOT PROVEN |
| T4 | Worker exfiltrate qua network | Không hạn chế network | AppContainer capability tối thiểu + policy/firewall | NOT PROVEN |
| T5 | Worker dùng handle kế thừa | `bInheritHandles=FALSE` ở proof; PROC-02 dùng handle list | Handle list + audit; test leakage | NOT PROVEN |
| T6 | Worker tạo daemon/breakaway | Job Object kill-on-close; không breakaway | Giữ nguyên containment + recovery | Lifecycle PROVEN; boundary recovery NOT PROVEN |
| T7 | Cleanup sót profile/process/ACL | Cleanup fixture bình thường | Cleanup bounded, idempotent, kiểm tra orphan | Một phần PROVEN; crash NOT PROVEN |
| T8 | Task/provider injection điều khiển đường dẫn/command | Validate task/state, không tin provider | Giữ validation + boundary OS | Hiện có validation; boundary NOT PROVEN |
| T9 | Process cùng user đọc lén | Cùng user | Không giải quyết trong SEC-01; cần threat model account/host riêng | NOT PROVEN |
| T10 | Operator chạy sai/attestation sai | `recover --confirm-child-stopped` là attestation | Quy trình + audit; không thay bằng chứng OS | Theo PROC-03, không mở rộng |

## 9. Bằng chứng thực nghiệm

Host: Windows 11 IoT Enterprise LTSC, build `10.0.26100`, AMD64, Python
3.12.7. Principal: `POTA-PC\POTA`, SID
`S-1-5-21-2773687659-1529040039-3189417923-1001`, integrity medium
(`S-1-16-8192`), không elevated.

Thiết lập fixture:

- Mỗi lần chạy tạo AppContainer profile tên `OMP.SEC01.Proof.<random>`.
- Fixture nằm dưới thư mục `AC` của profile.
- Root fixture: user full control; AppContainer SID read/execute.
- `allowed`: user và AppContainer SID full control (kế thừa).
- Sáu thư mục `source`, `runtime`, `vault`, `config`, `audit`, `credentials`:
  chỉ user SID full control; không grant AppContainer SID.
- Mỗi thư mục chứa một canary giả ngẫu nhiên; hash SHA-256 được ghi trước/sau.

Kết quả một lần chạy PASS (`.omp/sec01-evidence-final.json`):

- Worker token: AppContainer, cùng user SID, profile SID riêng, low integrity,
  0 capability, chỉ `SeChangeNotifyPrivilege` và
  `SeIncreaseWorkingSetPrivilege`.
- Positive: `allowed` read/write exit 0, nội dung khớp.
- Readiness: Worker ghi `SEC01-READY` vào `worker-ready.txt` trong `allowed`
  sau khi đã gắn job; đây là marker hoàn tất có deadline 15 s mỗi command.
- Negative: 6/6 đọc và 6/6 ghi bị `Access is denied.`; 6/6 hash không đổi.
- Same-user control: process không AppContainer đọc/ghi được cả sáu canary.
- Environment: 16 key tường minh; secret parent không lọt.
- Network: loopback curl exit 28, timeout ~3010 ms, `parent_requests_seen=0`;
  giữ `NOT PROVEN`.
- Lifecycle: Job Object tạo, main Worker gắn trước resume, 0 active process sau
  exit; đóng job kill child suspended trong bound.
- Cleanup: fixture xóa, `DeleteAppContainerProfile` HRESULT 0, không lỗi.

Bốn lần chạy lặp lại (`final`, `ready1`, `ready2`, `ready3`) đều `PASS` và
verifier trả `{"ok": true, "failures": []}`. Evidence nằm trong `.omp/`
(không commit).

### 9.1 Đối soát finding review R1/R2

Review SEC-01 yêu cầu sửa các finding; bản này đã xử lý và thêm regression:

- **R1 (cleanup mọi exit sau create).** `_create_process` và `_finish_process`
  bắt `BaseException` (gồm `KeyboardInterrupt`/`SystemExit`), terminate owned
  child, wait có deadline 5000 ms và đóng cả hai handle trước khi re-raise
  exception gốc; lỗi cleanup chỉ được gắn qua `add_note`, không thay lỗi gốc,
  không retry và không fallback cùng-user. Regression dùng `CreateProcessW`
  thật trên Windows và chỉ mock điểm lỗi được inject: interrupt sau create,
  `SystemExit`, token-query failure và job-attach failure. Test kiểm child đã
  exit, cả hai handle đã đóng và không có lần create thứ hai. Trên code trước
  sửa (chỉ bắt `Exception`), hai case interrupt/`SystemExit` fail vì child còn
  sống và handle còn mở; hai case còn lại khóa hành vi cleanup dưới helper
  bounded mới.
- **R1a (ownership guard trước native call).** R1 vẫn bắt đầu guard sau khi
  `CreateProcessW` trả về và dựng `result`, nên interrupt tại chính boundary
  return của native call để lại child suspended ngoài job. Bản này đặt `try`
  bao trùm lời gọi native; nhánh lỗi đọc trực tiếp raw `PROCESS_INFORMATION`
  (native đã điền handle dù `ok`/`result` chưa tồn tại), terminate, wait bound
  5000 ms và đóng cả hai handle trước khi re-raise. Regression mới inject
  `KeyboardInterrupt` ngay sau khi native call thành công nhưng trước khi trả
  về caller; harness có `finally` cleanup độc lập bằng handle `OpenProcess`
  riêng theo PID, nên negative control không bỏ orphan. Bằng chứng:
  `.omp/r1a-fail-before.log` fail trên bản pre-fix với
  `owned child is still alive after the failure path`;
  `.omp/r1a-pass-after.log` pass trên bản sửa; cả hai chỉ một create call,
  child gone và handle đóng.
- **R2 (verifier đối soát raw field với claim).** Verifier từ chối claim khi:
  control token không cùng user SID với principal; hash before/after không
  đúng dạng SHA-256, không bằng nhau hoặc không cùng một canary; allowed/
  readiness/control success có `exit_code != 0`, timeout hoặc nội dung không
  khớp raw output; identity/environment flag mâu thuẫn raw output; cleanup
  `ok=true` nhưng `errors` khác rỗng; `profile_deleted=true` nhưng HRESULT
  khác 0; cleanup-kill `process_terminated=true` nhưng `wait_result` khác 0.
  Ba mâu thuẫn reviewer tái hiện (control `user_sid=S-1-5-18`,
  `source.write.hash_after` khác before, `allowed.write.exit_code=1` với
  `ok=true`) đều có test riêng và bị từ chối.

Verifier không thay thế bằng chứng OS: negative denial vẫn là output thật
`Access is denied.` và hash canary thật; các giới hạn NOT PROVEN ở mục 7–10
giữ nguyên.

## 10. Vì sao không tuyên bố mạnh hơn

- AppContainer token vẫn mang user SID. Trên fixture, ACE chỉ grant user SID
  **không** đủ cho Worker; cần grant AppContainer SID (hoặc capability phù
  hợp). Proof chứng minh hệ quả access check trên fixture, không chứng minh
  mọi ACL thật đã đúng.
- Proof tự đặt DACL. Chưa có code production cài ACL, kiểm tra inheritance,
  xử lý file mới, hay chống Worker đổi ACL.
- Network chỉ thử loopback. Không có negative proof cho Internet/LAN/DNS, và
  không có capability policy production.
- Handle inheritance chỉ dừng ở `bInheritHandles=FALSE`; không kiểm tra
  `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` hay leakage.
- Job Object chỉ là lifecycle. Nó không hạn chế quyền file/network/credential.
- Cleanup mọi exit sau create chỉ áp dụng cho process/handle do proof tạo.
  Không chứng minh được owner bị hard-kill, orphan profile, hay recovery sau
  mất điện; các case đó vẫn NOT PROVEN.
- Chưa chứng minh Worker CLI/Python/provider chạy được dưới AppContainer. Đây
  là prerequisite của SEC-02; nếu không giải quyết được thì candidate bị chặn
  cho production dù fixture PASS.

## 11. Map acceptance SEC-01

| AC | Nội dung | Bằng chứng |
| --- | --- | --- |
| AC1 | Threat-model matrix đầy đủ, property có evidence/NOT PROVEN | Tài liệu này, mục 3–8 |
| AC2 | ADR chọn candidate, nguồn Microsoft, minimum OS, alternatives | ADR-0006 |
| AC3 | Process Windows thật dưới boundary, identity/rights, readiness marker/deadline, không AI thật | P1, P7; proof dùng `cmd.exe`/`whoami`/`curl.exe`; marker `SEC01-READY`; timeout 15 s/command, 8 s network, 5 s cleanup |
| AC4 | Positive + negative controls + same-user control | P2–P5 |
| AC5 | Matrix credential/env/network/handles/lifecycle, nhãn đúng | P6–P9, P11 |
| AC6 | Fail-closed, cleanup hữu hạn, không resource bền | `_create_process` lỗi ném `ProofError` không fallback; ownership guard bắt đầu trước native call, mọi exit sau create cleanup owned process/handle trong deadline (P7a, regression Windows cho interrupt tại native-return, `KeyboardInterrupt`/`SystemExit`, token-query/job-attach); `_cleanup` chỉ xóa fixture sở hữu (test); cleanup evidence xóa fixture + profile |
| AC7 | Không phá G2, không kill PID lạ, không mutate repo | Chỉ fixture tạm; không đổi `omp_core`/test baseline |
| AC8 | Evidence OS/Python/principal/command/exit/before-after/limits | JSON evidence + mục 9 |
| AC9 | `tools/check.py`, verifier, `git diff --check` | Handoff SEC-01 ghi kết quả thật: full 233 tests OK, focused 73 tests OK, owned cleanup 6 tests OK, proof/verify PASS, diff check exit 0 |
| AC10 | Handoff REVIEW + hash/file list/AC matrix/prompt reviewer | `docs/handoffs/SEC-01.md` + chat |

## 12. Điều kiện để chuyển sang SEC-02

SEC-02 chỉ nên bắt đầu khi reviewer chấp nhận candidate và ít nhất các câu hỏi
sau có kế hoạch kiểm chứng:

1. Worker CLI/Python/provider thật chạy được dưới AppContainer với quyền tối
   thiểu (đọc runtime, ghi workspace của run).
2. OMP cài ACL cho source/runtime/vault/config/audit và kiểm tra lại sau khi
   tạo file mới.
3. Network capability policy có negative proof cho Internet/LAN/DNS.
4. Handle inheritance được giới hạn bằng handle list và có test leakage.
5. Cleanup/recovery khi owner chết không để profile, process hoặc ACL bền.
6. Không hạ chuẩn: nếu prerequisite thiếu, ghi BLOCKED thay vì fallback
   cùng-user không giới hạn.
