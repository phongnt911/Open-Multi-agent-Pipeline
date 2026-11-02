# Review SEC-02-B1c-DESIGN r1

- 2026-11-02; reviewer: AI điều phối, độc lập với bản nháp Antigravity.
- Base `12f3e625d73979e7a8592298f2e8fb7cba6b7243`; verdict **CHANGES_REQUIRED**. Đây là findings trên proposal, chưa có runtime implementation.
- Đọc ADR-0011 (52 dòng), handoff, ADR-0009 và mã main/windows_job/trusted_layout liên quan. SUCCESS/exit0 không thay AC.
- Chưa ACCEPTED design, chưa giao B1c-1 implementation; G3 mở.

## Findings

- **D-R1 — P2, D4/D5:** ADR §3/§4 dùng “checkpoint offline” nhưng không định nghĩa namespace/version, field set, required/optional theo phase, types, serialization/read/write và transition invariants. Không thể kiểm thử compatibility/malformed/unsupported/replay. Cần một format nội bộ cụ thể (nếu thực sự cần), tách khỏi state v2 production; CLI hiện hành reject, không auto-migrate. Nêu rõ v2 chỉ bắt evidence/intent ở committing/complete; không phải mọi phase.
- **D-R2 — P1, D3/D6:** ADR §5 sau mất Job handle lại gợi ý `--confirm-child-stopped`. Flag này chỉ thuộc CLI recovery legacy; không thể chứng minh profile ownership/tree stop của offline format mới. Cần hard refusal cho tiếp tục/complete/replacement/adopt/delete sau mất owner/live proof, giữ evidence; operator investigation không biến thành OS proof hoặc quyền xóa profile.
- **D-R3 — P1, D5/D6:** §1/§6 nhận `layout, profile, manifest` rồi flow lại `profile.create → layout.setup` và cuối cùng tự close/delete; chưa rõ borrowed hay transferred ownership. Rủi ro proposal dọn tài nguyên không do callee sở hữu hoặc tạo lặp. Chọn một API cụ thể, không ellipsis: hoặc allocate nội bộ, hoặc transfer resources mới do trusted fixture tạo với preconditions rõ và không tạo lại; chỉ cleanup đúng owned objects, không adopt từ JSON.
- **D-R4 — P1, D5:** §4 cấm stdout PASS nhưng chưa cấm lấy JSON artifact PASS/exit0 làm authority. B1b reader chỉ kiểm handle/schema/binding; spec_hash tự nó không phải Oracle. Cần trusted expected result/rule được test harness approve độc lập và bind digest; mismatch phải fail kể cả nonce/hash hợp lệ. Không nhận expectation từ Worker/artifact/journal hoặc thêm arbitrary pytest/plugin engine.
- **D-R5 — P2, D6:** Lifecycle chưa có matrix đầy đủ cho allocation/layout/manifest/save PID/resume/stop/reader/close Job/delete profile/final save; thiếu save/cleanup failure và interrupt/poison behavior. Cần phase/invariant và restart refusal cho từng gap, có deadline 10s, preserve exception type/cause đã sanitize, không retry/replace nếu unknown. Chỉ successful run đọc artifact, lỗi chạy/stop/reader không được chuyển complete.
- **D-R6 — P2, D7:** B1c-1 hiện gộp orchestration/state/reader/cleanup/native trong hai filename với hàm `...`; chưa có dependency/AC/negative controls hoặc phạm vi đủ nhỏ. Cần chia gói đầu tiên có consumer trực tiếp và tests quan sát hành vi; không module scaffold, không reserve production version, không đổi main/B1a/B1b/baseline tests. Chỉ ra các gói còn chưa được giao.

## Kiểm chứng cần sau sửa

Đối soát D1–D8 với source, matrix current-vs-proposal và từng fault boundary. Giao lại đúng hai file owned. Handoff phải nêu residual/NOT RUN thay vì khẳng định đầy đủ D1–D8 trước reviewer acceptance. Chưa có native proof mới; design checks không thay gate runtime.
