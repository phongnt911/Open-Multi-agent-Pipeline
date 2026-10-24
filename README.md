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
# Sau crash có child_pid cần xử lý, dùng digest bytes thô của checkpoint:
omp recover <run_id> --state-sha256 <sha256>
omp abort <run_id> --state-sha256 <sha256>
```

`--state-sha256` là SHA-256 của đúng bytes `.omp/state.json`, không phải JSON
đã normalize. Có thể lấy read-only bằng
`python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('.omp/state.json').read_bytes()).hexdigest())"`.

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
- Windows: mỗi Worker/test chạy trong Job Object riêng với
  `KILL_ON_JOB_CLOSE`; process được tạo suspended với job-list nên containment
  có hiệu lực trước khi resume, không có gap create/attach. `child_pid` được
  save trước resume. Owner bị kill cứng làm OS đóng job handle và dừng cả cây;
  timeout/Ctrl+C/root exit còn con bị terminate có giới hạn. Chỉ xóa
  `child_pid` khi job đã rỗng; cleanup không chứng minh được thì giữ PID và
  fail-closed. Không fallback sang spawn không containment.
- POSIX giữ process group + SIGKILL. Sau crash cứng, PID còn lưu sẽ chặn
  resume. `recover` kiểm tra run ID, digest bytes checkpoint, lock, phase và
  checkout; mặc định không kill và không clear `child_pid`. Nếu PID đã absent,
  người vận hành phải tự xác minh PID và toàn bộ cây tiến trình đã dừng, rồi
  mới chạy `recover --confirm-child-stopped`. Lệnh chỉ clear `child_pid`, lưu
  audit `operator_attestation_only`; đây không phải bằng chứng OS và không tự
  chạy Worker. PID còn sống/không xác định bị refuse.
- `abort` giữ nguyên checkpoint, clone, logs, checkout và provenance; nó chỉ
  archive exact bytes state vào `.omp/runs/<run_id>/aborted-state.json`, ghi
  marker `aborted.json` và audit. `abort` idempotent, không kill và refuse nếu
  checkpoint còn `child_pid` (xử lý `recover` trước). Run đã abort bị
  `resume`/`recover` từ chối; `run` chỉ được bắt đầu run mới khi marker và
  archive khớp đúng bytes state cũ và `child_pid` là null.
- Mọi `run`/`resume`/`recover`/`abort` dùng cùng OS lock; audit nằm dưới
  `.omp/audit/<run_id>/` và không chứa config, task, argv, prompt, log hoặc
  token. Lỗi audit/checkpoint không được báo success; xem ADR-0005.
- Worker dở được gọi lại trong phiên mới với thay đổi hiện tại. Test thất bại
  giữ nguyên checkout; resume chạy lại test. Sau khi kiểm tra, có thể đổi
  `phase` thành `worker` để yêu cầu sửa tiếp. Chưa tự retry/failover.
- Clone bị gián đoạn tại `preparing` được giữ nguyên. Dùng `abort` để archive
  state và đánh dấu run cũ, sau đó `run` mới; `recover` không tự phục hồi clone.
- `recover` trên run `complete` chỉ đối soát intent/tree/commit và audit, không
  đổi phase hay chạy lại test/Worker. State legacy v1 dở dang bị từ chối;
  checkpoint v1 complete chỉ được đọc/audit read-only.
- **Clone không phải sandbox OS.** MVP dành cho Worker hợp tác, chạy với
  sandbox của provider. Job Object chỉ kiểm soát vòng đời process, không hạn
  chế quyền file/network/credential. Cùng tài khoản OS vẫn có thể truy cập repo
  nguồn, runtime, Git metadata, file ignored và môi trường ngoài repo. Kiểm tra
  whitelist sau thực thi phát hiện sai lệch, không ngăn tác động trước đó.
- Chưa có immutable vault hoặc test runner cách ly. Không dùng cho Worker
  đối kháng hoặc DB production. Không có migration, cài package tự động,
  submodule/LFS, dashboard hoặc push. Không lưu secrets trong Git.

## Kiểm thử

```powershell
python tools/check.py
```

Tests dùng provider giả cục bộ, không gọi AI hay tốn quota. Test containment
native chỉ chạy trên Windows; POSIX chạy baseline và bỏ qua test Windows. Xem
`docs/SDD.md` cho thiết kế hiện hành và `docs/SRS.md` cho tầm nhìn dài hạn.

## Phát triển dự án

AI và người đóng góp đọc [AGENTS.md](AGENTS.md) trước khi sửa code.
[Lộ trình](docs/ROADMAP.md) quy định thứ tự gate và acceptance;
[quy trình đóng góp](docs/CONTRIBUTING.md) quy định review/kiểm chứng;
[contracts](docs/CONTRACTS.md) xác định bất biến và khoảng trống hiện tại.
Task và handoff dùng mẫu trong `docs/templates/`. CI kiểm tra Windows/Linux
trên Python 3.10/3.12, không gọi AI thật hoặc tự publish.

[Quy trình điều phối/review AI](docs/COORDINATION.md) quy định quyền giao việc
và nghiệm thu. FND-01/FND-02, G0, [DAT-01](docs/tasks/DAT-01.md) và
[REC-01](docs/tasks/REC-01.md), [REC-02](docs/tasks/REC-02.md) đã nghiệm thu và tích hợp.
G1 DONE theo [quyết định điều phối](docs/reviews/G1-CLOSURE.md); PROC-01 và
PROC-02 đã merge/nghiệm thu. [PROC-03](docs/tasks/PROC-03.md) đang REVIEW,
G2 vẫn mở. AI thực thi bàn giao để reviewer nghiệm thu, không tự push main
hoặc nhận tiếp task khác.
