# ADR-0010: Layout L2, Trusted Inputs và Offline Artifact Reader — SEC-02 B1b

- Ngày: 2026-10-28. Trạng thái: **Accepted (đã khắc phục đầy đủ SEC02B1B-R1–R9, R2a–R9a sau r2, R3b–R8c sau r3, và R5d, R6c, R8d sau r4)**.
- Base: main `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`.
- Quyết định điều phối: giao B1b triển khai layout L2, environment allowlist, approved manifest và artifact reader offline; chưa tích hợp CLI/runner (thuộc B1c).
- Kế thừa: ADR-0008, ADR-0009 và kết quả probe native B0. G3 vẫn mở; B1c/B2 chưa giao.

## Vấn đề và mục tiêu

SEC-02-B1a đã cung cấp primitive AppContainer profile và Job containment native trên Windows. Tuy nhiên:
1. Worker không được chạy trực tiếp trong repository OMP thật hoặc thư mục dùng chung của hệ thống.
2. Worker không được kế thừa `os.environ` hay `PATH` của cha vì có nguy cơ rò rỉ token/secret hoặc override runtime/python path (`PYTHONPATH`, `PYTHONHOME`).
3. Verification cần một manifest độc lập, bất biến trong vault không thể bị Worker sửa đổi, nhằm bind chặt chẽ candidate bytes SHA-256, tree identity, invocation argv, timeout và task spec.
4. Artifact kết quả từ Worker phải được đọc an toàn: không import/execute code candidate, chứng minh Job đã dừng hoàn toàn (`tree_stopped is True` và `active_processes == 0` kiểu int strict) trước khi mở file, đọc trực tiếp qua Windows handle chống symlink/junction/reparse/ADS/hardlink redirect và giới hạn kích thước tối đa 64 KiB.

Mục tiêu B1b là hiện thực hóa các primitive này trong module độc lập `omp_core/trusted_layout.py` với consumer trực tiếp là test suite, bảo đảm tuyệt đối tính tương thích với 457 baseline tests hiện có.

## Các hướng đã xét

| Hướng | Đánh đổi | Quyết định |
| --- | --- | --- |
| L1 (Workspace trong profile folder của AppContainer) | Đơn giản về ACL nhưng profile folder không phản ánh cấu trúc Git repo của OMP và khó tích hợp orchestration | Không chọn |
| L2 (Workspace `<repo>/.omp/runs/<run_id>/checkout` ngoài profile) | Cần thiết lập DACL protected và explicit low mandatory label `S-1-16-4096` cho checkout, vault/state giữ user-only | **Chọn**: phản ánh đúng kiến trúc OMP, đã được probe B0 chứng minh tính khả thi |
| Dùng `Path.read_text()` sau khi kiểm tra đường dẫn | Dễ bị tấn công TOCTOU (Time-of-Check to Time-of-Use), race condition tráo symlink hoặc hardlink | **Bác bỏ**: bắt buộc mở handle với `FILE_FLAG_OPEN_REPARSE_POINT`, kiểm tra file ID, reparse attribute và final path trước khi đọc |
| Tích hợp ngay vào `main.py` và runner | Trộn lẫn bước xây primitive với orchestration, vi phạm nguyên tắc thay đổi nhỏ có thể review độc lập | **Bác bỏ**: B1b chỉ xây dựng module và kiểm thử offline; B1c mới nối vào workflow |

## Quyết định thiết kế cụ thể

### 1. Owned L2 Layout và Win32 ACL/Label

- Tạo disposable Git repo root hoàn toàn mới do fixture/caller sở hữu độc quyền. Từ chối reparse point hoặc thư mục có sẵn không rõ nguồn gốc.
- Cấu trúc thư mục:
  - `<repo>/.omp/runs/<run_id>/checkout`: thư mục làm việc của Worker.
  - `<repo>/.omp/runs/<run_id>/vault`: vùng trusted lưu manifest và canary.
  - `<repo>/.omp/runs/<run_id>/state`: vùng trusted lưu state và canary.
  - `<repo>/.omp/runs/<run_id>/log`: vùng trusted lưu log thực thi.
  - `<repo>/.omp/runs/<run_id>/tmp`: vùng temp riêng cho Worker.
