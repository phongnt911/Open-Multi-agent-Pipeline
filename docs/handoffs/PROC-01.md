# Handoff PROC-01

- Trạng thái: REVIEW (r2 sau PROC01-R1).
- Owner / revision hoặc diff: AI thực thi PROC-01 (opencode, DeepSeek V4.1 Flash Free), nhận việc ngày 2026-10-23. Base HEAD `fd5e21908fbfd9827077f2a7f1c1b510ee19c324`; toàn bộ thay đổi của task còn trong working tree, chưa commit/push.
- File thay đổi:
  - `tests/test_process_lock.py` (mới) — SHA-256 `7bc208c108de9e949560dbf7819f3a88395a90fa6249e5d56db809d82a7206a3`.
  - `docs/tasks/PROC-01.md` — owner, trạng thái REVIEW, AC1–AC7.
  - `docs/CONTRACTS.md` — bổ sung hợp đồng process lock.
  - `docs/handoffs/PROC-01.md` — handoff này.
  - Không sửa production: `omp_core/main.py` giữ SHA-256 `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0`.
  - Không sửa test cũ: `tests/test_crash_recovery.py` giữ SHA-256 `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820`; các test cũ, workflow, `tools/check.py`, `pyproject.toml` và review history không có diff.
- Hành vi thay đổi và lý do: Không thay đổi hành vi production. Bộ test mới dùng process OMP thật để xác nhận OS lock hiện có đã đúng: độc quyền, non-blocking, tự nhả khi owner chết, `status` read-only và `child_pid` vẫn là guard riêng. Vì không tái hiện được lỗi lock nên không sửa `omp_core/main.py`; chỉ thêm test/evidence và ghi rõ contract. Không thêm PID registry, incarnation registry hay dependency mới. Sau PROC01-R1, helper cleanup test được sửa để mọi lần chờ fallback đều có timeout và luôn thử đóng pipe trước khi báo lỗi.

## Tiêu chí đã đạt / chưa đạt

- **AC1 — đạt**: `test_owner_blocks_resume_and_run_status_is_read_only` và `test_simultaneous_runs_have_one_owner_and_one_worker`. Contender `resume` bị từ chối bởi OS lock; contender `run` bị từ chối bởi boundary unfinished-run hoặc lock hiện có. Không có traceback, checkpoint không đổi, chỉ có đúng một Worker marker, và owner vẫn hoàn tất.
- **AC2 — đạt**: `test_hard_kill_releases_lock_but_child_pid_blocks_resume`. Owner bị kill thật sau readiness; lock file vẫn tồn tại và `resume` mới acquire được OS lock, sau đó bị chặn bởi `Previous child PID`, không auto-clear PID và không spawn Worker mới. Fake Worker được release, xác nhận file exit và process đã dừng trước khi kết thúc fixture; containment cây process vẫn thuộc PROC-02.
- **AC3 — đạt trong phạm vi PROC-01**: `test_owner_blocks...` xác nhận lock đang giữ chặn contender; `test_pid_only_metadata_does_not_grant_lock_or_kill` đặt `child_pid` thành PID của process không liên quan còn sống, xác nhận `resume` bị chặn bởi child guard, không kill PID đó và state không đổi. Không thêm hoặc claim process-incarnation registry; PID đơn lẻ vẫn không phải bằng chứng ownership. Đây là giới hạn được ghi nhận, không phải containment.
- **AC4 — đạt**: trong `test_owner_blocks...`, `status` chạy thành công khi owner đang giữ lock, trả `phase: worker`, không đổi state byte và không tăng Worker marker.
- **AC5 — đạt**: 111 baseline tests giữ nguyên và pass; suite mới không sửa test cũ. Breakdown: `test_cli` 18, `test_crash_recovery` 21, `test_errors` 6, `test_pipeline` 8, `test_recovery` 31, `test_validation` 27 = 111; tất cả exit 0.
- **AC6 — đạt trên Windows sau rerun**: `python tools/check.py` chạy 117 tests, `OK`, exit 0 ở lần chạy cuối; lần chạy trước đó gặp flake `os.replace` đã ghi dưới đây. `git diff --check` exit 0; diff các file bảo vệ so với base exit 0.
- **AC7 — đạt sau PROC01-R1**: negative control vẫn fail trước khi tạo lock; readiness có deadline; mọi process có timeout/cleanup; Worker count có marker riêng; fixture dùng temp riêng dưới `.omp/test-tmp`, Git identity cục bộ, `core.autocrlf=false`, file LF. `_terminate` hiện dùng timeout cho cả lần `communicate` đầu và fallback, đóng pipe trong `finally`, và báo `AssertionError` rõ ràng nếu cả hai lần đều timeout. Nhánh timeout có test fault injection riêng; không có lỗi production mới nên chỉ thêm test/evidence.

