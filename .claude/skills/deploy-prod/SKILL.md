---
name: deploy-prod
description: Chuẩn bị và deploy PRODUCTION qua Jenkins prod — image lấy từ RC mới nhất đã chạy ở PREPROD, tag release vX.Y.Z bắt đầu từ v1.0.1, account AWS và Jenkins riêng, KHÔNG auto-approve. Mỗi bước ghi lên prod cần người dùng xác nhận bằng lời; execute chỉ khi được yêu cầu rõ cho đúng run ID. Dùng cho repo có deployment/deploy-manifest.json.
---

# Deploy PRODUCTION

Đọc [quy trình chung](../../../deployment/DEPLOY-WORKFLOW.md), [PROD-DEPLOY.md](../../../deployment/PROD-DEPLOY.md) và
`deployment/deploy-manifest.json`. PROD là **không đảo ngược, hướng ra ngoài** — ngoại lệ "hỏi thật" của `CLAUDE.md §3`:
mỗi bước ghi lên prod cần người dùng xác nhận **bằng lời trong chat** (không qua `AskUserQuestion`), kể cả khi đã duyệt PREPROD.

> **Bản khung**: helper có sẵn; cấu hình AWS / Jenkins prod còn `_CHƯA ĐIỀN_` ở `.local/env/`. Gặp chỗ trống → dừng, liệt kê thứ còn thiếu.

## Điều kiện vào

- `.local/env/.env.jenkins-prod` và `.local/env/.env.aws.prod` có thật — **hai file độc lập**, không mượn khoá nonprod/preprod.
- PREPROD đã có RC build `SUCCESS`/`UNSTABLE` cho mọi target trong phạm vi.
- `environments.PROD.params_verified = true` (bước 1) — chưa thì chỉ được dry-run.
- `/viper-publish` Bước 3: `context/shared/DEPLOY.md §5` rollback đã điền.

## Các bước

1. **Kiểm Jenkins prod trước, chỉ GET**: `python3 scripts/jenkins_build.py history <target> --env PROD` và
   `… params <target> --env PROD`. Báo người dùng: đã có bản prod nào, tag dạng gì, tên và kiểu tham số form. Đối
   chiếu `environments.PROD.parameters`; sai thì sửa manifest rồi **mới** đặt `params_verified: true`.
2. **AWS**: `python3 scripts/aws_session.py --env PROD` (hoặc để prepare tự chạy khi `.env.aws.prod` có đủ bốn dòng
   `AWS_SSO_*`). Helper **từ chối ghi** nếu account STS lệch account đã khai.
3. **Prepare** — `python3 scripts/deploy_workflow.py prepare --env PROD [--only …] [--rc-tag <RC preprod>] [--release-tag vX.Y.Z]`.
   Đối chiếu SM ↔ `sm_configs.prod.py` ↔ code ↔ DevOps inject như PREPROD; blocker duy nhất là khoá code dùng mà file
   chưa có dòng — hỏi giá trị. Khoá sinh mới / khoá đối tác ngoài chưa có giá trị thì để trống, **không chép từ
   UAT/PREPROD**. Chốt bảng target × RC preprod × commit × tag release prod theo run ID + diff SM đã che giá trị.
4. **Dry-run** — `… dry-run --env PROD --run <ID>`. **Dừng ở đây** và trình bảng chốt.
5. **Execute** — chỉ khi người dùng yêu cầu execute rõ ràng **cho đúng run ID đó**: `… execute --env PROD --run <ID>`
   (sync source sạch → push SM theo proof → gửi job song song → theo dõi, **in link chờ duyệt** — không auto-approve).
6. **Tổng hợp** bảng target / build URL / kết quả / `.local/releases/prod-ledger.json`. `SUCCESS`/`UNSTABLE` là xong
   (báo target vàng); không báo thành công khi còn job lỗi / chờ duyệt / đang chạy. Sau đó smoke test + dogfood
   `smoke` trên production theo `/viper-publish` Bước 5 và 7. Không retry job `unknown`, không huỷ build, không gửi chat.
