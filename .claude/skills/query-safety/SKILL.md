---
name: query-safety
description: Rà tĩnh truy vấn DB của mọi boundary — truy vấn danh sách không trần, lời gọi DB trong vòng lặp (N+1), gọi mạng trong transaction, lọc chưa có index ứng viên — trước khi sửa repository/service hoặc trước lượt đo perf; chỉ kiểm và báo, không tự sửa sản phẩm. Nạp khi dự án có DB quan hệ + ORM (Prisma, TypeORM, SQLAlchemy, GORM, JPA…) hoặc khi perf-testing cần biết truy vấn nào đáng nghi.
---

# Query-safety — truy vấn an toàn

> **Bật khi nào**: dự án có ≥1 boundary chạm DB quan hệ. Backend không DB, hoặc chỉ key-value → không áp dụng, ghi
> `n/a` ở `PRODUCTION-READY.md` kèm lý do. Hợp nhất với `perf-testing`: đỏ ở đây là thứ đáng đo trước.

## 1. Phạm vi

**Mọi target `srcroot/boundaries/*`** (đường phỏng vấn: thư mục mã nguồn gốc repo) — repository, service, worker,
helper, cả BFF. Không chỉ file `*.repository.*`: truy vấn nguy hiểm hay nằm ở service và worker. Bỏ test, file
khai báo kiểu, và **hợp đồng sinh máy** (client/contract generate) — không ai sửa tay chúng.

## 2. Quy tắc

| Mã | Mức | Bắt gì | Đọc kết quả thế nào |
|---|---|---|---|
| QS001 | lỗi | Truy vấn danh sách (`findMany`, `find`, `select … ` không `LIMIT`, `.all()`) **thiếu trần**; trần literal vô nghĩa (0, null, chuỗi); hoặc spread/key động đặt **sau** trần có thể ghi đè nó | Trần động (`take: limit`) chỉ chứng minh *cấu trúc* — phải rà validation runtime của `limit` |
| QS002 | cảnh báo | Lời gọi DB trực tiếp trong vòng lặp / callback `map`·`forEach`·`for…of` | Rà N+1: gộp batch, `IN (…)`, hoặc ghi lý do tập hữu hạn |
| QS003 | lỗi | Gọi mạng (`fetch`, `axios`, client HTTP/gRPC, SDK bên ngoài) **trong callback transaction**, kể cả iterator lồng | Giữ kết nối DB chờ I/O ngoài — đưa I/O ra ngoài, giữ nhất quán bằng outbox |
| QS004 | cảnh báo | Điều kiện lọc scalar trực tiếp chưa có index ứng viên **dẫn đầu** trong schema | Cần `EXPLAIN` thật mới kết luận; chưa phủ SQL thô, OR/AND lồng |

Thêm quy tắc riêng của ORM thì đánh mã tiếp `QS005…` và ghi ở đây cùng commit.

## 3. Hợp đồng checker

Hiện thực bằng **parser AST của chính ngôn ngữ** — không regex. Template có sẵn bản TypeScript Compiler API cho
Prisma (`scripts/check-query-safety.mjs` · `scripts/lib/query-safety.mjs` · `scripts/lib/ts-ast.mjs`, test
`scripts/tests/query-safety.test.mjs`):

```
scripts/check-query-safety.mjs [--target <tên>] [--file <đường dẫn> [--stdin]] [--json]
```

| Exit | Nghĩa |
|---|---|
| **0** | không có lỗi **trong phạm vi đã phân tích** |
| **1** | có lỗi (QS001/QS003) |
| **2** | **chưa kiểm được** — thiếu parser/dependency, đọc source hỏng. **2 không bao giờ là xanh** |

Cảnh báo không đổi exit. Báo cáo ghi `file:dòng · mã · đoạn code · gợi ý`. **Không** nằm trong `make check` thường
— để không chặn MVP một tuần; nằm trong nhóm PT.

**Nối vào luồng chính** (template, NestJS + Prisma ở `srcroot/boundaries/*`):

| Ở đâu | Gì |
|---|---|
| Hook ghi | `scripts/guard_write.py` (`lib/query_check.py`): sửa file nguồn boundary mà sinh **QS001 mới** ⇒ **cảnh báo** đưa vào ngữ cảnh, không chặn ghi |
| Nhóm PT | `make check-pt` (hoặc `make check PT=1`) chạy `make check-query-safety` cùng các checker khác — `scripts/perf/pt-gates.sh` |
| Lệnh riêng | `make check-query-safety` `[target=… file=…]` |
Typescript lấy từ `node_modules` của target đầu tiên có (`scripts/lib/node-deps.mjs`); không có ở đây mà bật ống remote
thì checker tự đẩy bundle sang máy chạy (`scripts/remote/README.md`). Stack khác (FastAPI, Go, Spring) viết checker
cùng hợp đồng exit và `--file --stdin --json` rồi giữ nguyên tên target.


## 4. Miễn trừ

Sổ `context/perf/EXEMPTIONS.md`, mỗi dòng: **mã · file · fingerprint của truy vấn · số lần gọi · lý do · người chốt ·
hạn**. Gate đỏ khi miễn trừ hết hạn, truy vấn đổi (lệch fingerprint), có bản sao mới vượt số đã chốt, hoặc truy vấn
mới chưa có dòng. **Không miễn cả file.** Không thêm trần chỉ để cắt dữ liệu lịch sử, xoá tài khoản hay tập
aggregate — chỗ đó cần phân trang/stream thật.

## 5. Giới hạn — ghi thẳng vào báo cáo

Checker nhìn **lời gọi trong source**, không lần call graph liên hàm hay alias: helper gọi từ service không có nghĩa
toàn luồng đã an toàn. Không thay `EXPLAIN ANALYZE`, không thay đo tải. Lỗi môi trường phải hiện **CHƯA KIỂM ĐƯỢC**.

Kiểm chính checker bằng test đối kháng: truy vấn mới, bản sao, miễn trừ hết hạn, sửa fingerprint — cả bốn phải đỏ.
