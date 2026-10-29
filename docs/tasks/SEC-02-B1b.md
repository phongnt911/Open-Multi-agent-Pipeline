# SEC-02-B1b: Layout L2 và trusted inputs offline

- Trạng thái: **REVIEW**, AI thực thi hoàn thành B1b-1–B1b-8 ngày 2026-10-28, bàn giao reviewer độc lập. Một owner tuần tự, không subagent.
- Base: `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`, main sau PR #8. Nhánh đã tạo: `codex/sec-02-b1b-offline-layout`.
- Phụ thuộc: B1a DONE, main CI 40625012964 success Windows 3.12/3.14, **457 tests**. G3 mở; B1c/B2 chưa giao.
- Điều phối chạy lại baseline trước giao: `python tools/check.py` **457 tests OK**, 127.868s, exit 0; `git diff --check` exit 0; diff code/tests/workflow/checker/dependency so với base rỗng. Đây chưa phải evidence implementation B1b hoặc local 3.14.
- Thiết kế/plan: `../superpowers/plans/2026-10-28-sec02-b1b.md`; kế thừa ADR-0008/0009 và spec B1, không triển khai proposal schema.

## Đọc trước, baseline và preservation

Đọc AGENTS, COORDINATION, README, ROADMAP, CONTRIBUTING, CONTRACTS, SDD, THREAT-MODEL, ADR-0008/0009, spec B1, task này, B1a-MERGE; đọc API profile/start/active_processes/tree_stopped/close trong windows_job.py và baseline tests. Không import tools.proofs vào runtime hoặc native harness.

Working tree có docs/history/workspace/temp có sẵn, kể cả task B1a DONE chưa publish. Trước sửa lưu snapshot path/status/hash trong `.omp/sec02-b1b-*`; đối soát base, index, diff, 457 baseline và hash control. Không reset/clean/stash/delete, không thay ACL để đọc vùng ngoài ownership. Không stage tài liệu có sẵn. Nhánh mới đang track origin/main chỉ để xác định base; không push main.

## Ownership — đúng sáu file implementation

1. `omp_core/trusted_layout.py` mới: module có consumer trực tiếp là offline tests, layout/env/manifest/artifact reader; không framework/plugin/registry.
2. `tests/test_trusted_layout.py` mới: unit/fault/native cùng harness trong file này, không sửa tests baseline.
3. `docs/CONTRACTS.md`: chỉ thêm section B1b API, schema manifest riêng và limit; giữ phần accepted khác.
4. `docs/decisions/0010-offline-trusted-layout.md` mới: contract cụ thể, threat/ownership/failure và giới hạn.
5. `docs/tasks/SEC-02-B1b.md`: owner, status, evidence, không tự DONE.
6. `docs/handoffs/SEC-02-B1b.md` mới: AC mapping, hashes, test/OS/runtime/count/exit, negative controls và prompt review.

Điều phối sở hữu ROADMAP/B1 index/plan. AI thực thi chỉ đọc các file này. Không sửa main.py, windows_job.py, baseline tests, tools/proofs, workflow/checker/dependency/README/SDD/review history. Nếu API B1a không đủ, ghi BLOCKED thay vì sửa ngoài scope.

## Contract và acceptance

