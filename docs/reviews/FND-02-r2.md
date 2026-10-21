# Review lại FND-02 — vòng r2

- Ngày: 2026-10-21
- Reviewer: AI điều phối, độc lập với AI thực thi
- Verdict: **ACCEPTED** cho revision working tree có hash dưới đây.
- Base HEAD xác nhận: `8f580aa029e8321a7d5520a791e0078e5f8d6210`.
- Không sửa code, tests, task, handoff hoặc báo cáo review cũ.
- Task giữ REVIEW theo yêu cầu chủ dự án; không tự ghi DONE, không đóng G0.
- Không commit/push/merge; không nhận DAT-01 hoặc chạy AI thật/agent phụ.

## Identity đã kiểm tra

Ba hash SHA-256 thực tế khớp hoàn toàn yêu cầu và handoff:

| File | SHA-256 |
| --- | --- |
| omp_core/main.py | 95c83179c04fbf7d23ae53745f0fc2a76e2b68176d0bcf3699a56de3e22c5503 |
| tests/test_cli.py | f0a16d3bfa31236c46c9f78a548e99902c6f32add08c50c623249e149f8a70a7 |
| tests/test_errors.py | ccfd3b9788f51aba541b41755b277f9a229c76bf9a6ae066c118f089dec40912 |

Đọc toàn bộ code, hai test file, task/handoff, README/CONTRACTS, AGENTS,
COORDINATION và review cũ. Đọc đường execute → stop → exception conversion →
advance.last_error → CLI rendering. Không dựa riêng vào kết luận handoff.
Kết luận này không tự áp dụng cho revision thay đổi sau các hash trên.

## Findings và việc khép lại R1/R2

Không còn finding chặn nghiệm thu trong phạm vi FND-02 được yêu cầu.

### FND02-R1 — đã xử lý

Vị trí đã kiểm tra: main.py:229, :240, :270, :277, :331–362 và :483–485,
đường in lỗi CLI :549–550.

- Timeout operation và cleanup được dựng lại chỉ với label/timeout.
- Lỗi được raise ngoài except đang xử lý raw timeout; không để raw argv lọt
  vào cause/context của exception công khai ở đường được yêu cầu.
- Cleanup timeout có cause là operation timeout đã sanitize, vẫn truy được
  `worker` 2 giây và `worker cleanup` 10 giây; checkpoint giữ phase `worker`.
- Khi fake process vẫn sống, child_pid được giữ lại, không tự cho resume.
- Các OS command stop, process.wait và logic checkpoint/transition vẫn theo
  cơ chế MVP; không thêm containment/recovery mới.

Probe độc lập của reviewer, không gọi test method của tác giả:

1. Fake communicate và wait ném TimeoutExpired chứa token giả ở cmd,
   output/stderr. Đi qua execute và stop thật; chỉ mock thao tác OS dừng PID.
2. Duyệt cả hai nhánh cause và context bằng graph traversal có visited set,
   kiểm tra str và repr của mọi exception node, không chỉ chọn một nhánh.
3. Timeout đơn: một node sạch. Timeout kép: hai node sạch, cause/timeout/label
   đủ truy vết, không còn token hoặc `--token=` trong exception graph.
4. Chạy main resume với checkpoint fixture và fault injection tương tự:
   exit 1, stderr không traceback/token/argv; last_error an toàn, phase còn
   worker, PID còn giữ. Không giả vờ đây là test kill OS thật.

### FND02-R2 — đã xử lý

Vị trí đã kiểm tra: main.py:204–211 và :313–329.

- NUL trong argv bị validate từ chối bằng InputError, không echo giá trị.
- Popen boundary wrap OSError/ValueError bằng OperationError và giữ cause.
- Không thêm catch ValueError toàn CLI để che lỗi không liên quan.
- Probe CLI thật qua `run` với config có NUL/token giả: exit 1, không
  traceback/token, chưa tạo state.json.
- Probe execute thật với executable thiếu: OperationError có cause
  FileNotFoundError. Không gọi AI hoặc mở rộng schema DAT-01.

## Acceptance mapping sau sửa

