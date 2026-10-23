# PROC-01: Khóa liên tiến trình và bảo vệ quyền sở hữu run

- Trạng thái: REVIEW.
- Gate: G2 (Vòng đời tiến trình).
- Phụ thuộc: G1 DONE tại fd5e219, run 39797882641 attempt 2 completed/success và 4 jobs terminal success; xem docs/reviews/G1-CLOSURE.md.
- Owner: AI thực thi PROC-01 (opencode, DeepSeek V4.1 Flash Free); nhận việc ngày 2026-10-23.
- Reviewer: AI điều phối.
- Base HEAD: `fd5e21908fbfd9827077f2a7f1c1b510ee19c324`.
- File sở hữu: `omp_core/main.py` (chỉ thêm module/hàm lock và hook vào run/resume), `tests/test_process_lock.py` (mới), `docs/tasks/PROC-01.md`, `docs/handoffs/PROC-01.md`, `docs/CONTRACTS.md` (nếu cần cập nhật hợp đồng lock).
- File chỉ đọc: các file tests hiện có (`test_validation.py`, `test_cli.py`, `test_pipeline.py`, `test_recovery.py`, `test_crash_recovery.py`, `test_errors.py`), `pyproject.toml`, workflow CI, review history.

---

## 1. Vấn đề và Kết quả mong muốn

- **Hiện trạng**: Đã có lock() dùng msvcrt.LK_NBLCK trên Windows và fcntl.flock trên POSIX; run/resume giữ lock quanh mutation/advance. Task kiểm chứng bằng process thật và chỉ sửa lỗi được test tái hiện, không xây lock framework mới.
- **Kết quả mong muốn**:
  1. Khi một tiến trình OMP đang nắm quyền sở hữu run, tiến trình thứ hai cố gắng thực thi `run` hoặc `resume` trên cùng run/project phải bị từ chối ngay lập tức với lỗi rõ ràng (`OperationError`, exit code nonzero), không được phép can thiệp state hoặc spawn thêm Worker.
  2. Khóa phải có cơ chế tự động giải phóng (auto-release) khi tiến trình sở hữu bị tắt hoặc crash đột ngột (hard-kill).
  3. PID metadata cũ/tái sử dụng không được thay thế quyền acquire OS lock hoặc làm kill nhầm process. OS lock là nguồn quyết định exclusivity; không cần thêm PID registry nếu không có consumer thực sự.
  4. Lệnh `omp status` chỉ đọc, không đòi hỏi độc quyền ghi và không làm ảnh hưởng tiến trình đang chạy.

---

## 2. Phạm vi

### Trong phạm vi
- Kiểm chứng OS lock hiện có trên Windows/POSIX và integration run/resume; chỉ sửa production nếu regression chứng minh lỗi.
- Bộ kiểm thử đa tiến trình thật trong `tests/test_process_lock.py`: spawn hai tiến trình OMP đồng thời, kiểm chứng từ chối tranh chấp, kiểm chứng tự nhả khóa khi kill tiến trình sở hữu.
- Kiểm chứng PID recycling ở lock ownership; nếu cần metadata owner thì đối soát process incarnation, không coi token cạnh PID là bằng chứng identity. Không unlink file lock để phá stale lock.

### Ngoài phạm vi
- Windows Job Object và containment toàn bộ cây con (thuộc phạm vi PROC-02).
- Các lệnh phục hồi nâng cao `omp recover` / `omp abort` (thuộc phạm vi PROC-03).
- Không thêm dependency bên ngoài (ưu tiên thư viện chuẩn `msvcrt` / `fcntl` / `ctypes` nếu cần).

---

## 3. Tiêu chí nghiệm thu (Acceptance Criteria)

- [x] **AC1**: Hai process orchestrator thật tranh cùng project: chỉ owner mutation/spawn Worker; contender exit nonzero, không traceback, không sửa checkpoint/clone/logs hoặc spawn Worker thứ hai. Test run/resume với fixture phù hợp; không ép đổi nguyên văn message nếu boundary hiện có đúng.
- [x] **AC2**: Kill owner thật sau readiness handshake; process mới acquire cùng OS lock được mà không xóa lock file. Kiểm acquire riêng với resume: checkpoint còn child_pid phải tiếp tục chặn, không auto-clear/spawn Worker mới. Cleanup fake child có đối soát trước thao tác thủ công trong fixture; containment thuộc PROC-02.
- [x] **AC3**: PID metadata giả/cũ/tái sử dụng không thay OS lock authority: lock đang giữ thì contender bị chặn; lock đã nhả thì PID lạ không bị coi là owner hoặc bị kill nhầm. Không claim child ownership từ PID-only; mock incarnation phải ghi giới hạn.
- [x] **AC4**: Lệnh `omp status` là read-only, có thể quan sát trạng thái run ngay cả khi tiến trình khác đang giữ lock.
- [x] **AC5**: Toàn bộ 111 baseline tests hiện có giữ nguyên kết quả PASS 100%, không bị regression.
- [x] **AC6**: Kiểm thử độc lập trên Windows đạt `python tools/check.py` và `git diff --check` sạch sẽ.
- [x] **AC7**: Negative control pre-exit/boundary chưa tới phải fail; readiness có deadline, mọi process có timeout/finally cleanup, Worker count có marker riêng. Fixture độc quyền, Git identity cục bộ, Python >=3.10. Lỗi mới có regression fail trước/pass sau; nếu code đã đúng chỉ thêm tests/evidence.

---

## 4. Kế hoạch kiểm chứng

- Viết test suite `tests/test_process_lock.py` sử dụng `subprocess.Popen` để khởi chạy đồng thời hai tiến trình orchestrator thật.
- Kiểm chứng negative control: khẳng định tiến trình thứ 2 thất bại khi tiến trình thứ nhất đang giữ lock.
- Kiểm chứng kill recovery: tách acquire-lock thành công khỏi child_pid guard. Không yêu cầu resume thành công khi Worker cũ chưa đối soát.

## Điều kiện nhận việc và bàn giao

Đọc AGENTS/COORDINATION/CONTRACTS/SDD/CONTRIBUTING, ROADMAP và review G1.
Đối chiếu main.py SHA-256 `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0`, test_crash_recovery.py `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820`.
Giữ mọi diff có sẵn. Dependency G1 đã được điều phối xác nhận tại G1-CLOSURE;
không dùng báo cáo pending cũ để tự mở lại gate khi không có evidence mới.
Ghi owner/IN_PROGRESS, chạy baseline 111 tests trước sửa.
Không reset/clean/stash, AI thật, agent phụ, dependency runtime mới, commit/push/merge.
Không sửa tests cũ/checker/workflow/review history hoặc mở PROC-02/03.
Handoff ghi AC mapping, hash, commands/count/exit, Windows evidence, flakes và
giới hạn; bàn giao REVIEW cùng prompt reviewer trực tiếp trong chat, không tự DONE/đóng G2.
