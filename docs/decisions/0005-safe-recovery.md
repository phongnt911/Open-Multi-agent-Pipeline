# ADR-0005: Recovery/abort CLI có đối soát, lock và audit

- Ngày: 2026-10-23
- Trạng thái: Proposed cho PROC-03 REVIEW; chưa phải gate bảo mật G2/G3.
- Phạm vi: CLI `recover`/`abort`, mutation `child_pid` có kiểm soát, sidecar
  audit và tương tác với checkpoint/run hiện có.

## Bối cảnh

Checkpoint v2 đã có state machine chặt, evidence/commit intent và atomic save.
PROC-02 đã thêm containment Job Object trên Windows: khi owner chết, Windows
đóng handle cuối và `KILL_ON_JOB_CLOSE` dừng cây tiến trình. Tuy nhiên
checkpoint chỉ lưu `child_pid`; sau crash, `resume` fail-closed vì không chứng
minh được PID đó còn thuộc run hay không. Hướng dẫn cũ yêu cầu sửa
`state.json` thủ công, không có audit và dễ tạo hai Worker.

Các dữ kiện không được suy diễn:

- PID còn/không còn **không** chứng minh ownership, incarnation hoặc toàn bộ
  cây tiến trình đã dừng.
- Job Object của PROC-02 không được persist thành handle sau khi owner chết;
  recovery không thể mở lại job cũ để query `ActiveProcesses`.
- `git checkout` không chứng minh process tree đã dừng.
- Xác nhận của người vận hành là attestation, không phải bằng chứng OS.

## Quyết định

### 1. CLI và input bắt buộc

```text
omp [--root ROOT] recover RUN_ID --state-sha256 HEX [--confirm-child-stopped]
omp [--root ROOT] abort   RUN_ID --state-sha256 HEX
```

- `RUN_ID` phải khớp chính xác `state.run_id`.
- `--state-sha256` là SHA-256 của **bytes thô** `.omp/state.json`, 64 ký tự
  hexadecimal. Không dùng digest của JSON đã normalize.
- `--confirm-child-stopped` chỉ dành cho `recover`. Đây là attestation tường
  minh của người vận hành rằng họ đã kiểm tra PID và cả cây tiến trình; OMP
  không coi đây là bằng chứng OS và không tự động dùng flag này.
- Không có flag free-form reason để tránh ghi secret vào audit. Reason là mã
  cố định do OMP sinh.
- `abort` không nhận `--confirm-child-stopped`: abort không đổi `child_pid`,
  không kill và không tuyên bố stopped.

### 2. Validate trước lock, revalidate dưới lock

Trước khi lấy lock, CLI chỉ đọc và validate:

1. `--root` là Git root; đọc object ID length theo Git hiện có.
2. `state.json` tồn tại, là JSON object UTF-8, validate bằng `validate_state`
   với schema/field/phase/evidence/intent hiện có.
3. `state.run_id` khớp `RUN_ID`, digest bytes khớp `--state-sha256`.

Sau đó CLI lấy exclusive non-blocking OS lock `.omp/lock`. Nếu lock đang bị
giữ, dừng với lỗi contention; không ghi state/audit. Dưới lock, CLI đọc lại
**bytes mới**, tính lại digest, validate lại state/run ID và abort marker. Chỉ
sau bước này mới được audit hoặc mutate. Nếu bytes/digest/run ID đổi giữa hai
lần đọc, refuse và giữ nguyên checkpoint. Abort marker chỉ được đọc/validate
dưới lock vì marker và checkpoint phải được đối soát như một cặp. Preflight
mismatch hoặc lock contention không ghi audit vì CLI chưa sở hữu lock và chưa
có checkpoint đã revalidate; mọi refusal/no-op sau lock đều có audit event.

`run`, `resume`, `recover`, `abort` dùng cùng lock. `recover` không bao giờ
spawn Worker/test; `abort` không spawn gì. Vì vậy không có hai Worker hoặc hai
mutation song song từ các CLI này.

### 3. Semantics của `recover`

`recover` chỉ chuẩn bị checkpoint để `resume` có thể tự chạy tiếp; nó **không**
gọi `advance`, không chạy Worker/test, không sửa checkout/commit và không push.

Sau revalidation dưới lock:

| Điều kiện | Kết quả | Mutation |
| --- | --- | --- |
| Run đã có abort marker hợp lệ | Refuse: run đã abort; giữ evidence | Không |
| State schema v1 chưa complete | Refuse: legacy v1 không resume | Không |
| Phase `complete`, `child_pid` null | Success `no_change`: đối soát intent/tree/commit | Không |
| Phase `complete`, `child_pid` khác null | Refuse: checkpoint complete không nhất quán | Không |
| Phase `preparing`, `checkout` đã tồn tại | Refuse: interrupted clone; dùng `abort`/run mới | Không |
| Phase khác `preparing`, `checkout` thiếu | Refuse: checkout/evidence thiếu | Không |
| `child_pid is None` | Success `no_change`: checkpoint sẵn sàng cho `resume` | Không |
| `child_pid` khác null, PID còn sống | Refuse: không kill, không clear | Không |
| `child_pid` khác null, trạng thái PID unknown | Refuse: ownership không xác định | Không |
| `child_pid` khác null, PID absent, thiếu `--confirm-child-stopped` | Refuse: absence không phải proof | Không |
| `child_pid` khác null, PID absent, có `--confirm-child-stopped` | Success: clear **chỉ** `child_pid` | Có |

