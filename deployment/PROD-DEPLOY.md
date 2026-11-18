# Deploy production

Production lấy image từ **PREPROD** (RC mới nhất đã build `SUCCESS`/`UNSTABLE`) nhưng mang **tag release riêng
`vX.Y.Z`, bắt đầu từ `v1.0.1`**, độc lập số RC. Đọc trước [quy trình chung](DEPLOY-WORKFLOW.md). Skill: `deploy-prod`.

PROD là môi trường **không đảo ngược, hướng ra ngoài** — ngoại lệ "hỏi thật" của `CLAUDE.md §3`: mỗi bước ghi lên
prod cần người dùng xác nhận bằng lời trong chat.

## Cấu hình riêng (file độc lập, không commit)

| File | Nội dung |
|---|---|
| `.local/env/.env.jenkins-prod` | Jenkins prod: URL, user, token, đường job — `_CHƯA ĐIỀN_` |
| `.local/env/.env.aws.prod` | account AWS production, role, region, khoá STS, quy ước tên secret — `_CHƯA ĐIỀN_` |
| `.local/sm/sm_configs.prod.py` | giá trị Secrets Manager prod (khuôn `.local/templates/sm_configs.example.py`) |

`--env PROD` chỉ đọc hai file prod; nguồn PREPROD vẫn đọc bằng `.env.jenkins`. Không bao giờ mượn khoá nonprod/preprod.

## Khác PREPROD ở bốn điểm

1. **Nguồn = PREPROD** (`--source-env preprod`, chọn RC cũ bằng `--rc-tag`). Không lấy từ UAT, không tăng RC.
2. **Tag release**: chưa có bản prod → `v1.0.1`; có rồi → tăng patch; ép bằng `--release-tag`. Jenkins prod đã có build
   mà không đọc được tag → dừng, không đoán.
3. **Form chưa đối chiếu thì không gửi**: `environments.PROD.params_verified` trong manifest phải `true` — đặt sau khi
   đã đọc tên/kiểu tham số form thật bằng lệnh chỉ GET. Chưa `true` thì dry-run vẫn chạy, build thật bị chặn.
4. **Không auto-approve**: job gửi song song, helper chỉ theo dõi và in link build chờ duyệt.

Khoá sinh mới (token, HMAC…) và khoá đối tác ngoài chưa có giá trị thì để trống — không chép từ UAT/PREPROD.

## Sau deploy

Ledger `.local/releases/prod-ledger.json`, lượt `PROD-<YYYYMMDD>-<NN>`; mỗi dòng `deploy` có `rc_tag` (image nguồn)
và `release_tag`. Smoke test + rollback theo `context/shared/DEPLOY.md` và `/viper-publish` Bước 5–6.
