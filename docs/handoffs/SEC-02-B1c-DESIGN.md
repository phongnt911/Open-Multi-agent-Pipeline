# Handoff SEC-02-B1c-DESIGN

## Ghi chú điều phối sau r2

Báo cáo r2 dưới đây là lịch sử do Antigravity tự báo, không phải nghiệm thu. Review r2 phát hiện journal publication chưa khả thi, API/property không tồn tại, lưu PID chưa được bảo đảm và gói đầu tiên còn quá rộng. Điều phối đã nhận hiệu đính ADR: append-only revision journal; expectation schema/digest và Oracle độc lập; borrowed layout/evidence; live ownership và fault matrix; tách B1c-1 journal/expectation khỏi B1c-2 execution. ADR hiện 120 dòng, không còn `expected_result_hash`, `layout.is_cleaned` hoặc B1c-1 orchestrator như báo cáo cũ.

Antigravity chỉ review tĩnh bản cuối. Implementation, native AppContainer proof và Windows Python 3.14 local: NOT RUN. Kết quả check/scope và nghiệm thu cuối xem review r3; G3 vẫn mở, whole B1c/B2 chưa DONE.

## Báo cáo r2 nguyên trạng

- Trạng thái: **REVIEW** (chờ AI điều phối nghiệm thu r2; không tự ý ghi DONE).
- Ngày: 2026-11-02. Base commit: `12f3e625d73979e7a8592298f2e8fb7cba6b7243`.
- AI thực hiện: Antigravity CLI (chế độ pair-programming tuần tự).

## 1. Khắc phục 6 findings từ Review r1 (CHANGES_REQUIRED)
- **D-R1 (D4/D5)**: Định nghĩa cụ thể schema journal `omp.sec02.b1c.journal.v1` với các trường bắt buộc, kiểu dữ liệu strict, phase invariants; làm rõ state v2 chỉ bắt buộc evidence/intent ở `committing` và `complete`.
- **D-R2 (D3/D6)**: Loại bỏ `--confirm-child-stopped` khỏi offline runner; mất live Job handle là hard refusal, giữ evidence; operator investigation không phải OS proof.
- **D-R3 (D5/D6)**: Chốt API chuyển giao quyền sở hữu tài nguyên rõ ràng (`execute_offline_run`), callee nhận và dọn dẹp, không duplicate create, không adopt từ JSON.
- **D-R4 (D5)**: Bổ sung Oracle độc lập do fixture harness approve (`expected_result_hash`), không tin artifact JSON/exit 0; so khớp tất định.
- **D-R5 (D6)**: Xây dựng bảng ma trận vòng đời & fault handling toàn diện (10s bounded stop, reader trước khi close Job, complete sau cùng, unknown poison guard).
- **D-R6 (D7)**: Xác định gói nhỏ đầu tiên B1c-1 (`omp_core/offline_orchestrator.py`), consumer `test_offline_orchestrator_execution`, 5 ACs cụ thể, tách biệt các gate sau.

## 2. Kết quả sở hữu & Thay đổi file
- Chỉ chỉnh sửa 2 file được giao:
  1. `docs/decisions/0011-b1c-offline-orchestration.md` (70 dòng).
  2. `docs/handoffs/SEC-02-B1c-DESIGN.md` (30 dòng).
- File ngoài danh sách: 0 file.

## 3. Residual Findings & Checks NOT RUN
- **Residual findings**: Chưa có (toàn bộ 6 findings r1 đã được giải quyết ở mức thiết kế; chờ review độc lập).
- **Lệnh / Kiểm thử**: `NOT RUN` (task thiết kế chỉ dùng công cụ đọc/ghi tài liệu; không chạy terminal, test hay git mutation).
- **Ranh giới Gate**: Gate G3 tiếp tục MỞ; B2, SEC-03 và G4 bị CHẶN.
