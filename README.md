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
stdin. Config dùng `worker_argv`, `test_argv`, `timeout_seconds`; không ghép
lệnh shell. Chuẩn bị dependency và tests trong repo trước khi chạy.

Task mẫu: `examples/task.json`. File cho phép phải là đường dẫn tương đối
chính xác dùng `/`, không glob. Không cho phép Worker sửa tests hoặc config
runner. `omp init` sinh lệnh test kiểm tra tường minh: 0 test hoặc test lỗi
đều trả nonzero, kể cả khi chạy với `PYTHONOPTIMIZE=1`; exit code 0 của lệnh
rỗng không chứng minh tính đúng đắn. CLI phân biệt lỗi input/config
(`InputError`) với lỗi operation/process (`OperationError`), in `OMP: ...`,
trả nonzero và không in traceback hay argv Worker cho lỗi dự kiến. Argv chứa
NUL bị từ chối trước side effect; executable không tồn tại được báo
`OperationError` có cause. Timeout của Worker/cleanup được thay bằng label an
toàn, không đưa argv vào stderr hay `last_error`. Dữ liệu thiết kế cũ ở
`examples/legacy-*`.

Kết quả nằm trong `.omp/runs/<run_id>/checkout`, logs nằm cạnh checkout.
OMP bỏ remote và không tự push hoặc áp dụng thay đổi vào repo nguồn.
Xem kết quả bằng `git -C .omp/runs/<run_id>/checkout show`; sau khi duyệt,
có thể fetch commit từ clone rồi cherry-pick thủ công.

## Phục hồi và giới hạn

- Checkpoint ghi file tạm, flush và replace. Khóa OS ngăn hai bộ điều phối
  cùng chạy trong một repo. Commit có run ID để đối soát khi resume.
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
và nghiệm thu. Công việc kế tiếp: [FND-02](docs/tasks/FND-02.md), prompt giao
việc ở `docs/prompts/FND-02.txt`. AI thực thi bàn giao để reviewer nghiệm thu,
không tự push main hoặc nhận tiếp task khác.
