# ĐẶC TẢ YÊU CẦU PHẦN MỀM (SOFTWARE REQUIREMENTS SPECIFICATION)
**Dự án:** OMP (Open Multi-agent Pipeline)  
**Phiên bản:** 1.0.0  
**Nền tảng mục tiêu:** Windows (PowerShell / Windows Terminal)  
**Ngôn ngữ phát triển:** Python 3.10+  

---

## 1. Giới thiệu & Mục tiêu Dự án

### 1.1. Mục đích
**OMP (Open Multi-agent Pipeline)** là một bộ điều phối đa tác tử (Multi-Agent Orchestrator) và khung kiểm soát an toàn (Harness) chạy cục bộ trên máy tính cá nhân. Hệ thống giúp tự động hóa hoàn toàn quy trình phát triển phần mềm bằng cách điều khiển tuần tự các công cụ AI CLI sẵn có (**Codex Plus**, **Gemini Pro/Flash**, **OpenCode / Oh-My-OpenCode**).

### 1.2. Phạm vi & Ràng buộc Cốt lõi
* **Zero-API Key & Zero-Cloud Hub:** Tận dụng 100% phiên đăng nhập sẵn có (OAuth/Session) của từng công cụ CLI trên máy người dùng; không yêu cầu mua thêm API Key hay đăng ký tài khoản điều phối trên đám mây.
* **Disk-First Memory:** Toàn bộ trạng thái thực thi, kế hoạch, bộ nhớ dài hạn và lịch sử lỗi đều được lưu trữ trực tiếp trên ổ đĩa cứng trong thư mục `.omp/`.
* **Sequential Execution (Tuần tự tuyệt đối):** Chỉ một AI hoạt động tại một thời điểm để triệt tiêu xung đột mã nguồn, xung đột cổng mạng và đảm bảo tính nhất quán của cơ sở dữ liệu.

---

## 2. Phân vai Tác tử (Agent Roles)

Hệ thống phân chia các AI CLI vào 3 tầng vai trò dựa trên năng lực suy luận và hạn ngạch (Quota):

| Vai trò | AI CLI Ưu tiên | AI CLI Dự phòng (Failover) | Trách nhiệm chính |
| :--- | :--- | :--- | :--- |
| **1. Scout (Trinh sát)** | Gemini CLI (Flash) / OpenCode Free | Python AST Local Script | Quét cấu trúc dự án, rút trích chữ ký hàm và đóng gói ngữ cảnh tĩnh vào `.omp/context_pack.md`. |
| **2. Leader / Architect (Kiến trúc sư trưởng)** | Codex Plus | Gemini Pro -> OpenCode (Reasoning Model) | Phân tích yêu cầu, chia nhỏ Task, sinh bộ Test chuẩn, duyệt yêu cầu cài đặt thư viện/DB và phản biện kiến trúc cuối cùng. |
| **3. Worker / Coder (Thợ thực thi)** | OpenCode (Free Models) / Gemini | Gemini Pro -> Codex Plus (khi kịch khung retry) | Nhận từng Task nhỏ trong phiên làm việc mới (Fresh Session), viết code để vượt qua bộ Test của Leader. |

---

## 3. Yêu cầu Chức năng (Functional Requirements)

### FR-01: Điều phối Dây chuyền Tuần tự (Sequential Pipeline)
* Hệ thống thực thi từng công đoạn và từng nhiệm vụ (Task) theo phương thức nối tiếp: AI trước hoàn tất và đóng gói kết quả xuống đĩa thì AI tiếp theo mới được khởi chạy.
* Tuyệt đối không khởi chạy song song nhiều Worker cùng sửa mã nguồn một lúc.

### FR-02: Tự động Chuyển đổi Dự phòng (Leader & Worker Failover)
* Mọi lệnh gọi CLI đều được bọc bộ đếm thời gian chờ (**Timeout**) và bộ phân tích luồng lỗi đầu ra (`stderr`/`stdout`).
* Khi phát hiện CLI hiện tại bị lỗi mạng, treo quá thời gian chờ, hoặc hết hạn ngạch (`429`, `Rate limit`, `Quota exceeded`), hệ thống tự động đánh dấu CLI đó vào trạng thái tạm nghỉ (`cooldown`) và chuyển quyền điều khiển sang CLI dự phòng tiếp theo trong danh sách.
* Tác tử mới đọc trạng thái từ `.omp/state.json` để tiếp tục công việc ngay tại bước bị gián đoạn mà không cần con người can thiệp thủ công.