Khi clear `child_pid`:

- Không đổi phase, config, task, evidence, commit intent, result, clone, log,
  checkout hoặc repository nguồn.
- Validate lại state sau mutation trước khi ghi.
- Audit ghi `containment_proof: "operator_attestation_only"` và
  `child_pid_after: null`; không ghi "stopped by OMP".
- Nếu PID còn sống hoặc query lỗi, flag xác nhận cũng không được dùng để clear.
- Không kill, signal, `taskkill`, terminate job hoặc mở process handle có quyền
  terminate. Chỉ dùng query quyền hạn chế để phân loại PID.

`recover` không sửa `last_error`; lỗi cũ vẫn là evidence. `resume` hiện có chịu
trách nhiệm chạy tiếp và cập nhật checkpoint theo state machine.

### 4. Semantics của `abort`

`abort` đánh dấu run bị bỏ có kiểm soát, nhưng **không** xóa hay thay đổi
checkpoint, clone, log, checkout, commit, evidence hoặc provenance.

Sau revalidation dưới lock:

| Điều kiện | Kết quả | Mutation |
| --- | --- | --- |
| Abort marker hợp lệ, digest khớp state hiện tại | Success `no_change` (idempotent) | Không ghi marker mới; audit thêm; archive thiếu được phục hồi từ bytes hiện tại |
| Abort marker hỏng hoặc digest không khớp | Refuse: giữ nguyên mọi bytes | Không |
| Phase `complete` | Success `no_change`: không có gì để abort | Không |
| Run chưa complete, chưa abort, `child_pid` khác null | Refuse: chưa có bằng chứng tree đã dừng | Không |
| Run chưa complete, chưa abort, `child_pid is None` | Archive state thô + ghi marker + audit | Sidecar only |

Chi tiết run chưa complete:

1. Tạo `RUN_DIR = .omp/runs/<run_id>` nếu chưa có.
2. Ghi atomic bản sao **bytes thô** của `state.json` vào
   `RUN_DIR/aborted-state.json` trước khi tạo marker. Nếu archive đã tồn tại,
   chỉ chấp nhận khi bytes khớp chính xác; khác thì refuse, không ghi đè.
3. Ghi atomic `RUN_DIR/aborted.json` gồm schema, run ID, state SHA-256, action,
   timestamp và `child_pid` tại thời điểm abort (không đổi state chính).
4. Ghi audit event. Chỉ báo success sau khi archive/marker và audit đã bền vững.

Sau abort hợp lệ:

- `resume` và `recover` refuse run đó dù `child_pid` còn hay không.
- `run` được phép bắt đầu run mới chỉ khi marker hợp lệ, digest marker khớp
  bytes `state.json` hiện tại, archive khớp đúng bytes đó và `child_pid` là
  null. Nếu marker tồn tại bên cạnh checkpoint (kể cả phase complete), marker
  và archive bắt buộc phải đối soát khớp exact bytes trước mọi shortcut; marker
  hỏng, digest lệch hoặc archive thiếu/lệch thì `run`/`resume`/`recover`
  fail-closed. Run mới thay `.omp/state.json` nhưng bản checkpoint cũ vẫn còn
  nguyên tại `RUN_DIR/aborted-state.json`; clone/log/checkpoint cũ không bị xóa.

`abort` không clear `child_pid`, không kill, không tuyên bố tree stopped và
không đổi phase thành complete. Vì vậy abort không thể tạo resume giả. Nếu
`child_pid` còn ghi, người vận hành phải xử lý riêng bằng
`recover --confirm-child-stopped` (sau khi PID absent) rồi mới abort; abort
không tự bỏ guard.

### 5. Audit

Audit event là file JSON riêng dưới `.omp/audit/<run_id>/`, tên duy nhất gồm
UTC timestamp và UUID; ghi atomic bằng temp + fsync + `os.replace`.

Field tối thiểu:

- `schema_version`, `timestamp_utc`, `action` (`recover`/`abort`);
- `result` (`success`/`no_change`/`refused`/`error`), `reason_code`;
- `run_id`, `state_path`, `before_sha256`, `after_sha256` (`after_sha256` là
  digest mới sau thao tác, hoặc chuỗi `"unknown"` nếu `checkpoint_changed: true`
  nhưng digest mới không đọc được như ở `checkpoint_digest_failed`);
