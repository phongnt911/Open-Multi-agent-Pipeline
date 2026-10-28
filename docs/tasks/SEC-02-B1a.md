# SEC-02-B1a: Primitive AppContainer trong Windows Job runner

- Trạng thái: **REVIEW** (2026-10-27). Owner: AI thực thi B1a, một người tuần
  tự, không agent phụ. Reviewer: AI điều phối, độc lập với thực thi.
- Gate: G3 mở. Phụ thuộc B0 ACCEPTED/PR #7 merged, main CI 40336785632 success 2/2.
- Base khởi tạo B1a: `bade93db8a71a5be116bde7792a112205e8fd4a4`; commit B1a hiện
  tại trên nhánh `codex/sec-02-b1a-native-primitives`:
  `dae334830e3a0ae58dcbdcfbf29dc50693e124b5`; baseline **392 tests**.
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

- Base khởi tạo `bade93db8a71a5be116bde7792a112205e8fd4a4`, HEAD hiện tại
  `dae334830e3a0ae58dcbdcfbf29dc50693e124b5` trên nhánh
  `codex/sec-02-b1a-native-primitives`, chưa tạo commit mới. Chỉ sửa
  `tests/test_appcontainer_process.py`, `docs/tasks/SEC-02-B1a.md` và
  `docs/handoffs/SEC-02-B1a.md` trong vòng khắc phục CI (`omp_core/windows_job.py`
  và `docs/CONTRACTS.md` giữ nguyên byte-for-byte).
- Fix review R1–R6 + R3a trước đó: giữ nguyên đầy đủ (allocation/owner-construction
  guard, failed-start cleanup + poison, sticky `close()` giữ đúng type
  `KeyboardInterrupt`/`SystemExit`, usability guards, strict fixture cleanup).
- Fix review CI (`docs/reviews/SEC-02-B1a-CI.md`, `docs/reviews/SEC-02-B1a-CI-r2.md`, `docs/reviews/SEC-02-B1a-CI-r3.md`):
  - **SEC02B1A-CI-R1 & SEC02B1A-CI-R1a / R1a1**: Khi interpreter đang ở `S-1-16-12288`
    (High integrity trên GitHub Actions runner), `AppContainerNativeTests.setUpClass`
    uỷ quyền sang process con Python controller ở Medium integrity (`S-1-16-8192`)
    tạo bằng `DuplicateTokenEx` + `SetTokenInformation(TokenIntegrityLevel, S-1-16-8192)` +
    `CreateProcessAsUserW` (chữ ký chuẩn 11 tham số Win32, tạo `CREATE_SUSPENDED | CREATE_NO_WINDOW`).
    Ownership được bảo vệ liên tục qua mọi boundary:
    * `primary` token được bao bọc trong try/finally đóng bằng `_close_handle_checked`
      ngay khi duplicate xong; nếu `_close_handle_checked(own)` lỗi, `primary` vẫn
      được đóng trước khi re-raise.
    * `_OwnedDesktop.create` và `_spawn_with_token` nằm trong cùng khối try có
      bảo vệ đóng `primary` token (Trigger B: desktop create lỗi thì `primary`
      token vẫn được đóng và không leak).
    * Khi `_spawn_with_token` đã sinh child suspended: nếu bước đóng `primary`
      token ném `KeyboardInterrupt`/`SystemExit` (Trigger A) hoặc `CloseHandle`
      trả `FALSE`/ném `OSError` (Trigger C), `_start_with_medium_token` tự chạy
      ngay `_cleanup_controller_on_exception` để terminate child process, chờ wait
      xong, đóng handle process/thread, đóng `_OwnedDesktop`, và poison
      `_UNPROVEN_CONTROLLER_STATE` để chặn mở replacement controller.
    * Caller `_delegate_native_controller` nhận `out_info=info` từ trước khi spawn,
      có cờ `_cleaned_up` để ngăn chặn double-cleanup và double-close.
    * `_close_handle_checked` kiểm tra giá trị `BOOL` của `CloseHandle`, zero giá
      trị handle và ném `OSError` kèm winerror nếu trả về `FALSE`.
    * Interpreter `S-1-16-4096` (Low) vẫn fail-closed.
  - **SEC02B1A-CI-R1a2**: Xoá bỏ hoàn toàn compatibility retry bắt `TypeError` trong
    `_delegate_native_controller`; gọi trực tiếp `_start_with_medium_token(..., out_info=info)`
    chỉ một lần duy nhất. Cập nhật các fake factories (`fake_start` và `start`) trong
    test harness để nhận `out_info: _PROCESS_INFORMATION | None = None` và cập nhật
    handles/PID theo caller-owned contract. Lỗi nội bộ (`TypeError` hoặc bất kỳ ngoại lệ nào)
    từ factory hoặc bên trong bước khởi tạo sẽ lập tức lan truyền (propagate) ra ngoài
    sau khi đã dọn dẹp tài nguyên qua `_cleanup_controller_on_exception`; factory tuyệt
    đối không bị retry hay che giấu lỗi. Thêm regression test
    `test_delegate_internal_type_error_propagates_without_retry_or_swallowing` (chứng
    minh fail-before trên code cũ với `AssertionError: TypeError not raised` và pass-after)
    cùng Control 8 trong controls script.
  - **SEC02B1A-CI-R1b**: Xoá hoàn toàn `_set_desktop_dacl` và mọi thao tác
    `SetSecurityInfo` lên desktop hiện tại của caller; không sửa DACL của
    `.omp/test-tmp` dùng chung. Khi desktop hiện tại không có sẵn ACE cho `AC`
    (kiểm tra read-only qua `_current_desktop_allows_appcontainer`),
    `_delegate_native_controller` tạo một desktop tạm độc quyền thuộc quyền sở hữu
    của fixture (`_OwnedDesktop.create` qua `CreateDesktopW` với SDDL tối thiểu chỉ
    cấp `SYSTEM`, `Administrators`, user hiện tại và `AC`), gắn child controller và
    các AppContainer Worker vào desktop sở hữu này qua `STARTUPINFOW.lpDesktop`, và
    đóng bằng `CloseDesktop` có kiểm tra kết quả sau khi chứng minh child đã dừng
    (đã **Resolved** trong `SEC-02-B1a-CI-r3.md`).
  - **SEC02B1A-CI-R1c**: `_validate_child_evidence` (và `AppContainerNativeTests._delegated`)
    xác thực đầy đủ schema có kiểu và bất biến bảo mật của cả 6 native tests (gồm
    `test_secure_worker_token_identity_and_owned_isolation` với `ReadFile` kiểm tra
    foreign handle, `test_same_user_control_reads_and_writes_the_same_canary` với
    SHA-256 64-hex hợp lệ và `tree_stopped is True`, `test_interrupted_allocation_releases_real_profile`,
    `test_real_collision_never_deletes_existing_profile`, `test_profile_close_refuses_live_tree_then_allows_sequential_reuse`,
    và `test_root_early_exit_with_live_descendant_is_failure`), từ chối mọi gói bằng
    chứng thiếu trường, rỗng, trùng PID hoặc mâu thuẫn ở cả ranh giới validator và
    `_delegate_native_controller` (đã **Resolved** trong `SEC-02-B1a-CI-r3.md`).
  - **SEC02B1A-CI-R2**: `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises`
    tự cấp phát `TemporaryDirectory(prefix="omp-b1a-cleanup-")` độc quyền, đăng ký
    `self.addCleanup(root.cleanup)` ngay khi tạo, patch `SCRATCH_ROOT` trỏ tới đường
    dẫn con chưa tồn tại để chứng minh test độc lập hoàn toàn với thứ tự chạy và
    `.omp/test-tmp`, đồng thời giữ nguyên mọi assertion gom đủ 4 lỗi và xoá sạch
    `scratch` (đã **Resolved** trong `SEC-02-B1a-CI-r2.md`).
