# OMP — Open Multi-agent Pipeline

MVP: một provider, một task, checkout riêng, kiểm thử và commit cục bộ.
Python 3.10+, Git, không cần thư viện Python lúc chạy.

## Sử dụng

```powershell
python -m pip install -e .
# Chuyển tới repo cần thực hiện task
omp init
# Xem và sửa .omp/config.json trước khi chạy
omp run C:\path\to\task.json
omp status
omp resume
```

Repo mục tiêu phải có commit, working tree sạch và bỏ qua `.omp/` trong
`.gitignore`. Provider mặc định là Codex CLI đã đăng nhập, nhận prompt qua
stdin. Config dùng `schema_version`, `worker_argv`, `test_argv`,
`timeout_seconds`; không ghép lệnh shell. Chuẩn bị dependency và tests trong
repo trước khi chạy.

Task mẫu: `examples/task.json`. Config/task mới dùng `schema_version: 1`.
Config/task cũ không có version được nhận như legacy v0 và chuẩn hóa trong bộ
nhớ; file nguồn không bị ghi lại. Field lạ, sai kiểu, version không hỗ trợ,
argv rỗng/NUL, timeout ngoài `1..86400` hoặc allowed path không an toàn bị từ
chối trước side effect. File cho phép phải là đường dẫn tương đối chính xác
dùng `/`, không glob/traversal/backslash/drive/UNC/ADS và không có segment
`.git`/`.omp` (không phân biệt hoa thường); đường dẫn có space hoặc Unicode vẫn
hợp lệ. Không cho phép Worker sửa tests hoặc config runner. `omp init` sinh
lệnh test kiểm tra tường minh: 0 test hoặc test lỗi đều trả nonzero, kể cả khi
chạy với `PYTHONOPTIMIZE=1`; exit code 0 của lệnh rỗng không chứng minh tính
đúng đắn. CLI phân biệt lỗi input/config (`InputError`) với lỗi
operation/process (`OperationError`), in `OMP: ...`, trả nonzero và không in
traceback hay argv Worker cho lỗi dự kiến. Argv chứa NUL bị từ chối trước side
effect; executable không tồn tại được báo `OperationError` có cause. Timeout
của Worker/cleanup được thay bằng label an toàn, không đưa argv vào stderr hay
`last_error`. `examples/legacy-*` là tư liệu thiết kế cũ, không phải schema chạy.

Kết quả nằm trong `.omp/runs/<run_id>/checkout`, logs nằm cạnh checkout.
OMP bỏ remote và không tự push hoặc áp dụng thay đổi vào repo nguồn.
Xem kết quả bằng `git -C .omp/runs/<run_id>/checkout show`; sau khi duyệt,
có thể fetch commit từ clone rồi cherry-pick thủ công.

## Phục hồi và giới hạn

- Checkpoint ghi file tạm, flush và replace; lỗi cleanup không che lỗi gốc (REC-02).
  Khóa OS ngăn hai bộ điều phối cùng chạy trong một repo. Commit có run ID và marker
  để đối soát khi resume; ma trận gián đoạn (Crash Recovery Matrix) được mô tả tại ADR-0003.
