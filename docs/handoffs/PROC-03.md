# Handoff PROC-03 — Bằng chứng tích hợp (Integration Evidence)

- Trạng thái: **REVIEW** — Revision R2 đã được reviewer độc lập **ACCEPTED** tại
  `docs/reviews/PROC-03-r2.md`; G2 vẫn mở; chuẩn bị bằng chứng tích hợp
  (packaging, clean source-copy install, smoke entrypoint, manifest 8 files).
  Chưa DONE/MERGE, không commit/push/merge.
- Owner / revision: AI thực thi PROC-03 (opencode, DeepSeek V4.1 Flash Free / Antigravity),
  nhận việc 2026-10-23; revision R2 (đã sửa hoàn tất PROC03-R1–R3 theo
  `docs/reviews/PROC-03.md` và được ACCEPTED tại `docs/reviews/PROC-03-r2.md`).
  Base main `873cae570e6ee4309676b49808e0cca051154292` (merge PROC-02); branch
  `codex/proc-03-recovery-cli`; HEAD vẫn là base, **không commit/push/merge**.
- Phân định phạm vi working tree: Working tree có 47 paths. Tách biệt rõ ràng:
  - **Đúng 8 files thuộc sở hữu PROC-03** (7 file có hash accepted + file handoff này).
  - **1 file baseline invariant control** (`omp_core/windows_job.py`, không đổi).
  - **39 files ngoài scope** (12 tài liệu coordination/tasks/handoffs cũ, 1 workspace file,
    26 file review & prompt history gồm cả `docs/reviews/PROC-03.md` và `PROC-03-r2.md`).
    Tuyệt đối giữ nguyên, không stage/clean/stash.

## Manifest 8 file PROC-03 và hash xác nhận

| STT | File | Git status | SHA-256 | Trạng thái hash / Ghi chú |
| --- | --- | --- | --- | --- |
| 1 | `omp_core/main.py` | ` M` | `087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2` | Accepted R2 hash |
| 2 | `tests/test_recovery_cli.py` | `??` | `0b8d5f0425afe046bbccf4583aa1993fd32fa15b5932d4ab9fab6ebaf1a8a578` | Accepted R2 hash (23 tests focused) |
| 3 | `README.md` | ` M` | `585ae21637a144427f6219e699aedcba65bce4544d57565c6ba90a72105e7159` | Accepted R2 hash |
| 4 | `docs/CONTRACTS.md` | ` M` | `79dbd08473a5990be392df78b03b90048c0a3564c8b02be111a03f67aa8cbb9b` | Accepted R2 hash |
| 5 | `docs/SDD.md` | ` M` | `a4145f6f904c8b917129a7d7318301a94f447ea094b0d758556dfefae89f7003` | Accepted R2 hash |
| 6 | `docs/decisions/0005-safe-recovery.md` | `??` | `e5ebec9c2da4167913ca4c11525dd345a5cf1b38033a30ccb1992ed3ef36e359` | Accepted R2 hash |
| 7 | `docs/tasks/PROC-03.md` | `??` | `abc18fe8c73e87fe207f25ceacba85c6d2739e673cd0ef8a3b87d6e71f253107` | Accepted R2 hash |
| 8 | `docs/handoffs/PROC-03.md` | `??` | *(File handoff cập nhật bằng chứng tích hợp)* | Handoff tích hợp |

Control baseline invariant:
`omp_core/windows_job.py` giữ nguyên mã băm `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d` (git diff với `873cae5` exit 0).

39 paths có sẵn ngoài scope được loại khỏi manifest và không commit:
- 12 tài liệu điều phối/task cũ: `docs/COORDINATION.md`, `docs/FND-01.md`, `docs/ROADMAP.md`,
  `docs/handoffs/FND-02.md`, `docs/handoffs/G1-INTEGRATION.md`, `docs/tasks/DAT-01.md`,
  `docs/tasks/FND-02.md`, `docs/tasks/PROC-01.md`, `docs/tasks/PROC-02.md`, `docs/tasks/REC-01.md`,
  `docs/tasks/REC-02.md`, `docs/templates/HANDOFF.md`.
