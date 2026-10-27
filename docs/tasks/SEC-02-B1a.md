# SEC-02-B1a: Primitive AppContainer trong Windows Job runner

- Trạng thái: **REVIEW** (2026-10-27). Owner: AI thực thi B1a, một người tuần
  tự, không agent phụ. Reviewer: AI điều phối, độc lập với thực thi.
- Gate: G3 mở. Phụ thuộc B0 ACCEPTED/PR #7 merged, main CI 40336785632 success 2/2.
- Base: `bade93db8a71a5be116bde7792a112205e8fd4a4`; baseline **392 tests**.
- Nhánh triển khai đề nghị: `codex/sec-02-b1a-native-primitives` từ đúng base.
  Chưa tạo nhánh trong lượt chuẩn bị. Local hiện ở nhánh B0 với dirty docs; phải
  preserve snapshot và kiểm base/code trước chuyển nhánh, không reset/clean/stash.
- Spec/plan: `../superpowers/specs/2026-10-26-sec02-b1-design.md`,
  `../superpowers/plans/2026-10-26-sec02-b1a.md`.

## Đọc trước và ownership — đúng năm file

AGENTS/COORDINATION/README/ROADMAP/CONTRACTS/SDD, ADR-0004/0008/0009, spec/plan,
B0 review r3/integration r2/MERGE/CLOSURE và luồng windows_job.start/resume/wait/
terminate_tree/close + main._start_process/execute; baseline tests liên quan.

Được sửa/tạo:
1. `omp_core/windows_job.py`: profile ownership + optional native containment API.
2. `tests/test_appcontainer_process.py` mới: focused regression/native fixture,
   có thể chứa subprocess harness cùng file; không test rỗng hoặc import proof.
3. `docs/CONTRACTS.md`: chỉ ghi API mới/giới hạn B1a, không claim activation.
4. `docs/tasks/SEC-02-B1a.md`: owner/trạng thái/evidence, không tự DONE.
5. `docs/handoffs/SEC-02-B1a.md` mới: handoff và prompt reviewer.

Chỉ đọc: main.py, mọi baseline tests (gồm B0/A/SEC-01), tools/proofs, ADR-0009,
workflow/checker/dependency/examples, AGENTS/COORDINATION/ROADMAP/README, specs/
plans/review history. Không sửa tests cũ để đổi expected/skip. Nếu bắt buộc sửa
ngoài scope, ghi blocker và dừng phần phụ thuộc. Evidence-only `.omp/sec02-b1a-*`.

## Interface, compatibility và anti-goals

Implement đúng resource-owner API trong spec: WindowsAppContainerProfile.create,
properties name/sid/folder/closed, close; optional keyword `app_container=None`
trong WindowsJobProcess.start. Profile None giữ legacy behavior. Profile thật
buộc explicit env, validation trước file/log/process side effect, zero capabilities,
ba attributes at-create, child suspended; caller vẫn save trước resume.

Không thêm đường CLI/flag/config, không gọi API mới từ main.py, không thay schema,
Oracle/evidence/commit semantics hoặc env của legacy Worker. Không sửa layout
`.omp/runs` thật, cài dependency/runtime, gọi provider/credentials/network thật,
copy/import tools.proofs vào runtime/native harness. Không fallback Popen/same-user.

## Acceptance B1a-1–B1a-10

- [ ] **B1a-1** Profile owner tạo mới/fail-closed, SID/folder identity typed,
  ownership từ native return; existing-name collision không adopt/delete nhầm.
  FreeSid/HRESULT failure không giả close success; close thành công idempotent.
- [ ] **B1a-2** Optional path có 3 attributes tại CreateProcessW; KILL_ON_JOB_CLOSE,
  non-inheritable Job, explicit stdin/log handle allowlist; SID/backing objects
  sống đến native return. Legacy None vẫn hai attributes và env behavior cũ.
