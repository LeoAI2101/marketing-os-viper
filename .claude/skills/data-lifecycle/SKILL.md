---
name: data-lifecycle
description: Đối chiếu mọi model/bảng trong schema với sổ vòng đời dữ liệu (retention · archive · purge · legal hold) ở context/DATA-LIFECYCLE.md trước khi đổi schema; chỉ kiểm tĩnh và báo cáo, không chạy job xoá, không tự chốt thời hạn. Nạp khi dự án lưu dữ liệu cá nhân hoặc dữ liệu có nghĩa vụ lưu giữ.
---

# Vòng đời dữ liệu

> **Bật khi nào**: schema có dữ liệu cá nhân (PII), dữ liệu giao dịch, chứng từ, hoặc Authority nêu nghĩa vụ lưu giữ /
> quyền xoá. Thời hạn là **quyết định của Authority** — skill này chỉ bảo đảm không model nào bị bỏ quên.

## 1. Sổ nguồn — `context/DATA-LIFECYCLE.md`

Tạo khi skill bật lần đầu. Bảng nằm giữa hai dòng neo `<!-- DATA-LIFECYCLE:BEGIN -->` / `<!-- DATA-LIFECYCLE:END -->`,
đúng tám cột:

| Target | Model | Retention | Archive | Purge | Legal hold | Trạng thái | Bằng chứng |
|---|---|---|---|---|---|---|---|

- Khoá là `Target.Model` — tên model trong schema, **không** phải tên bảng map (`@@map`, `__tablename__`), vì hai
  boundary có thể cùng tên bảng (`OutboxEvent`).
- `pending` = đã kiểm kê nhưng chính sách chưa chốt → gate còn đỏ, và **đỏ là đúng** tới khi Authority chốt.
- `confirmed` đòi mọi ô có nội dung thật (không rỗng, không placeholder) — **không** phải chứng nhận job purge đã chạy.

## 2. Mã lỗi

DL001 model thiếu dòng · DL002 dòng còn `pending` · DL003 nội dung/trạng thái sai · DL004 trùng dòng · DL005 dòng
thừa (model đã xoá khỏi schema).

## 3. Hợp đồng checker

`scripts/check-data-lifecycle.py [--json] [--file <schema> --stdin]` — đọc schema của **mọi** boundary có schema
(không hardcode loại BFF ra: BFF có schema là tự vào sổ), chỉ Python chuẩn, không cần DB. `--file … --stdin` thay
nội dung một schema mà vẫn so toàn sổ, để kiểm một bản sửa trước khi ghi.

Exit **0** đạt kiểm tĩnh · **1** có finding · **2** chưa đọc được đầu vào (không bao giờ xanh). Parser lexical bỏ
comment/chuỗi và theo dõi block — không thay `prisma validate`/migration tool, không chứng minh kiểu hay quan hệ.

Quy ước cùng commit (luật #4): **thêm model thì thêm dòng sổ trong CÙNG commit** và chạy checker.

**Nối vào luồng chính** (template, NestJS + Prisma ở `srcroot/boundaries/*`):

| Ở đâu | Gì |
|---|---|
| Hook ghi | `scripts/guard_write.py` (`lib/lifecycle_check.py`): ghi `schema.prisma` hay sổ mà sinh **finding mới** (model mới thiếu dòng, xoá dòng của model đang sống…) ⇒ **CHẶN**; schema và sổ sửa cùng một patch được xét cùng nhau. `pending` cũ không chặn sửa field khác |
| Nhóm PT | `make check-pt` (hoặc `make check PT=1`) chạy `make check-data-lifecycle` cùng các checker khác — `scripts/perf/pt-gates.sh` |
| Lệnh riêng | `make check-data-lifecycle` `[file=…]` |
Chỉ Python chuẩn. Stack khác (FastAPI, Go, Spring) viết checker
cùng hợp đồng exit và `--file --stdin --json` rồi giữ nguyên tên target.


## 4. Trước khi ghi `confirmed`

Rà source, không suy từ cấu hình:

- **Append-only**: role DB của app có bị chặn `UPDATE/DELETE` không — purge khi đó cần role riêng.
- **Legal hold**: bản ghi đang bị giữ có chặn job xoá không.
- **Đánh dấu ≠ xoá**: cột `redactedAt`/`deletedAt` không có nghĩa nội dung đã xoá; tệp ở object storage, khoá cache,
  stream vẫn có thể còn.
- Không suy retention từ TTL của cache/stream, đồng hồ DSAR hay lịch backup.

Ghi nguồn, điều kiện và phần chưa rõ ở cột Bằng chứng. Không viết một thời hạn chung cho mọi model để gate xanh.

## 5. Giới hạn

Sổ chỉ phủ model trong schema ORM — **chưa** phủ object storage, khoá/stream của cache, bảng ngoài ORM, dữ liệu ở hệ
thống ngoài. Ghi phần chưa phủ vào cuối `DATA-LIFECYCLE.md` để không ai đọc nhầm "sổ xanh = mọi dữ liệu đã có chính sách".
