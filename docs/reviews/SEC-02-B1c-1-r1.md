# SEC-02-B1c-1 — review r1, tests trước implementation

- Verdict CHANGES_REQUIRED; chưa có module nên chưa nghiệm thu tính năng. Tests stage1 không đủ J1–J6 dù response nói complete.
- Ag y conversation `ab956658-6025-4d95-b3c7-69494a8dae25`; stage1 archive `2102939362d81c2e`. Đã xác định command bị từ chối lượt trước: `Get-ChildItem omp_core, tests` qua RunCommand trong worktree mới. Không retry hoặc mở rộng permissions.
- RED điều phối: `python .omp/b1c1-check.py focused`, exit1; log `.omp/b1c1-focused-1793606999544200100.log`; ImportError vì module chưa tồn tại. Đây chỉ RED setup, KHÔNG chứng minh guards bắt lỗi; cần negative controls chạy assertions thật sau implementation.

| ID | Mức | Vị trí/trigger | Thiếu và cách xác minh |
| --- | --- | --- | --- |
| J-R1 | P2 | Tests 52–259: DummyManifest, không có B1b reader import/call | Thiếu consumer trực tiếp J1. Dùng TrustedManifest thật + read_trusted_artifact trên file fixture, fake Job được phép; reader phải nhận sai-output/count valid shape, rồi Oracle refuse; good path pass |
| J-R2 | P2 | Tests 726–785 | mutant_verify định nghĩa nhưng không gọi; gap test chỉ là ordinary rejection; thiếu phase mutant. Mỗi check chain/phase/Oracle cần bị vô hiệu hóa có chủ đích rồi assertions acceptance thực sự fail, ngoài test runner ghi expected failure; test meta phải fail nếu mutant không được phát hiện |
| J-R3 | P2 | Tests 687–720 | Chỉ fsync fault; secret test có thể pass khi không raise, không xem traceback/cause; chưa lock/release/write/flush/rename/cleanup/interrupt boundary. Thêm table-driven failure tests, assertRaises và trace/cause không lộ sentinel, head/evidence không đổi, primary error không bị cleanup mask |
| J-R4 | P2 | Tests 334–348, 583–594, 791–815 | Child import module trước helper entrypoint, script path chỉ có tests directory; subprocess không cwd; rival chỉ same-revision API call, không race publication. Fixture bounded child với cwd/import bootstrap đúng, actual lock contention trước/sau close và child tạo rival target giữa temp-write/rename, exact/different bytes đều được kiểm |
| J-R5 | P2 | Tests 351–536 | Thiếu closed record/nested schema matrix, missing/bool/version/OID SHA256/nonce, deep/nonfinite/UTF8/gap/hashchain/stale/identity/PID replacement/sticky reason/phase edge negatives. Thêm subTests guards thực tế, không giảm AC |
| J-R6 | P3 | Tests 274–301, 358–384, 544–570, 656–682, 753–775 | Fixture record lặp lại nhiều lần. Dùng một builder và deepcopy/helper để tránh shared nested mutation; không thêm framework. Không cần một error subclass/inspection flag chỉ vì test đã tự phát minh API |

Cho phép chuyển stage2: sửa thiếu sót tests và triển khai đúng task; Antigravity chỉ file tools ba file ownership, không command/tests/git. Điều phối chạy focused/fault/native/mutants/full checks và gửi findings. Không coi stage1 hoặc static response là PASS runtime.
