# ADR-0008: Trusted verification và CPython Worker dưới AppContainer trên Windows (Giai đoạn A)

- Ngày: 2026-10-25
- Trạng thái: Proposed cho SEC-02 REVIEW giai đoạn A (đã khắc phục SEC02A-R1b–R4c theo docs/reviews/SEC-02-A-r3.md và docs/reviews/SEC-02-A-r4.md); chưa tích hợp production, chưa đóng G3.
- Phạm vi: Thiết kế trusted verification black-box và proof CPython Worker (3.12 và 3.14) dưới AppContainer trên Windows.
- Liên quan: [ADR-0006](0006-windows-isolation.md), [ADR-0007](0007-windows-python-baseline.md), [threat model SEC-01/02](../security/THREAT-MODEL.md), [task SEC-02](../tasks/SEC-02.md), [review SEC-02-A](../reviews/SEC-02-A.md), [review SEC-02-A-r2](../reviews/SEC-02-A-r2.md), [review SEC-02-A-r3](../reviews/SEC-02-A-r3.md).

## 1. Bối cảnh

Trong SEC-01, chúng ta đã chứng minh rằng một process Windows (`cmd.exe`, `curl.exe`) chạy dưới AppContainer profile bị hệ điều hành từ chối truy cập vào các thư mục canary nhạy cảm. Tuy nhiên, hai lỗ hổng kiến trúc lớn vẫn tồn tại:
1. **CPython Runtime Boundary**: Chưa chứng minh được CPython runtime (Python 3.12 và 3.14) có thể khởi chạy và hoạt động ổn định trong môi trường AppContainer (low integrity, 0 capability) mà chỉ cần cấp quyền đọc tối thiểu trên runtime files, không cần cấp quyền quản trị (admin) hoặc sửa đổi quyền toàn hệ thống.
2. **Untrusted Test Execution & Flawed Oracle**: Nếu kiểm tra kết quả Worker bằng cách import mã nguồn do Worker viết vào cùng process test runner (như `pytest` hoặc `unittest` thông thường), mã đối kháng có thể:
   - Monkeypatch các assertion function hoặc test runner.
   - Chặn import hooks qua `sitecustomize.py`, `conftest.py` hoặc sửa `sys.modules`.
   - Gọi `os._exit(0)` để giả mạo trạng thái thành công ngay lập tức.
   - In ra chuỗi `PASS` trên stdout để đánh lừa bộ phân tích log.
   - Để lại descendant process chạy ngầm trong Job Object để sửa đổi artifact sau khi root process đã exit.

Do đó, SEC-02 giai đoạn A đặt ra yêu cầu: Phải xây dựng một mô hình xác minh tin cậy (Trusted Verification) độc lập, trong đó Oracle chạy ở process riêng biệt (trusted process), tuyệt đối không import code của Worker, kiểm tra toàn bộ cây tiến trình dừng hẳn trước khi đọc kết quả, và kiểm chứng CPython 3.12 / 3.14 chạy thực sự dưới AppContainer.

## 2. Quyết định kiến trúc (Cập nhật sau Review SEC02A-R1b–R4c)

### 2.1 Ma trận Principal và Process

| Thực thể | Process & Runtime | Integrity Level | Quyền hạn OS & File Access | Vai trò & Trách nhiệm |
| --- | --- | --- | --- | --- |
| **Controller & Oracle** | Process riêng, Python 3.12 | Medium (`S-1-16-8192`) | User SID chuẩn; toàn quyền trên fixture, vault, runner config, manifest, canaries | Thiết lập fixture, sinh nonce/spec, tạo AppContainer profile, khởi tạo Job Object, thu hồi kết quả và đánh giá độc lập |
| **Fake Worker (3.12)** | Process riêng, CPython 3.12 | Low (`S-1-16-4096`) dưới AppContainer SID | Chỉ có quyền đọc/thực thi runtime; toàn quyền trên `workspace_312`; **KHÔNG** có quyền truy cập vault, runner config, manifest, canaries | Thực thi task giả lập theo task spec, giải bài toán, ghi artifact ra workspace |
| **Fake Worker (3.14)** | Process riêng, CPython 3.14 | Low (`S-1-16-4096`) dưới AppContainer SID | Chỉ có quyền đọc/thực thi runtime; toàn quyền trên `workspace_314`; **KHÔNG** có quyền truy cập vault, runner config, manifest, canaries | Tương tự Worker 3.12, chứng minh tương thích đa phiên bản runtime theo ADR-0007 |
| **Same-User Control** | Process riêng, Python 3.12 | Medium (`S-1-16-8192`) | User SID chuẩn, không AppContainer | Negative control xác minh rằng các canary thực sự có thể truy cập bởi user thường, loại trừ lỗi cấu hình DACL toàn cục |

