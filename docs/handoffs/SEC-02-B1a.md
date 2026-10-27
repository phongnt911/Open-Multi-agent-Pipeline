# Handoff SEC-02-B1a — AppContainer primitive trong Job runner

- Ngày: 2026-10-27. Trạng thái: **REVIEW** sau khi sửa review R1–R6 và
  SEC02B1A-R3a (không commit/push/merge/DONE, G3 chưa đóng).
- Base/HEAD: `bade93db8a71a5be116bde7792a112205e8fd4a4` (chưa có commit mới).
- Nhánh: `codex/sec-02-b1a-native-primitives`.
- Máy: Windows 11 build 26100; Python 3.12.7; Python 3.14.8 local runtime.
- Task/spec/plan: `docs/tasks/SEC-02-B1a.md`,
  `docs/superpowers/specs/2026-10-26-sec02-b1-design.md`,
  `docs/superpowers/plans/2026-10-26-sec02-b1a.md`.

## File và hash

| File | SHA-256 |
| --- | --- |
| `omp_core/windows_job.py` | `692dd73830405d4d92472e7b6796fdd9b977b00f0247e7e2ec1a4cf598689cde` |
| `tests/test_appcontainer_process.py` (mới) | `97f6b115dfa0f840a66f993dbefee7ba1574cfba6fc46b04f0a4391eaac2f727` |
| `docs/CONTRACTS.md` | `76ae41bc77b7c991f43d24db0dcadabac9dcd15d6a28f7400c321d4ae6fde7dc` |
| `docs/tasks/SEC-02-B1a.md` | `36c48440d6e7728b49acbe29b6db7fcc5f941256d1099d333945032e8e885c0a` |

`windows_job.py` base là `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d`;
`omp_core/main.py` control là
`087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2` và không đổi.
Handoff này là file mới nên hash của chính nó thay đổi sau khi ghi.

## Sửa review R1–R6 và R3a

| Finding | Thay đổi production | Regression |
| --- | --- | --- |
| R1 native-return allocation | `CreateAppContainerProfile` nằm trong guard; interrupt/`SystemExit` sau khi cấp SID đọc out pointer làm bằng chứng sở hữu và FreeSid + delete; owner construction cũng được bảo vệ; collision không đọc/xoá profile lạ; cleanup lỗi ghi note, giữ type gốc. | `test_interrupt_after_allocation_releases_owned_profile`, `test_interrupt_during_owner_construction_releases_owned_profile`, `test_combined_cleanup_failures_preserve_primary_exception`, `test_collision_never_frees_or_deletes_foreign_profile`; native `test_interrupted_allocation_releases_real_profile` + `test_real_collision_never_deletes_existing_profile`. |
| R2 failed-start cleanup | Một đường cleanup: terminate process + job, wait bounded tới job rỗng, kiểm CloseHandle và unlink; set `_failed_start` trước cleanup, chỉ xoá khi mọi bước chứng minh xong; profile bị poison chặn replacement start/close; không fallback/retry. | `test_unproven_failed_start_poisons_profile_and_blocks_reuse`, `test_proven_failed_start_does_not_poison_profile`, `test_interrupt_at_native_return_with_failed_cleanup_poisons_profile`, `test_start_failure_cleanup_does_not_mask_primary_error`. |
| R3 unlink/close sticky | `_unlink_with_retry` trả `bool`; `WindowsJobProcess.close` set `_close_failed=True` trước cleanup có thể raise, gộp lỗi handle + stdin, chỉ gỡ binding profile sau khi mọi bước thành công; `profile.close` chỉ gỡ binding sau FreeSid + delete thành công. | `test_interrupt_during_close_is_sticky_and_keeps_binding`, `test_unlink_and_handle_failures_are_reported_together`; sticky handle cũ vẫn giữ binding. |
| **R3a** close nuốt interrupt | `CloseHandle` bắt riêng `KeyboardInterrupt`/`SystemExit`: giữ instance đầu, chạy nốt các handle còn lại + unlink, zero handle ngay sau khi thử (không double-close), ghi note sanitize (`thread`/`process`/`job`/`stdin`), rồi `raise` đúng exception gốc. Sticky `_close_failed` và profile binding giữ nguyên vì chỉ clear sau khi mọi bước thành công. | `test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` (2 interrupt types + `CloseHandle` false + unlink false): đúng type/instance, `api.closed == [103, 102, 101]` một lần mỗi handle, notes sanitize, `__cause__`/`__context__` None, call hai refuse, binding còn. |
| R4 profile usability | Start từ chối closed, `_sid_release_failed`, `_sid_pointer is None`, `_failed_start` trước `_kernel32`/file/native; close từ chối SID đã mất/failed-start. | `test_released_sid_blocks_start_and_close_before_native`, `test_failed_sid_release_blocks_start_before_native`, `test_delete_profile_failure_blocks_reuse_before_native`. |
| R5 native fixture cleanup | `_cleanup` gom mọi lỗi và raise `AssertionError`; fallback `TerminateJobObject` + poll job rỗng; register cleanup ngay sau khi tạo scratch; bỏ `ignore_cleanup_errors`. | `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises`; native `setUp` vẫn cleanup thật khi test fail. |
| R6 negative control | Control gỡ guard binding và guard failed-start trên source copy; regression fail vì chạm `_kernel32` (side effect), không chỉ đổi error text; source thật pass, hash không đổi. | `.omp/sec02-b1a-controls.py` (`CONTROLS_OK`). |

