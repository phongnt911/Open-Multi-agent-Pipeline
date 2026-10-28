# Handoff SEC-02-B1a — AppContainer primitive trong Job runner

- Ngày: 2026-10-27. Trạng thái: **REVIEW** sau khi sửa review R1–R6, SEC02B1A-R3a,
  `SEC02B1A-CI-R1` / `SEC02B1A-CI-R2` theo `docs/reviews/SEC-02-B1a-CI.md`,
  `SEC02B1A-CI-R1a` / `SEC02B1A-CI-R1b` / `SEC02B1A-CI-R1c` theo
  `docs/reviews/SEC-02-B1a-CI-r2.md`, `SEC02B1A-CI-R1a1` theo
  `docs/reviews/SEC-02-B1a-CI-r3.md`, và `SEC02B1A-CI-R1a2` theo
  `docs/reviews/SEC-02-B1a-CI-r4.md` (không commit/push/merge/DONE, G3 chưa đóng).
- Base khởi tạo B1a: `bade93db8a71a5be116bde7792a112205e8fd4a4`; commit B1a hiện
  tại (HEAD): `dae334830e3a0ae58dcbdcfbf29dc50693e124b5` (chưa tạo commit mới).
- Nhánh: `codex/sec-02-b1a-native-primitives`.
- Máy: Windows 11 build 26100; Python 3.12.7; Python 3.14.8 local runtime.
- Task/spec/plan: `docs/tasks/SEC-02-B1a.md`,
  `docs/superpowers/specs/2026-10-26-sec02-b1-design.md`,
  `docs/superpowers/plans/2026-10-26-sec02-b1a.md`.

## File và hash

| File | SHA-256 |
| --- | --- |
| `omp_core/windows_job.py` (không đổi so với `dae3348`) | `692dd73830405d4d92472e7b6796fdd9b977b00f0247e7e2ec1a4cf598689cde` |
| `tests/test_appcontainer_process.py` | `d21924171b07de4e9f8b3ce70990fb297c360e54adb379f1ececb1d87760cc87` |
| `docs/CONTRACTS.md` (không đổi so với `dae3348`) | `76ae41bc77b7c991f43d24db0dcadabac9dcd15d6a28f7400c321d4ae6fde7dc` |
| `docs/tasks/SEC-02-B1a.md` | `c57e8885470772759acba4fa3646576995a8651c5e48a7393c83d53cd494a995` |

`windows_job.py` base là `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d`;
`tests/test_appcontainer_process.py` tại `dae334830e3a0ae58dcbdcfbf29dc50693e124b5`
là `97f6b115dfa0f840a66f993dbefee7ba1574cfba6fc46b04f0a4391eaac2f727`;
`omp_core/main.py` control là
`087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2` và không đổi.
Handoff này là file được cập nhật cuối nên hash của chính nó được in ở báo cáo bàn giao.

## Sửa review R1–R6, R3a, SEC-02-B1a-CI (CI-R1, CI-R2), CI-r2 (R1a, R1b, R1c), CI-r3 (R1a1), và CI-r4 (R1a2)

