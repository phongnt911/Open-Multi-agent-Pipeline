# REC-01: Chuyển trạng thái và bằng chứng phục hồi có đối soát

- Trạng thái: DONE
- Gate: G1 (mở)
- Owner: AI thực thi REC-01 (Antigravity)
- Reviewer/nghiệm thu: AI điều phối trong chat chủ dự án
- Phụ thuộc: DAT-01 DONE, review r3 ACCEPTED, local 59 tests và packaging đạt.
- Base Git hiện tại: `517f00e092201a08886eb30500cd21e648bc2c4b`; bắt đầu từ
  working tree DAT-01 đã nghiệm thu, không phải source nguyên bản ở HEAD.
- DAT-01 chưa commit/CI remote. Ghi HEAD, diff/file list và hash lúc nhận;
  tuyệt đối không reset/clean/stash để làm sạch baseline.

## Quyết định nghiệm thu của điều phối

Ngày 2026-10-22: ACCEPTED tại `docs/reviews/REC-01-r3.md` trên đúng hash bàn giao;
reviewer chạy lại 90/90 tests và diff checks đạt, probe riêng R1a/R2a/R5 đạt.
DONE là nghiệm thu nội dung; chưa commit/integration/CI revision mới. G1 vẫn mở,
chuyển sang REC-02; không cấp quyền push/merge.

## Baseline phải bảo toàn

Trước khi sửa, đối chiếu hash DAT-01:

| File | SHA-256 |
| --- | --- |
| omp_core/main.py | e9e493c2f6cf4d06c03ffb6b6563cb5da0b71566f4a0fd71221353d4a25a5d17 |
| tests/test_validation.py | 1d9bec19808dba8344b577c926501ef778bb1e024527f2acfda8df1ef31d20f9 |
| tests/test_cli.py | 328a400bebdfc386b61848deba50ed9a03d42dd6b598b09bc0c622523e38f22a |
| tests/test_pipeline.py | eed4b1d34f81c2a25af27c79ea0ad3a68d589185c7afd944844bfa6e066f451a |

Hash lệch trước khi bắt đầu phải báo điều phối và đối soát, không âm thầm
nhận code khác làm baseline. Sau triển khai code đổi hash là bình thường;
handoff phải ghi hash mới và diff riêng REC-01 so với baseline DAT-01.

## Mục tiêu và phạm vi

Mỗi trạng thái phải có điều kiện chuyển rõ ràng. Kết quả test chỉ có hiệu lực
cho đúng candidate Git tree, task và command đã chạy. Resume không được dùng
test result cũ cho code mới hoặc tin commit chỉ vì message/parent khớp.
No-op task phải có kết quả tường minh, không bịa commit mới hoặc bỏ qua test.

REC-01 xây logic phục hồi có đối soát và fault injection tối thiểu cho logic
đó. REC-02 mới kiểm tra toàn crash matrix/clone dở/kill cứng/atomic write.
Không làm process containment, PID ownership, retry/failover, test vault hay
sandbox OS trong task này; không tự phục hồi clone dở bằng xóa checkout.

## Ownership

- omp_core/main.py; có thể tách state/recovery helper thực sự cần thiết,
  không tạo framework, registry hoặc module rỗng.
- tests/test_recovery.py mới; tests/test_pipeline.py, test_validation.py,
  test_cli.py chỉ cập nhật/add cases liên quan schema/phase/evidence.
- tests/test_errors.py chỉ đọc; giữ Git error/cause và timeout redaction.
- docs/CONTRACTS.md, docs/SDD.md, README.md: mô tả hành vi đã triển khai.
- docs/decisions/0002-recovery-evidence.md: ghi quyết định schema/tương thích
  theo yêu cầu dưới đây, không thay ADR cũ.
- docs/tasks/REC-01.md, docs/handoffs/REC-01.md.
- Không sửa ROADMAP, COORDINATION, AGENTS, reviews/handoff/task DAT-01, FND,
  workflow, checker, dependency, workspace cá nhân hoặc artifact của người khác.

## Hợp đồng thiết kế bắt buộc

### 1. Transition và trạng thái lỗi

Giữ phase tuyến tính `preparing → worker → verifying → committing → complete`.
Viết bảng precondition/postcondition và helper enforce transition dùng thật
trong orchestrator. Không đổi phase bằng dict assignment rải rác để bỏ guard.

- preparing: chỉ chuyển worker sau checkout đúng baseline đã sẵn sàng.
- worker: chỉ chuyển verifying sau Worker exit 0 và không còn PID chưa đối soát.
- verifying: scope hợp lệ; tạo bằng chứng test đạt cho candidate hiện tại.
- committing: chỉ vào khi đã có verification hợp lệ và commit intent.
- complete: kết quả được đối soát với evidence và checkout/commit thực tế.

Test fail/timeout/scope sai không được có evidence PASS hoặc phase complete.
Giữ nguyên thông tin lỗi an toàn; không tự retry/gọi lại Worker vô hạn.
PID còn lưu tiếp tục chặn; không xóa PID để dễ resume, không suy ra ownership.

### 2. Candidate và verification identity

- Dùng Git candidate tree OID xác định nội dung tracked và file allowed mới
  sẽ được commit. Identity không thay đổi index/HEAD/repo nguồn của người dùng.
  Có thể dùng temporary Git index trong clone; không để file tạm chung giữa run.
- Run/task/config identity phải gắn với evidence; command identity dùng hash
  canonical argv/test settings, không thêm argv/token vào lỗi/log công khai.
- Bằng chứng tối thiểu: passed exit code 0, candidate tree OID, command/task
  identity và operation ID. Có schema/type/required fields và tests dữ liệu hỏng.
