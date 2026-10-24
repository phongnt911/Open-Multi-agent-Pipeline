# Handoff SEC-01

- Trạng thái: REVIEW
- Owner / revision hoặc diff: AI thực thi SEC-01 (opencode, DeepSeek V4.1
  Flash Free); base `6cae125c947ddeb90c611d921e23786e753d8a73`; nhánh
  `codex/sec-01-windows-isolation-proof`; chưa commit/push/merge.
- File thay đổi:
  - `docs/security/THREAT-MODEL.md` (mới)
  - `docs/decisions/0006-windows-isolation.md` (mới)
  - `docs/handoffs/SEC-01.md` (mới)
  - `tools/proofs/sec01_windows.py` (mới)
  - `tests/test_security_proof.py` (mới)
  - `docs/tasks/SEC-01.md` (đổi trạng thái IN_PROGRESS → REVIEW)
  - `README.md`, `docs/CONTRACTS.md`, `docs/SDD.md` (chỉ bổ sung claim/limit
    liên quan SEC-01, trên working tree vốn đã có sửa đổi từ trước)
- Hành vi thay đổi và lý do: không đổi `omp_core`, CLI, schema, checkpoint,
  runner hay dependency. Chỉ thêm proof độc lập, verifier, tests và tài liệu
  threat model/ADR để chứng minh candidate AppContainer trên fixture. Không
  tích hợp boundary vào production. Bản này sửa finding review SEC-01 R1/R1a/R2:
  ownership guard bắt đầu trước `CreateProcessW`, cleanup owned process/handle
  bằng raw `PROCESS_INFORMATION` trên mọi exit sau native creation (gồm
  `KeyboardInterrupt`/`SystemExit`) và verifier đối soát raw field với claim.
- Tiêu chí đã đạt / chưa đạt:
  - AC1: đạt. Threat model có assets/actors/trust boundaries/assumptions,
    property matrix và threat matrix; mọi claim có evidence hoặc NOT PROVEN.
  - AC2: đạt. ADR-0006 chọn AppContainer, có nguồn Microsoft chính thức,
    minimum OS Windows 8/Server 2012 (API) và runtime đã thử Windows 11
    26100; so sánh alternatives.
  - AC3: đạt. Process Windows thật dưới AppContainer; token/identity/rights
    được kiểm trước resume; readiness marker `SEC01-READY`; deadline 15 s mỗi
    command, 8 s network, 5 s cleanup; không gọi AI thật.
  - AC4: đạt. Positive control đọc/ghi `allowed`; 6/6 negative read và 6/6
    negative write bị OS từ chối với `Access is denied.`; hash canary không
    đổi; same-user non-AppContainer control đọc/ghi được cả sáu canary.
  - AC5: đạt. Matrix credential/env/network/handles/lifecycle; network,
    credential, handles, real assets và production Worker đều NOT PROVEN,
    không dùng nhãn network-isolated/credential-isolated.
  - AC6: đạt theo code path và test: launch lỗi ném `ProofError` không
    fallback; ownership guard bắt đầu trước native call; mọi exit sau create
    (interrupt tại native-return, `KeyboardInterrupt`/`SystemExit`,
    token-query/job-attach failure) terminate + wait bound 5000 ms + đóng
    owned handle bằng raw `PROCESS_INFORMATION` trước khi propagate; cleanup
    chỉ xóa fixture sở hữu; evidence xóa fixture và
    `DeleteAppContainerProfile` HRESULT 0.
  - AC7: đạt. Chỉ dùng fixture tạm; không đổi `omp_core`, baseline tests,
    workflow, `tools/check.py`, dependency; không kill PID lạ.
  - AC8: đạt. Evidence có OS/Python/principal/command/exit/before-after
    hashes/positive-negative controls/limits; Windows-only.
  - AC9: đạt. `python tools/check.py` 233 tests OK (128.786 s); focused
    `tests.test_security_proof` 73 tests OK; `OwnedProcessCleanupTests` 6
    tests OK; proof/verify PASS; `git diff --check` exit 0. Có một lần chạy
    full check trước đó lỗi transient `WinError 32` khi dọn temp của test CLI
    không liên quan; test đó chạy riêng OK và full rerun OK (ghi ở mục dưới).
  - AC10: đạt. Handoff này kèm hash/file list/AC matrix/commands/risks và
    prompt reviewer; không DONE/đóng G3/commit/push/merge/gọi AI thật.