- **B1b-1 — Offline và compatibility:** API mới chỉ được tests gọi; main/CLI/config/task/state/evidence/commit/recover/abort giữ nguyên. Không auto-migrate hoặc reserve config v2/state v3/evidence v2. 457 baseline giữ assertion, không skip/nới expected.
- **B1b-2 — Owned L2:** tạo disposable Git repo do fixture sở hữu, đo đúng cấu trúc `<repo>/.omp/runs/<run_id>/checkout` với vault/state/log là vùng trusted bên cạnh. Không đổi ACL/label repo OMP thật, parent dùng chung, runtime cài đặt, account/policy. Root phải được tạo mới độc quyền; từ chối existing/unknown root và reparse trước mutation; lỗi setup không launch. Native DACL protected với controller/container SID tối thiểu; explicit checkout low label `S-1-16-4096`, đọc lại DACL/label thật, query failure fail-closed (không coi absent là PASS). ACL cleanup chỉ trên tài nguyên owned.
- **B1b-3 — Env:** dựng mapping allowlist mới từ trusted input tường minh; không copy os.environ/PATH của parent, không nhận env từ task. Chỉ SystemRoot/runtime path đã approve, TEMP/TMP trong owned workspace và key OMP cần thiết đã đặc tả; từ chối key/value/NUL/duplicate case-insensitive và override nguy hiểm. Fake secret và PYTHONPATH/PYTHONHOME không xuất hiện trong child. Worker CPython 3.12 dùng `-I -P` và approved argv thực sự; không chỉ kiểm bool tự khai.
- **B1b-4 — Manifest độc lập:** định nghĩa schema nội bộ riêng `omp.sec02.b1b.manifest.v1`, không phải checkpoint. Bind task_id/run_id/nonce, verifier version, candidate bytes SHA-256, canonical tree identity và algorithm, approved argv/timeout, expected result/spec hash. Typed strict/unknown-field reject, bool không là int, timeout finite dương, SHA-256/OID đúng format. Manifest ở vault không Worker-writable, atomic publication; không overwrite/adopt manifest lạ. Đối soát candidate/invocation tại verify; drift/replay/malformed từ chối, không echo payload/secret.
- **B1b-5 — Artifact reader:** không import/execute candidate; chỉ đọc JSON artifact fixed relative path trong owned checkout, tối đa 64 KiB (đọc limit+1), UTF-8 strict và typed schema bind identity/nonce/manifest. Trước mở bắt buộc query Job bằng B1a API và chứng minh active_processes == 0/tree_stopped; alive/unknown/closed-handle-unknown refuse. Không nhận caller bool thay OS proof. Đọc bằng handle không follow reparse, kiểm final path/file identity thuộc root; từ chối symlink/junction/ADS/device/UNC/traversal, hardlink tới vùng ngoài và file đổi trong khi đọc. Không check-path rồi mở lại theo tên. Không claim đây là enforcement SEC-03 toàn repo. Nếu chưa bảo đảm property, BLOCKED, không fallback Path.read_text.
- **B1b-6 — Native property:** trên Windows Python 3.12 gọi production module + WindowsJobProcess.start(profile thật), độc lập đọc token/SID/low integrity/0 capabilities và DACL/label. Worker đọc/ghi checkout được; OS thực sự deny đọc/ghi canary vault/state, same-user control thành công; hash before/after bất biến. Descendant còn sống phải chặn reader, sau stop mới đọc; không replacement/complete. Báo raw field có sanitize, không chỉ aggregate PASS.
- **B1b-7 — Fault/cleanup:** fault allocation/ACL/label query/manifest write-flush-fsync-replace/artifact open-read/stop/release; ownership từ allocation đầu tiên, stop bounded theo API B1a (10s), cleanup không che lỗi gốc, giữ cause/context an toàn và type KeyboardInterrupt/SystemExit. Unknown cleanup giữ guard; không retry launch/fallback same-user. Không xóa evidence hoặc tài nguyên không owned. Kiểm mọi native BOOL/HRESULT/handle; High CI controller dùng pattern đã review B1a, Low fail-closed, thiếu prerequisite ghi BLOCKED thay skip.
- **B1b-8 — Negative controls và review:** regression fail-before/pass-after trong source copy độc quyền; bỏ tree-stop guard và làm lỏng manifest binding từng mutant phải khiến test phát hiện sai, không chỉ verifier tự tuyên bố. Native denial có same-user control; raw/aggregate contradictions bị reject. Handoff hash đúng sáu file, baseline controls, preservation, command/OS/Python/count/exit/time/skips và checks chưa chạy. Không tự ACCEPTED/DONE hoặc đóng G3.

## Checks bắt buộc trước REVIEW

Chỉ bắt buộc Windows Python 3.12, ghi rõ path interpreter/version/controller integrity và Worker runtime thực tế:

```powershell
python -m unittest tests.test_trusted_layout -v
python tools/check.py
python -m py_compile omp_core/trusted_layout.py tests/test_trusted_layout.py
git diff --check
git diff --exit-code 8e33dad -- omp_core/main.py omp_core/windows_job.py .github tools pyproject.toml AGENTS.md
```

Đối soát hash mọi baseline test theo base (không dùng broad tests diff vì có file mới). Full count = **457 + N** mới; 0 skip, báo đúng N sau discover. Không cài dependency/runtime mới, không AI thật. Packaging và exact-commit CI Windows Python 3.12 được nghiệm thu ở bước integration sau review; chưa chạy phải ghi thiếu, không giả local là CI.

## Giới hạn và quyền

Chỉ offline fake Worker trong disposable repo. Không kích hoạt trên repo người dùng hoặc claim immutable vault/network/credential/arbitrary test authority/full sandbox, owner hard-kill/power-loss recovery. B1c sở hữu nối orchestration/schema; SEC-03 sở hữu scope/Git toàn cục; B2 activation còn bị chặn. Không commit/push/merge, không nhận task tiếp theo. Bàn giao REVIEW để reviewer độc lập nghiệm thu.

Quyết định chủ dự án ngày 2026-10-28: B1b chỉ bắt buộc kiểm thử Windows Python 3.12, gồm controller, Worker, focused/full/native và CI nghiệm thu. Python 3.14 không phải điều kiện chặn B1b. Evidence B1a 3.12/3.14 là lịch sử giữ nguyên; không sửa baseline tests hoặc workflow trong gói B1b để né kiểm tra.