- [ ] **B1a-3** Closed/invalid profile hoặc env None/sai type/key/value/NUL bị từ
  chối trước side effect; không dùng arbitrary SID từ task data. Bound live/
  unknown job chặn start tiếp hoặc profile close; chỉ cho sequential reuse sau stop.
- [ ] **B1a-4** Native Workers CPython 3.12/3.14 chạy production start API thật;
  trước resume đọc token AppContainer/SID/low integrity/0 capabilities/Job member,
  marker chưa có; sau explicit resume marker + allowed IO/stdio có. Fake-secret
  không lọt env, protected handle ngoài allowlist không dùng được.
- [ ] **B1a-5** Fixture độc quyền chứng minh OS deny protected canaries read/write,
  same-user positive control, before/after hashes; owner native fixture không
  claim actual repo/vault/layout đã tích hợp. Native runtime copies/ACL/labels
  chỉ trong owned fixture. Không import proof/global hook để đo production API.
- [ ] **B1a-6** Fault ở profile alloc/folder/SID, attribute size/init/3 updates,
  CreateProcessW (gồm interrupt ngay native-return), token/membership query,
  ResumeThread, terminate/wait/query/release đều có test; cleanup trong deadline,
  không orphan/replacement. Query/cleanup unknown giữ guard và báo failure.
- [ ] **B1a-7** Root early-exit còn descendant/readiness được kiểm thật; stop
  production 10s mặc định, test timeout hữu hạn; unknown stop không close profile
  như thành công. Exception chains giữ stage/WinError/cause đã sanitize, không
  argv/token/env; KeyboardInterrupt/SystemExit giữ type. Harness finally cleanup
  độc lập, không kill PID ngoài fixture; owner hard-kill mới không thuộc claim này.
- [ ] **B1a-8** Negative control gỡ guard trên source copy làm regression fail;
  bản thật pass, source/sentinel ngoài fixture không đổi. Có fail-before/pass-after
  cho behavior mới; malformed/native failure không bị test chỉ mirror mock PASS.
- [ ] **B1a-9** 392 baseline tests/files không đổi; focused/full Windows Python
  3.12/3.14 đạt, counts thực tế/0 skip mới; native evidence ghi controller/Worker
  thật và prerequisite. Thiếu prerequisite ghi BLOCKED, không giả PASS. Package
  build/install/import/entrypoint ngoài source cần kiểm vì sửa runtime module.
- [ ] **B1a-10** Type hints/docstrings, contracts khớp; handoff có exact base/file
  hashes, AC mapping, red-green, OS/versions/count/exit/skips/limits/resources.
  Chỉ bàn giao REVIEW, không commit/push/merge/DONE/đóng G3 hoặc nhận B1b/B1c.

## Checks và điểm dừng

```powershell
python tools/check.py
python -m unittest tests.test_appcontainer_process -v
.omp/python-runtimes/cpython-3.14.8-windows-x86_64-none/python.exe -m unittest tests.test_appcontainer_process -v
.omp/python-runtimes/cpython-3.14.8-windows-x86_64-none/python.exe tools/check.py
git diff --check
```

Controller medium 3.12/native harness và Worker 3.12/3.14 theo spec. Không tự
cài runtime nếu path thiếu; ghi version/path thật. Native evidence phải chỉ ra
gọi production WindowsJobProcess, không chỉ standalone proof PASS. CI exact
commit Windows 3.12/3.14 + package smoke còn cần khi tích hợp, không tự publish.

Code hashes trước sửa: windows_job.py `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d`;
main.py control `087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2`.
Hash lệch phải giải thích trước nhận scope; baseline working tree docs bẩn không
phải lỗi cần dọn. Có thể dùng checkout cô lập nếu cần nhưng phải giữ/local cung
cấp task/spec/plan chưa publish, không tự copy dirty code ngoài ownership.

Task READY chỉ là gói giao đã chuẩn bị; lượt điều phối chưa triển khai hoặc
nghiệm thu các AC trên. Khi nhận việc ghi owner/IN_PROGRESS, cuối cùng REVIEW.