## PROC01-R1 — fallback cleanup timeout

- **Finding đã xử lý**: `_terminate` cũ gọi `process.communicate()` không timeout ở nhánh fallback sau `TimeoutExpired`, có thể treo test runner.
- **Sửa**: cả hai lần `communicate` dùng `TERMINATE_TIMEOUT = 10`; sau timeout lần hai, helper đóng từng pipe trong `finally` (`_close_pipes`) rồi raise `AssertionError` chứa PID và thông tin timeout. Các cleanup callback độc lập của `unittest` vẫn tiếp tục được chạy sau lỗi cleanup.
- **Test mới**: `test_terminate_timeout_fallback_is_bounded_and_reports` dùng fake process có `poll()` luôn `None`, ghi nhận hai lần `communicate` timeout, hai lần `kill`, kiểm tra cả ba pipe đã đóng và thông báo lỗi rõ ràng. Không thay đổi assertions lock/`child_pid` của các test cũ.

## Lệnh kiểm thử, OS, Python, số test và exit code

Môi trường: Windows 11 `10.0.26100.0`, Python `3.12.7`, Git `2.56.0.windows.1`.

| Lệnh | Kết quả | Exit code |
| --- | --- | --- |
| `python tools/check.py` (baseline lần 1, trước sửa) | 111 tests, `FAILED (errors=1)` do `PermissionError [WinError 5]` tại `omp_core/main.py:216` (`os.replace`) trong `test_noop_task_completes_as_no_change_without_empty_commit` | 1 |
| `python tools/check.py` (baseline rerun) | 111 tests, `OK` | 0 |
| `python -m unittest tests.test_process_lock -q` (sau PROC01-R1) | 6 tests, `OK` | 0 |
| `python tools/check.py` (full sau PROC01-R1, lần 1) | 117 tests, `OK` | 0 |
| `python tools/check.py` (full lần 2) | 117 tests, `FAILED (errors=2)` cùng `PermissionError [WinError 5]` tại `os.replace` trong `test_crash_recovery` và `test_recovery` | 1 |
| `python tools/check.py` (full rerun lần 3) | 117 tests, `OK` | 0 |
| `git diff --check` | sạch | 0 |
| `git diff --exit-code fd5e219 -- tests cũ, workflow, tools/check.py, pyproject.toml, AGENTS.md, docs/reviews` | không diff | 0 |

Log baseline/full nằm ngoài repo: `%TEMP%\proc01-baseline-check.txt`, `%TEMP%\proc01-baseline-check-2.txt`, `%TEMP%\proc01-r1-tests-1.txt`, `%TEMP%\proc01-r1-full-1.txt`, `%TEMP%\proc01-r1-full-2.txt` và `%TEMP%\proc01-r1-full-3.txt`.

Windows có flake `PermissionError [WinError 5]` tại `os.replace` khi lưu checkpoint: baseline lần đầu lỗi 1 test, full lần 2 lỗi 2 test (`test_interrupted_worker_with_pid_recorded_blocks_resume_safely`, `test_candidate_tree_with_allowed_new_file`); các lần rerun cùng lệnh đều pass. Chưa coi là lỗi logic PROC-01, không sửa production từ flake này. Đây là rủi ro Windows/file-scanner cần reviewer biết; test mới không che lỗi này.

## Kiểm thử chưa chạy và nguyên nhân

