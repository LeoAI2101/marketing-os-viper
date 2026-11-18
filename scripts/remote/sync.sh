#!/usr/bin/env bash
# sync.sh — đẩy code máy giữ code → máy chạy.  MỘT CHIỀU, không bao giờ sync ngược.
#
#   bash scripts/remote/sync.sh            một lượt
#   bash scripts/remote/sync.sh --watch    lặp mỗi 2s
#   PT=1 bash scripts/remote/sync.sh       kèm snapshot mốc hợp đồng API cho nhóm PT
#                                          (máy chạy không có .git — skill api-contract)
#
# Sync hai chiều sẽ ăn mất thay đổi ở máy giữ code khi artifact build ở máy chạy ghi đè.
# Artifact build ở máy chạy ở lại máy chạy; thứ sinh máy cần vào git thì kéo về bằng
# pull-generated.sh (allowlist hẹp). Bộ cờ -rlptz + -e: openrsync của macOS 15+ không
# có cờ dài kiểu GNU (--archive, --rsh), máy giữ code là Mac vẫn chạy được.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/remote/_map.sh

WATCH=0
[ "${1:-}" = "--watch" ] && WATCH=1

# ── env là CĂN CƯỚC CỦA MÁY CHẠY, không phải hàng gửi đi ─────────────────────
# Cổng, mật khẩu local, đường dẫn khác nhau từng máy — env Ở LẠI chỗ nó mô tả và
# `--delete` không được chạm tới nó. Dự án đã có cụm local (deployment/local/
# docker-compose.yml) mà máy đích thiếu env thì DỪNG: đẩy sang một repo thiếu env
# tạo ra đúng triệu chứng tốn giờ nhất — mọi service chết, log đọc lên như lỗi code.
if [ -f deployment/local/docker-compose.yml ] && \
   ! "${RP_SSH[@]}" "$RP_TARGET" "test -f '$RP_REPO_RUNNER/deployment/local/.env'" 2>/dev/null; then
  {
    echo "✗ Máy chạy ($RP_RUNNER_HOST) thiếu deployment/local/.env — DỪNG, không đẩy code sang."
    echo
    echo "  Dựng ở MÁY CHẠY (không phải ở đây):"
    echo "    cd $RP_REPO_RUNNER && cp deployment/.env.example deployment/local/.env   rồi điền giá trị local"
  } >&2
  exit 1
fi

# ── Thứ KHÔNG BAO GIỜ sang ───────────────────────────────────────────────────
# Viết TAY, không lấy .gitignore làm exclude-list (nó chặn cả thứ máy chạy cần).
# Mọi thư mục binary native phải ở đây: Prisma engine, esbuild, @swc/core… build cho
# hệ điều hành của máy giữ code, copy sang là chết với thông báo vô nghĩa — máy chạy
# tự `npm ci` bản của nó (run.sh).
EXCLUDES=(
  --exclude '.git/'
  --exclude 'node_modules/'
  --exclude 'dist/'
  --exclude 'dist-contract/'
  --exclude 'build/'
  --exclude 'out/'
  --exclude '.next/'
  --exclude '.turbo/'
  --exclude '.dart_tool/'
  --exclude '.venv/'
  --exclude '__pycache__/'
  --exclude 'coverage/'
  --exclude '.nyc_output/'
  --exclude 'playwright-report/'
  --exclude 'test-results/'
  --exclude '.playwright-mcp/'
  --exclude '.viper/'
  --exclude '*.tsbuildinfo'
  --exclude '.DS_Store'
  --exclude 'demos/loop-*/'
  # env của máy chạy: không đẩy, và --delete không xoá bản ở đích.
  --exclude 'deployment/local/.env'
  --exclude 'deployment/local/.env.*'
  # .local/: chỉ khuôn công khai đi sang; env thật, sm_configs, sổ release ở lại máy này.
  --include '/.local/'
  --include '/.local/templates/***'
  --exclude '/.local/***'
  # ── Artifact SINH MÁY: không đẩy bản ở đây đè lên, không xoá bản ở máy chạy ──
  # Chúng sinh Ở MÁY CHẠY (cần node_modules) rồi kéo về đây để commit (pull-generated.sh).
  --exclude 'srcroot/contracts/'
  --exclude 'srcroot/boundaries/*/src/shared/contracts/'
  --exclude 'srcroot/web-experiences/*/src/shared/generated/'
  # Mobile: build/ và plumbing ephemeral (liên kết mềm tuyệt đối vào máy giữ code).
  --exclude 'srcroot/mobile-experiences/*/build/'
  --exclude 'srcroot/mobile-experiences/*/ios/Flutter/ephemeral/'
  --exclude 'srcroot/mobile-experiences/*/macos/Flutter/ephemeral/'
)

