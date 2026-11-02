# ADR-0011 — B1c: journal và orchestration cho fixture offline

- Ngày 2026-11-02; base `12f3e625d73979e7a8592298f2e8fb7cba6b7243`.
- ACCEPTED cho thiết kế fixture offline theo review r3; mọi API/format mới dưới đây CHƯA TRIỂN KHAI.
- B1a/B1b DONE; whole B1/B1c chưa DONE. G3 mở; B2/SEC-03/G4, provider/credentials/network thật không được giao.

## 1. Hiện trạng và lựa chọn (D1–D3)

`main.main → validate/preflight → lock/revalidate → advance` clone checkout, rồi `execute` save PID trước resume. PID chỉ clear khi `_tree_stopped`; `process.close` còn là bước riêng. Verifying dùng scope/candidate tree trước-sau test, evidence/intent rồi đối soát commit/no-change. Recover/abort giữ checkpoint bytes; attestation/PID absent không là tree proof.

| Hướng | Đánh đổi | Quyết định |
| --- | --- | --- |
| Đổi main/config/state ngay | Ghép parser, lifecycle, activation; chưa đủ end-to-end proof | Không chọn |
| Seam offline với fixture consumer, chia journal/Oracle rồi execution | Kiểm từng boundary, default/parser không đổi | Chọn |
| VM/service/plugin hoặc import tools.proofs | Dependency/framework và giả định proof vào runtime | Ngoài scope |

B1c không cho CLI/main gọi seam mới, không fallback cooperative, không reserve config v2/state v3/evidence v2.

## 2. Compatibility (D4)

| Input | Read/status | Resume/recover | Write/abort |
| --- | --- | --- | --- |
| Config/task thiếu version (legacy v0) | Normalize copy trong memory thành v1 | Legacy workflow giữ nguyên | Không rewrite input; init vẫn config v1 |
| Config/task v1 | Giữ nguyên | Giữ nguyên | Giữ nguyên |
| Explicit version 0/2, bool/string hoặc unknown field | Reject, không nhầm missing-version với explicit 0 | Reject | Không migrate |
| State v1 | Validate/status read-only | Resume refuse; recover incomplete refuse, complete không PID audit-only | Incomplete không PID có thể archive sidecar; complete audit/no-change; có PID refuse |
| State v2 | Hiện trạng | Legacy pipeline hiện có | Evidence/intent bắt buộc CHỈ committing/complete |
| State thiếu/lạ version, bool, unknown field hoặc config/task lồng không v1 | Reject | Refuse | Giữ bytes cũ |
| Journal mới | Inspect riêng, không phải CLI status | Không có disk-resume API | Append từ live owner, không ghi .omp/state.json |

Evidence hiện tại không có version riêng, lồng trong state v2. CLI hiện hành reject journal theo type/version/field validators; không hứa error code mới hoặc hỗ trợ format chỉ vì ADR.

## 3. Trusted expectation/Oracle (D5)

Đề xuất immutable `FixtureExpectation`, do trusted harness approve TRƯỚC launch:
- Closed fields: schema_version exact `omp.sec02.b1c.expectation.v1`, task_id (safe ID như run_id), tests_run (strict int 1..2^31-1), output_sha256 (64 hex lowercase).
- Canonical bytes: UTF-8 JSON sorted keys, separators (",", ":"), ensure_ascii=False, không newline; SHA-256 phải bằng manifest.spec_hash.
- B1b reader kiểm handle/schema/run/task/nonce/candidate/invocation trước Oracle. Oracle bắt artifact có status PASS, tests_run đúng expected, failures/errors strict int0 và output str có UTF-8 SHA-256 đúng expected.
- Không lấy expectation từ Worker, artifact hay journal. Negative control dùng artifact ĐÚNG schema/bindings nhưng SAI output/count; không chỉ thêm field lạ.
- Reader success, JSON PASS, exit0 hoặc spec_hash đơn lẻ KHÔNG phải authority. Đây chỉ deterministic fixture rule, không chứng minh arbitrary tests/plugin/Worker đối kháng đã thực thi kiểm thử.

## 4. Journal v1: observations append-only (D4–D6)

Không dùng no-clobber cho một file cần overwrite nhiều lần. Mỗi snapshot là revision bất biến tại control directory do fixture tạo mới độc quyền: `controller/journal/00000000.json`, rồi 00000001.json. Control directory ngoài quyền Worker, tách production state/L2 checkout.

