#!/usr/bin/env python3
"""jenkins_build.py — trigger/kiểm tra build Jenkins cho các target của dự án.

VÌ SAO CÓ FILE NÀY
    Tool ops đứng NGOÀI luồng VIPER (không phải một trong 6 lệnh hợp đồng, không ghi
    context/DECISIONS.md hay STATE.md). Chỉ để đỡ phải vào UI Jenkins bấm từng job.

CẤU TRÚC JOB — khai ở .local/env/.env.jenkins, KHÔNG đóng cứng trong code
    JENKINS_JOB_PATH nhận chỗ giữ: {env} (viết HOA) · {group} · {target} · {service}
    ({service} là tên repo GitLab ở deployment/gitlab-repo-map.tsv, BFF có tiền tố d-)
        {JENKINS_BASE_URL}/{JENKINS_JOB_PATH}/
    {group} là nhóm srcroot của target (boundaries · web-experiences ·
    mobile-experiences) — dò từ đĩa, khớp bảng target của sync_srcroot_to_gitlab.sh.
    UAT: RC_TAG mới + IMAGE_TAG commit. PT: IMAGE_TAG là RC nguồn UAT.
    PREPROD: RC_TAG là RC nguồn UAT. GIT_TAG giữ riêng cho job cũ.
    --source-env truyền SOURCE_ENV; UAT trở lên bắt buộc chỉ rõ nguồn, không có mặc định.

BÍ MẬT
    Đọc JENKINS_BASE_URL/JENKINS_USER/JENKINS_TOKEN từ .local/env/.env.jenkins
    (mẫu: .local/templates/.env.jenkins.example). File đó khớp mẫu `.env.*` nên .gitignore
    CHẶN SẴN (luật #6 — không secret trong code).
    JENKINS_USER là USER ID trong Jenkins (xem ở trang cá nhân), KHÔNG PHẢI email —
    email bị 401 dù token đúng. JENKINS_TOKEN là API Token (Configure → API Token).

DÙNG
    scripts/jenkins_build.py targets
    scripts/jenkins_build.py history <target> --env SIT [--limit 10]
    scripts/jenkins_build.py build <target> --env SIT
    scripts/jenkins_build.py build d-identity-trust --env UAT --source-env sit --rc-tag v1.0.1-rc-1 --image-tag 68c02ec8 --wait
    scripts/jenkins_build.py build <target> --env PT --source-env uat --image-tag v1.0.1-rc-1 --wait
    scripts/jenkins_build.py build <target> --env PREPROD --source-env uat --rc-tag v1.0.1-rc-1 --wait
    scripts/jenkins_build.py status <target> --env SIT [--build 42]
    scripts/jenkins_build.py deploy-run --env UAT [--run UAT-20260924-01] [--only <target>] [--dry-run] [--wait]
    scripts/jenkins_build.py sync --env UAT [<target> ...]

    deploy-run đọc một lượt trong .local/releases/<env>-ledger.json (do prepare_deploy.py
    ghi), gửi build cho từng service theo khối `deploy` và ghi kết quả ngược vào sổ.
    sync đọc lịch sử build Jenkins env đó, thêm mọi build thành công (SUCCESS/UNSTABLE) chưa có
    vào `deployments`, cập nhật `current` và chốt các dòng `queued` — kể cả build bấm tay trên UI.

    Env khai ở JENKINS_ENVS_REQUIRE_RC_TAG bắt buộc --rc-tag. Thiếu thì lệnh build tự in
    bảng RC_TAG dùng ở các build gần đây (đọc thật từ Jenkins) rồi dừng, đỡ phải vào UI.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ops_env import (ROOT, discover_targets, env_key, env_list, jenkins_file, load_env_file, require_target,
                     with_own_envs)  # noqa: E402
from release_common import COMMIT_PATTERN, RC_PATTERN, ReleaseError, now, select_targets, service_map  # noqa: E402
from release_ledger import OK_RESULTS, editing, ensure_draft, find_run, ledger_path, load, sync_target  # noqa: E402
from release_promotion import (PROMOTION_ENVS, build_promotion_params, promotion_blocker,
                               promotion_params, source_error)  # noqa: E402

import requests  # noqa: E402

ENV_FILE = ".local/env/.env.jenkins"

TARGET_GROUP = discover_targets()
SERVICE_OF = {target: service for target, (_, service) in service_map().items()}

POLL_INTERVAL_S = 5
QUEUE_TIMEOUT_S = 10 * 60
BUILD_TIMEOUT_S = 45 * 60

# Không có đường job mặc định: cây job là của tổ chức — khai JENKINS_JOB_PATH (hoặc
# JENKINS_JOB_PATH_<NHÓM>) ở .local/env/.env.jenkins. Thiếu thì dừng, không đoán.


def load_cfg(env: str | None = None) -> dict:
    """Cấu hình Jenkins của một env: PROD có `.env.jenkins-prod` riêng (Jenkins khác nonprod)."""
    return load_env_file(
        jenkins_file(env),
        required=("JENKINS_BASE_URL", "JENKINS_USER", "JENKINS_TOKEN"),
    )


def job_url(cfg: dict, target: str, env: str, service: str | None = None) -> str:
    group = require_target(TARGET_GROUP, target)
    # JENKINS_JOB_PATH_<NHÓM> (vd _BOUNDARIES) thắng mẫu chung: cây job Jenkins hay đặt tên
    # nhóm khác srcroot và job boundary mang tên service.
    template = cfg.get(f"JENKINS_JOB_PATH_{env_key(group)}") or cfg.get("JENKINS_JOB_PATH")
    if not template:
        sys.exit(f"✗ {ENV_FILE}: chưa khai JENKINS_JOB_PATH (hoặc JENKINS_JOB_PATH_{env_key(group)}) — "
                 "đường job Jenkins của tổ chức, xem .local/templates/.env.jenkins.example")
    try:
        path = template.format(env=env.upper(), group=group, target=target,
                          service=service or SERVICE_OF.get(target, target))
    except KeyError as exc:
        sys.exit(f"✗ {ENV_FILE}: JENKINS_JOB_PATH có chỗ giữ lạ {exc}"
                 f" — chỉ nhận {{env}} {{group}} {{target}} {{service}}")
    return f"{cfg['JENKINS_BASE_URL'].rstrip('/')}/{path.strip('/')}/"


def auth(cfg: dict) -> tuple:
    return (cfg["JENKINS_USER"], cfg["JENKINS_TOKEN"])


def api_get(cfg: dict, url: str, **kwargs) -> requests.Response:
    resp = requests.get(url, auth=auth(cfg), timeout=30, **kwargs)
    if resp.status_code == 401:
        sys.exit(f"✗ 401 Unauthorized ở {url} — kiểm lại JENKINS_USER/JENKINS_TOKEN trong {ENV_FILE}")
    if resp.status_code == 404:
        # In nguyên URL vừa gọi: 404 ở đây gần như luôn là JENKINS_JOB_PATH chưa khớp
        # cây job thật, và cách sửa nhanh nhất là nhìn URL sai cạnh URL đúng trên UI.
        sys.exit(
            f"✗ 404 không thấy {url}\n"
            f"  Kiểm JENKINS_JOB_PATH trong {ENV_FILE} (hoặc tên target/env)."
        )
    resp.raise_for_status()
    return resp


def get_retry(url: str, attempts: int = 3, **kwargs) -> requests.Response:
    """GET chỉ đọc nên thử lại khi mạng tới Jenkins chập chờn. POST gửi build KHÔNG qua đây."""
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(url, timeout=30, **kwargs)
        except requests.ConnectionError:
            if attempt == attempts:
                raise
            print(f"  … mạng tới Jenkins lỗi, thử lại ({attempt}/{attempts - 1})")
            time.sleep(POLL_INTERVAL_S)


def get_crumb(cfg: dict) -> dict:
    resp = get_retry(f"{cfg['JENKINS_BASE_URL'].rstrip('/')}/crumbIssuer/api/json", auth=auth(cfg))
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    data = resp.json()
    return {data["crumbRequestField"]: data["crumb"]}


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def fetch_history(cfg: dict, target: str, env: str, limit: int) -> list:
    url = job_url(cfg, target, env)
    tree = f"builds[number,result,timestamp,building,actions[parameters[name,value]]]{{0,{limit}}}"
    resp = api_get(cfg, f"{url}api/json", params={"tree": tree})
    rows = []
    for b in resp.json().get("builds", []):
        params = {}
        for action in b.get("actions", []):
            for p in action.get("parameters", []) or []:
                params[p["name"]] = p.get("value")
        rows.append(
            {
                "number": b["number"],
                "result": "ĐANG CHẠY" if b.get("building") else (b.get("result") or "?"),
                "timestamp": fmt_ts(b["timestamp"]),
                "git_tag": params.get("GIT_TAG") or "-",
                "image_tag": params.get("IMAGE_TAG") or "-",
                "rc_tag": params.get("RC_TAG") or "-",
            }
        )
    return rows


def print_history(rows: list, target: str, env: str) -> None:
    print(f"Lịch sử build {target} [{env}]:")
    if not rows:
        print("  (chưa có build nào)")
        return
    print(f"  {'BUILD':>6}  {'THỜI GIAN':<19}  {'KẾT QUẢ':<10}  {'GIT_TAG':<20}  {'IMAGE_TAG':<12}  RC_TAG")
    for r in rows:
        print(f"  {r['number']:>6}  {r['timestamp']:<19}  {r['result']:<10}  {r['git_tag']:<20}  {r['image_tag']:<12}  {r['rc_tag']}")


def cmd_targets(cfg: dict, args) -> None:
    """In cây job sẽ gọi — kiểm JENKINS_JOB_PATH mà không cần chạm mạng."""
    print(f"{len(TARGET_GROUP)} target (dò từ srcroot/), {len(ENVS)} env: {', '.join(ENVS)}\n")
    for target, group in TARGET_GROUP.items():
        print(f"  {target:<24} {group:<20} {job_url(cfg, target, ENVS[0])}")


def cmd_history(cfg: dict, args) -> None:
    env = args.env.upper()
    rows = fetch_history(cfg, args.target, env, args.limit)
    print_history(rows, args.target, env)


def cmd_build(cfg: dict, args) -> None:
    env = args.env.upper()
    rc_tag = args.rc_tag

    if env in ENVS_REQUIRE_SOURCE_ENV and not args.source_env:
        sys.exit(f"✗ env {env} bắt buộc --source-env <nguồn image> (ví dụ: sit, uat)")
    if error := source_error(env, args.source_env):
        sys.exit(f"✗ {error}")

    if env in ENVS_CAN_TAO_RC and env not in PROMOTION_ENVS and not rc_tag:
        print(f"✗ env {env} bắt buộc --rc-tag. Các RC_TAG gần đây:\n")
        rows = fetch_history(cfg, args.target, env, 10)
        print_history(rows, args.target, env)
        sys.exit(
            f"\nChạy lại với --rc-tag <tag>, giữ --source-env đã chọn và các tham số khác."
        )

    prod_gate(env)
    url = job_url(cfg, args.target, env)
    params = {}
    if args.source_env:
        params["SOURCE_ENV"] = args.source_env
    if rc_tag:
        params["RC_TAG"] = rc_tag
    if args.git_tag:
        params["GIT_TAG"] = args.git_tag
    if args.image_tag:
        params["IMAGE_TAG"] = args.image_tag
    if env in PROMOTION_ENVS:
        try:
            params = build_promotion_params(env, TARGET_GROUP[args.target], args)
        except ReleaseError as exc:
            sys.exit(f"✗ {exc}")

    queue_url = trigger_build(cfg, url, params)
    if not args.wait:
        return

    build_url = wait_for_build_start(cfg, queue_url)
    print(f"✓ build bắt đầu: {build_url}")
    result = wait_for_build_finish(cfg, build_url)
    print(f"{'✓' if result == 'SUCCESS' else '✗'} kết quả: {result}")
    if result != "SUCCESS":
        sys.exit(1)


def trigger_build(cfg: dict, url: str, params: dict) -> str:
    headers = get_crumb(cfg)
    resp = requests.post(
        f"{url}buildWithParameters",
        auth=auth(cfg),
        headers=headers,
        params=params,
        timeout=30,
    )
    if resp.status_code == 401:
        sys.exit(f"✗ 401 Unauthorized — kiểm lại JENKINS_USER/JENKINS_TOKEN trong {ENV_FILE}")
    if resp.status_code not in (200, 201):
        sys.exit(f"✗ trigger build thất bại: HTTP {resp.status_code}")

    queue_url = resp.headers.get("Location")
    if not queue_url:
        sys.exit(f"✗ Jenkins không trả queue URL (HTTP {resp.status_code}) — không rõ build có chạy không")
    print(f"✓ đã đưa vào hàng đợi: {queue_url}")
    return queue_url


def wait_for_build_start(cfg: dict, queue_url: str) -> str:
    deadline = time.time() + QUEUE_TIMEOUT_S
    while time.time() < deadline:
        resp = get_retry(f"{queue_url}api/json", auth=auth(cfg))
        resp.raise_for_status()
        data = resp.json()
        if data.get("cancelled"):
            sys.exit("✗ build bị huỷ khỏi hàng đợi")
        executable = data.get("executable")
        if executable:
            return executable["url"]
        why = data.get("why", "đang chờ")
        print(f"  … còn trong hàng đợi: {why}")
        time.sleep(POLL_INTERVAL_S)
    sys.exit(f"✗ chờ quá {QUEUE_TIMEOUT_S}s mà build chưa rời hàng đợi")


def wait_for_build_finish(cfg: dict, build_url: str, auto_approve: bool = False) -> str:
    deadline = time.time() + BUILD_TIMEOUT_S
    seen = set()
    while time.time() < deadline:
        resp = get_retry(f"{build_url}api/json", auth=auth(cfg))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("building"):
            return data.get("result") or "UNKNOWN"
        if auto_approve:
            from jenkins_approval import check_inputs
            from deploy_manifest import read_manifest
            manifest = read_manifest(ROOT)
            check_inputs(cfg, build_url, seen, requests, get_crumb, manifest.get('approval_input_ids', ()),
                         manifest.get('approve_spacing_seconds', 0))
        else:
            from jenkins_approval import notify_pending
            notify_pending(cfg, build_url, seen, requests)
        print("  … đang chạy")
        time.sleep(POLL_INTERVAL_S)
    sys.exit(f"✗ chờ quá {BUILD_TIMEOUT_S}s mà build chưa xong")


def cmd_params(cfg: dict, args) -> None:
    """In định nghĩa tham số form của job (chỉ GET) để đối chiếu tên tham số, nhất là job PROD."""
    env = args.env.upper()
    url = job_url(cfg, args.target, env)
    tree = "property[parameterDefinitions[name,type,description,defaultParameterValue[value],choices]]"
    data = api_get(cfg, f"{url}api/json", params={"tree": tree}).json()
    defs = [d for prop in data.get("property", []) for d in prop.get("parameterDefinitions", [])]
    print(f"Tham số form job {env} {args.target}: {url}")
    for d in defs:
        default = (d.get("defaultParameterValue") or {}).get("value")
        extra = f" · lựa chọn: {', '.join(map(str, d['choices']))}" if d.get("choices") else ""
        print(f"  {d['name']:<20} {d.get('type', '?'):<28} mặc định={default!r}{extra}")
    if not defs:
        print("  (job không có tham số)")


def cmd_status(cfg: dict, args) -> None:
    env = args.env.upper()
    url = job_url(cfg, args.target, env)
    build_ref = str(args.build) if args.build else "lastBuild"
    resp = api_get(cfg, f"{url}{build_ref}/api/json")
    data = resp.json()
    result = "ĐANG CHẠY" if data.get("building") else (data.get("result") or "?")
    print(f"build #{data['number']} [{env}] {args.target}")
    print(f"  kết quả : {result}")
    print(f"  thời gian: {fmt_ts(data['timestamp'])}")
    print(f"  url     : {data['url']}")


DONE_STATUSES = ("queued", "success", "unknown")


def deploy_params(group: str, deploy: dict, source_env: str | None, env: str = "UAT") -> dict:
    """Form job khác nhau theo nhóm: boundary promote image ECR, web build lại từ Git ra S3."""
    if (ROOT / 'deployment/deploy-manifest.json').exists():
        from deploy_manifest import read_manifest, params
        return params(read_manifest(ROOT), env, group, deploy, source_env)
    if env in PROMOTION_ENVS:
        return promotion_params(env, group, deploy)
    if group == "web-experiences":
        return {"RC_TAG": deploy["rc_tag"], "SHORT_COMMIT": deploy["image_tag"]}
    params = {"RC_TAG": deploy["rc_tag"], "IMAGE_TAG": deploy["image_tag"]}
    return {"SOURCE_ENV": source_env.lower(), **params} if source_env else params


def prod_gate(env: str) -> None:
    """Gửi build thật lên PROD chỉ khi tham số form job đã được người xác minh trên Jenkins prod."""
    from deploy_manifest import read_manifest
    if env == "PROD" and not read_manifest(ROOT).get("environments", {}).get("PROD", {}).get("params_verified"):
        sys.exit("✗ tham số form job Jenkins PROD chưa xác minh — chạy `jenkins_build.py params <target> --env PROD`,\n"
                 "  đối chiếu environments.PROD.parameters trong deployment/deploy-manifest.json rồi đặt params_verified=true.")


def deploy_blocker(row: dict, env: str = "UAT") -> str | None:
    """Lý do KHÔNG gửi build cho một dòng của lượt; None = gửi được."""
    deploy = row.get("deploy")
    if row.get("error") or not deploy:
        return f"lượt chuẩn bị lỗi: {row.get('error') or 'thiếu khối deploy'}"
    if deploy.get("skip"):
        return f"bỏ qua: {deploy.get('note') or 'skip=true'}"
    if deploy.get("status") == "superseded":
        return f"bỏ qua: {deploy.get('note') or 'đã có lượt mới hơn'}"
    if deploy.get("status") in DONE_STATUSES:
        return f"đã gửi trước đó ({deploy['status']})"
    if env == 'SIT':
        return None if COMMIT_PATTERN.fullmatch(deploy.get('image_tag') or '') else 'SIT thiếu commit đã chốt'
    if not RC_PATTERN.fullmatch(deploy.get("rc_tag") or ""):
        return "rc_tag trống hoặc sai dạng vX.Y.Z-rc-N"
    if env in PROMOTION_ENVS:
        return promotion_blocker(row, env)
    if not COMMIT_PATTERN.fullmatch(deploy.get("image_tag") or ""):
        return "image_tag trống hoặc không phải mã commit"
    return None


def save_deploy(path: Path, env: str, run_id: str, target: str, **fields) -> None:
    with editing(path, env) as ledger:
        row = next(r for r in find_run(ledger, run_id)["services"] if r["target"] == target)
        row["deploy"].update(fields)


def deploy_one(cfg: dict, env: str, run: dict, row: dict, args) -> None:
    path, deploy = args.ledger or ledger_path(env), row["deploy"]
    params = deploy_params(row["group"], deploy, run.get("source_env"), env)
    url = job_url(cfg, row["target"], env, row["service"])
    print(f"\n{row['target']} → {deploy['rc_tag']} · {deploy['image_tag']}")
    try:
        try:
            queue_url = trigger_build(cfg, url, params)
        except requests.RequestException as exc:
            # Chưa chắc Jenkins nhận hay chưa: không đổi status, không tự gửi lại.
            sys.exit(f"✗ không gọi được Jenkins ({type(exc).__name__}). Dòng giữ `pending` —"
                     f" kiểm job trên UI rồi mới chạy lại deploy-run.")
        save_deploy(path, env, run["id"], row["target"], status="queued",
                    jenkins={"job_url": url, "queue_url": queue_url, "triggered_at": now()})
        if not args.wait:
            return
        build_url = wait_for_build_start(cfg, queue_url)
        result = wait_for_build_finish(cfg, build_url)
    except requests.RequestException as exc:
        save_deploy(path, env, run["id"], row["target"], status="queued")
        sys.exit(f"✗ mất kết nối khi chờ build ({type(exc).__name__}); build vẫn có thể đang chạy —"
                 f" chạy `sync --env {env}` sau để chốt kết quả.")
    ok = result in OK_RESULTS   # vàng (UNSTABLE) vẫn đã lên ArgoCD
    save_deploy(path, env, run["id"], row["target"], status="success" if ok else "failed",
                result=result, build_url=build_url, finished_at=now())
    print(f"{'✓' if ok else '✗'} kết quả: {result}")
    if not ok:
        sys.exit(1)


def cmd_deploy_run(cfg: dict, args) -> None:
    env = args.env.upper()
    try:
        run = find_run(load(args.ledger or ledger_path(env), env), args.run)
    except ReleaseError as exc:
        sys.exit(f"✗ {exc}")
    if env in ENVS_REQUIRE_SOURCE_ENV and not run.get("source_env"):
        sys.exit(f"✗ lượt {run['id']} không có source_env mà env {env} bắt buộc nguồn image —"
                 f" chạy lại prepare_deploy.py --env {env} --source-env <nguồn>")
    if error := source_error(env, run.get("source_env")):
        sys.exit(f"✗ {error}")
    rows = run["services"]
    if args.only:
        known = {r["target"] for r in rows} | {r["service"] for r in rows}
        if unknown := [n for n in args.only if n not in known]:
            sys.exit(f"✗ lượt {run['id']} không có: {', '.join(unknown)}")
        rows = [r for r in rows if r["target"] in args.only or r["service"] in args.only]

    todo = []
    print(f"Lượt {run['id']} · {env}" + (f" ← {run['source_env']}" if run.get("source_env") else ""))
    for row in rows:
        why = deploy_blocker(row, env)
        state = why or "GỬI  " + " ".join(f"{k}={v}" for k, v in deploy_params(
            row["group"], row["deploy"], run.get("source_env"), env).items())
        print(f"  {row['target']:<26} {state}")
        if not why:
            todo.append(row)
    if not todo:
        print("\nKhông có service nào cần gửi.")
        return
    if args.dry_run:
        print(f"\n--dry-run: không gửi {len(todo)} build.")
        return
    prod_gate(env)
    if getattr(args, 'parallel', False):
        from jenkins_parallel import run_parallel
        run_parallel(sys.modules[__name__], cfg, env, run, todo, args)
        return
    try:
        for row in todo:
            deploy_one(cfg, env, run, row, args)
    finally:
        if args.wait:   # build đã xong → ghi luôn vào deployments/current, không chờ sync tay
            sync_ledger(env, [r["target"] for r in todo], args.ledger)


def sync_ledger(env: str, names: list[str], path: Path | None = None) -> int:
    """Đọc lịch sử build Jenkins env đích, ghi build thành công mới vào sổ. Trả mã thoát."""
    from release_jenkins import JenkinsReader   # import muộn: release_jenkins import ngược file này
    path = path or ledger_path(env)
    reader, failed, snaps = JenkinsReader(env), 0, {}
    for target, (group, service) in select_targets(names).items():
        try:
            snaps[target] = reader.snapshot(target, group, service)["builds"]
        except ReleaseError as exc:
            failed += 1
            print(f"  {target:<26} ✗ {exc}")
    with editing(path, env) as ledger:
        for target, builds in snaps.items():
            for d in sync_target(ledger, target, builds, now()):
                print(f"  {target:<26} + #{d['build_number']} {d['rc_tag'] or '-'} {d['commit_tag'] or '-'}"
                      f" ({d['result']}, {d['deployed_at']})" + (f" ← {d['run_id']}" if d["run_id"] else ""))
        source_env = next((r["source_env"] for r in reversed(ledger["runs"]) if r.get("source_env")), None)
        draft = ensure_draft(ledger, env, service_map(), source_env, now())
    print(f"Đã đồng bộ {len(snaps)} service vào {path}. Lượt nháp kế tiếp: {draft['id']}.")
    return 1 if failed else 0


def cmd_sync(cfg: dict, args) -> None:
    try:
        code = sync_ledger(args.env.upper(), args.services, args.ledger)
    except ReleaseError as exc:
        sys.exit(f"✗ {exc}")
    sys.exit(code)


# Danh sách env đọc từ .local/env/.env.jenkins — argparse cần nó làm `choices` để gõ sai
# env thì chết ngay ở dòng lệnh. Chưa có file thì lấy mặc định, `--help` vẫn đọc được.
def _cfg_or_empty() -> dict:
    return load_env_file(".env.jenkins") if (ROOT / ENV_FILE).exists() else {}


_BOOT = _cfg_or_empty()
ENVS = with_own_envs(env_list(_BOOT, "OPS_ENVS", "SIT,UAT,PT,PREPROD"))
ENVS_CAN_TAO_RC = set(env_list(_BOOT, "JENKINS_ENVS_REQUIRE_RC_TAG", "UAT,PREPROD"))
ENVS_REQUIRE_SOURCE_ENV = set(env_list(_BOOT, "JENKINS_ENVS_REQUIRE_SOURCE_ENV", "UAT,PT,PREPROD")) | {"PROD"}


def source_env_value(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
        raise argparse.ArgumentTypeError("Môi trường nguồn không hợp lệ.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger/kiểm tra build Jenkins cho các target của dự án")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_t = sub.add_parser("targets", help="In target + cây job sẽ gọi (không chạm mạng)")
    p_t.set_defaults(func=cmd_targets)

    p_hist = sub.add_parser("history", help="Xem lịch sử build + RC_TAG gần đây")
    p_hist.add_argument("target", choices=sorted(TARGET_GROUP))
    p_hist.add_argument("--env", required=True, choices=ENVS)
    p_hist.add_argument("--limit", type=int, default=10)
    p_hist.set_defaults(func=cmd_history)

    p_build = sub.add_parser("build", help="Trigger build")
    p_build.add_argument("target", choices=sorted(TARGET_GROUP))
    p_build.add_argument("--env", required=True, choices=ENVS)
    p_build.add_argument("--source-env", type=source_env_value, default=None,
                         help="Nguồn image → SOURCE_ENV; bắt buộc UAT trở lên, không có mặc định")
    p_build.add_argument("--rc-tag", default=None,
                         help=f"Bắt buộc với: {', '.join(sorted(ENVS_CAN_TAO_RC)) or '(không env nào)'}")
    p_build.add_argument("--git-tag", default=None)
    p_build.add_argument("--release-tag", default=None, help="PROD: tag release vX.Y.Z (RELEASE_TAG)")
    p_build.add_argument("--image-tag", default=None, help="UAT: commit; PT: RC nguồn UAT")
    p_build.add_argument("--short-commit", default=None, help="Commit bản web UAT khi promote PT/preprod")
    p_build.add_argument("--wait", action="store_true", help="Chờ tới khi build xong")
    p_build.set_defaults(func=cmd_build)

    p_params = sub.add_parser("params", help="In tham số form của job (GET) — xác minh job PROD")
    p_params.add_argument("target", choices=sorted(TARGET_GROUP))
    p_params.add_argument("--env", required=True, choices=ENVS)
    p_params.set_defaults(func=cmd_params)

    p_status = sub.add_parser("status", help="Kiểm trạng thái build")
    p_status.add_argument("target", choices=sorted(TARGET_GROUP))
    p_status.add_argument("--env", required=True, choices=ENVS)
    p_status.add_argument("--build", type=int, default=None, help="Mặc định: build gần nhất")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("deploy-run", help="Gửi build theo một lượt trong sổ release (prepare_deploy.py)")
    p_run.add_argument("--env", required=True, choices=ENVS)
    p_run.add_argument("--run", default=None, help="ID lượt; mặc định lượt mới nhất")
    p_run.add_argument("--only", nargs="+", default=None, help="Chỉ gửi các target/service này")
    p_run.add_argument("--dry-run", action="store_true", help="In danh sách sẽ gửi, không gọi Jenkins")
    p_run.add_argument("--wait", action="store_true", help="Chờ từng build xong rồi mới gửi cái kế")
    p_run.add_argument("--parallel", action="store_true", default=True, help="Gửi mọi job đồng thời rồi theo dõi độc lập")
    p_run.add_argument("--auto-approve", action="store_true", help="Approve input không tham số của đúng build trong lượt execute")
    p_run.add_argument("--ledger", type=Path, default=None, help="Mặc định .local/releases/<env>-ledger.json")
    p_run.set_defaults(func=cmd_deploy_run)

    p_sync = sub.add_parser("sync", help="Ghi mọi build pipeline thành công mới vào sổ release")
    p_sync.add_argument("services", nargs="*", help="Bỏ trống = toàn bộ boundaries + web")
    p_sync.add_argument("--env", required=True, choices=ENVS)
    p_sync.add_argument("--ledger", type=Path, default=None, help="Mặc định .local/releases/<env>-ledger.json")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    cfg = load_cfg(getattr(args, "env", None))
    args.func(cfg, args)


if __name__ == "__main__":
    main()