## Kết quả thực thi và Evidence (2026-10-28)

- **Môi trường:** Windows 11 Build 26100 AMD64, CPython 3.12.7, Controller Medium Integrity (`S-1-16-8192`).
- **Khắc phục Review SEC-02-B1b r1 (R1–R9), r2 (R2a–R9a), r3 (R3b, R5b, R5c, R6b, R8c) và r4 (R5d, R6c, R8d):**
  - R1: Query OS thật qua WindowsJobProcess (`tree_stopped is True`, `active_processes == 0` kiểu int strict); từ chối duck typing/fake object (`SimpleNamespace`) và boolean process count.
  - R2 & R2a: Neo root identity bằng directory handle không cờ `FILE_SHARE_DELETE`, giữ handle mở liên tục xuyên suốt quá trình mở và đọc artifact (loại bỏ TOCTOU / directory swap race), xác minh normalized final path trước khi trả kết quả. Chặn junction checkout root, ancestor reparse points, leaf symlinks và ADS.
  - R3, R3a & R3b: Ràng buộc chặt chẽ thuật toán `tree_algorithm` với độ dài OID (`sha1`: 40, `sha256`: 64). Canonical candidate identity phân giải tự động từ `approved_argv[1:]`; nếu caller truyền `candidate_relpath`, bắt buộc khớp với canonical candidate identity (từ chối mọi decoy override R3b). Bắt buộc đầy đủ execution inputs: `actual_argv`, `actual_timeout`, `actual_tree_oid`, `actual_spec_hash` đều không được None (R3b). Đối soát `actual_timeout` bằng so sánh đẳng thức số dương hữu hạn có kiểu, từ chối NaN, bool, số âm, hoặc timeout bị đổi. Schema artifact typed strict (`failures`, `errors` là int `>= 0`, `output` str, status `PASS`/`success` yêu cầu 0 failures và 0 errors).
  - R4 & R4a: Xuất bản nguyên tử trong vault bằng temp file độc quyền (`os.O_CREAT | os.O_EXCL`), flush, fsync và commit bằng `os.rename` (MoveFileExW không `MOVEFILE_REPLACE_EXISTING`). No-clobber guarantee: từ chối ghi đè khi rival xuất hiện tại commit boundary, bảo toàn 100% bytes đích, dọn dẹp file temp; tái xuất bản nội dung giống nhau thành công idempotent.
  - R5, R5a, R5b, R5c & R5d: `OwnedL2Layout.cleanup()` giữ nguyên `_cleaned = False` và raise `TrustedLayoutError` nếu reset DACL hay rmtree gặp lỗi (không báo sạch giả). `_reset_dacl_for_deletion` kiểm tra trực tiếp DWORD return code từ `SetNamedSecurityInfoW` (R5c). Toàn bộ exception chain (`__cause__` và `__context__`) được đệ quy làm sạch qua `_sanitize_exception_chain` để loại bỏ tokens/passwords/secrets/bearer (`Bearer <credential>`) trong `.args`, `filename`, `filename2`, `strerror` không phụ thuộc test markers (R5b). Reader sanitize exception chain trên mọi đường truyền kể cả khi đóng handle thành công (R5d), đồng thời bảo toàn chính xác kiểu ngoại lệ ngắt (`KeyboardInterrupt`/`SystemExit`) với `__context__` an toàn (R5b, R5d).
  - R6, R6a, R6b & R6c: `_is_reparse_point` fail-closed. `_query_dacl` trích xuất đầy đủ records ACE (`type`, `flags`, `mask`, `sid`), đối soát exact mask (`0x1F01FF`), inheritance flags (`_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE = 3`). Ancestor traverse DACL exact policy (R6b, R6c): đối soát đúng 2 ACEs, User mask `_FILE_ALL_ACCESS`, flags 0; Container mask `0x1200A0`, flags 0 (từ chối container có mask `0x1200A9`, Full Control hay inheritance flags trên ancestor). Từ chối mọi ACE lạ hoặc deny ACE.
  - R7 & R7a: `build_trusted_environment`: validate `temp_dir` và `runtime_path` là thư mục tuyệt đối tồn tại thực tế, không chứa dấu chấm phẩy `;` (ngăn PATH separator injection), bản thân và mọi tổ tiên không phải reparse point. Regex key Win32 `^[A-Za-z_][A-Za-z0-9_]*$`, từ chối ký tự `=`, cấm value rỗng.
  - R8, R8a, R8b, R8c & R8d: Delegation harness CI High: khởi tạo kernel32 trước spawn, giải phóng handle qua `_release_controller_handles` trong `finally`, đối soát khắt khe báo cáo con (exact test list, đúng test count, 0 skips kiểu list, 0 errors, 0 failures, child integrity và PID). Domain-specific `raw_evidence` được trích xuất từ các native tests (R8c), runner fail-closed nếu thiếu observations (không fallback PASS giả). Validator kiểm tra per-test schema và typed invariants nghiêm ngặt (R8d): `bool` strict `True`, `capabilities` strict `int 0`, AppContainer SID đúng format, hex hashes 40/64, `artifact_status` `PASS`/`success`, loại bỏ triệt để các mâu thuẫn giữa summary report và raw evidence. Entrypoint native-controller CLI trích xuất danh sách test bất biến trước khi chạy suite.
  - R9 & R9a: Bộ test regression `FindingRegressionTests` gồm 24 tests bao phủ toàn diện R1–R8, R2a–R8b, R3b–R8c, và R5d, R6c, R8d. Native tests: xác thực thuộc tính token độc lập (`appcontainer_sid`, 0 capabilities, `in_any_job == True`), cô lập biến môi trường cha (`fake_secret_leaked == False`), positive same-canary control (đọc, sửa, phục hồi, đối soát hash trước/sau khớp trên chính file canary). Test descendant process: marker do child tự ghi, chứng minh Job ở trạng thái live chặn reader, và sau `terminate_tree()` reader đọc thành công.