- `phase_before`, `phase_after`, `state_schema_version`;
- `child_pid_before`, `child_pid_after`, `checkpoint_changed`;
- `operator_confirmed_child_stopped`, `containment_proof`;
- `abort_marker` (đường dẫn tương đối hoặc null).

Audit **không** chứa config, task specification, worker/test argv, prompt,
stdout/stderr, token, environment hoặc raw exception command. Reason dùng mã
cố định như `checkpoint_ready`, `complete_verified`,
`complete_child_pid_recorded`, `child_pid_absent_operator_attested`,
`child_pid_still_recorded`, `state_digest_mismatch`, `unsupported_schema`,
`checkpoint_save_failed`, `checkpoint_digest_failed`, `audit_save_failed`.

Quy tắc lỗi:

- Refusal/no-op: ghi audit trước khi báo kết quả. Audit lỗi thì command lỗi,
  không báo refused/no_change thành công.
- Recover mutation: ghi checkpoint atomic trước, đọc lại digest, rồi ghi audit.
  Nếu checkpoint save lỗi, cố ghi audit `error`; nếu audit cũng lỗi, lỗi audit
  là lỗi chính và giữ checkpoint error trong cause chain. Không báo success.
  Nếu checkpoint đã ghi nhưng audit lỗi, command trả lỗi; retry thấy
  `child_pid` đã null và ghi audit `no_change`.
- Abort: archive, marker, rồi audit. Nếu marker đã ghi nhưng audit lỗi, command
  trả lỗi; retry idempotent ghi audit. Không báo success trước audit.
- Mọi lỗi ở trên giữ cause chain đã sanitize; diagnostics I/O và refusal giữ
  nguyên exception type và sanitized context, không nối raw exception, message
  hoặc filename chứa secret/argv qua cause/context.

### 6. Ranh giới crash

| Crash tại | Trạng thái bền vững | Lần chạy lại |
| --- | --- | --- |
| Trước lock | Không đổi | Preflight mới |
| Sau lock, trước audit/mutation | Không đổi | Revalidate/refuse hoặc làm lại |
| Recover: sau save state, trước audit | `child_pid` đã clear | Audit `no_change`; không chạy Worker |
| Abort: sau archive, trước marker | Archive có, chưa abort | Làm lại; archive ghi đè cùng bytes |
| Abort: sau marker, trước audit | Aborted, thiếu audit | Retry idempotent ghi audit |
| Audit temp/partial | Không có event chính thức | Retry tạo event mới; temp dọn nếu được |
| State temp/partial | State cũ nguyên bytes | Save atomic giữ state cũ |

Không có nhánh nào tự chạy Worker, tự resume, tự kill hoặc tự đổi phase.

### 7. Refusal policy và OS evidence

- Lock đang giữ hoặc owner còn sống: refuse; không mutation.
- State digest/run ID mismatch, state hỏng, version không hỗ trợ: refuse; giữ
  bytes.
- PID sống, PID lạ/reused, ownership unknown: refuse; không kill, không clear.
- PID absent: chỉ là absence; không đủ clear nếu thiếu attestation.
- Complete state còn `child_pid`: refuse `run`/`recover`/`abort`; không coi là
  complete sạch và không spawn replacement.
- Abort marker hỏng/không khớp, archive thiếu/lệch, hoặc `child_pid` còn ghi:
  refuse; không overwrite và không tạo run mới.
- Lock contention không ghi audit vì CLI chưa sở hữu lock; state/evidence vẫn
  nguyên bytes.
- Job Object/KILL_ON_JOB_CLOSE chỉ là containment lifecycle đã có; recovery
  không có handle/job identity để chứng minh tree stopped sau owner death.
- Operator confirmation chỉ là attestation; audit ghi rõ giới hạn. Không có
  tuyên bố "OMP stopped the tree" hoặc "PID này chắc chắn là Worker cũ".

### 8. Compatibility và phạm vi

- Không đổi checkpoint schema. State v1/v2 vẫn được validate đúng contract cũ;
  audit/marker là sidecar, không trở thành field của state.
- State v1 chưa complete: `recover`/`resume` refuse; `abort` chỉ được archive
  và đánh dấu aborted khi `child_pid` là null, không rewrite state.
- Không migration ngầm, không auto-clear, không auto-run, không auto-push,
  không failover/retry, không dependency runtime mới.
- POSIX dùng cùng semantics; query PID chỉ để phân loại, không phải ownership
  proof. Không thêm containment POSIX trong PROC-03.

## Hệ quả

- Người vận hành có thể clear guard sau crash bằng CLI có digest/run binding và
  audit, thay vì sửa JSON thủ công, nhưng phải chịu trách nhiệm attestation khi
  không có bằng chứng OS.
- Abort trở thành terminal sidecar marker; evidence cũ vẫn còn để điều tra.
- Các giới hạn còn lại: không chứng minh PID incarnation, không chứng minh
  descendant đã dừng khi thiếu Job handle/OS registry, không phải sandbox hay
  bảo đảm chịu mất điện vật lý.
