# Handoff PROC-02

- Trạng thái: **REVIEW** — revision R1–R3/R1a đã được **ACCEPTED tại `docs/reviews/PROC-02-r4.md`**; task vẫn REVIEW, G2 mở; chưa DONE/commit/push/merge.
- Owner / revision: AI thực thi PROC-02 (opencode, DeepSeek V4.1 Flash Free),
  nhận việc 2026-10-23; revision theo `docs/reviews/PROC-02.md`,
  `docs/reviews/PROC-02-r2.md`, `docs/reviews/PROC-02-r3.md` và
  `docs/reviews/PROC-02-r4.md` (ACCEPTED). Base `main`
  `12f2da99b8c983308adc4f4cbae782affa1ef749`; branch
  `codex/proc-02-containment`; HEAD vẫn là base, **không commit/push/merge**.
  Working tree chứa cả thay đổi có sẵn của các việc trước; chỉ các file dưới đây
  được sửa có chủ đích.
- File thay đổi:
  - `omp_core/main.py`: tích hợp process lifecycle, save-before-resume, clear
    `child_pid` chỉ khi tree dừng, cleanup bound; R1 giữ operation/cleanup/save
    diagnostics qua cause chain đã sanitize; R1a clone đệ quy cause/context
    của operation `WindowsJobError` để giữ stage/WinError lồng nhau.
  - `omp_core/windows_job.py` (mới): Job Object native bằng `ctypes`.
  - `tests/test_process_containment.py` (mới): 16 test Windows native.
  - `tests/test_errors.py`: fixture boundary và 4 regression lỗi kết hợp
    R1/R1a.
  - `tests/test_process_lock.py`: R2 phân nhánh Windows/POSIX cho hard-kill.
  - `tests/test_cli.py`: fixture cleanup-timeout dùng `_start_process`; không
    thay đổi trong revision R1–R3/R1a.
  - `README.md`, `docs/CONTRACTS.md`, `docs/SDD.md`: guarantee và giới hạn.
  - `docs/decisions/0004-windows-containment.md` (mới): ADR/process model/lỗi;
    R3 sửa minimum OS.
  - `docs/tasks/PROC-02.md`: owner/status REVIEW; r4 ACCEPTED, ghi revision
    R1a; không sửa hash nghiệm thu.
  - `docs/handoffs/PROC-02.md`: file này.
  - Không sửa AGENTS, COORDINATION, ROADMAP, workflow/checker/dependency,
    review history (`docs/reviews/PROC-02*.md`), workspace cá nhân.

## Hash SHA-256 working tree

| File | SHA-256 |
| --- | --- |
| `omp_core/main.py` | `eda722bd69328db368ae3cb61b97f2ac4174f5adc67a60f1cdf7d9b2b63ef8e2` |
| `omp_core/windows_job.py` | `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d` |
| `tests/test_process_containment.py` | `ae5a1332e8ee72c78ba54595b5c612648c3c2f6478ff40e552238910dcde3111` |
| `tests/test_errors.py` | `0cb2291881e9f90ac84cc3e8c35516b74690d89282d2ed51aba469ce8c3aea8d` |
| `tests/test_process_lock.py` | `5f11cb671ac2125730bde5d428eb0671da8705861bc7e48fb53bdadc14862f5b` |
| `tests/test_cli.py` | `6ad9c402acbdfbf85ae406ee8c79bfe0aeba801313eac8b3c6d076b6c427865a` |
| `tests/test_crash_recovery.py` | `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820` (không đổi) |
| `README.md` | `d964bfa49613425cb152009fdfeff1551559e9b56c12c41e42d0a3a80805b42c` |
| `docs/CONTRACTS.md` | `2c8be56a954a121b0c48993526b419ae241695973b4ed0e8435b934a98ed53d8` |
| `docs/SDD.md` | `66eb3af9d0740c8d38018d30c53cf8b94b6efedf6adedb4d4ff1c80e7952d4a1` |
| `docs/decisions/0004-windows-containment.md` | `9188d025d75aeaa5fc2cdb7013bdca94c58758a3a24b2660bd498d3fb25482b5` |
| `docs/tasks/PROC-02.md` | `16068c9403a329ebb8750f6d5b5020002b60e3e724b3613e912067d94ca71634` |

