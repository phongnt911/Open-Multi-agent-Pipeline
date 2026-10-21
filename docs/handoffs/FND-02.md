# Handoff FND-02

- Trạng thái: REVIEW
- Owner / revision hoặc diff:
  - Owner: AI thực thi FND-02 (DeepSeek V4.1 Flash Free, phiên opencode).
  - Base: `main` tại `8f580aa029e8321a7d5520a791e0078e5f8d6210`
    (`feat: bootstrap OMP MVP and AI delivery standards`).
  - `git status` trước khi sửa: `## main...origin/main`, working tree sạch.
  - SHA-256 revision hiện tại:
    - `omp_core/main.py`: `95c83179c04fbf7d23ae53745f0fc2a76e2b68176d0bcf3699a56de3e22c5503`
    - `tests/test_cli.py`: `f0a16d3bfa31236c46c9f78a548e99902c6f32add08c50c623249e149f8a70a7`
    - `tests/test_errors.py`: `ccfd3b9788f51aba541b41755b277f9a229c76bf9a6ae066c118f089dec40912`
  - Thay đổi hiện chưa commit; không commit, push hay merge.
  - Reviewer verdict: **ACCEPTED** cho revision này tại
    `docs/reviews/FND-02-r2.md`; task vẫn REVIEW, G0 vẫn mở.
- File thay đổi:
  - `omp_core/main.py` (M): annotation/TypedDict, `InputError`/`OperationError`,
    runner mặc định kiểm tra zero tests tường minh; FND02-R1 sanitize timeout/
    cleanup timeout và FND02-R2 validate NUL, wrap lỗi spawn.
  - `tests/test_cli.py` (mới, 9 tests): argv thật do `omp init` sinh cho
    zero/pass/fail ở chế độ thường và `PYTHONOPTIMIZE=1`; lỗi input/config;
    worker nonzero; timeout không lộ token; NUL argv; cleanup timeout.
  - `tests/test_errors.py` (mới, 6 tests): phân biệt `InputError` và
    `OperationError`; missing executable; NUL tại boundary `execute`; `stop`
    timeout; cleanup timeout không lộ argv trong exception/cause chain.
  - `README.md`, `docs/CONTRACTS.md`: mô tả hành vi CLI/runner/error liên quan.
  - `docs/tasks/FND-02.md`: REVIEW, owner, AC và review follow-up R1/R2.
  - `docs/handoffs/FND-02.md`: file này.
  - Không sửa `tests/test_pipeline.py`, workflow, `tools/check.py`, ROADMAP,
    dependency, schema hay recovery; không sửa `docs/reviews/FND-02.md`.
  - Diff tracked hiện tại: 4 file, +418/−82 dòng (README, CONTRACTS, task,
    main.py); thêm mới `tests/test_cli.py`, `tests/test_errors.py`,
    `docs/handoffs/FND-02.md`, `docs/reviews/FND-02.md`,
    `docs/reviews/FND-02-r2.md` (chưa track).
- Hành vi thay đổi và lý do:
  - Mọi hàm production trong `main.py` có type hint đầu vào/đầu ra; JSON dùng
    alias `JsonValue`/`JsonObject` và TypedDict `Config`, `Task`, `State`.
    Không thêm validate schema đầy đủ; DAT-01 vẫn sở hữu phần đó.
  - `OmpError` là base tương thích `ValueError`; `InputError` cho CLI/config/task
    trước side effect, `OperationError` cho Git/lock/process/scope. CLI in
    `OMP: ...`, trả exit 1, không in traceback cho lỗi dự kiến và giữ `__cause__`
    khi wrap.
  - Runner mặc định do `omp init` sinh bỏ `assert`; đếm test bằng
    `suite.countTestCases()`, in lỗi rõ và `sys.exit(1)` khi 0 test. Vì vậy zero
    tests vẫn nonzero khi `PYTHONOPTIMIZE=1`.
  - Timeout vẫn là `subprocess.TimeoutExpired` để giữ contract cũ, nhưng argv
    trong thông báo được thay bằng label để không lộ token provider.
  - FND02-R1: `stop` sanitize timeout của `process.wait`; `execute` tách lỗi
    operation và cleanup, raise lỗi công khai ngoài block `except` để raw argv
    không nằm trong `__cause__`/`__context__`, và chain timeout operation đã
    sanitize để giữ dấu vết. Cleanup lỗi khác được chuẩn hóa thành
    `OperationError` với tên lớp, không echo message gốc.
  - FND02-R2: `validate` từ chối NUL trong `worker_argv`/`test_argv` trước side
    effect; tại `execute`, `Popen` lỗi `OSError`/`ValueError` được wrap thành
    `OperationError` có cause. Không bắt `ValueError` toàn chương trình.
