# Quy trình đóng góp

## Trước khi code

Đọc AGENTS.md và chọn task trong ROADMAP. Tạo bản task theo
`docs/templates/WORK_ITEM.md`; file dùng tên mã task khi cần lưu bền vững.
Ghi owner, base revision (và working-tree diff nếu chưa commit), file sở hữu,
phụ thuộc, tiêu chí nghiệm thu và lệnh test. Không coi thay đổi chưa commit
của người khác là lỗi cần dọn dẹp.

Mặc định một AI triển khai một task. Khi được người dùng cho phép làm song
song: mỗi task có checkout/branch riêng, không chia quyền sửa cùng file;
người tích hợp review diff và chạy lại check sau tích hợp. Quy định này khác
với runtime OMP, nơi một run vẫn chỉ có một Worker hoạt động.

## Thực hiện và xác minh

1. Chứng minh lỗi/nhu cầu bằng case tái hiện hoặc acceptance test.
2. Thực hiện thay đổi tối thiểu; không trộn chỉnh format toàn repo vào bugfix.
3. Chạy `python tools/check.py`. Lệnh này kiểm tra cú pháp, JSON mẫu,
   trailing whitespace ở file nguồn/tài liệu, lệnh shell nguy hiểm trong code,
   và unittest với yêu cầu có test. Đây không thay lint/type checker đầy đủ.
4. Nếu đổi packaging: cài package và thử entrypoint từ thư mục ngoài source.
5. Nếu đổi CLI/schema: cập nhật README/CONTRACTS, tests tương thích và ví dụ.
6. Nếu đổi recovery/process/security: thêm failure test và evidence Windows.
7. Đọc lại diff, `git diff --check`, điền handoff; chưa chạy check nào phải ghi rõ.

## Review và Definition of Done

- Tiêu chí task có evidence cho từng mục; không có acceptance còn mơ hồ.
- Regression test chứng minh hành vi, không chỉ kiểm tra implementation detail.
- Không giảm phạm vi test hoặc bỏ failure để làm pipeline xanh.
- Không có lỗi có thể làm mất sửa đổi nguồn, chạy trùng Worker hoặc báo test
  đạt sai. Các lỗi này chặn merge bất kể tính năng khác hoạt động.
- Nền tảng hỗ trợ là Windows; Python 3.12 dùng ngay làm baseline phát triển.
- CI bắt buộc cho mọi PR: Windows × Python 3.12/3.14; test không có mạng,
  không dùng AI thật hoặc credentials. Packaging smoke dùng package đã cài.
- Reviewer khác tác giả khi có thể; security gate cần review độc lập. Nếu chỉ
  một người, ghi rõ thiếu review độc lập và không tuyên bố gate security đã đạt.
- Commit/PR có mã task và mô tả hành vi, validation, rủi ro. Dùng Conventional
  Commits khi tạo commit theo yêu cầu: `fix(recovery): ...`, `docs: ...`.
- Không tự merge, phát hành hay push nếu chưa được cho phép. Thiết lập branch
  protection/required checks cần quyền quản trị remote và chưa được áp dụng
  chỉ bằng việc thêm file workflow.

## Ngoại lệ và bằng chứng

Nếu sandbox ngăn Git/process tests, ghi rõ lệnh và lỗi; chạy theo cơ chế cấp
quyền của môi trường. Không đánh dấu PASS cho check chưa chạy. CI URL, OS,
Python version, số test và exit code phải gắn với revision/diff được review.
Đổi quyết định kiến trúc dùng ADR trong `docs/decisions/` với lý do và migration.
Không bỏ gate bằng ngoại lệ im lặng; người dùng quyết định đổi phạm vi rõ ràng.

## Chính sách nền tảng từ 2026-10-25

Python tối thiểu 3.12 (pyproject.toml và .python-version). Chạy tools/check.py,
package install/entrypoint smoke và các proof Windows liên quan trên 3.12/3.14.
Không thêm Linux/POSIX hoặc Python 3.10 trở lại CI để mở rộng scope ngầm.
Không suy test parser phụ thuộc thông điệp CPython là hợp đồng ứng dụng; nếu
khác biệt xảy ra phải giữ cause/type và chứng minh fail trước/pass sau trước
khi sửa test. Evidence và reviews lịch sử vẫn giữ nguyên phiên bản đã chạy.
