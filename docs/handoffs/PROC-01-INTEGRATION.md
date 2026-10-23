# Manifest tích hợp PROC-01

- Ngày lập: 2026-10-23.
- Base HEAD: `fd5e21908fbfd9827077f2a7f1c1b510ee19c324`.
- Trạng thái task: **REVIEW**; review r2 `docs/reviews/PROC-01-r2.md` đã ACCEPTED trong phạm vi PROC-01. Chưa DONE, chưa đóng G2.
- Mục đích: chốt đúng file set, hash và evidence để tích hợp/CI; không commit/push trong lượt chuẩn bị này.

## 1. Identity và hash

Các hash dưới đây được đối chiếu trực tiếp trên working tree hiện tại và khớp review r2.

| File | SHA-256 | Vai trò |
| --- | --- | --- |
| `tests/test_process_lock.py` | `7bc208c108de9e949560dbf7819f3a88395a90fa6249e5d56db809d82a7206a3` | Test mới, 6 tests, gồm PROC01-R1 timeout branch |
| `docs/CONTRACTS.md` | `a71029f3016345e9570a26d90aa66d1f57bc8af698ab6ced90d26674f2e426dd` | Bổ sung contract OS process lock |
| `docs/tasks/PROC-01.md` | `fc85c427b5c36f7e512d0e20490fb650b9694719b523a792f952bdc7a0b62586` | Task PROC-01, trạng thái REVIEW, AC1–AC7 |
| `docs/handoffs/PROC-01.md` | `c649362d084a7cd979d0aa8658e3c2044688108bfe970f46eac557df0e865c28` | Handoff r2 và evidence kiểm thử |
| `docs/handoffs/PROC-01-INTEGRATION.md` | Manifest này; hash công bố sau khi ghi | Evidence tích hợp, không self-hash trong nội dung |
| `omp_core/main.py` | `982225ec9f60349fd9efca1ee0dba50b9310f239e2610c39d4754f5a598140d0` | Không đổi; không nằm trong commit set |
| `tests/test_crash_recovery.py` | `19d54f6fc55439b70090bf8ad0bca01efe437da2074146de8b8b5043abd74820` | Không đổi; protected test |

## 2. Commit/integration set

Đề xuất commit tối thiểu gồm 4 file bắt buộc:

1. `tests/test_process_lock.py`
2. `docs/CONTRACTS.md`
3. `docs/tasks/PROC-01.md`
4. `docs/handoffs/PROC-01.md`

Có thể đưa thêm manifest này vào cùng commit handoff:

5. `docs/handoffs/PROC-01-INTEGRATION.md`

Không stage bất kỳ file nào ngoài danh sách trên. Nếu chưa được cấp quyền commit/push, giữ nguyên working tree và dùng manifest làm danh sách review tại chỗ.

## 3. Thay đổi điều phối/workspace có sẵn — loại trừ

Các nhóm sau đã có trong working tree từ trước hoặc thuộc workspace/review history; không phải thay đổi tích hợp PROC-01.

### 3.1. Tài liệu điều phối tracked đã sửa

`README.md`, `docs/COORDINATION.md`, `docs/FND-01.md`, `docs/ROADMAP.md`,
`docs/handoffs/FND-02.md`, `docs/handoffs/G1-INTEGRATION.md`,
`docs/tasks/DAT-01.md`, `docs/tasks/FND-02.md`,
`docs/tasks/REC-01.md`, `docs/tasks/REC-02.md`,
`docs/templates/HANDOFF.md`.

Không đưa các file này vào commit PROC-01; không reset/clean/stash để xóa chúng.

### 3.2. Review history, prompt và evidence cũ chưa track

`docs/prompts/DAT-01-review.txt` và các file hiện có trong `docs/reviews/`:
`DAT-01.md`, `DAT-01-r2.md`, `DAT-01-r3.md`, `REC-01.md`,
`REC-01-r2.md`, `REC-01-r3.md`, `REC-02.md`, `REC-02-r2.md`,
`G1-INTEGRATION.md`, `G1-INTEGRATION-r2.md`, `G1-CI-VERIFICATION.md`,
`G1-CLOSURE.md`, `PROC-01.md`, `PROC-01-r2.md`,
`PROJECT-NEXT-STEP.md`.