- Tiêu chí đã đạt / chưa đạt: xem bảng AC bên dưới; tất cả AC1–AC7 có evidence
  local. Chưa có CI cho diff hiện tại vì chưa push.
- Lệnh kiểm thử, OS, Python, số test và exit code:
  - Môi trường: Windows 11 IoT Enterprise LTSC 10.0.26100,
    Python 3.12.7, Git 2.56.0.windows.1.
  - Baseline trước sửa: `python tools/check.py` → 8 tests OK, exit 0;
    `git diff --check` → exit 0.
  - Trước sửa, regression FND02-R1/R2:
    - `test_cli.py`: regression NUL argv fail với traceback; regression cleanup
      timeout làm lộ `REVIEW-FAKE-SECRET` trong stderr và `last_error`.
    - `test_errors.py`: cleanup timeout FAIL vì raw cleanup argv lộ trong
      exception; missing executable ERROR vì ném `FileNotFoundError` thay vì
      `OperationError`.
    - Direct NUL boundary test được thêm sau khi sửa, chỉ dùng làm coverage
      boundary; không tuyên bố là pre-fix evidence.
  - Sau sửa: `python tools/check.py` → 23 tests OK, exit 0.
  - Sau sửa: `git diff --check` → exit 0.
  - `python -m unittest discover -s tests -p "test_cli.py" -v` → 9 tests OK,
    exit 0.
  - `python -m unittest discover -s tests -p "test_errors.py" -v` → 6 tests OK,
    exit 0.
  - Zero-tests regression sau sửa:
    `python -m unittest tests.test_cli.InitRunnerTests.test_zero_tests_fail_in_normal_and_optimized_modes -v`
    → 1 test OK, exit 0.
  - CI baseline đọc qua GitHub API: run `39542511418` trên base SHA
    `8f580aa029e8321a7d5520a791e0078e5f8d6210`, kết luận `success`
    (`https://github.com/phongnt911/Open-Multi-agent-Pipeline/actions/runs/39542511418`).
    Diff hiện tại chưa được push nên chưa có CI cho diff này.
- Kiểm thử chưa chạy và nguyên nhân:
  - CI Windows/Linux × Python 3.10/3.12 cho diff hiện tại: chưa push, không có
    quyền chạy CI từ phiên này.
  - Linux/macOS local và Python 3.10: máy hiện tại chỉ có Windows/Python 3.12.7.
  - Build/install package và entrypoint ngoài source: đã chạy trên Windows/
    Python 3.12.7 trong venv riêng; xem mục “Evidence tích hợp” bên dưới. Chưa
    chạy trên Python 3.10/Linux/macOS hoặc cài từ file sdist/wheel độc lập.
  - Type checker đầy đủ (mypy/pyright): repo chưa cấu hình; annotation được
    kiểm tra bằng introspection (`inspect.signature`) và đọc code.
  - Live provider/AI thật: bị cấm trong task; toàn bộ test dùng subprocess giả.
  - Crash/kill recovery, Windows Job Object, schema migration: ngoài scope
    FND-02 (DAT-01/REC/PROC).
  - Đã thử `PYTHONOPTIMIZE=1 python tools/check.py` trước khi thêm regression
    R1/R2: 17 tests, 1 failure ở `test_failed_tests_never_complete` (exit 1).
    Nguyên nhân là fixture cũ dùng chuỗi test `assert app.value == 1`; optimize
    bỏ `assert` nên test giả đạt. Đây không phải runner mặc định, không phải
    AC3 và không sửa baseline vì AC5. Chưa chạy lại toàn bộ optimize sau khi
    thêm regression R1/R2.
