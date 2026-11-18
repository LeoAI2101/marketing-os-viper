"""Sổ release theo môi trường: mỗi lượt chuẩn bị là một run có ID, là file vận hành để deploy.

.local/releases/<env>-ledger.json
  current  — {target: rc/commit mới nhất đã biết, run nào ghi} — nhìn một chỗ là biết
             toàn bộ boundaries + web đang ở tag nào.
  runs[]   — lượt cuối luôn là lượt NHÁP cho toàn bộ service (`draft: true`), `sync` tạo sau mỗi
             lần deploy thành công: `source.rc_tag` = RC mới nhất đã deploy thành công,
             `deploy.rc_tag` = RC kế tiếp (RC cao nhất từng thấy kể cả build lỗi + 1), `image_tag`
             trống. Sửa tay dòng nào cần; `prepare_deploy --source-env` điền commit vào chính lượt
             nháp. Lượt đã gửi giữ nguyên RC đã deploy. Dòng `pending` của lượt cũ khi target đã có
             ở lượt mới hơn → `superseded`, deploy-run bỏ qua.
  deployments[] — mọi build pipeline env này thành công (SUCCESS/UNSTABLE) đọc từ Jenkins,
             kể cả build bấm tay trên UI; `sync_target` thêm build mới, không xoá dòng cũ.
  runs[]   — lịch sử từng lượt, không sửa lượt cũ trừ khối `deploy` của chính lượt đó.
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ops_env import ROOT
from release_common import RC_PATTERN, ReleaseError, write_json
from release_ecr import next_rc, rc_key
from release_promotion import PROMOTION_ENVS, PROMOTION_SOURCE

VERSION = 2


def ledger_path(env: str) -> Path:
    return ROOT / ".local" / "releases" / f"{env.lower()}-ledger.json"


def empty(env: str) -> dict:
    return {"version": VERSION, "environment": env, "current": {}, "deployments": [], "runs": []}


def load(path: Path, env: str) -> dict:
    if not path.exists():
        return empty(env)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ReleaseError(f"{path.name} không phải JSON hợp lệ; sửa tay trước khi chạy tiếp.") from None
    if data.get("version") != VERSION or data.get("environment") != env:
        raise ReleaseError(f"{path.name} không phải sổ v{VERSION} của {env}.")
    data.setdefault("deployments", [])
    return data


@contextmanager
def editing(path: Path, env: str):
    """Khoá file trong lúc đọc-sửa-ghi để hai lượt chạy song song không đè nhau."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path, env)
        yield data
        data.pop("_next_rc", None)      # tạm của sync_target → ensure_draft, không ghi ra file
        write_json(path, data)


def new_run_id(data: dict, env: str, when: datetime | None = None) -> str:
    prefix = f"{env}-{(when or datetime.now()).strftime('%Y%m%d')}-"
    used = [int(r["id"].removeprefix(prefix)) for r in data["runs"]
            if r["id"].startswith(prefix) and r["id"].removeprefix(prefix).isdigit()]
    return f"{prefix}{max(used, default=0) + 1:02d}"


def find_run(data: dict, run_id: str | None) -> dict:
    if not data["runs"]:
        raise ReleaseError("Sổ chưa có lượt nào; chạy prepare_deploy.py trước.")
    if run_id is None:
        return data["runs"][-1]
    for run in data["runs"]:
        if run["id"] == run_id:
            return run
    raise ReleaseError(f"Không có lượt {run_id} trong sổ.")


OK_RESULTS = ("SUCCESS", "UNSTABLE")


def record_current(data: dict, target: str, info: dict, run_id: str | None, basis: str, at: str) -> None:
    data["current"][target] = {
        "rc_tag": info.get("rc_tag"), "commit_tag": info.get("commit_tag"),
        "release_tag": info.get("release_tag"),
        "build_number": info.get("build_number"), "deployed_at": info.get("deployed_at"),
        "run_id": run_id, "basis": basis, "updated_at": at}


def next_rc_after(builds: list[dict]) -> str:
    rcs = [b["rc_tag"] for b in builds if b.get("rc_tag")]
    return next_rc(max(rcs, key=rc_key) if rcs else None)


def matches(deploy: dict | None, build: dict) -> bool:
    if deploy and deploy.get("release_tag") and build.get("release_tag"):
        return deploy["release_tag"] == build["release_tag"]      # PROD: tag release là khoá khớp
    if deploy and RC_PATTERN.fullmatch(deploy.get("image_tag") or ""):
        return deploy.get("rc_tag") == build["rc_tag"] == deploy["image_tag"]
    return bool(deploy) and deploy.get("rc_tag") == build["rc_tag"] \
        and deploy.get("image_tag") == build["commit_tag"]