- Chưa chạy POSIX/Linux: môi trường hiện tại là Windows; nhánh `fcntl.flock` chưa có evidence thực thi trong handoff này.
- Chưa chạy Python 3.10: mới chạy Python 3.12.7.
- Chưa chạy CI, provider AI thật hoặc network: đúng giới hạn task, tests dùng fake Worker.
- Chưa kiểm chứng Windows Job Object/containment cây process, sandbox OS hoặc PID recycling bằng PID thực sự bị tái sử dụng; các phần này thuộc PROC-02/giới hạn của PROC-01.
- Nhánh timeout cleanup được kiểm bằng fault injection, không phải process thật bị treo pipe; chưa chứng minh mọi file descriptor lạ trên Windows đều đóng được.
- Chưa chứng minh lock file là bất biến theo inode trên mọi filesystem mạng; test chỉ xác nhận cùng path lock còn tồn tại và OS lock được acquire lại.

## Rủi ro / dữ liệu hoặc process còn tồn tại

- Lock file không bị unlink theo thiết kế; `status` không lấy lock. OS lock chỉ serialize orchestrator, không thay guard `child_pid` và không bảo đảm dừng toàn bộ cây process.
- Cleanup test hiện có bound 2×10 giây cho `communicate`; nếu vẫn thất bại, test báo lỗi rõ ràng và đóng pipe best-effort. Không có containment tổng quát cho process tree hoặc bảo đảm process bị treo ở kernel sẽ biến mất.
- Baseline Windows có flake `os.replace` như trên; cần rerun để phân biệt flake với lỗi thật khi review.
- Working tree còn nhiều thay đổi/tài liệu có sẵn ngoài PROC-01; không reset/clean/stash và không coi là diff của task này.

## Docs/ADR cập nhật

- `docs/CONTRACTS.md`: thêm `process lock` (non-blocking, auto-release khi process chết, không unlink lock file, `status` read-only, không thay `child_pid` guard/containment).
- `docs/tasks/PROC-01.md`: owner, trạng thái REVIEW, AC1–AC7.
- Không tạo ADR mới; không mở PROC-02/03.

## Bước tiếp theo / owner đề xuất

1. Reviewer r2 kiểm tra riêng PROC01-R1: `_terminate` có timeout ở cả hai lần `communicate`, đóng pipe trong `finally`, lỗi cleanup rõ ràng và test timeout.
2. Reviewer chạy lại `python tools/check.py`, `python -m unittest tests.test_process_lock -q` và `git diff --check` trên Windows; đối chiếu 111 baseline + 6 test mới = 117.
3. Reviewer quyết định ACCEPTED/BLOCKED cho PROC-01. Không tự chuyển DONE hoặc đóng G2; nếu accepted, điều phối quyết định đóng task/gate theo COORDINATION.
4. Nếu review yêu cầu thay đổi production, cần regression tái hiện lỗi trước; không mở rộng sang containment/PROC-02.

## Prompt bàn giao reviewer (chat)

Bạn là reviewer G2 cho PROC-01 của OMP, vòng r2 sau finding PROC01-R1. Base là `fd5e21908fbfd9827077f2a7f1c1b510ee19c324`; task đang REVIEW, chưa DONE. Hãy đọc `docs/reviews/PROC-01.md` (không sửa), `docs/tasks/PROC-01.md`, `docs/handoffs/PROC-01.md`, test `tests/test_process_lock.py` và diff liên quan. Kiểm tra rằng không có diff production `omp_core/main.py` (SHA-256 `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0`), test cũ/workflow/checker/review history không bị sửa. Xác minh PROC01-R1: `_terminate` dùng timeout cho cả lần `communicate` đầu và fallback, đóng pipe trong `finally`, báo lỗi rõ ràng nếu vẫn timeout, và có test nhánh timeout; assertions lock/`child_pid` phải giữ nguyên. Chạy lại `python tools/check.py`, `python -m unittest tests.test_process_lock -q`, `git diff --check`; đối chiếu 111 baseline + 6 test mới = 117, exit code 0, ghi rõ nếu gặp flake `os.replace`. Trả verdict ACCEPTED/BLOCKED kèm lý do, evidence còn thiếu và giới hạn (POSIX/Python 3.10/containment chưa kiểm). Không tự đóng G2 hoặc tuyên bố DONE.
