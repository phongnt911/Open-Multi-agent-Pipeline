# ADR-0004: Containment cây tiến trình trên Windows bằng Job Object

- Ngày: 2026-10-23
- Trạng thái: Proposed cho PROC-02 REVIEW; chưa phải gate bảo mật G2/G3.
- Phạm vi: process lifecycle của Worker và test command trên Windows.

## Bối cảnh

`subprocess.Popen` + `taskkill /T /F` không đủ cho một run OMP:

- Popen trả về sau khi process đã chạy. Nếu owner chết trước khi ghi
  `child_pid`, không có cơ chế OS nào giữ toàn bộ cây tiến trình.
- Tạo process rồi mới `AssignProcessToJobObject` để lại khoảng trống
  create/attach. Owner chết trong khoảng trống có thể để process treo hoặc
  process con không được quản lý.
- `taskkill` là best-effort, không chứng minh job rỗng, và có thể không dừng
  kịp con/cháu trước khi verification/complete.

Yêu cầu PROC-02 là containment thực sự trên Windows, không chỉ giảm cửa sổ
race bằng sleep/retry.

## Nguồn API chính thức

- Job Objects: <https://learn.microsoft.com/windows/win32/procthread/job-objects>
- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` và
  `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`:
  <https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information>
- `UpdateProcThreadAttribute` /
  `PROC_THREAD_ATTRIBUTE_JOB_LIST`:
  <https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute>
- `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`:
  <https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute>
- `CreateProcessW`, `STARTUPINFOEX`, `CREATE_SUSPENDED`:
  <https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw>
- `IsProcessInJob`:
  <https://learn.microsoft.com/windows/win32/api/jobapi/nf-jobapi-isprocessinjob>
- `TerminateJobObject`:
  <https://learn.microsoft.com/windows/win32/api/jobapi/nf-jobapi-terminatejobobject>

`PROC_THREAD_ATTRIBUTE_JOB_LIST` cần Windows 10 / Windows Server 2016 trở lên.
Nested jobs có từ Windows 8 nhưng không đủ cho implementation này vì job-list
attribute là bắt buộc. Nếu Windows trả lỗi khi tạo/đính attribute, OMP
fail-closed; không fallback sang spawn không containment.

## Quyết định

Mỗi Worker/test command trên Windows có một Job Object riêng:

1. `CreateJobObjectW` tạo job không kế thừa handle.
2. Đặt `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Không đặt
   `JOB_OBJECT_LIMIT_BREAKAWAY_OK` hay `SILENT_BREAKAWAY_OK`.
3. Tạo process bằng `CreateProcessW` với:
   - `CREATE_SUSPENDED`;
   - `EXTENDED_STARTUPINFO_PRESENT`;
   - `PROC_THREAD_ATTRIBUTE_JOB_LIST` trỏ tới job;
   - `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` chỉ gồm stdin/log;
   - `CREATE_UNICODE_ENVIRONMENT`.
   Nhờ job list, process vào job ngay tại thời điểm tạo; không có bước
   create rồi attach.
4. Kiểm tra `IsProcessInJob` trước khi cho process chạy. Nếu false, terminate
   process, đóng job và fail-closed.
5. Ghi `child_pid` vào checkpoint và `save()` thành công **trước**
   `ResumeThread`. Nếu save lỗi, job bị terminate khi process vẫn suspended;
   Worker chưa chạy một dòng code nào.
6. `ResumeThread`; nếu resume lỗi, terminate job và fail-closed.
7. Khi root exit, query `ActiveProcesses` của job. Nếu còn con/cháu, terminate
   job và trả operation error; không coi root exit code là complete. Chỉ khi
   job đã rỗng mới được xóa `child_pid`.
8. Timeout, Ctrl+C, operation error: `TerminateJobObject`, chờ có giới hạn đến
   khi `ActiveProcesses == 0` (mặc định 10 giây, poll 10 ms). Nếu hết hạn, giữ
   `child_pid`, trả cleanup error và fail-closed.
9. Đóng process/thread handles; đóng job handle sau khi đã chứng minh rỗng.
   Job handle không được kế thừa bởi Worker.

