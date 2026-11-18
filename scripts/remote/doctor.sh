#!/usr/bin/env bash
# doctor.sh — soi từng chặng của ỐNG, in bảng cổng thật.  CHẠY Ở MÁY GIỮ CODE.
#
# Soi CỤM (container/env/kết nối service) là việc của `make doctor` — hai tầng, đừng gộp.
# Một phép kiểm không bao giờ xanh được tệ hơn không có phép kiểm: mọi dòng dưới đây
# phải xanh được ở trạng thái bình thường.
set -uo pipefail
cd "$(dirname "$0")/../.."

ok()   { printf '  \033[32m✓\033[0m %-24s %s\n' "$1" "${2:-}"; }
no()   { printf '  \033[31m✗\033[0m %-24s %s\n' "$1" "${2:-}"; FAIL=1; }
warn() { printf '  \033[33m▲\033[0m %-24s %s\n' "$1" "${2:-}"; }
FAIL=0
SLUG="${RP_SLUG:-$(basename "$PWD")}"

echo
echo "── Chặng 0 · máy giữ code (đầu chờ) ───────────────────────────────"
if [ -f .local/remote/.remote-on ]; then ok "chế độ remote" "BẬT (.local/remote/.remote-on có mặt)"
else warn "chế độ remote" "TẮT — make chạy local. Bật: mkdir -p .local/remote && touch .local/remote/.remote-on"; fi

if (exec 3<>/dev/tcp/127.0.0.1/22) 2>/dev/null; then ok "sshd ở máy này" "nghe ở :22 — máy chạy nối vào được"
else warn "sshd ở máy này" "không nghe ở 127.0.0.1:22 — máy chạy cần một cổng SSH tới đây (có thể là cổng khác/qua NAT)"; fi
command -v rsync >/dev/null && ok "rsync" "$(rsync --version 2>/dev/null | head -1)" || no "rsync" "chưa cài"

# Kiểm SỰ TỒN TẠI trước khi source: _map.sh `exit 1` khi thiếu bản đồ, mà exit trong file
# được source thoát luôn doctor — mọi dòng hướng dẫn phía sau không bao giờ chạy.
if ! compgen -G "$HOME/.config/remote-pipe/$SLUG.*.map" >/dev/null; then
  no "bản đồ ống" "chưa có ~/.config/remote-pipe/$SLUG.<máy>.map"
  echo
  echo "  Ống mọc từ MÁY CHẠY. Trên máy Linux/WSL gõ MỘT lần:"
  echo
  echo "      ssh -p <cổng> $(id -un)@<máy-giữ-code> 'cat ${PWD/#$HOME/\~}/scripts/remote/pipe.sh' \\"
  echo "        | RP_REPO_HOST=${PWD/#$HOME\//} bash -s -- --install"
  echo
  echo "  Từng bước: scripts/remote/README.md"
  echo
  exit 1
fi
source scripts/remote/_map.sh
ok "bản đồ ống" "$RP_MAP"

echo
echo "── Bảng cổng THẬT đang dùng ───────────────────────────────────────"
printf '  %-22s %s\n' "cổng điều khiển (ctl)" "$RP_CTL   ← cổng DUY NHẤT bắt buộc"
printf '  %-22s %s\n' "máy chạy"             "$RP_RUNNER_USER@$RP_RUNNER_HOST"
printf '  %-22s %s\n' "repo ở máy chạy"      "$RP_REPO_RUNNER"

# Listener của -R thuộc sshd (có thể của root) — `ss` đọc bảng socket của kernel, không
# cần quyền trên tiến trình; lsof cho macOS.
if rp_port_open "$RP_CTL" \
   || { command -v ss >/dev/null && ss -Hltn "sport = :$RP_CTL" 2>/dev/null | grep -q .; } \
   || lsof -nP -iTCP:"$RP_CTL" -sTCP:LISTEN >/dev/null 2>&1; then
  ok "listener ở đây" "cổng $RP_CTL có người nghe"
else
  no "listener ở đây" "cổng $RP_CTL KHÔNG có listener — ống đã rớt"
  echo "     → ở $RP_RUNNER_HOST gõ: bash ~/.config/remote-pipe/$SLUG.pipe.sh"
fi

echo
echo "── Chặng 1 · điều khiển được máy chạy ─────────────────────────────"
# `| tail -1`: lần nối đầu ssh in "Permanently added …" ra stderr; hostname luôn là dòng cuối.
REMOTE_HOST="$("${RP_SSH[@]}" "$RP_TARGET" 'hostname' 2>&1 | tr -d '\r' | tail -1)"
if [ -n "$REMOTE_HOST" ] && [ "$REMOTE_HOST" != "$(hostname)" ] && [[ "$REMOTE_HOST" != *"ssh"* ]]; then
  ok "ssh qua ống" "hostname = $REMOTE_HOST"
  [ "${REMOTE_HOST%%.*}" = "$RP_RUNNER_HOST" ] || warn "bản đồ lệch" "bản đồ ghi '$RP_RUNNER_HOST' nhưng đầu kia là '$REMOTE_HOST' — chạy lại --install ở máy đó"
else
  no "ssh qua ống" "${REMOTE_HOST:-không trả lời}"
fi

if [ "$FAIL" = "0" ]; then
  if "${RP_SSH[@]}" "$RP_TARGET" "test -d '$RP_REPO_RUNNER/scripts/remote'" 2>/dev/null; then
    ok "repo ở máy chạy" "$RP_REPO_RUNNER đã có code"
  else
    warn "repo ở máy chạy" "chưa có code — bash scripts/remote/sync.sh"
  fi
  tools="$("${RP_SSH[@]}" "$RP_TARGET" "bash -lc 'for t in make python3 node docker tmux; do command -v \$t >/dev/null && printf \"%s \" \$t; done'" 2>/dev/null)"
  ok "công cụ ở máy chạy" "${tools:-—}"
  if [ -f deployment/local/docker-compose.yml ]; then
    if "${RP_SSH[@]}" "$RP_TARGET" "test -f '$RP_REPO_RUNNER/deployment/local/.env'" 2>/dev/null; then
      ok "env của máy chạy" "deployment/local/.env có mặt"
    else
      no "env của máy chạy" "thiếu deployment/local/.env ở máy chạy — sync.sh sẽ dừng hẳn"
    fi
  fi
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "  Kết luận: ống thông. Gõ lệnh quen thuộc ở đây (make dev / check / test) — chạy ở $RP_RUNNER_HOST."
else
  echo "  Kết luận: CÓ CHẶNG ĐỎ — sửa chặng đỏ ĐẦU TIÊN rồi chạy lại, đừng nhảy cóc."
fi
echo
exit "$FAIL"