## Kiểm tra gói giao việc — điều phối, 2026-10-26

- Đã tự đối chiếu spec/plan với API hiện tại, ADR-0009 và B0 closure; chọn API
  profile owner thay raw SID như refinement được ghi rõ, không tự đổi schema.
- `python tools/check.py`: Windows 11 build 26100, Python 3.12.7, **392 tests OK**,
  99.673s, exit 0; log `.omp/sec02-b1-preparation-check.log`.
- `git diff --check` exit 0; audit `.omp/sec02-b1-preparation-audit.json` xác nhận
  không sửa code/tests/review history. Các hunk tài liệu có sẵn được giữ nguyên.
- Chưa chạy focused/native B1a hoặc package smoke B1a vì chưa có implementation;
  chưa chạy lại 3.14 trong lượt chỉ lập tài liệu. Main CI B0 trên hai runtime là
  baseline, không được ghi thành B1a PASS. Tài liệu mới chưa commit/push.

## Bằng chứng thực thi — 2026-10-27

- Base/HEAD `bade93db8a71a5be116bde7792a112205e8fd4a4` giữ nguyên; nhánh
  `codex/sec-02-b1a-native-primitives`, chưa commit. Chỉ sửa năm file ownership.
- Fix review R1–R6: allocation/owner-construction guard + native interrupt
  cleanup; failed-start cleanup kiểm terminate/wait/job-empty/close và poison
  profile; unlink trả kết quả, close sticky + giữ binding; profile mất SID/
  release lỗi/failed-start bị chặn trước `_kernel32`; native fixture cleanup
  strict (collect + AssertionError, fallback terminate job); controls gỡ guard
  hành vi (binding + failed-start) thay vì chỉ đổi error text.
- Fix review R3a (r2): `WindowsJobProcess.close()` giữ đúng type
  `KeyboardInterrupt`/`SystemExit` khi `CloseHandle` ném, vẫn chạy nốt cleanup
  best-effort, ghi note đã sanitize cho bước lỗi, giữ sticky failure + profile
  binding, không đóng lần hai handle không rõ trạng thái. Regression
  `test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` (2 interrupt
  types + job CloseHandle false + unlink false), control revert close() fail
  trước / pass sau.
- Focused 3.12.7: **51 tests OK**, exit 0. Focused 3.14.8 (bản copy nhãn Medium):
  **51 tests OK**, exit 0. `.omp` 3.14 local mang nhãn Low nên không tạo được
  AppContainer profile; test fail-closed với thông báo prerequisite (30 unit
  pass, không skip) — ghi BLOCKED cho đúng path đó, không giả PASS.
- Full `tools/check.py`: 3.12.7 **443 tests OK** (98.9s); 3.14.8 Medium copy
  **443 tests OK** (100.6s). Trước đó lần full 3.14 từng có 1 flake baseline
  `test_recovery` (`os.replace` PermissionError) và flake PID reuse `git.exe`;
  pass lại và pass trên 3.12, không phải thay đổi B1a.
- Controls: base `windows_job.py` làm focused fail (ImportError); gỡ guard
  binding và guard failed-start trên source copy đều làm regression fail vì
  chạm `_kernel32` (side effect thật), source thật pass; hash source/sentinel
  không đổi. Native probe 3.12 controller → 3.14.8 Worker: token AppContainer/
  SID/low/0 capability, canary denied, hash không đổi. Package smoke (`uv`
  build/venv 3.12.7/install/import/`omp --help`) OK. `git diff --check` exit 0.
- Chi tiết lệnh, hash, AC mapping, giới hạn và prompt reviewer:
  `../handoffs/SEC-02-B1a.md`. Mọi log nằm trong `.omp/sec02-b1a-*`.
- Chưa commit/push/merge/DONE, chưa đóng G3; không nhận B1b/B1c.