- DACL trên Windows (R6a, R6b):
  - User SID (Orchestrator/Controller): Full Access (`FILE_ALL_ACCESS`) trên toàn bộ cây.
  - Container SID (AppContainer Worker):
    - Traverse access trên `.omp`, `runs`, `<run_id>` (`_FILE_GENERIC_EXECUTE`, exact mask `0x1200A0`, flags `0`).
    - Full Access (`FILE_ALL_ACCESS` có kế thừa `_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE`, mask `0x1F01FF`) trên `checkout` và `tmp`.
    - Không có ACE trên `vault`, `state`, `log` (User-only protected DACL).
  - Kiểm tra DACL khắt khe: mọi ACE lạ (ví dụ `Everyone` `S-1-1-0`, deny ACE hay foreign SID) trên các vùng bảo vệ đều bị từ chối fail-closed.
  - Ancestor traverse DACL exact policy (R6b, R6c): đối soát đúng 2 ACEs, User mask `_FILE_ALL_ACCESS`, flags 0; Container mask `0x1200A0`, flags 0 (từ chối Container có mask `0x1200A9`, Full Control hay inheritance flags trên ancestor).
- Mandatory Integrity Label:
  - Gán nhãn Low Integrity `S-1-16-4096` tường minh trên `checkout` bằng `AddMandatoryAce` và `SetNamedSecurityInfoW(LABEL_SECURITY_INFORMATION)`.
  - Đọc lại DACL và SACL label để xác minh với đầy đủ type/mask/flags và đối soát cả ancestor traverse; nếu truy vấn thất bại hoặc thiếu thuộc tính, fail-closed ngay lập tức.
- Dọn dẹp ACL và thư mục (R5a, R5c):
  - Chỉ thực hiện trên tài nguyên do layout sở hữu.
  - `_reset_dacl_for_deletion` kiểm tra DWORD return code từ `SetNamedSecurityInfoW` (R5c); nếu mã lỗi khác `ERROR_SUCCESS` (0) hoặc rmtree gặp lỗi, `self._cleaned` giữ nguyên `False` và raise `TrustedLayoutError` (không báo sạch giả dù thư mục biến mất).
  - Toàn bộ exception chain (`__cause__` và `__context__`) được đệ quy sanitize qua `_sanitize_exception_chain` để làm sạch token/secret/password/bearer (`Bearer <credential>`) trong `.args`, `filename`, `filename2`, `strerror` mà không phụ thuộc vào fake test markers (R5b), đồng thời giữ nguyên exception type gốc (`KeyboardInterrupt`/`SystemExit`).

### 2. Environment Allowlist

- Hàm `build_trusted_environment` dựng mapping mới hoàn toàn từ đầu:
  - Cho phép: `SystemRoot`, `SystemDrive`, `PATH` (chỉ runtime đã phê duyệt và System32), `TEMP`/`TMP` (trỏ vào thư mục temp sở hữu), `LOCALAPPDATA` (nếu có), `OMP_RUN_ID`, `OMP_TASK_ID` và các biến tiền tố `OMP_` tường minh.
  - Cấm: sao chép `os.environ` của cha, nhận env từ task, các khóa rỗng, chứa NUL, trùng lặp không phân biệt hoa thường, các biến nguy hiểm (`PYTHONPATH`, `PYTHONHOME`, `COMSPEC`, `SHELL`, `LD_PRELOAD`).
  - Kiểm tra format key Win32 theo regex `^[A-Za-z_][A-Za-z0-9_]*$`, từ chối ký tự `=`, cấm giá trị chuỗi rỗng.
  - Xác thực đường dẫn khắt khe (R7a): `temp_dir` và `runtime_path` (nếu có) bắt buộc là đường dẫn tuyệt đối, phải tồn tại thực tế và là thư mục (`is_dir()`), cấm chứa dấu chấm phẩy `;` (ngăn PATH separator injection), và cả bản thân lẫn mọi thư mục tổ tiên không được là reparse point hay junction.
  - Worker CPython 3.12 được gọi với cờ `-I -P` (isolated mode và safe path).

### 3. Manifest độc lập (`omp.sec02.b1b.manifest.v1`)

- Schema nội bộ riêng biệt phục vụ verification, không thay thế checkpoint state v2.
- Ràng buộc:
  - `task_id`, `run_id`, `nonce`, `verifier_version`, `expected_artifact_relpath`.
  - `candidate_bytes_sha256`: SHA-256 (64 ký tự hex thường).
  - `canonical_tree_oid`: OID (40 ký tự cho `sha1`, 64 ký tự cho `sha256`) kèm ràng buộc chặt chẽ với `tree_algorithm`.
  - `approved_argv`: tuple các chuỗi không rỗng, không NUL.
  - `timeout_seconds`: số thực dương hữu hạn (loại trừ kiểu bool).
  - `spec_hash`: SHA-256 spec của task.