| Finding | Thay đổi production / test harness | Regression |
| --- | --- | --- |
| R1 native-return allocation | `CreateAppContainerProfile` nằm trong guard; interrupt/`SystemExit` sau khi cấp SID đọc out pointer làm bằng chứng sở hữu và FreeSid + delete; owner construction cũng được bảo vệ; collision không đọc/xoá profile lạ; cleanup lỗi ghi note, giữ type gốc. | `test_interrupt_after_allocation_releases_owned_profile`, `test_interrupt_during_owner_construction_releases_owned_profile`, `test_combined_cleanup_failures_preserve_primary_exception`, `test_collision_never_frees_or_deletes_foreign_profile`; native `test_interrupted_allocation_releases_real_profile` + `test_real_collision_never_deletes_existing_profile`. |
| R2 failed-start cleanup | Một đường cleanup: terminate process + job, wait bounded tới job rỗng, kiểm CloseHandle và unlink; set `_failed_start` trước cleanup, chỉ xoá khi mọi bước chứng minh xong; profile bị poison chặn replacement start/close; không fallback/retry. | `test_unproven_failed_start_poisons_profile_and_blocks_reuse`, `test_proven_failed_start_does_not_poison_profile`, `test_interrupt_at_native_return_with_failed_cleanup_poisons_profile`, `test_start_failure_cleanup_does_not_mask_primary_error`. |
| R3 unlink/close sticky | `_unlink_with_retry` trả `bool`; `WindowsJobProcess.close` set `_close_failed=True` trước cleanup có thể raise, gộp lỗi handle + stdin, chỉ gỡ binding profile sau khi mọi bước thành công; `profile.close` chỉ gỡ binding sau FreeSid + delete thành công. | `test_interrupt_during_close_is_sticky_and_keeps_binding`, `test_unlink_and_handle_failures_are_reported_together`; sticky handle cũ vẫn giữ binding. |
| **R3a** close nuốt interrupt | `CloseHandle` bắt riêng `KeyboardInterrupt`/`SystemExit`: giữ instance đầu, chạy nốt các handle còn lại + unlink, zero handle ngay sau khi thử (không double-close), ghi note sanitize (`thread`/`process`/`job`/`stdin`), rồi `raise` đúng exception gốc. Sticky `_close_failed` và profile binding giữ nguyên vì chỉ clear sau khi mọi bước thành công. | `test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` (2 interrupt types + `CloseHandle` false + unlink false): đúng type/instance, `api.closed == [103, 102, 101]` một lần mỗi handle, notes sanitize, `__cause__`/`__context__` None, call hai refuse, binding còn. |
| R4 profile usability | Start từ chối closed, `_sid_release_failed`, `_sid_pointer is None`, `_failed_start` trước `_kernel32`/file/native; close từ chối SID đã mất/failed-start. | `test_released_sid_blocks_start_and_close_before_native`, `test_failed_sid_release_blocks_start_before_native`, `test_delete_profile_failure_blocks_reuse_before_native`. |
| R5 native fixture cleanup | `_cleanup` gom mọi lỗi và raise `AssertionError`; fallback `TerminateJobObject` + poll job rỗng; register cleanup ngay sau khi tạo scratch; bỏ `ignore_cleanup_errors`. | `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises`; native `setUp` vẫn cleanup thật khi test fail. |
| R6 negative control | Control gỡ guard binding và guard failed-start trên source copy; regression fail vì chạm `_kernel32` (side effect), không chỉ đổi error text; source thật pass, hash không đổi. | `.omp/sec02-b1a-controls.py` (`CONTROLS_OK`). |
| **SEC02B1A-CI-R1** High CI runner (`S-1-16-12288`) chặn `AppContainerNativeTests.setUpClass` | Không sửa production. Khi interpreter ở `S-1-16-12288` và không có `_DELEGATED_ENV`, `setUpClass` gọi `_delegate_native_controller()`. Hàm này dùng `_create_medium_token()` (`OpenProcessToken` → `DuplicateTokenEx` → `SetTokenInformation(TokenIntegrityLevel, S-1-16-8192)`) và `_spawn_with_token()` (`CreateProcessAsUserW` chữ ký chuẩn 11 tham số Win32, tạo child `CREATE_SUSPENDED \| CREATE_NO_WINDOW`) để khởi chạy child controller chạy `_run_delegated_native_suite_cli()`. Trước khi `ResumeThread`, parent đọc `_token_information(pid)` của child đang suspended để xác minh `integrity == S-1-16-8192`, `is_container == 0`, `user == caller_user`; nếu sai sẽ `TerminateProcess` ngay mà không resume. Interpreter Low (`S-1-16-4096`) vẫn fail-closed. | `NativeControllerDelegationTests` (13 tests) + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`CI_R1_R2_CONTROLS_OK`). |
| **SEC02B1A-CI-R1a** Controller ownership & checked cleanup | Bảo vệ ownership của child controller liên tục từ khoảnh khắc native-return của `CreateProcessAsUserW` qua mọi bước khởi tạo tiếp theo (gồm cả `ctypes.WinDLL("kernel32")` và bước đọc token). Hợp nhất một đường cleanup duy nhất `_release_controller_handles()` có deadline tổng hữu hạn (`_CONTROLLER_CLEANUP_TIMEOUT_SECONDS = 5.0`), kiểm tra kết quả của mọi lời gọi WinAPI (`TerminateProcess`, `WaitForSingleObject`, `CloseHandle`, `CloseDesktop`), giữ nguyên handle chưa chứng minh đóng/dừng xong, chặn double-close, giữ nguyên exception gốc và type `KeyboardInterrupt`/`SystemExit` (gắn note đã sanitize qua `add_note`), và poison `_UNPROVEN_CONTROLLER_STATE` để từ chối mở controller thay thế khi cleanup chưa chứng minh xong. `_wait_for_exit` kiểm tra `WAIT_FAILED`/`STILL_ACTIVE` và uỷ quyền timeout termination qua `_release_controller_handles`. | `test_delegate_ownership_guard_covers_kernel_init_and_native_return`, `test_release_controller_handles_checks_results_and_preserves_interrupts` + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`control_r1a_cleanup_unchecked_rc == 1`). |
| **SEC02B1A-CI-R1a1** Token cleanup ownership transfer & desktop-create protection | Đưa `primary` token, `_OwnedDesktop`, và `PROCESS_INFORMATION` vào ownership guard liên tục. `_close_handle_checked` kiểm tra giá trị `BOOL` và zero handle. Khi `_close_handle_checked(own)` lỗi, `primary` vẫn được đóng. `_OwnedDesktop.create` nằm trong cùng khối try có bảo vệ đóng `primary` token (Trigger B: desktop create lỗi thì `primary` không bị leak). Khi child đã spawn: nếu đóng `primary` ném `KeyboardInterrupt`/`SystemExit` (Trigger A) hoặc `CloseHandle` trả `FALSE`/ném `OSError` (Trigger C), `_start_with_medium_token` tự chạy ngay `_cleanup_controller_on_exception` để terminate/wait child, đóng handles, đóng desktop, và poison `_UNPROVEN_CONTROLLER_STATE` từ chối replacement controller. `_delegate_native_controller` nhận `out_info=info` từ trước khi spawn và dùng cờ `_cleaned_up` để ngăn chặn double-cleanup. | `test_token_close_interrupt_after_spawn_terminates_and_cleans_controller`, `test_desktop_create_failure_closes_primary_token`, `test_token_close_failure_or_raise_cleans_controller_and_poisons_state` + `.omp/sec02-b1a-ci-r3-review-boundaries.py --assert-safety` (exit 0) + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`control_r1a1_token_close_unchecked_rc == 1`). |
| **SEC02B1A-CI-R1a2** Bỏ retry bắt TypeError & lan truyền lỗi nội bộ factory | Xoá bỏ hoàn toàn compatibility retry bắt `TypeError` trong `_delegate_native_controller`; gọi trực tiếp `_start_with_medium_token(..., out_info=info)` chỉ đúng một lần. Cập nhật các fake factories (`fake_start` và `start`) trong test harness để nhận `out_info: _PROCESS_INFORMATION | None = None` và cập nhật caller-owned process information theo đúng contract. Lỗi nội bộ `TypeError` (hoặc bất kỳ exception nào) phát sinh từ bên trong factory lập tức được lan truyền ra ngoài sau khi dọn dẹp handles/desktop qua `_cleanup_controller_on_exception`; factory chỉ được gọi đúng 1 lần, không che giấu lỗi, không retry ngoài chính sách fail-closed. | `test_delegate_internal_type_error_propagates_without_retry_or_swallowing` (chứng minh fail-before với `AssertionError: TypeError not raised` trên code cũ, pass-after trên code mới) + reviewer probe `.omp/sec02-b1a-ci-r4-review-typeerror.py --assert-no-retry` (exit 0) + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`control_r1a2_retry_typeerror_rc == 1`). |
| **SEC02B1A-CI-R1b** Không sửa DACL desktop hiện tại | Xoá hoàn toàn `_set_desktop_dacl` và mọi lời gọi `SetSecurityInfo` trên desktop của caller; bỏ sửa DACL trên `SCRATCH_ROOT` dùng chung. Khi desktop hiện tại không có sẵn ACE cho `AC` (`_current_desktop_allows_appcontainer()` chỉ đọc SDDL), `_delegate_native_controller` tạo một desktop tạm độc quyền thuộc quyền sở hữu của fixture (`_OwnedDesktop.create` dùng `CreateDesktopW` với SDDL tối thiểu chỉ cấp `SYSTEM`, `Administrators`, user hiện tại và `AC`), truyền qua `STARTUPINFOW.lpDesktop` cho child controller và các AppContainer Worker của nó, và đóng bằng `CloseDesktop` có kiểm tra `BOOL` sau khi child đã dừng. | `test_native_fixture_never_modifies_existing_desktop_dacl` + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`caller_desktop_dacl_unchanged == True`, `has_set_desktop_dacl == False`, `control_r1b_set_desktop_dacl_present_rc == 1`). |
| **SEC02B1A-CI-R1c** Xác thực đầy đủ bằng chứng cả 6 native tests | `_validate_child_evidence()` và `AppContainerNativeTests._delegated()` kiểm tra đầy đủ schema có kiểu và bất biến của cả 6 native tests (`test_secure_worker_token_identity_and_owned_isolation`, `test_same_user_control_reads_and_writes_the_same_canary`, `test_interrupted_allocation_releases_real_profile`, `test_real_collision_never_deletes_existing_profile`, `test_profile_close_refuses_live_tree_then_allows_sequential_reuse`, `test_root_early_exit_with_live_descendant_is_failure`), từ chối cả 5 mutation của reviewer (`canary_access_allowed`, `foreign_handle_usable`, `invalid_digest`, `other_four_evidence_empty`, `control_cleanup_unproven`) cùng trùng PID hoặc thiếu trường ở cả ranh giới validator và `_delegate_native_controller`. `SECURE_WORKER` kiểm tra `OMP_B1A_PROBE_HANDLE` bằng `kernel32.ReadFile` cho chuỗi `b"protected"` để tránh dương tính giả khi bảng handle của child tái sử dụng cùng chỉ số slot. | `test_validator_and_delegate_reject_contradictory_or_incomplete_evidence` + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`control_r1c_validator_weakened_rc == 1`). |
| **SEC02B1A-CI-R2** `test_cleanup_collects_all_failures_and_raises` phụ thuộc `.omp/test-tmp` tạo sẵn | Trong `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises`: tự cấp phát `TemporaryDirectory(prefix="omp-b1a-cleanup-")` độc quyền, đăng ký `self.addCleanup(root.cleanup)` ngay lập tức, patch `SCRATCH_ROOT` trỏ vào đường dẫn con chưa tồn tại (`missing-scratch-root`), tạo `scratch` trong `owned-parent`, và kiểm tra đủ 4 lỗi (`WaitForSingleObject`, `profile close`, `restore canary DACL`, `CloseHandle(job)`), `not scratch.exists()`, và `not missing_global_root.exists()` (đã **Resolved** trong `SEC-02-B1a-CI-r2.md`). | `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises` chạy độc lập với `SCRATCH_ROOT` chưa tồn tại + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`CI_R1_R2_CONTROLS_OK`). |
| **SEC02B1A-CI-R1b** Không sửa DACL desktop hiện tại | Xoá hoàn toàn `_set_desktop_dacl` và mọi lời gọi `SetSecurityInfo` trên desktop của caller; bỏ sửa DACL trên `SCRATCH_ROOT` dùng chung. Khi desktop hiện tại không có sẵn ACE cho `AC` (`_current_desktop_allows_appcontainer()` chỉ đọc SDDL), `_delegate_native_controller` tạo một desktop tạm độc quyền thuộc quyền sở hữu của fixture (`_OwnedDesktop.create` dùng `CreateDesktopW` với SDDL tối thiểu chỉ cấp `SYSTEM`, `Administrators`, user hiện tại và `AC`), truyền qua `STARTUPINFOW.lpDesktop` cho child controller và các AppContainer Worker của nó, và đóng bằng `CloseDesktop` có kiểm tra `BOOL` sau khi child đã dừng. | `test_native_fixture_never_modifies_existing_desktop_dacl` + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`caller_desktop_dacl_unchanged == True`, `has_set_desktop_dacl == False`, `control_r1b_set_desktop_dacl_present_rc == 1`). |
| **SEC02B1A-CI-R1c** Xác thực đầy đủ bằng chứng cả 6 native tests | `_validate_child_evidence()` và `AppContainerNativeTests._delegated()` kiểm tra đầy đủ schema có kiểu và bất biến của cả 6 native tests (`test_secure_worker_token_identity_and_owned_isolation`, `test_same_user_control_reads_and_writes_the_same_canary`, `test_interrupted_allocation_releases_real_profile`, `test_real_collision_never_deletes_existing_profile`, `test_profile_close_refuses_live_tree_then_allows_sequential_reuse`, `test_root_early_exit_with_live_descendant_is_failure`), từ chối cả 5 mutation của reviewer (`canary_access_allowed`, `foreign_handle_usable`, `invalid_digest`, `other_four_evidence_empty`, `control_cleanup_unproven`) cùng trùng PID hoặc thiếu trường ở cả ranh giới validator và `_delegate_native_controller`. `SECURE_WORKER` kiểm tra `OMP_B1A_PROBE_HANDLE` bằng `kernel32.ReadFile` cho chuỗi `b"protected"` để tránh dương tính giả khi bảng handle của child tái sử dụng cùng chỉ số slot. | `test_validator_and_delegate_reject_contradictory_or_incomplete_evidence` + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`control_r1c_validator_weakened_rc == 1`). |
| **SEC02B1A-CI-R2** `test_cleanup_collects_all_failures_and_raises` phụ thuộc `.omp/test-tmp` tạo sẵn | Trong `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises`: tự cấp phát `TemporaryDirectory(prefix="omp-b1a-cleanup-")` độc quyền, đăng ký `self.addCleanup(root.cleanup)` ngay lập tức, patch `SCRATCH_ROOT` trỏ vào đường dẫn con chưa tồn tại (`missing-scratch-root`), tạo `scratch` trong `owned-parent`, và kiểm tra đủ 4 lỗi (`WaitForSingleObject`, `profile close`, `restore canary DACL`, `CloseHandle(job)`), `not scratch.exists()`, và `not missing_global_root.exists()` (đã **Resolved** trong `SEC-02-B1a-CI-r2.md`). | `NativeFixtureCleanupTests.test_cleanup_collects_all_failures_and_raises` chạy độc lập với `SCRATCH_ROOT` chưa tồn tại + `.omp/sec02-b1a-ci-r1-r2-controls.py` (`CI_R1_R2_CONTROLS_OK`). |

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
| B1a-4 | `AppContainerNativeTests.test_secure_worker_token_identity_and_owned_isolation`: production `start` thật, đọc token trước resume (`is_container`, SID khớp profile, `S-1-16-4096`, 0 capability, cùng user, Job member), marker chưa có; sau resume có marker/stdin/env/workspace IO, fake secret không lọt, handle ngoài allowlist không dùng được (kiểm tra `ReadFile` nội dung canary `b"protected"` bị từ chối). |
| B1a-5 | Cùng test native: canary user-only DACL bị deny read/write, hash trước/sau giữ nguyên; `test_same_user_control_reads_and_writes_the_same_canary` chứng minh same-user không AppContainer truy cập được; fixture chỉ dùng tài nguyên/desktop thuộc quyền sở hữu của fixture, không sửa DACL desktop của caller hay `SCRATCH_ROOT` dùng chung. |
| B1a-6 | `StartFailureTests` (10) + `ProcessLifecycleFaultTests` (10) + `NativeControllerDelegationTests` (14): fault profile alloc/folder/SID, attribute size/init/update, CreateProcessW false, interrupt tại native return, cleanup thất bại → poison, token/membership query, ResumeThread, terminate/wait/query/release, cleanup deadline, sticky close, interrupt tại `CloseHandle` giữ type, guard giữ khi unknown, bảo vệ ownership/checked cleanup của delegated controller qua mọi token/desktop/process boundary (`_close_handle_checked`, `_release_controller_handles`, `_UNPROVEN_CONTROLLER_STATE`, `_OwnedDesktop`), và xoá bỏ retry bắt `TypeError` để lan truyền lỗi nội bộ factory một lần duy nhất (`test_delegate_internal_type_error_propagates_without_retry_or_swallowing`). |
| B1a-7 | `test_root_early_exit_with_live_descendant_is_failure` (root exit còn descendant → lỗi, descendant chết, timeout hữu hạn); `test_wait_wraps_cleanup_failure_with_sanitized_cause` (cause giữ WinError, không argv); `test_token_verification_failure_keeps_stage_and_hides_inputs` (không env/argv); `test_close_handle_interrupt_keeps_type_after_best_effort_cleanup` (KeyboardInterrupt/SystemExit tại `CloseHandle` giữ type sau cleanup, notes sanitize); `_cleanup` strict + fallback job stop. |
| B1a-8 | `.omp/sec02-b1a-controls.py`: base code → focused fail (ImportError); gỡ guard binding và guard failed-start → regression fail vì `kernel touched before validation` (side effect thật); source thật pass; hash source/sentinel không đổi; không mock-mirror. R3a control `.omp/sec02-b1a-r3a-control.py`: revert `close()` về `except BaseException` → regression fail với `WindowsJobError: close contained process failed: thread, job, stdin`; source thật pass (`R3A_CONTROL_OK`). CI R1/R2/R1a/R1b/R1c/R1a1/R1a2 control `.omp/sec02-b1a-ci-r1-r2-controls.py`: fail-before trên commit `dae334830e3a0ae58dcbdcfbf29dc50693e124b5` (High integrity `RuntimeError` + missing `SCRATCH_ROOT` `FileNotFoundError`), pass-after trên 3.12.7 và 3.14.8, và 8 negative controls trên bản copy source (gồm Control 7 R1a1 và Control 8 R1a2) đều fail (`CI_R1_R2_CONTROLS_OK`). Reviewer typeerror probe `.omp/sec02-b1a-ci-r4-review-typeerror.py --assert-no-retry` exit 0; reviewer boundaries probe `.omp/sec02-b1a-ci-r3-review-boundaries.py --assert-safety` exit 0. |
| B1a-9 | Focused **65 OK** và full **457 OK** trên 3.12.7 + 3.14.8 Medium copy, 0 skip mới; baseline 392 không đổi; native evidence ghi rõ provenance (controller Medium trực tiếp vs nhánh uỷ quyền với parent local Medium + injected High check → child Medium thật trên owned desktop; bằng chứng parent thực sự ở High cần tiến trình elevated/CI High thật); package smoke OK; prerequisite Low-label ghi BLOCKED, không skip/fake. |
| B1a-10 | Type hints/docstrings, CONTRACTS cập nhật, handoff có hash/AC/lệnh/giới hạn; chỉ bàn giao REVIEW. |

