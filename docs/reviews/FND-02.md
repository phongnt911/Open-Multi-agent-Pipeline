# Review FND-02

- Ngày: 2026-10-21
- Reviewer: AI điều phối, độc lập với phiên AI thực thi
- Verdict: **CHANGES_REQUIRED**
- Base HEAD xác nhận: `8f580aa029e8321a7d5520a791e0078e5f8d6210`
- Đối tượng: working-tree diff và file untracked được bàn giao, chưa commit.
- Không sửa code/tests/handoff/task của tác giả; chỉ thêm báo cáo review này.
- Task giữ REVIEW; chưa ACCEPTED/DONE, không đóng G0, không commit/push/merge.

## Phạm vi đã đọc

Đọc toàn bộ main.py, hai test file mới, diff README/CONTRACTS/task và handoff;
đối chiếu task, AGENTS, COORDINATION, CONTRIBUTING, CONTRACTS, SDD và ROADMAP.
Đường gọi kiểm tra: CLI → read/validate → lock/Git → advance → execute →
stop/save → CLI exception rendering. So sánh file cũ với base bằng git diff.
Không có thay đổi tests/test_pipeline.py, pyproject, workflow, checker,
AGENTS hoặc ROADMAP trong phần tác giả bàn giao.

SHA-256 main.py được review:
`88edccfa4b99e7811e19bd88b8c8cea7b076654a801bfd213921843524159bb6`.
Hai test mới:
`test_cli.py: 15e06ab1aa3351d9076a3fd5bcb0d2ce363a11ac2d011ca11f9b026c8dcd9b91`;
`test_errors.py: 5f11ac203a3023c17cb4310fc5d1dc48bc2ae36569385206288e2bf71c87fca4`.
Evidence không tự áp dụng cho diff thay đổi sau review.

## Acceptance mapping

| AC | Kết quả | Evidence reviewer |
| --- | --- | --- |
| AC1 | Đạt ở mức đọc code/introspection | 11 hàm production đều có annotation tham số/return; JsonValue, Config/Task/State, không Any; docstrings có boundary/side effects/errors. Chưa chạy static type checker. |
| AC2 | Chưa đạt | Test hiện có pass nhưng probe NUL argv in traceback; missing executable không thành OperationError; cleanup timeout có thể đưa argv/token vào str(exc). Xem FND02-R1/R2. |
| AC3 | Đạt | 3 InitRunnerTests đọc test_argv từ omp init; zero/pass/fail, mỗi case chạy thường và PYTHONOPTIMIZE=1; reviewer chạy lại 7 CLI tests đạt. |
| AC4 | Đạt, tái hiện độc lập | Đọc runner bằng git show từ base, AST lấy command nguyên bản; chạy trong tests/ rỗng: baseline thường=1, optimize=0 sai; bản mới thường=1, optimize=1. Không sửa baseline source. |
| AC5 | Đạt cho suite hiện hành | 17 tests OK, 8 test cũ không đổi; git diff --check exit 0 trên Windows/Python 3.12.7. |
| AC6 | Đạt trong phạm vi kiểm tra | Diff/status đúng file scope; pyproject không đổi; tests đọc được dùng fake subprocess; không thấy runtime/log/credential mới trong diff. Không thể chứng minh mọi hoạt động lịch sử từ Git status. |
| AC7 | Cần cập nhật sau sửa | Handoff đủ các trường và công khai giới hạn; tuyên bố AC2 đạt và docs mô tả process/error quá rộng so với probe. Cập nhật mapping/docs/evidence sau sửa R1/R2. |

## Findings

### FND02-R1 — P1: Cleanup timeout làm rò argv trong lỗi công khai

- Vị trí: `omp_core/main.py:296–300`; đường xuất lỗi `:429–430`, `:494–495`.
- Trigger: communicate timeout, sau đó stop(process) cũng ném TimeoutExpired
  (process.wait(timeout=10) giữ nguyên process.args). Arg có thể chứa token.
- Tái hiện: fake Popen.communicate ném TimeoutExpired; fake stop ném
  TimeoutExpired(['worker', '--token=REVIEW-FAKE-SECRET'], 10). execute trả lỗi
  có chuỗi fake secret; __cause__ không được gắn tường minh vào timeout gốc.
  Đây là fault injection đường lỗi, không phải kiểm chứng timeout cleanup OS.
- Hậu quả: stop được gọi trước bước sanitization nên lỗi cleanup thoát nguyên
  argv; main in str(exc), advance cũng ghi vào last_error. Không cần traceback
  để token xuất hiện. Test timeout hiện tại chỉ đi qua cleanup thành công.
