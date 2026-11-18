# WORKLOAD — mức tải và ngưỡng hiệu năng

> Skill `perf-testing`. **Authority chốt** mọi con số dưới đây — chưa chốt (`"confirmed": false`)
> thì chỉ được chạy **probe**, và `make perf-gate` luôn đỏ. Đổi một con số = đổi `workload_hash`:
> mọi báo cáo đo theo bản cũ tự hết hiệu lực, không so hồi quy chung với bản mới.
>
> Khối ```json bên dưới là nguồn **duy nhất** máy đọc (`scripts/lib/perf_report.py · load_workload`).
> Phần chữ quanh nó là để người đọc hiểu vì sao có con số đó — viết căn cứ vào §2.

## 1. Ngưỡng (máy đọc)

```json
{
  "confirmed": false,
  "groups": {
    "R1": {"weight": 70, "p95_ms": 300},
    "W1": {"weight": 30, "p95_ms": 500}
  },
  "p99_factor": 2.5,
  "max_server_error_rate": 0.001,
  "mix_tolerance_pct": 1,
  "load": {"rps": 40, "duration_seconds": 1800, "warmup_seconds": 300, "active_users": 100},
  "peak": {"rps": 60, "duration_seconds": 900, "warmup_seconds": 300, "active_users": 100},
  "soak": {"rps": 40, "duration_seconds": 28800, "warmup_seconds": 300, "active_users": 100,
           "sample_interval_seconds": 300, "rss_growth": 0.05, "inventory": ["units"]},
  "stress": {"headroom": 3, "reference_rps": 60, "surface": "gateway"},
  "spike": {"peak_rps": 100, "ramp_seconds": 30},
  "recovery": {"faults": ["pod", "cache", "boundary"], "max_seconds": 60, "p95_ms": 300, "consecutive": 3},
  "plans": {"seq_scan_rows": 100000}
}
```

| Khoá | Nghĩa |
|---|---|
| `groups` | Nhóm thao tác (đọc `R…`, ghi `W…`): `weight` là tỷ trọng (chuẩn hoá theo tổng), `p95_ms` là SLO của nhóm |
| `p99_factor` · `max_server_error_rate` | p99 không quá `p99_factor × p95`; tỷ lệ 5xx phải **dưới** mức này; lỗi nghiệp vụ phải bằng 0 |
| `mix_tolerance_pct` | Cơ cấu nhóm thực đo lệch tỷ trọng quá bấy nhiêu điểm phần trăm ⇒ đỏ |
| `load` · `peak` · `soak` | Nhịp tối thiểu (`rps`), thời gian giữ, warmup, số người dùng đồng thời của từng bài |
| `soak.inventory` | Loại mẫu bắt buộc có trong chuỗi telemetry: `units` (RSS/PID/restart), `queues` (hàng đợi/outbox phải về 0 mỗi cửa sổ), `streams` (độ dài stream không phình đơn điệu) |
| `stress` | Điểm bão hoà phải ≥ `headroom × reference_rps`, đo qua `surface` (cửa vào thật, không gọi thẳng boundary) |
| `spike` · `recovery` | Spike 0 → `peak_rps` trong `ramp_seconds`; mỗi fault trong `faults` trở lại SLO trong `max_seconds` (đủ `consecutive` mẫu liền) |
| `plans` | EXPLAIN đường nóng có `Seq Scan` trên bảng ≥ `seq_scan_rows` dòng ⇒ đỏ |

## 2. Căn cứ

_CHƯA ĐIỀN_ — nguồn của từng con số (PRD/intake, số liệu thật, ước lượng DAU → RPS), ai chốt, ngày nào.
