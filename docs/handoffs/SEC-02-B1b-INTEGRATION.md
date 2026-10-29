# Kế hoạch Bàn giao Tích hợp SEC-02-B1b (SEC-02-B1b-INTEGRATION)

- **Mã nhiệm vụ:** `SEC-02-B1b-INTEGRATION`
- **Mục tiêu:** Thiết lập quy trình tích hợp cô lập cho Người điều phối (Coordinator) nghiệm thu, tạo commit, mở PR và merge gói thay đổi `SEC-02-B1b`.
- **Trạng thái:** **SẴN SÀNG CHO ĐIỀU PHỐI REVIEW TÍCH HỢP** (Giai đoạn implementation offline đã được review r5 ACCEPTED ngày 2026-10-29).
- **Base commit xác thực:** `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef` (main sau PR #8).
- **Worktree tích hợp:** Đã tồn tại sẵn tại `.omp/sec02-b1b-integration` (không tạo lại, không xóa hoặc reset/stash/clean worktree gốc hay worktree tích hợp).
- **Phạm vi quyền hạn:**
  - AI lập hồ sơ chỉ sở hữu file `docs/handoffs/SEC-02-B1b-INTEGRATION.md`, không tự ý commit, push, PR hay merge.
  - Người dùng (User) đã ủy quyền cho Người điều phối (Coordinator) thực hiện commit, push remote, mở PR và merge **NẾU VÀ CHỈ NẾU** toàn bộ các cổng kiểm chứng được xác minh đạt (`IF verified`).

---

## 1. Bảng Đối Soát Mã Băm SHA-256 Đúng 6 File Được Chấp Thuận

Dữ liệu SHA-256 đối soát từ biên bản audit `.omp/sec02-b1b-r5-audit.json` ngày 2026-10-29 (môi trường Windows 11 Build 26100, CPython 3.12.7):

| File | SHA-256 Checksum (từ Audit r5) | Phân loại | Vai trò & Mục đích |
| --- | --- | --- | --- |
| `omp_core/trusted_layout.py` | `07cb1596b649fd61959f89d31beaf9803a28f96ff2d203c5cb794af9437f254f` | **Implementation** | Layout L2, Win32 DACL/Label, allowlist env, schema manifest `omp.sec02.b1b.manifest.v1`, artifact reader offline chống symlink/junction/TOCTOU qua handle. |
| `tests/test_trusted_layout.py` | `7e7588c94023a0dc54f13131582224c62de2054b74d088aabdb052aeecff3bf3` | **Implementation** | Unit tests, fault injection, negative controls, native AppContainer tests và suite regression bao phủ các findings R1–R8, R2a–R8b, R3b–R8c, R5d, R6c, R8d. |
| `docs/CONTRACTS.md` | `45fbce8dee1294afb946a7f2176d4f1efb689c7b0d83fae2c553a6910223a620` | **Implementation** | Hợp đồng kiến trúc: Đặc tả API, bất biến bảo mật và giới hạn B1b (Lưu ý: chỉ tích hợp phần section B1b vào base). |
| `docs/decisions/0010-offline-trusted-layout.md` | `9c75955b86aa0a6531c95c4f6bf601341ac58246c03065f83e98c46b848749f4` | **Implementation** | Quyết định kiến trúc ADR-0010: Phân tích đánh đổi, threat model, DACL/nhãn Low và reader offline. |
| `docs/tasks/SEC-02-B1b.md` | `8c5fa3ba8926def919bc45a099bd63ba7c0f36bd4ffac8114de67b919b415a4b` | **Process Artifact** | Hồ sơ nhiệm vụ: Nhật ký triển khai, ma trận AC B1b-1–B1b-8, lịch sử xử lý findings qua 5 vòng review. |
| `docs/handoffs/SEC-02-B1b.md` | `8c9530cb276ef163c10452411c4c3a59961440f1ab5d947df8d20f7eb3ec38f5` | **Process Artifact** | Hồ sơ bàn giao implementation: Báo cáo kỹ thuật chuyển giao cho Reviewer độc lập r5. |

### Phân định vai trò:
- **Implementation Files (4 files):** Trực tiếp định hình mã nguồn, kiểm thử và hợp đồng kỹ thuật cho runtime/test, bảo đảm tương thích với baseline MVP hiện hành.
- **Process Artifacts (2 files):** Ghi nhận quá trình phát triển, bằng chứng kiểm thử và nhật ký giải quyết findings phục vụ kiểm toán và truy vết.

---

## 2. Môi Trường Tích Hợp Cô Lập (Isolated Integration Worktree)

- **Vị trí worktree tích hợp:** Thư mục `.omp/sec02-b1b-integration` đã tồn tại sẵn, được liên kết với base commit `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`.
- **Bảo toàn workspace chính:**
  - Workspace chính (`C:/Users/POTA/Documents/GitHub/Open-Multi-agent-Pipeline`) chứa các file dirty thuộc các luồng công việc khác; tuyệt đối không `git reset`, `git clean`, `git stash`, hay `git checkout` trên workspace này.
  - Không xóa, không tạo mới thay thế worktree `.omp/sec02-b1b-integration`.
- **Quy tắc nạp file vào worktree tích hợp:**
  - Sao chép 5 file nguyên vẹn (`omp_core/trusted_layout.py`, `tests/test_trusted_layout.py`, `docs/decisions/0010-offline-trusted-layout.md`, `docs/tasks/SEC-02-B1b.md`, `docs/handoffs/SEC-02-B1b.md`) vào worktree tích hợp và xác nhận mã băm khớp chính xác bảng ở Mục 1.
  - Đối với `docs/CONTRACTS.md`: Chỉ áp dụng duy nhất đoạn section `## Hợp đồng SEC-02-B1b: Layout L2, Trusted Inputs và Offline Reader` (từ heading B1b đến trước heading `Ranh giới module mục tiêu`) vào bản sạch của `docs/CONTRACTS.md` tại base `8e33dad`. Không stage toàn bộ file `docs/CONTRACTS.md` từ workspace chính để tránh lọt sửa đổi của các stream khác.

---

## 3. Các Bước Kiểm Chứng Dự Kiến Trên Exact Commit (Windows Python 3.12)

*(Lưu ý phân biệt: Kết quả r5 trong audit là bằng chứng thực thi đã có; các bước dưới đây là quy trình dự kiến do Người điều phối thực hiện trên commit trong worktree tích hợp).*

### 3.1 Focused Test Suite B1b
```powershell
python -m unittest tests.test_trusted_layout -v
```
- **Tiêu chí:** 64 tests OK, 0 failure, 0 error, 0 skip, exit code 0.

### 3.2 Full Repository Check
```powershell
python tools/check.py
```
- **Tiêu chí:** `Source checks passed; running 521 tests`, 521 tests OK (457 baseline + 64 B1b), 0 skip, exit code 0.

### 3.3 Biên dịch Bytecode
```powershell
python -m py_compile omp_core/trusted_layout.py tests/test_trusted_layout.py
```
- **Tiêu chí:** Exit code 0.

### 3.4 Kiểm tra Định dạng Dòng và Khoảng trắng
```powershell
git diff --check
```
- **Tiêu chí:** Exit code 0, không có trailing whitespace hoặc lỗi định dạng CRLF/LF.

### 3.5 Bất biến Files Cốt Lõi Được Bảo Vệ
```powershell
git diff --exit-code 8e33dad -- omp_core/main.py omp_core/windows_job.py .github tools pyproject.toml AGENTS.md
```
- **Tiêu chí:** Exit code 0.

### 3.6 Đối Soát Toàn Bộ 13 Baseline Test Files (Không Drift)
Không liệt kê danh sách dự đoán; thực hiện đối soát tự động toàn bộ test baseline đã tracked trong `tests/` so với base commit `8e33dad`:
```powershell
git diff --exit-code 8e33dad -- tests/ ":(exclude)tests/test_trusted_layout.py"
```
- **Tiêu chí:** Exit code 0 (toàn bộ 13 file test baseline bất biến so với base `8e33dad`).

### 3.7 Kiểm chứng Reviewer Mutants
Script `.omp/sec02-b1b-r2-run-mutants.py` là artifact uncommitted trong thư mục `.omp` của workspace gốc (không được track trong Git, do đó không mặc định hiện diện trong clean checkout). Trong audit r5, 4/4 mutants đã bị bắt. Người điều phối có thể chạy script này từ thư mục `.omp` gốc nếu cần xác thực lại.

---

## 4. Kiểm Tra Đóng Gói và Smoke Ngoài Source (Packaging & Outside-Source Smoke)

Thực hiện kiểm thử độc lập mà không can thiệp thư mục `TEMP` chung của hệ thống và không cài đặt build tool toàn cục:

1. **Khởi tạo thư mục sở hữu riêng biệt dưới `.omp`:**
   Tạo thư mục dùng một lần mới phân bổ tại `.omp/tmp-smoke-<id>`. Tuyệt đối không xóa hay chiếm dụng các thư mục temp sẵn có.
2. **Khởi tạo môi trường ảo cô lập:**
   ```powershell
   python -m venv .omp/tmp-smoke-<id>/venv
   ```
3. **Cài đặt từ Git Archive với build-system dependencies khai báo sẵn:**
   Xuất archive mã nguồn sạch từ worktree tích hợp và cài đặt vào venv cô lập:
   ```powershell
   git archive HEAD --format=tar.gz -o .omp/tmp-smoke-<id>/source.tar.gz
   .omp/tmp-smoke-<id>/venv/Scripts/pip install .omp/tmp-smoke-<id>/source.tar.gz
   ```
4. **Kiểm tra Import và CLI ngoài source:**
   Dùng đường dẫn tuyệt đối tới `venv/Scripts/python.exe` và `venv/Scripts/omp.exe`; đặt cwd vào thư mục `outside` mới tạo trong disposable root, tách khỏi source được giải nén. Chạy Python với `-I` để import `omp_core.trusted_layout` từ package đã cài, rồi gọi `omp --help` từ cùng cwd. Không dựa vào đường dẫn tương đối sau khi đổi cwd.
- **Tiêu chí:** Cả import và lệnh CLI `omp --help` đều thành công, exit code 0. Dọn dẹp thư mục `.omp/tmp-smoke-<id>` sau khi hoàn tất.

---

## 5. Danh Sách 9 File Được Phép Xuất Bản (Authorized Publication - Exact 9 Paths)

Cam kết tích hợp được cấp quyền xuất bản chỉ bao gồm **đúng 9 file** sau đây:
1. `omp_core/trusted_layout.py` (Implementation module B1b)
2. `tests/test_trusted_layout.py` (Test suite B1b)
3. `docs/decisions/0010-offline-trusted-layout.md` (ADR-0010)
4. `docs/CONTRACTS.md` (Hợp đồng B1b - chỉ section B1b)
5. `docs/tasks/SEC-02-B1b.md` (Hồ sơ task B1b)
6. `docs/handoffs/SEC-02-B1b.md` (Hồ sơ bàn giao implementation B1b)
7. `docs/reviews/SEC-02-B1b-r5.md` (Biên bản review r5)
8. `docs/tasks/SEC-02-B1b-INTEGRATION.md` (Hồ sơ task tích hợp)
9. `docs/handoffs/SEC-02-B1b-INTEGRATION.md` (Tài liệu bàn giao tích hợp này)

Reviewer độc lập kiểm tra `git status --porcelain` và `git diff --staged --name-only` trong worktree tích hợp để đảm bảo không có file thứ 10 nào lọt vào.

---

## 6. Cổng PR CI và Expected-Head Merge Gate

### 6.1 Tạo Commit và Push Nhánh Tích Hợp
Sau review manifest/diff ở Mục 5, tạo commit cục bộ. Sau đó chạy Mục 3 và 4 trên đúng commit sạch; chỉ push khi tất cả đạt. Các lệnh dưới đây là hai bước riêng, không chạy push trước khi đọc kết quả kiểm chứng:
```powershell
git commit -m "feat(sec): layout L2 and trusted inputs offline (SEC-02-B1b)"
git push origin codex/sec-02-b1b-integration
```

### 6.2 Cổng CI GitHub Actions (Bắt buộc CẢ HAI job Windows 3.12 và 3.14)
- Dù phạm vi chấp thuận local của B1b chỉ yêu cầu Windows Python 3.12, pipeline GitHub Actions CI của repository được cấu hình bắt buộc cho cả hai phiên bản.
- **Tiêu chí:** CẢ HAI job CI **Windows Python 3.12** và **Windows Python 3.14** phải báo thành công cuối cùng (`terminal success`) trên exact head ref / PR merge ref. Tổng số test trên CI phải đạt 521 tests OK, 0 skips.

### 6.3 Expected-Head Merge Gate
Trước khi thực hiện merge:
1. **Kiểm tra remote base không đổi:**
   ```powershell
   git fetch origin main
   $REMOTE_MAIN_HEAD = git rev-parse origin/main
   ```
   Bắt buộc `$REMOTE_MAIN_HEAD` phải bằng đúng `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`. Nếu `origin/main` đã có commit mới, dừng ngay việc merge để rebase và kiểm thử lại.
2. **Thực hiện merge an toàn qua API:**
   Khi sử dụng API merge, bắt buộc truyền tham số `expected_head_sha` bằng đúng PR head SHA đã qua review để ngăn chặn race condition tại thời điểm merge.

---

## 7. Xác Minh Cây Thư Mục và CI Sau Merge (Post-Merge Verification)

Sau khi merge hoàn tất vào nhánh `main`:
1. **Fetch không checkout (Không làm bẩn workspace):**
   ```powershell
   git fetch origin main
   ```
   Tuyệt đối không chạy `git checkout main` trong thư mục làm việc chính đang dirty.
2. **So sánh Tree Hash:**
   Đối soát cây thư mục giữa commit được chấp thuận và commit merge trên `main`:
   ```powershell
   git rev-parse origin/main^{tree}
   ```
   Xác nhận merge commit phản ánh đúng các thay đổi của 9 file, không bị ghi đè hay mất mát.
3. **Giám sát CI trên nhánh `main`:**
   Chờ và kiểm tra logs của CẢ HAI job CI trên `main` (Windows Python 3.12 và Windows Python 3.14) hoàn tất thành công (`success`), bao gồm cả bước kiểm tra packaging.

---

## 8. Ranh Giới, Giới Hạn và Các Bước Tiếp Theo

- **Gate G3 tiếp tục MỞ:** Việc tích hợp B1b chỉ đưa các primitive offline vào codebase; Gate G3 **CHƯA ĐÓNG**.
- **Không tự ý chuyển giao scope:** Nhiệm vụ `SEC-02-B1c` (kết nối orchestration/schema) và `SEC-02-B2` (kích hoạt production AppContainer Worker) **CHƯA ĐƯỢC GIAO**.
- **Tính xác thực:** Tài liệu này thiết lập kế hoạch kiểm chứng cụ thể cho Điều phối; các kết quả trong tương lai phải dựa trên logs thực tế, không giả định hay bịa đặt kết quả trước khi chạy.

## Quyết định định dạng khi stage

Git staged diff phát hiện blank line thừa ở EOF của task B1b đầu vào và handoff tích hợp mới. Điều phối chỉ bỏ blank line EOF trong bản tích hợp; không đổi code/tests hoặc nội dung acceptance. Bảng sáu hashes ở trên là bytes đầu vào r5; task tích hợp có SHA-256 riêng trong integration manifest. Workspace gốc và sáu file đầu vào giữ nguyên.