Handoff không tự hash chính nó. `tests/test_crash_recovery.py` giữ đúng hash
baseline; `main.py`, `test_process_containment.py`, `test_errors.py` và
`docs/tasks/PROC-02.md` thay đổi có chủ đích trong R1a. `test_process_lock.py`
giữ nguyên hash của R2 đã được review chấp nhận.

## Hành vi thay đổi và lý do

Trên Windows, mỗi Worker/test được tạo suspended trong Job Object riêng có
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; process được đưa vào job ngay bằng
`PROC_THREAD_ATTRIBUTE_JOB_LIST`, không có bước create-rồi-attach. Sau khi
kiểm tra `IsProcessInJob`, OMP save `child_pid` rồi mới `ResumeThread`. Nếu
save/resume/job-membership lỗi, job bị terminate; Worker không chạy code.

- Timeout/Ctrl+C/operation failure: `TerminateJobObject`, chờ job rỗng có
  giới hạn 10 giây. Root exit còn con/cháu là operation error, không complete.
- `child_pid` chỉ bị xóa khi job đã rỗng được chứng minh. Cleanup không chứng
  minh được thì giữ PID, fail-closed, không spawn replacement.
- Owner hard-kill: job handle không inherit; Windows đóng handle cuối và kill
  toàn cây, kể cả process suspended. Không cần cleanup code chạy trong owner.
- Không fallback sang Popen không containment. POSIX giữ process group +
  SIGKILL baseline.
- Job Object chỉ là lifecycle containment, **không phải sandbox** bảo mật.

### R1 — giữ cause chain operation/cleanup/save

- `_public_operation_error`, `_public_cleanup_error`, `_cleanup_root_cause` và
  `_public_save_error` tạo exception mới đã sanitize; không nối raw exception
  có `cmd`/argv/token vào cause/context.
- Khi có final save error, chuỗi là: save error → cleanup public → cleanup root
  (`WindowsJobCleanupTimeout`/`WindowsJobError`) → operation public. Nếu cleanup
  không có root Win32 thì bỏ qua tầng root; nếu chỉ có một lỗi thì giữ lỗi đó.
- Operation `WindowsJobError` có root sanitized riêng; `KeyboardInterrupt` được
  clone giữ nguyên loại thay vì bị bọc thành `OperationError`.
- R1a thay clone một tầng bằng `_clone_diagnostic_chain`: mỗi node
  `__cause__`/`__context__` được clone và redact riêng, giữ type/message gồm
  stage và WinError, không trỏ tới exception raw. Chuỗi cleanup/save vẫn đặt
  operation public vào cause của chúng nên operation root lồng nhau không bị
  ghi đè hoặc mất.
- 4 regression mới trong `tests/test_errors.py`: ba test R1
  `test_combined_operation_and_cleanup_timeout_preserves_both_diagnostics`,
  `test_combined_operation_error_and_win32_cleanup_failure_keeps_both`,
  `test_final_save_failure_keeps_operation_and_cleanup_chain` và test R1a
  `test_operation_windows_error_keeps_nested_winerror_across_cleanup_and_save`.
  Các test duyệt cả `__cause__`/`__context__`, kiểm token/argv, giữ
  `child_pid`, không replacement/complete.
- Native R1a regression
  `test_query_failure_after_root_exit_preserves_winerror_cause`: root Python
  thật exit, lần query accounting đầu tiên fail, các lần cleanup sau dùng
  helper thật; kiểm nested cause/context có `WinError 5`, guard được clear và
  không có replacement. Probe trước sửa ép `_public_operation_error` về nhánh
  clone một tầng R1: 2 regression fail vì nested cause/context là `None`;
  sau sửa cả 2 pass.