- Lưu trữ trong vault, xuất bản nguyên tử bằng file temp tạo độc quyền (`os.O_CREAT | os.O_EXCL`), write, flush, fsync và commit không ghi đè (R4a):
  - Dùng `os.rename` trên Windows (MoveFileExW không cờ `MOVEFILE_REPLACE_EXISTING`), thất bại với `FileExistsError` nếu file đích đã tồn tại.
  - No-clobber guarantee: nếu file đích đã tồn tại với cùng nội dung, coi là thành công idempotent và dọn dẹp file temp. Nếu file đích tồn tại với nội dung khác, từ chối thay thế (`TrustedLayoutSecurityError`), bảo toàn 100% bytes file đích và dọn dẹp file temp.
- Đối soát candidate bytes và invocation khi verify; phát hiện drift/replay thì fail-closed mà không làm lộ payload.

### 4. Handle-Based Artifact Reader

- Đọc artifact JSON tại đường dẫn tương đối cố định trong `checkout`.
- Điều kiện tiên quyết: truy vấn OS qua B1a `WindowsJobProcess` chứng minh `isinstance(job_process, WindowsJobProcess)`, `tree_stopped is True` và `active_processes == 0` (strict int, không chấp nhận bool, SimpleNamespace hay duck typing).
- Neo root identity bằng directory handle mở với `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT` không cấp `FILE_SHARE_DELETE` (R2a):
  - Directory handle được giữ mở liên tục xuyên suốt toàn bộ quá trình mở và đọc file artifact (chống swap directory hay rename race).
  - Xác minh normalized final path của root handle không bị tráo đổi trước khi trả kết quả.
  - Chặn checkout root là junction hoặc nằm dưới ancestor reparse point.
- Mở file bằng `CreateFileW` với `FILE_FLAG_OPEN_REPARSE_POINT` nằm trong try block của root handle:
  - Kiểm tra `GetFileType == FILE_TYPE_DISK`.
  - Kiểm tra `FILE_ATTRIBUTE_REPARSE_POINT` trong `GetFileInformationByHandle`: từ chối nếu là symlink/junction.
  - Kiểm tra `nNumberOfLinks <= 1`: từ chối hardlink ra ngoài.
  - Kiểm tra `GetFinalPathNameByHandleW`: đường dẫn chuẩn hóa cuối cùng phải nằm trọn trong checkout root đã neo, không chứa ADS (`:`), không phải thiết bị hay UNC traversal.
  - Đọc tối đa 64 KiB + 1 byte: từ chối nếu vượt quá 64 KiB (65536 bytes).
  - Xác thực lại file identity sau khi đọc để chống tráo file (anti-TOCTOU).
- Bắt buộc xác minh candidate trên đĩa (R3a, R3b): script candidate được xác định theo canonical candidate identity từ tham số script đầu tiên trong `manifest.approved_argv[1:]`. Nếu caller truyền `candidate_relpath`, đường dẫn này bắt buộc phải khớp với canonical candidate identity (R3b); mọi decoy override đều bị từ chối bằng `TrustedLayoutSecurityError`. Kiểm tra tồn tại là file thông thường, đọc và đối soát SHA-256 khớp tuyệt đối với `manifest.candidate_bytes_sha256`.
- Bắt buộc đầy đủ execution inputs (R3b): các tham số `actual_argv`, `actual_timeout`, `actual_tree_oid`, `actual_spec_hash` là bắt buộc (không được None). Nếu thiếu hoặc không khớp manifest, raise `TrustedLayoutSecurityError`. `actual_timeout` được đối soát bằng phép so sánh đẳng thức số dương hữu hạn có kiểu (`actual_timeout == manifest.timeout_seconds`), từ chối NaN, bool, số âm, hoặc timeout bị thay đổi/hạ thấp.
- An toàn xử lý lỗi và ngắt trong reader cleanup (R5b, R5d): khi việc đọc gặp bất kỳ lỗi hoặc ngắt nào, exception chain được sanitize trên mọi đường truyền (không phụ thuộc vào việc đóng handle thành công hay thất bại); kiểu ngoại lệ ban đầu (`KeyboardInterrupt` / `SystemExit`) được bảo toàn nguyên vẹn khi raise, lỗi đóng handle được lưu vào `__context__` an toàn đã sanitize.
- Decode strict UTF-8 và parse JSON, kiểm tra liên kết `run_id`, `task_id`, `nonce` khớp với manifest. Xác thực schema artifact khắt khe (R3a): các trường số lượng (`failures`, `errors`) phải là integer `>= 0` (loại trừ bool/str); trường `output` phải là string; nếu `status` là `PASS` hoặc `success` thì bắt buộc `failures == 0` và `errors == 0`.