| AC | Kết quả | Evidence reviewer |
| --- | --- | --- |
| AC1 | Đạt trong phạm vi đọc code/introspection | Mọi hàm production, kể cả ba helper mới, có input/return annotations; JSON aliases/TypedDict thay Any tràn lan; boundary docstrings mô tả side effects/lỗi. Không tuyên bố static type checker đã đạt. |
| AC2 | Đạt cho yêu cầu FND-02/R1/R2 | 9 CLI tests, 6 error tests đạt; probes riêng xác nhận NUL/missing executable và timeout kép ở CLI/checkpoint/exception graph. Redaction toàn diện cho mọi lỗi khác vẫn ngoài scope. |
| AC3 | Đạt | InitRunnerTests lấy test_argv từ omp init; zero/pass/fail ở normal/optimize, tất cả đạt khi reviewer chạy lại. |
| AC4 | Đạt, kiểm tra lại độc lập | Dùng git show base và AST lấy command nguyên bản, không sửa baseline: empty tests normal=1, optimize=0; bản mới normal=1, optimize=1. |
| AC5 | Đạt | 23 tests OK gồm nguyên 8 regression cũ, không skip; git diff với base xác nhận test_pipeline.py không đổi; git diff --check exit 0. |
| AC6 | Đạt trong phạm vi kiểm tra | File scope phù hợp; workflow/checker/dependency/schema và cơ chế recovery không bị đổi; fake providers; không có runtime/credential mới trong diff. Không suy ra toàn bộ lịch sử hoạt động từ status. |
| AC7 | Đạt | README/CONTRACTS/task/handoff khớp các hành vi được probe và công khai CI/platform/packaging chưa chạy. Task vẫn REVIEW. |

## Checks đã chạy lại

Môi trường: Windows 11 build 10.0.26100, Python 3.12.7, 64-bit.

| Lệnh/check | Kết quả | Exit |
| --- | --- | --- |
| python tools/check.py | Source checks + 23 tests OK; không skip | 0 |
| python -m unittest discover -s tests -p "test_cli.py" -v | 9 tests OK | 0 |
| python -m unittest discover -s tests -p "test_errors.py" -v | 6 tests OK | 0 |
| git diff --check | Đạt | 0 |
| git diff --exit-code 8f580aa -- tests/test_pipeline.py .github tools pyproject.toml AGENTS.md docs/ROADMAP.md | Không có thay đổi | 0 |
| Script probe độc lập R1/R2 + zero-tests + annotations | Mọi assertion đạt | 0 |

Probe tạo repo/checkpoint tạm trong .omp/test-tmp rồi cleanup. Lượt Git và
tempfile integration được chạy ngoài sandbox phiên do hạn chế MSYS/tempfile
Windows đã biết; không truy cập mạng, secrets thật hoặc provider AI.
Exit 0 của probe là script xác minh đạt các assertion, không phải CLI lỗi trả 0.

## Check chưa chạy và rủi ro còn lại

- Chưa có CI cho working-tree diff chưa commit/push này, chưa có integration
  evidence cho tree cuối, chưa chạy Python 3.10/Linux/macOS local hoặc build/
  installed-package smoke lại cho diff này.
- Chưa chạy static type checker; annotations không đồng nghĩa kiểu đã được
  kiểm chứng hoàn toàn bằng mypy/pyright.
- Fault injection không chứng minh containment Windows, kill descendants
  hoặc chịu crash/mất điện. Các hạng mục PROC/REC vẫn giữ nguyên.
- Chưa có redaction framework cho mọi exception/side effect; không mở rộng
  ACCEPTED thành cam kết bảo mật toàn hệ thống. Exception traceback locals và
  file log provider không thuộc phép kiểm tra str/cause/context của R1.
- Full suite dưới optimize chưa được chạy lại; fixture assert cũ đã ghi nhận
  ở handoff, không đổi 8 test baseline để mở scope. Runner mặc định đã được
  kiểm tra normal/optimize bằng đúng argv sinh từ init.
- Schema và raw I/O/checkpoint error handling toàn diện vẫn thuộc task sau;
  PermissionError thoáng qua tác giả nêu không xuất hiện trong lượt review này.

## Quyết định và bước tiếp theo

ACCEPTED cho nội dung FND-02 trên đúng revision đã nhận, R1/R2 đã khép lại.
Giữ task REVIEW theo yêu cầu hiện tại, không tự ghi DONE. G0 vẫn mở đến khi có
CI/integration evidence cho tree cuối và quyết định nghiệm thu/tích hợp riêng.
Không mở DAT-01. Bước kế tiếp chỉ chuẩn bị evidence tích hợp/packaging và danh
sách file, không sửa production hoặc tự push/merge.
