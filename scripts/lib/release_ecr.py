"""Nguồn tuỳ chọn (`prepare_deploy.py --ecr`): đọc tag image trên ECR, chỉ API đọc.

Cần khoá có ecr:DescribeImages ở account giữ ECR (thường KHÁC account của khoá SSO developer).
Tên repo thật đọc từ log Jenkins, dạng:
  <ECR_REGISTRY_ID>.dkr.ecr.<region>.amazonaws.com/<ECR_REPOSITORY_TEMPLATE>

"Tag đang chạy" suy từ ECR, không hỏi cluster:
  1. Có ECR_RUNNING_TAG[_<ENV>] (tag trôi pipeline gắn cho bản đang chạy, vd `uat`)
     → image mang tag đó.
  2. Không có → image mang RC cao nhất (so semver rồi số rc) trong repo của env.
Commit tag là tag dạng mã commit cùng digest với image đã chọn.
"""
from __future__ import annotations

import os
import re

from ops_env import ENV_DIR, aws_env_cfg, aws_file, env_key, has_own_aws_file, load_env_file, own_aws_account
from release_common import COMMIT_PATTERN, RC_PATTERN, ReleaseError, require

RC_PARTS = re.compile(r"v(\d+)\.(\d+)\.(\d+)-rc-(\d+)")
FIRST_RC = "v1.0.0-rc-1"


def ecr_config(env: str) -> dict:
    name = aws_file(env)
    raw = load_env_file(name) if (ENV_DIR / name).exists() else {}
    cfg = aws_env_cfg(raw, env)
    # Env có account riêng: khóa trong file thắng khóa AWS_* đang export ở shell (shell thường giữ khóa account khác).
    if not own_aws_account(raw, env) and not has_own_aws_file(env):
        cfg.update({k: v for k, v in os.environ.items() if k.startswith("AWS_")})
    cfg.update({k: v for k, v in os.environ.items() if k.startswith("ECR_")})
    require(cfg, ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"))
    for key in ("ECR_REGISTRY_ID", "ECR_REPOSITORY_TEMPLATE", "ECR_RUNNING_TAG", "AWS_DEFAULT_REGION"):
        if cfg.get(f"{key}_{env.upper()}"):
            cfg[key] = cfg[f"{key}_{env.upper()}"]
    # Không có mặc định: account ECR, region, tên repo là của tổ chức (.local/env/.env.aws).
    require(cfg, ("AWS_DEFAULT_REGION", "ECR_REGISTRY_ID", "ECR_REPOSITORY_TEMPLATE"))
    return cfg


def rc_key(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in RC_PARTS.fullmatch(tag).groups())


def next_rc(tag: str | None) -> str:
    if not tag:
        return FIRST_RC
    major, minor, patch, rc = rc_key(tag)
    return f"v{major}.{minor}.{patch}-rc-{rc + 1}"


def repository_name(cfg: dict, env: str, target: str, group: str, service: str) -> str:
    template = cfg.get(f"ECR_REPOSITORY_{env_key(target)}") or cfg.get("ECR_REPOSITORY_TEMPLATE")
    if not template:
        raise ReleaseError("Thiếu ECR_REPOSITORY_TEMPLATE ở .local/env/.env.aws — tên repo ECR của tổ chức.")
    try:
        return template.format(env=env.lower(), service=service, target=target, group=group)
    except (KeyError, ValueError):
        raise ReleaseError("ECR_REPOSITORY_* chỉ nhận {env} {service} {target} {group}.") from None


def describe(image: dict) -> dict:
    tags = sorted(image.get("imageTags") or [])
    rcs = sorted((t for t in tags if RC_PATTERN.fullmatch(t)), key=rc_key)
    commits = [t for t in tags if COMMIT_PATTERN.fullmatch(t) and not t.isdigit()]
    pulled = image.get("lastRecordedPullTime")
    return {
        "rc_tag": rcs[-1] if rcs else None,
        "commit_tag": commits[0] if commits else None,
        "tags": tags,
        "digest": image["imageDigest"],
        "pushed_at": image["imagePushedAt"].isoformat(),
        "last_pulled_at": pulled.isoformat() if pulled else None,
    }


def pick_running(images: list[dict], running_tag: str | None) -> tuple[dict | None, str]:
    if running_tag:
        hit = [i for i in images if running_tag in (i.get("imageTags") or [])]
        return (describe(hit[0]) if hit else None), f"running-tag:{running_tag}"
    with_rc = [describe(i) for i in images]
    with_rc = [i for i in with_rc if i["rc_tag"]]
    return (max(with_rc, key=lambda i: rc_key(i["rc_tag"])) if with_rc else None), "highest-rc"


def pick_latest(images: list[dict]) -> dict | None:
    tagged = [i for i in images if i.get("imageTags")]
    return describe(max(tagged, key=lambda i: i["imagePushedAt"])) if tagged else None


class EcrReader:
    def __init__(self, cfg: dict, env: str, client=None):
        self.cfg, self.env = cfg, env
        if client is None:
            import boto3
            client = boto3.client(
                "ecr", region_name=cfg["AWS_DEFAULT_REGION"],
                aws_access_key_id=cfg["AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=cfg["AWS_SECRET_ACCESS_KEY"],
                aws_session_token=cfg.get("AWS_SESSION_TOKEN") or None)
        self.client = client

    def coordinates(self) -> dict:
        return {"region": self.cfg["AWS_DEFAULT_REGION"],
                "registry_id": self.cfg.get("ECR_REGISTRY_ID") or None,
                "running_tag": self.cfg.get("ECR_RUNNING_TAG") or None}

    def images(self, repository: str) -> list[dict]:
        query = {"repositoryName": repository, "filter": {"tagStatus": "TAGGED"}}
        if self.cfg.get("ECR_REGISTRY_ID"):
            query["registryId"] = self.cfg["ECR_REGISTRY_ID"]
        try:
            pages = self.client.get_paginator("describe_images").paginate(**query)
            return [image for page in pages for image in page.get("imageDetails", [])]
        except Exception as exc:  # botocore ClientError/NoCredentials — chỉ lộ mã lỗi
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            raise ReleaseError(f"ECR {repository}: {code}") from None

    def snapshot(self, target: str, group: str, service: str) -> dict:
        repository = repository_name(self.cfg, self.env, target, group, service)
        images = self.images(repository)
        running, basis = pick_running(images, self.cfg.get("ECR_RUNNING_TAG"))
        rcs = [t for i in images for t in (i.get("imageTags") or []) if RC_PATTERN.fullmatch(t)]
        return {"repository": repository, "basis": basis, "running": running,
                "latest_pushed": pick_latest(images), "max_rc": max(rcs, key=rc_key) if rcs else None,
                "releases": [describe(i) for i in images if i.get("imageTags")]}
