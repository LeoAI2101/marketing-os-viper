"""Tag đang chạy theo job Jenkins của từng service — nguồn mặc định, chỉ GET.

Mỗi job env (<nhóm boundaries>/<service>, <nhóm experiences>/<service>)
kết thúc bằng Helm deploy (boundary) hoặc đẩy S3/CloudFront (web); Helm lỗi thì build FAILURE.
`lastSuccessfulBuild` của Jenkins tính cả UNSTABLE (Sonar/test) — đúng là bản đang chạy.
  rc_tag     — RC_TAG trên form (SIT không có RC)
  commit_tag — IMAGE_TAG (boundary) / SHORT_COMMIT (web) trên form; trống thì lấy revision
               Git của đúng repo service (SIT: image được tag bằng 8 ký tự đầu commit này),
               bỏ repo jenkins-central của pipeline.
  max_rc     — RC cao nhất từng gửi trong lịch sử, kể cả build lỗi: build lỗi vẫn có thể đã
               push tag đó lên ECR, RC kế tiếp phải vượt nó.
Không thấy được rollback làm tay ngoài Jenkins (Argo/kubectl).
"""
from __future__ import annotations

from datetime import datetime

import requests

from jenkins_build import auth, get_retry, job_url, load_cfg
from release_common import COMMIT_PATTERN, RC_PATTERN, RELEASE_PATTERN, ReleaseError
from release_ecr import rc_key

HISTORY = 100
BUILD = "number,result,building,timestamp,actions[parameters[name,value],lastBuiltRevision[SHA1],remoteUrls]"
TREE = f"lastSuccessfulBuild[{BUILD}],builds[{BUILD}]{{0,{HISTORY}}}"
COMMIT_PARAMS = ("IMAGE_TAG", "SHORT_COMMIT")


def build_commit(build: dict, service: str) -> str | None:
    for action in build.get("actions", []):
        urls = action.get("remoteUrls") or []
        if any(u.rstrip("/").removesuffix(".git").endswith("/" + service) for u in urls):
            sha = (action.get("lastBuiltRevision") or {}).get("SHA1")
            return sha[:8] if sha else None
    return None


def build_params(build: dict) -> dict:
    return {p["name"]: p.get("value") for a in build.get("actions", [])
            for p in (a.get("parameters") or [])}


def valid_rc(value) -> str | None:
    return value if RC_PATTERN.fullmatch(value or "") else None


RELEASE_PARAMS = ("RELEASE_TAG", "RC_TAG", "IMAGE_TAG", "GIT_TAG")


def release_tag_of(params: dict) -> str | None:
    """Tag release PROD (vX.Y.Z, không -rc-) đọc từ form build; tên tham số chưa cố định nên thử lần lượt."""
    return next((params[k] for k in RELEASE_PARAMS if RELEASE_PATTERN.fullmatch(params.get(k) or "")), None)


def running_from(build: dict | None, service: str) -> dict | None:
    if not build:
        return None
    params = build_params(build)
    commit = next((params[k] for k in COMMIT_PARAMS if COMMIT_PATTERN.fullmatch(params.get(k) or "")),
                  None) or build_commit(build, service)
    return {
        "rc_tag": valid_rc(params.get("RC_TAG")) or valid_rc(params.get("IMAGE_TAG")), "commit_tag": commit,
        "release_tag": release_tag_of(params),
        "build_number": build["number"], "result": None if build.get("building") else build.get("result"),
        "deployed_at": datetime.fromtimestamp(build["timestamp"] / 1000).astimezone().isoformat(
            timespec="seconds") if build.get("timestamp") else None,
        "digest": None,
    }


class JenkinsReader:
    def __init__(self, env: str, cfg: dict | None = None):
        self.env, self.cfg = env, cfg or load_cfg(env)

    def coordinates(self) -> dict:
        return {"jenkins": self.cfg["JENKINS_BASE_URL"]}

    def snapshot(self, target: str, group: str, service: str) -> dict:
        url = job_url(self.cfg, target, self.env, service)
        try:
            resp = get_retry(f"{url}api/json", params={"tree": TREE},
                             auth=auth(self.cfg), allow_redirects=False)
        except requests.RequestException:
            raise ReleaseError("Jenkins: lỗi kết nối hoặc quá thời gian chờ.") from None
        if resp.status_code != 200:
            raise ReleaseError(f"Jenkins {self.env} {service}: HTTP {resp.status_code}")
        job = resp.json()
        rcs = [rc for b in job.get("builds", []) if (rc := valid_rc(build_params(b).get("RC_TAG")))]
        running = running_from(job.get("lastSuccessfulBuild"), service)
        builds = [running_from(b, service) for b in job.get("builds", [])]
        if running and running["build_number"] not in {b["build_number"] for b in builds}:
            builds.append(running)          # bản đang chạy cũ hơn cửa sổ HISTORY build
        return {"repository": url, "basis": "jenkins-last-success", "running": running,
                "latest_pushed": running, "max_rc": max(rcs, key=rc_key) if rcs else None,
                "builds": builds}
