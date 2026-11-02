# SEC-02-B1c-DESIGN — review r3 của điều phối

- Ngày 2026-11-02, base `12f3e625d73979e7a8592298f2e8fb7cba6b7243`.
- Verdict: ACCEPTED cho DESIGN only; hết finding thiết kế chặn sau hiệu đính và kiểm chứng. Không gọi lỗi baseline bên dưới là đã sửa.
- Scope: tài liệu mới và cập nhật trạng thái; không thay runtime/default/parser/test/workflow/dependencies. Không phải nghiệm thu whole B1c hoặc G3.

## Review độc lập

Đối chiếu trực tiếp `omp_core/main.py` (validate_config/validate_state/validate_verification_evidence/execute/advance/recover/abort), profile/process API của B1a, layout/manifest/artifact reader của B1b. R1/r2 được giữ nguyên, không thay lịch sử bằng báo cáo success của agent.

| AC | Kết quả thiết kế |
| --- | --- |
| D1 | Call flow thật, PID clear phụ thuộc tree proof; process.close là bước riêng; abort state v1 không bị mô tả sai thành blanket refusal |
| D2–D3 | Chọn fixture offline; không đổi main/schema/default hoặc fallback; không import proof vào runtime |
| D4 | Compatibility hiện tại và closed journal/expectation proposal; chưa reserve version production mới |
| D5 | Trusted expectation canonical hash, Oracle so output/count độc lập; live ownership khác inventory; reader trước Job close |
| D6 | Append-only snapshots, PID-save gap không giả durable, fault/interrupt/close/restart hard refusal; borrowed evidence không bị xóa |
| D7 | B1c-1 chỉ journal/expectation, consumer file I/O + artifact fixture, negative controls; execution/bridge/recovery gate riêng |
| D8 | API chưa triển khai, native B1c proof NOT RUN, G3 mở và giới hạn giữ nguyên |

Antigravity review tĩnh cùng conversation `f0a7940d-29ae-4290-9cd2-22f50edfa81a`, archive `fb19b8097770fc13`: response có nội dung, không có denied_actions được báo, không finding chặn. Agent vẫn nhắc `expected_result_hash` của draft cũ: điều phối không chấp nhận tên đó; bản thực tế chỉ có `output_sha256` và digest expectation. `OfflineRunResult` là proposal B1c-2, phải đặc tả trước khi giao implementation; không là API công bố. Review không chạy shell/tests hoặc chứng minh runtime.

## Kiểm chứng và lỗi quan sát

- `python tools/check.py`: source checks pass; chạy đủ 521 tests trong 101.700s, FAILED errors=1. Test `test_complete_child_pid_is_refused_by_recover_abort_and_run_check` lỗi trong setup `complete_run → advance → execute`; WindowsJobError báo root exited with 1 descendant(s), job terminated them. Archive đầy đủ `e776671bc55f2c55`.
- Đọc fixture: test command là Python `-B -c 'import app; assert app.value == 1'`, không chủ ý spawn child. Đọc `WindowsJobProcess.wait`: root signaled rồi query active count; khác biệt thời điểm accounting là giả thuyết, CHƯA chứng minh nguyên nhân. Không nới guard/test/expected hoặc gọi đã sửa.
- `python .omp/b1c-design-repro.py` (root): chạy nguyên test lỗi 10 lần tuần tự, 10/10 OK trong 13.453s; archive `f28cb0625896998f`. Không sửa tests hoặc guard. Chưa tái hiện được; nguyên nhân vẫn chưa xác định. Full rerun `python tools/check.py`: source checks và 521 tests OK trong 101.605s, archive `602053e46d566e7a`. Lỗi đầu vẫn là observation chưa xác định nguyên nhân, cần điều tra riêng nếu tái hiện; không có thay đổi runtime để gọi là fix.
- Preservation: 180 file gốc không drift, original index empty; code/test/tools/workflow/AGENTS/pyproject không đổi so base.
- B1c implementation/native boundary proof, local Python 3.14: NOT RUN. Các baseline native tests không là B1c proof mới. CI xanh không chứng minh power loss, OS isolation tổng quát hoặc restart authority.

## Tiếp theo

Đã nghiệm thu DESIGN sau checks đầy đủ; final source/link/scope check không có lỗi, đúng tám Markdown files. Khi xuất bản vẫn cần CI Windows Python 3.12/3.14 cho head/PR và kiểm main sau merge. B1c-1 implementation chưa giao; cần task ownership/AC cụ thể theo ADR-0011. Nếu lỗi baseline tái hiện, mở task điều tra riêng, không âm thầm sửa production trong gói tài liệu này.
