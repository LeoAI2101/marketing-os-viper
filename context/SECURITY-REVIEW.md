---
type: security-review
tier: T2
---

# SECURITY-REVIEW — rà bảo mật mỗi vòng

> Pha P của **mỗi vòng có chạy P** (`/viper-polish` Bước 2b): skill `security-audit` rà bề mặt mới của vòng, ghi
> một mục ở đây. `gate.py P1` kiểm mục của vòng hiện tại; mục `Rà bảo mật` ở `PRODUCTION-READY.md` nhóm 2 là
> `(mỗi vòng)` nên `repeat.py` tự bỏ tick khi mở vòng.
>
> Sổ **cộng dồn**, không reset: mỗi vòng thêm `## Vòng N` ở cuối, mục cũ giữ nguyên. Chi tiết của chế độ `trọn`
> (`architecture.md`, `coverage-ledger.json`, `findings.json`, `REPORT.md`…) nằm ở `.local/security-audit/run-<N>/` —
> **không commit** (có thể chứa đường khai thác); sổ này là vết trong git, chỉ ghi tóm tắt **không kèm cách khai thác**.

## Cách ghi một mục

| Dòng | Luật |
|---|---|
| `## Vòng N — <tên vòng>` | Đúng số vòng ở `STATE.md` |
| `Chế độ` | `hướng dẫn` (mặc định — rà tập trung) hoặc `trọn` (đủ 6 pha). `trọn` chỉ khi Authority chốt: dòng `Rà bảo mật` của `_PROPOSAL.md §14` hoặc `DECISIONS.md` |
| `Phạm vi` | Bề mặt mới/đổi của vòng: endpoint, resolver, job, webhook, đầu vào không tin cậy, bí mật mới (`_PROPOSAL.md §10 §14`) |
| `Báo cáo` | *(chỉ `trọn`)* `.local/security-audit/run-<N>/REPORT.md` |
| Bảng phát hiện | Mỗi dòng một phát hiện: `verdict` (`confirmed` · `needs_validation` · `rejected`), `mức` (`informational`…`critical`, để `—` khi chưa confirmed), `trạng thái` (`đã sửa <commit>` · `mở` · `chấp nhận — DECISIONS <ngày>`). Không có phát hiện → một dòng `—` |

Gate P1 **đỏ** khi: thiếu mục của vòng · thiếu `Chế độ`/`Phạm vi` · `trọn` mà thiếu `Báo cáo` · còn dòng `confirmed`
mức `high`/`critical` đang `mở`. `needs_validation` không chặn, nhưng phải có kế hoạch kiểm ở cột ghi chú.

Mẫu một mục (bỏ khối comment khi chép):

<!--
## Vòng 2 — Nhắc lịch trước hẹn

Chế độ  : hướng dẫn
Phạm vi : POST /reminders (đầu vào số điện thoại), job gửi SMS, webhook trạng thái từ nhà mạng
Ngày rà : 2026-08-19 · commit 3f2a9c1

| # | Phát hiện | Verdict | Mức | Trạng thái | Ghi chú |
|---|---|---|---|---|---|
| 1 | Webhook trạng thái không kiểm chữ ký | confirmed | high | đã sửa 4b1e0d2 | — |
| 2 | Rate limit theo IP, chưa theo số điện thoại | needs_validation | — | mở | cần đo trên cụm PT |
-->
