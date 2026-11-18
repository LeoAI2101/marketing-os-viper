#!/usr/bin/env python3
"""ops_env.py — phần dùng chung của bộ tool ops (secrets_manager · jenkins_build · sonar).

VÌ SAO CÓ FILE NÀY
    Ba tool ops đứng NGOÀI luồng VIPER (không phải một trong 6 lệnh hợp đồng, không ghi
    context/DECISIONS.md hay STATE.md). Cả ba đều cần đúng hai thứ: (a) đọc file env của
    own_file chúng ở `.local/env/`, (b) biết repo này ĐANG CÓ target nào. Viết ba lần là ba
    danh sách target rồi lệch nhau đúng vào vòng có target mới.

DANH SÁCH TARGET — DÒ TỪ ĐĨA, KHÔNG GÕ TAY
    `srcroot/<nhóm>/<tên>/` với nhóm ∈ boundaries · web-experiences · mobile-experiences
    (VIPER.md §1.3). Thư mục `{{...-NAME}}` của template và thư mục chưa có manifest
    (package.json / pubspec.yaml) đều bị loại — chúng chưa phải target thật.

    Gõ tay thì mỗi vòng mở thêm boundary là ba file phải nhớ sửa; dò từ đĩa thì thêm
    target xong là ba tool thấy ngay. Vẫn là rào an toàn: chỉ tên CÓ TRÊN ĐĨA mới hợp lệ,
    không nhận tên tuỳ ý và không thao tác theo prefix.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# VIPER_OPS_ROOT: trỏ bộ tool ops sang một cây khác (test dựng repo mẫu tạm, scripts/tests/release_fixtures.py).
ROOT = Path(os.environ.get("VIPER_OPS_ROOT") or Path(__file__).resolve().parent.parent.parent)
SRCROOT = ROOT / "srcroot"

# File thật nằm ở `.local/env/`; khuôn công khai ở `.local/templates/`.
# vì chúng là cấu hình để CHẠY/ĐẨY sản phẩm chứ không phải mã nguồn (deployment/README.md).
ENV_DIR = ROOT / ".local" / "env"
TEMPLATE_DIR = ROOT / ".local" / "templates"

GROUPS = ("boundaries", "web-experiences", "mobile-experiences")
MANIFESTS = ("package.json", "pubspec.yaml")


# ── Target ────────────────────────────────────────────────────────────────────

def discover_targets(groups: tuple[str, ...] = GROUPS) -> dict[str, str]:
    """{tên target: nhóm srcroot}. Thứ tự: theo `groups`, trong nhóm thì theo alphabet."""
    if (ROOT / 'deployment/deploy-manifest.json').exists():
        # Hai kiểu nạp cùng tồn tại: `scripts/lib` trên sys.path (`from ops_env import …`) và `scripts`
        # trên sys.path (`from lib.ops_env import …` — các checker .mjs, hook guard_write). Kiểu sau không
        # thấy `deploy_manifest` trần ⇒ checker chết trước khi kiểm và hook chặn MỌI lượt ghi boundary.
        try:
            from deploy_manifest import read_manifest, targets
        except ModuleNotFoundError:
            from lib.deploy_manifest import read_manifest, targets
        # require_any=False: dự án mới chưa scaffold target nào thì vẫn import được tool (`--help`);
        # lệnh cần một target cụ thể tự báo «target lạ» qua require_target.
        return {name: row['group'] for name, row in targets(ROOT, read_manifest(ROOT), require_any=False).items()
                if row['group'] in groups}
    found: dict[str, str] = {}
    for group in groups:
        base = SRCROOT / group
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if "{{" in d.name:                                   # chỗ giữ nhịp của template
                continue
            if not any((d / m).exists() for m in MANIFESTS):     # chưa scaffold
                continue
            found[d.name] = group
    if not found:
        sys.exit(
            "✗ không thấy target nào trong srcroot/ — chưa scaffold, hoặc đang chạy tool\n"
            "  từ ngoài repo. Pha I dựng target đầu tiên (/viper-implement)."
        )
    return found


def require_target(targets: dict[str, str], name: str) -> str:
    if name not in targets:
        sys.exit(f"✗ không rõ target '{name}'. Hợp lệ: {', '.join(sorted(targets))}")
    return targets[name]


def env_key(target: str) -> str:
    """Tên target → mảnh tên biến môi trường: d-orders → D_ORDERS, web-portal → WEB_PORTAL."""
    return target.upper().replace("-", "_")


# ── File env ──────────────────────────────────────────────────────────────────

def _is_tracked(path: Path) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    return r.returncode == 0


def load_env_file(name: str, required: tuple[str, ...] = (), goi_y: str = "") -> dict[str, str]:
    """Đọc .local/env/<name> dạng KEY=VALUE.

    Có `<name>.example` cùng chỗ được commit (chỉ TÊN biến, không giá trị) — file này thì
    khớp mẫu `.env.*` của .gitignore nên không có đường nào commit nhầm. Nếu nó lỡ vào git
    thì DỪNG: đọc bí mật từ file đã có trong lịch sử là coi như bí mật đó đã lộ.
    """
    path = ENV_DIR / name
    if not path.exists():
        example_path = TEMPLATE_DIR / f"{name}.example"
        sys.exit(
            f"✗ thiếu .local/env/{name}"
            + (f" ({', '.join(required)})" if required else "")
            + (f"\n  Tạo: cp .local/templates/{example_path.name} .local/env/{name}   rồi điền giá trị thật."
               if example_path.exists() else "")
            + (f"\n  {goi_y}" if goi_y else "")
        )
    if _is_tracked(path):
        sys.exit(
            f"✗ deployment/{name} ĐANG ĐƯỢC GIT THEO DÕI — không đọc bí mật từ file đã vào git.\n"
            f"  Gỡ khỏi git: git rm --cached deployment/{name}"
        )

    values: dict[str, str] = {}
    for i, line in enumerate(path.read_text().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if "=" not in s:
            sys.exit(f"✗ deployment/{name}:{i} không phải dạng KEY=VALUE")
        k, _, v = s.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        values[k.strip()] = v

    missing_keys = [k for k in required if not values.get(k)]
    if missing_keys:
        sys.exit(f"✗ deployment/{name} thiếu biến: {', '.join(missing_keys)}")
    return values


def env_file_name(base: str, env: str | None, sep: str = ".") -> str:
    """Tên file env của MỘT môi trường: có file own_file thì dùng nó, không thì file chung.

    `.env.aws.<env>` và `.env.jenkins-<env>` (vd PROD: account AWS và Jenkins tách hẳn nonprod) là
    file ĐỘC LẬP, tự đủ khoá — không phủ lên file chung như khoá hậu tố `_<ENV>` của PREPROD.
    """
    if env:
        own_file = f"{base}{sep}{env.lower()}"
        if (ENV_DIR / own_file).exists():
            return own_file
    return base


def aws_file(env: str | None) -> str:
    return env_file_name(".env.aws", env, ".")


def jenkins_file(env: str | None) -> str:
    return env_file_name(".env.jenkins", env, "-")


def has_own_aws_file(env: str | None) -> bool:
    return aws_file(env) != ".env.aws"


def with_own_envs(envs: list[str]) -> list[str]:
    """Thêm env có file Jenkins/AWS own_file (vd PROD) vào danh sách OPS_ENVS đọc từ file chung."""
    extra = [e for e in ("PROD",) if e not in envs and ((ENV_DIR / f".env.jenkins-{e.lower()}").exists()
                                                       or (ENV_DIR / f".env.aws.{e.lower()}").exists())]
    return envs + extra


AWS_STS_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
AWS_PER_ENV_PREFIXES = ("AWS_", "SM_")


def own_aws_account(raw: dict[str, str], env: str | None) -> bool:
    """Môi trường khai `AWS_SSO_ACCOUNT_ID_<ENV>` là có account AWS own_file (vd PREPROD)."""
    return bool(env and raw.get(f"AWS_SSO_ACCOUNT_ID_{env.upper()}"))


def aws_env_cfg(raw: dict[str, str], env: str | None) -> dict[str, str]:
    """Cấu hình AWS của MỘT môi trường, từ nội dung `.env.aws` đã đọc.

    Khoá `<KEY>_<ENV>` (chỉ tiền tố AWS_ và SM_) thắng khoá chung `<KEY>`: PREPROD khai
    `AWS_SSO_ACCOUNT_ID_PREPROD`, `AWS_ACCESS_KEY_ID_PREPROD`... còn start URL, vùng SSO, role,
    vùng mặc định, quy ước tên secret kế thừa khoá chung.

    Env có account own_file thì ba khoá STS CHỈ lấy từ `_<ENV>` — thiếu thì để trống, KHÔNG rơi về
    khoá chung: rơi là gọi Secrets Manager của account này bằng khoá account kia.
    """
    cfg = dict(raw)
    if not env:
        return cfg
    suffix = f"_{env.upper()}"
    for key, value in raw.items():
        if key.endswith(suffix) and key.startswith(AWS_PER_ENV_PREFIXES) and value != "":
            cfg[key[: -len(suffix)]] = value
    if own_aws_account(raw, env):
        for key in AWS_STS_KEYS:
            cfg[key] = raw.get(key + suffix, "")
    return cfg


def env_list(cfg: dict[str, str], key: str, default: str) -> list[str]:
    """Biến dạng danh sách ngăn bằng dấu phẩy — vd OPS_ENVS=SIT,UAT,PREPROD."""
    raw = cfg.get(key) or default
    return [x.strip().upper() for x in raw.split(",") if x.strip()]
