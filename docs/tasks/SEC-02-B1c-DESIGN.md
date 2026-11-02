# SEC-02-B1c-DESIGN — chốt hợp đồng trước implementation

- Trạng thái: DONE (DESIGN only, điều phối nghiệm thu theo review r3), 2026-11-02. Antigravity viết draft r1/r2; sau review r2 điều phối nhận hiệu đính ADR. Antigravity chỉ review tĩnh bản cuối, không sửa chồng file.
- Base: `12f3e625d73979e7a8592298f2e8fb7cba6b7243`; worktree riêng `.omp/sec02-b1c-design`, branch `codex/sec-02-b1c-design`.
- Giao theo chỉ đạo tiếp tục sau B1b và PR #10. Đây chỉ giao **B1c-DESIGN**, không giao whole B1c implementation, B2, SEC-03 hoặc G4. G3 mở.
- Phụ thuộc đã đủ: B1a native primitives, B1b layout/env/manifest/reader DONE; 521 baseline tests. ADR-0009 proposal chưa phải schema/API production.

## Ownership

Antigravity chỉ tạo hai file mới:
1. `docs/decisions/0011-b1c-offline-orchestration.md`: ADR Proposed với thiết kế cụ thể, options/tradeoffs, schema/compatibility/lifecycle/recovery và giới hạn.
2. `docs/handoffs/SEC-02-B1c-DESIGN.md`: nguồn đã đọc, AC mapping, câu hỏi/gates, không bịa test/proof; bàn giao REVIEW.

Điều phối sở hữu task này và việc cập nhật B1 index/roadmap sau review. Mọi file khác chỉ đọc; không sửa code/test/CLI/schema/ADR cũ/review history. Không commit/push/merge hoặc cấp quyền bởi AI thiết kế. Không import tools.proofs, không chạy provider thật, không thay ACL/label/profile trên tài sản thật, không delegate/subagent.

## Acceptance

- D1: Đối chiếu call flow hiện tại: validate/preflight → lock/revalidate → advance → execute/save PID/resume → verification/tree identity → commit/complete; recovery/abort giữ evidence và attestation limits. Ghi file/symbol thật; phân biệt current API với proposal.
- D2: So ít nhất hai hướng có thể triển khai: tích hợp trực tiếp main/schema có activation gate, hoặc orchestration offline với fake fixture và consumer cụ thể. Chọn phương án nhỏ nhất vẫn kiểm chứng điểm nối B1a/B1b; không tạo registry/plugin/framework hoặc module chỉ scaffold.
- D3: Chốt ranh giới B1c/B2 có thể kiểm thử: không đổi default production; request trusted không được âm thầm fallback cooperative. Nêu rõ cách gọi fixture/native và đường nào tuyệt đối chưa được public CLI/main gọi.
- D4: Matrix chính xác cho config legacy/v1, task v1, state v1/v2 và mọi format mới được đề xuất; read/status/resume/write/abort/recover, unknown/bool/type/version mismatch, bytes cũ bất biến. Không reserve config v2/state v3/evidence v2 chỉ bằng tài liệu. Tránh tăng version nếu không có consumer thực.
- D5: Schema/identity cụ thể cho run/task/nonce, candidate/tree algorithm, approved invocation/spec/manifest, durable ownership và completion. Phân biệt PID/SID/path strings với ownership/OS proof; không serialize live handles. Bind evidence với tree và inputs thật, không dùng artifact exit0/PASS làm authority.
- D6: Lifecycle/recovery matrix trước/sau mỗi allocation/save/resume/tree-stop/reader/job-close/profile-delete/complete. Allocation crash trước checkpoint, unknown cleanup hoặc stale profile phải fail-closed; không auto-delete/adopt từ tên/SID trong JSON. Không suy PID absent thành tree stopped, attestation thành OS proof, hoặc xóa evidence để resume.
- D7: Đề xuất gói implementation đầu tiên có ownership, dependency, AC/failure/native tests/negative controls và consumer trực tiếp; các bước chưa đủ evidence phải ghi gate riêng. Không gộp whole orchestration/schema/recovery/activation vào một diff.
- D8: Ghi giới hạn NOT PROVEN và checks thực sự đã/chưa chạy. Không tự ACCEPTED/DONE, không giảm yêu cầu Windows 3.12/3.14 cho process/recovery/security.

## Kiểm chứng và handoff

Điều phối đọc hai tài liệu và mã liên quan, đối chiếu từng AC, sửa finding rồi mới ACCEPTED design. Chạy `python tools/check.py` và diff/scope/preservation checks; task docs-only không thêm runtime tests để mirror prose. Chưa có implementation/native proof mới phải ghi NOT RUN, không gọi CI xanh là nghiệm thu boundary. Sau design ACCEPTED mới giao gói implementation cụ thể; không suy B1c/B2 DONE.