- **Focused test suite:** `python -m unittest tests.test_trusted_layout -v`
  - Kết quả: **64 tests OK**, 0 errors, 0 failures, 0 skips, thời gian ~3.4s, exit 0.
- **Full test suite:** `python tools/check.py`
  - Kết quả: **521 tests OK** (457 baseline + 64 B1b), 0 errors, 0 failures, 0 skips, exit 0.
- **Reviewer mutant suite:** `python .omp/sec02-b1b-r2-run-mutants.py`
  - Kết quả: 4/4 mutants caught by assertion, exit 0.
- **Bytecode compilation:** `python -m py_compile omp_core/trusted_layout.py tests/test_trusted_layout.py`
  - Kết quả: exit 0.
- **Diff checks:**
  - `git diff --check`: exit 0.
  - `git diff --exit-code 8e33dad -- omp_core/main.py omp_core/windows_job.py .github tools pyproject.toml AGENTS.md`: exit 0.
  - Baseline tests drift so với `8e33dad`: 0 drift.
- **Bàn giao:** `docs/handoffs/SEC-02-B1b.md` với đầy đủ hashes sáu file, AC mapping và reviewer prompt.

## Điều phối sửa tiếp và kiểm chứng r5 — 2026-10-29

- Owner phần sửa r5: AI điều phối. Chỉ sửa file test B1b và docs ownership; production module giữ SHA-256 `07cb1596b649fd61959f89d31beaf9803a28f96ff2d203c5cb794af9437f254f`.
- Review nguồn và probe phát hiện R8d còn nhận newline cuối SID/hash và identity khác nhưng cùng format. Thêm 9 mutation subcases: cả 9 thất bại trước sửa; sau sửa dùng fullmatch ASCII/hex, token SID quan sát từ OS khớp owned profile, tree/hash khớp manifest identity và algorithm/length. Reference này kiểm mâu thuẫn fixture trusted, không xác thực report đối kháng.
- High controller thật (`S-1-16-12288`) tái hiện 2 errors: regression R8b gọi entrypoint Medium trực tiếp và cleanup terminate process đã exit. Sửa test để delegate tới Medium thật; chỉ truyền `terminate=False` sau wait trả WAIT_OBJECT_0. Timeout/token-query failure regression vẫn yêu cầu terminate. Không sửa helper/test baseline, không skip.
- Windows 11 10.0.26100, Python 3.12.7: focused 64 OK/3.571s; full check cuối 521 OK/101.924s/exit 0/0 skip, gồm cleanup assertions cuối. Bốn mutants original0/mutant1 đều caught. py_compile và diff check exit 0.
- Evidence: `.omp/sec02-b1b-r5-review-probe.py`/`.json`, snapshot, original source copies, `sec02-b1b-r5-delta.diff`, `sec02-b1b-r5-audit.py`/`.json`; tool archives full-before `12ec067291236ac4`, focused-after `e9950d1c1370c460`, full-after `1a4db77707c2c741`.
- Đối soát toàn bộ 13 file test baseline theo git show: 0 drift; protected diff và index rỗng. Giữ thay đổi có sẵn ngoài ownership theo snapshot đầu lượt r5.
- Review r5 ACCEPTED trong phạm vi implementation offline; Antigravity review độc lập delta cuối không có finding tồn đọng (static only), điều phối chạy kiểm chứng động. Task vẫn REVIEW chờ integration: chưa có packaging/exact-commit CI cho patch này, chưa commit/push/merge. B1c/B2 chưa giao; G3 mở. Xem docs/reviews/SEC-02-B1b-r5.md.
