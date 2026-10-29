# Hợp đồng kiến trúc và dữ liệu

## Hiện trạng so với mục tiêu

DAT-01 đã khóa schema dữ liệu MVP; REC-01 nâng cấp checkpoint lên
`schema_version: 2` có đối soát chuyển trạng thái, bằng chứng kiểm thử
(`verification_evidence`), ý định commit (`commit_intent`) và kết quả no-op
tường minh (`completion_kind`). Config/task vẫn giữ `schema_version: 1`;
input cũ không có version được nhận như legacy v0 và chuẩn hóa trong bộ nhớ.
State v1 cũ được hỗ trợ đọc/status ở chế độ read-only không rewrite đĩa; resume
trên state v1 chưa hoàn tất bị chặn trước mutation. Migration tự động và crash
matrix sâu thuộc REC-02/PROC; containment Windows đã có primitive native ở
PROC-02 (đã merge), chưa phải gate bảo mật. PROC-03 (DONE) thêm `recover`/
`abort` có digest/run binding, lock, audit và sidecar archive; không đổi
checkpoint schema.

## Schema dữ liệu REC-01

| Dữ liệu | Version | Required | Optional | Ghi chú |
| --- | --- | --- | --- | --- |
| Config | 1; thiếu version là legacy v0 | `worker_argv`, `test_argv`, `timeout_seconds` | - | Legacy được normalize trong memory; argv nonempty string, không NUL; timeout integer `1..86400` |
| Task | 1; thiếu version là legacy v0 | `specification`, `allowed_files_to_modify` | - | Specification nonempty; allowed path là file path tương đối chính xác dùng `/` |
| State | 2 (hỗ trợ đọc v1 read-only) | `run_id`, `phase`, `base_commit`, `config`, `task` | `child_pid`, `last_error`, `result_commit`, `verification_evidence`, `commit_intent`, `completion_kind` | `committing`/`complete` bắt buộc `verification_evidence` (`candidate_tree`, `command_hash`, `task_hash`, `run_id`, `exit_code: 0`) và `commit_intent` (`run_id`, `base_commit`, `expected_tree`, `commit_marker`); `complete` bắt buộc thêm `result_commit` và `completion_kind`. Bắt buộc `intent.expected_tree == evidence.candidate_tree`. |
| Abort marker | 1 (sidecar) | `schema_version`, `action`, `run_id`, `state_sha256`, `child_pid`, `created_utc` | - | Ở `.omp/runs/<run_id>/aborted.json`; `state_sha256` là SHA-256 bytes thô checkpoint được lưu tại `aborted-state.json`. Không phải field của state. |
| Audit event | 1 (sidecar) | `schema_version`, `timestamp_utc`, `action`, `result`, `reason_code`, `run_id`, `state_path`, `before_sha256`, `after_sha256`, `phase_before`, `phase_after`, `state_schema_version`, `child_pid_before`, `child_pid_after`, `checkpoint_changed`, `operator_confirmed_child_stopped`, `containment_proof`, `abort_marker` | - | Ở `.omp/audit/<run_id>/<timestamp>-<uuid>.json`; atomic, không chứa config/task/argv/prompt/log/token/raw exception command. `after_sha256` là SHA-256 sau thao tác (bằng `before_sha256` nếu `checkpoint_changed: false`), hoặc chuỗi `"unknown"` nếu `checkpoint_changed: true` nhưng digest mới không đọc được (ví dụ `checkpoint_digest_failed`). |

- Unknown field bị từ chối ở mọi object config/task/state, kể cả object lồng
  trong state. Không có field retry/budget giả khi chưa có consumer.
