#!/usr/bin/env python3
"""Đọc tag đang chạy của boundaries + web, ghi thành một lượt trong sổ release.

Công cụ ops ngoài luồng VIPER, cùng nhóm jenkins_build.py. Không gọi Argo, không đổi runtime:
  mặc định — job Jenkins của từng service, lastSuccessfulBuild env đó (lib/release_jenkins.py)
  --ecr    — boundaries đọc ECR bằng khoá .local/env/.env.aws (lib/release_ecr.py); web vẫn
             Jenkins vì web build ra S3/CloudFront, không có image ECR.

Mỗi lần chạy là một lượt có ID (<ENV>-<YYYYMMDD>-<NN>) trong .local/releases/<env>-ledger.json:
tag đang chạy, commit tag, RC kế tiếp đề xuất và — nếu có --source-env — commit image sẽ
promote. PT/preprod giữ RC nguồn UAT, không tăng RC; PROD lấy RC của PREPROD và mang tag release vX.Y.Z riêng (từ v1.0.1). `jenkins_build.py deploy-run
--env <ENV>` đọc lượt đó để gửi build tiếp.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from release_common import RC_PATTERN, RELEASE_PATTERN, ReleaseError, environments, now, select_targets, service_map
from release_ecr import EcrReader, ecr_config, next_rc, rc_key
from release_jenkins import JenkinsReader
from release_ledger import editing, ensure_draft, ledger_path, record_current, sync_target
from release_promotion import PROMOTION_ENVS, promotion_plan, select_release, source_error


def deploy_plan(current: dict | None, source: dict | None, source_env: str | None,
                max_rc: str | None = None, forced_rc: str | None = None) -> dict:
    seen = [rc for rc in ((current or {}).get("rc_tag"), max_rc) if rc]
    top = max(seen, key=rc_key) if seen else None
    plan = {"rc_tag": next_rc(top), "image_tag": None,
            "skip": False, "note": None, "status": "pending", "jenkins": None}
    if forced_rc and top and rc_key(forced_rc) <= rc_key(top):
        plan["note"] = f"--rc-tag {forced_rc} không vượt {top} đã dùng; giữ {plan['rc_tag']}."
    elif forced_rc:
        plan["rc_tag"] = forced_rc
    if not source_env:
        plan["note"] = "Chưa có image_tag: chạy lại với --source-env hoặc điền tay trước khi deploy."
    elif not source or not source["commit_tag"]:
        plan["note"] = f"{source_env} không có image mang commit tag."
    else:
        plan["image_tag"] = source["commit_tag"]
        same = (source.get("digest") == current["digest"]) if current and current.get("digest") \
            else current and source["commit_tag"] == current["commit_tag"]
        if same:
            plan.update(skip=True, note=f"{source_env} chưa có image mới so với bản đang chạy.")
    return plan


def readers_for(groups: set[str], env: str, source_env: str | None, use_ecr: bool) -> dict:
    """{nhóm: (reader env đích, reader env nguồn | None)}; chỉ dựng cái cần dùng."""
    def jenkins(e):
        return JenkinsReader(e) if e else None

    readers = {g: (jenkins(env), jenkins(source_env)) for g in groups}
    if use_ecr and "boundaries" in groups:
        readers["boundaries"] = (EcrReader(ecr_config(env), env),
                                 source_env and EcrReader(ecr_config(source_env), source_env))
    return readers


def prepare_row(target: str, group: str, service: str, readers: tuple,
                forced_rc: str | None = None, forced_release: str | None = None) -> tuple[dict, list | None]:
    """(dòng của lượt, lịch sử build Jenkins env đích | None nếu đọc ECR/lỗi)."""
    reader, source_reader = readers
    builds = None
    row = {"target": target, "group": group, "service": service,
           "repository": None, "basis": None, "current": None, "latest_pushed": None,
           "max_rc_seen": None, "source": None, "deploy": None}
    try:
        snap = reader.snapshot(target, group, service)
        row.update(repository=snap["repository"], basis=snap["basis"],
                   current=snap["running"], latest_pushed=snap["latest_pushed"])
        if source_reader:
            src = source_reader.snapshot(target, group, service)
            latest = select_release(src, forced_rc) if reader.env in PROMOTION_ENVS else src["latest_pushed"]
            keep = ("commit_tag", "rc_tag", "digest", "pushed_at", "build_number", "deployed_at")
            row["source"] = latest and {"env": source_reader.env, "repository": src["repository"],
                                        **{k: latest[k] for k in keep if k in latest}}
            if row["source"] and reader.env not in PROMOTION_ENVS:
                row["source"]["rc_tag"] = (row["current"] or {}).get("rc_tag")
        builds = snap.get("builds")
        row["max_rc_seen"] = snap["max_rc"]
        row["deploy"] = promotion_plan(row["current"], row["source"], group, reader.env, forced_release) \
            if reader.env in PROMOTION_ENVS else deploy_plan(
                row["current"], row["source"], source_reader and source_reader.env, snap["max_rc"], forced_rc)
    except ReleaseError as exc:
        row["error"] = str(exc)
    return row, builds


def print_row(row: dict) -> None:
    current = row["current"] or {}
    deploy = row["deploy"] or {}
    if row.get("error"):
        print(f"  {row['target']:<26} Lỗi: {row['error']}")
        return
    rc = current.get("rc_tag") or "chưa có RC"
    commit = current.get("commit_tag") or "-"
    print(f"  {row['target']:<26} {rc:<16} {commit:<12}", end="")
    if deploy.get("skip"):
        print(" bỏ qua (không đổi)")
    else:
        release = f" · release {deploy['release_tag']}" if deploy.get("release_tag") else ""
        print(f" → {deploy['rc_tag']:<16} {deploy['image_tag'] or '(chưa có image_tag)'}{release}")


def run(args) -> int:
    env = args.env.upper()
    source_env = args.source_env.upper() if args.source_env else None
    if env not in environments() or (source_env and source_env not in environments()):
        raise ReleaseError("Môi trường không có trong OPS_ENVS.")
    if source_env == env:
        raise ReleaseError("--source-env phải khác --env.")
    if error := source_error(env, source_env):
        raise ReleaseError(error)
    targets = select_targets(args.services)
    path = args.ledger or ledger_path(env)
    readers = readers_for({g for g, _ in targets.values()}, env, source_env, args.ecr)
    if args.rc_tag and not RC_PATTERN.fullmatch(args.rc_tag):
        raise ReleaseError("--rc-tag phải dạng vX.Y.Z-rc-N (vd v1.0.1-rc-1).")
    release = getattr(args, "release_tag", None)
    if release and (env != "PROD" or not RELEASE_PATTERN.fullmatch(release)):
        raise ReleaseError("--release-tag chỉ dùng cho PROD và phải dạng vX.Y.Z (vd v1.0.1).")
    results = [prepare_row(t, g, s, readers[g], args.rc_tag, release) for t, (g, s) in targets.items()]
    rows = [row for row, _ in results]
    created = now()
    with editing(path, env) as ledger:
        for row, builds in results:
            if builds is not None:
                sync_target(ledger, row["target"], builds, created)
            elif row["current"]:
                record_current(ledger, row["target"], row["current"], None, row["basis"], created)
        # Điền vào lượt nháp (toàn bộ service, RC kế tiếp ghi sẵn). RC trong nháp thắng RC tự tính
        # — có thể đã sửa tay; chỉ --rc-tag mới đè.
        draft = ensure_draft(ledger, env, service_map(), source_env, created)
        run_id = draft["id"]
        slots = {r["target"]: i for i, r in enumerate(draft["services"])}
        for row in rows:
            old = draft["services"][slots[row["target"]]]
            if row.get("deploy") and not args.rc_tag and env not in PROMOTION_ENVS:
                row["deploy"]["rc_tag"] = max(row["deploy"]["rc_tag"], old["deploy"]["rc_tag"], key=rc_key)
                if row["source"]:
                    row["source"]["rc_tag"] = (old.get("source") or {}).get("rc_tag")
            draft["services"][slots[row["target"]]] = row
        draft.update(source_env=source_env, reader="ecr" if args.ecr else "jenkins", prepared_at=created)
    print(f"Lượt {run_id} · {env}" + (f" ← nguồn {source_env}" if source_env else ""))
    print(f"  {'TARGET':<26} {'RC ĐANG CHẠY':<16} {'COMMIT':<12}   DEPLOY TIẾP")
    for row in rows:
        print_row(row)
    print(f"\nĐã ghi {path} (lượt {run_id}).")
    extra = f" --ledger {path}" if args.ledger else ""
    print(f"Duyệt/sửa khối deploy rồi: python3 scripts/jenkins_build.py deploy-run --env {env} --run {run_id}{extra}")
    return 1 if any(r.get("error") for r in rows) else 0


def parser() -> argparse.ArgumentParser:
    envs = environments()
    parser = argparse.ArgumentParser(description="Đọc tag đang chạy trên ECR và ghi một lượt vào sổ release")
    parser.add_argument("services", nargs="*",
                        help="Target hoặc service; bỏ trống = toàn bộ boundaries + web")
    parser.add_argument("--env", type=str.upper, choices=envs, default="UAT",
                        help="Môi trường đích theo OPS_ENVS; mặc định UAT")
    parser.add_argument("--source-env", type=str.upper, choices=envs,
                        help="Môi trường nguồn image (vd SIT) để lấy commit sẽ promote")
    parser.add_argument("--rc-tag", help="UAT: ép RC mới; PT/preprod/prod: chọn đúng RC nguồn (UAT/UAT/PREPROD), không tăng RC")
    parser.add_argument("--release-tag", help="PROD: ép tag release vX.Y.Z (mặc định v1.0.1 cho bản đầu, sau đó tăng patch)")
    parser.add_argument("--ecr", action="store_true",
                        help="Boundaries đọc ECR thay Jenkins (cần quyền ecr:DescribeImages)")
    parser.add_argument("--ledger", type=Path, help="Mặc định .local/releases/<env>-ledger.json")
    return parser


def main() -> int:
    try:
        return run(parser().parse_args())
    except (ReleaseError, OSError) as exc:
        print(f"Lỗi: {exc if isinstance(exc, ReleaseError) else 'Không đọc/ghi được file.'}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Đã dừng bước đọc dữ liệu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
