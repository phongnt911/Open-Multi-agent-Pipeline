# Handoff Windows / Python baseline — 2026-10-25

- Quyền: chủ dự án yêu cầu bỏ Linux, dùng Python 3.12 ngay, kiểm chứng 3.14
  rồi nâng CI, cập nhật tài liệu/triển khai và giao công việc tiếp theo.
- Owner: AI điều phối; đây là implementation/self-review nền tảng, không
  giả là review độc lập security gate. Không sửa production hoặc tests.
- Base main: 1cbaf61afa70f0b6dd3d5fbcb239e493ca08d639.
- Nhánh triển khai: codex/windows-python312-314.

## Thay đổi

1. Windows-only support/CI; bỏ Ubuntu và Python 3.10 khỏi matrix. Python
   3.12 baseline, 3.14 compatibility lane bắt buộc; không continue-on-error.
2. requires-python >=3.12, .python-version=3.12, Windows/Python classifiers;
   không runtime dependency. Giữ interpreter mặc định 3.12.7, không đổi PATH/
   registry. 3.14.8 uv cài riêng trong .omp để kiểm chứng.
3. Cập nhật AGENTS, contributing, contracts, SDD, README, coordination,
   roadmap, PR/handoff templates và checker docstring; pin LF cho file pin.
   Giữ nguyên evidence/review history Linux/3.10, không xóa code POSIX legacy.
4. ADR-0007 ghi quyết định/giới hạn. SEC-02 READY **chỉ giai đoạn A** theo
   docs/tasks/SEC-02.md; chưa triển khai runner/vault production hoặc SEC-03.

## Scope triển khai

.python-version, .gitattributes, pyproject.toml, .github/workflows/check.yml,
.github/pull_request_template.md, AGENTS.md, README.md, tools/check.py,
docs/CONTRIBUTING.md, docs/COORDINATION.md, docs/CONTRACTS.md, docs/SDD.md,
docs/ROADMAP.md, docs/templates/HANDOFF.md, ADR-0007, task SEC-02, handoff này.
Với file mixed, chỉ chọn thay đổi nền tảng/current status/next task được chỉ
đạo; không nhận task/handoff/review/workspace khác. .review-bh07 và .omp
không thuộc commit scope. Snapshot trước sửa ở .omp/platform-update-preserve.json.

## Evidence local

| Check | Windows/Python | Kết quả |
| --- | --- | --- |
| Parser boundary tests baseline | 3.14.8 | 2 OK, exit 0 |
| python tools/check.py trước sửa policy | 3.14.8 | 233 OK, 112.278 s, 0 skipped, exit 0 |
| python tools/check.py tree cuối | 3.12.7 | 233 OK, 109.040 s, 0 skipped, exit 0 |
| SEC-01 native proof + verifier | 3.12.7 | PASS / ok=true, exits 0 |
| SEC-01 native proof | 3.14.8 | FAILED tạo profile, HRESULT 0x80070005, exit 1; không coi là PASS |
| Source-copy install + pip check | 3.12.7 và 3.14.8 | exits 0; package không runtime dependencies, Requires-Python >=3.12 |
| import site-packages + omp --help ngoài source | 3.12.7 và 3.14.8 | exits 0 |

Không sửa production hoặc nới test để đạt 3.14. Full compatibility CI không
thay native AppContainer proof. 3.14 uv trên host chạy low integrity; whoami
và token API xác nhận S-1-16-4096, khác 3.12 medium S-1-16-8192.
CreateAppContainerProfile bị Access denied trước khi tạo fixture/profile;
không đổi policy hoặc fallback. Helper cũ in stale GetLastError pipe-ended;
raw HRESULT được probe riêng, giữ log thất bại. Không kết luận lỗi language
compatibility từ hạn chế quyền runtime này. ADR/task SEC-02 ghi prerequisite
controller 3.12 với fake Worker 3.12/3.14 và điểm dừng BLOCKED nếu thiếu proof.

Một lần pip --isolated install 3.12 timeout sau 180 s; không dùng làm PASS.
Retry cấu hình network thông thường đạt, dùng Windows Job Object và timeout
90 s/cleanup 10 s để dừng toàn cây. Không kết luận root cause mạng đã được
sửa; không đổi dependency dự án. Các process owned còn lại đã được kiểm tra.

Logs .omp/py314-baseline-*.log, .omp/platform-{312,314}-*.log và JSON proof.
Build/dist/egg-info trong source copy/venv là artifact tạm, không commit hoặc
dùng artifact root có sẵn làm evidence. Source copy ban đầu lấy từ Git index.

## CI và điểm dừng

Chưa có CI exact commit tại lúc soạn handoff này. Sau local pass, push/PR và
merge theo quyền triển khai; ghi exact SHA và từng job Windows 3.12/3.14 trong
PR/evidence. Không dùng CI cũ 3.10/Linux thay matrix mới, không claim native
security proof 3.14 đã PROVEN. G3 vẫn mở. Không chạy provider thật/AI quota.

SEC-02 chỉ nhận giai đoạn A, bàn giao REVIEW và prompt reviewer; B cần review
A trước. Không tự DONE hoặc đóng G3.

Ngoài scope: snapshot phát hiện thư mục scratch .review-bh07 thay đổi/biến mất
trong lúc làm việc; không ghi/xóa/khôi phục hoặc đưa thư mục đó vào commit.
Các file bảo toàn khác ngoài scope khớp snapshot. Chi tiết chỉ lưu .omp,
không coi thay đổi scratch bên ngoài là thay đổi OMP hoặc lý do stage all.