- Rủi ro / dữ liệu hoặc process còn tồn tại:
  - `OmpError` kế thừa `ValueError` để giữ 8 regression tests cũ; `InputError`
    và `OperationError` phân biệt được nhưng chưa có mã lỗi ổn định/retryability
    (DAT-01/PROV-02).
  - `OSError` từ `save` vẫn là lỗi I/O cấp thấp, không phải `OperationError`;
    CLI vẫn bắt và trả nonzero. Không thay contract checkpoint trong task này.
  - Cleanup lỗi không phải timeout được đổi thành `OperationError` chỉ với tên
    lớp, không chain raw exception để tránh argv; cause của operation được giữ.
    Nếu cần chẩn đoán sâu cleanup, phải xem log/runtime cục bộ, không đưa raw
    argv vào lỗi công khai.
  - Redaction mới tập trung vào timeout/cleanup timeout và lỗi spawn; chưa có
    framework redaction cho mọi message operation (DAT-01/PROV-02).
  - Runner chỉ bảo đảm zero tests bị chặn; `assert` trong test của người dùng
    vẫn có thể bị `PYTHONOPTIMIZE` bỏ, nên không chứng minh test user đúng.
  - Nếu thư mục `tests` không tồn tại, runner mặc định hiện in `ImportError`
    (nonzero) thay vì thông báo thân thiện; zero tests trong thư mục rỗng đã xử lý.
  - Một lần chạy đơn lẻ test timeout gặp `PermissionError` thoáng qua khi
    `os.replace` `state.json`; chạy lại test đó và các full check sau đó đều
    đạt (hiện 23 tests), chưa tái hiện. Cần theo dõi nếu lặp lại trên Windows.
  - Không có thay đổi schema/recovery; state vẫn `schema_version: 1` và chưa
    validate đầy đủ. Không có runtime/log/credential mới trong Git.
- Docs/ADR cập nhật:
  - `README.md`, `docs/CONTRACTS.md` mô tả runner/error hiện hành, gồm NUL,
    lỗi spawn và timeout/cleanup timeout.
  - `docs/tasks/FND-02.md` giữ REVIEW, đánh dấu AC1–AC7 và thêm review
    follow-up FND02-R1/R2.
  - Không tạo ADR vì không đổi quyết định kiến trúc; không sửa ROADMAP vì
    không thuộc file sở hữu FND-02 (điều phối cập nhật trạng thái nếu cần).
- Bước tiếp theo / owner đề xuất:
  - AI điều phối dùng evidence build/install và danh sách file dưới đây để
    quyết định bước tích hợp; không cần review lại nội dung FND-02 vì r2 đã
    ACCEPTED trên đúng ba hash.
  - Yêu cầu bàn giao: sau mỗi lần thực thi, prompt copy trực tiếp được đăng
    trong khung chat cho bước kế tiếp; không lưu nội dung prompt trong file.
  - Không ghi DONE, không đóng G0 và không merge/push từ phía AI thực thi.
    CI cho revision được tích hợp vẫn cần trước khi đóng G0.

## Mapping AC1–AC7