### 2.2 Ràng buộc Controller theo ADR-0007
Theo ADR-0007, CPython 3.14 trên host chạy ở mức low integrity (`S-1-16-4096`), do đó không có quyền gọi Win32 API `CreateAppContainerProfile` (trả về lỗi `0x80070005: Access is denied`). Vì vậy:
- **Bắt buộc**: Controller / Orchestrator phải thực thi bằng Python 3.12 (medium integrity).
- Worker process có thể chạy CPython 3.12 hoặc CPython 3.14 dưới AppContainer do Controller tạo ra.
- Các unit test và negative control được thiết kế portable bằng mock fault-site và tách biệt khỏi native prerequisite, loại bỏ toàn bộ `skipIf`, đảm bảo chạy đạt 100% (88 tests OK, 0 skipped) trên cả hai runtime Python 3.12 và 3.14.

### 2.3 Nguyên tắc Black-box Trusted Oracle và Chặn Cây Tiến Trình (R1b, R2b, R2c)
Oracle kiểm tra sự hoàn thành của task dựa trên các nguyên tắc sau:
1. **Kiểm tra trạng thái cây tiến trình ngay tại Launch Boundary (R1b)**:
   - Ngay trong `run_worker_in_appcontainer`, sau khi root process kết thúc, hàm kiểm tra `ActiveProcesses` trong Job Object qua `QueryInformationJobObject`.
   - Nếu còn tiến trình active (`active_processes > 0`), hàm lập tức gọi `TerminateJobObject` và lặp kiểm tra với bounded deadline 5.0 giây.
   - Nếu `TerminateJobObject` trả về `FALSE` hoặc hết deadline mà tiến trình vẫn còn active, hàm lập tức **ném ngoại lệ `Sec02ProofError` (fail-closed)**, ngăn chặn hoàn toàn việc thực hiện bất kỳ lần launch nào tiếp theo trên cùng Job Object.
2. **Không import**: Oracle tuyệt đối không dùng `import`, `exec()`, `eval()`, hay tải module đối kháng vào process của mình.
3. **Gắn chặt Provenance thật: Candidate Bytes, Invocations tại Boundary và Canonical Tree Identity (R2b, R2c)**:
   - Manifest trong vault lưu: `task_id`, `run_id`, `nonce`, `candidate_hash` (SHA-256 của `LEGIT_CANDIDATE_CODE`), `approved_argv_pattern`, `approved_timeout_seconds`, `spec`, và canonical `tree_id` (`TREE-<sha256(task_id:candidate_hash)[:16]>`).
   - **Manifest là nguồn approved duy nhất (R2c)**: sau khi fixture ghi manifest và chốt hash, Controller đọc lại, validate toàn bộ trường bắt buộc, rồi dựng identity/spec/argv/timeout từ manifest thay vì hardcode. Manifest 3.12 và 3.14 phải khớp nhau ngoài trường `target`; lệch bị FAIL.
   - Validate manifest từ chối: thiếu trường, `task_id`/`run_id`/`nonce` rỗng, `candidate_hash` không phải SHA-256, `tree_id` không canonical, `approved_argv_pattern` không đúng 4 token `[python.exe, -I, -P, <script>.py]`, timeout không phải số dương hữu hạn, hoặc `spec.a`/`spec.b`/`spec.expected` không phải số (bool bị loại).
   - Launcher trích xuất tham số invocation thực tế tại boundary bằng `shlex.split(command_line)` thay vì tái dựng từ wrapper.
   - **Đối soát candidate script trên đĩa tại thời điểm verify**: Oracle đọc lại file `worker_legit.py` hiện tại trên đĩa và so khớp SHA-256 với `identity["candidate_hash"]`. Nếu candidate file bị sửa đổi sau launch (mutation-after-launch probe), Oracle lập tức REJECT.
   - Oracle đối soát manifest với identity/invocation trước khi đọc artifact:
     - `task_id`, `run_id`, `nonce`, `tree_id`, `candidate_hash` khớp identity.
     - `approved_argv_pattern` bind đúng executable/script basename và đúng thứ tự `-I -P` của `identity["approved_argv"]`.
     - `approved_timeout_seconds` khớp identity timeout.
     - `spec.expected` khớp `identity["expected_result"]`; nếu identity có `spec` thì toàn bộ spec phải khớp.
     - Candidate bytes hash trên đĩa và launch hash.
     - Invocation argv thực tế tại boundary khớp exact 4-token contract, không chấp nhận `-c`, token thừa hoặc executable/script khác.
     - Toàn bộ các trường định danh trong artifact: `task_id`, `run_id`, `nonce`, `tree_id`, và kết quả tính toán (`result: 42`).
     - Bất kỳ sai lệch nào đều khiến Oracle đánh FAIL ngay lập tức.
