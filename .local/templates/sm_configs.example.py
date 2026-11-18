# Khuôn Secrets Manager — chỉ TÊN khoá + giá trị KHÔNG bí mật. File thật ở .local/sm/sm_configs.<env>.py,
# KHÔNG commit, KHÔNG in giá trị.
#
# Mỗi target (tên thư mục dưới srcroot/<nhóm>/) một dict khoá → giá trị. Khoá bí mật để '' kèm comment
# `# BÍ MẬT`. Khoá do DevOps inject cho mọi pod ghi comment `# DevOps inject global mọi pod` — helper đọc
# đúng chú thích này (deployment/DEPLOY-WORKFLOW.md §DevOps inject).
SM_CONFIGS = {
    # '<target>': {
    #     'APP_ENV': 'production',
    #     'DB_HOST': '',  # DevOps inject global mọi pod
    #     'DB_PASSWORD': '',  # DevOps inject global mọi pod
    #     'DB_CONNECTION_LIMIT': '',  # skill connection-budget đọc ô này bằng AST
    #     'INTERNAL_CALLER_TOKEN': '',  # BÍ MẬT · MUST_MATCH
    # },
}

# Khoá phải cùng giá trị ở mọi target có nó (bên gọi ↔ bên nhận)
MUST_MATCH = []