### R2 — assertion hard-kill theo OS

- `tests/test_process_lock.py` phân nhánh bằng `os.name == "nt"`.
- Windows: sau owner hard-kill, Worker phải gone trong deadline; resume vẫn bị
  `child_pid` chặn; state/worker-starts/lock không đổi; không skip test.
- POSIX: giữ baseline Worker alive, resume bị guard, state/worker-starts không
  đổi, sau đó `release_worker()` và kiểm state lần cuối. Không triển khai POSIX
  containment mới và không đổi POSIX production.
- Test `test_hard_kill_releases_lock_but_child_pid_blocks_resume` đạt trên
  Windows; nhánh POSIX chưa có runtime local trong môi trường này.

### R3 — minimum OS và fail-closed

- ADR-0004, `docs/CONTRACTS.md`, handoff và prompt reviewer dùng minimum
  **Windows 10 / Windows Server 2016+** cho `PROC_THREAD_ATTRIBUTE_JOB_LIST`.
- Nested jobs (có từ Windows 8) được nêu riêng nhưng không thay thế yêu cầu
  job-list.
- Setup/attribute lỗi hoặc OS không hỗ trợ vẫn fail-closed; không thêm fallback
  Popen hoặc create-then-attach. Windows 11 64-bit là runtime đã kiểm.

## Fixture/test cũ đã đổi (không nới assertion)

1. `tests/test_errors.py`: fixture cleanup-timeout patch `pipeline._start_process`
   thay vì `subprocess.Popen`; assertion cleanup/redaction giữ nguyên. Thêm bốn
   regression lỗi kết hợp R1/R1a nêu trên.
2. `tests/test_cli.py`: cleanup-timeout CLI patch `_start_process` thay vì
   `Popen`; bỏ RoutingPopen vì `_start_process` là boundary mới. Assertions
   `SystemExit(1)`, "timed out", không secret/argv và `last_error` sạch giữ nguyên.
3. `tests/test_process_lock.py`: hard-kill owner phân nhánh OS. Windows chứng
   minh Worker bị containment dừng; POSIX giữ baseline Worker alive/release.
   Các assertion lock/state/worker-starts vẫn chạy trên cả hai nhánh, không bị
   bỏ qua.
4. `tests/test_crash_recovery.py` không đổi; toàn bộ crash matrix vẫn đạt.
5. `tests/test_process_containment.py` có 16 test native; không skip Windows.

## AC matrix

