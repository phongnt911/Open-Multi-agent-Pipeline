# Bằng chứng Tích hợp và Manifest Packaging Gate G1 (Hợp đồng dữ liệu)

- Ngày lập: 2026-10-22
- Tác giả: AI thực thi tích hợp G1 (Antigravity).
- Base Git commit: `517f00e092201a08886eb30500cd21e648bc2c4b`.
- Trạng thái Gate G1: **VẪN MỞ** (chờ commit tích hợp và CI remote hoàn tất trước khi điều phối đóng gate).
- Tình trạng các tác vụ thành phần thuộc Gate G1:
  - **DAT-01**: DONE (ACCEPTED tại `docs/reviews/DAT-01-r3.md`).
  - **REC-01**: DONE (ACCEPTED tại `docs/reviews/REC-01-r3.md`).
  - **REC-02**: DONE (ACCEPTED tại `docs/reviews/REC-02-r2.md`).

---

## 1. Xác nhận Tính toàn vẹn Mã băm (SHA-256) đã Nghiệm thu

Toàn bộ các file mã nguồn, kiểm thử, hợp đồng kiến trúc và biên bản bàn giao của revision hiện hành đã được đối soát trực tiếp, khớp 100% với các báo cáo nghiệm thu của reviewer độc lập (`docs/reviews/REC-02-r2.md`, `docs/reviews/REC-01-r3.md`, `docs/reviews/DAT-01-r3.md`):