### FR-03: Cắt Ngữ cảnh & Tối ưu Bộ nhớ đệm (Context Slicing & Prompt Caching)
* **Fresh Session cho Worker:** Mỗi khi giao một `TASK-XX.json` cho Worker, hệ thống khởi tạo một phiên CLI hoàn toàn mới, chỉ truyền mô tả của đúng task đó và các file liên quan trực tiếp (không truyền lịch sử hội thoại cũ).
* **Tối ưu Cache cho Leader:** Giới hạn số lần gọi Leader ở mức tối thiểu (1 lần lập kế hoạch + 1 lần duyệt kiến trúc). Giữ cố định phần đầu của Prompt (`.omp/context_pack.md`) ở cả 2 lần gọi để tận dụng cơ chế Prompt Caching của nền tảng CLI.

### FR-04: Chống Gian lận Kiểm thử (Shadow Test Vault & Single Test Runner)
* Chỉ có **Leader** mới có quyền tạo hoặc chỉnh sửa kịch bản kiểm thử (Test Cases).
* Ngay sau khi Leader sinh Test, hệ thống sao lưu bản gốc vào két sắt `.omp/vault/tests/` kèm mã băm `SHA-256`.
* Sau khi Worker hoàn thành việc viết code, hệ thống **tự động chép đè** toàn bộ file Test từ két sắt ra thư mục làm việc trước khi kích hoạt công cụ kiểm thử duy nhất của hệ thống (`pytest`, `jest`...) để chấm điểm.

### FR-05: Quản lý Môi trường & Cơ sở dữ liệu Nền Tập trung
* Toàn bộ dây chuyền sử dụng chung **1 cơ sở dữ liệu nền tảng nhất quán** (Shared Database).
* Worker bị cấm tự ý chạy lệnh cài đặt thư viện mới hoặc lệnh thay đổi cấu trúc DB (Migration). Nếu cần thiết, Worker phải xuất yêu cầu vào file `.omp/pending_actions.json`.
* Bộ điều phối sẽ chuyển yêu cầu đó cho **Leader** phê duyệt và thực thi tập trung trước khi cho phép Worker chạy tiếp.

### FR-06: Tự động Tài liệu hóa, Ghi nhớ & Đồng bộ Git
* Khi một Task hoặc Tính năng hoàn tất, hệ thống tự động:
  1. Cập nhật tài liệu dự án (`README.md`, `CHANGELOG.md`).
  2. Ghi nhật ký tiến độ vào `.omp/memory/project_progress.md`.
  3. Rút trích bài học từ các lỗi đã sửa vào `.omp/memory/lessons_learned.md`.
  4. Thực thi `git add`, `git commit` và `git push` để lưu vết cho các phiên làm việc tiếp theo.

### FR-07: Giám sát Trực quan (Visual Dashboard & Oh-My-Posh)
* Hiển thị bảng điều khiển trực tiếp trên Terminal (TUI) gồm: tiến độ Task, trạng thái sức khỏe các CLI, và luồng log thời gian thực của AI đang làm việc.
* Xuất chuỗi trạng thái siêu nhẹ để tích hợp trực tiếp lên thanh dấu nhắc lệnh **Oh-My-Posh** trên Windows.

---

## 4. Yêu cầu Phi chức năng (Non-Functional Requirements)

* **NFR-01 (An toàn Hệ điều hành Windows):** Chặn tuyệt đối các lệnh xóa hủy diệt (`Remove-Item -Recurse -Force`, `del /s /q`, `rm -rf`, `format`) hoặc hành vi sửa/xóa file nằm ngoài thư mục dự án. Mọi lệnh xóa file hợp lệ trong dự án đều phải chuyển hướng vào thùng rác tạm `.omp/trash/`.
* **NFR-02 (Khả năng Phục hồi sau Sự cố):** Nếu máy tính tắt đột ngột hoặc mất kết nối, khi khởi động lại lệnh `omp resume`, hệ thống khôi phục chính xác trạng thái đang chạy từ `.omp/state.json`.
* **NFR-03 (Chuẩn Mã nguồn Mở):** Mã nguồn Python tuân thủ chuẩn PEP 8, có chú thích kiểu dữ liệu (Type Hints), dễ dàng đóng gói qua `pyproject.toml` để chia sẻ cho cộng đồng.