| AC | Trạng thái bàn giao | Bằng chứng |
| --- | --- | --- |
| AC1 ADR/proof/API/handle | Đã triển khai | `docs/decisions/0004-windows-containment.md`: link Microsoft Job Objects, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `PROC_THREAD_ATTRIBUTE_JOB_LIST`, `PROCESS_INFORMATION`; handle ownership/lifetime và ma trận lỗi. Minimum OS đã sửa thành Windows 10/Server 2016+; không dùng private CPython handle. |
| AC2 Containment trước execution; gap/failure | Đã triển khai | Job-list atomic + `CREATE_SUSPENDED`; save trước resume. Test: `test_worker_does_not_execute_before_resume`, `test_save_failure_before_resume_never_executes_worker`, `test_resume_failure_stops_suspended_worker_before_execution`, `test_attach_setup_failure_is_fail_closed_without_popen_fallback`; owner hard-kill running/before-save/after-save trước resume. Không fallback Popen. |
| AC3 Timeout/Ctrl+C/operation/root exit | Đã triển khai | `TerminateJobObject` + query `ActiveProcesses` bounded 10 s. Test timeout và root early-exit thật; Ctrl+C là fault injection `KeyboardInterrupt` sau readiness; type `KeyboardInterrupt` được giữ sau R1. Không dùng `process.poll()` làm bằng chứng tree dừng trên Windows. |
| AC4 Real process tree/marker | Đã triển khai | 16 test native; cây root-child-grandchild active 3; owner bị `TerminateProcess` không `/T`; breakaway bị từ chối WinError 5; detached child vẫn bị terminate; sau cleanup kiểm PID/marker trong deadline. Chỉ kill PID thuộc fixture. |
| AC5 Bounded/cause/redaction/fail-closed | Đã triển khai | Cleanup/wait/query có deadline; `test_cleanup_deadline_is_bounded_and_reports_timeout`; R1/R1a chain giữ operation + cleanup + save diagnostics đã sanitize, kể cả nested stage/WinError của operation `WindowsJobError`; test native query-failure sau root exit và test fake cleanup/final-save; last_error/stderr không lộ token; cleanup lỗi giữ `child_pid`, không replacement/complete. Không nối raw argv. |
| AC6 Baseline success/fail/no-op/crash | Đã kiểm chứng | Full suite 137 tests OK; 117 baseline vẫn đạt sau fixture cần thiết và 4 regression mới. Config/task/state contract không đổi; clone/checkpoint/commit tests đạt. |
| AC7 117 baseline + regression native | Đã kiểm chứng | 117 baseline + 16 native + 4 regression R1/R1a = 137. Windows-native dùng fake Worker, readiness/deadline, negative control, finally cleanup; không skip Windows; không AI/quota. |
| AC8 Python/OS/platform/tools | Đã kiểm chứng một phần | Windows 11 IoT Enterprise LTSC (build 26100.0), Python 3.12.7: full 137/137, 0 skipped, exit 0. Containment riêng 16/16, exit 0. `python tools/check.py`, `py_compile`, `git diff --check` đạt. **Chưa chạy local Linux/Python 3.10**, CI thật hoặc live provider; nhánh POSIX của R2 chưa có runtime local. |
| AC9 Handoff/docs | Đã triển khai | File này + README/CONTRACTS/SDD/ADR; base/hash/lệnh/OS/count/exit/limit; nêu rõ Ctrl+C là fault injection, Job Object không phải sandbox và minimum OS đúng. |
| AC10 REVIEW, không tự đóng | Đang REVIEW | Không DONE/ACCEPTED, không đóng G2, không nhận PROC-03; không commit/push/merge; không dependency mới/agent phụ/AI thật; review history không sửa. |

## Lệnh kiểm thử, OS, Python, count, exit

Môi trường: Windows 11 IoT Enterprise LTSC build 26100.0, Python 3.12.7,
AMD64; thời điểm chạy 2026-10-23.

- Baseline trước revision: 132 tests OK (117 baseline + 15 containment).
- Full cuối sau R1–R3 + R1a: `python tools/check.py` → `Source checks passed;
  running 137 tests`, `Ran 137 tests in 87.267s`, `OK`, exit 0; log
  `.omp/proc02-r1a-full-final.log`.
- Containment riêng: `python -m unittest tests.test_process_containment -v`
  → `Ran 16 tests in 2.477s`, `OK`, exit 0 (Windows); log
  `.omp/proc02-r1a-containment.log`.
- R1a pre-fix probe (ép nhánh clone một tầng R1) → 2 regression fail với nested
  cause/context `None`; log `.omp/proc02-r1a-before.log`. Sau sửa hai test
  pass trong full/containment ở trên.
- `python -m py_compile omp_core/main.py omp_core/windows_job.py
  tests/test_process_containment.py tests/test_errors.py
  tests/test_process_lock.py tests/test_cli.py` → exit 0.
- `git diff --check` → exit 0.
- Trong revision, lần full đầu tiên sau khi thêm R1 đã fail 1 test Ctrl+C
  (`test_ctrl_c_fault_injection_stops_tree_and_clears_child_pid`) vì
  `_clone_diagnostic` bọc `KeyboardInterrupt` thành `OperationError`; đã sửa
  để giữ loại `BaseException`/`KeyboardInterrupt`, chạy lại full và containment
  đạt. Không sửa assertion hoặc nới redaction.
- **Chưa chạy:** Linux local, Python 3.10 local, CI thật, live provider; nhánh
  POSIX của test lock chỉ được kiểm bằng đọc code, không có runtime local. Không
  claim power-loss/OS sandbox safety.
