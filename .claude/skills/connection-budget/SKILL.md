---
name: connection-budget
description: Tính ngân sách kết nối DB (Postgres/MySQL) theo số replica tối đa, surge khi rolling deploy, số pool mỗi pod và kết nối phụ (migration, vận hành, IdP), so với trần max_connections; đối chiếu snapshot đo thật. Chỉ đo và báo cáo, không sửa pool, HPA hay runtime. Nạp khi có ≥2 boundary dùng chung một DB, hoặc chạy trên nền tự co giãn (Kubernetes HPA, serverless).
---

# Ngân sách kết nối

> **Bật khi nào**: nhiều boundary/replica dùng chung một instance DB, có autoscale, hoặc `perf-testing` thấy lỗi
> cạn pool. Một app một replica trên PaaS → thường `n/a`, ghi lý do.

## 1. Sổ nguồn — `context/perf/CONNECTION-BUDGET.json` (+ `.md` giải thích)

Là **kịch bản kế hoạch, không phải cấu hình vận hành**. Mỗi boundary: `maxReplicas`, `surge` (số pod hoặc tỷ lệ),
`pool_instances` mỗi pod, nguồn của `DB_CONNECTION_LIMIT` theo môi trường. Cộng thêm: IdP (Keycloak…), migration,
vận hành, dự phòng; và `max_connections` từng môi trường. Mọi giả định có cờ `*_confirmed` — chưa xác nhận ⇒ đỏ.

## 2. Công thức

```
mỗi boundary = (maxReplicas + ceil(surge)) × pool_instances × DB_CONNECTION_LIMIT
tổng         = Σ boundary + IdP + migration + vận hành + dự phòng
đạt khi      tổng ≤ 80 % max_connections
```

- Chỉ boundary có client DB mới có pool; BFF không DB thì loại.
- Worker chạy cùng process **không** tự là pool mới; nhưng module đăng ký client DB riêng (provider thứ hai,
  `new PrismaClient`, engine SQLAlchemy thứ hai) **là** pool mới — phải rà DI.
- `DB_CONNECTION_LIMIT` để trống thì ORM tự chọn (Prisma: `2 × CPU + 1`) ⇒ báo "thiếu giới hạn xác định", không đoán.
- Đọc pool từ file cấu hình môi trường bằng **phân tích literal** (AST), không import, không in giá trị khác — file
  đó thường cạnh secret.

## 3. Hợp đồng checker

`scripts/check-connection-budget.py --env <tên> [--observation <snapshot.json>] [--json]` — chỉ Python chuẩn
(`scripts/lib/connection_budget.py`; quét provider Prisma/NestJS). Nhóm PT gọi với `env=PT`; `make check-connection-budget
env=<tên>` chạy lẻ. **Chặn deploy**: có sổ `CONNECTION-BUDGET.json` thì `guard_bc` chạy checker với đúng môi trường
của lệnh deploy (`ENV=…` / `--env …`) — đỏ hoặc chưa kiểm được thì chặn.
Exit **0** đủ đầu vào, đã xác nhận, trong trần · **1** thiếu bằng chứng hoặc vượt trần · **2** đầu vào hỏng.

Quét provider **bảo thủ**: factory, alias, module động, spread mà chưa chứng minh được số pool ⇒ đỏ "chưa chứng
minh số pool DI" — rà tay, không nới checker.

## 4. Snapshot đo thật (khi có môi trường perf)

Collector chạy trên máy chịu tải, chỉ ghi **số** (số kết nối theo app/role, `max_connections`), có hạn 24 giờ và
khớp hash source. Kèm mốc lấy **độc lập**: `--expected-host`, `--expected-project`, `--expected-db-instance` —
không chép ngược từ snapshot để hợp thức hoá. Pool đang nhàn không chứng minh trần đã đủ.

## 5. Quyền

Chỉ sửa hành vi checker và test của nó. **Không** sửa pool, provider, HPA, runtime, cũng không đổi cờ xác nhận để
gate xanh — vượt trần là phát hiện sản phẩm, ghi vào `ROADMAP.md`/`STATE.md` blocker cho Authority.