## Lệnh và kết quả

| Lệnh (rút gọn) | Kết quả | Log |
| --- | --- | --- |
| `python -m unittest tests.test_appcontainer_process -v` | 65 OK, 3.456s, exit 0 | `.omp/sec02-b1a-ci-focused-312.log` |
| `%TEMP%\omp-b1a-py314-medium\python.exe -m unittest tests.test_appcontainer_process -v` | 65 OK, 3.807s, exit 0 | `.omp/sec02-b1a-ci-focused-314-medium.log` |
| `.omp/python-runtimes/.../python.exe -m unittest tests.test_appcontainer_process -v` | 58 unit OK; native class fail-closed prerequisite (Low IL `S-1-16-4096`), 0 skip, exit 1 → BLOCKED cho path này | `.omp/sec02-b1a-ci-focused-314-low-blocked.log` |
| `python tools/check.py` | 457 tests OK, 126.142s, exit 0 | `.omp/sec02-b1a-ci-full-312.log` |
| 3.14.8 Medium copy `tools/check.py` | 457 tests OK, 122.972s, exit 0 | `.omp/sec02-b1a-ci-full-314.log` |
| `python .omp/sec02-b1a-ci-r4-review-typeerror.py --assert-no-retry` | exit 0, no retry, call count 1, unexpected internal TypeError propagated | `.omp/sec02-b1a-ci-r4-review-typeerror.json` |
| `python .omp/sec02-b1a-ci-r3-review-boundaries.py --assert-safety` | exit 0, both KI/SystemExit trigger controller/desktop cleanup, desktop create failure closes primary token, replacement blocked | — |
| `.omp/sec02-b1a-ci-r1-r2-controls.py` | `CI_R1_R2_CONTROLS_OK`: fail-before (`dae3348`), pass-after injected-High branch → child Medium thật trên 3.12.7 + 3.14.8 & R2 missing `SCRATCH_ROOT`, 8 negative controls fail, hash không đổi | `.omp/sec02-b1a-ci-r1-r2-controls.log` |
| `.omp/sec02-b1a-controls.py` | `CONTROLS_OK`, hash không đổi | `.omp/sec02-b1a-controls.log` |
| `.omp/sec02-b1a-r3a-control.py` | `R3A_CONTROL_OK`: reverted close fail (WindowsJobError thay type), real source pass | `.omp/sec02-b1a-r3a-control.log` |
| `.omp/sec02-b1a-native-probe.py <3.14>` | 3.12 controller → 3.14.8 Worker, canary denied, hash unchanged | `.omp/sec02-b1a-ci-native-probe.log` |
| `.omp/sec02-b1a-package-smoke.py` | `PACKAGE_SMOKE_OK` (uv build, venv 3.12.7, import site-packages, `omp --help` exit 0) | `.omp/sec02-b1a-ci-package-smoke.log` |
| `git diff --check` | exit 0 | — |