- Integration re-run trên working tree/hash nghiệm thu:
  `python tools/check.py` → `Ran 137 tests in 79.188s`, `OK`, exit 0;
  `python -m unittest tests.test_process_containment -v` → `Ran 16 tests in
  2.516s`, `OK`, exit 0. Log `.omp/proc02-integration-evidence/full-check.log`
  và `containment.log`.

## Evidence tích hợp — hash, packaging, manifest

- **Hash:** 12/12 file trong bảng nghiệm thu r4 khớp SHA-256; control
  `tests/test_crash_recovery.py` cũng khớp. Không sửa production/tests/review
  history trong lượt này; handoff không nằm trong bảng hash nghiệm thu. Log
  `.omp/proc02-integration-evidence/hash-check.txt`.
- **Source copy:** `python .omp/proc02_integration_prepare.py` → exit 0;
  `.omp/proc02-integration-evidence/source` chỉ chứa `pyproject.toml`,
  `README.md`, `omp_core/__init__.py`, `omp_core/main.py`,
  `omp_core/windows_job.py`; trước build `artifact_dirs_before_build=[]`
  (không `build/`, `dist/`, `*.egg-info`, `__pycache__`). `main.py` và
  `windows_job.py` trong copy khớp hash repo. Log `source-copy.txt` và
  `source-copy-after-build.txt`.
- **Venv/build:** `.omp/proc02-integration-evidence/venv`, Python 3.12.7,
  pip 24.2. `.omp/proc02-integration-evidence/venv/Scripts/python.exe -m pip
  install "setuptools>=68"` → exit 0 (`setuptools 84.2.1`, pip cache).
  `.omp/proc02-integration-evidence/venv/Scripts/python.exe -m pip install
  --no-build-isolation --no-deps .omp/proc02-integration-evidence/source` →
  exit 0; wheel `omp_orchestrator-0.1.0-py3-none-any.whl` size 23094,
  SHA-256 `5b17f0e32fe4ed51a96db76c2201e33b17eded8e9a07bbe8c02708aa0fb05701`.
  `pip show`: version 0.1.0, `Requires:` rỗng; metadata `requires=None`;
  `pip check` → `No broken requirements found`, exit 0. Log `pip-install.log`,
  `pip-show.txt`, `metadata.txt`, `pip-check.txt`. Không dùng `build/` hoặc
  `omp_orchestrator.egg-info/` cũ trong repo làm evidence.
- **Smoke ngoài source:** cwd `.omp/proc02-integration-evidence/smoke`;
  `..\venv\Scripts\omp.exe --help` exit 0; `import omp_core` trỏ
  `...\venv\Lib\site-packages\omp_core\__init__.py`,
  `is_under_source=False`, exit 0. Log `smoke-entrypoint.log`,
  `smoke-import.log`.
- **Manifest PROC-02 (12 file):** `python .omp/proc02_make_manifest.py` →
  `accepted_hashes=12/12`, `manifest_files=12`, `excluded_existing=34`,
  `working_tree_status_paths=46`, exit 0; log `manifest-build.log`.
  Các file: `omp_core/main.py`, `omp_core/windows_job.py`,
  `tests/test_process_containment.py`, `tests/test_errors.py`,
  `tests/test_process_lock.py`, `tests/test_cli.py`, `README.md`,
  `docs/CONTRACTS.md`, `docs/SDD.md`,
  `docs/decisions/0004-windows-containment.md`, `docs/tasks/PROC-02.md`,
  `docs/handoffs/PROC-02.md`. `tests/test_crash_recovery.py` là control
  baseline, không phải file manifest; hash handoff không nằm trong bảng
  nghiệm thu nên được ghi trong manifest JSON, không dùng để thay hash r4.