- Lệnh kiểm thử, OS, Python, số test và exit code:
  - Baseline trước thay đổi: `python tools/check.py` → 160 tests, `OK`,
    `124.085s`, exit 0 (log `.omp/sec01-baseline.log`).
  - Owned cleanup sau R1a: `python -m unittest
    tests.test_security_proof.OwnedProcessCleanupTests -v` → 6 tests, `OK`,
    exit 0.
  - Focused sau R1a: `python -m unittest tests.test_security_proof -v` →
    73 tests, `OK`, exit 0.
  - R1a fail-before: probe chạy regression trên bản pre-fix
    (`.omp/pre-fix/sec01_windows_pre.py`) → FAIL
    `owned child is still alive after the failure path`, exit 1
    (`.omp/r1a-fail-before.log`); harness `finally` đã dọn orphan độc lập.
  - R1a pass-after: cùng probe trên `tools/proofs/sec01_windows.py` → `OK`,
    exit 0 (`.omp/r1a-pass-after.log`).
  - Proof sau R1a: `python tools/proofs/sec01_windows.py --output
    .omp/sec01-evidence-final.json` → `PASS`, exit 0
    (log `.omp/proof-final.log`).
  - Verify: `python tools/proofs/sec01_windows.py --verify
    .omp/sec01-evidence-final.json` → `{"ok": true, "failures": []}`,
    exit 0 (log `.omp/verify-final.log`).
  - Lặp lại trước R1a: `.omp/sec01-evidence-ready1.json`, `ready2`,
    `ready3` đều `PASS`/verify OK.
  - Full sau R1a: `python tools/check.py` → 233 tests, `OK`, `128.786s`,
    exit 0 (`.omp/check-final.log`).
  - `git diff --check` → exit 0.
  - OS: Windows 11 IoT Enterprise LTSC build 10.0.26100, AMD64; Python
    3.12.7. Linux CI không thay thế bằng chứng Windows.
- Finding review đã sửa (SEC01-R1/R1a/R2):
  - R1: `_create_process` và `_finish_process` bắt `BaseException`, terminate
    owned child, wait tối đa `OWNED_PROCESS_CLEANUP_TIMEOUT_MS` = 5000 ms và
    đóng thread/process handle trước khi re-raise exception gốc; lỗi cleanup
    gắn bằng `add_note`, không thay lỗi gốc, không retry/fallback. Regression
    dùng `CreateProcessW` thật, chỉ mock điểm inject: `KeyboardInterrupt`,
    `SystemExit`, token-query failure, job-attach failure; mỗi test kiểm child
    đã exit, handle đã đóng, create chỉ chạy một lần. Trên code trước sửa
    (chỉ bắt `Exception`), hai test interrupt/`SystemExit` fail vì child còn
    sống và handle còn mở; hai test token-query/job-attach khóa hành vi
    cleanup đã có dưới helper bounded mới.
  - R1a: ownership guard nay bắt đầu **trước** `CreateProcessW`; nhánh lỗi
    đọc raw `PROCESS_INFORMATION` (native đã điền handle dù `ok`/`result`
    chưa tồn tại), terminate/wait bound/đóng handle rồi mới re-raise. Thêm
    regression inject `KeyboardInterrupt` ngay sau native creation thành công,
    trước khi trả về caller; harness có `finally` cleanup độc lập bằng
    `OpenProcess` riêng theo PID nên negative control không bỏ orphan.
    Bằng chứng fail-before/pass-after ở `.omp/r1a-fail-before.log` và
    `.omp/r1a-pass-after.log`; cả hai chỉ một create call, sau sửa child gone
    và handle đóng.
  - R2: verifier đối soát raw field với claim — control cùng user SID, hash
    before/after đúng SHA-256 và nhất quán/binding, allowed/readiness/control
    success phải exit 0, không timeout, content/output khớp raw, identity/env
    flag khớp raw output, cleanup `ok`/`errors`/HRESULT và cleanup-kill
    `wait_result` nhất quán. Ba mâu thuẫn reviewer tái hiện đều có test từ
    chối riêng.
  - Giữ nguyên proof OS denial thật, same-user control và mọi giới hạn
    NOT PROVEN; không đổi `omp_core`, baseline tests, review history.
