# SEC-02-B1b-INTEGRATION

- Trạng thái: DONE, 2026-10-29. Điều phối: AI trong chat; Antigravity sở hữu riêng handoff tích hợp mới, không sửa implementation.
- Quyền chủ dự án: giao việc tích hợp/review; đủ điều kiện thì commit, push, tạo PR và merge. Không tự mở G3/B1c/B2.
- Base: `8e33dadaf257e14bb641a0cf0b0327e7c28f45ef`. Đầu vào: review B1b r5 ACCEPTED và sáu hashes trong handoff/audit r5.

## Ownership và preservation

- Sáu file implementation accepted: `omp_core/trusted_layout.py`, `tests/test_trusted_layout.py`, `docs/CONTRACTS.md` (chỉ section B1b), `docs/decisions/0010-offline-trusted-layout.md`, `docs/tasks/SEC-02-B1b.md`, `docs/handoffs/SEC-02-B1b.md`.
- Artifact quy trình được xuất bản cùng gói: task tích hợp này, handoff tích hợp và review r5. Các báo cáo integration/merge sau đó được điều phối ghi riêng.
- Antigravity chỉ được tạo `docs/handoffs/SEC-02-B1b-INTEGRATION.md`; đọc-file và edit-file, không command/Git/remote hoặc chạy test trùng. Điều phối tự kiểm tra manifest, copy, diff, tests và publication.
- Dùng worktree riêng dưới `.omp/sec02-b1b-integration`; không checkout/reset/stash/clean workspace gốc. Snapshot/hash mọi file git liệt kê trước copy; không stage .omp/temp/workspace hay tài liệu lịch sử ngoài scope. Không sửa accepted bytes chỉ để CI xanh.

## Tiêu chí nghiệm thu và thứ tự

1. Đối soát accepted SHA-256 và base còn đúng remote main; chỉ copy đúng sáu file cùng ba artifact quy trình.
2. Reviewer đọc exact diff/file list, CONTRACTS chỉ thêm B1b, baseline code/tests/workflow/dependency không drift; ghi report với giới hạn độc lập.
3. Tạo conventional commit trên nhánh integration riêng; kiểm tree/hashes/index và preservation.
4. Chạy focused/full `python tools/check.py` trên exact commit Windows Python 3.12, 0 skip; packaging install và import/entrypoint từ ngoài source. Fake Worker, không AI/credentials thật; log dưới .omp.
5. Push nhánh, PR vào main; chờ CI Windows 3.12/3.14 hiện hành kết thúc, đọc job steps/logs và đối soát exact head/merge ref. Lỗi phải sửa có evidence/review hoặc BLOCKED, không skip/nới test.
6. Khi review + checks + CI đạt, merge bằng expected-head SHA; không bypass branch protection. Đối soát merge tree với head accepted và chờ actual-main CI.
7. Ghi integration/merge evidence và trạng thái đúng; B1b chỉ DONE khi điều kiện nghiệm thu đủ. G3 vẫn mở, chưa tích hợp main/CLI. Giữ nguyên mọi thay đổi có sẵn ngoài scope.

## Handoff bắt buộc

Báo file thay đổi, manifest/hashes, base/head/merge/tree, test command/OS/Python/count/skips/exit, packaging, CI URL/jobs, denied actions, lỗi và phần chưa chứng minh. SUCCESS/exit0/response rỗng không thay thế sản phẩm và evidence.

## Điều phối nghiệm thu sau merge — 2026-10-29

B1b và gói tích hợp DONE sau PR #9, merge 3428204d62856bc6b70c95f95cf887d6841740f9, main CI https://github.com/phongnt911/Open-Multi-agent-Pipeline/actions/runs/40813350800 success 2/2; mỗi lane Windows Python 3.12.10/3.14.9 chạy 521 tests OK, 0 skip, packaging và smoke ngoài source đạt. Merge tree khớp head accepted. Xem docs/reviews/SEC-02-B1b-MERGE.md. Các trạng thái REVIEW/IN_PROGRESS ở evidence trước đó là lịch sử trước merge. G3 vẫn mở; B1c/B2 chưa giao. Đây là cập nhật trạng thái điều phối local sau actual-main CI, không thuộc chín-file tree đã merge; không tự xuất bản tài liệu/history ngoài scope.

## Ghi chú hồ sơ đóng việc — 2026-11-02

Bản này thuộc gói xuất bản hồ sơ B1b sau nghiệm thu. Các mô tả tài liệu local/chưa xuất bản ở trên phản ánh thời điểm ghi nhận ngày 2026-10-29; bằng chứng và giới hạn cũ được giữ nguyên. Gói tài liệu này không đổi code/tests/schema hoặc mở B1c/B2, không đóng G3.