| AC | Evidence | Kết quả |
| --- | --- | --- |
| AC1 | `omp_core/main.py`: alias JSON, TypedDict `Config`/`Task`/`State`, annotation cho `read`, `read_object`, `save`, `lock`, `git`, `validate`, `_timeout_error`, `stop`, `_public_operation_error`, `_public_cleanup_error`, `execute`, `check_scope`, `advance`, `main`; docstring nêu side effect/lỗi. Introspection cho thấy không tham số thường nào thiếu annotation. | Đạt |
| AC2 | `tests/test_errors.py` (InputError vs OperationError, Git failure, missing executable, NUL boundary, cleanup timeout cause chain); `tests/test_cli.py::CliErrorTests` cho config hỏng, config sai, worker nonzero, timeout, NUL argv, cleanup timeout; assert không có `Traceback`, không lộ secret trong stderr/`last_error`/exception chain; CLI trả exit 1. | Đạt |
| AC3 | `tests/test_cli.py::InitRunnerTests`: dùng `test_argv` đọc từ `.omp/config.json` do `omp init` sinh; zero tests → nonzero, pass → 0, fail → nonzero, mỗi case chạy cả thường và `PYTHONOPTIMIZE=1`. | Đạt |
| AC4 | Baseline: `test_cli.py` chạy trước sửa có 1 failure ở subTest `python_optimize=True` của zero tests (`0 == 0`, exit 0 sai); sau sửa test zero-tests OK. Normal mode zero tests đã nonzero trên baseline. Không sửa source baseline để tạo evidence. | Đạt |
| AC5 | `tests/test_pipeline.py` không đổi; `python tools/check.py` 23 tests OK exit 0; `git diff --check` exit 0; 8 test cũ vẫn chạy, không skip/nới expected. | Đạt |
| AC6 | Không thêm dependency (`pyproject.toml` không đổi); test dùng subprocess giả, không gọi AI/mạng; `git status` chỉ có file thuộc task; không commit/push; không thêm `.omp`, log hay credential vào Git. | Đạt |
| AC7 | README/CONTRACTS/task khớp hành vi; handoff có base SHA, file list, mapping AC, regression zero-tests và R1/R2, lệnh/OS/Python/số test/exit code, check chưa chạy và rủi ro. | Đạt |

## Regression zero-tests trước/sau

Trước sửa, chạy test CLI mới trên baseline:

```text
python -m unittest discover -s tests -p "test_cli.py" -v
Ran 6 tests in 2.642s
FAILED (failures=1)
```

Failure duy nhất là subTest `python_optimize=True` của
`InitRunnerTests.test_zero_tests_fail_in_normal_and_optimized_modes`:
`AssertionError: 0 == 0` — runner baseline dùng `assert` nên bị bỏ khi optimize,
zero tests trả exit 0. SubTest chế độ thường vẫn nonzero.

Sau sửa, test zero-tests cụ thể đạt:

```text
python -m unittest tests.test_cli.InitRunnerTests.test_zero_tests_fail_in_normal_and_optimized_modes -v
Ran 1 test in 0.385s
OK
```

Full check sau sửa: `python tools/check.py` → 23 tests, `OK`, exit 0.

## Regression FND02-R1/R2 trước/sau

Trước sửa, các regression mới thất bại đúng lỗi reviewer nêu:

- `test_cli.py`: regression NUL argv fail với traceback; regression cleanup
  timeout làm lộ `REVIEW-FAKE-SECRET` trong stderr và `last_error`.
- `test_errors.py`: cleanup timeout ném raw `TimeoutExpired` chứa
  `REVIEW-FAKE-SECRET`; missing executable ném `FileNotFoundError` thay vì
  `OperationError` có cause.
- Direct NUL boundary test và `stop` timeout test được thêm sau khi sửa để khóa
  hành vi `Popen`/`stop`; đây là coverage bổ sung, không dùng làm pre-fix
  evidence.

Sau sửa:

```text
python -m unittest discover -s tests -p "test_cli.py" -v
Ran 9 tests
OK

python -m unittest discover -s tests -p "test_errors.py" -v
Ran 6 tests
OK
```

`test_errors.py::test_cleanup_timeout_hides_argv_from_public_exception` đi qua
toàn bộ chuỗi `__cause__`/`__context__` và xác nhận token giả không xuất hiện;
`test_errors.py::test_stop_timeout_hides_argv` khóa boundary `stop`;
`test_cli.py::test_cleanup_timeout_hides_argv_from_cli_and_last_error` kiểm tra
stderr CLI và `last_error`. Mapping: FND02-R1 → timeout/cleanup timeout ở
`stop`/`execute`; FND02-R2 → NUL validation và wrap `Popen` `OSError`/
`ValueError` tại `execute`.