## Mô hình handle và owner death

Job handle do owner (OMP orchestrator) giữ, không inherit. Khi owner bị
`TerminateProcess` hoặc crash, Windows đóng handle cuối cùng; với
`KILL_ON_JOB_CLOSE`, toàn bộ process còn trong job bị OS kết thúc, kể cả process
đang suspended. Đây là lý do không cần cleanup code chạy trong owner để xử lý
hard-kill.

Nếu owner chết:

| Thời điểm owner chết | Trạng thái Worker | Kết quả mong đợi |
| --- | --- | --- |
| Trước/sau khi tạo job, trước `CreateProcessW` | Chưa có process | Không có process |
| Trong `CreateProcessW`/sau tạo trước save | Suspended, đã ở job | Job handle đóng, process bị kill; không chạy code |
| Sau save trước resume | Suspended, đã ở job | Process bị kill; checkpoint còn `child_pid` để chặn resume |
| Sau resume | Running cùng con/cháu | Cả cây bị kill |

## Ma trận lỗi

| Điểm lỗi | Process còn chạy? | Hành vi |
| --- | --- | --- |
| Tạo/cấu hình job lỗi | Không | `OperationError`; không fallback Popen |
| Tạo attribute list/job list/handle list lỗi | Không | `OperationError`; không fallback |
| `CreateProcessW` lỗi (thiếu executable, argv NUL, ...) | Không | `OperationError`, cause là lỗi OS/ValueError, không echo argv |
| `IsProcessInJob` false | Có, suspended | Terminate process, đóng job, fail-closed |
| Save `child_pid` lỗi | Có, suspended | Terminate job; Worker không chạy; giữ/không ghi complete |
| `ResumeThread` lỗi | Có, suspended | Terminate job; fail-closed |
| Timeout/Ctrl+C | Có | Terminate job, chờ rỗng có giới hạn |
| Root exit còn descendant | Có | Terminate job, operation error, không complete |
| Cleanup hết hạn | Không chứng minh được | Giữ `child_pid`, cleanup error, không spawn thay thế |
| Breakaway của Worker | Không được phép | Job không bật breakaway; test native xác nhận bị từ chối |

## Hệ quả và giới hạn

- Mọi process con/cháu được tạo bởi Worker/test nằm trong job, trừ khi OS cho
  phép breakaway. Job này không bật breakaway; test với
  `CREATE_BREAKAWAY_FROM_JOB` nhận WinError 5.
- Worker cố ý tạo daemon rồi exit 0 sẽ bị coi là lỗi operation và daemon bị
  terminate. Đây là fail-closed có chủ đích.
- Cleanup có bound 10 giây. Hết bound thì `child_pid` vẫn còn; người vận hành
  phải xử lý theo PROC-03, không tự sửa state.
- Job Object chỉ kiểm soát vòng đời process. Nó **không** phải sandbox bảo mật:
  không hạn chế quyền file/network/credential/memory, không đổi user, không
  bảo vệ repo nguồn. G2/G3 vẫn mở.
- Không tự động xóa `child_pid`, không spawn Worker thay thế, không tự
  resume/complete sau cleanup lỗi.
- Không thay đổi POSIX: process group + `start_new_session` giữ nguyên baseline.
- Minimum implementation là Windows 10 / Windows Server 2016+ (job-list
  attribute); môi trường thử nghiệm là Windows 11 64-bit. Nested jobs có từ
  Windows 8 nhưng không thay thế yêu cầu job-list. Hành vi trên Windows cũ hơn
  là fail-closed, không phải fallback.
- stdin của Worker được ghi vào file tạm riêng dưới run directory trước khi
  resume; file được xóa best-effort sau khi process tree dừng. Prompt không đi
  vào command line hay exception.
- Test cũ `test_process_lock` thay đổi kỳ vọng hard-kill: Worker bị containment
  dừng, nhưng `child_pid` vẫn chặn resume cho đến khi được xử lý. Đây là thay
  đổi hành vi có chủ đích của PROC-02, không phải nới contract.