Closed field set bắt buộc trong mọi record:
| Field | Kiểu / ràng buộc |
| --- | --- |
| schema_version | exact string omp.sec02.b1c.journal.v1 |
| revision | strict int 0..2^31-1, bằng số filename |
| previous_sha256 | null ở revision0; nếu không là SHA-256 exact bytes record trước |
| run_id, task_id | ASCII [A-Za-z0-9][A-Za-z0-9_-]{0,63} |
| nonce | 32 hex lowercase, do fixture approve, khớp manifest |
| phase | preparing, worker, verifying, cleaning, complete, blocked |
| manifest_sha256 | 64 hex của manifest bytes trusted controller approve/publish |
| expectation_sha256 | 64 hex, bằng approved manifest.spec_hash |
| tree_algorithm, candidate_tree | sha1/sha256 và 40/64 hex lowercase, khớp manifest/tree thực trước-sau execution |
| candidate_sha256 | 64 hex, khớp approved worker.py |
| resources | closed object profile_name:str-or-null, profile_sid:str-or-null, child_pid:strict positive int-or-null |
| observations | closed object exit_code:strict signed32 int-or-null; tree_stopped, oracle_pass, process_closed, profile_closed:bool-or-null |
| reason_code | null hoặc allocation_failed, journal_failed, start_failed, resume_failed, worker_failed, stop_unknown, reader_failed, oracle_mismatch, process_close_failed, profile_close_failed, interrupted |

Inventory strings và observation bools là dữ liệu lịch sử, KHÔNG ownership/OS proof. Không serialize handles, prompt, raw argv/env hoặc exception payload.
- Revision0 preparing, previous/PID/observations null. preparing có thể append inventory khi allocation thành công.
- worker cho PID null TRƯỚC start/save, dương chỉ sau start. Worker/PID-null vẫn là intent UNPROVEN, không được suy chưa có process để retry.
- verifying: PID null, exit0 và tree_stopped true từ live Job query; oracle_pass chỉ true sau Oracle.
- cleaning từ verifying sau Oracle, hoặc từ preparing/worker/verifying để xử lý lỗi. Failure reason sticky, không biến thành happy path.
- complete chỉ từ cleaning: reason null, exit0 và mọi observation bool true. Chỉ complete FIXTURE verification, không OMP commit/run complete; không có result_commit/commit_intent trong format này.
- blocked terminal, observations có thể null/false. Không có edge từ complete/blocked về worker.
- Edges: preparing→preparing/worker/cleaning/blocked; worker→worker/verifying/cleaning/blocked; verifying→verifying/cleaning/blocked; cleaning→cleaning/complete/blocked.
- Identity/digests/OID bất biến giữa revisions; inventory null→known; PID clear chỉ sau live tree-stop observation. Failure path không complete.
- Mỗi record max64KiB, UTF-8 strict, JSON top-level object. Duplicate keys, NaN/Infinity, unknown field/version, bool/int alias, malformed/deep/oversize JSON refuse, không echo payload.
- Validate trước side effect, OS lock một controller/fixture rồi revalidate. Temp O_EXCL → flush/fsync → rename no-clobber tới revision mới. Exact existing bytes idempotent; bytes khác/gap/chain mismatch refuse. Không overwrite/repair record cũ hoặc xóa temp/evidence lạ.
- Chain chỉ kiểm inconsistency so với trusted context, không là chữ ký/immutable vault; không claim power-loss từ fsync/rename. Inspect không trả live owner hoặc quyền resume.

## 5. Ownership và seam execution đề xuất B1c-2 (D3/D5)

API CHƯA CÓ: `execute_offline_run(*, layout: OwnedL2Layout, profile: WindowsAppContainerProfile, manifest: TrustedManifest, expectation: FixtureExpectation, journal: OfflineJournal) -> OfflineRunResult`.

Trusted harness tạo control directory/lock và record preparing TRƯỚC allocation; tạo profile rồi `OwnedL2Layout.create(parent_dir, run_id, user_sid=controller_sid, container_sid=profile.sid)`. Runtime là copy disposable, fixture grant RX đúng profile SID; không sửa runtime cài đặt/repo thật.
- Preflight kiểm types/identity, profile.closed false, layout.run_id/container_sid khớp live owner objects, approved manifest/expectation và script worker.py. Không dùng property layout.is_cleaned (không có).
- Profile ownership transfer CHỈ sau preflight thành công; trước đó harness vẫn owns. Returned process thuộc callee. Layout/runtime/control directory BORROW, không callee cleanup/delete; giữ journal/manifest/log/artifact.
- Callee không create profile/layout lần hai, không dựng owner từ JSON. WindowsJobProcess.start giữ open/binding guards của B1a.
- Candidate fixture là worker.py đã approve; argv exact approved runtime + -I -P + worker.py; env qua build_trusted_environment. Không nhận expectation/env/provider từ nguồn untrusted.
- Happy path: worker intent → start suspended AppContainer/Job → PID append durable → resume/wait exit0 và Job empty → verifying → reader KHI JOB HANDLE CÒN QUERY ĐƯỢC → Oracle → cleaning → process.close → profile.close → complete append.
- Không tạo Git commit trong seam. Bridge commit/production state thuộc gói sau.