- Yêu cầu sửa: đảm bảo lỗi công khai và last_error không chứa argv ở cả lỗi
  operation ban đầu lẫn lỗi cleanup; giữ thông tin nguyên nhân nội bộ có chủ
  đích, không đổi cơ chế containment/recovery ngoài scope. Không chỉ redact
  timeout của communicate mà bỏ qua timeout phát sinh khi cleanup.
- Xác minh: regression fault-injection cleanup timeout + token giả; kiểm tra
  stderr CLI, str(exception) công khai và last_error không chứa token/argv;
  cause/context cần đủ để truy nguyên. Giữ 8 test cũ và timeout test hiện có.

### FND02-R2 — P2: Lỗi spawn chưa được chuẩn hóa theo AC2

- Vị trí: `omp_core/main.py:276–283`; exception boundary `:494`.
- Trigger 1: config worker_argv là [python, '-c', 'pass', '\u0000']; validate
  hiện chấp nhận, Popen ném ValueError: embedded null character. Sau thay đổi,
  main chỉ bắt OmpError/OSError/SubprocessError, bỏ ValueError so với baseline.
- Tái hiện CLI thật với repo fixture và argv trên: exit 1 nhưng stderr có
  Traceback, dòng cuối ValueError: embedded null character.
- Trigger 2: executable không tồn tại. Gọi execute với đường dẫn giả trả
  FileNotFoundError, không phải OperationError và không có wrapper/cause.
- Hậu quả: lỗi input dự kiến in traceback, phân loại process chưa đúng contract
  mặc dù normal nonzero exit đã thành OperationError. Hai test classification
  hiện tại chỉ kiểm tra validate và Git, bỏ qua spawn.
- Yêu cầu sửa: validate tối thiểu input không thể spawn hoặc wrap lỗi launch
  phù hợp với InputError/OperationError và giữ cause. Không bắt mọi ValueError
  toàn chương trình để che bug; không mở rộng thành DAT-01 schema framework.
  Message công khai không echo argv hoặc secrets.
- Xác minh: CLI NUL argv nonzero không traceback/secret; direct boundary cho
  missing executable trả OperationError có cause; nonzero/timeout tests cũ đạt.

## Checks reviewer đã thực hiện

Windows 11 build 10.0.26100, Python 3.12.7 (64-bit).

| Lệnh/check | Kết quả | Exit |
| --- | --- | --- |
| python tools/check.py | Source checks; 17 tests OK, không skip | 0 |
| python -m unittest discover -s tests -p "test_cli.py" -v | 7 tests OK | 0 |
| python -m unittest discover -s tests -p "test_errors.py" -v | 2 tests OK | 0 |
| git diff --check | Không lỗi whitespace | 0 |
| Baseline/current runner probe | 4 lần chạy, kết quả theo AC4 | 0 cho script probe |
| NUL argv CLI probe | Exit 1, traceback=True: finding tái hiện | 0 cho script probe |
| Missing executable probe | FileNotFoundError, OperationError=False | 0 cho script probe |
| Cleanup-timeout injection | secret_in_cli_rendering=True | 0 cho script probe |
| Annotation introspection | 11/11 hàm có input/return annotations | 0 |

Các probe chạy trong repo tạm dưới .omp/test-tmp, cleanup sau khi kết thúc.
Git integration tests/probes chạy ngoài sandbox vì giới hạn MSYS đã biết;
không gọi AI thật hoặc dùng secrets thật. Probe báo exit 0 nghĩa script
tái hiện thành công, không có nghĩa các trường hợp lỗi đã đạt acceptance.

Đọc lại run GitHub Actions 39542511418 bằng connector: bốn matrix jobs
Windows/Linux × Python 3.10/3.12 đều completed/success, bao gồm build/install
và smoke. Đây là evidence run baseline do tác giả dẫn, không phải CI của diff.
URL: https://github.com/phongnt911/Open-Multi-agent-Pipeline/actions/runs/39542511418

Chưa chạy CI diff hiện tại, Python 3.10/Linux/macOS local, installed-package
smoke hoặc static type checker cho diff này. Không chạy lại toàn suite dưới
optimize: fixture assert cũ ngoài scope đã được tác giả ghi nhận; 6 tổ hợp
runner mặc định được kiểm tra dưới normal/optimize qua test_cli.

## Quyết định và giao sửa

CHANGES_REQUIRED vì R1/P1 và R2/P2 vi phạm AC2; không chặn bởi việc thiếu CI
đơn thuần. AI thực thi sửa đúng R1/R2 trong file ownership của FND-02, bổ sung
regression và cập nhật handoff/AC mapping/docs, giữ task REVIEW. Không tự sửa
báo cáo review này, không nhận DAT-01, không commit/push/merge.

Reviewer sẽ đọc lại diff và chạy checks cùng regression mới trước quyết định
tiếp theo. G0 vẫn mở; cần CI cho tree được tích hợp và quyền tích hợp riêng.
