# Deploy nhiều môi trường — SIT · UAT · PT · PREPROD · PROD

Dùng khi sản phẩm **không** deploy lên một PaaS bằng `make deploy` đơn giản (`/viper-publish`), mà đi qua pipeline
của tổ chức: Jenkins build image → registry → ArgoCD/cụm, secret ở AWS Secrets Manager. Thường là đường intake.
Năm skill `deploy-sit` · `deploy-uat` · `deploy-pt` · `deploy-preprod` · `deploy-prod` đọc file này.

> **Bản khung.** Phần AWS / Jenkins / GitLab đánh dấu `_CHƯA ĐIỀN_` — Authority bổ sung sau. Gặp chỗ trống thì
> skill **dừng và báo**, không đoán URL, account, đường job hay tên service.

## Chuỗi nâng bản

```
SIT  ── build từ cây local (snapshot sạch), không tạo RC
 └─► UAT ── lấy commit SIT đã build xong, cấp RC mới từng target
      ├─► PT      ── giữ nguyên RC của UAT, không tăng RC
      └─► PREPROD ── giữ nguyên RC của UAT (không lấy nguồn PT), account AWS riêng
           └─► PROD ── image = RC mới nhất đã chạy ở PREPROD, tag release vX.Y.Z riêng (PROD-DEPLOY.md)
```

Build xong `SUCCESS` hoặc `UNSTABLE` (vàng vì quét bảo mật/chất lượng) đều tính là đã lên môi trường — báo rõ target vàng.

## Bốn bước, mọi môi trường

| Bước | Lệnh helper | Ghi gì |
|---|---|---|
| prepare | `python3 scripts/deploy_workflow.py prepare --env <ENV> [--only <target…>] [--rc-tag …]` | AWS login → đối chiếu Secrets Manager ↔ `.local/sm/sm_configs.<env>.py` ↔ code ↔ khai báo DevOps inject → bảng target × commit × RC theo **run ID** |
| dry-run | `… dry-run --env <ENV> --run <ID>` | ghim run, phạm vi, hash code, cấu hình; đổi gì thì prepare + dry-run lại. **Mặc định dừng ở đây** |
| execute | `… execute --env <ENV> --run <ID>` | chỉ khi người dùng yêu cầu execute rõ ràng: sync source sạch → push key SM còn thiếu theo proof → gửi **song song** mọi job → theo dõi |
| status | `… status --env <ENV>` | đồng bộ ledger `.local/releases/<env>-ledger.json` với Jenkins |

Helper có sẵn (port từ DriverPlus, đã bỏ mọi giá trị riêng của tổ chức): `scripts/deploy_workflow.py` ·
`aws_session.py` · `secrets_manager.py` · `jenkins_build.py` · `prepare_deploy.py` · `env_audit.py` ·
`gitlab_main_commits.py` · `sync_srcroot_to_gitlab.sh` · `sonar_scan.sh` · `deploy.sh` + `scripts/lib/`
(`ops_env`, `deploy_manifest`, `release_*`, `jenkins_*`…). Cần Python 3.10+, `requests`, `boto3`, AWS CLI v2.
**Không có mặc định nào của tổ chức trong code**: host/nhóm GitLab, đường job Jenkins, account ECR, region, tên
repo đều đọc từ `.local/env/` — thiếu thì tool dừng và chỉ đúng khoá cần khai. Test: `python3 -m unittest discover
-s scripts/tests -p 'test_*.py'` (dựng repo mẫu tạm, không đọc secret thật).

Qua make (tuỳ chọn, khi `make deploy` của dự án trỏ vào workflow này):
`make deploy ENV=SIT` (prepare) · `make deploy ENV=SIT RUN=<ID>` (dry-run) · `make deploy ENV=SIT RUN=<ID> CONFIRM=1` (execute).

## Luật chung

1. **Mặc định chỉ prepare + dry-run.** Execute là hướng ra ngoài: cần lời yêu cầu rõ của người dùng **cho đúng run ID**.
   PROD cần xác nhận bằng lời ở **mỗi** bước ghi, kể cả khi đã duyệt PREPROD (không qua `AskUserQuestion`).
2. **Không đoán giá trị.** Câu hỏi duy nhất của luồng: code dùng khoá mà file `sm_configs` chưa có dòng và SM cũng
   chưa có — helper thêm dòng trống rồi dừng hỏi giá trị. Phát hiện khác về giá trị (rỗng, placeholder, `MUST_MATCH`
   lệch) chỉ in «LƯU Ý». Không điền mặc định của code vào file; không chép giá trị môi trường này sang môi trường kia.
