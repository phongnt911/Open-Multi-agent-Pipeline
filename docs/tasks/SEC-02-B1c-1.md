# SEC-02-B1c-1 — journal và expectation cho fixture offline

- Trạng thái: REVIEW (local đạt, chờ CI Windows3.12/3.14 exact revision); giao bởi điều phối ngày 2026-11-02 theo yêu cầu tiếp tục của chủ dự án.
- Owner triển khai: Antigravity CLI tuần tự; reviewer/nghiệm thu: điều phối.
- Base: `84ed4f98e103881ce98c491875d2d5427a7cb791`, branch `codex/sec-02-b1c-journal`, worktree `.omp/sec02-b1c-journal`.
- Phụ thuộc: B1c-DESIGN/ADR-0011 ACCEPTED (PR #11); B1a/B1b DONE. Baseline 521 tests.
- Không giao B1c-2 execution, commit bridge, restart authority, B2, SEC-03/G4; G3 mở.

## Ownership và quyền

Ownership đã đổi theo review r2: điều phối nhận module, Antigravity stage3 chỉ tests/handoff. Sau stage3, điều phối nhận hiệu đính cả tests vì fixture/mutants và fault coverage vẫn chưa đạt; Antigravity từ đây chỉ review tĩnh, không sửa file. Điều phối thực thi mọi tests sau denied RunCommand, không mở rộng permissions.

Antigravity ban đầu được sửa/tạo CHỈ:
1. `omp_core/offline_journal.py`.
2. `tests/test_offline_journal.py`.
3. `docs/handoffs/SEC-02-B1c-1.md`.

Task, reviews, README/CONTRACTS/roadmap/B1 index do điều phối sở hữu. Không phải người duy nhất trong repo: giữ nguyên sửa đổi người khác; không sửa main, B1a/B1b, tests cũ, proof scripts, workflow, packaging/dependencies hoặc ADR đã accepted. Không commit/push/merge, không delegate, không mở rộng permissions, không provider thật hay sửa ACL/tài sản thật. Có thể chạy Python tests/check bằng argv trong worktree; không shell=True/eval/exec. Nếu bị chặn, báo action/target và tiếp tục phần độc lập.

## Hợp đồng cụ thể

ADR-0011 sections 3–4 là nguồn schema; section 7 là ranh giới gói. Không dựng orchestration scaffold. Module stdlib với type hints/docstrings; consumer thực là fixture file I/O và artifact từ B1b reader.

### Expectation

- Frozen `FixtureExpectation`: closed schema `omp.sec02.b1c.expectation.v1`, task_id safe ASCII ID, tests_run strict int 1..2^31-1, output_sha256 64 lowercase hex. Tạo object/from_dict đều validate; canonical UTF-8 JSON sorted keys, separators=(",", ":"), ensure_ascii=False, không newline; property/method digest SHA-256 rõ ràng.
- `verify_artifact(artifact, *, expectation, manifest) -> None`: chỉ deterministic fixture Oracle sau B1b reader, không là OS authority. Validate expectation/manifest binding (task_id và canonical digest == manifest.spec_hash), artifact run/task/nonce khớp manifest; status PASS, output str/hash đúng, tests_run strict int đúng expected, failures/errors strict int0. Không coerces bool thành int hoặc lấy expectation từ artifact/journal. Fail bằng lỗi boundary không echo payload.
- Tests phải dùng artifact hợp lệ từ `read_trusted_artifact` trên fixture thật (fake Job object được phép cho core tests); wrong output/count với schema/bindings hợp lệ phải bị Oracle reject.

### Journal

- API nhỏ: `create_journal(directory: Path, initial: Mapping[str, object])` trả live `OfflineJournal` context manager; `owner.append_snapshot(record)`; `inspect_journal(directory)` chỉ trả dữ liệu lịch sử/readonly copies. Chốt signatures/return types trong handoff. Không open/adopt/resume owner từ disk, không API native allocation/spawn/clear guard.
- Caller là trusted harness, chuẩn bị parent directory ngoài quyền Worker. Validate initial TRƯỚC tạo directory; directory phải mới, mkdir exclusive, không dùng exist_ok hoặc nhận production .omp/state.json. Existing directory giữ nguyên và refuse; không rewrite/repair/xóa evidence.
- Native Windows byte lock giữ suốt owner context lifetime; không tự thêm POSIX support. Có thể reuse primitive hiện có nếu không đổi primitive; không tạo framework. Owner.close idempotent, append sau close refuse; context exception/interrupt phải thả lock, không mask lỗi gốc.
- Snapshot closed fields và invariants theo ADR; revision0 preparing, reason/resources/observations null (profile allocation chỉ sau snapshot0). Canonical journal JSON cùng serialization rule expectation, max65536 bytes; filename revision format tối thiểu 8 decimal digits, không leading-zero aliases khác. revision strict 0..2^31-1; previous hash exact previous file bytes.
- Profile inventory null→known, không đổi known name/SID. PID null→positive chỉ worker; known PID không đổi sang PID khác hoặc mất nếu tree_stopped chưa true. Observations không được hạ true→false/null hay thay exit code đã biết. reason sticky; blocked cần reason; complete mọi bool true, exit0/reasonnull, chỉ sau cleaning; verifying PIDnull/exit0/tree_stoppedtrue. Cleaning không lỗi chỉ sau verifying/Oracle true; cleaning do lỗi giữ reason khác null. Terminal không có transition mới; exact last-record retry vẫn idempotent.
- append validate record trước side effect và revalidate toàn chain khi đang giữ lock; so với head digest/revision trong memory để chặn disk tampering/stale owner. Đúng next revision hoặc retry exact bytes của last record; identity/digests/OID không đổi. Không lấy journal observations làm live OS proof.
- Strict bounded read UTF-8/JSON; reject duplicate keys, nonfinite constants, unknown/missing fields/version, bool/int aliases, OID algorithm mismatch, invalid IDs/nonce, malformed/deep/oversize data, gaps/conflicts/invalid phase edges/hash chain. Schema chỉ cần nesting depth tối đa 3 object levels; reject sâu hơn, không phụ thuộc recursion limit CPython. Inspect không ghi, lock/create directories, spawn hay cleanup.
- Publication: own temp O_EXCL → full write/flush/fsync → Windows rename no-clobber → update head sau thành công. Rival exact final bytes idempotent; bytes khác refuse. Chỉ cleanup temp do lần gọi này đã tạo; giữ temp/evidence lạ. fsync/rename không được gọi là power-loss guarantee.
- Lỗi validation/I/O dùng lỗi boundary có message/code hữu hạn, cause sanitized; không raw payload/path/env/prompt/exception secrets kể cả traceback/chained cause/cleanup note. Giữ type interrupt KeyboardInterrupt/SystemExit, cleanup failure không che lỗi gốc. Không catch rồi bỏ qua hoặc assert validation production.

## Acceptance và kiểm chứng

- J1: Tests RED trước implementation (ImportError vì chưa có module không đủ: ghi các assertions về schema/Oracle/publication phải fail khi checks bị gỡ). Sau implementation có tests đủ success/negative và consumer thật.
- J2: Closed schemas/canonical digest/identity/phase/monotonic observations/PID/terminal; mọi edge, malformed/missing/unknown/version/type/UTF-8/depth/size và SHA1/SHA256 được kiểm.
- J3: File I/O append/inspect chain, exact-byte idempotence/no-clobber, stale/tampered/partial/gap/rival; bytes cũ và foreign temps giữ nguyên; inspect không mutation/OS operations.
- J4: Fault injection write/flush/fsync/rename, lock acquire/release/cleanup và interrupt; nguyên nhân chính/cleanup giữ đúng, secret-safe diagnostics; không complete giả sau I/O lỗi.
- J5: Windows thật có child process thử tranh byte lock trong khi owner sống và sau close; publication rival qua process/native I/O có bounded timeout và cleanup child. Không gọi đây là AppContainer proof.
- J6: Negative controls riêng gỡ chain check, phase guard, Oracle output/count comparison: mỗi mutant phải làm acceptance assertions fail; không chỉ assert function name đã gọi.
- J7: `python tools/check.py` toàn bộ, không skip/nới test cũ; Windows Python3.12 và3.14 CI, package/entrypoint smoke exact revision trước merge. Lỗi containment local từng quan sát ở PR11 chưa chứng minh fix; nếu tái hiện báo điều phối, không tiện sửa production khác scope.
- J8: Handoff ghi file, test command/count/result/RED/mutant evidence, lỗi/quyền/NOT RUN/limits. Bàn giao REVIEW; không tự DONE, không tự nhận B1c-2.

## Plan thực hiện tuần tự

1. Đọc required docs + code/tests B1b và lock liên quan, xác nhận baseline/ownership và điểm mơ hồ.
2. Viết acceptance tests rồi chứng minh RED; triển khai schema/Oracle trước, journal sau, không thêm abstraction ngoài API đã giao.
3. Chạy focused tests/fault/native/negative controls rồi full check. Ghi handoff REVIEW.
4. Điều phối đọc toàn diff và test assertions, chạy lại checks, trả findings cụ thể; chỉ nghiệm thu khi đủ evidence. Docs runtime contract/status do điều phối cập nhật.