- **Loại khỏi manifest (34 path có sẵn):** 11 file điều phối/task/handoff
  `docs/COORDINATION.md`, `docs/FND-01.md`, `docs/ROADMAP.md`,
  `docs/handoffs/FND-02.md`, `docs/handoffs/G1-INTEGRATION.md`,
  `docs/tasks/DAT-01.md`, `docs/tasks/FND-02.md`, `docs/tasks/PROC-01.md`,
  `docs/tasks/REC-01.md`, `docs/tasks/REC-02.md`, `docs/templates/HANDOFF.md`;
  1 workspace `Open-Multi-agent-Pipeline.code-workspace`; 22 review/prompt
  history gồm toàn bộ `docs/reviews/*` (kể cả `PROC-02-r4.md`) và
  `docs/prompts/DAT-01-review.txt`. Manifest JSON chi tiết ở
  `.omp/proc02-integration-evidence/manifest.json` (ignored runtime).
- **Check còn thiếu cho integration:** CI remote trên exact revision cho
  Windows/Linux × Python 3.10/3.12; đặc biệt runtime POSIX branch
  (`tests/test_process_lock.py`, merge path), Linux local, Python 3.10 local,
  package smoke trên Linux/Python 3.10, minimum Windows 10/Server 2016+ và
  live provider. Acceptance r4 chỉ là Windows 11/Python 3.12 local; chưa có
  CI xanh mới.

## Windows evidence chính

- `test_job_contains_worker_child_and_grandchild_before_termination`: root,
  child, grandchild đều alive, `ActiveProcesses == 3`; terminate xong active 0
  và cả 3 PID gone.
- `test_worker_does_not_execute_before_resume`: marker chưa xuất hiện,
  `resumed == False`, `in_job == True`; chỉ sau resume marker mới có.
- `test_stdin_prompt_is_delivered_to_contained_worker`: prompt nhiều dòng được
  đọc đầy đủ từ stdin temp file (đã rewind trước khi resume); không lộ prompt
  trong argv/log.
- `test_owner_hard_kill_stops_running_tree_without_cleanup`: owner bị
  `TerminateProcess` (không taskkill `/T`); cả cây gone.
- `test_owner_hard_kill_stops_suspended_tree_before_save` và
  `...after_save_before_resume`: process suspended trong job bị kill khi owner
  chết; không có cleanup code trong owner.
- `test_execute_timeout_stops_descendants_and_clears_child_pid`: timeout thật
  1 s; cả cây gone; `child_pid == None`.
- `test_root_early_exit_is_failure_and_stops_descendants`: root exit 0 còn con
  → `OperationError`, con gone, `child_pid == None`, không complete.
- `test_breakaway_is_denied_and_tree_can_be_stopped`: `CREATE_BREAKAWAY_FROM_JOB`
  bị từ chối WinError 5; tree dừng.
- `test_detached_child_remains_in_job_and_is_stopped`: `DETACHED_PROCESS` vẫn
  ở job, active 2, terminate hết.
- `test_ctrl_c_fault_injection_stops_tree_and_clears_child_pid`: **fault
  injection**, không phải Ctrl+C console thật; tree thật bị dừng và guard clear.
- `test_cleanup_deadline_is_bounded_and_reports_timeout`: deadline 0.2 s trả
  `WindowsJobCleanupTimeout`, không treo.
- R1/R1a regression dùng fake process để tạo đồng thời operation timeout/error,
  cleanup timeout/Win32 failure và final-save failure; kiểm chain type/message,
  `child_pid` còn, không token/argv, không replacement/complete. Cleanup root
  được kiểm là `WindowsJobCleanupTimeout` hoặc `WindowsJobError` sanitized.
- `test_query_failure_after_root_exit_preserves_winerror_cause`: với process
  root thật trong Job Object, accounting query lần đầu fail; public chain có
  nested `WindowsJobError` chứa `query job accounting failed` và `WinError 5`;
  cleanup dùng query thật thành công nên `child_pid == None`.
- `test_operation_windows_error_keeps_nested_winerror_across_cleanup_and_save`:
  operation có inner WinError, cleanup fail và final save fail; chain vẫn giữ
  operation public → outer `WindowsJobError` → inner `WindowsJobError`; secret
  argv bị redact và `child_pid` vẫn 4242.