3. **Chỉ giá trị đã điền mới lên SM**; ô rỗng không đẩy. Secret chưa có vỏ trên AWS → báo DevOps, không tự tạo.
4. **Không in secret.** Diff SM chỉ hiện tên khoá + trạng thái, giá trị che.
5. **Song song, không retry mù.** Mọi job gửi một lượt; POST không rõ kết quả → `unknown`, đối chiếu Jenkins rồi mới
   sửa trạng thái. Timeout không huỷ build. Không tự gửi chat.
6. **Auto-approve** chỉ input không tham số của đúng build trong lượt, bằng token đang cấu hình; không có quyền → in
   «Build đang chờ duyệt. Vui lòng ping dev lead để approve: <link>». **PROD không bao giờ auto-approve.**
7. **Legacy là hợp đồng** (`VIPER.md §1.2`): vòng ≥2, hook `guard_bc` chặn `deploy_workflow.py execute` như
   `make deploy` khi `BACKWARD-COMPATIBILITY-CHECKLIST.md §3` chưa xanh.

## Manifest — `deployment/deploy-manifest.json`

Không chứa secret. Khung có sẵn, các khoá:

| Khoá | Là gì |
|---|---|
| `discovery` | nhóm `boundaries` · `web-experiences` · `mobile-experiences` → glob đường dẫn target |
| `mapping_file` | bảng TSV hai cột: đường dẫn target ↔ repository của pipeline (`deployment/gitlab-repo-map.tsv`) |
| `schema` | khuôn `SM_CONFIGS`/`MUST_MATCH` — `.local/templates/sm_configs.example.py` |
| `environments.<ENV>` | `source` (môi trường nguồn), `parameters` (form Jenkins theo nhóm, placeholder `{source_env}` `{rc}` `{commit}` `{release_tag}`), `pre_execute_checks` riêng, `params_verified` (PROD) |
| `pre_execute_checks` | argv kiểm bắt buộc trước execute — mặc định `gate.py P1`, `make check`, `make test`. Chỉ SIT chạy (build từ cây local); UAT/PT/PREPROD khai rỗng vì chỉ nâng image đã kiểm |
| `build_env` | nhóm → JSON env build-time (`VITE_*`…) — không gửi nhầm vào SM |
| `devops_injected_env` | khoá nền tảng DevOps inject cho mọi pod (DB, Redis, Kafka…) — tin theo khai báo, không đẩy lên SM |
| `derived_env` · `local_only_env` · `env_helpers` | biến code tự dựng · biến chỉ local/build · tên wrapper đọc env |
| `approval_input_ids` · `approve_spacing_seconds` | input duyệt được phép auto-approve · giãn cách giữa các lần duyệt |

## AWS — `_CHƯA ĐIỀN_`

Region, account từng môi trường, role, cách đăng nhập (SSO bằng mã thiết bị hay `aws login`), quy ước tên secret,
ECR. Khuôn: `.local/templates/.env.aws.example` (nonprod, khoá hậu tố `_<ENV>` cho môi trường có account riêng) và
`.env.aws.prod.example` (production, file độc lập). Helper phải **từ chối ghi** khi account STS trả về lệch account đã khai.

## Jenkins — `_CHƯA ĐIỀN_`

URL nonprod / prod, đường job theo `{env}` `{group}` `{target}` `{service}`, tên và kiểu tham số form từng môi trường,
input duyệt. Khuôn: `.local/templates/.env.jenkins.example`, `.env.jenkins-prod.example`.

## Sync source sang repo của pipeline — `_CHƯA ĐIỀN_`

Adapter nhận `--push` và tên target, chỉ sync **snapshot sạch đã review**, không force-push. Khuôn:
`.local/templates/.env.gitlab-sync.example`; bảng ánh xạ `deployment/gitlab-repo-map.tsv`.

## DevOps inject — `_CHƯA ĐIỀN_`

Bảng khoá nền tảng DevOps cấp cho **mọi pod** (thường DB · Redis · Kafka). Nguồn chốt là chú thích
`# DevOps inject global mọi pod` trong các file `sm_configs.*.py` thật. Không dùng kubectl, không đọc cụm: thiếu
khoá trên pod thì lộ lúc khởi động, không chặn trước deploy.
