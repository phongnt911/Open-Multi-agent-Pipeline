# TÀI LIỆU THIẾT KẾ HỆ THỐNG (SYSTEM DESIGN DOCUMENT)

**Dự án:** OMP (Open Multi-agent Pipeline)  
**Phiên bản:** 1.0.0  
**Kiến trúc:** Layered Architecture \& Sequential State Machine

\---

## 1\. Kiến trúc Phân tầng Tổng thể

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ TẦNG 1: GIAO DIỆN \\\& QUAN SÁT (OBSERVABILITY LAYER)                      │
│  ├─ Python Rich TUI Dashboard (Bảng tiến độ Task, CLI Health, Live Log) │
│  └─ Oh-My-Posh Status Exporter (Xuất trạng thái ra .omp/posh\\\_status.txt)│
├─────────────────────────────────────────────────────────────────────────┤
│ TẦNG 2: BỘ ĐIỀU PHỐI TRUNG TÂM (PYTHON ORCHESTRATOR ENGINE)             │
│  ├─ State Machine Controller (Điều khiển dây chuyền tuần tự 8 trạm)     │
│  ├─ Context \\\& Cache Packer (Quét AST tạo Repo Map, cắt Task Context)    │
│  └─ Centralized DB \\\& Env Manager (Duyệt cài đặt thư viện \\\& đồng bộ DB)  │
├─────────────────────────────────────────────────────────────────────────┤
│ TẦNG 3: KHUNG KỶ LUẬT \\\& BẢO VỆ (HARNESS, HOOK \\\& SANDBOX LAYER)          │
│  ├─ Shadow Test Vault (Lưu trữ SHA-256, ghi đè cưỡng bức file Test)     │
│  ├─ PowerShell Safe Profile Hook (Đánh tráo lệnh xóa sang .omp/trash/)  │
│  └─ Automated Test \\\& Git Runner (Chạy Pytest/Jest, Auto Commit \\\& Push)  │
├─────────────────────────────────────────────────────────────────────────┤
│ TẦNG 4: LỚP GIAO TIẾP CLI ĐA TÁC TỬ (STATELESS CLI ADAPTERS)            │
│  ├─ Codex Plus Adapter   (Leader / Architect chính)                     │
│  ├─ Gemini Pro/Flash CLI (Scout \\\& Failover Leader / Worker)             │
│  └─ OpenCode CLI         (Sequential Worker thực thi từng task nhỏ)     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
         \\\[ Ổ ĐĨA CỨNG CỤC BỘ (.omp/ + Mã nguồn Git + Shared DB) ]



2\\. Thiết kế Cấu trúc Dữ liệu trên Đĩa (.omp/ Schemas)

2.1. Cấu hình Hệ thống (.omp/config.json)

2.2. Trạng thái Thời gian thực (.omp/state.json)

2.3. Định dạng Gói Nhiệm vụ (.omp/tasks/TASK-XX.json)

2.4. Định dạng Yêu cầu Cài đặt / DB (.omp/pending\\\_actions.json)

3\\. Thiết kế Máy Trạng thái Tuần tự 8 Bước (Sequential State Machine)

STATE 1 - SCOUTING (Trinh sát): Script AST của Python + CLI scout quét cây thư mục, chữ ký hàm, cấu trúc DB hiện tại và lịch sử .omp/memory/project\\\_progress.md để xuất ra file tĩnh .omp/context\\\_pack.md.



STATE 2 - PLANNING \\\& TEST GEN (Lập kế hoạch \\\& Khóa Test - Gọi Leader Lần 1): CLI leader đọc context\\\_pack.md + yêu cầu người dùng -> Sinh danh sách .omp/tasks/TASK-XX.json và bộ file kiểm thử trong tests/. Ngay lập tức, Harness sao lưu toàn bộ file test vào .omp/vault/tests/ kèm mã băm SHA-256.



STATE 3 - ENV\\\_AND\\\_DB\\\_SYNC (Đồng bộ Môi trường \\\& DB Tập trung): Bộ điều phối thực thi các lệnh cài đặt thư viện và migration do Leader chỉ định trên cơ sở dữ liệu nền tảng chung (shared\\\_database).



STATE 4 - SEQUENTIAL\\\_CODING (Worker thực thi từng Task): Lấy 1 task PENDING có sequence\\\_order nhỏ nhất, mở một phiên CLI worker mới hoàn toàn (bọc trong SafeSandbox.ps1). Nếu Worker phát sinh nhu cầu cài thư viện/DB, Worker ghi vào pending\\\_actions.json và lùi về STATE 3 để xin phép Leader.



STATE 5 - HARNESS\\\_VERIFICATION (Chống gian lận \\\& Chạy Kiểm thử):



