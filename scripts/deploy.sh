#!/usr/bin/env bash
# `make deploy` dùng cùng workflow với bốn skills.
# Chuẩn bị: AWS_PROFILE=<profile> make deploy ENV=SIT
# Dry-run: make deploy ENV=SIT RUN=<ID>
# Execute: make deploy ENV=SIT RUN=<ID> CONFIRM=1
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_ID="$(printf '%s' "${ENV:-SIT}" | tr 'a-z' 'A-Z')"
PHASE="${DEPLOY_PHASE:-}"
if [ -z "$PHASE" ]; then
  if [ "${CONFIRM:-0}" = 1 ]; then
    PHASE=execute
  elif [ -n "${RUN:-}" ]; then
    PHASE=dry-run
  else
    PHASE=prepare
  fi
fi
if [ "$PHASE" = execute ] && [ "${CONFIRM:-0}" != 1 ]; then
  echo '✗ Execute cần CONFIRM=1 theo yêu cầu deploy thật.' >&2
  exit 1
fi
if [ "$PHASE" = execute ] || [ "$PHASE" = dry-run ]; then
  [ -n "${RUN:-}" ] || { echo '✗ Cần RUN của lượt đã prepare và dry-run; không tự tạo rồi execute.' >&2; exit 1; }
fi
if [ "${SKIP_TEST:-0}" = 1 ]; then
  echo '✗ Không bỏ qua kiểm tra trước deploy; chạy lại không có SKIP_TEST=1.' >&2
  exit 1
fi
CMD=(python3 scripts/deploy_workflow.py "$PHASE" --env "$ENV_ID")
[ -z "${RUN:-}" ] || CMD+=(--run "$RUN")
[ -z "${AWS_PROFILE:-}" ] || CMD+=(--profile "$AWS_PROFILE")
[ -z "${RC_TAG:-}" ] || CMD+=(--rc-tag "$RC_TAG")
[ "${TARGET:-all}" = all ] || CMD+=(--only "$TARGET")
exec "${CMD[@]}"