4. **Kiểm tra tính toàn vẹn (Integrity Check)**: Oracle xác thực SHA-256 của các file nhạy cảm (`vault`, `manifest`, `runner_config`, các canaries) trước và sau khi Worker chạy. Bất kỳ sự thay đổi nào đều dẫn đến REJECT ngay lập tức.
5. **Độc lập với stdout/claim**: Bỏ qua các chuỗi in ra stdout như `PASS`, `OK`, `SUCCESS`. Chỉ công nhận PASS khi artifact chứa đúng kết quả tính toán, đúng định danh, và không có vi phạm canaries.

### 2.4 Quản lý tài nguyên sở hữu và Dọn dẹp Fail-closed (R1b)
- Lớp `OwnedSession` bắt đầu bảo vệ ngay từ trước khi gọi Win32 API đầu tiên (`CreateAppContainerProfile`).
- **Kiểm tra kết quả trả về của WinAPI**:
  - Mọi lời gọi WinAPI (`TerminateJobObject`, `CloseHandle`, `FreeSid`, `DeleteAppContainerProfile`) đều được kiểm tra mã trả về (BOOL hoặc HRESULT). Nếu WinAPI thất bại (trả về FALSE hoặc HRESULT lỗi), lỗi được ghi nhận vào `cleanup_errors` và con trỏ/handle reference **được giữ nguyên** (không gán None) để không che giấu việc giải phóng tài nguyên thất bại.
  - Sau khi gửi `TerminateJobObject`, hệ thống lặp truy vấn `ActiveProcesses` trong Job Object với deadline 5.0 giây. Nếu sau deadline tiến trình vẫn còn active, hệ thống ghi nhận lỗi timeout vào `cleanup_errors`.
- **Bảo toàn ngữ nghĩa ngắt và chống rò rỉ secret**:
  - `run_proof()` bắt riêng biệt `(KeyboardInterrupt, SystemExit)` và **re-raise** ngoại lệ gốc. Việc dọn dẹp tài nguyên được tập trung duy nhất tại khối `finally: session.cleanup()` (loại bỏ double cleanup).
  - Thông báo lỗi và log công khai không nối nguyên văn `str(exception)` để triệt tiêu nguy cơ rò rỉ secret tokens.
- Thứ tự cleanup trong `finally:`:
  1. Terminate và bounded wait cho các tiến trình còn lại trong Job Object, kiểm tra return code và đóng `CloseHandle(job)`.
  2. Dọn dẹp thư mục fixture tạm thời (`temp_dir.cleanup()`).
  3. Giải phóng con trỏ AppContainer SID qua `FreeSid`.
  4. Xóa AppContainer Profile qua `DeleteAppContainerProfile`.
- Mỗi lần khởi chạy Worker, danh sách thuộc tính `attr_blob` được giải phóng qua `DeleteProcThreadAttributeList`.

