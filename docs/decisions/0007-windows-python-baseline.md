# ADR-0007: Windows và Python 3.12/3.14

- Ngày: 2026-10-25. Quyết định trực tiếp của chủ dự án.
- Baseline: main 1cbaf61afa70f0b6dd3d5fbcb239e493ca08d639.
- Trạng thái: Accepted về chính sách; triển khai phải có CI exact SHA.

## Quyết định

Windows là nền tảng hỗ trợ duy nhất. Bỏ Linux/Ubuntu và Python 3.10 khỏi CI.
Dùng Python 3.12 ngay làm baseline; .python-version pin minor 3.12 và package
requires-python >=3.12. Không đổi PATH/registry/runtime mặc định toàn máy.
Python 3.14 chỉ thêm vào CI sau kiểm chứng Windows full suite và
package/entrypoint ngoài source; matrix Windows 3.12/3.14 đều bắt buộc.
Native proof được ghi riêng theo host/principal/runtime, không suy từ unit tests.
Không coi phiên bản Python ngoài matrix là đã được kiểm chứng.

## Evidence trước nâng matrix

CPython 3.14.8 cài riêng trong .omp bằng uv --no-bin --no-registry. Baseline
233 tests OK, 112.278 s, exit 0, không skip; parser boundary tests 2/2 OK.
Không cần sửa production/tests hoặc nới assertion. Python 3.12.7 hiện có
giữ nguyên làm interpreter phát triển. Packaging/native proof được kiểm tiếp
trong handoff WINDOWS-PYTHON; CI remote phải gắn exact SHA sau publish.

## Hệ quả và giới hạn

Không xóa code POSIX legacy hoặc sửa lịch sử nghiệm thu Linux/3.10; các nhánh
đó ngoài phạm vi bảo trì/CI. Đây là đổi support policy có chủ đích, không phải
chứng minh cùng kết quả trên Linux. Không thêm runtime dependency.
G3 vẫn mở; SEC-01 chỉ proof fixture, chưa sandbox Worker production.
SEC-02 giai đoạn A được giao riêng, phải review trước phần production.

## Nguồn

- Python 3.14 changes: https://docs.python.org/3.14/whatsnew/3.14.html
- Python Windows runtime: https://www.python.org/downloads/windows/

Không dựa vào message parser từ Python khác để hạ chuẩn tests; giữ typed
error/cause/redaction và chứng minh regression trước mọi thay đổi nếu cần.

## Giới hạn của interpreter kiểm chứng cục bộ

3.14.8 từ uv trong .omp chạy với low integrity S-1-16-4096 trên host này;
whoami và token API cùng xác nhận. Native CreateAppContainerProfile trả
HRESULT 0x80070005 (Access denied), trước khi tạo profile/fixture. Proof
3.12 chạy medium integrity S-1-16-8192 và đạt. Log message pipe-ended từ
helper cũ dùng GetLastError không phải HRESULT gốc; raw HRESULT đã được
probe riêng. Không đổi policy/registry hoặc dùng fallback để vượt giới hạn.

233 tests trên 3.14 vẫn đạt; compatibility CI/package không đồng nghĩa
AppContainer orchestration trên 3.14 đã PROVEN. SEC-02 A phải dùng controller
3.12 với quyền bình thường để kiểm CPython Worker 3.12/3.14 dưới boundary,
hoặc ghi BLOCKED nếu prerequisite không đủ. Không nâng production baseline
sang 3.14 hoặc claim Worker production isolated từ thay đổi matrix.