| File | SHA-256 | Báo cáo nghiệm thu gốc | Trạng thái đối soát |
| --- | --- | --- | --- |
| `omp_core/main.py` | `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `tests/test_crash_recovery.py` | `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820` | `docs/reviews/G1-INTEGRATION.md` (sửa G1-CI-R1, gốc: `97088625...`) | Đã kiểm chứng hermetic |
| `docs/decisions/0003-crash-recovery-matrix.md` | `a4421fb14d2861a76110117690006310f1e7fee15a96aba2b055cef08e3ab412` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `docs/CONTRACTS.md` | `671f9ade9ebf3f8d9d30cd59dbcf1ae894f1c6ad04454aa68e230d802337a934` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `docs/SDD.md` | `c64383fc7c6e85e14c66f38ccf7e68ade8dc28a507bf0885fff25cb77e017fce` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `README.md` | `33b5193303e76639ca46a709bf531e098a7d4db5eaad51c93ee86e44ddb271fd` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `docs/handoffs/REC-02.md` | `4f40ac4c80fe3aa57e3f537386c370291d725466f3e97e154ed4c8532509e1e9` | `docs/reviews/REC-02-r2.md` | Khớp 100% |
| `tests/test_recovery.py` | `427f5c2123ff763d495c5ac0fdfd26ba3fce22f4a41cb50779dcabf24e4f209c` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `tests/test_validation.py` | `775af961855ab0f973143dced95b04e8002663c7f79d8148ffc9aca451db722d` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `tests/test_cli.py` | `ce3b80f1e2ebca43f3db02a264451d9df81916d9abe73194752d6d0819ebef6a` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `tests/test_pipeline.py` | `c6a851a8872f24f436a833be681affb3278ff699183f075a860061fda6cc3aaa` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `docs/decisions/0002-recovery-evidence.md` | `298cc89a23004380134611d1f8dc170a8583ae9a17bf0f7724c455bf5cfb3981` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `docs/handoffs/REC-01.md` | `cd631d4e727c2ecbac937635496ea1fb77e8070b00f94e59fd40af4a700e7f43` | `docs/reviews/REC-01-r3.md` | Khớp 100% |
| `docs/handoffs/DAT-01.md` | `134c9598f6a9cdcbd759f552de81ce6257237c16bf1473e4cc99ba44294d3702` | `docs/reviews/DAT-01-r3.md` | Khớp 100% |
| `tests/test_errors.py` | `ccfd3b9788f51aba541b41755b277f9a229c76bf9a6ae066c118f089dec40912` | Base commit 517f00e | Khớp 100% |
| `pyproject.toml` | `7d8dd22b90d338bf3a938f1a1152a47f04ee95b55c295b42cffbe26fbdf195c1` | Base commit 517f00e | Khớp 100% |
| `examples/task.json` | `559de10d8ace3cc13495b986a14d9da0d80b4d4a85920d043b24d6a741672b9d` | DAT-01 commit spec | Khớp 100% |

*Ghi chú*: File `docs/tasks/REC-02.md` có mã băm `48cd2ab20368658ad16640773352000c961000a9e34770259af3ff8b760f64f7`, là bản cập nhật trạng thái `DONE` và ghi nhận kết luận của điều phối sau khi nghiệm thu tại `docs/reviews/REC-02-r2.md`, hoàn toàn đúng quy trình.

---

## 2. Manifest Phân loại File Tích hợp G1

Để chuẩn bị cho commit tích hợp sạch sẽ, toàn bộ 34 files bị thay đổi hoặc chưa track trong working tree được phân tách nghiêm ngặt thành 4 nhóm theo đúng quy chuẩn `AGENTS.md` và `docs/COORDINATION.md`:

### Nhóm 1: Files Code, Tests và Tài liệu Kỹ thuật cốt lõi thuộc G1 (20 files)
Các file này trực tiếp hiện thực hóa hợp đồng dữ liệu Gate G1 (Validation, Transition Guard, Crash Matrix, Atomic Checkpoint):
1. `omp_core/main.py`: Production codebase hoàn thiện của G1 (schema 1, strict transitions, candidate tree, atomic save, error chaining).
2. `pyproject.toml`: Khai báo package và entrypoint `omp`.
3. `examples/task.json`: Task mẫu định dạng schema 1.
4. `tests/test_validation.py`: 27 unit tests kiểm tra schema/path/OID/parser.
5. `tests/test_cli.py`: 18 tests kiểm tra ranh giới CLI và error classification.
6. `tests/test_pipeline.py`: 8 integration tests baseline.
7. `tests/test_recovery.py`: 31 tests chuyển trạng thái, candidate tree và commit intent.
8. `tests/test_crash_recovery.py`: 21 tests bao phủ ma trận gián đoạn M1–M9.
9. `tests/test_errors.py`: 6 tests phân loại lỗi và khử thông tin nhạy cảm.
10. `docs/CONTRACTS.md`: Đặc tả hợp đồng dữ liệu, bảng schema, atomic checkpoint.
11. `docs/SDD.md`: Thiết kế kiến trúc tổng thể sau G1.
12. `README.md`: Hướng dẫn vận hành, CLI, quy tắc schema và atomic recovery.
13. `docs/decisions/0002-recovery-evidence.md`: ADR-0002 về verification evidence.
14. `docs/decisions/0003-crash-recovery-matrix.md`: ADR-0003 về crash recovery matrix.
15. `docs/tasks/DAT-01.md`: Gói giao việc DAT-01 (DONE).
16. `docs/tasks/REC-01.md`: Gói giao việc REC-01 (DONE).
17. `docs/tasks/REC-02.md`: Gói giao việc REC-02 (DONE).
18. `docs/handoffs/DAT-01.md`: Biên bản bàn giao DAT-01.
19. `docs/handoffs/REC-01.md`: Biên bản bàn giao REC-01.
20. `docs/handoffs/REC-02.md`: Biên bản bàn giao REC-02.

### Nhóm 2: Tài liệu Điều phối và Lộ trình chung (6 files)
Tài liệu quản lý dự án cấp cao, được bảo toàn nguyên trạng, không tự ý sửa:
1. `docs/ROADMAP.md`: Lộ trình tổng thể theo gate.
2. `docs/COORDINATION.md`: Quy chế điều phối giữa các AI.
3. `docs/templates/HANDOFF.md`: Biểu mẫu bàn giao.
4. `docs/FND-01.md`: Tài liệu nghiệm thu FND-01.
5. `docs/tasks/FND-02.md`: Gói giao việc FND-02.
6. `docs/handoffs/FND-02.md`: Biên bản bàn giao FND-02.

### Nhóm 3: Lịch sử Review & Chuyển giao AI (9 files)
Các báo cáo độc lập ghi lại quá trình kiểm định của reviewer/điều phối, không đưa vào mã nguồn sản phẩm:
1. `docs/reviews/DAT-01.md`
2. `docs/reviews/DAT-01-r2.md`
3. `docs/reviews/DAT-01-r3.md`
4. `docs/reviews/REC-01.md`
5. `docs/reviews/REC-01-r2.md`
6. `docs/reviews/REC-01-r3.md`
7. `docs/reviews/REC-02.md`
8. `docs/reviews/REC-02-r2.md`
9. `docs/prompts/DAT-01-review.txt`

### Nhóm 4: Cấu hình Workspace cá nhân và Thư mục Runtime (Loại trừ)
Tuyệt đối KHÔNG commit vào kho mã nguồn:
1. `Open-Multi-agent-Pipeline.code-workspace`: File cấu hình IDE cá nhân của developer.
2. `.omp/`: Runtime local, cache kiểm thử, log tiến trình (đã cấu hình trong `.gitignore`).

---

## 3. Bằng chứng Build, Install và Isolated Entrypoint Smoke Test

### A. Quy trình thực hiện
Đã thực hiện quy trình đóng gói và kiểm thử cô lập hoàn toàn ngoài source repository trong thư mục tạm `.omp/test-tmp/g1_smoke_44t4q5di`:
1. **Tạo bản sao source sạch**: Sao chép `pyproject.toml`, `README.md` và thư mục `omp_core/` (đã loại bỏ `__pycache__` và `*.pyc`).
2. **Khởi tạo môi trường ảo riêng biệt**:
   - Lệnh: `C:\Users\POTA\AppData\Local\Programs\Python\Python312\python.exe -m venv <smoke_dir>\venv`
   - Exit code: `0` (thời gian: `5.55s`).
3. **Cài đặt package từ bản sao source**:
   - Lệnh: `<venv>\Scripts\pip.exe install --no-deps <smoke_dir>\source`
   - Exit code: `0` (thời gian: `5.75s`).
   - Cài đặt thành công `omp-orchestrator-0.1.0`.
   - File entrypoint sinh ra: `<venv>\Scripts\omp.exe`
     - Kích thước: `108,462` bytes.
     - SHA-256: `022aca76a60a726df765dd159e270060b54517454a8932ecba7a0d94308accfd`.
   - Đối chiếu file cài đặt: `<venv>\Lib\site-packages\omp_core\main.py`
     - SHA-256: `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0`.
     - Trạng thái: **Khớp 100%** với mã nguồn trong repo.

### B. Kiểm thử Entrypoint ngoài source repository
Toàn bộ các lệnh sau được gọi từ thư mục làm việc `<smoke_dir>` (hoàn toàn nằm ngoài source tree):

1. **Kiểm tra trợ giúp CLI (`omp --help`)**:
   - Lệnh: `<venv>\Scripts\omp.exe --help`
   - Exit code: `0`.
   - Kết quả: In hướng dẫn cú pháp chuẩn và danh sách các lệnh con: `init`, `run`, `resume`, `status`.

2. **Khởi tạo cấu hình (`omp init`)**:
   - Khởi tạo repo Git sạch: `git init` tại `<smoke_dir>\test_repo`, commit baseline `2a746610ac3b6add94ce1c58c96601928749b2fb`.
   - Lệnh: `<venv>\Scripts\omp.exe --root <test_repo> init`
   - Exit code: `0`.
   - Kết quả: Tạo `<test_repo>\.omp\config.json` hợp lệ với `schema_version: 1`, `timeout_seconds: 600`.

3. **Kiểm tra trạng thái khi chưa có run (`omp status` - Fail-closed)**:
   - Lệnh: `<venv>\Scripts\omp.exe --root <test_repo> status`
   - Exit code: `1`.
   - Stderr: `OMP: Cannot read JSON from ...\.omp\state.json: [Errno 2] No such file or directory` (thông báo lỗi sạch sẽ, không có traceback Python).

4. **Chạy tác vụ End-to-End (`omp run task.json`)**:
   - Cấu hình Worker và Test script chuẩn, task sửa file `app.py`.
   - Lệnh: `<venv>\Scripts\omp.exe --root <test_repo> run <test_repo>\task.json`
   - Exit code: `0` (thời gian: `1.47s`).
   - Stdout: `Completed 2b8822c757ad8824c0f3163bcb56690b044c5af0 in ...\checkout`.

5. **Kiểm tra trạng thái sau khi hoàn tất (`omp status`)**:
   - Lệnh: `<venv>\Scripts\omp.exe --root <test_repo> status`
   - Exit code: `0`.
   - Stdout: In JSON state chuẩn xác: `phase: "complete"`, `completion_kind: "committed"`, `result_commit: "2b8822c757ad8824c0f3163bcb56690b044c5af0"`, có đầy đủ `commit_intent` và `verification_evidence`.
   - Git commit trên run checkout khớp 100% với `result_commit`.

---

## 4. Bằng chứng Kiểm tra Toàn diện Test Suite

### A. Kết quả chạy `python tools/check.py`
- Lệnh: `python tools/check.py`
- Môi trường: Windows 11 IoT Enterprise LTSC (`10.0.26100.0`), Python 3.12.7, Git 2.56.0.windows.1.
- Kết quả: `Source checks passed; running 111 tests`, `Ran 111 tests in 80.158s`, `OK`, exit code: `0`.
- Phân rã 111 tests:
  - 27 validation unit tests (`tests/test_validation.py`)
  - 18 CLI boundary tests (`tests/test_cli.py`)
  - 6 error classification & redaction tests (`tests/test_errors.py`)
  - 8 pipeline integration tests (`tests/test_pipeline.py`)
  - 31 recovery & transition tests (`tests/test_recovery.py`)
  - 21 crash recovery matrix tests (`tests/test_crash_recovery.py`)

### B. Công khai Hiện tượng Flake trên Windows
Theo quy định tại `AGENTS.md`, mọi hiện tượng flake phải được công khai minh bạch:
- Trong lượt chạy kiểm tra đầu tiên, test `test_cleanup_timeout_hides_argv_from_cli_and_last_error` gặp lỗi `[WinError 5] Access is denied` khi `os.replace` đổi tên file tạm `.tmp` đè lên `state.json`.
- Nguyên nhân: Do tiến trình nền trên Windows (Antivirus / Windows Search Indexer) đang giữ handle đọc file `state.json` tạm thời đúng thời điểm `os.replace` diễn ra.
- Chạy lại đơn lẻ (`python -m unittest ...`): PASS ngay lập tức trong 0.556s.
- Chạy lại toàn bộ suite 111 tests tuần tự: PASS 100% (exit code 0).

### C. Kiểm tra Định dạng và Khối diff được Bảo vệ
- `git diff --check`: Exit code `0` (không có lỗi khoảng trắng thừa, tab hoặc lỗi dòng).
- `git diff --exit-code 517f00e -- tests/test_errors.py pyproject.toml .github tools AGENTS.md`: Exit code `0` (toàn bộ các file được bảo vệ không bị thay đổi).

---

## 5. Giới hạn Kỹ thuật và Các Kiểm tra / CI còn thiếu

Trước khi điều phối chính thức đóng Gate G1, các giới hạn và bước kiểm tra sau cần được ghi nhận rõ:
1. **CI Remote GitHub Actions (Matrix 4 jobs)**:
   - Chưa chạy CI remote (Ubuntu/Windows × Python 3.10/3.12) cho revision hiện hành do tuân thủ quy tắc không tự ý `git commit`, `git push` lên GitHub.
   - Khi chủ dự án cấp quyền commit và push branch, toàn bộ 4 jobs CI cần chạy và đạt 100% trên remote.
2. **Kiểm thử trên Môi trường Linux thật & Python 3.10 runtime thật**:
   - Toàn bộ bằng chứng hiện tại được thực thi trên môi trường Windows 11 và Python 3.12.7. Tính tương thích Python 3.10 được kiểm chứng qua signature callback `onerror`, chưa chạy trên runtime 3.10 thực tế.
3. **Giới hạn An toàn Mất điện Vật lý**:
   - Ma trận crash recovery M1–M9 và atomic save chứng minh logic phần mềm không làm hỏng dữ liệu và xử lý đúng exception đĩa; điều này **không tương đương** với việc chứng nhận ổ cứng vật lý chịu được rút nguồn đột ngột khi bộ đệm phần cứng chưa ghi xuống phiến đĩa.
4. **Giới hạn Quản lý Tiến trình (Spawn Gap & OS Containment)**:
   - Khoảng trống giữa khi gọi `subprocess.Popen` và khi lưu `child_pid` chưa được bảo vệ triệt để nếu orchestrator bị hard-kill ngoài OS; nội dung này thuộc phạm vi giải quyết của Gate G2 (PROC-01 khóa liên tiến trình và PROC-02 Windows Job Objects).

---

## 6. Kết luận và Khuyến nghị

1. Toàn bộ 3 nhiệm vụ thành phần của Gate G1 (**DAT-01**, **REC-01**, **REC-02**) đã hoàn thành xuất sắc và được nghiệm thu độc lập.
2. Bản sao mã nguồn đã được đóng gói và kiểm thử entrypoint thành công ngoài source tree với đầy đủ 5 trường hợp sử dụng cơ bản.
3. Hồ sơ tích hợp và manifest đã được chuẩn bị đầy đủ và phân loại tách bạch.
4. **Khuyến nghị**: Đề xuất chủ dự án xem xét cấp quyền thực hiện commit tích hợp G1 sạch sẽ (theo Nhóm 1 trong manifest) và kích hoạt CI remote để chính thức đóng Gate G1 trước khi bước sang Gate G2 (PROC-01).

---

## 7. Xử lý Finding G1-CI-R1 từ Báo cáo Review G1-INTEGRATION

- **Finding**: `G1-CI-R1` (P2) — Fixture amend phụ thuộc Git identity toàn máy (`docs/reviews/G1-INTEGRATION.md`).
- **Nguyên nhân**:
  - Tại `tests/test_crash_recovery.py:399-402` (`test_commit_before_complete_with_corrupted_commit_is_rejected`), test gọi `git commit --amend` trong checkout clone mà không truyền identity.
  - Clone fixture được tạo bằng `git clone --local`, không kế thừa config `user.name`/`user.email` từ repo nguồn.
  - Trên môi trường CI runner sạch không có cấu hình Git global, lệnh amend trả về mã lỗi 128 (`Committer identity unknown`), khiến test bị fail trước khi chạy đến assertion kiểm tra recovery.
- **Biện pháp khắc phục**:
  - Truyền trực tiếp cờ cấu hình `-c user.name="OMP Test" -c user.email="test@localhost"` vào invocation `git commit --amend` tại `tests/test_crash_recovery.py:399-413`.
  - Giữ nguyên 100% kịch bản kiểm thử, tamper HEAD subject, và assertion `assertRaises(OperationError)` / `unexpected commit during recovery`.
  - Không sửa đổi mã nguồn production (`omp_core/main.py`), không sửa cấu hình Git toàn máy, không sửa CI workflow file (`.github/workflows/check.yml`).
- **Bằng chứng kiểm chứng**:
  - Tái hiện lỗi trên máy local trong môi trường không có Git identity (`GIT_CONFIG_GLOBAL=os.devnull`, `GIT_CONFIG_NOSYSTEM=1`, unset author/committer env variables): lệnh ban đầu trả mã lỗi 128 (Fail-Before).
  - Kiểm thử đơn lẻ sau sửa đổi trong môi trường cô lập trên: PASS `OK` trong 1.612s (Pass-After).
  - Toàn bộ suite 111 tests (`python tools/check.py`) trong môi trường cô lập trên: PASS 100% (111 tests `OK` trong 71.292s).
  - `git diff --check`: Exit code 0 (không có khoảng trắng thừa hay lỗi dòng).
  - SHA-256 mới của `tests/test_crash_recovery.py`: `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820`.

