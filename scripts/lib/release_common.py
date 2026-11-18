"""Cấu hình, mapping target → service và ghi JSON dùng chung cho công cụ chuẩn bị tag."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ops_env import ENV_DIR, ROOT, discover_targets, env_list, load_env_file, with_own_envs

RC_PATTERN = re.compile(r"v\d+\.\d+\.\d+-rc-[1-9]\d*")
RELEASE_PATTERN = re.compile(r"v\d+\.\d+\.\d+")      # tag release PROD, không có -rc-
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")


class ReleaseError(Exception):
    """Lỗi có thông điệp an toàn để hiển thị, không chứa response/token."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def config(name: str, prefix: str) -> dict:
    values = load_env_file(name) if (ENV_DIR / name).exists() else {}
    values.update({k: v for k, v in os.environ.items() if k.startswith(prefix)})
    return values


def require(cfg: dict, keys: tuple[str, ...]) -> None:
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        raise ReleaseError("Thiếu cấu hình: " + ", ".join(missing))


def environments() -> list[str]:
    values = with_own_envs(env_list(config(".env.jenkins", "OPS_"), "OPS_ENVS", "SIT,UAT,PT,PREPROD"))
    if not values or any(not re.fullmatch(r"[A-Z][A-Z0-9_-]*", value) for value in values):
        raise ReleaseError("OPS_ENVS phải chứa tên môi trường hợp lệ, ngăn bằng dấu phẩy.")
    return values


DEPLOY_GROUPS = ("boundaries", "web-experiences")


def service_map() -> dict[str, tuple[str, str]]:
    """{target: (nhóm srcroot, tên service)} cho mọi target có image — boundaries + web."""
    if (ROOT / 'deployment/deploy-manifest.json').exists():
        from deploy_manifest import read_manifest, targets
        return {name: (row['group'], row['service'])
                for name, row in targets(ROOT, read_manifest(ROOT), require_any=False).items()
                if row['group'] in DEPLOY_GROUPS}
    targets = discover_targets(DEPLOY_GROUPS)
    result = {}
    for line in (ROOT / "deployment/gitlab-repo-map.tsv").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        source, remote = line.split()
        group, target = source.split("/", 1)
        if group in DEPLOY_GROUPS and target in targets:
            service = remote.rsplit("/", 1)[-1]
            if not NAME_PATTERN.fullmatch(service) or service in (s for _, s in result.values()):
                raise ReleaseError("Mapping service không hợp lệ hoặc bị trùng.")
            result[target] = (group, service)
    return result


def select_targets(names: list[str]) -> dict[str, tuple[str, str]]:
    """Không truyền tên nào → toàn bộ boundaries + web."""
    mapping = service_map()
    if not names:
        return mapping
    reverse = {service: target for target, (_, service) in mapping.items()}
    selected = {}
    for name in names:
        target = name if name in mapping else reverse.get(name)
        if not target:
            raise ReleaseError(f"'{name}' không có trong bảng mapping boundaries/web.")
        if target in selected:
            raise ReleaseError("Danh sách có service trùng nhau.")
        selected[target] = mapping[target]
    return selected


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