- R2 `test_hard_kill_releases_lock_but_child_pid_blocks_resume` trên Windows:
  owner hard-kill, Worker gone, resume bị `Previous child PID` chặn, state và
  worker-start count không đổi.

## Rủi ro / giới hạn / dữ liệu còn tồn tại

- Minimum implementation là Windows 10 / Windows Server 2016+ (job-list
  attribute); đã thử Windows 11 64-bit. Nested jobs có từ Windows 8 nhưng không
  thay thế yêu cầu job-list. Hệ điều hành cũ hơn hoặc API không hỗ trợ sẽ
  fail-closed, không fallback Popen/attach-after-create.
- Không phải sandbox: cùng user vẫn đọc/ghi file, network, credential, memory
  ngoài phạm vi job. Không chứng minh an toàn khi mất điện hoặc Worker đối
  kháng ở mức kernel/admin.
- Worker cố ý để daemon sống sau khi root exit sẽ bị terminate và operation
  fail; đây là fail-closed, có thể ảnh hưởng Worker có background helper.
- Cleanup hết 10 s: `child_pid` còn, cần PROC-03 xử lý; không tự xóa/sửa state.
- Temp stdin nằm dưới run directory, xóa best-effort; không chứa trong argv/log.
- Không có process orphan quan sát thấy sau test; test dùng finally và kiểm PID.
  Các script proof ad-hoc nằm ngoài repo (temp), không phải artifact.
- Chưa có Linux local/Python 3.10 local; CI là bước xác nhận tiếp theo. R2 POSIX
  mới được kiểm bằng code path/assertion, không phải runtime.
- R1 regression fake process kiểm composition/guard; R1a có thêm native probe
  query accounting sau root exit. Không tuyên bố regression fake thay thế
  native probe. R1a chỉ mở rộng chain của operation; cleanup root vẫn sanitize
  theo thiết kế R1.

## Docs/ADR cập nhật

- `docs/decisions/0004-windows-containment.md` (mới; R3 minimum Windows
  10/Server 2016+).
- `README.md`: hành vi/giới hạn Windows và test.
- `docs/CONTRACTS.md`: containment contract, fail-closed, non-sandbox, minimum
  OS đúng.
- `docs/SDD.md`: process model, trust boundary, next step.
- `docs/tasks/PROC-02.md`: status REVIEW, ghi revision R1a.

## Bước tiếp theo / owner đề xuất

r4 đã ACCEPTED cho revision hash hiện tại. Reviewer/integrator độc lập xác nhận
hash, manifest và packaging evidence; sau đó mới lập exact-commit CI plan
Windows/Linux × Python 3.10/3.12, ưu tiên POSIX branch. Acceptance không cho
phép commit/push/merge/DONE/G2; khi được phép mới chạy CI/publish và package
smoke. Không nhận PROC-03.

## Prompt bàn giao reviewer code (đã dùng cho r4)