## Evidence tích hợp build/install ngoài source

- Ngày: 2026-10-21.
- Môi trường: Windows 11 IoT Enterprise LTSC 10.0.26100 (64-bit),
  Python 3.12.7, Git 2.56.0.windows.1.
- Temp root: `C:\Users\POTA\AppData\Local\Temp\opencode\omp-fnd02-integration-r2`
  (ngoài repo; không commit). Source copy gồm `pyproject.toml` và `omp_core/`;
  hash `omp_core/main.py` trong source copy khớp
  `95c83179c04fbf7d23ae53745f0fc2a76e2b68176d0bcf3699a56de3e22c5503`.
- Venv riêng, không `--system-site-packages`:
  `python -m venv <temp>\venv` → exit 0; Python 3.12.7, pip 24.2.
- Build/install:
  `<temp>\venv\Scripts\python.exe -m pip install --disable-pip-version-check --no-input <temp>\source`
  → exit 0. Pip build isolation cài build dependency `setuptools>=68`; wheel
  tạo ra `omp_orchestrator-0.1.0-py3-none-any.whl`, size 8311 byte,
  sha256 `ab2371f40906de3bae4020a2733d82675820e6f1f9eaeb6e1518a7b08baf8a2d`.
  Runtime dependencies rỗng; `pip check` → exit 0; `pip list` chỉ có
  `omp-orchestrator 0.1.0` và `pip 24.2`.
- Installed artifact:
  `venv\Lib\site-packages\omp_core\main.py` có SHA-256
  `95c83179c04fbf7d23ae53745f0fc2a76e2b68176d0bcf3699a56de3e22c5503`,
  khớp source/review.
- Entrypoint ngoài source (cwd `<temp>\external`, không có `omp_core` local):
  - `<venv>\Scripts\omp.exe --help` → exit 0.
  - `<venv>\Scripts\omp.exe init` trong Git repo tạm → exit 0; tạo
    `.omp/config.json`; `test_argv[0]` trỏ tới Python trong venv.
  - `<venv>\Scripts\omp.exe status` khi chưa có state → exit 1, stderr
    `OMP: Cannot read JSON ... state.json`, không có `Traceback` (đây là lỗi
    dự kiến).
  - Runner mặc định sinh bởi `omp init`, chạy ngoài source:
    - một test pass, chế độ thường → exit 0;
    - không có test, `PYTHONOPTIMIZE=1` → exit 1, in
      `FAIL: no tests discovered`.
- Repo checks sau build/install: `python tools/check.py` → 23 tests OK, exit 0;
  `git diff --check` → exit 0. Lượt build/install dùng source copy trong temp,
  không ghi thêm artifact vào repo. Lưu ý: repo đã có sẵn `build/` và
  `omp_orchestrator.egg-info/` từ trước (bị `.gitignore`), trong đó
  `build/lib/omp_core/main.py` có hash
  `749b990e18fd7fffeacac07852d8b2fa0e2a6dc66f8e131f0e03c1b7653c1674`,
  khác revision hiện tại; không dùng làm evidence và không sửa/xóa trong lượt
  này.

### Danh sách file cần tích hợp

- Đã sửa: `omp_core/main.py`, `README.md`, `docs/CONTRACTS.md`,
  `docs/tasks/FND-02.md`.
- Mới, cần thêm: `tests/test_cli.py`, `tests/test_errors.py`,
  `docs/handoffs/FND-02.md`.
- Review evidence (không sửa): `docs/reviews/FND-02.md`,
  `docs/reviews/FND-02-r2.md`.
- Không đổi nhưng cần cho build/package: `pyproject.toml`,
  `omp_core/__init__.py`; không đổi `tests/test_pipeline.py`, workflow,
  `tools/check.py`, dependency, schema hoặc recovery.

### Check còn thiếu

