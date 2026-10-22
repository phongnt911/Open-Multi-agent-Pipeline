# DAT-01: Validate và version hóa dữ liệu tại boundary

- Trạng thái: DONE (điều phối nghiệm thu ngày 2026-10-21)
- Gate: G1 (chưa đóng)
- Owner: AI thực thi DAT-01 (opencode, DeepSeek V4.1 Flash Free)
- Reviewer/nghiệm thu: AI điều phối trong chat chủ dự án
- Phụ thuộc: FND-01/FND-02/G0 DONE; CI 39546732827 đạt trên main
  `517f00e092201a08886eb30500cd21e648bc2c4b`.
- Base: `517f00e092201a08886eb30500cd21e648bc2c4b`; lúc nhận có 6 file
  tài liệu điều phối đã sửa chưa commit (`README.md`,
  `docs/COORDINATION.md`, `docs/FND-01.md`, `docs/ROADMAP.md`,
  `docs/handoffs/FND-02.md`, `docs/tasks/FND-02.md`) và
  `docs/tasks/DAT-01.md` chưa track. Bảo toàn các thay đổi này; không coi
  working tree có docs là lỗi phải dọn.
- Handoff: `docs/handoffs/DAT-01.md`

## Quyết định đóng task

Review độc lập ACCEPTED tại docs/reviews/DAT-01-r3.md; mọi finding R1/R1a/R2/R3
đã khép. Điều phối xác nhận bốn hash giữ nguyên, evidence reviewer 59 tests OK,
diff check đạt và packaging/entrypoint smoke từ bản copy/venv riêng đạt.
Chi tiết kiểm chứng bổ sung ở cuối handoff. DAT-01 DONE về nội dung đã nghiệm
thu; chưa commit/tích hợp remote hoặc có CI cho revision này. G1 vẫn mở vì còn
REC-01 và REC-02; không diễn giải DONE là đã phát hành hoặc bảo đảm crash/OS.
Các chỉ dẫn REVIEW bên dưới là quy trình dành cho AI thực thi trước nghiệm thu.

## Mục tiêu

Config/task/state hỏng phải bị từ chối rõ ràng trước khi tạo/chỉnh checkpoint,
clone, chạy Worker/test hoặc commit. Resume/status không được tin cast TypedDict
như validation. Không có KeyError/AttributeError/unknown phase exit 0 im lặng.
Task này chỉ xây hợp đồng dữ liệu; không triển khai REC-01/REC-02.

## Ownership

- omp_core/main.py; có thể thêm omp_core/models.py nếu giúp gom validation
  thật sự, không tạo framework/schema registry.
- tests/test_validation.py mới; tests/test_cli.py/test_errors.py bổ sung boundary.
- tests/test_pipeline.py chỉ được cập nhật fixture cho schema hợp lệ nếu cần;
  giữ nguyên ý nghĩa 8 hành vi, assertions, không skip/nới expected.
- README.md, docs/CONTRACTS.md, examples/task.json: mô tả schema/compatibility.
- docs/tasks/DAT-01.md, docs/handoffs/DAT-01.md.
- Không sửa reviews, workflow, tools/check.py, pyproject/dependency, AGENTS,
  COORDINATION, ROADMAP hoặc artifacts/runtime do người khác tạo.

## Quyết định schema cho task này

1. Config/task mới do init/ví dụ tạo dùng schema_version=1. Input config/task
   cũ không có version được nhận như legacy v0 cùng cấu trúc MVP; chuẩn hóa
   trong bộ nhớ, không tự ghi lại file cũ. Có test legacy chạy thành công.
   Checkpoint giữ schema_version=1 với các trường MVP hiện hành, không tự bump
   hoặc migration. State thiếu version/khác version phải lỗi và giữ bytes gốc.
2. Unknown fields bị từ chối, kể cả nested config/task. Chỉ nhận các trường
   MVP đã dùng; State có required fields và optional child_pid/last_error/
   result_commit được mô tả rõ. Không thêm retry/budget giả khi chưa có consumer;
   giới hạn retry/call budget sẽ bổ sung theo schema ở REC/PROV tương ứng.
3. Mọi object/list/string/int kiểm tra đúng kiểu: bool không phải version,
   timeout/PID hợp lệ. Timeout integer 1..86400 giây; PID null hoặc integer >0;
   không coi PID hợp lệ là bằng chứng process ownership.
4. run_id là identifier an toàn `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` để tương thích
   state/fixture MVP; không separator, dot, drive, UNC hay traversal. UUID hex
   hiện tại là tập con hợp lệ. Không nhận run_id làm đường dẫn tự do.