Flake: các lượt full 3.14 trước đây từng lỗi baseline `tests/test_recovery.py`
`os.replace` (`PermissionError`) và PID reuse `git.exe` ở `test_crash_recovery`;
lượt CI-R1a1 và CI-R1a2 này full 3.12 và full 3.14 đều pass ngay lần đầu. File baseline
không bị sửa và không phải thay đổi B1a.

## Native evidence (controller/Worker thật & phân biệt provenance)

- Controller trực tiếp ở Medium (`S-1-16-8192`):
  - 3.12.7: `C:\Users\POTA\AppData\Local\Programs\Python\Python312\python.exe`,
    integrity `S-1-16-8192`, elevation 0.
  - 3.14.8: `%TEMP%\omp-b1a-py314-medium\python.exe`, integrity `S-1-16-8192`,
    `python.exe` SHA-256 `1b276111176d62c944c84ccffc25aa1b861d45fea27131a4c31326ed5c0fb417`
    (trùng byte với `.omp/python-runtimes/.../python.exe`).
- Kiểm chứng nhánh uỷ quyền (`provenance_mode: injected_high_parent_to_real_medium_child`,
  ghi trong `.omp/sec02-b1a-ci-r1-r2-controls.json`):
  - **Phân biệt rõ provenance**: Trên máy local hiện tại, tiến trình parent chạy thực
    tế ở Medium (`parent_actual_integrity == S-1-16-8192`) và chỉ mock hàm
    `_current_integrity_rid()` trả `S-1-16-12288` ở bước kiểm tra đầu vào của
    `setUpClass` (`parent_injected_integrity == S-1-16-12288`) để kích hoạt nhánh
    `_delegate_native_controller()`. Việc hạ token (`DuplicateTokenEx` +
    `SetTokenInformation`), tạo `_OwnedDesktop` (`CreateDesktopW`), khởi chạy child
    controller bằng `CreateProcessAsUserW`, kiểm tra token trước `ResumeThread`, và
    chạy cả 6 test AppContainer thật trong child controller đều dùng WinAPI thật
    100%, không sửa DACL desktop của caller (`caller_desktop_dacl_unchanged == True`,
    `has_set_desktop_dacl == False`). Bằng chứng native từ một tiến trình parent
    thực sự có token High (`S-1-16-12288`) cần môi trường elevated/GitHub Actions
    runner High thật và không được tuyên bố từ lần chạy local không nâng quyền này.
  - **Python 3.12.7**: Parent PID `37900` (`parent_actual_integrity == S-1-16-8192`,
    `parent_injected_integrity == S-1-16-12288`) → Child Medium controller PID
    `35252` (`integrity == S-1-16-8192`, `is_container == 0`, cùng `user` SID) →
    AppContainer Worker PID `37800` (`profile_sid == S-1-15-2-982267806-4068483602-3586249970-4204139162-1274294517-597295947-503550857`,
    `integrity == S-1-16-4096`, `capabilities == 0`, `in_any_job == true`, canary
    read/append `denied:PermissionError`, `foreign_handle == denied`, canary SHA-256
    trước/sau đều `9b2bbce6c72dd9bbd9c7a9976d5d76d1fcc2bb9832dec246375bb92006beccc3`),
    same-user control PID `55532` đọc/ghi canary thành công (`canary_sha256_after == 0e4c138480de9837ecc21241cb42569602b351a8bd69899c029e2876e644191b`).
  - **Python 3.14.8**: Parent PID `45292` (`parent_actual_integrity == S-1-16-8192`,
    `parent_injected_integrity == S-1-16-12288`) → Child Medium controller PID
    `55404` (`integrity == S-1-16-8192`, `is_container == 0`, cùng `user` SID) →
    AppContainer Worker PID `44652` (`profile_sid == S-1-15-2-131798082-2792998659-3547453345-457943883-437205534-635424555-162845754`,
    `integrity == S-1-16-4096`, `capabilities == 0`, `in_any_job == true`, canary
    read/append `denied:PermissionError`, `foreign_handle == denied`, canary SHA-256
    trước/sau đều `9b2bbce6c72dd9bbd9c7a9976d5d76d1fcc2bb9832dec246375bb92006beccc3`),
    same-user control PID `26656` đọc/ghi canary thành công (`canary_sha256_after == 0e4c138480de9837ecc21241cb42569602b351a8bd69899c029e2876e644191b`).
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
- Chỉ đổi ACL/label trên thư mục tạm độc quyền thuộc quyền sở hữu của fixture và
  tạo desktop tạm độc quyền (`_OwnedDesktop` qua `CreateDesktopW` / `CloseDesktop`)
  khi desktop hiện tại hạn chế `AC`; tuyệt đối không sửa DACL của desktop caller,
  không sửa `SCRATCH_ROOT` dùng chung, không đổi ACL repo hay nhãn runtime `.omp`.
