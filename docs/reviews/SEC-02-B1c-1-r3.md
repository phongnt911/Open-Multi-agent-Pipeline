# SEC-02-B1c-1 — review r3 của điều phối

- Verdict: BLOCKED chờ bằng chứng Windows Python3.14 và CI exact revision; local implementation không còn finding chặn. Task REVIEW, chưa DONE.
- Base `84ed4f98e103881ce98c491875d2d5427a7cb791`; branch `codex/sec-02-b1c-journal`.
- Scope: một module journal/expectation, một file tests mới và hồ sơ hợp đồng/giao việc/review. Main/CLI/state/B1a/B1b/tests cũ/workflow/dependencies không đổi. README sửa link G1-CLOSURE không có trên base thành ROADMAP đã có; không nhập tài liệu dirty lịch sử.

## Review và hiệu đính

Điều phối đọc toàn bộ draft module/tests, findings r1/r2, viết lại phần production và bổ sung tests sau các lượt giao Antigravity. Không lấy status SUCCESS hoặc claims coverage làm evidence. Snapshot/Oracle/cleanup hiện tại dùng stdlib, không plugin/registry/scaffold, không import proof scripts.

Antigravity review tĩnh ở conversation mới `ebacc6d3-60db-4120-b170-44dc6f1877c7`; lượt đầu response rỗng do print_timeout (`d56244262dfddda7`) được coi chưa hoàn thành, resume đúng conversation. Response cuối archive `5f2f4f573d19958e` không có finding chặn; STATIC-ONLY/NOT RUN. Review nói giữ stream sau unlock lỗi: thực tế nếu close thành công thì OS handle đã đóng và reference được bỏ; chỉ giữ reference khi stream chưa đóng. Không suy báo cáo này thành native proof.

| AC | Evidence hiện tại |
| --- | --- |
| J1 | RED setup ImportError chỉ là bước đầu; sau đó 4 probes thật bắt fake cleaning, oversize write, unhashable phase và echo input trên draft; hiệu đính đạt. Các regressions đã có trong tests mới |
| J2 | Closed/typed schema, canonical bytes/digest; missing/nested/unknown/bool/nonce/OID/algorithm; matrix 36 phase edges, profile inventory/PID/reason/observations/terminal và immutable expectation |
| J3 | File append/inspect thực; memory head đối chiếu exact-byte chain, stale/altered bytes/gaps/aliases refuse; read 65537-byte bound; no mutation/no disk-resume authority; input mapping snapshot không bị thay sau publication |
| J4 | Stream write/short-write/flush/close, open/fdopen/fsync/rename, lock/unlock/cleanup-unlink và interrupt; fd được đóng; cause/context/notes/filename không echo input; primary không bị cleanup mask |
| J5 | Windows child tranh byte lock khi owner sống/sau close; unlock lỗi vẫn close handle; child tạo target giữa fsync/rename, exact bytes idempotent, khác bytes giữ nguyên và refuse |
| J6 | Cùng acceptance assertions qua real guard và 4 mutants: chain helper, phase table, Oracle bỏ output hoặc count. Mỗi mutant thật gây AssertionError theo kỳ vọng, không chỉ định nghĩa hàm rồi bỏ đó |
| J7 | Local Windows Python3.12.7 source checks + 553 tests OK; Windows3.12/3.14 CI exact revision và packaging/smoke PENDING |
| J8 | Handoff gốc giữ lịch sử với amendment; giới hạn/chưa chạy ghi rõ; original checkout bảo toàn |

## Lệnh và kết quả

- `python .omp/b1c1-check.py focused`: draft stage2 13/errors2, log `b1c1-focused-1793607413712051400.log`; probes độc lập4 có failures3/errors1. Không bịa PASS hoặc nới tests để đạt.
- Focused30 phát hiện double-unlock sau failed stream close; log `b1c1-focused-1793608216101966300.log`. Theo dõi lock-held riêng sửa lỗi, giữ failed handle để retry.
- Focused32 RED PID `2**31` ở `b1c1-focused-1793608493001826400.log`; bỏ signed-int ceiling không có trong ADR, giữ positive strict int (exit_code vẫn signed32).
- Full check qua runner gọi nguyên `tools/check.py.main()`: 551 OK/101.851s, rồi sau delta PID/Oracle 553 OK/99.816s, log `b1c1-full-1793608532211013600.log`. Lỗi containment baseline từng ghi PR11 không tái hiện ở các lượt này; không tuyên bố đã sửa lỗi cũ.
- Sau bổ sung assertions existing-directory/pre-I/O, valid-shape identity/profile inventory và foreign temp collision, focused32 OK/2.547s, log `b1c1-focused-1793608716891779600.log`; runtime không đổi sau full553. CI phải kiểm đúng bản tests cuối.
- `python .omp/b1c1-doc-check.py`: source/local Markdown links pass sau sửa link README cũ; `python .omp/b1c1-preserve.py`: 180 original files không drift, original index empty, không file ngoài ownership. Git diff whitespace/scope check trước commit.
- Test count gồm 521 baseline + 32 mới; subTests không được đếm thành test riêng. Không skip/nới expected test cũ.

## Giới hạn và bước tiếp theo

Chỉ deterministic fixture Oracle và observations journal. Trusted harness sở hữu parent/control directory ngoài quyền Worker; module không cấp ACL hoặc bảo vệ real assets. Hash chain không chữ ký/immutable vault; inspection không ownership/OS proof. Native I/O/lock/race tests không AppContainer/owner-crash/power-loss proof. Local Python3.14/B1c-2 proof: NOT RUN. G3 mở; B1c-2 execution/bridge/recovery authority, B2/SEC-03/G4 chưa giao. Chỉ nghiệm thu sau CI Windows hai phiên bản, rồi tích hợp đúng head đã kiểm.
