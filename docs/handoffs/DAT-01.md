# Handoff DAT-01

- Trạng thái: DONE (điều phối đóng ngày 2026-10-21 sau ACCEPTED tại `docs/reviews/DAT-01-r3.md`; G1 còn REC-01 và REC-02)
- Owner / revision hoặc diff:
  - Owner: AI thực thi DAT-01 (opencode, DeepSeek V4.1 Flash Free / Antigravity).
  - Base: `main` tại `517f00e092201a08886eb30500cd21e648bc2c4b`.
  - `git status` lúc nhận: 6 file tài liệu điều phối đã sửa chưa commit
    (`README.md`, `docs/COORDINATION.md`, `docs/FND-01.md`,
    `docs/ROADMAP.md`, `docs/handoffs/FND-02.md`, `docs/tasks/FND-02.md`)
    và `docs/tasks/DAT-01.md` chưa track. Đã bảo toàn các thay đổi này;
    không sửa `docs/FND-01.md`, `docs/ROADMAP.md`, `docs/handoffs/FND-02.md`,
    `docs/tasks/FND-02.md`.
  - Revision hiện tại chưa commit; không commit, push, merge hoặc gọi AI thật.
  - Reviewer độc lập đã kết luận **ACCEPTED** tại `docs/reviews/DAT-01-r3.md`.
  - SHA-256 bốn file code/test cốt lõi đã được nghiệm thu và xác nhận:
    - `omp_core/main.py`:
      `e9e493c2f6cf4d06c03ffb6b6563cb5da0b71566f4a0fd71221353d4a25a5d17`
    - `tests/test_validation.py`:
      `1d9bec19808dba8344b577c926501ef778bb1e024527f2acfda8df1ef31d20f9`
    - `tests/test_cli.py`:
      `328a400bebdfc386b61848deba50ed9a03d42dd6b598b09bc0c622523e38f22a`
    - `tests/test_pipeline.py`:
      `eed4b1d34f81c2a25af27c79ea0ad3a68d589185c7afd944844bfa6e066f451a`

## Phân định file cho bước tích hợp

### Files thuộc commit tích hợp DAT-01 (9 files)
1. `omp_core/main.py` (M): Thêm `schema_version` 1, validator config/task/state, strict state shape, legacy v0 normalize trong memory, exact Git OID length, path policy, preflight trước lock và revalidate trong lock, `status` read-only, `init` ghi schema mới, wrap Git invocation `OSError`/`PermissionError` thành `OperationError` có cause.
2. `tests/test_validation.py` (A): 27 unit tests schema/version/path/OID/no-mutation/parser error/Git OS error classification.
3. `tests/test_cli.py` (M): 18 CLI boundary tests, kiểm tra CLI run/resume/status bị chặn trước lock/save/execute, state bytes giữ nguyên khi Git ném PermissionError/OSError.
4. `tests/test_pipeline.py` (M): Cập nhật fixture schema version 1, giữ nguyên 8 hành vi baseline của MVP.
5. `README.md` (M): Tài liệu hóa schema 1, legacy normalization, status read-only, fail-closed Git metadata.
6. `docs/CONTRACTS.md` (M): Bảng đặc tả schema DAT-01, boundary ordering, OID fail-closed, error classification.
7. `examples/task.json` (M): Cập nhật `schema_version: 1`.
8. `docs/tasks/DAT-01.md` (A): Task document, acceptance AC1–AC8.
9. `docs/handoffs/DAT-01.md` (A): Biên bản bàn giao evidence này.

### Files tách riêng, KHÔNG đưa vào commit tích hợp DAT-01
- `Open-Multi-agent-Pipeline.code-workspace`: Cấu hình workspace cá nhân.
- `docs/reviews/DAT-01.md`, `docs/reviews/DAT-01-r2.md`, `docs/reviews/DAT-01-r3.md`: Lịch sử review của điều phối.
- `docs/prompts/DAT-01-review.txt`: Prompt phục vụ chuyển giao giữa các phiên AI.
- `docs/COORDINATION.md`, `docs/templates/HANDOFF.md`: Quy chuẩn điều phối chung của repo (cập nhật quy định prompt handoff).
- `docs/FND-01.md`, `docs/ROADMAP.md`, `docs/handoffs/FND-02.md`, `docs/tasks/FND-02.md`: Thay đổi của các tác vụ FND có sẵn từ trước.

## Hành vi thay đổi và lý do