# Hợp đồng sinh máy đi RIÊNG một lượt, `-u`: chỉ đẩy khi bản ở đây MỚI HƠN (vừa sinh ở
# máy chạy rồi kéo về commit) — bản máy chạy vừa sinh mà chưa kéo về thì vẫn giữ.
gen_dirs() {
  local d
  for d in srcroot/contracts/ srcroot/boundaries/*/src/shared/contracts/ \
           srcroot/web-experiences/*/src/shared/generated/; do
    [ -d "$d" ] && printf '%s\n' "$d"
  done
  return 0
}

do_sync() {
  # Nhóm PT cần mốc Git của api-contract; máy chạy không có .git nên mang snapshot sang.
  if [ "${PT:-0}" = 1 ] && [ -f context/perf/API-CONTRACT-BASE.json ]; then
    RP_LOCAL=1 node scripts/check-api-contract.mjs --snapshot || return $?
  fi
  local rsh="ssh -p $RP_CTL -o UserKnownHostsFile=$RP_KNOWN_HOSTS -o HostKeyAlias=rp-$RP_RUNNER_HOST -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  rsync -rlptz --delete "${EXCLUDES[@]}" -e "$rsh" ./ "$RP_TARGET:$RP_REPO_RUNNER/" || return $?
  local gen=() d
  while IFS= read -r d; do gen+=("./$d"); done < <(gen_dirs)
  if [ "${#gen[@]}" -gt 0 ]; then
    local pushed
    pushed="$(rsync -rlptzuR --out-format='%n' -e "$rsh" "${gen[@]}" "$RP_TARGET:$RP_REPO_RUNNER/")" || return $?
    pushed="$(printf '%s\n' "$pushed" | grep -v '/$' | grep -c . || true)"
    [ "$pushed" = 0 ] || echo "▸ hợp đồng sinh máy: đẩy $pushed tệp mới hơn sang máy chạy" >&2
  fi
  if [ "${PT:-0}" = 1 ] && [ -f .local/contracts/api-contract-base.json ]; then
    "${RP_SSH[@]}" "$RP_TARGET" "set -e; mkdir -p '$RP_REPO_RUNNER/.local/contracts'; tmp=\$(mktemp '$RP_REPO_RUNNER/.local/contracts/api-contract-base.XXXXXX'); trap 'rm -f \"\$tmp\"' EXIT; cat > \"\$tmp\"; mv \"\$tmp\" '$RP_REPO_RUNNER/.local/contracts/api-contract-base.json'" < .local/contracts/api-contract-base.json || return $?
  fi
}

if [ "$WATCH" = "1" ]; then
  echo "▸ sync --watch: đẩy lại mỗi 2s, Ctrl-C để dừng" >&2
  while true; do do_sync; sleep 2; done
else
  started=$(date +%s); rc=0
  do_sync || rc=$?
  if [ "$rc" = "0" ]; then echo "✅ sync → $RP_RUNNER_HOST XONG — $(( $(date +%s) - started ))s" >&2
  else echo "❌ sync → $RP_RUNNER_HOST DỪNG (mã $rc)" >&2; exit "$rc"; fi
fi