## 6. Fault/restart matrix (D6)

| Boundary | Live owner xử lý | Durable/restart |
| --- | --- | --- |
| Profile allocation trước save inventory | B1a partial-allocation guard, cleanup đúng created resource | preparing unproven; không adopt/delete theo tên/SID |
| Layout/ACL/manifest thất bại | Harness dừng setup, close owned profile, không launch | Giữ lỗi/evidence, không fallback |
| Preparing/worker-intent append lỗi | Không start, cleanup đúng transferred ownership | Record cũ giữ nguyên, không nói đã save |
| Start lỗi/interrupt sau native allocation | B1a bounded cleanup/poison, không resume | Có thể chưa có PID durable |
| Save PID lỗi sau start | Terminate/wait10s bằng live object, giữ live PID trong memory | Không hứa PID đã lưu; worker intent unproven |
| Resume/wait/nonzero exit | Stop10s; không đọc kết quả như success | cleaning/blocked nếu ghi được; không complete |
| Stop/query không chứng minh Job empty | Giữ poison/owner guards, không replacement/profile close | Unknown kể cả PID absent |
| Reader/Oracle lỗi sau tree stop | Close process rồi owned profile nếu an toàn | Giữ failure/evidence, không complete |
| process.close lỗi | Sticky failure/binding, không profile.close khi unsafe | blocked/unknown, không double-close mù |
| profile.close lỗi | Giữ failed owner/diagnostic | Không complete/replacement |
| Append observation/complete lỗi | Cleanup live resources best-effort, giữ lỗi gốc | Không success từ record chưa durable, không rerun Worker |
| Owner death/restart ở mọi gap | Không còn live Job context/ownership proof | Inspect only; refuse resume/complete/replacement/adopt/delete |

Stop bound10s, không thêm retry/thread/failover. Interrupt giữ KeyboardInterrupt/SystemExit; sanitize cause/context và ghi cleanup note, không mask lỗi gốc. Callee không xóa evidence để unblock. Harness teardown chỉ trên test fixture owned sau khi lưu observations, không phải production recovery. Legacy --confirm-child-stopped không áp dụng journal; cần native proof/gói riêng cho restart authority.

## 7. Gói đầu tiên (D7)

B1c-1 ĐỀ XUẤT: journal + expectation core; không gộp native execution/recovery.
- Ownership: omp_core/offline_journal.py, tests/test_offline_journal.py, task/handoff riêng; stdlib only. Không sửa baseline/main/B1a/B1b/workflow/dependencies.
- API dự kiến: create_journal cho fixture mới, append_snapshot từ live owner, inspect_journal read-only; FixtureExpectation validate/digest và deterministic artifact comparison. Inspect không authorise native operations.
- Consumer trực tiếp: fixture tests tạo file journal thật, append/inspect snapshots và compare artifact từ B1b reader. Không registry/interface/module scaffold.
- AC: closed typed format; phase/chain/identity guards; no-clobber/idempotent publication; inspect malformed/stale giữ bytes; valid-shape wrong-output/count reject; inspect không spawn/clear guard/resume.
- Tests: mọi edge/missing/unknown/bool/version/nonce/algorithm; OS lock contention; write/flush/fsync/rename/rival failure; strict JSON/UTF-8/size; interrupt và secret-safe diagnostics. Bỏ chain/phase/expectation checks từng mutant phải bị assertions bắt.
- Windows Python3.12/3.14 + tools/check.py, baseline byte comparison. Native I/O/lock/race observations không được gọi là AppContainer/process proof mới.
- Chỉ giao implementation sau design ACCEPTED và work item cụ thể. B1c-2 execution/native matrix, B1c-3 bridge commit/production compatibility, recovery authority và B2 chưa giao.

## 8. Limits (D8)

Chưa có runtime implementation/native proof mới. B0/B1a/B1b evidence là lịch sử, không thay B1c checks. Network/credentials/arbitrary tests/plugins/owner-crash/power-loss/real assets vẫn NOT PROVEN; G3 mở. Không suy production support config/state/evidence mới từ ADR.