- CI cho đúng working-tree revision/tree tích hợp: chưa push, chưa có CI.
- Linux/macOS và Python 3.10 local: chưa chạy; evidence build/install mới chỉ
  trên Windows/Python 3.12.7.
- Cài từ file sdist/wheel độc lập (ngoài `pip install .`) và entrypoint trên
  Python 3.10: chưa chạy.
- `omp run`/`resume` bằng entrypoint đã cài: chưa chạy vì sẽ gọi Codex thật;
  các đường này đã được kiểm tra bằng test fake trong repo.
- Static type checker (mypy/pyright): chưa cấu hình/chạy.
- Full suite `PYTHONOPTIMIZE=1`: chưa chạy lại; fixture assert cũ
  `test_failed_tests_never_complete` vẫn là giới hạn đã ghi nhận, ngoài runner
  mặc định. Runner mặc định normal/optimize đã được kiểm tra riêng.
- Crash/kill containment, Windows Job Object, schema migration: ngoài scope
  FND-02 (DAT-01/REC/PROC).
- Artifact ignored `build/` và `omp_orchestrator.egg-info/` đã cũ, không đại
  diện revision; cần chủ repo/điều phối quyết định dọn trước khi đóng gói lại.
  Không xóa trong lượt này vì không thuộc file ownership và có thể là dữ liệu
  cục bộ của người khác.

## Xác nhận tích hợp/packaging của AI điều phối

Ngày 2026-10-21, Windows 11 build 10.0.26100, Python 3.12.7 (64-bit).
Đây là evidence bổ sung và quyết định đề xuất tích hợp, không phải review lại
code đã được ACCEPTED. Giữ task REVIEW; không ghi DONE, không đóng G0.

### Revision và phạm vi

- HEAD vẫn là `8f580aa029e8321a7d5520a791e0078e5f8d6210`.
- Xác nhận lại trước và sau checks: main.py
  `95c83179c04fbf7d23ae53745f0fc2a76e2b68176d0bcf3699a56de3e22c5503`;
  test_cli.py
  `f0a16d3bfa31236c46c9f78a548e99902c6f32add08c50c623249e149f8a70a7`;
  test_errors.py
  `ccfd3b9788f51aba541b41755b277f9a229c76bf9a6ae066c118f089dec40912`.
- Không thay production, tests hoặc hai báo cáo review. Trong lượt này chỉ
  cập nhật handoff và tạo dữ liệu kiểm chứng ngoài source.
- Danh sách file tích hợp đã đối soát: 4 file sửa gồm main.py, README.md,
  docs/CONTRACTS.md, docs/tasks/FND-02.md; 5 file mới gồm tests/test_cli.py,
  tests/test_errors.py, docs/handoffs/FND-02.md và hai báo cáo review
  docs/reviews/FND-02.md, docs/reviews/FND-02-r2.md.
- `git diff --exit-code 8f580aa -- pyproject.toml tests/test_pipeline.py .github tools`
  exit 0: packaging config, 8 baseline tests, workflow và checker không đổi.

### Kiểm chứng mới do điều phối tự chạy

- `python tools/check.py`: 23 tests OK, không skip, exit 0.
- `git diff --check`: exit 0; chạy lại sau cập nhật handoff.
- Build/install dùng source copy ngoài repo chỉ từ pyproject.toml, README.md,
  omp_core (bỏ __pycache__), không sử dụng build/egg-info cũ.
- Evidence root:
  `C:\Users\POTA\.codex\visualizations\2026\10\21\01a15c40-cc19-7b63-871a-11e5ac0114a0\omp-fnd02-package-3_k22d09`.
  Có evidence.json, commands.json, log từng lệnh, before-artifacts.json,
  source copy, venv riêng, wheel và external fixture. Dữ liệu này nằm ngoài
  repo và không thuộc danh sách file đưa vào Git.

Trong các lệnh dưới, `<pkg>` là evidence root và `<python>` là
`<pkg>/venv/Scripts/python.exe`:

| Lệnh/check | Kết quả | Exit |
| --- | --- | --- |
| python -m venv <pkg>/venv | Venv mới, không system-site-packages | 0 |
| <python> -m pip wheel --disable-pip-version-check --no-input --no-deps --wheel-dir <pkg>/wheels <pkg>/source | Build wheel qua build isolation | 0 |
| <python> -m pip install --disable-pip-version-check --no-input --no-index --no-deps <wheel> | Cài từ file wheel độc lập vào venv mới | 0 |
| <python> -m pip check | Không lỗi dependency | 0 |
| <python> -m pip list --format=json --disable-pip-version-check | Chỉ pip 24.2 và omp-orchestrator 0.1.0 | 0 |
| <python> -I -c "import omp_core; print(omp_core.__file__)" | Từ external cwd, trỏ site-packages trong venv | 0 |
| <pkg>/venv/Scripts/omp.exe --help | Chạy từ external cwd | 0 |
| omp.exe init | Trong repo fixture ngoài source; sinh đúng Python venv trong test_argv | 0 |
| omp.exe status khi chưa có state | Lỗi dự kiến, không traceback | 1, đúng kỳ vọng |
| Runner sinh từ init, zero tests + PYTHONOPTIMIZE=1 | FAIL: no tests discovered | 1, đúng kỳ vọng |
| Runner sinh từ init, một test pass | OK | 0 |

Wheel: `omp_orchestrator-0.1.0-py3-none-any.whl`, 8311 byte,
SHA-256 `2b6a8d691c3aea6d8ac05ff3979115df3f009d50445da77a7e5b363b068e1277`.
main.py đã cài có bytes/hash khớp main.py được review; không import nhầm repo.
Hash wheel khác lượt build trước không tự chứng minh source khác: chưa có
cam kết reproducible build/metadata timestamps; identity source đã đối soát.

Git/tempfile checks chạy ngoài sandbox phiên theo cơ chế cấp quyền do giới
hạn MSYS/Windows. Build isolation chỉ dùng build dependency setuptools đã khai
báo; không thêm runtime dependency, không cài package vào Python hệ thống.

### Artifact và checks còn thiếu

- Manifest tên file + SHA-256 của build/, dist/, omp_orchestrator.egg-info/
  ở repo gốc trước/sau hoàn toàn khớp; dist/ không tồn tại trước và sau.
  Không tạo build/dist/egg-info mới trong repo gốc, không sửa/xóa artifact cũ.
  Build artifacts mới chỉ nằm trong source copy ngoài repo.
- Evidence cài wheel độc lập trên Windows/Python 3.12 đã được bổ sung ở lượt
  này, thay cho mục wheel còn thiếu ở phần evidence lịch sử bên trên.
- Vẫn thiếu CI cho đúng revision/tree tích hợp, build/smoke Python 3.10 và
  Linux; chưa thử sdist độc lập, static type checker hoặc installed run/resume.
- Không chạy AI thật. Installed run/resume có thể kiểm tra bằng fake provider
  nhưng chưa chạy trong lượt packaging này; không tuyên bố đã có evidence đó.
- Rủi ro packaging còn lại: build dependency setuptools chưa pin version,
  chưa kiểm chứng reproducible wheel hay mọi nền tảng; source copy chỉ chứng
  minh gói runtime, không chứng minh tài liệu/tests có trong sdist.

### Quyết định đề xuất

Hash, local checks, review đã có và packaging evidence khớp: **đề xuất đưa
đúng 9 file nêu trên vào bước tích hợp khi được chủ dự án cho phép**. Không
đưa runtime/log/venv/wheel/build cache vào Git. Không thay verdict reviewer.
Chưa tích hợp hoặc commit/push/merge trong lượt này; task vẫn REVIEW, G0 mở.
Trước khi đóng G0 cần CI cho tree cuối và quyết định tích hợp/nghiệm thu riêng.
Nếu production/tests đổi hash hoặc base/tree đổi, đối soát lại evidence liên
quan; không suy ra CI của baseline là CI của revision chưa commit này.