### 2.5 Cờ cô lập CPython cho Worker và Giới hạn
- Khởi chạy CPython với các tham số: `python.exe -I -P worker_script.py`
  - `-I` (Isolated mode): Bỏ qua biến môi trường `PYTHONPATH`, `PYTHONHOME`, không nạp `sitecustomize`, không thêm thư mục hiện tại vào `sys.path`.
  - `-P` (Safe path): Không thêm thư mục script vào đầu `sys.path` một cách tiềm ẩn rủi ro.
- **Giới hạn được làm rõ**: Cờ `-I -P` ngăn chặn hiệu quả các import hook và `sitecustomize.py` đặt trong workspace của Worker. Nó **không** ngăn chặn `sitecustomize.py` nếu file này nằm trực tiếp trong thư mục `Lib` của chính CPython runtime (điều này được bảo vệ bằng quyền DACL Read-Only trên thư mục runtime).

## 3. Chống 7 kịch bản tấn công của Adversary và Thực thi Thực tế (R3b, R4b)

Tất cả 7 kịch bản tấn công đều được thực thi native thực sự dưới AppContainer và chạy qua cùng một hàm Oracle chung. Yêu cầu:
- Từng kịch bản tấn công phải tạo marker file (`status_file`) và bắt buộc hoàn thành thực thi bình thường (`exit_code == 0`, `timed_out == False`) trước khi được công nhận là đã kiểm thử (R4b: loại bỏ kiểm tra lỏng `!= 99`, yêu cầu thành công `== 0`).
- Nếu launcher thất bại (timeout, crash, exit_code khác 0), attack KHÔNG được tính là phát hiện thành công.
- Kịch bản ATK-7 (Monkeypatching) thực sự can thiệp và monkeypatch `unittest.TestCase.assertEqual` trong chính tiến trình Worker.

| ID | Kịch bản tấn công | Cơ chế phát hiện và ngăn chặn | Kết quả kiểm chứng |
| --- | --- | --- | --- |
| **ATK-1** | **Tamper Expected**: Worker cố tình sửa đáp án hoặc file manifest trong vault | OS từ chối truy cập (Access Denied do DACL). Hash của manifest không đổi. Artifact do Worker sinh ra chứa kết quả sai bị Oracle đánh FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-2** | **Import Injection**: Worker thả `sitecustomize.py` vào workspace | Worker chạy với `-I -P`, file hook bị bỏ qua, không thể tạo flag; artifact tính toán sai bị Oracle đánh FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-3** | **Config Tampering**: Worker cố tình sửa `runner_config.json` để hạ thấp tiêu chuẩn kiểm tra | OS từ chối truy cập (Access Denied). File config được bảo vệ bởi DACL; artifact sai bị Oracle đánh FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-4** | **Fake PASS stdout**: Worker in chuỗi `PASS` hoặc giả mạo output của pytest/unittest | Oracle không kiểm tra chuỗi stdout của Worker để quyết định PASS mà dựa trên artifact file và nonce; artifact sai bị Oracle đánh FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-5** | **Early Exit 0**: Worker gọi `os._exit(0)` ngay từ đầu nhằm giả mạo quá trình chạy thành công | Oracle kiểm tra file artifact bắt buộc; thiếu file artifact dẫn đến FAIL ngay lập tức. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-6** | **Replay Attack**: Worker gửi lại artifact cũ từ run trước đó | Oracle kiểm tra `nonce` ngẫu nhiên chỉ xuất hiện trong run hiện tại. Mismatch nonce dẫn đến FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |
| **ATK-7** | **Monkeypatching**: Worker monkeypatch `unittest.TestCase.assertEqual` trong process của mình | Worker monkeypatch thành công trong process của nó; tuy nhiên Process Worker tách biệt hoàn toàn với Oracle; monkeypatch không thể ảnh hưởng tới Oracle; artifact sai bị Oracle đánh FAIL. | **EXECUTED (exit 0), DETECTED & REJECTED** |

## 4. Quy tắc Verifier nghiêm ngặt và Đối soát Raw Evidence (R4b, R4c)