- Chưa chạy GitHub Actions từ xa (nơi parent thực sự có token High `S-1-16-12288`)
  cho bản sửa này vì chưa được phép commit/push.
- Working tree còn dirty docs của các task khác; đã giữ nguyên, không reset/stash.

## Prompt reviewer

```
Bạn là reviewer độc lập cho SEC-02-B1a (vòng khắc phục r5 theo docs/reviews/SEC-02-B1a-CI-r4.md
cho SEC02B1A-CI-R1a2 trên base dae334830e3a0ae58dcbdcfbf29dc50693e124b5).
Đọc docs/tasks/SEC-02-B1a.md, spec/plan, docs/reviews/SEC-02-B1a-CI-r4.md và handoff này.
Trọng tâm:
1. (SEC02B1A-CI-R1a1) Đưa primary token, desktop và raw PROCESS_INFORMATION vào
   ownership guard liên tục:
   - Primary token được đóng bằng _close_handle_checked (kiểm tra BOOL và zero handle)
     trong mọi trường hợp kể cả khi _close_handle_checked(own) bị lỗi.
   - _OwnedDesktop.create nằm trong khối try có bảo vệ đóng primary token (Trigger B:
     desktop-create failure đóng primary token và không leak).
   - Khi child suspended đã được spawn: nếu đóng primary token ném KeyboardInterrupt/
     SystemExit (Trigger A) hoặc CloseHandle trả FALSE/ném OSError (Trigger C),
     _start_with_medium_token tự gọi ngay _cleanup_controller_on_exception để
     terminate child, chờ wait xong, đóng process/thread handles, đóng desktop, và
     poison _UNPROVEN_CONTROLLER_STATE từ chối mở replacement controller.
   - _delegate_native_controller nhận out_info=info từ trước khi spawn và dùng cờ
     _cleaned_up để ngăn chặn double-cleanup.
2. (SEC02B1A-CI-R1a2) Bỏ compatibility retry bắt rộng TypeError trong _delegate_native_controller:
   - Xoá khối try...except TypeError; gọi trực tiếp _start_with_medium_token(..., out_info=info).
   - Cập nhật fake_start và start trong test harness để nhận out_info đúng contract.
   - Lỗi nội bộ (TypeError hoặc bất kỳ ngoại lệ nào) lan truyền ngay sau cleanup, factory chỉ được gọi đúng 1 lần.
Chạy lại:
- python .omp/sec02-b1a-ci-r4-review-typeerror.py --assert-no-retry (exit 0)
- python .omp/sec02-b1a-ci-r3-review-boundaries.py --assert-safety (exit 0)
- python .omp/sec02-b1a-ci-r1-r2-controls.py (fail-before trên dae3348, pass-after
  trên 3.12 và 3.14, và 8 negative controls)
- python -m unittest tests.test_appcontainer_process -v (65 tests OK)
- %TEMP%\omp-b1a-py314-medium\python.exe -m unittest tests.test_appcontainer_process -v (65 tests OK)
- python tools/check.py và 3.14 Medium copy tools/check.py (457 tests OK)
- git diff --check
Xác nhận omp_core/windows_job.py, omp_core/main.py và docs/CONTRACTS.md không đổi so với dae3348,
baseline 392 tests không đổi, không commit/push/DONE. Trả ACCEPTED hoặc
CHANGES_REQUIRED kèm lý do; không tự DONE/merge/đóng G3.
```
