#!/usr/bin/env python3
"""Đọc đầu nhánh main của mọi repo theo mapping của sync_srcroot_to_gitlab.sh.

Chỉ git ls-remote; không clone, fetch, checkout, push hoặc kích hoạt pipeline.
Đọc cùng .local/env/.env.gitlab-sync, GITLAB_GROUP và SYNC_MAP_FILE.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ops_env import ROOT, discover_targets
from release_common import ReleaseError, config, now, require, write_json

MAIN_REF = "refs/heads/main"


def load_config() -> dict:
    cfg = config(".env.gitlab-sync", "GITLAB_")
    if "SYNC_MAP_FILE" in os.environ:
        cfg["SYNC_MAP_FILE"] = os.environ["SYNC_MAP_FILE"]
    # Không có mặc định: host/nhóm là của tổ chức — khai ở .local/env/.env.gitlab-sync.
    require(cfg, ("GITLAB_HOST", "GITLAB_GROUP"))
    cfg["GITLAB_PROTO"] = cfg.get("GITLAB_PROTO") or ("https" if cfg.get("GITLAB_TOKEN") else "ssh")
    if cfg["GITLAB_PROTO"] not in ("https", "ssh"):
        raise ReleaseError("GITLAB_PROTO chỉ nhận https hoặc ssh.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?", cfg["GITLAB_HOST"]):
        raise ReleaseError("GITLAB_HOST phải là hostname[:port], không chứa credential hoặc đường dẫn.")
    if cfg["GITLAB_PROTO"] == "https":
        require(cfg, ("GITLAB_USER", "GITLAB_TOKEN"))
    return cfg


def project_path(group: str, sub: str) -> str:
    path = group.strip("/") + "/" + sub
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", path)
            or any(p in (".", "..") for p in path.split("/"))):
        raise ReleaseError("Đường dẫn repo trong cấu hình/mapping không hợp lệ.")
    return path


def repo_list(cfg: dict) -> list[dict]:
    configured = cfg.get("SYNC_MAP_FILE")
    entries = {}
    if (ROOT / 'deployment/deploy-manifest.json').exists():
        from deploy_manifest import read_manifest, targets
        manifest = read_manifest(ROOT)
        entries = targets(ROOT, manifest)
        configured = configured or manifest.get('mapping_file')
    path = Path(configured) if configured else ROOT / "deployment/gitlab-repo-map.tsv"
    if not path.is_absolute():
        path = ROOT / path
    mapping = {}
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if len(parts) < 2:
                raise ReleaseError("Bảng mapping GitLab thiếu cột đường dẫn repo.")
            mapping.setdefault(parts[0], parts[1])  # Cùng quy tắc dòng đầu của awk trong script sync.
    rows = []
    for target, group in discover_targets().items():
        entry = entries.get(target, {})
        source = entry.get('path', f"{group}/{target}").removeprefix('srcroot/')
        sub = entry.get('repository') or (mapping.get(entry.get('path')) or mapping.get(source) if path.exists() else source)
        row = {"target": target, "group": group, "branch": "main", "repository": None}
        if sub:
            row["repository"] = project_path(cfg["GITLAB_GROUP"], sub)
        else:
            row["error"] = "Thiếu dòng mapping; bỏ qua như script sync."
        rows.append(row)
    return rows


def git_environment(cfg: dict) -> dict:
    # Token chỉ đi qua môi trường tiến trình con, không qua URL/argv hoặc file tạm.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_TRACE", "GIT_CONFIG_"))
           and k not in ("GIT_CURL_VERBOSE", "GITLAB_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if cfg["GITLAB_PROTO"] == "https":
        credential = base64.b64encode(f"{cfg['GITLAB_USER']}:{cfg['GITLAB_TOKEN']}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"http.https://{cfg['GITLAB_HOST']}/.extraHeader",
                    "GIT_CONFIG_VALUE_0": "Authorization: Basic " + credential})
    else:
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=10"
    return env


def read_main(cfg: dict, project: str) -> dict:
    host = cfg["GITLAB_HOST"]
    remote = (f"https://{host}/{project}.git" if cfg["GITLAB_PROTO"] == "https"
              else f"ssh://git@{host}/{project}.git")
    command = ["git", "-c", "credential.helper=", "-c", "http.followRedirects=false",
               "ls-remote", "--exit-code", remote, MAIN_REF]
    try:
        result = subprocess.run(command, env=git_environment(cfg), cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise ReleaseError("Quá 30 giây chờ GitLab.") from None
    if result.returncode == 2:
        raise ReleaseError("Repo chưa có nhánh main.")
    if result.returncode:
        raise ReleaseError(f"Không đọc được repo (git exit {result.returncode}); kiểm URL/quyền/token.")
    lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if (len(lines) != 1 or len(lines[0]) != 2 or lines[0][1] != MAIN_REF
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", lines[0][0])):
        raise ReleaseError("GitLab không trả đúng một commit hợp lệ ở refs/heads/main.")
    sha = lines[0][0]
    return {"commit_short": sha[:8], "commit_sha": sha,
            "commit_url": f"https://{host}/{project}/-/commit/{sha}"}


def run(args) -> int:
    if args.output and args.output.exists():
        raise ReleaseError("File output đã tồn tại; chọn tên mới để giữ kết quả trước.")
    cfg = load_config()
    rows = repo_list(cfg)
    print(f"{'TARGET':<28} {'COMMIT MAIN':<12} REPO")
    for row in rows:
        if row["repository"]:
            try:
                row.update(read_main(cfg, row["repository"]))
            except (ReleaseError, OSError) as exc:
                row["error"] = str(exc) if isinstance(exc, ReleaseError) else "Không gọi được git."
        print(f"{row['target']:<28} {row.get('commit_short', '—'):<12} {row['repository'] or '—'}")
        if row.get("error"):
            print("  Lỗi: " + row["error"])
    if args.output:
        write_json(args.output, {"created_at": now(), "branch": "main", "repositories": rows})
        print(f"\nĐã ghi {args.output}")
    errors = sum(bool(row.get("error")) for row in rows)
    print(f"\nĐọc được {len(rows) - errors}/{len(rows)} repo; chỉ đọc nhánh main.")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Lấy commit mới nhất trên main của toàn bộ repo GitLab đã mapping")
    parser.add_argument("--output", type=Path, help="Ghi thêm JSON; mặc định chỉ in bảng")
    try:
        return run(parser.parse_args())
    except (ReleaseError, OSError) as exc:
        print(f"Lỗi: {exc if isinstance(exc, ReleaseError) else 'Không đọc/ghi được file.'}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Đã dừng đọc GitLab.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