Verifier độc lập từ chối báo cáo (exit 1) nếu có bất kỳ vi phạm nào sau đây:
1. Thiếu một trong hai runtime `3.12` hoặc `3.14`, hoặc runtime có status khác `PASS`.
2. Worker `user_sid` không khớp với `principal.user_sid`.
3. Worker `app_container_sid` không khớp với `profile.sid`.
4. Bất kỳ asset nào có `hash_before != hash_after`, hoặc hash không phải chuỗi hex SHA-256 64 ký tự.
5. Danh sách `attacks` bị rỗng hoặc thiếu bất kỳ kịch bản nào trong 7 attacks.
6. Legitimate task có `exit_code` không phải strict int `0`, `timed_out == True`, hoặc có `descendants_active == True`.
7. `manifest_identity_bound == False` hoặc thiếu `manifest_hash`.
8. **Kiểm tra Provenance và Schema typed bắt buộc (R4b, R4c)**:
   - `oracle.expected_result` bắt buộc phải bằng 42 (khớp spec đã tuyên bố).
   - `oracle.candidate_hash` bắt buộc là chuỗi SHA-256 64 hex hợp lệ và khớp `LEGIT_CANDIDATE_HASH`.
   - `oracle.tree_id` bắt buộc phải là canonical tree ID (`TREE-<hash[:16]>`).
   - `oracle.approved_argv_pattern` bắt buộc đúng 4 token `[python.exe, -I, -P, <script>.py]`; `oracle.approved_timeout_seconds` bắt buộc là số dương hữu hạn.
   - Legitimate task `candidate_hash` bắt buộc khớp `oracle.candidate_hash`.
   - `actual_argv` bắt buộc khớp **exact** approved pattern: đúng 4 token, đúng thứ tự `-I -P`, executable/script basename khớp; `-c`, token thừa/thiếu hoặc executable khác đều bị REJECT.
   - `actual_timeout_seconds` bắt buộc khớp `oracle.approved_timeout_seconds` (strict number, không bool).
   - Legitimate task và mọi attack bắt buộc có `exit_code` strict int `0`; `False` không được coi là `0`.
   - Mọi attack trong cả hai runtime bắt buộc có `launch_result` dạng JSON object với `exit_code` strict int `0` và `timed_out is False`.
   - `solution_payload` trong legitimate task mâu thuẫn với `oracle` (`task_id`, `run_id`, `nonce`, `tree_id` hoặc `result != 42`).
   - `active_processes_after_root` bắt buộc strict int; `descendants_active` bắt buộc boolean; `active_processes_after_root > 0` nhưng `descendants_active == False` bị REJECT.
   - Bất kỳ attack nào có `oracle_verdict == PASS` nhưng `oracle_fooled == False` (hoặc ngược lại).
   - Bất kỳ attack nào có `attack_executed == False` hoặc `attack_detected == False`.
   - Object bị dereference (`principal`, `profile`, `oracle`, `runtimes_tested`, `legitimate_task`, `token`, `os_denials.assets`, `same_user_control.assets`, `attacks`, `launch_result`, `solution_payload`, `cleanup`) bắt buộc đúng kiểu; malformed/null trả failure có kiểm soát, không traceback.
9. **Negative control source-copy (A7)**: bản copy source đã gỡ guard manifest phải chấp nhận manifest sai (chứng minh guard là load-bearing), trong khi source hiện tại từ chối; sentinel ngoài fixture không đổi.

## 5. Hệ quả và Giới hạn (Boundaries & NOT PROVEN)

- **Giai đoạn A chỉ là Proof**: Bằng chứng này được thực hiện trên fixture độc lập trong `tools/proofs/sec02_windows.py`. Nó chưa được tích hợp vào `omp_core`, runner chính thức hay vault production (thuộc giai đoạn B).
- **Không áp dụng cho Arbitrary Pytest Execution**: Proof này áp dụng cho mô hình kiểm tra black-box artifact. Việc thực thi arbitrary test suite của bên thứ ba trong cùng môi trường vẫn tiềm ẩn nguy cơ nếu không có sự cô lập hoàn toàn ở cấp OS.
- **Gate G3 vẫn MỞ**: Quyết định này là cơ sở để reviewer đánh giá Giai đoạn A. Chưa đóng Gate G3 và chưa chuyển task sang DONE.