def settle_queued(rows: list, builds: list[dict]) -> None:
    """queued → success/failed theo build khớp RC + commit; dòng success thiếu số build thì điền."""
    for _, row in rows:
        deploy = row.get("deploy")
        if not deploy or deploy.get("status") not in ("queued", "success") or deploy.get("build_number"):
            continue
        done = [b for b in builds if b["result"] and matches(deploy, b)]
        if done:
            build = max(done, key=lambda b: b["build_number"])
            ok = build["result"] in OK_RESULTS
            deploy.update(status="success" if ok else "failed", result=build["result"],
                          build_number=build["build_number"], finished_at=build["deployed_at"])


def sync_target(data: dict, target: str, builds: list[dict], at: str) -> list[dict]:
    """Ghi build thành công mới của target vào sổ, chốt `queued`. Trả dòng deployments mới."""
    rows = [(run["id"], row) for run in data["runs"] for row in run["services"]
            if row["target"] == target]
    settle_queued(rows, builds)
    seen = {d["build_number"] for d in data["deployments"] if d["target"] == target}
    added = []
    for build in sorted(builds, key=lambda b: b["build_number"]):
        if build["result"] not in OK_RESULTS or build["build_number"] in seen:
            continue
        run_id = next((rid for rid, row in reversed(rows) if matches(row.get("deploy"), build)), None)
        entry = {"target": target, **{k: build[k] for k in
                 ("rc_tag", "commit_tag", "build_number", "result", "deployed_at")}, "run_id": run_id}
        if build.get("release_tag"):
            entry["release_tag"] = build["release_tag"]
        data["deployments"].append(entry)
        added.append(entry)
    ok = [b for b in builds if b["result"] in OK_RESULTS]
    if ok:
        last = max(ok, key=lambda b: b["build_number"])
        run_id = next((d["run_id"] for d in data["deployments"]
                       if d["target"] == target and d["build_number"] == last["build_number"]), None)
        record_current(data, target, last, run_id, "jenkins-last-success", at)
    data.setdefault("_next_rc", {})[target] = next_rc_after(builds)
    return added


def is_open_draft(run: dict) -> bool:
    return bool(run.get("draft")) and all(
        (r.get("deploy") or {}).get("status") in ("pending", None) for r in run["services"])


def supersede_older(data: dict) -> None:
    """Dòng `pending` của lượt cũ, khi target đã có ở lượt mới hơn → superseded."""
    newest = {}
    for i, run in enumerate(data["runs"]):
        for row in run["services"]:
            newest[row["target"]] = i
    for i, run in enumerate(data["runs"]):
        for row in run["services"]:
            deploy = row.get("deploy")
            if newest[row["target"]] > i and deploy and deploy.get("status") == "pending":
                deploy.update(status="superseded",
                              note=f"Thay bằng lượt {data['runs'][newest[row['target']]]['id']}")


def ensure_draft(data: dict, env: str, targets: dict, source_env: str | None, at: str) -> dict:
    """Lượt nháp cuối sổ cho toàn bộ target: source.rc_tag = RC đang chạy, deploy.rc_tag = kế tiếp.

    Có lượt nháp chưa gửi → cập nhật tại chỗ, giữ RC đã sửa tay nếu cao hơn RC kế tiếp tự tính.
    """
    upcoming = data.pop("_next_rc", {})
    run = data["runs"][-1] if data["runs"] and is_open_draft(data["runs"][-1]) else None
    if run is None:
        run = {"id": new_run_id(data, env), "created_at": at, "environment": env,
               "source_env": source_env, "reader": "jenkins", "draft": True, "services": []}
        data["runs"].append(run)
    rows = {r["target"]: r for r in run["services"]}
    for target, (group, service) in targets.items():
        running = (data["current"].get(target) or {}).get("rc_tag")
        auto = None if env in PROMOTION_ENVS else upcoming.get(target) or next_rc(running)
        row = rows.get(target)
        if row is not None and not row.get("deploy"):
            # Dòng lỗi của lần chuẩn bị trước (không có khối deploy): dựng lại như dòng mới.
            run["services"].remove(row)
            row = None
        if row is None:
            row = {"target": target, "group": group, "service": service,
                   "source": {"env": source_env, "commit_tag": None,
                              "rc_tag": None if env in PROMOTION_ENVS else running},
                   "deploy": {"rc_tag": auto, "image_tag": None, "skip": False,
                              "note": f"Chạy prepare_deploy --source-env {PROMOTION_SOURCE[env]}" if env in PROMOTION_ENVS
                                      else "Điền image_tag hoặc chạy prepare_deploy --source-env",
                              "status": "pending", "jenkins": None}}
            run["services"].append(row)
            continue
        if env in PROMOTION_ENVS:
            continue
        row["source"] = {**(row.get("source") or {}), "rc_tag": running}
        if rc_key(row["deploy"]["rc_tag"]) < rc_key(auto):      # RC tay thấp hơn/cũ → nâng
            row["deploy"]["rc_tag"] = auto
    run["updated_at"] = at
    supersede_older(data)
    return run