- 1 file workspace: `Open-Multi-agent-Pipeline.code-workspace`.
- 26 review & prompt history files: `docs/prompts/DAT-01-review.txt`, `docs/reviews/DAT-01-r2.md`,
  `docs/reviews/DAT-01-r3.md`, `docs/reviews/DAT-01.md`, `docs/reviews/G1-CI-VERIFICATION.md`,
  `docs/reviews/G1-CLOSURE.md`, `docs/reviews/G1-INTEGRATION-r2.md`, `docs/reviews/G1-INTEGRATION.md`,
  `docs/reviews/PROC-01-INTEGRATION.md`, `docs/reviews/PROC-01-MERGE.md`, `docs/reviews/PROC-01-r2.md`,
  `docs/reviews/PROC-01.md`, `docs/reviews/PROC-02-INTEGRATION.md`, `docs/reviews/PROC-02-MERGE.md`,
  `docs/reviews/PROC-02-r2.md`, `docs/reviews/PROC-02-r3.md`, `docs/reviews/PROC-02-r4.md`,
  `docs/reviews/PROC-02.md`, `docs/reviews/PROC-03-r2.md`, `docs/reviews/PROC-03.md`,
  `docs/reviews/PROJECT-NEXT-STEP.md`, `docs/reviews/REC-01-r2.md`, `docs/reviews/REC-01-r3.md`,
  `docs/reviews/REC-01.md`, `docs/reviews/REC-02-r2.md`, `docs/reviews/REC-02.md`.

Chi tiết manifest đã được lưu cấu trúc tại `.omp/proc03-integration-evidence/manifest.json`.

## Hành vi đã triển khai và kết quả review R2 (PROC03-R1–R3)

Review độc lập tại `docs/reviews/PROC-03-r2.md` đã đánh giá **ACCEPTED** toàn bộ AC1–AC10:
- **PROC03-R1 (P2 - RESOLVED):** `_sanitize_exception` loại bỏ raw message, `filename`, `filename2`,
  `__cause__` và `__context__`. Các hàm `_write_audit_event`, `_audit_refusal`, `_audit_error_or_raise`
  thoát khỏi khối `except` trước khi ném, ngăn Python tự động gán raw exception vào `__context__`.
  Toàn bộ exception graph sạch secrets/tokens khi duyệt bằng stack+visited. `KeyboardInterrupt` semantics
  được bảo tồn nguyên vẹn.
- **PROC03-R2 (P1 - RESOLVED):** `_check_existing_run` kiểm tra abort marker (`aborted.json`),
  archive exact bytes (`aborted-state.json`) và `child_pid` trước mọi shortcut phase complete.
  Mọi sai lệch bytes/missing archive/marker child_pid đều bị từ chối fail-closed cả ở preflight lẫn
  trong exclusive lock, không gọi `advance` hay cho phép mutation.
- **PROC03-R3 (P2 - RESOLVED):** Tại `checkpoint_digest_failed` trong `recover`, khi checkpoint đã
  bị đột biến (`checkpoint_changed=True`) nhưng đọc digest mới gặp lỗi, audit event ghi nhận
  `after_sha256: "unknown"`, không tái dùng before hash. Đồng bộ contract và sidecar schema 1.

## Bằng chứng đóng gói (Packaging & Smoke Evidence)

Để đảm bảo quy trình packaging hoàn toàn sạch và không bị ảnh hưởng bởi các tệp thừa trong working tree:
1. **Source copy sạch:** Sao chép 5 tệp (`pyproject.toml`, `README.md`, `omp_core/__init__.py`,
   `omp_core/main.py`, `omp_core/windows_job.py`) sang `.omp/proc03-integration-evidence/source`.
   Không sao chép `build/`, `*.egg-info`, `.git`, `.omp` hoặc `__pycache__`.
   Mã băm SHA-256 của cả 5 tệp trong source copy khớp chính xác với working tree.
2. **Môi trường ảo cách ly (Isolated venv):**
   - Tạo venv độc lập: `python -m venv .omp/proc03-integration-evidence/venv` (exit code: 0).
   - Cài đặt build tool: `venv\Scripts\python -m pip install setuptools>=68` (exit code: 0).
3. **Build & Install từ clean source copy:**
   - Lệnh: `venv\Scripts\python -m pip install --no-build-isolation .omp/proc03-integration-evidence/source`
   - Exit code: **0**
   - Build wheel artifact: `omp_orchestrator-0.1.0-py3-none-any.whl`, size 30,382 bytes,
     SHA-256: `6080d45659fc5f2e900b80df6517161fe4cd3390f9a111644921adb56c9b7459`.
   - Lưu trữ log: `.omp/proc03-integration-evidence/pip-install.log`.
4. **Kiểm tra Metadata & Dependencies:**
   - `venv\Scripts\python -m pip show omp-orchestrator`: Name: `omp-orchestrator`, Version: `0.1.0`.
   - `venv\Scripts\python -m pip check`: Exit code **0** (`No broken requirements found.`).
   - Không có runtime dependency mới nào được thêm vào.