### 5. Xử lý lỗi và dọn dẹp

- Bọc mọi lỗi I/O, security và system thành phân cấp `TrustedLayoutError`, `TrustedLayoutSecurityError` và `TrustedLayoutBlockedError`.
- Giữ nguyên `__cause__` và `__context__` an toàn, sanitize toàn bộ chuỗi exception qua `_sanitize_exception_chain` để không làm lộ bí mật qua diagnostic chain.
- Không bao giờ nuốt `KeyboardInterrupt` hoặc `SystemExit`.
- Dọn dẹp chỉ xóa tài nguyên thuộc quyền sở hữu, không xóa canaries hoặc bằng chứng ngoài scope.

### 6. Controller Delegation trên High CI Harness

- Kế thừa pattern an toàn của B1a: khởi tạo kernel32 trước spawn, giải phóng handle qua `_release_controller_handles` trong `finally`, kiểm tra `ResumeThread != 0xFFFFFFFF`, `WaitForSingleObject == 0`, `GetExitCodeProcess == 0`.
- Kiểm tra báo cáo con khắt khe (R8a, R8c): khớp chính xác danh sách tên test native, số lượng test run, 0 skips (kiểm tra kiểu list, len == 0), 0 failures (int strict), 0 errors (int strict), Medium integrity, đúng child PID và bắt buộc có domain-specific `raw_evidence` cho từng test (R8c).
- Controller runner fail-closed nếu thiếu observations, không dùng fallback gán PASS giả (R8c).
- Validator kiểm tra per-test schema và typed invariants nghiêm ngặt (R8d):
  - `in_any_job` và các cờ trạng thái (`running_reader_refused`, `stopped_reader_passed`, `drift_reader_refused`, `child_marker_verified`, `active_processes_alive`, v.v.) bắt buộc là strict bool `True` (từ chối chuỗi `'NO'`, số 1, bool `False`).
  - `capabilities` strict int 0 (từ chối `False` hay số khác 0).
  - `appcontainer_sid` kiểm bằng `re.fullmatch` với chữ số ASCII, không nhận newline cuối; đối soát token SID quan sát từ OS với `profile_sid` của profile owned.
  - `tree_oid` và `candidate_sha` kiểm fullmatch hex thường và đối soát với `manifest_identity` lấy từ manifest đã approve; algorithm `sha1`/`sha256` phải khớp độ dài OID 40/64. Thiếu reference hoặc identity lệch bị từ chối.
  - `artifact_status` bắt buộc là `'PASS'` hoặc `'success'` (từ chối `'FAIL'`).
  - Loại bỏ triệt để các mâu thuẫn giữa summary report và raw evidence.
- Entrypoint CLI native-controller (R8b): trích xuất danh sách test identities bất biến trước khi gọi `TestSuite.run()`, tránh lỗi `AttributeError` khi unittest dọn dẹp suite. Test từ High đi qua delegation tới Medium thật, không gọi trực tiếp entrypoint yêu cầu Medium.
- Cleanup controller: chỉ truyền `terminate=False` sau `WaitForSingleObject == WAIT_OBJECT_0`; lỗi token/spawn/wait chưa chứng minh exit vẫn `terminate=True`. Không sửa hoặc nới helper baseline. Các reference trong report phục vụ kiểm mâu thuẫn fixture trusted, không phải chữ ký hay authority của report đối kháng.

## Ranh giới và Giới hạn

1. B1b chỉ là module offline và test harness, chưa kích hoạt trong runner chính (`main.py`).
2. Không tuyên bố là giải pháp sandbox toàn diện hay phòng thủ network/credential.
3. Không can thiệp hoặc thay đổi các bài test baseline (457 tests phải giữ nguyên vẹn).
4. Phù hợp với quyết định của chủ dự án: chỉ bắt buộc kiểm thử trên Windows Python 3.12.
