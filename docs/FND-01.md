# FND-01: Nền phát triển và quy chuẩn AI

- Trạng thái: REVIEW
- Gate: G0
- Owner: AI trong chat hiện tại; reviewer: chưa chỉ định
- Baseline: working tree MVP từ yêu cầu trước, chưa commit
- Phụ thuộc: không
- File sở hữu: AGENTS.md, docs quy trình/roadmap/contracts/ADR/templates,
  tools/check.py, .github/, .editorconfig, .gitattributes, README liên kết.
- Không sửa: hành vi runtime MVP và tests baseline.

## Tiêu chí nghiệm thu

- [x] Quy chuẩn AI có MUST/MUST NOT, ownership và báo cáo trung thực.
- [x] Lộ trình có gate, dependency, acceptance và backlog ưu tiên.
- [x] Contracts phân biệt hiện trạng với mục tiêu; ADR ghi quyết định.
- [x] Có template work item, handoff và PR.
- [x] Lệnh check offline có kiểm tra zero tests, source và test baseline.
- [x] CI khai báo Windows/Linux Python 3.10/3.12 và installed-package smoke.
- [ ] CI server đạt trên revision được review — chưa push/run remote.
- [ ] Review độc lập — chưa có.

## Evidence local

Ngày 2026-10-21, Windows, Python 3.12.7:

- `python tools/check.py`: source checks + 8 tests đạt, exit 0.
- `git diff --check`: exit 0.
- Git integration tests chạy ngoài sandbox phiên do MSYS bị chặn tạo đối
  tượng OS. Chỉ dùng repo tạm dưới .omp/test-tmp, không gọi AI hoặc mạng.
- Build/install: `.omp/foundation-venv/Scripts/python.exe -m pip install .`
  đạt, wheel 0.1.0 được build và cài trong venv riêng, không cài vào Python hệ thống.
- Từ `.omp/package-smoke`, import xác nhận đường dẫn `site-packages` và
  chạy entrypoint đã cài `omp --help` đạt. Không chạy AI thật.

## Handoff

Chưa coi G0 DONE. Tiếp theo: reviewer xem quy chuẩn/CI, thực hiện FND-02;
trước khi đóng G0 phải chạy đủ matrix trên server theo quyền remote được cấp.
Branch protection chưa cấu hình, bộ check source không thay linter/type checker
đầy đủ và chưa phát hiện mọi biến thể dynamic shell call.