```text
Bạn là reviewer độc lập cho PROC-02 (containment cây tiến trình Windows), trạng
thái REVIEW, G2 vẫn mở. Base main 12f2da99b8c983308adc4f4cbae782affa1ef749,
branch codex/proc-02-containment, HEAD chưa commit. Đây là revision sau
docs/reviews/PROC-02.md, PROC-02-r2.md và PROC-02-r3.md; không sửa các review
đó. Đọc
docs/tasks/PROC-02.md, docs/handoffs/PROC-02.md,
docs/decisions/0004-windows-containment.md, omp_core/windows_job.py,
omp_core/main.py, tests/test_errors.py, tests/test_process_lock.py và
tests/test_process_containment.py.

Hãy kiểm tra độc lập:
1. Hash/file list và việc không sửa AGENTS/COORDINATION/ROADMAP/workflow/review
   history.
2. Job Object KILL_ON_JOB_CLOSE + PROC_THREAD_ATTRIBUTE_JOB_LIST có loại bỏ
   create/attach gap; process suspended, save child_pid trước resume; không
   fallback Popen khi setup lỗi; job handle không inherit.
3. R1/R1a: operation/cleanup/save diagnostics đều truy được qua cause/context
   đã sanitize; operation `WindowsJobError` giữ nested stage/WinError qua
   `_clone_diagnostic_chain`, không raw argv/token; final save/cleanup không
   làm mất operation root; guard còn, không replacement/complete;
   KeyboardInterrupt vẫn giữ type.
4. R2: hard-kill test phân nhánh OS. Windows bắt Worker gone; POSIX giữ Worker
   alive/release và toàn bộ lock/state/worker-start/PID assertion, không skip.
5. R3: minimum implementation là Windows 10 / Windows Server 2016+, không phải
   mốc Windows 8/8.1; docs/CONTRACTS/ADR/handoff/prompt đồng bộ; setup lỗi fail-closed,
   không create-then-attach/Popen fallback.
6. Owner hard-kill, timeout, Ctrl+C (fault injection), root early-exit,
   breakaway/detached child, save/resume/attach failure; sau cleanup không còn
   PID/marker trong deadline; không kill PID ngoài fixture.
7. child_pid chỉ clear khi job rỗng; cleanup timeout giữ guard; không spawn
   replacement/complete.
8. 117 baseline behaviors + 16 native + 4 regression R1/R1a = 137 Windows tests;
   fixture changes không nới assertion. Chạy `python tools/check.py`,
   `python -m unittest tests.test_process_containment -v`, `git diff --check`;
   ghi OS/Python/count/exit/skips và các check chưa chạy (Linux/Python 3.10
   local, CI thật, live provider; POSIX branch chưa runtime). Kiểm regression
   native query-failure và combined nested WinError/cleanup/final-save.
9. Xác nhận giới hạn: Job Object không phải sandbox; cleanup 10 s; không claim
   power-loss/OS isolation. Minimum OS Windows 10/Server 2016+.

Trả quyết định ACCEPTED / CHANGES / BLOCKED kèm evidence cụ thể. Không merge,
không push, không tự đóng G2 hoặc chuyển DONE.
```


## Prompt bàn giao evidence tích hợp (chat)

```text
Bạn là reviewer/integrator độc lập cho evidence tích hợp PROC-02, sau khi
docs/reviews/PROC-02-r4.md đã ACCEPTED revision hiện tại. Task vẫn REVIEW, G2
mở; không commit/push/merge, không chuyển DONE/đóng G2, không nhận PROC-03.

Hãy kiểm tra độc lập:
1. 12 hash trong bảng r4 khớp working tree; control tests/test_crash_recovery.py
   không đổi. Không có production/test/review history bị sửa.
2. Manifest đúng 12 file PROC-02: main.py, windows_job.py, test containment,
   test errors, test_process_lock, test_cli, README, CONTRACTS, SDD, ADR-0004,
   task và handoff. Tách rõ 34 path có sẵn: coordination/task/handoff cũ,
   workspace và review/prompt history; không gộp vào scope publish.
3. Source copy sạch (không build/dist/egg-info cũ), isolated venv, build/install
   từ source copy, wheel/metadata, pip check và entrypoint smoke ngoài source.
   Không dùng build/egg-info cũ trong repo làm evidence.
4. Chạy lại/đối chiếu `python tools/check.py` (137 tests) và containment (16
   tests) trên exact hash; ghi OS/Python/count/exit/skips.
5. Xác nhận checks còn thiếu: CI Windows/Linux × Python 3.10/3.12, đặc biệt
   POSIX branch runtime, Linux/Python 3.10 local, package smoke trên Linux/3.10,
   minimum Windows 10/Server 2016+ và live provider. Không tuyên bố CI đã xanh.

Trả ACCEPTED / CHANGES / BLOCKED kèm evidence cụ thể. Không tự publish, commit,
push, merge hoặc đóng G2.
```