- Config/task mới dùng `schema_version: 1`; input thiếu version là legacy v0, được normalize thành v1 trong memory, không ghi lại file nguồn. Unknown field, sai kiểu, version lạ, argv rỗng/NUL, timeout ngoài `1..86400` bị `InputError` trước side effect.
- State bắt buộc `schema_version: 1`; required `run_id`, `phase`, `base_commit`, `config`, `task`; optional `child_pid`, `last_error`, `result_commit`. `complete` bắt buộc `result_commit`; nested config/task phải v1. State lỗi là `OperationError`, bytes checkpoint được giữ nguyên.
- `run_id` khớp `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; phase thuộc `preparing|worker|verifying|committing|complete`; commit ID là full hex 40/64 ký tự đúng object format repo; PID là `null` hoặc integer `>0`.
- **R1 & R1a (Git Error Boundary):** `git_object_id_length` chỉ trả 40/64 khi Git trả `sha1`/`sha256`. Thiếu file Git (`FileNotFoundError`), hệ điều hành từ chối chạy (`PermissionError`/`OSError`), Git trả lỗi (`CalledProcessError`) hoặc không hỗ trợ option đều wrap thành `OperationError` có cause gốc (`from exc`) để chặn trước lock/mutation/Worker; không đoán mò SHA-1. Message an toàn `Git command could not be executed: <class>`, không echo `argv`.
- **R2 (Parser Boundary):** `read` phân biệt `JSONDecodeError`, `ValueError` (integer vượt giới hạn parser) và `RecursionError` (JSON lồng quá sâu), chuyển thành `InputError`/`OperationError`, giữ `__cause__`, không echo payload.
- **R3 (Fixture Isolation):** Fixture kiểm tra cấu hình dùng thư mục tạm cô lập, có sentinel guard xác nhận không ghi đè dữ liệu ngoài vùng kiểm soát.
- Allowed path từ chối empty segment, `.`/`..`, backslash, drive/UNC/ADS, glob, NUL và segment `.git`/`.omp` case-insensitive; space/Unicode hợp lệ.
- `run`/`resume` validate config/task/state trước lock và revalidate dữ liệu authoritative trong lock trước clone/Worker/test/checkpoint mutation. `status` chỉ validate và in, không ghi state. `advance` validate state trước khi tạo run directory hoặc gọi process/Git mutation.

## Tiêu chí nghiệm thu (Acceptance Mapping)

- AC1 đạt: Validators có type hints, không side effects; field/version rules tài liệu hóa đầy đủ trong `docs/CONTRACTS.md`, `README.md`.
- AC2 đạt: 27 validation unit tests; kiểm tra đầy đủ missing/invalid/unsupported, parser limits và Git error classification.
- AC3 đạt: State lỗi và Git OS error chặn CLI run/resume/status trước lock/save/execute; checkpoint giữ nguyên bytes, không tạo lock/runs.
- AC4 đạt: Legacy config/task chuẩn hóa trong memory, source bytes không đổi; state v1 bắt buộc; `status` read-only.
- AC5 đạt: Path traversal và case-insensitive `.GIT`/`.OMP` bị từ chối; repo SHA-256 thật đạt; Git metadata fail-closed, không fallback.
- AC6 đạt: Toàn bộ 23 test baseline FND-02 giữ nguyên hành vi và redaction/cause chain.
- AC7 đạt: Regression tests fail trước sửa và pass sau sửa; full suite 59 tests đạt `OK`; `git diff --check` sạch sẽ (exit 0).
- AC8 đạt: Bàn giao đầy đủ base SHA, hash 4 file, danh sách file tích hợp, lệnh test, packaging smoke evidence, và rủi ro/checks còn thiếu.

## Bằng chứng kiểm thử và Packaging Smoke Test

### 1. Unit & Integration Test Suite
- Lệnh: `python tools/check.py`
  - Kết quả: `Source checks passed; running 59 tests`, `Ran 59 tests in 18.038s`, `OK`, exit code: `0`.
  - Phân rã: 27 validation + 18 CLI + 6 errors + 8 pipeline.
- Kiểm tra cú pháp/whitespace: `git diff --check` -> exit code: `0`.
- Kiểm tra file bất biến: `git diff --exit-code 517f00e -- tests/test_errors.py pyproject.toml .github tools AGENTS.md` -> exit code: `0`.
- Flake Windows đã ghi nhận: Đôi khi fixture cleanup `.gitignore` gặp `WinError 32` do OS chưa kịp nhả file handle; chạy tuần tự suite đạt 100%.

### 2. Packaging & Isolated Entrypoint Smoke Test
- Môi trường: Windows 11 IoT Enterprise LTSC (`10.0.26100.0`), Python 3.12.7, Git 2.56.0.windows.1.
- Quy trình kiểm chứng:
  1. Sao chép source code sạch (`omp_core/`, `pyproject.toml`, `README.md`) sang thư mục tạm độc lập `.omp/test-tmp/smoke-.../source`.
  2. Tạo môi trường ảo riêng biệt: `python -m venv <smoke_root>/venv` -> exit code: `0`.
  3. Cài đặt package từ source copy: `<venv>/Scripts/pip.exe install <smoke_source>` -> exit code: `0`, cài đặt thành công `omp-orchestrator-0.1.0` và sinh entrypoint executable `omp.exe`.
  4. Chạy smoke entrypoint từ thư mục hoàn toàn nằm ngoài source repo:
     - `<venv>/Scripts/omp.exe --help` -> exit code: `0`, in hướng dẫn sử dụng và các lệnh `init`, `run`, `resume`, `status`.
     - `<venv>/Scripts/omp.exe init` trong một repo Git tạm riêng -> exit code: `0`, tạo `.omp/config.json` hợp lệ với `schema_version: 1`.
     - `<venv>/Scripts/omp.exe status` khi chưa có state -> exit code: `1`, báo lỗi phân loại `OMP: Cannot read JSON...` sạch sẽ, không traceback.
  5. Dọn dẹp thư mục tạm thành công.

## Kiểm thử chưa chạy và nguyên nhân

- Chưa chạy CI matrix (Windows/Linux × Python 3.10/3.12): Cần tạo commit/PR trên GitHub để CI tự động kích hoạt; AI thực thi không có quyền tự kích hoạt pipeline remote.
- Chưa chạy static type checker toàn diện (`mypy`/`pyright`): Scope task DAT-01 tập trung vào runtime validation và type hints mã mới; việc cấu hình mypy toàn dự án được theo dõi tại FND-02.
- Chưa kiểm tra crash-kill vật lý, ngắt điện đột ngột, Windows Job Object, và sandbox OS cấp tiến trình: Các nội dung này thuộc phạm vi của các tác vụ phục hồi và bảo mật kế tiếp (REC-01, SEC-03).

## Rủi ro và ghi nhận

- Git phiên bản cũ không hỗ trợ `--show-object-format` sẽ bị từ chối hoạt động (fail-closed) thay vì chạy với giả định SHA-1; người dùng cần sử dụng Git hỗ trợ tùy chọn này.
- Validation đường dẫn ngăn chặn directory traversal và segment đặc biệt ở tầng chuỗi đầu vào MVP, không thay thế cơ chế sandbox cách ly OS.
- PID lưu trong state là số nguyên dương hợp lệ, không chứng minh quyền sở hữu tiến trình trên OS nếu xảy ra PID recycling.

## Bước tiếp theo

- Chủ dự án thực hiện bước tích hợp: Stage chính xác 9 file thuộc phạm vi DAT-01 nêu trên và tạo commit theo quy chuẩn Conventional Commits (`feat(data): validate and version boundary data (DAT-01)`).
- AI điều phối cập nhật trạng thái roadmap và giao tác vụ tiếp theo `REC-01` theo kế hoạch.

## Xác nhận và quyết định cuối của điều phối

Ngày 2026-10-21: đã xác nhận HEAD vẫn `517f00e092201a08886eb30500cd21e648bc2c4b`
và bốn SHA-256 trên khớp review r3 trước/sau packaging. Không review lại code
đã ACCEPTED, không sửa production/tests hoặc các báo cáo review lịch sử.

- Evidence reviewer độc lập gần nhất: `python tools/check.py` 59/59 tests OK,
  exit 0 trên Windows/Python 3.12.7, code/test giữ nguyên hash. Không chạy lặp
  full tests chỉ vì cập nhật trạng thái tài liệu.
- `git diff --check` và protected-file diff check chạy lại đạt exit 0.
- Điều phối tự kiểm chứng lại từ source copy ngoài repo và venv riêng tại
  `C:\Users\POTA\.codex\visualizations\2026\10\21\01a15c40-cc19-7b63-871a-11e5ac0114a0\omp-dat01-package-ioll2zj6`.
  Evidence JSON và logs nằm trong thư mục đó, không thuộc file tích hợp.
- `python -m venv <root>/venv`: 0; venv python `-m pip install --no-deps
  <root>/source`: 0; `pip check`: 0. Build isolation chỉ dùng dependency build
  đã khai báo, không thêm runtime dependency.
- `python -I -c "import omp_core; print(omp_core.__file__)"` từ external cwd:
  exit 0, trỏ site-packages venv; bytes/hash installed main.py khớp source.
- Installed `omp --help`: 0; `omp init` trong Git fixture ngoài source: 0,
  config.schema_version=1 và test_argv trỏ Python venv; `omp status` khi chưa
  có state: 1 đúng kỳ vọng, không traceback. Không gọi Worker/AI thật.
- Chốt đúng 9 file DAT-01 đã liệt kê. ROADMAP, task REC-01 và cập nhật điều
  phối mới là nhóm tài liệu quản lý riêng; workspace cá nhân, prompt, FND và
  review history vẫn tách riêng, không tự stage chung.

Quyết định: DAT-01 DONE về nội dung đã nghiệm thu, giao REC-01 READY. Không
commit/push/merge ở lượt này. CI của DAT-01 chưa có, không dùng CI baseline làm
bằng chứng thay thế. G1 vẫn mở, chỉ đóng sau REC-01/REC-02 và evidence tích hợp
cuối cùng. Các nhận xét chưa nghiệm thu/REVIEW cũ là lịch sử bàn giao.