- State mới dùng `schema_version: 2` cùng `run_id`, `phase`, `base_commit`,
  `config`, `task`; `verification_evidence`, `commit_intent`, `completion_kind`,
  `child_pid`, `last_error`, `result_commit` được kiểm tra chặt chẽ. `run_id` khớp
  `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; commit ID là full hex 40 hoặc 64 ký tự
  theo object format của repo; Git không hỗ trợ `--show-object-format` hoặc
  trả metadata lỗi bị từ chối, không đoán SHA-1. State thiếu/sai version, field
  lạ, phase lạ hoặc dữ liệu sai kiểu bị từ chối; lỗi parser do integer vượt
  giới hạn hoặc JSON lồng quá sâu được bọc thành lỗi boundary, giữ cause,
  không traceback và không sửa bytes checkpoint. Checkpoint v1 cũ được hỗ trợ
  đọc cho `status`, nhưng `resume` trên state v1 dở dang bị chặn trước mutation.
- Candidate tree OID được tính độc lập qua index Git cô lập trong clone,
  bảo đảm nhận diện chính xác sửa đổi, thêm mới và xóa file mà không làm bẩn repo nguồn.
- Bằng chứng test (`verification_evidence`) gắn chặt candidate tree, SHA-256 test
  command và exit code 0; test tự sửa candidate file làm evidence bị hủy bỏ.
- Commit intent ghi nhận trước khi commit; crash recovery đối soát HEAD, parent,
  marker và tree OID để hoàn tất idempotent, ngăn ngừa commit trùng lặp.
- No-op task hoàn tất tường minh với `completion_kind = no_change` và giữ nguyên
  `base_commit` mà không tạo commit rỗng.
- `run`/`resume` validate config, task và state trước khi tạo `.omp/lock` rồi
  đọc/validate lại sau khi lấy lock trước khi clone, chạy Worker/test hoặc ghi
  checkpoint. `status` chỉ đọc và validate, không chuẩn hóa hay ghi state.
- Timeout/Ctrl+C cố dừng cây tiến trình. Sau crash cứng, PID còn lưu sẽ chặn
  resume: phải xác minh tiến trình và các con đã dừng rồi đặt `child_pid`
  thành `null` trong state. Không tự nhận diện tiến trình qua PID tái sử dụng.
- Worker dở được gọi lại trong phiên mới với thay đổi hiện tại. Test thất bại
  giữ nguyên checkout; resume chạy lại test. Sau khi kiểm tra, có thể đổi
  `phase` thành `worker` để yêu cầu sửa tiếp. Chưa tự retry/failover.
- Clone bị gián đoạn tại `preparing` cần lưu trữ run và di chuyển state cũ
  ra ngoài trước khi bắt đầu run mới; chưa tự phục hồi bước clone.
- **Clone không phải sandbox OS.** MVP dành cho Worker hợp tác, chạy với
  sandbox của provider. Cùng tài khoản OS vẫn có thể truy cập repo nguồn,
  runtime, Git metadata, file ignored và môi trường ngoài repo. Kiểm tra
  whitelist sau thực thi phát hiện sai lệch, không ngăn tác động trước đó.
- Chưa có immutable vault hoặc test runner cách ly. Không dùng cho Worker
  đối kháng hoặc DB production. Không có migration, cài package tự động,
  submodule/LFS, dashboard hoặc push. Không lưu secrets trong Git.

## Kiểm thử

```powershell
python tools/check.py
```

Tests dùng provider giả cục bộ, không gọi AI hay tốn quota. Xem `docs/SDD.md`
cho thiết kế hiện hành và `docs/SRS.md` cho tầm nhìn dài hạn.

## Phát triển dự án

AI và người đóng góp đọc [AGENTS.md](AGENTS.md) trước khi sửa code.
[Lộ trình](docs/ROADMAP.md) quy định thứ tự gate và acceptance;
[quy trình đóng góp](docs/CONTRIBUTING.md) quy định review/kiểm chứng;
[contracts](docs/CONTRACTS.md) xác định bất biến và khoảng trống hiện tại.
Task và handoff dùng mẫu trong `docs/templates/`. CI kiểm tra Windows/Linux
trên Python 3.10/3.12, không gọi AI thật hoặc tự publish.

[Quy trình điều phối/review AI](docs/COORDINATION.md) quy định quyền giao việc
và nghiệm thu. FND-01/FND-02, G0, [DAT-01](docs/tasks/DAT-01.md) và
[REC-01](docs/tasks/REC-01.md) đã được triển khai; tiếp theo [REC-02](docs/tasks/REC-02.md).
G1 chưa đóng. AI thực thi bàn giao để reviewer nghiệm thu, không
tự push main hoặc nhận tiếp task khác.