Hoàn tác (git checkout --) mọi file bị Worker sửa ngoài danh sách allowed\\\_files\\\_to\\\_modify.



Chép đè toàn bộ file Test gốc từ .omp/vault/tests/ ra thư mục tests/.



Chạy bộ kiểm thử duy nhất (pytest/jest). Nếu Pass -> sang STATE 6. Nếu Fail -> trích xuất 40 dòng lỗi cuối gán vào last\\\_error\\\_trace và lặp lại STATE 4 (tối đa 3 lần trước khi nâng cấp lên CLI cao hơn hoặc gọi Leader xử lý).



STATE 6 - TASK\\\_COMMIT\\\_AND\\\_NEXT (Lưu vết Task): Đánh dấu TASK-XX.json là COMPLETED, tạo git commit cục bộ. Nếu còn task PENDING thì quay lại STATE 4, nếu hết thì chuyển sang STATE 7.



STATE 7 - ARCHITECT\\\_REVIEW (Phản biện Kiến trúc - Gọi Leader Lần 2): Ghép context\\\_pack.md ở đầu Prompt (để trúng Cache của STATE 2) + git diff tổng thể gửi cho leader duyệt. Nếu đạt -> sang STATE 8, nếu chưa đạt -> sinh task sửa lỗi bổ sung và quay lại STATE 4.



STATE 8 - DOCS\\\_UPDATE\\\_AND\\\_GIT\\\_PUSH (Cập nhật Tài liệu \\\& Đẩy Code): Tự động cập nhật tài liệu, ghi nối nhật ký vào .omp/memory/project\\\_progress.md, đúc kết lỗi vào lessons\\\_learned.md và thực thi git push để đồng bộ cho phiên làm việc kế tiếp.



4\\. Thiết kế Các Lớp Bảo vệ Vật lý trên Windows (Defense-in-Depth)

Lớp 1 - PowerShell Safe Profile (SafeSandbox.ps1): Ghi đè các lệnh Remove-Item, del, rm, rmdir, erase trong phiên PowerShell gọi CLI. Chặn mọi truy cập ra ngoài thư mục gốc của dự án và chuyển hướng thao tác xóa hợp lệ vào .omp/trash/. Chặn Worker tự gọi git reset --hard, git push --force, Format-Volume, Stop-Process.



Lớp 2 - File-Scope Whitelisting: Kiểm tra git status --porcelain sau mỗi phiên Worker để thu hồi ngay lập tức các thay đổi trên file không được cấp phép trong TASK-XX.json.



Lớp 3 - Immutable Shadow Test Vault: Đảm bảo 100% bộ kiểm thử chạy thực tế luôn là bản gốc do Leader sinh ra nhờ cơ chế ghi đè cưỡng bức từ két sắt .omp/vault/tests/ ngay trước thời điểm chấm điểm.



5\\. Cấu trúc Mã nguồn Dự án Python (docs/omp-orchestrator)

omp-orchestrator/

├── pyproject.toml                    # Cấu hình đóng gói thư viện Python (dependencies: rich, pydantic)

├── README.md                         # Hướng dẫn sử dụng cho cộng đồng

├── docs/

│   ├── SRS.md                        # Tài liệu Đặc tả Yêu cầu Phần mềm

│   └── SDD.md                        # Tài liệu Thiết kế Hệ thống

├── omp\_core/

│   ├── \_\_init\_\_.py

│   ├── main.py                       # CLI Entrypoint (omp init, omp run, omp resume, omp status)

│   ├── config\_models.py              # Pydantic Schemas cho config, state, task, pending\_actions

│   ├── cli\_wrapper.py                # Bộ bọc tiến trình CLI, bắt lỗi Timeout/Quota \& Failover

│   ├── context\_engine.py             # Bộ quét AST tạo context\_pack.md \& lắp ghép Prompt

│   ├── sandbox\_harness.py            # Két sắt Shadow Test Vault, kiểm tra phạm vi file \& chạy Test

│   ├── env\_controller.py             # Quản lý cơ sở dữ liệu nền \& duyệt cài đặt thư viện

│   ├── memory\_git.py                 # Tự động cập nhật tài liệu, ghi bộ nhớ dài hạn \& Git Push

│   ├── orchestrator.py               # Máy trạng thái điều phối tuần tự 8 bước

│   ├── ui\_dashboard.py               # Giao diện Rich TUI \& bộ xuất trạng thái Oh-My-Posh

│   ├── hooks/

│   │   └── SafeSandbox.ps1           # Script khóa lệnh hủy diệt trên Windows PowerShell

│   └── templates/

│       ├── scout\_context.md

│       ├── leader\_plan\_test.md

│       ├── worker\_execute.md

│       └── leader\_review.md

└── tests/

&#x20;   └── test\_orchestrator.py


