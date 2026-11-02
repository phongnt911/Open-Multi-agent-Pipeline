# Review SEC-02-B1c-DESIGN r2

- Ngày 2026-11-02; reviewer điều phối. Verdict **CHANGES_REQUIRED** cho bản 70 dòng sau sửa r1.
- Đóng D-R2: đã bỏ attestation legacy làm recovery authority. D-R3/D-R4 có hướng đúng nhưng chưa đủ hợp đồng triển khai.
- R2-1/P2: Journal một file dùng rename no-clobber sẽ không thể ghi phase kế tiếp. Các object evidence/intent chưa định nghĩa field/types, và có phase committing/result_commit nhưng flow không có Git commit. Cần format nhất quán và policy ghi thực thi được, không copy schema production chưa có consumer.
- R2-2/P2: `layout.is_cleaned` không có; `OwnedL2Layout.create(root,run_id,profile.sid)` sai signature keyword-only user_sid/container_sid. Callee cleanup layout sau complete sẽ xóa journal/evidence; phải phân biệt tài nguyên được transfer và vùng evidence borrowed/retained.
- R2-3/P2: Save PID lỗi không thể hứa “giữ PID trong journal” vì write chưa thành công. Cần giữ live PID trong owner frame, durable record cũ là unproven, restart refuse.
- R2-4/P2: D-R6 chưa thực sự chia nhỏ. Đưa cả runner/native/schema vào B1c-1 vẫn khó review; cần gói journal/expectation có consumer I/O cụ thể trước seam execution.
- Quyết định điều phối: nhận ownership hiệu đính ADR sau hai vòng; Antigravity chuyển sang review tĩnh, không cùng sửa file. Không thay acceptance D1–D8, không giảm gate. Handoff r1/r2 là lịch sử; verdict cuối ghi riêng.
- Chưa có code/native proof mới; mọi proposed API mới phải ghi rõ chưa tồn tại.
