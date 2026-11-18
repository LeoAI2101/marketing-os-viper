#!/usr/bin/env python3
"""secrets_manager.py — xem và cập nhật AWS Secrets Manager cho các target của dự án.

VÌ SAO CÓ FILE NÀY
    Tool ops đứng NGOÀI luồng VIPER (không phải 1 trong 6 lệnh hợp đồng, không ghi
    context/DECISIONS.md hay STATE.md). Đi cặp với scripts/jenkins_build.py.

DANH SÁCH TARGET — DÒ TỪ ĐĨA (scripts/lib/ops_env.py), không gõ tay ở đây. Repo này mọc
    thêm boundary theo từng vòng (ROADMAP.md §1); danh sách chép cứng là danh sách sẽ lệch.

QUY ƯỚC TÊN SECRET — khai ở .local/env/.env.aws, KHÔNG đóng cứng trong code
    SM_NAME_TEMPLATE   mặc định {env}/{service_type}/{application_group}/{target}
                       → sit/<service_type>/<application_group>/<target>
    SM_NAME_<TARGET>   ngoại lệ theo target (target viết hoa, gạch ngang → gạch dưới).
                       Có ngoại lệ khi devops đã đóng cứng một tên khác trong tham số
                       job Jenkins — khi đó JOB JENKINS là nguồn sự thật, không phải
                       quy ước ông+cháu. Đặt tên ngoại lệ ở file env, không sửa file này.

ĐỊNH DẠNG PAYLOAD — JSON object phẳng {"KEY": "value", ...}.
    Payload hiện tại không phải JSON object → TỪ CHỐI merge (xem dưới).

NGỮ NGHĨA GHI — AWS KHÔNG có API sửa một key
    put_secret_value luôn ghi đè TOÀN BỘ payload và luôn đẻ version mới:
        version mới ← AWSCURRENT      version cũ ← AWSPREVIOUS
    Nên lựa chọn thật không phải "nâng version hay không", mà là số phận của những
    key KHÔNG được nhắc tới trong lần ghi:
        merge (mặc định) — đọc AWSCURRENT, áp key mới lên trên, key cũ GIỮ NGUYÊN
        --replace        — payload đưa vào LÀ toàn bộ secret, key không nhắc tới BỊ XOÁ

NĂM RÀO AN TOÀN — đừng gỡ cái nào
    1. Chỉ target CÓ THẬT trong srcroot/. Không nhận tên tuỳ ý, không thao tác theo prefix
       — cùng namespace còn secret của đội khác.
    2. KHÔNG BAO GIỜ create_secret. Vỏ secret do devops tạo; gõ sai tên thì fail to
       chứ không đẻ secret rác trên hạ tầng dùng chung.
    3. KHÔNG BAO GIỜ in giá trị secret ra stdout/log — chỉ in tên key, độ dài, sha4.
    4. Dry-run mặc định. Phải --yes mới ghi thật.
    5. Ghim VersionId: merge là read-modify-write, có khe hở. Trước khi ghi kiểm lại
       AWSCURRENT còn đúng version lúc đọc không — lệch thì DỪNG, không ghi đè người khác.

HAI FILE KHAI BÁO
    .local/templates/sm_configs.example.py  KHUÔN — ĐƯỢC commit. Liệt kê TOÀN BỘ env key của
                                  từng target (không chỉ key bí mật), giá trị mẫu.
    .local/sm/sm_configs.<env>.py   GIÁ TRỊ THẬT của MỘT môi trường — .gitignore chặn.
                                  Mỗi env một file vì APP_ENV, domain và chuỗi kết nối
                                  DB/Redis đều khác nhau.
                                  Đọc bằng ast.literal_eval chứ không `import`: file chứa
                                  giá trị production thì không được là đường chạy mã.
    `push` đối chiếu hai file cả hai chiều nên chúng không trôi khỏi nhau.

BÍ MẬT AWS — đọc .local/env/.env.aws (mẫu: .local/templates/.env.aws.example).

DÙNG
    cp .local/templates/sm_configs.example.py .local/sm/sm_configs.sit.py   # rồi điền giá trị thật của SIT
    scripts/secrets_manager.py list
    scripts/secrets_manager.py keys <target> --env SIT
    scripts/secrets_manager.py push --env SIT               # dry-run, xem diff
    scripts/secrets_manager.py push --env SIT --yes         # ghi thật
    scripts/secrets_manager.py push --env SIT --only-new    # CHỈ thêm key mới
    scripts/secrets_manager.py rollback <target> --env SIT --yes
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ops_env import (  # noqa: E402
    AWS_STS_KEYS, aws_env_cfg, aws_file, discover_targets, env_key, env_list, load_env_file, own_aws_account,
    require_target, with_own_envs,
)

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("✗ thiếu boto3 — cài: pip install boto3")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENV_FILE = ".local/env/.env.aws"
EXAMPLE_FILE = ROOT / ".local/templates/sm_configs.example.py"

TARGETS = discover_targets()


def schema_file() -> Path:
    if (ROOT / 'deployment/deploy-manifest.json').exists():
        from deploy_manifest import inside, read_manifest
        return inside(ROOT, read_manifest(ROOT)['schema'])
    return EXAMPLE_FILE


def config_file(env: str) -> Path:
    """Giá trị thật của MỘT môi trường — .gitignore chặn `.local/sm/sm_configs.*.py` trừ bản example.

    Mỗi env một file vì APP_ENV, domain (KEYCLOAK_BASE_URL/URL_*) và chuỗi kết nối DB/Redis
    đều khác nhau — dùng chung một file là sớm muộn đẩy value của SIT lên UAT.
    """
    return ROOT / ".local/sm" / f"sm_configs.{env.lower()}.py"


# ── Tên secret ────────────────────────────────────────────────────────────────

def secret_name(cfg: dict, target: str, env: str) -> str:
    require_target(TARGETS, target)
    override = cfg.get(f"SM_NAME_{env_key(target)}")
    name_template = override or cfg.get("SM_NAME_TEMPLATE") or "{env}/{service_type}/{application_group}/{target}"
    try:
        return name_template.format(
            env=env.lower(),
            service_type=cfg.get("SM_SERVICE_TYPE", ""),
            application_group=cfg.get("SM_APPLICATION_GROUP", ""),
            target=target,
        )
    except KeyError as exc:
        sys.exit(f"✗ {ENV_FILE}: mẫu tên secret có chỗ giữ lạ {exc} — chỉ nhận"
                 f" {{env}} {{service_type}} {{application_group}} {{target}}")


# ── Đọc sm_configs.example.py / sm_configs.<env>.py ───────────────────────────
#
# Đọc bằng ast.literal_eval, KHÔNG `import`. Hai file kia trông như Python nhưng chỉ
# được phép chứa dict hằng — parse thay vì thực thi thì một file chứa giá trị production
# không bao giờ trở thành đường chạy mã.

def read_decl(path: Path, must_exist_hint: str = "") -> tuple[dict, set]:
    if not path.exists():
        sys.exit(f"✗ chưa có {path.name}. {must_exist_hint}")
    tree = ast.parse(path.read_text())
    configs, must_match = None, set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        try:
            val = ast.literal_eval(node.value)
        except ValueError:
            sys.exit(f"✗ {path.name}: `{name}` không phải hằng — file này chỉ được chứa dict/set hằng.")
        if name == "SM_CONFIGS":
            configs = val
        elif name == "MUST_MATCH":
            must_match = set(val)
    if configs is None:
        sys.exit(f"✗ {path.name} không có SM_CONFIGS")
    return {t: {k: str(v) for k, v in d.items()} for t, d in configs.items()}, must_match


# ── AWS ───────────────────────────────────────────────────────────────────────

def env_cfg(env: str | None) -> dict:
    """Cấu hình AWS của một env (khoá `_<ENV>` thắng khoá chung); không đụng os.environ."""
    return aws_env_cfg(load_env_file(aws_file(env), required=("AWS_DEFAULT_REGION",)), env)


def load_cfg(env: str | None = None) -> dict:
    raw = load_env_file(aws_file(env), required=("AWS_DEFAULT_REGION",))
    cfg = aws_env_cfg(raw, env)
    missing_keys = [k for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") if not cfg.get(k)]
    if missing_keys:
        hint = (f"\n  {env} là account AWS riêng: khoá nằm ở {', '.join(k + '_' + env.upper() for k in missing_keys)}."
                f" Làm mới: python3 scripts/aws_session.py --profile default --env {env}"
                if own_aws_account(raw, env) else "")
        sys.exit(f"✗ {ENV_FILE} thiếu biến: {', '.join(missing_keys)}{hint}")
    # boto3 đọc từ os.environ; file vừa refresh phải thay khóa cũ của shell — kể cả token cũ khi khóa mới không có token.
    for k in AWS_STS_KEYS + ("AWS_DEFAULT_REGION",):
        if cfg.get(k):
            os.environ[k] = cfg[k]
        elif k == "AWS_SESSION_TOKEN":
            os.environ.pop(k, None)
    return cfg


def client(cfg: dict | None = None):
    """Client Secrets Manager. Có `cfg` thì dùng đúng khóa trong đó (đa account); không thì theo os.environ."""
    cfg = cfg or {}
    explicit = {}
    if cfg.get("AWS_ACCESS_KEY_ID") and cfg.get("AWS_SECRET_ACCESS_KEY"):
        explicit = {"aws_access_key_id": cfg["AWS_ACCESS_KEY_ID"], "aws_secret_access_key": cfg["AWS_SECRET_ACCESS_KEY"],
                    "aws_session_token": cfg.get("AWS_SESSION_TOKEN") or None}
    region = cfg.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("✗ chưa khai AWS_DEFAULT_REGION ở .local/env/.env.aws (hoặc môi trường)")
    return boto3.client("secretsmanager", region_name=region, **explicit)


def describe(sm, name: str) -> dict | None:
    """None = vỏ secret chưa tồn tại."""
    try:
        return sm.describe_secret(SecretId=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def current_version_id(meta: dict) -> str | None:
    for vid, stages in (meta.get("VersionIdsToStages") or {}).items():
        if "AWSCURRENT" in stages:
            return vid
    return None


def previous_version_id(meta: dict) -> str | None:
    for vid, stages in (meta.get("VersionIdsToStages") or {}).items():
        if "AWSPREVIOUS" in stages:
            return vid
    return None


def read_payload(sm, name: str) -> tuple[dict, str | None]:
    """Trả (payload, version_id). Secret rỗng → ({}, None). Không phải JSON object → thoát."""
    try:
        resp = sm.get_secret_value(SecretId=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return {}, None
        raise
    raw = resp.get("SecretString")
    if raw is None:
        sys.exit(f"✗ {name}: payload là binary, không phải JSON — tool này không xử")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(
            f"✗ {name}: payload hiện tại KHÔNG phải JSON — không merge được.\n"
            f"  Muốn thay hẳn bằng JSON thì dùng --replace (có ý thức: mọi key cũ mất)."
        )
    if not isinstance(data, dict):
        sys.exit(f"✗ {name}: payload là JSON nhưng không phải object key-value — không merge được.")
    return {str(k): ("" if v is None else str(v)) for k, v in data.items()}, resp["VersionId"]


# ── Payload vào ───────────────────────────────────────────────────────────────

def parse_env_file(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"✗ không thấy file {path}")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    if tracked.returncode == 0:
        sys.exit(f"✗ {path} ĐANG ĐƯỢC GIT THEO DÕI — không đọc giá trị secret từ file đã vào git.")
    out = {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            sys.exit(f"✗ {path}:{i} không phải dạng KEY=VALUE")
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def parse_set(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        if "=" not in p:
            sys.exit(f"✗ --set cần dạng KEY=VALUE, nhận được: {p}")
        k, _, v = p.partition("=")
        out[k.strip()] = v
    return out


# ── Hiển thị (KHÔNG BAO GIỜ in giá trị) ───────────────────────────────────────

def sha4(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:4]


def show_diff(current: dict, incoming: dict, replace: bool) -> dict:
    merged = dict(incoming) if replace else {**current, **incoming}
    added = sorted(k for k in incoming if k not in current)
    changed = sorted(k for k in incoming if k in current and current[k] != incoming[k])
    removed = sorted(k for k in current if k not in merged)
    kept = sorted(k for k in merged if k not in added and k not in changed)

    for k in added:
        print(f"  + THÊM  {k:<32} ({len(incoming[k])} ký tự)")
    for k in changed:
        print(f"  ~ ĐỔI   {k:<32} (sha {sha4(current[k])} → {sha4(incoming[k])},"
              f" {len(current[k])} → {len(incoming[k])} ký tự)")
    for k in removed:
        print(f"  - XOÁ   {k:<32} (--replace)")
    if kept:
        print(f"    giữ   {len(kept)} key: {', '.join(kept)}")
    if not (added or changed or removed):
        print("  (không có gì thay đổi)")
    return merged


# ── Lệnh ──────────────────────────────────────────────────────────────────────

def secrets_of(cfg: dict) -> tuple[object, set] | None:
    """(client, tên secret có thật) của account mà `cfg` trỏ tới; None nếu env này chưa có khóa."""
    if not (cfg.get("AWS_ACCESS_KEY_ID") and cfg.get("AWS_SECRET_ACCESS_KEY")):
        return None
    sm = client(cfg)
    have = set()
    for page in sm.get_paginator("list_secrets").paginate():
        for s in page["SecretList"]:
            have.add(s["Name"])
    return sm, have


def cmd_list(cfg: dict, args) -> None:
    # Mỗi env một cấu hình (PREPROD là account khác): gom env dùng chung khóa để chỉ liệt kê một lần.
    per_env, cache = {}, {}
    for env in ENVS:
        ecfg = env_cfg(env)
        ident = tuple(ecfg.get(k) for k in AWS_STS_KEYS + ("AWS_DEFAULT_REGION",))
        try:
            if ident not in cache:
                cache[ident] = secrets_of(ecfg)
            per_env[env] = (ecfg, cache[ident], None)
        except ClientError as exc:
            cache[ident] = None
            per_env[env] = (ecfg, None, exc.response.get("Error", {}).get("Code", "ClientError"))
    declared, _ = read_decl(schema_file(), "File khuôn phải có sẵn trong repo.")
    print(f"{'target':<24} {'env':<9} {'tên secret':<58} {'vỏ':<5} {'ver':<4} {'AWSCURRENT':<11} đổi lần cuối")
    for target in TARGETS:
        if target not in declared:
            # Target có trên đĩa nhưng khuôn KHÔNG khai → không có env nào cần đẩy lên SM
            # (vd app Flutter: cấu hình đi bằng --dart-define lúc build). Nói ra, đừng lặng.
            print(f"{target:<24} {'—':<9} {'(khuôn không khai — không dùng Secrets Manager)':<58}")
            continue
        for env in ENVS:
            ecfg, found, error = per_env[env]
            name = secret_name(ecfg, target, env)
            if found is None:
                why = f"(lỗi AWS {error} — làm mới: aws_session.py --env {env})" if error else f"(env {env} chưa có khóa AWS)"
                print(f"{target:<24} {env:<9} {name:<58} {'?':<5} {'-':<4} {'-':<11} {why}")
                continue
            sm, have = found
            if name not in have:
                print(f"{target:<24} {env:<9} {name:<58} {'—':<5} {'-':<4} {'-':<11} -")
                continue
            meta = describe(sm, name)
            stages = meta.get("VersionIdsToStages") or {}
            cur = "có" if current_version_id(meta) else "KHÔNG"
            last = meta.get("LastChangedDate")
            last = last.strftime("%Y-%m-%d %H:%M") if last else "-"
            print(f"{target:<24} {env:<9} {name:<58} {'có':<5} {len(stages):<4} {cur:<11} {last}")


def cmd_keys(cfg: dict, args) -> None:
    sm = client(cfg)
    name = secret_name(cfg, args.target, args.env)
    meta = describe(sm, name)
    if meta is None:
        sys.exit(f"✗ vỏ secret chưa tồn tại: {name}\n  Devops phải tạo trước — tool này không tự tạo.")

    payload, vid = read_payload(sm, name)
    template_keys, _ = read_decl(schema_file(), "File khuôn phải có sẵn trong repo.")
    from deploy_manifest import platform_contract, TOOLING
    inject = set(platform_contract(ROOT)) | set(TOOLING)
    exp = [key for key in template_keys.get(args.target, {}) if key not in inject]
    have = sorted(payload)

    print(f"{name}  (version {vid or 'CHƯA CÓ — secret rỗng'})")
    print(f"  key đang có ({len(have)}): {', '.join(have) if have else '(rỗng)'}")
    missing = [k for k in exp if k not in payload]
    extra = [k for k in have if k not in exp]
    print(f"  khuôn khai ({len(exp)}): {', '.join(exp)}")
    print(f"  ⚠ THIẾU ({len(missing)}): {', '.join(missing) if missing else '(không thiếu)'}")
    print(f"  ⚠ LẠ    ({len(extra)}): {', '.join(extra) if extra else '(không có)'}"
          + ("  ← không tự xoá, có thể devops thêm" if extra else ""))


def cmd_pull(cfg: dict, args) -> dict:
    from private_config import pull, declarations
    from deploy_manifest import read_manifest, targets
    names = targets(ROOT, read_manifest(ROOT))
    result = pull(client(cfg), cfg, declarations(schema_file()), names, config_file(args.env),
                  lambda values, target: secret_name(values, target, args.env), ROOT,
                  prefer_remote=getattr(args, 'prefer_remote', False))
    print(f"✓ Đã lấy toàn bộ cấu hình {args.env} vào {config_file(args.env).relative_to(ROOT)}; không in giá trị.")
    for item in result['conflicts']:
        print(f"⚠ {item['target']}: {item['key']} khác SM — giữ giá trị local, rà diff trước execute.")
    return result


def cmd_init(cfg: dict, args) -> None:
    """Lần đầu: tên key từ code (để rỗng), không sao chép giá trị example hoặc env khác."""
    from deploy_manifest import read_manifest, targets, platform_contract, TOOLING
    from env_inventory import inventory
    from private_config import write_sm, untracked
    manifest = read_manifest(ROOT)
    path = config_file(args.env)
    platform = platform_contract(ROOT, manifest)
    untracked(ROOT, path)
    reconcile = getattr(args, 'reconcile', False)
    if path.exists() and not reconcile:
        sys.exit('✗ file đã có; dùng pull/audit, không ghi đè bằng init.')
    schema, must_match = read_decl(schema_file()) if schema_file().exists() else ({}, set())
    values = read_decl(path)[0] if path.exists() else {}
    for target, row in targets(ROOT, manifest).items():
        if row['group'] != 'boundaries':
            continue
        used, _ = inventory(ROOT, row, manifest.get('env_helpers', ()))
        block = {key: '' for key in (*schema.get(target, {}), *platform)}
        for key, detail in used.items():
            if key in manifest.get('derived_env', ()) or key in manifest.get('local_only_env', ()):
                continue
            # Key mới vào file ở dạng RỖNG: chỉ giá trị người điền mới lên SM, mặc định để code tự lo.
            block[key] = ''
        block.update({key: value for key, value in values.get(target, {}).items() if value != ''})
        values[target] = block
        schema.setdefault(target, {}).update({key: '' for key in block if key not in schema.get(target, {})})
    write_sm(schema_file(), schema, must_match, (*platform, *TOOLING))
    write_sm(path, values, must_match, (*platform, *TOOLING))
    print(f'✓ Khởi tạo {path.relative_to(ROOT)} từ code; key mới để rỗng cho người điền, không in giá trị.')


def cmd_put(cfg: dict, args) -> None:
    incoming = {}
    if args.from_env_file:
        incoming.update(parse_env_file(Path(args.from_env_file)))
    if args.set:
        print("⚠ --set: giá trị nằm lại trong lịch sử shell. Dữ liệu dài hạn nên dùng --from-env-file.")
        incoming.update(parse_set(args.set))
    if not incoming:
        sys.exit("✗ chưa có gì để ghi — cần --from-env-file hoặc --set KEY=VALUE")

    sm = client(cfg)
    name = secret_name(cfg, args.target, args.env)
    meta = describe(sm, name)
    if meta is None:
        sys.exit(
            f"✗ vỏ secret chưa tồn tại: {name}\n"
            f"  Devops phải tạo trước (tool này KHÔNG BAO GIỜ create_secret).\n"
            f"  Tên sai cũng ra lỗi này — đối chiếu SM_NAME_TEMPLATE trong {ENV_FILE}."
        )

    current, pinned_vid = read_payload(sm, name)
    mode = "replace" if args.replace else "merge"
    if pinned_vid is None:
        print(f"{name}  (secret RỖNG — đây sẽ là version 1; CHƯA rollback được vì không có AWSPREVIOUS)")
    else:
        print(f"{name}  ({mode}, từ version {pinned_vid})")

    merged = show_diff(current, incoming, args.replace)

    if not args.yes:
        print("\nDRY-RUN — chưa ghi gì. Thêm --yes để ghi thật.")
        return

    # Ghim VersionId: kiểm lại AWSCURRENT còn đúng bản lúc đọc không.
    meta2 = describe(sm, name)
    if current_version_id(meta2) != pinned_vid:
        sys.exit(
            "✗ secret đã đổi từ lúc đọc (người/pipeline khác vừa ghi) — DỪNG, không ghi đè.\n"
            "  Chạy lại lệnh để merge trên nền mới."
        )

    sm.put_secret_value(
        SecretId=name,
        SecretString=json.dumps(merged, ensure_ascii=False, sort_keys=True),
    )
    meta3 = describe(sm, name)
    print(f"\n✓ đã ghi. version mới: {current_version_id(meta3)}")
    if previous_version_id(meta3):
        print(f"  hoàn tác: scripts/secrets_manager.py rollback {args.target} --env {args.env} --yes")


def load_values(env: str) -> dict[str, dict[str, str]]:
    """Đọc sm_configs.<env>.py + đối chiếu khuôn + kiểm must-match. Sai là thoát, không đẩy nửa vời."""
    path = config_file(env)
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)], cwd=ROOT, capture_output=True, text=True
    )
    if tracked.returncode == 0:
        sys.exit(
            f"✗ {path.name} ĐANG ĐƯỢC GIT THEO DÕI — không đọc giá trị thật từ file đã vào git.\n"
            f"  Gỡ khỏi git: git rm --cached .local/sm/{path.name}"
        )

    template_keys, must_match = read_decl(schema_file(), "File khuôn phải có sẵn trong repo.")
    data, _ = read_decl(
        path,
        f"Tạo bản cho môi trường {env.upper()}:\n"
        f"    cp .local/templates/{schema_file().name} .local/sm/{path.name}\n"
        f"  rồi điền giá trị thật của {env.upper()}.",
    )

    # Đối chiếu với khuôn — bắt lệch cả hai chiều, để hai file không trôi khỏi nhau.
    errors = []
    for target in data:
        if target not in template_keys:
            errors.append(f"  [{target}] không có trong {schema_file().name}")
    for target, keys in template_keys.items():
        block = data.get(target)
        if block is None:
            errors.append(f"  [{target}] thiếu hẳn trong {path.name}")
            continue
        for k in keys:
            if k not in block:
                errors.append(f"  [{target}] thiếu key {k}")
        # Key bổ sung từ code/SM được giữ. Audit code/khuôn chạy trước execute.
    if errors:
        sys.exit(f"✗ {path.name} lệch {schema_file().name}:\n" + "\n".join(errors))

    # must-match: key khai lặp ở nhiều target thì value phải giống hệt nhau.
    for k in sorted(must_match):
        vals = {t: d[k] for t, d in data.items() if d.get(k)}
        if len(set(vals.values())) > 1:
            by_hash: dict[str, list[str]] = {}
            for t, v in vals.items():
                by_hash.setdefault(sha4(v), []).append(t)
            sys.exit(
                f"✗ {k} khai must-match nhưng value LỆCH nhau giữa các target:\n"
                + "\n".join(f"    sha {s}: {', '.join(ts)}" for s, ts in by_hash.items())
                + "\n  Sửa cho giống hệt nhau rồi chạy lại."
            )

    # Placeholder chưa thay — đẩy nguyên <DOMAIN> lên là hỏng cả xác thực (JWT sai `iss`).
    con_placeholder = [
        f"  [{t}] {k}" for t, d in data.items() for k, v in d.items() if "<" in v and ">" in v
    ]
    if con_placeholder:
        sys.exit(
            f"✗ {path.name} còn placeholder chưa thay:\n"
            + "\n".join(con_placeholder)
            + "\n  Thay bằng giá trị thật của môi trường rồi chạy lại."
        )
    return data


def cmd_push(cfg: dict, args) -> None:
    from deploy_manifest import platform_contract, TOOLING
    platform = platform_contract(ROOT)
    values = load_values(args.env)
    targets = [args.target] if args.target else list(values)

    sm = client(cfg)
    plan = []
    errors = []
    expected = json.loads(Path(args.expected_versions).read_text())['secrets'] if getattr(args, 'expected_versions', None) else {}
    for target in targets:
        name = secret_name(cfg, target, args.env)
        filled = {k: v for k, v in values[target].items() if v != "" and k not in (*platform, *TOOLING)}
        unfilled = [k for k, v in values[target].items() if v == ""]

        print(f"\n── {target}  →  {name}")
        if unfilled:
            print(f"  ⊘ chưa điền, bỏ qua ({len(unfilled)}): {', '.join(unfilled)}")
        if not filled:
            print("  (không có value nào để đẩy)")
            continue

        meta = describe(sm, name)
        if meta is None:
            print("  ✗ vỏ secret chưa tồn tại — devops phải tạo trước, tool KHÔNG tự tạo. Bỏ qua.")
            errors.append(target)
            continue

        current, pinned = read_payload(sm, name)
        if expected and (target not in expected or expected[target]['version'] != pinned):
            errors.append(target + ':VersionId đã đổi')
            continue
        if args.only_new:
            filled = {k: v for k, v in filled.items() if k not in current}
            if not filled:
                print("  (--only-new: không có key mới nào)")
                continue
        merged = show_diff(current, filled, replace=False)
        if merged != current:
            plan.append((target, name, merged, pinned))

    if errors:
        sys.exit('✗ Không sync được SM: ' + ', '.join(errors) + '; chưa ghi secret nào, kiểm vỏ/quyền/VersionId.')
    if not plan:
        print("\nKhông có gì để ghi.")
        return
    if not args.yes:
        print(f"\nDRY-RUN — chưa ghi gì. {len(plan)} secret sẽ đổi. Thêm --yes để ghi thật.")
        return

    for target, name, merged, pinned in plan:
        meta2 = describe(sm, name)
        if current_version_id(meta2) != pinned:
            print(f"✗ {name}: đã đổi từ lúc đọc — BỎ QUA, không ghi đè người khác.")
            sys.exit('✗ VersionId đổi; dừng sync, chưa gửi build.')
        # VersionId lấy từ RESPONSE — xem ghi chú ở `cmd_delete_keys`: `describe` ngay sau
        # khi ghi có thể còn trả bản đồ stage cũ, in ra version id của lượt TRƯỚC.
        ver = sm.put_secret_value(
            SecretId=name, SecretString=json.dumps(merged, ensure_ascii=False, sort_keys=True)
        ).get("VersionId")
        print(f"✓ {name}: version mới {ver}")


def cmd_delete_keys(cfg: dict, args) -> None:
    """
    Xoá HẲN vài khoá khỏi payload của một secret.

    Vì sao cần một lệnh riêng: `push` cố ý chỉ MERGE — bỏ một khoá khỏi `sm_configs`
    làm nó thôi được ĐẨY, chứ không làm nó biến mất khỏi SM. Khoá bỏ lại như thế phần
    lớn vô hại (app validate env bằng zod, khoá lạ bị strip), nhưng có loại gây hiểu
    nhầm thật: một `KAFKA_BROKERS` cũ trỏ sai cổng nằm trên SM sẽ được người sau đọc
    như cấu hình đang chạy.

    KHÔNG dùng `put --replace` cho việc này: bản đó thay TOÀN BỘ payload bằng thứ mình
    đưa vào, nên muốn xoá một khoá lại phải gõ lại tất cả những khoá còn lại — kể cả
    các secret thật, trên dòng lệnh. Ở đây chỉ đụng đúng các khoá được nêu tên.
    """
    sm = client(cfg)
    name = secret_name(cfg, args.target, args.env)
    meta = describe(sm, name)
    if meta is None:
        sys.exit(f"✗ vỏ secret chưa tồn tại: {name}")

    current, pinned = read_payload(sm, name)
    present = [k for k in args.key if k in current]
    absent = [k for k in args.key if k not in current]

    print(f"{name}")
    for k in present:
        print(f"  - XOÁ   {k:<32} ({len(current[k])} ký tự)")
    for k in absent:
        print(f"  ⊘ không có sẵn, bỏ qua: {k}")
    if not present:
        print("  (không có gì để xoá)")
        return

    remaining = {k: v for k, v in current.items() if k not in present}
    print(f"    còn   {len(remaining)} key: {', '.join(sorted(remaining))}")
    if not args.yes:
        print(f"\nDRY-RUN — chưa ghi gì. Thêm --yes để xoá thật.")
        return

    # Cùng hàng rào với `push`: đọc lại version ngay trước khi ghi. Ai đó vừa đổi secret
    # giữa lúc mình đọc và lúc mình ghi thì DỪNG, không đè mất thay đổi của họ.
    meta2 = describe(sm, name)
    if current_version_id(meta2) != pinned:
        sys.exit(f"✗ {name}: đã đổi từ lúc đọc — KHÔNG ghi đè. Chạy lại lệnh này.")
    # Lấy VersionId TỪ RESPONSE, không `describe` lại: `describe_secret` nhất quán cuối,
    # nên đọc ngay sau khi ghi có thể trả về bản đồ stage CŨ — in ra một version id trông
    # hợp lệ mà thật ra là của lượt ghi trước, tức một con số sai để đi hoàn tác theo.
    ver = sm.put_secret_value(
        SecretId=name, SecretString=json.dumps(remaining, ensure_ascii=False, sort_keys=True)
    ).get("VersionId")
    print(f"✓ {name}: version mới {ver}")
    print(f"  hoàn tác: scripts/secrets_manager.py rollback {args.target} --env {args.env} --yes")


def cmd_rollback(cfg: dict, args) -> None:
    sm = client(cfg)
    name = secret_name(cfg, args.target, args.env)
    meta = describe(sm, name)
    if meta is None:
        sys.exit(f"✗ vỏ secret chưa tồn tại: {name}")

    cur, prev = current_version_id(meta), previous_version_id(meta)
    if not prev:
        sys.exit(f"✗ {name} không có AWSPREVIOUS — chưa có gì để hoàn tác.")

    print(f"{name}: AWSCURRENT {cur} → {prev}")
    if not args.yes:
        print("DRY-RUN — chưa đổi gì. Thêm --yes để làm thật.")
        return

    sm.update_secret_version_stage(
        SecretId=name, VersionStage="AWSCURRENT", MoveToVersionId=prev, RemoveFromVersionId=cur,
    )
    print(f"✓ đã trả AWSCURRENT về {prev}")


# Danh sách env đọc từ .local/env/.env.aws. Đọc SỚM vì argparse cần nó làm `choices` —
# gõ sai tên env thì chết ngay ở dòng lệnh, không phải sau khi đã gọi AWS. Chưa có file
# thì lấy mặc định chứ KHÔNG chết ở đây: `--help` phải đọc được trước khi có bí mật.
def _envs() -> list[str]:
    if not (ROOT / ENV_FILE).exists():
        return with_own_envs(env_list({}, "OPS_ENVS", "SIT,UAT,PT,PREPROD"))
    return with_own_envs(env_list(load_env_file(".env.aws"), "OPS_ENVS", "SIT,UAT,PT,PREPROD"))


ENVS = _envs()


def main() -> None:
    p = argparse.ArgumentParser(description="Xem/cập nhật AWS Secrets Manager cho các target của dự án")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help=f"Bảng {len(TARGETS)} target × {len(ENVS)} env")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("keys", help="Tên key trong secret + đối chiếu repo (KHÔNG in giá trị)")
    s.add_argument("target", choices=sorted(TARGETS))
    s.add_argument("--env", required=True, choices=ENVS)
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("pull", help="Lấy toàn bộ cấu hình môi trường từ SM vào file riêng tư; giữ giá trị local đã điền")
    s.add_argument("--env", required=True, choices=ENVS)
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser("init", help="Deploy lần đầu: dựng file từ tên env trong code (để rỗng), không copy giá trị môi trường khác")
    s.add_argument("--env", required=True, choices=ENVS)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("put", help="Ghi key vào secret (mặc định merge + dry-run)")
    s.add_argument("target", choices=sorted(TARGETS))
    s.add_argument("--env", required=True, choices=ENVS)
    s.add_argument("--from-env-file", help="File KEY=VALUE (không được nằm trong git)")
    s.add_argument("--set", action="append", help="KEY=VALUE, lặp được")
    s.add_argument("--replace", action="store_true", help="Thay TOÀN BỘ — key không nhắc tới BỊ XOÁ")
    s.add_argument("--yes", action="store_true", help="Ghi thật (mặc định chỉ dry-run)")
    s.set_defaults(func=cmd_put)

    s = sub.add_parser("push", help="Đẩy value từ file lên SM theo schema (merge + dry-run)")
    s.add_argument("--env", required=True, choices=ENVS)
    s.add_argument("--target", choices=sorted(TARGETS), help="Mặc định: tất cả target trong schema")
    s.add_argument("--only-new", action="store_true", help="CHỈ thêm key mới, không đụng key đã có")
    s.add_argument("--yes", action="store_true", help="Ghi thật (mặc định chỉ dry-run)")
    s.add_argument("--expected-versions", help="Proof của lượt đã dry-run; chặn khi VersionId SM thay đổi")
    s.set_defaults(func=cmd_push)

    s = sub.add_parser("delete-keys", help="Xoá hẳn vài khoá khỏi secret (mặc định dry-run)")
    s.add_argument("target", choices=sorted(TARGETS))
    s.add_argument("--env", required=True, choices=ENVS)
    s.add_argument("--key", action="append", required=True, help="Tên khoá cần xoá, lặp được")
    s.add_argument("--yes", action="store_true", help="Xoá thật (mặc định chỉ dry-run)")
    s.set_defaults(func=cmd_delete_keys)

    s = sub.add_parser("rollback", help="Trả AWSCURRENT về AWSPREVIOUS")
    s.add_argument("target", choices=sorted(TARGETS))
    s.add_argument("--env", required=True, choices=ENVS)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_rollback)

    args = p.parse_args()
    cfg = load_cfg(getattr(args, "env", None))
    args.func(cfg, args)


if __name__ == "__main__":
    main()