- Kiểm thử chưa chạy và nguyên nhân:
  - Không chạy Worker OMP/Python/provider thật dưới AppContainer: runtime
    Python trong profile user chưa được grant; đây là prerequisite SEC-02,
    ghi NOT PROVEN.
  - Không test network Internet/LAN/DNS: chỉ loopback bounded; không sửa
    firewall/capability policy.
  - Không test inherited-handle leakage chủ động; chỉ dùng
    `bInheritHandles=FALSE`.
  - Không test owner hard-kill/recovery dưới AppContainer; mới chứng minh
    cleanup mọi exit sau create trong chính process proof, chưa chứng minh
    owner bị kill hay mất điện.
  - Không chạy trên Windows 8/10; minimum API khác runtime đã thử.
- Rủi ro / dữ liệu hoặc process còn tồn tại:
  - Không còn process/profile/fixture của proof; cleanup evidence
    `fixture_removed=true`, `profile_deleted=true`, HRESULT 0.
  - Evidence nằm trong `.omp/` (không commit, có thể bị dọn); reviewer cần
    chạy lại proof.
  - Working tree còn nhiều file docs/review/workspace sửa đổi từ trước,
    không thuộc SEC-01; không reset/clean/stash và không nhận toàn bộ diff.
  - Rủi ro chính: AppContainer chưa tích hợp; tài sản thật/credential/network/
    handle/Worker CLI đều NOT PROVEN; không được coi OMP là sandbox.
- Docs/ADR cập nhật: `docs/security/THREAT-MODEL.md`,
  `docs/decisions/0006-windows-isolation.md`, `README.md`,
  `docs/CONTRACTS.md`, `docs/SDD.md`, `docs/tasks/SEC-01.md`.
- Trạng thái nghiệm thu và tích hợp:
  - `docs/reviews/SEC-01-r3.md` kết luận **ACCEPTED** cho threat model và
    standalone fixture proof; 13/13 hash trong handoff khớp tại thời điểm
    review. Revision accepted là working tree chưa commit trên base
    `6cae125c947ddeb90c611d921e23786e753d8a73`; không sửa code/tests sau
    acceptance.
  - Manifest tích hợp: `docs/handoffs/SEC-01-INTEGRATION.md`, kèm snapshot
    `.omp/sec01-integration-preserve.json`. Manifest tách 9 file tích hợp,
    13 file evidence-only (review/proof logs) và 44 file docs/workspace/
    history có sẵn ngoài scope; README/CONTRACTS/SDD chỉ nhận đúng hunk
    SEC-01.
  - Chưa có exact commit SHA và **chưa có CI remote** trên revision accepted.
    Không commit/push/merge trong lượt này; CI phải chạy sau khi được phép
    tạo commit và phải ghi head SHA thật.
- Bước tiếp theo / owner đề xuất:
  1. Điều phối review manifest tích hợp, xác nhận 9 file và hunk SEC-01,
     không nhận 44 file ngoài scope.
  2. Khi có quyền publish: tạo commit từ manifest, push nhánh/PR, chạy CI
     Windows/Linux trên exact commit SHA và đối chiếu lại hash; không dùng
     local Windows thay CI Linux.
  3. Reviewer độc lập security gate đọc threat model/ADR/proof, chạy lại
     proof + verifier + `python tools/check.py` trên bản bàn giao.
  4. Chấp nhận candidate hoặc yêu cầu sửa finding; không DONE/đóng G3.
  5. Nếu chấp nhận, điều phối mở SEC-02 với prerequisite: Worker CLI/Python
     thật dưới AppContainer, ACL cho tài sản thật, network capability policy,
     handle list và recovery/cleanup khi owner chết.
- Prompt bàn giao reviewer (chat):