- `run_id` khớp `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; không dùng làm đường dẫn tự
  do. Phase chỉ thuộc `preparing`, `worker`, `verifying`, `committing`,
  `complete`.
- Commit ID là full hex OID đúng object format repo: 40 ký tự SHA-1 hoặc 64 ký
  tự SHA-256. Boundary đọc `git rev-parse --show-object-format` (read-only) và
  kiểm tra đúng độ dài; không ép mọi repo là SHA-1. Git không hỗ trợ option
  này hoặc metadata lỗi bị từ chối với cause; không fallback/đoán SHA-1.
- `read` bọc `JSONDecodeError`, `ValueError` do integer vượt giới hạn parser
  và `RecursionError` do JSON lồng quá sâu thành lỗi boundary, giữ `__cause__`,
  không echo payload; lỗi config/task là `InputError`, lỗi state là
  `OperationError`.
- Allowed path từ chối empty segment, `.`/`..`, backslash, drive/UNC/ADS,
  glob, NUL và segment `.git`/`.omp` không phân biệt hoa thường. Space/Unicode
  hợp lệ. Đây là policy MVP; symlink/junction/ignored/Git tampering sâu thuộc
  SEC-03.
- `child_pid` là `null` hoặc integer `>0`; PID hợp lệ không chứng minh process
  ownership. `last_error` là string; `result_commit` được validate như
  `base_commit`.
- `run`/`resume` validate trước lock và đọc/validate lại dữ liệu authoritative
  trong lock trước clone/process/checkpoint mutation. `status` chỉ đọc, không
  ghi state. Lỗi config/task là `InputError`; lỗi state là `OperationError`;
  CLI trả nonzero, không traceback cho lỗi dự kiến.
- `recover RUN_ID --state-sha256 HEX` chỉ đọc/validate state và lock, không
  chạy Worker/test, không sửa checkout/commit. Mặc định refuse khi còn
  `child_pid`; chỉ clear `child_pid` khi PID ở trạng thái absent và người vận
  hành truyền `--confirm-child-stopped`. Đây là attestation, không phải proof
  ownership/tree-stop; PID sống/unknown bị refuse, không kill. Complete state
  vẫn đối soát provenance/evidence/intent/HEAD/tree; complete còn `child_pid`
  bị coi là không nhất quán.
- `abort RUN_ID --state-sha256 HEX` không xóa checkpoint/clone/log/commit;
  archive exact state bytes vào `.omp/runs/<run_id>/aborted-state.json`, ghi
  marker `aborted.json`, rồi audit. Abort idempotent, không kill, không clear
  `child_pid`; nếu `child_pid` còn ghi thì refuse. `resume`/`recover` từ chối
  run đã abort; `run` chỉ bắt đầu run mới khi marker + archive khớp đúng bytes
  state cũ và `child_pid` null. Nếu abort marker tồn tại bên cạnh checkpoint
  (kể cả phase complete), marker và archive bắt buộc phải được validate và đối
  soát đúng exact bytes trước khi cho phép bắt đầu run mới; marker/archive lệch,
  hỏng hoặc thiếu fail-closed trước mọi shortcut phase complete.

## Bất biến bắt buộc

1. Repo nguồn: OMP không tự sửa, stage, reset hoặc commit nội dung người dùng.
2. Mỗi project/run chỉ một owner; không spawn Worker mới khi owner cũ chưa được
   chứng minh đã dừng. Khóa file không thay thế containment process tree. Trên
   Windows, Job Object kill-on-close là lớp containment chính; lỗi setup job
   phải fail-closed, không chạy Worker không được quản lý.
3. Không complete khi test fail, không tìm thấy test, scope sai hoặc checkpoint
   chưa đối soát. Test result phải thuộc đúng tree và command được phê chuẩn.
4. Không tin task/provider để chọn đường dẫn checkpoint, sửa baseline hoặc
   thay lệnh test. Sai schema phải từ chối trước side effect.
5. Side effect không tự replay nếu chưa có quy tắc idempotency/đối soát.
6. Không nâng cấp cam kết từ "phát hiện sau chạy" thành "ngăn trước chạy".

## Process containment trên Windows (PROC-02 REVIEW)

- Mỗi Worker/test command có một Job Object riêng, không kế thừa handle, bật
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Process được tạo suspended với
  `PROC_THREAD_ATTRIBUTE_JOB_LIST`; containment có hiệu lực trước
  `ResumeThread`, không có bước create-rồi-attach.
- `child_pid` phải được save thành công trước resume. Save/resume/job-membership
  lỗi thì terminate job và fail-closed; không fallback sang Popen không
  containment.
- Root exit khi job còn process là operation error, không phải complete.
  Timeout/Ctrl+C/cleanup dùng `TerminateJobObject` và chờ job rỗng có giới hạn
  10 giây. Chỉ khi job rỗng mới xóa `child_pid`; hết hạn thì giữ PID, trả
  cleanup error và không spawn thay thế.
- Owner bị kill cứng: Windows đóng job handle cuối cùng và kill process còn
  trong job, kể cả process suspended. Đây là containment vòng đời, không phải
  sandbox: không hạn chế quyền file/network/credential và không đổi user.
- Job không bật breakaway. Yêu cầu `PROC_THREAD_ATTRIBUTE_JOB_LIST` cần
  Windows 10 / Windows Server 2016 trở lên (nested jobs có từ Windows 8 nhưng
  không thay thế yêu cầu job-list); môi trường không hỗ trợ bị từ chối, không
  silently fallback.
- POSIX giữ process group + SIGKILL baseline. `child_pid` vẫn là guard sau crash
  cứng; PROC-03 `recover` chỉ phân loại PID read-only và cần explicit operator
  attestation, không tự kill hay chứng minh ownership.

## Cách ly quyền OS trên Windows (SEC-01 DONE: fixture proof)

- SEC-01 chọn AppContainer/lowbox làm candidate boundary; ADR-0006 và
  `docs/security/THREAT-MODEL.md` là nguồn chi tiết. Đây chưa phải cam kết
  production và chưa tích hợp `omp_core`.
- Trên fixture tạm, process AppContainer thật có cùng user SID, low integrity,
  0 capability; OS từ chối đọc/ghi sáu canary source/runtime/vault/config/
  audit/credentials và hash không đổi, trong khi process cùng user không
  AppContainer truy cập được. Đây là negative proof trên fixture, không phải
  bằng chứng tài sản thật đã được bảo vệ.
- Chưa được claim network-isolated, credential-isolated hoặc handle-isolated.
  Network mới thử loopback; credential là canary giả; inherited handle chưa
  kiểm; Worker CLI/Python/provider thật chưa chạy dưới boundary.
- Việc cài ACL, cấp capability tối thiểu, giới hạn handle, cleanup profile và
  recovery khi owner chết thuộc SEC-02/SEC-03; lỗi setup phải fail-closed,
  không fallback sang cùng-user unrestricted. Job Object vẫn chỉ là lifecycle.
- Không dùng AppContainer trong production cho đến khi runner tích hợp, có
  negative proof cho tài sản/network thật và review độc lập security gate.

## AppContainer primitive trong Job runner (SEC-02-B1a REVIEW)

- B1a chỉ thêm primitive vào `omp_core/windows_job.py`; `main.py`/CLI/schema
  không gọi API mới và legacy `app_container=None` giữ nguyên hai attribute,
  `env=None` và hành vi Worker cũ. Chưa phải activation production.
- `WindowsAppContainerProfile.create(name)` tạo profile mới và sở hữu SID +
  folder; tên trùng bị từ chối, không adopt/delete profile lạ. Native return và
  owner construction nằm trong guard: interrupt/`SystemExit` sau khi Windows đã
  cấp profile vẫn giải phóng SID + profile; out SID khác 0 là bằng chứng sở hữu,
  còn HRESULT collision không bao giờ xoá profile lạ. Lỗi cleanup được ghi note
  nhưng giữ nguyên type exception gốc. Thuộc tính `name`/`sid`/`folder`/`closed`
  là typed; `close()` chỉ giải phóng tài nguyên object này tạo, idempotent sau
  thành công, từ chối khi cây process bound còn sống/không xác minh được, khi
  SID đã mất quyền sở hữu hoặc start trước đó chưa chứng minh cleanup; giữ
  `closed=False` nếu FreeSid/HRESULT lỗi. Binding process chỉ được gỡ sau khi
  FreeSid và delete profile thành công.
- `WindowsJobProcess.start(..., app_container=<profile>)` yêu cầu `env` mapping
  text tường minh (key rỗng, `=`, NUL, value sai kiểu bị từ chối trước mọi side
  effect), profile mở/chưa bind và còn SID dùng được. `CreateProcessW` nhận ba
  attribute: job list, stdin/log handle allowlist, `SECURITY_CAPABILITIES` zero
  capability; child suspended, token được đọc lại và phải cùng user, đúng
  AppContainer SID, low integrity, 0 capability trước khi start trả về. Caller
  vẫn tự save PID rồi `resume`. Nếu start fail sau khi đã tạo process, cleanup
  terminate process + job, wait bounded tới job rỗng, đóng handle và kiểm kết
  quả unlink stdin; bước nào không chứng minh được sẽ poison profile (chặn
  replacement start và close) thay vì retry/fallback. Chỉ cleanup đầy đủ mới
  cho profile dùng lại.
- Một profile chỉ bind một cây tại một thời điểm: cây live/unknown chặn start
  kế tiếp và chặn `profile.close()`; sequential reuse chỉ sau `tree_stopped`.
  `WindowsJobProcess.close()` kiểm tra kết quả `CloseHandle` và unlink stdin,
  giữ failure sticky và giữ binding profile nếu bất kỳ bước nào lỗi; chỉ gỡ
  binding sau khi mọi bước thành công. `KeyboardInterrupt`/`SystemExit` tại
  `CloseHandle` được giữ đúng type: phần cleanup còn lại vẫn chạy best-effort,
  lỗi kèm theo ghi note đã sanitize, handle không rõ trạng thái không bị đóng
  lần hai và failure vẫn sticky. Không fallback Popen/same-user; lỗi native
  giữ stage + cause đã sanitize.
- Giới hạn B1a: chưa chứng minh network/credential/handle isolation, chưa tích
  hợp orchestrator, chưa có layout/env/approved manifest (B1b) hoặc recovery
  (B1c). AppContainer cần Windows 10/Server 2016+ và controller medium
  integrity; runtime 3.14 local mang nhãn Low phải chạy từ bản copy Medium.
  Fixture native chỉ dùng runtime/workspace/profile/canary tạm, không claim tài
  sản repo/vault thật.

## Hợp đồng SEC-02-B1b: Layout L2, Trusted Inputs và Offline Reader

- `omp_core.trusted_layout.build_trusted_environment(run_id, task_id, *, temp_dir, runtime_path=None, extra_omp_keys=None, local_app_data=None)`:
  Dựng mapping môi trường mới từ đầu cho Worker AppContainer:
  - Chỉ chứa `SystemRoot`, `SystemDrive`, `PATH` (chỉ runtime và System32), `TEMP`/`TMP` (thư mục temp do run sở hữu), `LOCALAPPDATA` (nếu có), `OMP_RUN_ID`, `OMP_TASK_ID` và các biến tiền tố `OMP_` tường minh.
  - Từ chối key/value không phải string, rỗng, chứa NUL hoặc trùng lặp không phân biệt hoa thường. Key phải tuân theo pattern identifier Win32 (`^[A-Za-z_][A-Za-z0-9_]*$`), cấm chứa ký tự `=`.
  - Kiểm tra đường dẫn khắt khe (R7a): `temp_dir` và `runtime_path` (nếu có) bắt buộc là đường dẫn tuyệt đối, phải tồn tại thực tế và là thư mục (`is_dir()`), cấm chứa dấu chấm phẩy `;` (ngăn PATH separator injection), và cả bản thân lẫn mọi thư mục tổ tiên không được là reparse point hay junction.
  - Cấm kế thừa `os.environ` của cha, cấm nhận env từ task và chặn triệt để biến nguy hiểm (`PYTHONPATH`, `PYTHONHOME`, `COMSPEC`, `SHELL`, `LD_PRELOAD`).
  - Worker CPython 3.12 thực thi bắt buộc nhận cờ `-I -P`.
- `omp_core.trusted_layout.TrustedManifest`:
  Manifest verification độc lập với schema riêng `omp.sec02.b1b.manifest.v1` (không phải checkpoint schema).
  - Thuộc tính bắt buộc: `schema_version`, `task_id`, `run_id`, `nonce`, `verifier_version`, `candidate_bytes_sha256` (64 hex lowercase), `canonical_tree_oid` (40 hoặc 64 hex lowercase khớp với `tree_algorithm`), `tree_algorithm` (`sha1` yêu cầu 40 hex chars; `sha256` yêu cầu 64 hex chars), `approved_argv` (tuple chuỗi không rỗng, không NUL), `timeout_seconds` (float/int dương hữu hạn, loại trừ bool), `spec_hash` (64 hex lowercase), `expected_artifact_relpath` (relative path chuẩn hóa forward-slash, không traversal, không NUL).
  - Strict typing: từ chối trường lạ, sai kiểu (bool không thay thế int/float), timeout không hợp lệ hoặc format hash sai.
  - Xuất bản nguyên tử không ghi đè (R4a): ghi vào temp file độc quyền (`os.O_CREAT | os.O_EXCL`), flush, fsync và commit bằng atomic move không thay thế đích (`os.rename` trên Windows, MoveFileExW không cờ `MOVEFILE_REPLACE_EXISTING`). Nếu file đích đã tồn tại với cùng nội dung, coi là thành công idempotent và dọn dẹp file temp. Nếu file đích tồn tại với nội dung khác, từ chối thay thế (`TrustedLayoutSecurityError`), bảo toàn 100% bytes file đích và dọn dẹp file temp.
  - `verify_candidate(candidate_bytes, invocation_argv)` đối soát tính toàn vẹn bytes và argv đã phê duyệt; drift hoặc replay bị từ chối mà không làm lộ payload.
- `omp_core.trusted_layout.OwnedL2Layout`:
  Quản lý cấu trúc thư mục L2 sở hữu độc quyền tại disposable root:
  - Cấu trúc: `<repo>/.omp/runs/<run_id>/checkout`, `vault`, `state`, `log`, `tmp`. Root phải được tạo mới độc quyền; từ chối root có sẵn hoặc reparse point.
  - DACL trên Windows (R6a, R6b, R6c): User SID có Full Control trên toàn bộ cây; Container SID có traverse trên thư mục cha (`_FILE_GENERIC_EXECUTE`, exact mask `0x1200A0`, flags `0`), Full Control có kế thừa (`_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE`, mask `0x1F01FF`) trên `checkout` và `tmp`, và tuyệt đối không có quyền (không có ACE) trên `vault`, `state`, `log` (user-only protected DACL). Ancestor DACL verification (R6b, R6c) kiểm tra chính xác số lượng ACE (đúng 2 ACE), User mask `_FILE_ALL_ACCESS`, flags 0; Container mask `0x1200A0`, flags 0 (từ chối container có mask `0x1200A9`, Full Control hoặc inheritance flags trên ancestor). Mọi ACE lạ (như `Everyone` `S-1-1-0`, deny ACE hay foreign SID) trên vùng bảo vệ đều bị từ chối fail-closed.
  - Mandatory Integrity Label: Gán nhãn Low Integrity `S-1-16-4096` kế thừa trên `checkout`.
  - Đọc lại DACL và SACL label để xác thực native với đầy đủ type/mask/flags và đối soát cả ancestor traverse; truy vấn lỗi hoặc thiếu quyền phải fail-closed.
  - Dọn dẹp an toàn và fail-closed (R5a, R5c): dọn dẹp chỉ xóa tài nguyên sở hữu. `_reset_dacl_for_deletion` kiểm tra trực tiếp DWORD return code từ `SetNamedSecurityInfoW` (R5c); nếu mã khác `ERROR_SUCCESS` (0) hoặc rmtree gặp lỗi, `self._cleaned` giữ nguyên `False` và raise `TrustedLayoutError` (không báo sạch giả). Toàn bộ exception chain (`__cause__` và `__context__`) được đệ quy sanitize làm sạch token/secret/password/bearer (`Bearer <credential>`) qua `_sanitize_exception_chain` mà không dựa vào fake markers (R5b), đồng thời bảo toàn exception type gốc (`KeyboardInterrupt`/`SystemExit`).
- `omp_core.trusted_layout.read_trusted_artifact(checkout_dir, rel_path, *, job_process, manifest, candidate_relpath=None, actual_argv=None, actual_timeout=None, actual_tree_oid=None, actual_spec_hash=None)`:
  Đọc artifact JSON từ Worker theo cơ chế handle an toàn:
  - Kiểm tra điều kiện tiên quyết từ Windows Job Object: bắt buộc `isinstance(job_process, WindowsJobProcess)`, `job_process.tree_stopped is True`, `type(job_process.active_processes) is int` và `job_process.active_processes == 0`. Tiến trình còn sống, không rõ trạng thái, duck typing hoặc caller truyền bool đơn thuần đều bị từ chối ngay lập tức.
  - Neo root `checkout_dir` bằng directory handle mở với `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT` không cấp `FILE_SHARE_DELETE` (R2a): directory handle được giữ mở liên tục xuyên suốt toàn bộ quá trình mở và đọc file artifact (chống swap directory hay rename race), sau đó xác minh normalized final path của root handle không bị tráo đổi.
  - Mở handle file bằng `CreateFileW` với cờ `FILE_FLAG_OPEN_REPARSE_POINT` nằm trong try block của root handle:
    - Xác minh `GetFileType == FILE_TYPE_DISK`.
    - Kiểm tra `FILE_ATTRIBUTE_REPARSE_POINT` và `nNumberOfLinks <= 1`; từ chối symlink, junction, reparse point và hardlink ra ngoài.
    - Kiểm tra `GetFinalPathNameByHandleW`: đường dẫn chuẩn hóa cuối cùng phải nằm hoàn toàn trong root đã neo, không chứa alternate data stream (`:`) hay device/traversal path.
    - Đọc tối đa 64 KiB + 1 byte; từ chối file vượt quá 64 KiB (65536 bytes).
    - Kiểm tra lại file identity sau khi đọc để loại bỏ rủi ro TOCTOU.
  - Bắt buộc xác minh candidate trên đĩa (R3a, R3b): canonical candidate identity được phân giải tự động từ tham số script đầu tiên trong `manifest.approved_argv[1:]`. Nếu caller truyền `candidate_relpath`, đường dẫn này bắt buộc phải trùng khớp với canonical approved candidate identity (R3b); mọi trường hợp dùng decoy path để lách script không được duyệt đều bị từ chối bằng `TrustedLayoutSecurityError`. Kiểm tra candidate file tồn tại là regular file, đọc bytes và đối soát SHA-256 khớp tuyệt đối với `manifest.candidate_bytes_sha256`.
  - Bắt buộc đầy đủ execution bindings (R3b): các đối số thực thi `actual_argv`, `actual_timeout`, `actual_tree_oid`, `actual_spec_hash` là bắt buộc (không được phép None hoặc bỏ sót). Bất kỳ đối số nào bị thiếu hoặc lệch so với manifest đã phê duyệt đều bị từ chối fail-closed bằng `TrustedLayoutSecurityError`. `actual_timeout` được so sánh đẳng thức số dương hữu hạn có kiểu (`actual_timeout == manifest.timeout_seconds`), từ chối NaN, bool, số âm, hoặc timeout bị thay đổi/hạ thấp.
  - An toàn xử lý lỗi và ngắt trong reader cleanup (R5b, R5d): khi đọc artifact gặp bất kỳ lỗi hoặc ngắt nào (`KeyboardInterrupt`, `SystemExit`, `OSError`), exception chain được sanitize trên mọi đường truyền (không phụ thuộc vào việc đóng handle thành công hay thất bại); kiểu ngoại lệ ban đầu (`KeyboardInterrupt` / `SystemExit`) được bảo toàn nguyên vẹn khi raise; nếu đóng handle thất bại, lỗi đóng handle được lưu vào `__context__` an toàn đã sanitize.
  - Decode strict UTF-8 và parse JSON; đối soát `task_id`, `run_id`, `nonce` khớp chính xác với manifest đã phê duyệt. Xác thực schema artifact khắt khe (R3a): các trường số lượng (`failures`, `errors`) phải là integer `>= 0` (loại trừ bool/str); trường `output` phải là string; nếu `status` là `PASS` hoặc `success` thì bắt buộc `failures == 0` và `errors == 0`.
- Controller Delegation và Raw Evidence (R8a, R8b, R8c, R8d):
  - High controller chạy native suite qua Medium child thật; khởi tạo kernel32 trước spawn, giải phóng handle trong `finally`. Chỉ bỏ terminate khi đã có `WaitForSingleObject == WAIT_OBJECT_0`; lỗi trước bằng chứng exit vẫn yêu cầu terminate. Không nới helper baseline.
  - Reference identity trong report là dữ liệu fixture trusted để kiểm mâu thuẫn, không phải chữ ký hoặc authority của report đối kháng.
  - Trích xuất domain-specific `raw_evidence` từ các test native (R8c): `test_native_l2_layout_and_os_access_denial`, `test_native_artifact_reader_with_job_proof`, `test_native_descendant_blocks_reader_until_stopped` thu thập observations có kiểu về token, DACL/label, canary negative & positive controls, reader state, descendant readiness/tree stop, và cleanup.
  - Controller runner fail-closed nếu thiếu observations, không fallback gán PASS giả (R8c).
  - Validator kiểm tra per-test schema và typed invariants nghiêm ngặt (R8d):
    - `in_any_job` và các cờ trạng thái (`running_reader_refused`, `stopped_reader_passed`, `drift_reader_refused`, `child_marker_verified`, `active_processes_alive`, v.v.) bắt buộc là strict bool `True` (từ chối chuỗi `'NO'`, số 1, bool `False`).
    - `capabilities` strict int 0 (từ chối `False` hay số khác 0).
    - `appcontainer_sid` fullmatch chữ số ASCII, từ chối newline cuối; token SID quan sát từ OS phải khớp `profile_sid` của profile owned.
    - `tree_oid`/`candidate_sha` fullmatch hex thường và khớp `manifest_identity` từ manifest đã approve; `tree_algorithm` phải khớp độ dài OID 40/64. Thiếu reference hoặc identity lệch bị từ chối.
    - `artifact_status` bắt buộc là `'PASS'` hoặc `'success'` (từ chối `'FAIL'`).
    - Loại bỏ triệt để các mâu thuẫn giữa summary report và raw evidence.
- Giới hạn B1b:
  - Module offline phục vụ kiểm thử và xác minh; chưa tích hợp vào CLI hoặc orchestrator `main.py` (thuộc B1c).
  - Không bảo vệ network, credential hệ thống hay chống cúp điện đột ngột ở mức OS.
  - Áp dụng trên Windows Python 3.12 theo quyết định của chủ dự án; không claim hỗ trợ nền tảng khác.


## Ranh giới module mục tiêu

| Trách nhiệm | Đầu vào/đầu ra | Quyền side effect |
| --- | --- | --- |
| CLI | argv → request/exit code | In thông báo; gọi service |
| Models/validation | JSON không tin cậy → dữ liệu hợp lệ hoặc lỗi | Không |
| State store | snapshot/event → checkpoint versioned | Chỉ runtime, khóa/atomic write |
| Process runner | argv/cwd/budget → kết quả có cấu trúc | Spawn/stop/log có containment |
| Git workspace | base commit + scope → checkout/patch/commit | Chỉ clone của run |
| Verification | tree + trusted runner → bằng chứng test | Môi trường test kiểm soát |
| Orchestrator | state + result → transition hợp lệ | Gọi các lớp trên tuần tự |
| Provider adapter | task/context → request/result chuẩn | Qua process runner |

Đây là hướng tách khi task yêu cầu, không phải danh sách file phải scaffold.
Không import CLI từ lớp thấp; không để provider tự ghi state hoặc commit.

## Hợp đồng hoàn tất tại REC-01 và các gate sau

- `phase`: REC-01 đã triển khai bảng `VALID_TRANSITIONS` và helper `transition`
  kiểm tra invariant tại production; cấm gán phase tự do.
- `candidate_tree`: REC-01 tính candidate tree OID qua index Git cô lập trong
  checkout, xác thực chính xác modification, new file và deletion.
- `verification`: REC-01 gắn verification evidence (candidate tree, command hash gồm
  test_argv và timeout_seconds, task hash, run ID, exit code 0); phát hiện test tự sửa
  candidate files và hủy bỏ evidence; thay đổi settings sau verification bị từ chối tái dùng evidence.
- `commit_intent`: REC-01 lưu intent trước khi committing, validate đầy đủ tại transition,
  đối soát commit recovery và đối soát actual commit ở first-run (parent, marker, tree) khớp
  intent/evidence, tránh commit trùng hoặc sai lệch; xử lý no-op tường minh thành `no_change`.
- `crash matrix`: REC-02 triển khai ma trận gián đoạn (Crash Matrix ADR-0003),
  atomic checkpoint (write/flush/fsync/replace, cleanup không che lỗi gốc), đối soát
  staging dở bằng git reset, commit idempotent không duplicate, fail-closed an toàn
  khi clone/checkout dở; ghi nhận giới hạn spawn-gap và bằng chứng Windows.
- `process`: owner identity, PID + dấu nhận diện đời tiến trình/OS containment;
  PID đơn lẻ không đủ chứng minh ownership. Trên Windows, job handle
  kill-on-close + kiểm tra job rỗng là bằng chứng containment trong phiên;
  owner hard-kill làm OS kill cây nhưng không persist handle để recovery query
  lại. PROC-03 không kill theo PID và chỉ clear guard bằng explicit attestation
  khi PID absent (PROC-01/PROC-02/PROC-03).
- `process lock`: `run`/`resume`/`recover`/`abort`/`init` giữ OS lock độc
  quyền non-blocking; lock tự nhả khi process chết, không unlink lock file để
  phá stale; `status` chỉ đọc và không lấy lock. OS lock chỉ serialize
  orchestrator, không thay `child_pid` guard hoặc containment process tree
  (PROC-01/PROC-02/PROC-03).
- `limits`: attempt/call/time budgets hữu hạn; restart không reset budget
  (PROV-02/PIPE-01).
- `error`: mã lỗi ổn định, phase/operation, retryability, message đã redaction;
  CLI trả nonzero cho invalid input, blocked và failed operation (PROV-02/UX-01).

## Nền tảng được hỗ trợ

Windows là nền tảng duy nhất được hỗ trợ và chạy CI từ 2026-10-25. Python
3.12 là baseline dùng ngay; 3.14 là lane compatibility bắt buộc sau full
Windows tests và package smoke. Native security proof được ghi riêng theo runtime. requires-python >=3.12 là mức tối thiểu; không tự suy
phiên bản Python khác đã được kiểm chứng. Linux/POSIX và Python 3.10 không
còn thuộc matrix; nhánh code cũ được giữ nguyên, không cam kết hỗ trợ. Không
xóa hoặc viết lại evidence Linux/3.10 của các lần nghiệm thu trước.
