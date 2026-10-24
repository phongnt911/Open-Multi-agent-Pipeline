# ADR-0006: Cách ly quyền OS trên Windows bằng AppContainer

- Ngày: 2026-10-24
- Trạng thái: Proposed cho SEC-01 REVIEW; chưa tích hợp production, chưa đóng G3.
- Phạm vi: chọn candidate boundary cho Worker OMP trên Windows.
- Liên quan: [threat model SEC-01](../security/THREAT-MODEL.md),
  [ADR-0004](0004-windows-containment.md) (Job Object chỉ là lifecycle).

## Bối cảnh

MVP hiện chạy Worker và test command bằng cùng tài khoản OS với orchestrator.
Clone, hash, whitelist sau thực thi và Job Object không ngăn Worker đọc/ghi repo
nguồn, runtime, vault, config, audit hoặc credentials. Job Object chỉ kiểm soát
vòng đời process; nó không hạn chế quyền file, network hay credential.

SEC-01 cần chọn một cơ chế Windows native, không cần admin, không cài driver/SDK,
không tạo tài khoản bền vững, có thể chứng minh bằng process thật và fixture
canary trước khi SEC-02/SEC-03 triển khai runner/vault/scope enforcement.

## Quyết định

Chọn **AppContainer (lowbox)** làm candidate boundary cho Worker trên Windows:

1. Mỗi run tạo một AppContainer profile riêng cho user hiện tại bằng
   `CreateAppContainerProfile`; tên profile chứa tiền tố OMP và random suffix.
2. Worker được tạo bằng `CreateProcessW` với
   `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` trỏ tới AppContainer SID,
   `CREATE_SUSPENDED`, `EXTENDED_STARTUPINFO_PRESENT` và environment block
   tường minh.
3. Worker chạy ở low integrity, không capability; chỉ các quyền file được
   DACL grant cho AppContainer SID (hoặc capability SID) mới truy cập được.
4. Job Object riêng chỉ dùng cho lifecycle/cleanup, không được coi là security
   boundary. Việc gắn job trước resume giữ nguyên theo ADR-0004.
5. Setup/launch/cleanup lỗi phải fail-closed. Không fallback sang spawn cùng
   user không giới hạn khi profile/attribute/job không tạo được.
6. Profile và fixture là tài nguyên tạm do run sở hữu; cleanup xóa fixture và
   gọi `DeleteAppContainerProfile`. Không sửa ACL toàn máy, không tạo account.

Đây là quyết định **candidate**, không phải tuyên bố OMP đã sandbox. Việc tích
hợp runner, cài ACL cho tài sản thật, policy network và recovery thuộc SEC-02/
SEC-03 và cần review riêng.

## Nguồn Microsoft chính thức

- AppContainer isolation (credential/file/network/process/window isolation):
  <https://learn.microsoft.com/windows/win32/secauthz/appcontainer-isolation>
- `CreateAppContainerProfile` (profile per-user, per-app; minimum Windows 8 /
  Server 2012):
  <https://learn.microsoft.com/windows/win32/api/userenv/nf-userenv-createappcontainerprofile>
- `GetAppContainerFolderPath`:
  <https://learn.microsoft.com/windows/win32/api/userenv/nf-userenv-getappcontainerfolderpath>
- `DeleteAppContainerProfile`:
  <https://learn.microsoft.com/windows/win32/api/userenv/nf-userenv-deleteappcontainerprofile>
- `SECURITY_CAPABILITIES` (minimum Windows 8 / Server 2012):
  <https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-security_capabilities>
- `UpdateProcThreadAttribute` và
  `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`:
  <https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute>
- `CreateProcessW`, `STARTUPINFOEX`, `CREATE_SUSPENDED`:
  <https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw>
- Mandatory Integrity Control:
  <https://learn.microsoft.com/windows/win32/secauthz/mandatory-integrity-control>

## Minimum OS và môi trường đã thử

- API `CreateAppContainerProfile`, `SECURITY_CAPABILITIES` và AppContainer có
  minimum chính thức **Windows 8 / Windows Server 2012** (desktop apps).
- Proof chỉ chạy trên **Windows 11 IoT Enterprise LTSC build 10.0.26100**,
  AMD64, Python 3.12.7. Đây là bằng chứng runtime duy nhất.
