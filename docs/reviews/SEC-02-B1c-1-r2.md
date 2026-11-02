# SEC-02-B1c-1 — review r2 implementation

- CHANGES_REQUIRED; stage2 archive `472c92acb829f9f3`, response đầy đủ nhưng không có kiểm thử bởi agent.
- Điều phối đọc toàn bộ module 793 dòng và tests 584 dòng. Focused chạy 13 tests, errors=2, log `.omp/b1c1-focused-1793607413712051400.log`: fsync lỗi raw OSError; rival fixture prewrites target trước revalidation, chưa tạo race giữa write/rename.
- Probe độc lập `.omp/b1c1-review-probes.py`: 4 tests, failures=3/errors=1. Bằng chứng RED thực cho hành vi, không còn chỉ ImportError.

| ID | Mức | Trigger / vị trí module stage2 | Yêu cầu |
| --- | --- | --- | --- |
| J2-R1 | P1 | 458–529: preparing→cleaning với reasonnull và observations alltrue được nhận; PID mới ngoài worker, complete còn PID cũng có thể qua | Guard happy cleaning chỉ sau verifying/Oracle, complete PIDnull, PID chỉ allocate worker, sticky first reason; thêm regression public API |
| J2-R2 | P2 | 139–189/541–583/703: không size-check khi write, inspect read_bytes không bound, regex chỉ 8digits và bỏ qua alias JSON; deep JSON RecursionError chưa bọc | Reject >65536 trước I/O, read(limit+1), canonical filename 8..10 digits tới strictintmax, refuse alias/gap/invalid/deep/UTF8/nonfinite, không mutate disk |
| J2-R3 | P1 | 82–116/148/207–213/364: heuristic regex vẫn echo unknown keys/values/path; phase list gây TypeError; raw I/O/cause còn lộ | Constant boundary messages, sanitize full cause/context/notes/filename/JSON/Unicode details; typed checks trước membership; probe unknown opaque sentinel phải pass |
| J2-R4 | P1 | 646–664/706–791: swallow unlock/close/unlink, fd/lock allocation ngoài try, không check short-write, exact rival temp không cleanup | Cleanup có ownership và preserve primary/interrupt; không false close/complete, fault write/flush/fsync/rename/open/fdopen/lock/unlock/close/unlink/native lock release; chỉ xóa own temp |
| J2-R5 | P2 | FixtureExpectation __slots__ + cached digest vẫn gán private fields bình thường; manifest Any/hasattr | Dùng frozen stdlib dataclass và TrustedManifest type/binding, không custom immutable boilerplate; Oracle surrogate output là boundary error |
| J2-R6 | P2 | Tests 447–477/490–554: thiếu write/flush/cleanup/lock faults, secret test try-only, không chain/count mutant, phase mutant bỏ toànvalidator | assertRaises + non-pattern opaque sentinel, full traceback/cause; mutate riêng link/phase/Oracle checks và chạy cùng acceptance assertions để thấy expected failure thật, không test chỉ minh họa mutant passes |

## Đổi ownership để tiến triển

Điều phối nhận sở hữu duy nhất `omp_core/offline_journal.py` từ đây; Antigravity KHÔNG sửa module nữa. Antigravity chỉ sửa `tests/test_offline_journal.py` và handoff. Điều phối giữ API public đã dùng, bổ sung helper private `_check_chain_link` (previous record/revision/hash) cho negative control; phase edge qua `VALID_PHASE_TRANSITIONS`. Không patch bỏ toànvalidator rồi gọi đó là mutant riêng phase.

Rival fixture phải tạo target đúng giữa fsync và os.rename (patch os.rename wrapper gọi bounded child helper tạo file rồi gọi original rename). Exact/different cases dùng journal mới tách biệt. Không nới revalidation nhận stale owner để chữa test sai. Child argv/cwd/timeout, không shell, không grandchild.

Antigravity ghi NOT RUN cho tests; điều phối chạy focused/probes/native/full rồi review lại. Không scope B1c-2/B2/SEC03/G4, không thay code/test cũ.