Không sửa, xóa hoặc đưa review history vào commit tích hợp. `PROC-01-r2.md` là
nguồn quyết định ACCEPTED, không phải file cần chỉnh.

### 3.3. Workspace và runtime

- `Open-Multi-agent-Pipeline.code-workspace`: cấu hình IDE cá nhân; loại trừ.
- `.omp/`: runtime/log/temp đã được `.gitignore`; loại trừ, không commit.

## 4. Checks đã đạt trên working tree

Môi trường: Windows 11 `10.0.26100.0`, Python `3.12.7`, Git `2.56.0.windows.1`.

| Check | Kết quả | Exit |
| --- | --- | --- |
| `python -m unittest tests.test_process_lock -q` | 6 tests `OK` | 0 |
| `python tools/check.py` | 117 tests `OK`; source checks passed | 0 |
| `git diff --check` | sạch | 0 |
| `git diff --exit-code fd5e219 -- omp_core/main.py` | production không đổi | 0 |
| `git diff --exit-code fd5e219 -- tests/test_cli.py tests/test_crash_recovery.py tests/test_errors.py tests/test_pipeline.py tests/test_recovery.py tests/test_validation.py` | baseline tests không đổi | 0 |
| `git diff --exit-code fd5e219 -- .github tools/check.py pyproject.toml` | workflow/checker/dependency không đổi | 0 |
| Review r2 `docs/reviews/PROC-01-r2.md` | ACCEPTED trong phạm vi PROC-01; hash test/production khớp | — |

Log local: `%TEMP%\proc01-integration-lock.txt` và
`%TEMP%\proc01-integration-full-1.txt`.

Handoff còn ghi các lượt flake `PermissionError [WinError 5]` tại `os.replace`
trên Windows; các lần rerun đã pass. Không coi flake đó là đã sửa root cause hoặc
là containment evidence.

## 5. CI còn thiếu

Chưa có CI/package smoke trên đúng revision tích hợp vì chưa commit/push và chưa
được cấp quyền. CI của base `fd5e219` chỉ chứng minh baseline G1, không thay
CI cho PROC-01.

Sau khi được cấp quyền commit/push, cần đạt đủ 4 job của
`.github/workflows/check.yml` trên revision tích hợp:

- `windows-latest` × Python 3.10
- `windows-latest` × Python 3.12
- `ubuntu-latest` × Python 3.10
- `ubuntu-latest` × Python 3.12

Mỗi job phải đạt:
1. `python tools/check.py` với 117 tests, exit 0.
2. `python -m pip install .`.
3. Installed package/console entrypoint `omp --help` ngoài source, exit 0.

Cũng còn thiếu evidence local POSIX/Linux và Python 3.10 cho revision này, cũng
như package smoke độc lập ngoài CI. Không mở PROC-02, không đóng G2 và không
tự ghi DONE chỉ từ các check local hoặc review r2.

## 6. Prompt tích hợp

```text
Bạn là AI tích hợp PROC-01 của OMP. Base là
fd5e21908fbfd9827077f2a7f1c1b510ee19c324; task đang REVIEW và review r2 đã
ACCEPTED trong phạm vi PROC-01.

Chỉ dùng manifest docs/handoffs/PROC-01-INTEGRATION.md. Commit set bắt buộc:
- tests/test_process_lock.py
- docs/CONTRACTS.md
- docs/tasks/PROC-01.md
- docs/handoffs/PROC-01.md
Có thể kèm docs/handoffs/PROC-01-INTEGRATION.md. Không gộp README, ROADMAP,
COORDINATION, workspace, review history hoặc các docs điều phối có sẵn.

Trước commit, đối chiếu hash test/CONTRACTS/task/handoff theo manifest và xác
nhận omp_core/main.py không đổi. Commit/push chỉ khi có quyền cụ thể. Sau push,
chạy CI Windows/Ubuntu × Python 3.10/3.12, gồm tools/check.py 117 tests,
pip install . và entrypoint omp --help ngoài source. Báo lại head SHA, run URL,
job IDs, test counts và exit codes. Không tự DONE, không đóng G2, không mở
PROC-02.
```