Native R1 có cleanup độc lập: test đăng ký `_delete_native_profile(name)` ngay khi
chọn tên; helper dùng raw Userenv/Advapi32 (không gọi helper production) để xoá
profile nếu production cleanup sót, rồi probe absence bằng
`CreateAppContainerProfile` có retry bounded.

## AC mapping

| AC | Bằng chứng chính |
| --- | --- |
| B1a-1 | `ProfileOwnershipTests` (11): reject name trước native call, collision không adopt/delete, partial alloc/folder-failure giải phóng đúng, `closed` typed/idempotent, FreeSid lỗi không giả close, close từ chối cây bound, interrupt allocation/owner construction + combined cleanup. Native: allocation interrupt thật giải phóng profile; collision thật không xoá profile. |
| B1a-2 | `AttributeListTests` (4) + `StartFailureTests.test_success_uses_three_attributes_and_returns_suspended_child`: legacy 2 attribute; secure thêm `SECURITY_CAPABILITIES` zero capability; list xoá khi update/size/init lỗi. |
| B1a-3 | `StartValidationTests` (9): env None/sai type/key/value/NUL bị từ chối trước side effect, profile lạ/đã đóng/mất SID/failed-start, bind live/unknown chặn start, argv rỗng/NUL. |
| B1a-4 | `AppContainerNativeTests.test_secure_worker_token_identity_and_owned_isolation`: production `start` thật, đọc token trước resume (`is_container`, SID khớp profile, `S-1-16-4096`, 0 capability, cùng user, Job member), marker chưa có; sau resume có marker/stdin/env/workspace IO, fake secret không lọt, handle ngoài allowlist không dùng được. |
| B1a-5 | Cùng test native: canary user-only DACL bị deny read/write, hash trước/sau giữ nguyên; `test_same_user_control_reads_and_writes_the_same_canary` chứng minh same-user không AppContainer truy cập được; fixture tự copy runtime/ACL/label trong `.omp/test-tmp`. |
| B1a-6 | `StartFailureTests` (10) + `ProcessLifecycleFaultTests` (10): fault profile alloc/folder/SID, attribute size/init/update, CreateProcessW false, interrupt tại native return, cleanup thất bại → poison, token/membership query, ResumeThread, terminate/wait/query/release, cleanup deadline, sticky close, interrupt tại `CloseHandle` giữ type, guard giữ khi unknown. |
| B1a-7 | `test_root_early_exit_with_live_descendant_is_failure` (root exit còn descendant → lỗi, descendant chết, timeout hữu hạn); `test_wait_wraps_cleanup_failure_with_sanitized_cause` (cause giữ WinError, không argv); `test_token_verification_failure_keeps_stage_and_hides_inputs` (không env/argv); `test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` (KeyboardInterrupt/SystemExit tại `CloseHandle` giữ type sau cleanup, notes sanitize); `_cleanup` strict + fallback job stop. |
| B1a-8 | `.omp/sec02-b1a-controls.py`: base code → focused fail (ImportError); gỡ guard binding và guard failed-start → regression fail vì `kernel touched before validation` (side effect thật); source thật pass; hash source/sentinel không đổi; không mock-mirror. R3a control `.omp/sec02-b1a-r3a-control.py`: revert `close()` về `except BaseException` → regression fail với `WindowsJobError: close contained process failed: thread, job, stdin`; source thật pass (`R3A_CONTROL_OK`). |
| B1a-9 | Focused 51 OK và full 443 OK trên 3.12.7 + 3.14.8 Medium copy, 0 skip mới; baseline 392 không đổi; native evidence ghi controller/Worker; package smoke OK; prerequisite Low-label ghi BLOCKED, không skip/fake. |
| B1a-10 | Type hints/docstrings, CONTRACTS cập nhật, handoff có hash/AC/lệnh/giới hạn; chỉ bàn giao REVIEW. |

## Lệnh và kết quả