- Snapshot trước test và sau test phải cùng candidate. Nếu test sửa candidate,
  bằng chứng không hợp lệ; dừng an toàn hoặc chạy lại qua quy trình hữu hạn,
  không tự cập nhật hash để coi test trước đó đã kiểm chứng code mới.
- Trước commit/resume, đối chiếu evidence với candidate/config hiện tại.
  Code, task hoặc command đổi làm evidence cũ mất hiệu lực. Chạy lại verification
  hợp lệ hoặc chặn rõ ràng; không được dùng kết quả PASS cũ để complete.
- Với runner tùy chỉnh, exit code theo contract trusted command hiện hành;
  không bịa test count nếu runner không cung cấp. Runner mặc định vẫn chặn
  zero tests, kể cả optimize. Chưa tuyên bố bảo vệ chống Worker đối kháng.

### 3. Commit intent và resume

- Lưu intent trước Git commit: run ID, base/expected parent, expected tree và
  commit marker duy nhất, gắn verification đã đạt. Không chỉ tin message.
- Nếu crash sau commit nhưng trước complete checkpoint: so sánh HEAD, parent,
  message marker và tree với intent/evidence. Khớp thì hoàn tất không commit
  lặp; khác thì fail closed và giữ nguyên clone để điều tra.
- Stage dở chỉ được xử lý trong clone thuộc run theo intent/scope đã xác minh;
  không reset/source checkout người dùng. Chưa đối soát được thì chặn.
- Resume complete phải kiểm tra evidence/result và checkout thực tế; tree
  bẩn/code đổi hoặc commit không khớp không được tiếp tục in completed giả.
- No-op: test vẫn phải đạt; tree bằng baseline được ghi completion_kind=no_change,
  result_commit=base_commit, không tạo commit rỗng. Resume no-op idempotent.
- Task thay đổi thật ghi completion_kind=committed với commit đã đối soát.

### 4. Schema và tương thích

Config/task vẫn version 1, giữ legacy input policy DAT-01. Evidence/intent và
completion semantics mới dùng checkpoint version 2. Validate fail-closed,
không thêm field mới vào strict schema 1 mà không version hóa.

Cho phép đọc/status state v1 theo schema DAT-01, không rewrite. Resume state v1
chưa hoàn tất phải chặn trước mutation với hướng dẫn giữ run/checkout và bắt
đầu run mới; không tự suy diễn test evidence hoặc nâng cấp tại chỗ. State v1
complete chỉ dùng cho status/đối soát điều kiện bắt đầu run mới, không diễn
giải thành evidence v2. Không cần xây command migration trong REC-01.

Tài liệu hóa rõ giới hạn tương thích trong ADR/README, version lạ giữ bytes và
bị từ chối. Tests schema v1 hiện có giữ ý nghĩa validation; fixture tạo run mới
có thể đổi v2 với giải thích cụ thể, không nới assertion nhằm giấu regression.

## Acceptance

- [x] AC1: transition table/docs và guard dùng trong production; transitions
  sai/thiếu evidence fail trước mutation, không có complete sau test fail.
- [x] AC2: candidate identity đúng cho tracked change, allowed new file và
  deletion; scope violations bị chặn; không đổi repo nguồn hoặc index khi chỉ
  tính identity. Git OID SHA-1/SHA-256 được tôn trọng.
- [x] AC3: test PASS gắn đúng tree/task/command; sửa code/command/task sau PASS
  hoặc test tự sửa candidate làm evidence mất hiệu lực, không commit/complete giả.
- [x] AC4: fault injection sau commit trước checkpoint; resume đúng commit
  hoàn tất một lần, không duplicate. Cùng message/parent nhưng tree sai bị chặn.
- [x] AC5: resume complete với checkout bẩn/evidence sai/HEAD sai bị từ chối;
  committed và no_change đều có kiểm thử idempotency, no-op vẫn chạy test.
- [x] AC6: checkpoint v2 strict, v1 status read-only; v1 resume chưa complete
  bị chặn giữ bytes/checkout, version lạ không mutation; config/task không đổi.
- [x] AC7: các 59 baseline behaviors giữ nguyên trừ thay đổi schema/resume đã
  được task này yêu cầu rõ; fixture/assertion đổi phải được giải thích từng
  trường hợp, không skip/nới expected. R1a/error/zero-tests/redaction vẫn đạt.
- [x] AC8: python tools/check.py, git diff --check đạt trên Windows; tests
  meaningful lỗi cũ fail trước/sau pass, fake providers, không AI thật.
- [x] AC9: handoff đủ baseline + diff REC-01 riêng, hash mới, AC mapping,
  commands/OS/count/exit, checks thiếu và risks; docs/ADR khớp code, không
  tuyên bố crash/power-loss/OS containment đã đạt từ mocked exceptions.

## Trình tự bàn giao

Đọc AGENTS/COORDINATION/CONTRIBUTING/CONTRACTS/SDD, task này và handoff/review
DAT-01. Ghi owner, IN_PROGRESS, baseline checks và các file không thuộc mình.
Viết failing regressions, làm thay đổi tối thiểu, kiểm tra diff/test/docs.
Nếu task có mâu thuẫn làm không thể giữ invariant, báo điều phối và tiếp tục
phần độc lập; không âm thầm đổi policy. Không tự triển khai REC-02.

Không commit/push/merge, không spawn agent phụ, không dependency mới, không gọi
AI thật. Tạo handoff và prompt review trực tiếp trong chat; chuyển REVIEW,
không tự ACCEPTED/DONE hoặc đóng G1. Điều phối review và giao task sau khi đạt.