- Sự tồn tại API trên Windows 8 không chứng minh runner/provider/Python thật
  chạy được. Minimum production phải được chốt sau test tương thích; hiện
  không tuyên bố hỗ trợ Windows 8/10.
- Nếu host không tạo được profile/attribute hoặc không launch được Worker,
  proof trả `FAILED`/`UNSUPPORTED` và OMP phải fail-closed; không hạ xuống
  cùng-user unrestricted.

## Alternatives đã cân nhắc

| Alternative | Ưu điểm | Vì sao không chọn cho SEC-01 |
| --- | --- | --- |
| Job Object + `KILL_ON_JOB_CLOSE` | Đã có, mạnh về lifecycle | Không hạn chế file/network/credential; không đổi identity |
| Restricted token / low integrity đơn thuần | Native, nhẹ | Không tạo app identity riêng; ACL user vẫn có thể cho phép; không đủ cho network/credential isolation |
| Tài khoản local riêng cho Worker | Cách ly user rõ hơn | Cần tạo/quản lý account, mật khẩu/profile, quyền admin; resource bền ngoài fixture; ngoài quyền SEC-01 |
| Windows Sandbox / container / Hyper-V | Cách ly mạnh, snapshot | Cần feature/licensing/admin hoặc nested virtualization; nặng, không phù hợp proof tối thiểu/stdlib |
| WDAC/AppLocker/firewall/ACL toàn máy | Policy mạnh | Cần admin/machine-wide policy; task cấm sửa policy toàn máy |
| AppContainer/lowbox | Per-user, không cần admin, có identity riêng, file/network/process isolation theo thiết kế | Chosen; cần chứng minh runtime/ACL/network trước khi production |

## Hệ quả

### Điều đã chứng minh

- Process Windows thật dưới AppContainer có token lowbox: cùng user SID,
  AppContainer SID riêng, low integrity, 0 capability.
- Fixture grant cho AppContainer SID đọc/ghi được; fixture chỉ grant user SID
  bị OS từ chối đọc/ghi, canary hash không đổi.
- Process cùng user không AppContainer đọc/ghi được fixture, nên denial không
  phải lỗi ACL toàn cục.
- Environment block tường minh không mang secret parent.
- Job Object gắn trước resume và kill-on-close hoạt động trong bound.
- Cleanup đường bình thường xóa fixture và profile thành công.

### Giới hạn và prerequisite

- **Chưa tích hợp**: `omp_core` không đổi; runner vẫn cùng-user.
- **Chưa bảo vệ tài sản thật**: proof chỉ dùng fixture tạm; chưa cài ACL cho
  repo nguồn, `.omp`, vault, config, audit.
- **Chưa chứng minh credential isolation**: chỉ có canary giả.
- **Chưa chứng minh network isolation**: chỉ thử loopback; chưa có capability
  policy hay negative proof Internet/LAN/DNS.
- **Chưa chứng minh inherited-handle isolation**: proof dùng
  `bInheritHandles=FALSE`, không kiểm tra handle list/leakage.
- **Chưa chứng minh Worker CLI/Python/provider thật chạy được** dưới
  AppContainer. Python cài trong profile user không tự động truy cập được bởi
  AppContainer; cần grant runtime tối thiểu và test lại. Đây là prerequisite
  của SEC-02, không được bỏ qua bằng fallback.
- **Chưa chứng minh recovery khi owner chết**: cleanup chỉ chạy đường bình
  thường; profile/process orphan sau hard-kill chưa được kiểm.
- **Không chống administrator/SYSTEM/kernel** hoặc process cùng user có quyền
  debug. Đây là giới hạn của mô hình, không phải bug của proof.

### Cleanup và recovery

- Profile name phải unique theo run và nằm trong giới hạn 64 ký tự của API.
- Cleanup phải idempotent: xóa fixture trước, gọi `DeleteAppContainerProfile`
  sau; lỗi xóa profile phải được báo, không che lỗi gốc.
- Không xóa profile của run khác. Không dùng `reset`/`clean` để dọn resource
  ngoài fixture.
- Nếu cleanup lỗi, run phải giữ evidence và trả lỗi; không tuyên bố PASS.

## Trạng thái

Proposed/REVIEW. Việc chấp nhận ADR này không đóng G3 và không cho phép dùng
AppContainer trong production trước khi SEC-02/SEC-03 hoàn tất và có review
độc lập.
