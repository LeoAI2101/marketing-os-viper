"""ECR/Jenkins giả lập trong tiến trình; không mở cổng hoặc gọi mạng."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))


def _sample_repo() -> Path:
    """Repo mẫu tạm cho bộ tool ops: srcroot/ có target giả + manifest + repo map (fixtures/).

    Tool dò target từ ĐĨA lúc import; template VIPER chưa có target nào, nên test không được
    dựa vào srcroot/ thật của repo. Đặt VIPER_OPS_ROOT TRƯỚC khi import bất kỳ module ops nào.
    """
    import atexit
    import os
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="viper-ops-test-"))
    atexit.register(shutil.rmtree, root, True)
    for rel in ("boundaries/d-orders", "boundaries/d-identity-trust", "boundaries/d-one", "boundaries/d-two",
                "boundaries/bff-auth-client", "web-experiences/ops-console-web",
                "mobile-experiences/sample-mobile-app"):
        (root / "srcroot" / rel).mkdir(parents=True)
        # Manifest chỉ nhận thư mục có file dự án — giống target thật đã scaffold.
        marker = "pubspec.yaml" if rel.startswith("mobile-") else "package.json"
        (root / "srcroot" / rel / marker).write_text("{}\n" if marker == "package.json" else "name: sample\n")
    (root / "deployment").mkdir()
    fixtures = Path(__file__).resolve().parent / "fixtures"
    shutil.copy(fixtures / "deploy-manifest.json", root / "deployment" / "deploy-manifest.json")
    shutil.copy(fixtures / "gitlab-repo-map.tsv", root / "deployment" / "gitlab-repo-map.tsv")
    shutil.copytree(SCRIPTS.parent / ".local" / "templates", root / ".local" / "templates")
    os.environ["VIPER_OPS_ROOT"] = str(root)
    return root


SAMPLE_ROOT = _sample_repo()

JENKINS = "https://jenkins.example.test"
SERVICE = "d-bff-auth-client"
TARGET = "bff-auth-client"
COMMIT = "0a1b2c3d4e5f6789"
SECRET = "NEVER_PRINT_THIS_SECRET"
# Mọi khoá của tổ chức (đường job, account ECR, tên repo) là BẮT BUỘC khai — không có mặc định
# trong code — nên fixture khai đủ, giá trị giả.
CFG = {"JENKINS_BASE_URL": JENKINS, "JENKINS_USER": "test", "JENKINS_TOKEN": SECRET,
       "JENKINS_JOB_PATH": "job/SAMPLE/job/{env}/job/{group}/job/{target}",
       "JENKINS_JOB_PATH_BOUNDARIES": "job/SAMPLE/job/{env}/job/sample-boundaries/job/{service}",
       "JENKINS_JOB_PATH_WEB_EXPERIENCES": "job/SAMPLE/job/{env}/job/sample-experiences/job/{service}"}
AWS_CFG = {"AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": SECRET,
           "AWS_DEFAULT_REGION": "ap-southeast-1", "ECR_REGISTRY_ID": "000000000000",
           "ECR_REPOSITORY_TEMPLATE": "{env}/sample/{service}"}


def response(data, code=200, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(data).encode()
    result.headers.update(headers or {})
    return result


def image(tags, digest, day, pulled=None):
    detail = {"imageTags": tags, "imageDigest": digest,
              "imagePushedAt": datetime(2026, 9, day, 8, tzinfo=timezone.utc)}
    if pulled:
        detail["lastRecordedPullTime"] = datetime(2026, 9, pulled, 9, tzinfo=timezone.utc)
    return detail


class FakeEcr:
    """Thay boto3 ECR client: repo → danh sách imageDetails, ghi lại mọi truy vấn."""

    def __init__(self, repos, error=None):
        self.repos, self.error, self.queries = repos, error, []

    def get_paginator(self, name):
        assert name == "describe_images", name
        return self

    def paginate(self, **query):
        self.queries.append(query)
        if self.error:
            raise self.error
        if query["repositoryName"] not in self.repos:
            raise ClientErrorLike("RepositoryNotFoundException")
        return [{"imageDetails": self.repos[query["repositoryName"]]}]


class ClientErrorLike(Exception):
    def __init__(self, code):
        super().__init__(f"{code}: {SECRET}")
        self.response = {"Error": {"Code": code, "Message": SECRET}}