| Lệnh (rút gọn) | Kết quả | Log |
| --- | --- | --- |
| `python -m unittest tests.test_appcontainer_process -v` | 51 OK, 3.39s, exit 0 | `.omp/sec02-b1a-focused-312.log` |
| `%TEMP%\omp-b1a-py314-medium\python.exe -m unittest tests.test_appcontainer_process -v` | 51 OK, 3.49s, exit 0 | `.omp/sec02-b1a-focused-314-medium.log` |
| `.omp/python-runtimes/.../python.exe -m unittest tests.test_appcontainer_process -v` | 30 unit OK; native class fail-closed prerequisite (Low IL), exit 1 → BLOCKED cho path này | `.omp/sec02-b1a-focused-314-low-blocked.log` |
| `python tools/check.py` | 443 tests OK, 98.9s, exit 0 | `.omp/sec02-b1a-full-312.log` |
| 3.14.8 Medium copy `tools/check.py` | 443 tests OK, 100.6s, exit 0 | `.omp/sec02-b1a-full-314.log` |
| `.omp/sec02-b1a-controls.py` | `CONTROLS_OK`, hash không đổi | `.omp/sec02-b1a-controls-fix.log` |
| `.omp/sec02-b1a-r3a-control.py` | `R3A_CONTROL_OK`: reverted close fail (WindowsJobError thay type), real source pass | `.omp/sec02-b1a-r3a-control.log` |
| `.omp/sec02-b1a-native-probe.py <3.14>` | 3.12 controller → 3.14.8 Worker, canary denied, hash unchanged | `.omp/sec02-b1a-native-probe.log` |
| `.omp/sec02-b1a-package-smoke.py` | `PACKAGE_SMOKE_OK` (uv build, venv 3.12.7, import site-packages, `omp --help` exit 0) | `.omp/sec02-b1a-package-smoke.log` |
| `git diff --check` | exit 0 | — |

Flake: các lượt full 3.14 trước đây từng lỗi baseline `tests/test_recovery.py`
`os.replace` (`PermissionError`) và PID reuse `git.exe` ở `test_crash_recovery`;
lượt R3a này full 3.14 pass ngay lần đầu. File baseline không bị sửa và không
phải thay đổi B1a.

## Native evidence (controller/Worker thật)

- Controller 3.12.7: `C:\Users\POTA\AppData\Local\Programs\Python\Python312\python.exe`,
  integrity `S-1-16-8192`, elevation 0.
- Worker 3.12.7: cùng interpreter; Worker 3.14.8 cho lượt 3.14:
  `%TEMP%\omp-b1a-py314-medium\python.exe`, integrity `S-1-16-8192`,
  `python.exe` SHA-256 `1b276111176d62c944c84ccffc25aa1b861d45fea27131a4c31326ed5c0fb417`
  (trùng byte với `.omp/python-runtimes/.../python.exe`).
- `.omp` 3.14 runtime gốc mang nhãn Low → process integrity `S-1-16-4096`, Windows
  từ chối tạo AppContainer profile (`0x80070005`). Vì vậy native evidence 3.14
  chạy bằng bản copy cùng build ở path Medium; nhánh `.omp` được ghi BLOCKED,
  không skip/không giả controller 3.14. Tạo lại bản copy bằng
  `python .omp/sec02-b1a-copy-medium.py` (in ra path + integrity).
- Native unittest tự đọc token độc lập (không dùng helper production, không
  import `tools.proofs`); probe 3.12→3.14 bổ sung bằng chứng launch Worker 3.14
  từ controller medium theo spec.

## Giới hạn / chưa claim

- Chưa activation production: `main.py`/CLI/schema không gọi API mới; B1b
  (layout/env/manifest) và B1c (orchestration/recovery) chưa làm.
- Không chứng minh network/credential/handle isolation, không chạy provider/AI
  thật, không dùng tài sản repo/vault thật; canary là fixture.
- Chỉ đổi ACL/label trong `.omp/test-tmp`; không đổi ACL repo hay nhãn runtime
  `.omp`. Nếu CI 3.14 cũng dùng image Low, cần quyết định hạ tầng (bản copy
  Medium hoặc relabel có kiểm soát) trước khi đòi native PASS trên path đó.
- Chưa chạy CI cho đúng commit này, chưa package publish.
- Working tree còn dirty docs của các task khác; đã giữ nguyên, không reset/stash.

## Prompt reviewer

```
Bạn là reviewer độc lập cho SEC-02-B1a (vòng sau review r2 CHANGES_REQUIRED, chỉ
còn SEC02B1A-R3a). Đọc docs/tasks/SEC-02-B1a.md, spec/plan,
docs/reviews/SEC-02-B1a.md, docs/reviews/SEC-02-B1a-r2.md và handoff này.
Trọng tâm: (R3a) `WindowsJobProcess.close()` giữ đúng type/instance
KeyboardInterrupt và SystemExit khi `CloseHandle` ném, chạy nốt các handle còn
lại + unlink best-effort, không đóng lần hai handle không rõ trạng thái, note
sanitize không lộ argv/env/tên file, `_close_failed` sticky, profile binding còn,
gọi close lần hai vẫn refuse. Chạy lại regression
`test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` và
`.omp/sec02-b1a-r3a-control.py` (fail-before/pass-after). Xác nhận R1–R6 không
regress: focused 3.12, focused 3.14 từ bản copy Medium, `.omp/sec02-b1a-controls.py`,
`git diff --check`; full nếu cần. Xác nhận baseline 392 không đổi, chỉ năm file
ownership bị sửa, review history không đổi, không commit/push/DONE. Trả ACCEPTED
hoặc CHANGES REQUESTED kèm lý do; không tự DONE/merge/đóng G3.
```
