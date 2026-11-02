# SEC-02-B1: Integration offline — phân chia công việc

- Trạng thái: **PLANNED**, không phải task implementation gộp.
- Gate: G3 mở; SEC-02 tổng REVIEW. B0 DONE ở hồ sơ điều phối local sau merge #7.
- Base thiết kế ban đầu: `bade93db8a71a5be116bde7792a112205e8fd4a4`.
- Baseline hiện hành: `3428204d62856bc6b70c95f95cf887d6841740f9` sau PR #9; cập nhật 2026-11-02.
- Spec: `../superpowers/specs/2026-10-26-sec02-b1-design.md`, kế thừa ADR-0009.
- Reviewer: AI điều phối; mỗi gói một AI thực thi tuần tự. Không subagent.

| Gói | Trạng thái | Phạm vi | Điểm review |
| --- | --- | --- | --- |
| [SEC-02-B1a](SEC-02-B1a.md) | DONE (2026-10-28, PR #8) | Native profile/process primitive, default runner giữ nguyên | Windows CI 3.12/3.14, 457 tests mỗi job |
| [SEC-02-B1b](SEC-02-B1b.md) | DONE (2026-10-29, PR #9) | L2 layout/env/manifest/artifact reader offline | Sáu file ownership; không activation/orchestration |
| B1c | Chưa giao | Orchestration/state/evidence/recovery, schema tương thích | Gói riêng sau B1a/B1b review |

B1 DONE chỉ khi tất cả gói integration được giao/nghiệm thu/tích hợp, không
lấy B1a DONE thay whole B1. Chưa reserve/implement config v2/state v3/evidence v2.
B2 activation chờ dependency table ADR-0009 và review security riêng; SEC-03,
network/provider/credentials thật hoặc G4 không được nhận ngầm.

B1b merge `3428204`, main CI 40813350800 success 2/2, 521 tests mỗi lane
Windows Python 3.12/3.14 cùng packaging/smoke; xem [báo cáo merge](../reviews/SEC-02-B1b-MERGE.md).
Chỉ xuất bản hồ sơ đóng B1b và bản tổng hợp này. Các tài liệu/history dirty khác
giữ riêng; không nhập vào gói đóng việc. Trạng thái PLANNED là whole B1,
không phủ nhận B1a/B1b DONE và không tự giao B1c.