5. **Kiểm tra SHA-256 các module đã cài đặt trong site-packages:**
   - `site-packages/omp_core/main.py`: `087c007ddf0c4577d4e9421454aeb71356ae8f901bb354b96aca2c3a84ed4aa2` (khớp exact revision hash).
   - `site-packages/omp_core/windows_job.py`: `fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d` (khớp control hash).
6. **Smoke Test entrypoint ngoài source tree (`.omp/proc03-integration-evidence/smoke`):**
   - `venv\Scripts\omp.exe --help`: Exit code **0**, output hiển thị đầy đủ các lệnh con gồm `{init,run,resume,recover,abort,status}`.
   - `venv\Scripts\omp.exe init` ngoài git repo: Exit code **1**, fail-closed an toàn: `OMP: fatal: not a git repository...`.
   - `venv\Scripts\omp.exe status` trong repo chưa có state: Exit code **1**, fail-closed an toàn: `OMP: Cannot read JSON...`.
   - `venv\Scripts\omp.exe recover --help`: Exit code **0**, hiển thị các tùy chọn `--state-sha256`, `--confirm-child-stopped`.
   - `venv\Scripts\omp.exe abort --help`: Exit code **0**, hiển thị tùy chọn `--state-sha256`.
   - Import module từ venv Python: `venv\Scripts\python -c "import omp_core.main as m; print(m.__file__)"` → Exit code **0**, trỏ đúng vào `site-packages`.

Toàn bộ 6 kiểm tra smoke đều đạt hoàn hảo. Script và logs lưu tại `.omp/proc03-integration-evidence/`.

## Lệnh kiểm thử, OS, Python, counts, exits, và Windows Flake

Môi trường kiểm thử: Windows 11 `10.0.26100` AMD64, Python `3.12.7`.
Tests sử dụng fakes và offline fixtures, tuyệt đối không gọi AI thật.

| Lệnh/Kiểm tra | Lần chạy | Kết quả | Exit code | Thời gian / Ghi chú |
| --- | --- | --- | --- | --- |
| `python tools/check.py` (Full suite 160 tests) | Lần đầu (Reviewer r2) | FAILED (1 error) | **1** | 117.232s, `WinError 32` tại cleanup temp dir |
| `python tools/check.py` (Full suite 160 tests) | Rerun tuần tự | **160 tests OK** | **0** | 119.544s, 0 skips |
| `python -W error::ResourceWarning -m unittest tests.test_recovery_cli -v` | Focused suite | **23 tests OK** | **0** | 16.838s, 0 skips (gồm test_r1, test_r2, test_r3) |
| `python -m py_compile omp_core/main.py tests/test_recovery_cli.py` | Compile bytecode | Đạt | **0** | Không lỗi cú pháp |
| `git diff --check` | Whitespace/format | Sạch | **0** | Không lỗi whitespace/LF |
| `git diff --exit-code 873cae5 -- <baseline_controls>` | Baseline invariance | Không đổi | **0** | tests/tools/pyproject/windows_job nguyên vẹn |

### Ghi nhận minh bạch Windows flake (WinError 32)
- Tại lần chạy full suite đầu tiên trong đợt review R2, kiểm thử `test_cli.CliErrorTests.test_cleanup_timeout_hides_argv_from_cli_and_last_error` gặp lỗi `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process` khi cleanup `TemporaryDirectory`, đối với file `.gitignore` trong repo tạm.
- Lần chạy rerun tuần tự ngay sau đó đạt **160 tests OK**.
- **Tuyên bố minh bạch:** Rerun tuần tự đạt **KHÔNG** chứng minh đã sửa root cause (do cơ chế khóa tệp trễ của Windows / Antivirus / File Indexer giữ file handle). Không sửa test baseline, không skip test, không nới expected để pass. Hiện tượng này được ghi nhận công khai vào bằng chứng tích hợp để tiếp tục theo dõi trên CI.

## Các checks còn thiếu / Giới hạn an toàn

1. **Checks còn thiếu (chưa chạy):**
   - CI remote trên exact revision commit cho ma trận: Windows & Linux × Python 3.10 & 3.12.
   - Runtime kiểm thử trên môi trường Linux local và Python 3.10 local.
   - Live AI provider execution (toàn bộ kiểm thử hiện tại sử dụng fake Worker offline).
   - Kiểm thử owner hard-kill mới riêng cho PROC-03 (hiện tại dựa vào 16 kiểm thử containment baseline của PROC-02 chạy trong full suite).
