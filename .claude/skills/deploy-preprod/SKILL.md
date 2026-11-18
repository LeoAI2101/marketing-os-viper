---
name: deploy-preprod
description: Chuẩn bị và deploy PREPROD qua pipeline của tổ chức (Jenkins + AWS Secrets Manager) — AWS login, đối chiếu Secrets Manager ↔ sm_configs ↔ code ↔ khai báo DevOps inject, chốt bảng commit/RC theo run ID, dry-run, gửi song song và theo dõi approval. Mặc định dừng ở dry-run; execute chỉ khi người dùng yêu cầu rõ. Dùng cho repo có deployment/deploy-manifest.json; không deploy production.
---

# Deploy PREPROD

Đọc [quy trình chung](../../../deployment/DEPLOY-WORKFLOW.md) và `deployment/deploy-manifest.json`. Không đóng cứng tên
service, account, namespace hay đường job — mọi thứ đó đến từ manifest và `.local/env/`.

> **Bản khung**: helper có sẵn (`scripts/deploy_workflow.py`…), nhưng cấu hình AWS / Jenkins / GitLab của tổ chức còn
> `_CHƯA ĐIỀN_` ở `.local/env/`. Helper thiếu khoá thì dừng và nêu tên khoá — **liệt kê đúng thứ còn thiếu**, không đoán.

## Điều kiện vào

- `.local/env/.env.aws` và `.local/env/.env.jenkins` có thật (khuôn ở `.local/templates/`), không còn ô bắt buộc trống.
- `deployment/deploy-manifest.json` có `environments.PREPROD` và form `parameters` đã đối chiếu với Jenkins thật.
- Vòng ≥2: `context/BACKWARD-COMPATIBILITY-CHECKLIST.md §3` xanh — `guard_bc` chặn execute nếu chưa.

## Các bước

1. **Phạm vi + thông tin xác thực.** Lấy profile và danh sách target từ yêu cầu/manifest; `.env.aws` có đủ bốn dòng
   `AWS_SSO_*` thì không cần `--profile`. PREPROD thường là **account AWS riêng**: khoá hậu tố `_PREPROD` trong `.local/env/.env.aws` (`AWS_SSO_ACCOUNT_ID_PREPROD` + ba khoá STS); thiếu thì dừng, **không mượn khoá nonprod**. Thiếu thông tin → báo điều kiện cần; **không in file
   secret**. Không hỏi về khoá DevOps inject, không kiểm pod, không dùng kubectl.
2. **Prepare** — `python3 scripts/deploy_workflow.py prepare --env PREPROD --profile <profile> [--only <target…>]`.
   AWS login phải xong trước khi đọc SM; phiên SSO hết hạn thì helper in URL + mã thiết bị để người dùng duyệt.
   Lần đầu: dựng **tên** khoá từ code (để rỗng), rồi pull secret của repo. Đối chiếu SM ↔ file ↔ code ↔ DevOps inject.
   Phát hiện về giá trị chỉ in «LƯU Ý». **Blocker duy nhất**: code dùng khoá mà `sm_configs.preprod.py` chưa có dòng
   và SM cũng chưa có — helper thêm dòng trống; **hỏi người dùng giá trị** (hoặc xác nhận để trống) rồi prepare lại.
3. **Nguồn.** PREPROD **giữ nguyên RC của UAT** đã build xong — không lấy nguồn PT, không tăng RC. Đây là nguồn image của PROD. Chốt bảng target × commit × RC theo run ID vừa tạo và diff SM đã che giá trị. Không đoán giá trị,
   không tự điền mặc định của code vào file.
4. **Dry-run** — `python3 scripts/deploy_workflow.py dry-run --env PREPROD --run <ID>`. **Mặc định dừng ở đây.** Người
   dùng đã yêu cầu execute rõ ràng cho run này thì đi tiếp khi kiểm đạt, không hỏi lại quyền đã có.
5. **Execute** — `python3 scripts/deploy_workflow.py execute --env PREPROD --run <ID>`: sync source sạch → push khoá SM
   còn thiếu theo proof (`secrets_manager.py push`) → gửi **song song** mọi job (không chạy từng target với
   `build --wait`) → theo dõi. Auto-approve chỉ đúng build trong lượt; không có quyền thì in lời nhắc ping dev lead kèm link.
6. **Tổng hợp** bảng target / build URL / kết quả + đường dẫn `.local/releases/preprod-ledger.json`. `SUCCESS` hoặc
   `UNSTABLE` là xong (báo rõ target vàng). **Không báo thành công** khi còn job lỗi / chờ duyệt / đang chạy.
   `status --env PREPROD` để đồng bộ; không retry job `unknown` khi chưa đối chiếu, không tự huỷ build, không gửi chat.