```text
Bạn là reviewer độc lập security gate cho SEC-01 của OMP. Hãy review bản
bàn giao trên nhánh `codex/sec-01-windows-isolation-proof`, base
`6cae125c947ddeb90c611d921e23786e753d8a73`, trạng thái REVIEW (chưa DONE,
G3 chưa đóng).

Đọc trước: AGENTS.md, docs/COORDINATION.md, docs/tasks/SEC-01.md,
docs/security/THREAT-MODEL.md, docs/decisions/0006-windows-isolation.md,
docs/handoffs/SEC-01.md, tools/proofs/sec01_windows.py,
tests/test_security_proof.py. Đối chiếu README/CONTRACTS/SDD phần claim/limit.

Việc cần làm:
1. Kiểm tra đủ AC1–AC10; mỗi AC ghi evidence file/test/lệnh hoặc thiếu.
2. Kiểm tra riêng các finding đã sửa:
   - SEC01-R1/R1a: ownership guard phải bắt đầu trước CreateProcessW; mọi exit
     sau native process creation (interrupt tại native-return, KeyboardInterrupt/
     SystemExit, token-query/job-attach failure) phải terminate/wait/đóng owned
     process+handle bằng raw PROCESS_INFORMATION trong deadline trước khi
     propagate; không retry, không fallback. Chạy
     `python -m unittest tests.test_security_proof.OwnedProcessCleanupTests -v`
     và kiểm regression interrupt tại native-return có harness finally cleanup
     độc lập; đối chiếu `.omp/r1a-fail-before.log` / `.omp/r1a-pass-after.log`.
   - SEC01-R2: verifier phải đối soát raw field với claim (control cùng user
     SID, hash before/after dạng SHA-256 và nhất quán, allowed/control/
     readiness success exit 0 + không timeout + content/output khớp); thử lại
     ba mâu thuẫn reviewer đã tái hiện và kiểm test từ chối.
3. Chạy lại:
   python tools/proofs/sec01_windows.py --output .omp/sec01-review.json
   python tools/proofs/sec01_windows.py --verify .omp/sec01-review.json
   python -m unittest tests.test_security_proof -v
   python tools/check.py
   git diff --check
4. Kiểm tra negative controls có thật sự do OS từ chối (không chỉ verifier
   phát hiện sau), same-user control, token/identity, env block, cleanup,
   fail-closed và không có fallback cùng-user.
5. Kiểm tra không overclaim: network/credential/handles/real assets/Worker
   CLI/OMP integration phải còn NOT PROVEN nếu chưa có proof.
6. Kiểm tra scope: không sửa omp_core, baseline tests, workflow, dependency,
   policy toàn máy; không commit/push/merge.

Kết luận bằng ACCEPTED / CHANGES_REQUIRED / BLOCKED. Mỗi finding có ID, mức
độ P0–P3, file:dòng, trigger, hậu quả, đề nghị và cách xác minh. Không đóng
G3 và không ghi DONE thay điều phối.
```

## Hash SHA-256 (tại thời điểm bàn giao)

```text
2fa94734612eb1830142ccbd724915bd08f8d351e9eb6bb8547d3325f3504adf  tools/proofs/sec01_windows.py
23592126b6b199a98cae0a142dc232d333ba8753d182b6abaf88ed9fc6332aa9  tests/test_security_proof.py
f73b94f7ee2a38d3a55767d6a18380f5bfd5d7fddcd9979311934afa201953db  docs/security/THREAT-MODEL.md
418d5a6189d29837745e66d22d638b60c92130392e06fc06d0b45a4ca493151c  docs/decisions/0006-windows-isolation.md
9350ee0c0110beca8c822a002ed536809b8c71348c212ed72b80edde85f82e93  docs/tasks/SEC-01.md
19d65348f9c2e78c6df140c1ffa327f3a9090cd354488d803c94cf350dd4d431  README.md
218f3fc73e44ee46f6855988a9b80e7f5e79d4909e80b650272ab7634e886bf9  docs/CONTRACTS.md
83e8c049fb31c382ad99577a626f42c26d8d4c3cbc587929578a01dcae800721  docs/SDD.md
087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2  omp_core/main.py (không đổi)
fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d  omp_core/windows_job.py (không đổi)
dcc2e4fb800eff39bb1b61f82f7f0d8ccca36e30621e955c760dc6157151fd83  tools/check.py (không đổi)
37bac15ce7f15a5da05dd3d505161546355b70df06f55634cdc502e6bffc8c31  .omp/sec01-evidence-final.json
eafb6e113c841ed87356f9218ba97aaa23c933220e67bad46ec6d545d55492cc  .omp/check-final.log
```

Không ghi DONE chỉ vì hết thời gian hoặc tests một phần đạt.
