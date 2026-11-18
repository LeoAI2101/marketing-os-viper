# `.local/` — cấu hình riêng của máy + sổ release dùng chung

Cùng khuôn với các dự án VIPER chạy deploy nhiều môi trường (SIT → UAT → PT/PREPROD → PROD).
Code ở `srcroot/`, cách chạy local ở `deployment/`; **thông tin xác thực vận hành và dấu vết deploy** ở đây.

```
.local/
├── README.md              ← file này (commit)
├── templates/             ← KHUÔN, chỉ TÊN biến, không giá trị (commit)
│   ├── .env.aws.example           AWS nonprod (SIT · UAT · PT · PREPROD)
│   ├── .env.aws.prod.example      AWS production — file độc lập, không mượn khoá nonprod
│   ├── .env.jenkins.example       Jenkins nonprod
│   ├── .env.jenkins-prod.example  Jenkins production
│   ├── .env.gitlab-sync.example   adapter sync source sang repo con của pipeline
│   ├── .env.sonar.example         SonarQube — lớp đối chiếu của skill code-quality-gate
│   └── sm_configs.example.py      khuôn Secrets Manager: SM_CONFIGS + MUST_MATCH
├── releases/
│   ├── <env>-ledger.json  ← sổ release từng môi trường (commit — cả đội dùng chung)
│   └── …                  ← proof, output, lock của lượt deploy (KHÔNG commit)
├── env/                   ← bản thật của khuôn: .env.aws, .env.jenkins… (KHÔNG commit, chmod 600)
├── sm/                    ← sm_configs.<env>.py giá trị thật (KHÔNG commit)
└── security-audit/        ← output của skill security-audit, run-<N>/ (KHÔNG commit)
```

## Luật

1. **Chỉ `README.md`, `templates/` và `releases/*-ledger.json` được commit.** Mọi thứ khác bị `.gitignore`
   chặn (`/.local/*`) — luật #6, không secret trong code.
2. Dùng khuôn: `cp .local/templates/.env.aws.example .local/env/.env.aws && chmod 600 .local/env/.env.aws`.
3. **Không in giá trị** của file trong `env/` và `sm/` ra terminal hay báo cáo — chỉ tên khoá.
4. Phần AWS / Jenkins / GitLab **chưa điền** ở bản khung này (`_CHƯA ĐIỀN_` trong `deployment/DEPLOY-WORKFLOW.md`).
   Skill `deploy-*` dừng và báo khi gặp chỗ trống — không đoán URL, account, đường job.
5. Chuyển máy: copy cả `.local/` sang clone tương ứng, dừng tác vụ đang dùng ledger trước, không copy lock đang hoạt
   động. Profile AWS / SSH key ngoài repo vẫn là điều kiện của máy.