5. Phase chỉ thuộc preparing/worker/verifying/committing/complete. Complete
   bắt buộc result_commit hợp lệ. Validate shape/field consistency tối thiểu;
   bảng transition, tree/test identity và commit reconciliation sâu thuộc REC-01.
6. Commit IDs phải đúng full hex OID theo format repo Git (SHA-1/SHA-256), không
   ép mọi repo là 40 ký tự. Chỉ đọc Git metadata khi cần, không mutate repo.
7. Argv nonempty list of nonempty strings, không NUL. specification nonempty.
   Allowed paths: exact relative file paths dùng `/`, không glob, empty segment,
   dot/dotdot, backslash, NUL, drive/UNC/ADS hoặc `.git`/`.omp` (case-insensitive).
   Không nhận directory prefix làm quyền ghi. Quy tắc symlink/junction/ignored
   files và Git tampering toàn diện vẫn SEC-03; không tuyên bố sandbox OS.
8. JSON hỏng, encoding lỗi, top-level không object, missing field, invalid
   field, unsupported version có thông báo phân biệt; không echo giá trị có
   secret/argv. Lỗi config/task là InputError; state sai từ runtime là
   OperationError; CLI nonzero, không traceback cho input dự kiến.

## Thứ tự validation và side effects

- run đọc/validate config, task và state đang có trước mọi mutation; resume/
  status validate state + nested config/task trước dùng run_id/phase/argv.
- Cho phép read-only filesystem/Git để xác định root/object format. Validation
  không được tạo .omp/lock, sửa checkpoint, tạo log/clone hoặc spawn Worker.
- Sau khi lấy lock, đọc/validate lại dữ liệu authoritative trong lock trước
  thực thi, tránh dùng snapshot cũ nếu input bị thay giữa preflight và lock.
  Đây là validation boundary, không xây lock/process ownership mới.
- Input/state sai giữ nguyên bytes, không bị advance catch ghi last_error
  đè lên dữ liệu không hợp lệ. status chỉ đọc, không chuẩn hóa lên đĩa.
- Giữ nguyên stop/timeout redaction FND-02, trình tự commit/retry hiện hành.

## Acceptance bắt buộc

- [x] AC1: validators có type hints, không side effects; required/optional/
  unknown fields và quy tắc version được tài liệu hóa rõ, không Any tràn lan.
- [x] AC2: tests missing/invalid/unsupported cho cả config/task/state; JSON
  hỏng, null/list/scalar, nested unknown, bool/int và timeout/PID boundaries.
- [x] AC3: state run_id/path/phase/commit lỗi không chạy Worker/test/Git mutation
  hoặc tạo runtime artifact; checkpoint giữ bytes gốc, CLI nonzero không traceback.
  Tests cần spy vào mutation/process boundaries, không chỉ assert message.
- [x] AC4: legacy config/task được nhận trong memory, source files không đổi;
  version lạ/state không version bị từ chối, không tự migration; init mới và
  examples tạo đúng schema. Test status chỉ đọc, không rewrite state.
- [x] AC5: có tests case-insensitive .GIT/.OMP, traversal, drive/UNC/ADS/glob/NUL;
  đường dẫn có space/Unicode hợp lệ không bị cấm nhầm. Git OID format được test
  bằng fixtures/mock metadata nếu Git cài đặt không hỗ trợ SHA-256 thực.
- [x] AC6: các ca hợp lệ vẫn run/resume/complete được; 23 test baseline FND-02
  giữ hành vi, không skip/nới expected, không mất redaction/cause chain.
- [x] AC7: regression lỗi cũ fail trước sửa/pass sau sửa; python tools/check.py
  và git diff --check đạt. Không đặt số test đích để thay acceptance.
- [x] AC8: handoff đủ base SHA/file list/hash code và test đổi, mapping AC1–AC8,
  lệnh/OS/Python/test count/exit, compatibility và checks chưa chạy. Docs khớp
  code. Không tự ACCEPTED/DONE hoặc đóng G1.

## Cách bàn giao và giới hạn

Đọc AGENTS/COORDINATION/CONTRIBUTING/CONTRACTS/SDD và task này. Chạy baseline,
ghi owner + IN_PROGRESS; làm regression rồi thay đổi tối thiểu. Nếu phát hiện
schema policy mâu thuẫn, báo điều phối, không tự chọn migration phá dữ liệu.
Không sửa containment, test vault, failover, scheduler, DB hay dashboard.
Không thêm dependency/runtime tools, không gọi AI thật hoặc spawn agent phụ.
Không commit/push/merge; cuối cùng tạo handoff, chuyển REVIEW, đăng prompt
review trực tiếp trong chat. Điều phối review, đóng task khi đạt và giao REC-01.
