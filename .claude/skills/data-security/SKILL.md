---
name: data-security
description: Rà an toàn dữ liệu của mọi boundary — API SQL "unsafe", SQL nối chuỗi trước binding, log thân request/header/trường nhạy cảm, biến bật log thân request mặc định — cộng phép thử đọc chéo dữ liệu giữa hai người dùng và quyền role DB runtime. Dùng trước khi đổi SQL/logging/truy cập dữ liệu hoặc trước deploy; chỉ đo và báo, không sửa sản phẩm, schema hay role để gate xanh.
---

# Data-security — an toàn dữ liệu

> Bổ sung cho `shared-production-ready` nhóm 2 và `context/shared/SECURITY.md`, không thay chúng. Rà sâu toàn diện
> (trust boundary, chuỗi tấn công) dùng skill `security-audit`.

## 1. Phạm vi

Mọi boundary và BFF trong `ARCHITECTURE.md` (đường phỏng vấn: cả app), toàn bộ source trừ test, file khai báo kiểu,
hợp đồng sinh máy. Không giới hạn ở repository — log rò dữ liệu nằm ở controller, middleware, worker.

## 2. Phần tĩnh — quy tắc

| Mã | Mức | Bắt gì |
|---|---|---|
| SEC001 | lỗi | Dùng API SQL "unsafe" của ORM (`$queryRawUnsafe`, `raw()` nhận chuỗi, `text()` ghép chuỗi…) — cấm kể cả chuỗi literal; không có nghĩa injection đang xảy ra, mà là cửa ngỏ sẽ mở |
| SEC002 | lỗi | SQL vào DB nối chuỗi / nội suy template **trước binding**, hoặc helper "raw" nhận dữ liệu động |
| SEC003 | lỗi / cảnh báo | Logger nhận trực tiếp `body`/`headers`/trường nhạy cảm (lỗi); nhận kết quả biến đổi hay member của nguồn nhạy cảm (cảnh báo — chưa chứng minh rò hay đã che) |
| SEC004 | lỗi | Biến môi trường kiểu `LOG_*BODY*` / `LOG_*REQUEST*` / `LOG_*PAYLOAD*` **bật mặc định** khi không khai |
| SEC005 | cảnh báo | SQL động chưa chứng minh binding — rà luồng dữ liệu, alias |

**Danh sách trường nhạy cảm** dựng từ dữ liệu thật của dự án (`PERSONAS.md`, schema): mật khẩu, mọi loại token,
`authorization`, `cookie`, số điện thoại, email, số giấy tờ… — khai **một chỗ** trong checker, đổi cùng test.

Tagged template có binding (`sql\`… ${x}\``) không phải nối chuỗi. Biến mutable, tham số, biến bị shadow trong
loop/catch **không** được coi là literal an toàn.

## 3. Phần hành vi — chỉ khi có môi trường test riêng

1. **Đọc chéo**: hai tài khoản seed riêng (A, B) + một chưa đăng nhập. A tạo bản ghi; B và người ẩn danh đọc thẳng
   theo id phải bị chặn. **Positive control** trước và sau: A tự đọc được — không có thì phép thử vô nghĩa.
   GraphQL: HTTP 200 vẫn có thể là lỗi — đọc `errors[].extensions.code` và `data`.
2. **Role DB runtime** (chỉ đọc catalog): role của app có quyền tạo DB/schema, sở hữu schema, superuser, bỏ qua RLS?
   Không thử DDL. Không kết luận "đã tách role migration" chỉ từ tên role.
3. **Canary log**: gửi một mật khẩu sai *giả* có marker, đọc log đúng cửa sổ thời gian, chỉ đếm số dòng có marker.
   Không xuất log thô, cookie, token, URL DB hay env.

Không đăng nhập được / thiếu fixture → ghi **missing**, không phải PASS. Không dùng đường tắt OTP/reset/đăng ký để
có phiên. Ma trận đầy đủ theo `PERSONAS.md §2` vẫn là việc của `/viper-polish` nhóm 2 và vai dogfood `breaker`.

## 4. Hợp đồng checker

`scripts/check-data-security.mjs [--target] [--file [--stdin]] [--json]` — TypeScript Compiler API, không regex
(`scripts/lib/data-security.mjs`, test `scripts/tests/data-security.test.mjs`).
Exit **0** không lỗi trong phạm vi phân tích · **1** có lỗi · **2** chưa kiểm được (không bao giờ xanh).
**Không có sổ miễn trừ**: lỗi giữ đỏ tới khi sửa, hoặc Authority chốt ngoại lệ ở `DECISIONS.md`.

**Nối vào luồng chính** (template, NestJS + Prisma ở `srcroot/boundaries/*`):

| Ở đâu | Gì |
|---|---|
| Hook ghi | `scripts/guard_write.py` (`lib/security_check.py`): ghi file nguồn boundary sinh **lỗi SEC mới** ⇒ **CHẶN** ghi; checker không chạy được ⇒ cũng chặn (không giả kết quả an toàn). Lỗi cũ không chặn sửa chỗ khác |
| Nhóm PT | `make check-pt` (hoặc `make check PT=1`) chạy `make check-data-security` cùng các checker khác — `scripts/perf/pt-gates.sh` |
| Lệnh riêng | `make check-data-security` `[target=… file=…]` |
Typescript lấy từ `node_modules` của target đầu tiên có (`scripts/lib/node-deps.mjs`); không có ở đây mà bật ống remote
thì checker tự đẩy bundle sang máy chạy (`scripts/remote/README.md`). Stack khác (FastAPI, Go, Spring) viết checker
cùng hợp đồng exit và `--file --stdin --json` rồi giữ nguyên tên target.

## 5. Giới hạn

AST không chứng minh an toàn toàn luồng: chưa có type checker, call graph, taint liên hàm. Cảnh báo SEC003 không có
nghĩa đã che PII. Ghi riêng ba loại: **lỗi sản phẩm · lỗi checker · thiếu bằng chứng** — không bỏ test đỏ hay đổi
kỳ vọng để làm xanh, và không suy "data-security xanh" thành "nhóm 2 của PRODUCTION-READY xanh".