- Focused 3.12.7: **65 tests OK** (3.456s, exit 0). Focused 3.14.8 (bản copy nhãn
  Medium): **65 tests OK** (3.807s, exit 0). `.omp` 3.14 local mang nhãn Low chạy
  58 unit tests OK và fail-closed ở `AppContainerNativeTests.setUpClass` (`S-1-16-4096`,
  0 skip, exit 1) — ghi BLOCKED cho đúng path Low-labeled đó, không giả PASS.
- Full `tools/check.py`: 3.12.7 **457 tests OK** (126.142s, exit 0); 3.14.8 Medium
  copy **457 tests OK** (122.972s, exit 0). Baseline **392 tests** không đổi.
- Controls & phân biệt nguồn gốc bằng chứng (provenance):
  `.omp/sec02-b1a-ci-r1-r2-controls.py` (`CI_R1_R2_CONTROLS_OK`) chứng minh
  fail-before trên commit `dae334830e3a0ae58dcbdcfbf29dc50693e124b5`, pass-after
  cho nhánh uỷ quyền với parent thực tế ở Medium (`parent_actual_integrity: S-1-16-8192`)
  và nhánh `S-1-16-12288` được kích hoạt qua mock chỉ ở bước kiểm tra đầu vào của
  parent (`provenance_mode: injected_high_parent_to_real_medium_child`) để sinh
  child controller Medium thật trên desktop fixture sở hữu cho cả 3.12.7 (child PID
  31228, Worker PID 61180) và 3.14.8 (child PID 60888, Worker PID 61388) mà không sửa
  DACL desktop của caller (`caller_desktop_dacl_unchanged: true`,
  `has_set_desktop_dacl: false`), cùng R2 với `SCRATCH_ROOT` chưa tồn tại và 8
  negative controls trên bản copy source (gồm Control 7 R1a1 và Control 8 R1a2) đều fail đúng kỳ vọng.
  Reviewer typeerror probe `.omp/sec02-b1a-ci-r4-review-typeerror.py --assert-no-retry`
  và reviewer boundary probe `.omp/sec02-b1a-ci-r3-review-boundaries.py --assert-safety`
  đều đạt exit 0. Bằng chứng từ parent thực sự chạy ở High (`S-1-16-12288`) cần tiến
  trình elevated/CI runner High thật, không được tuyên bố từ lần chạy local không nâng quyền.
  `.omp/sec02-b1a-controls.py` (`CONTROLS_OK`), `.omp/sec02-b1a-r3a-control.py`
  (`R3A_CONTROL_OK`), `.omp/sec02-b1a-package-smoke.py` (`PACKAGE_SMOKE_OK`),
  `git diff --check` exit 0.
- Chi tiết lệnh, hash, AC mapping, giới hạn và prompt reviewer:
  `../handoffs/SEC-02-B1a.md`. Mọi log nằm trong `.omp/sec02-b1a-*`.
- Chưa commit/push/merge/DONE, chưa đóng G3; không nhận B1b/B1c.