2. **Giới hạn an toàn hệ thống:**
   - Attestation `--confirm-child-stopped` chỉ là xác nhận thủ công của người vận hành, không phải bằng chứng kernel-level rằng cả cây tiến trình đã dừng.
   - Windows Job Object `KILL_ON_JOB_CLOSE` bảo vệ vòng đời tiến trình khi OMP chạy, nhưng recovery sau khi OMP đã kết thúc không thể mở lại job handle cũ để truy vấn `ActiveProcesses`.
   - Audit trail là tệp sidecar local, không phải append-only/WORM lưu trữ chống giả mạo; người dùng có cùng quyền hệ thống vẫn có thể chỉnh sửa/xóa.
   - Lệnh `abort` là trạng thái kết thúc (terminal) bảo toàn bằng chứng; không tự động dọn dẹp các nhánh Git checkout hay log cũ.

## Bước tiếp theo và đề xuất điều phối

Revision R2 đã được ACCEPTED; bằng chứng đóng gói (clean source packaging & isolated venv smoke) và manifest 8 files đã hoàn tất.
Task tiếp tục ở trạng thái **REVIEW**, **Gate G2 vẫn mở**.
Khi người điều phối cho phép:
1. Tạo commit tích hợp đúng 8 file thuộc manifest PROC-03 trên nhánh `codex/proc-03-recovery-cli`.
2. Kích hoạt và theo dõi CI Windows/Linux × Python 3.10/3.12 trên exact commit SHA.

---

## Prompt bàn giao reviewer kiểm tra evidence tích hợp (chat)

```text
Bạn là reviewer/integrator độc lập kiểm tra evidence tích hợp PROC-03, sau khi
docs/reviews/PROC-03-r2.md đã ACCEPTED revision R2 (PROC03-R1–R3 resolved).
Task vẫn REVIEW, Gate G2 vẫn mở; không commit/push/merge, không chuyển DONE hoặc đóng G2.

Đọc docs/tasks/PROC-03.md, docs/handoffs/PROC-03.md, docs/reviews/PROC-03-r2.md,
docs/decisions/0005-safe-recovery.md, omp_core/main.py, tests/test_recovery_cli.py,
README.md, docs/CONTRACTS.md, docs/SDD.md và .omp/proc03-integration-evidence/manifest.json.

Hãy kiểm tra độc lập:
1. Hash SHA-256 đúng 7 files PROC-03 khớp bảng accepted tại docs/reviews/PROC-03-r2.md;
   control baseline omp_core/windows_job.py giữ nguyên fdd738154183c1a761b3daa5cad7f3acc357ae64febb4b6f42d79456a068116d;
   review history docs/reviews/PROC-03.md và docs/reviews/PROC-03-r2.md không bị sửa.
2. Manifest đúng 8 paths PROC-03 trong .omp/proc03-integration-evidence/manifest.json;
   tách rõ 39 paths có sẵn trong working tree (12 coordination/task/template docs,
   1 workspace file, 26 review/prompt history files); không đưa vào commit scope.
3. Bằng chứng đóng gói tại .omp/proc03-integration-evidence/:
   - Source copy sạch tại source/ không chứa build/, *.egg-info, __pycache__.
   - Isolated venv tại venv/ build thành công wheel omp_orchestrator-0.1.0-py3-none-any.whl
     (SHA-256 6080d45659fc5f2e900b80df6517161fe4cd3390f9a111644921adb56c9b7459).
   - pip check exit 0; không có runtime dependency mới.
   - Module installed trong venv site-packages khớp exact hash r2.
   - 6 smoke tests ngoài source tree đạt (omp --help có recover/abort, init/status fail-closed
     an toàn khi ngoài git repo hoặc thiếu state, recover/abort help, import site-packages).
4. Kiểm tra đối soát suite:
   - Full suite: 160 tests OK ở rerun tuần tự; ghi nhận flake WinError 32 lần đầu
     (tại test_cleanup_timeout_hides_argv_from_cli_and_last_error), không coi rerun là sửa root cause.
   - Focused suite: 23 tests OK (16.838s), gồm test_r1, test_r2, test_r3.
   - py_compile exit 0, git diff --check exit 0, baseline diff exit 0.
5. Xác nhận checks còn thiếu: CI remote Windows/Linux × Python 3.10/3.12 trên exact commit SHA,
   Linux/Python 3.10 local, live provider, new owner hard-kill tests.

Ghi nhận báo cáo kiểm tra evidence tích hợp vào docs/reviews/PROC-03-INTEGRATION.md.
Trả ACCEPTED / CHANGES / BLOCKED kèm bằng chứng cụ thể.
Không tự commit, push, merge, chuyển task sang DONE hoặc đóng Gate G2.
```
