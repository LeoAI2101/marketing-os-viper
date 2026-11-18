"""Quy tắc promote RC đã chạy ở môi trường nguồn sang PT/preprod/PROD, không tạo RC mới.

PROD lấy image từ RC của PREPROD nhưng mang tag release riêng `vX.Y.Z` bắt đầu từ v1.0.1,
độc lập với số RC (RC chỉ để chọn image nguồn)."""
from __future__ import annotations

from release_common import COMMIT_PATTERN, RC_PATTERN, RELEASE_PATTERN, ReleaseError

PROMOTION_SOURCE = {"PT": "UAT", "PREPROD": "UAT", "PROD": "PREPROD"}
PROMOTION_ENVS = tuple(PROMOTION_SOURCE)
FIRST_RELEASE = "v1.0.1"


def source_error(env: str, source_env: str | None) -> str | None:
    expected = PROMOTION_SOURCE.get(env.upper())
    if expected and (source_env or "").upper() != expected:
        return f"{env.upper()} chỉ lấy nguồn {expected}; truyền --source-env {expected.lower()}."
    return None


def release_key(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.removeprefix("v").split("."))


def next_release(tag: str | None) -> str:
    """Tag release PROD kế tiếp: chưa có bản nào → v1.0.1, còn lại tăng patch."""
    if not tag:
        return FIRST_RELEASE
    if not RELEASE_PATTERN.fullmatch(tag):
        raise ReleaseError(f"Tag release PROD hiện tại {tag!r} không dạng vX.Y.Z.")
    major, minor, patch = release_key(tag)
    return f"v{major}.{minor}.{patch + 1}"


def select_release(snapshot: dict, rc_tag: str | None) -> dict | None:
    """Mặc định bản UAT thành công; chọn RC cũ phải có bằng chứng ở nguồn."""
    running = snapshot["running"]
    if not rc_tag or (running and running.get("rc_tag") == rc_tag):
        return running
    builds = [b for b in snapshot.get("builds", [])
              if b.get("rc_tag") == rc_tag and b.get("result") in ("SUCCESS", "UNSTABLE")]
    if builds:
        return max(builds, key=lambda b: b["build_number"])
    for image in snapshot.get("releases", []):
        if rc_tag in image.get("tags", []):
            return {**image, "rc_tag": rc_tag}
    raise ReleaseError(f"Không thấy RC {rc_tag} thành công ở UAT hoặc có trong ECR nguồn.")


def promotion_plan(current: dict | None, source: dict | None, group: str, env: str | None = None,
                   release_tag: str | None = None) -> dict:
    if not source or not RC_PATTERN.fullmatch(source.get("rc_tag") or ""):
        raise ReleaseError(f"{PROMOTION_SOURCE.get((env or '').upper(), 'UAT')} chưa có RC thành công để promote.")
    rc_tag = source["rc_tag"]
    image_tag = source.get("commit_tag") if group == "web-experiences" else rc_tag
    if not image_tag:
        raise ReleaseError("Bản web UAT thiếu commit để gửi SHORT_COMMIT.")
    same = bool(current and current.get("rc_tag") == rc_tag)
    for key in ("digest", "commit_tag"):
        if same and source.get(key) and current.get(key):
            same = source[key] == current[key]
    plan = {"rc_tag": rc_tag, "image_tag": image_tag, "skip": same,
            "note": "RC nguồn đã có ở môi trường đích." if same else None,
            "status": "pending", "jenkins": None}
    if (env or "").upper() == "PROD":
        if not release_tag and current and not current.get("release_tag"):
            raise ReleaseError("Jenkins PROD đã có build thành công nhưng không đọc được tag release (vX.Y.Z)"
                               " từ form; kiểm `jenkins_build.py params`/`history` rồi truyền --release-tag.")
        plan["release_tag"] = release_tag or next_release((current or {}).get("release_tag"))
    return plan


def promotion_params(env: str, group: str, deploy: dict) -> dict:
    if group == "web-experiences":
        params = {"RC_TAG": deploy["rc_tag"], "SHORT_COMMIT": deploy["image_tag"]}
        if env.upper() == "PROD":
            params["RELEASE_TAG"] = deploy["release_tag"]
        return params
    key = "IMAGE_TAG" if env.upper() == "PT" else "RC_TAG"
    params = {"SOURCE_ENV": PROMOTION_SOURCE.get(env.upper(), "uat").lower(), key: deploy["rc_tag"]}
    if env.upper() == "PROD":
        params["RELEASE_TAG"] = deploy["release_tag"]
    return params


def build_promotion_params(env: str, group: str, args) -> dict:
    if group not in ("boundaries", "web-experiences"):
        raise ReleaseError("PT/preprod/prod chỉ có job boundaries và web.")
    rc_tag = args.rc_tag or (args.image_tag if group == "boundaries" else None)
    if not RC_PATTERN.fullmatch(rc_tag or ""):
        raise ReleaseError(f"{env} cần RC nguồn {PROMOTION_SOURCE[env.upper()]} dạng vX.Y.Z-rc-N.")
    release = getattr(args, "release_tag", None)
    if env.upper() == "PROD" and not RELEASE_PATTERN.fullmatch(release or ""):
        raise ReleaseError("PROD cần --release-tag dạng vX.Y.Z (bắt đầu từ v1.0.1).")
    if group == "boundaries":
        if args.image_tag and args.image_tag != rc_tag:
            raise ReleaseError("--image-tag phải khớp RC nguồn UAT, không dùng commit.")
        return promotion_params(env, group, {"rc_tag": rc_tag, "release_tag": release})
    commit = getattr(args, "short_commit", None) or args.image_tag
    if not COMMIT_PATTERN.fullmatch(commit or ""):
        raise ReleaseError("Web cần --short-commit của RC nguồn UAT.")
    params = promotion_params(env, group, {"rc_tag": rc_tag, "image_tag": commit, "release_tag": release})
    if args.git_tag:
        params["GIT_TAG"] = args.git_tag
    return params


def promotion_blocker(row: dict, env: str = "PREPROD") -> str | None:
    deploy, source = row["deploy"], row.get("source") or {}
    expected = PROMOTION_SOURCE.get(env.upper(), "UAT")
    if row.get("group") not in ("boundaries", "web-experiences"):
        return "PT/preprod/prod chỉ có job boundaries và web"
    if (source.get("env") or "").upper() != expected:
        return f"thiếu bằng chứng nguồn {expected}; chạy lại prepare_deploy --source-env {expected}"
    if env.upper() == "PROD" and not RELEASE_PATTERN.fullmatch(deploy.get("release_tag") or ""):
        return "release_tag PROD thiếu hoặc sai dạng vX.Y.Z"
    if source.get("rc_tag") != deploy.get("rc_tag"):
        return f"RC deploy khác RC nguồn {expected} đã chuẩn bị; chạy lại prepare_deploy --rc-tag"
    if row["group"] == "boundaries" and deploy.get("image_tag") != deploy.get("rc_tag"):
        return f"image_tag phải là đúng RC nguồn {expected}"
    if row["group"] == "web-experiences":
        if not COMMIT_PATTERN.fullmatch(deploy.get("image_tag") or ""):
            return "SHORT_COMMIT thiếu hoặc không phải mã commit"
        if deploy.get("image_tag") != source.get("commit_tag"):
            return f"SHORT_COMMIT khác commit của RC nguồn {expected} đã chuẩn bị"
    return None
