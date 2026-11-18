#!/usr/bin/env bash
# _map.sh — nạp bản đồ ống + dựng sẵn mảng RP_SSH.  CHẠY Ở MÁY GIỮ CODE (đầu chờ).
#
# Mọi script khác của ống source file này để dùng đúng MỘT bộ cờ SSH — lệch bộ cờ giữa
# các script là kiểu hỏng chỉ lộ ra ở một lệnh, khó lần nhất. Không script nào được
# hardcode cổng: cổng do pipe.sh cấp phát lúc --install và ghi vào bản đồ.
#
# Dùng (từ gốc repo):  source scripts/remote/_map.sh

set -euo pipefail

# Tên ống = tên thư mục repo ở máy giữ code (máy chạy dùng cùng tên: ~/<slug>).
RP_SLUG="${RP_SLUG:-$(basename "$PWD")}"
RP_CFG_DIR="$HOME/.config/remote-pipe"
RP_KNOWN_HOSTS="$RP_CFG_DIR/known_hosts"
RP_ACTIVE_FILE="$RP_CFG_DIR/$RP_SLUG.active"

# MỘT BẢN ĐỒ CHO MỖI MÁY CHẠY: mỗi máy giữ cổng ctl riêng ⇒ nhiều ống sống song song
# được, và đổi máy chỉ là đổi con trỏ `.active` (scripts/remote/use).
rp_map_for() { echo "$RP_CFG_DIR/$RP_SLUG.$1.map"; }

# Glob thuần bash, không `ls | sed`: dưới `set -euo pipefail` một `ls` không khớp gì
# giết script ngay trong phép gán — trước khi in được thông báo nào.
rp_machines() {
  local f name
  for f in "$RP_CFG_DIR/$RP_SLUG."*.map; do
    [ -e "$f" ] || continue
    name="${f##*/"$RP_SLUG".}"
    printf '%s\n' "${name%.map}"
  done
}

# Máy đang dùng: biến môi trường thắng, rồi tới con trỏ, rồi tới "chỉ có đúng một".
RP_MACHINE="${RP_MACHINE:-$(cat "$RP_ACTIVE_FILE" 2>/dev/null || true)}"
if [ -z "$RP_MACHINE" ]; then
  _only="$(rp_machines | head -2)"
  [ "$(printf '%s\n' "$_only" | grep -c .)" = "1" ] && RP_MACHINE="$_only"
fi

RP_MAP="$(rp_map_for "${RP_MACHINE:-unset}")"

if [ -z "$RP_MACHINE" ] || [ ! -f "$RP_MAP" ]; then
  {
    echo "✗ Chưa chọn được máy chạy nào cho ống '$RP_SLUG'."
    _have="$(rp_machines)"
    if [ -n "$_have" ]; then
      echo
      echo "  Đã dựng ống với:"
      printf '    %s\n' $_have
      echo
      echo "  Chọn một:  scripts/remote/use <tên-máy>       (hoặc RP_MACHINE=<tên> make …)"
    else
      echo
      echo "  Chưa máy nào. Ống mọc từ MÁY CHẠY (Linux/WSL), không từ đây."
      echo "  Ống là của TỪNG TÀI KHOẢN: tài khoản khác đã dựng không giúp gì cho tài khoản này."
      echo "  Trên máy chạy gõ MỘT lần (đường dẫn repo tính từ \$HOME của máy giữ code):"
      echo
      echo "      ssh -p <cổng> $(id -un)@<máy-giữ-code> 'cat ${PWD/#$HOME/\~}/scripts/remote/pipe.sh' \\"
      echo "        | RP_REPO_HOST=${PWD/#$HOME\//} bash -s -- --install"
      echo
      echo "  Chi tiết: scripts/remote/README.md"
    fi
  } >&2
  exit 1
fi

# Bản đồ là key=value thuần, không eval file lạ — chỉ nhận khoá đã biết.
rp_map_get() { sed -n "s/^$1=//p" "$RP_MAP" | head -1; }

RP_CTL="$(rp_map_get ctl)"
RP_RUNNER_USER="$(rp_map_get runner_user)"
RP_RUNNER_HOST="$(rp_map_get runner_host)"
RP_REPO_RUNNER="$(rp_map_get repo_runner)"

for v in RP_CTL RP_RUNNER_USER RP_RUNNER_HOST RP_REPO_RUNNER; do
  if [ -z "${!v}" ]; then
    echo "✗ Bản đồ $RP_MAP thiếu khoá cho $v — chạy lại pipe.sh --install ở máy chạy" >&2
    exit 1
  fi
done

mkdir -p "$RP_CFG_DIR"

# HostKeyAlias theo TÊN MÁY, không theo 127.0.0.1:<ctl>: cổng ctl là CỬA ỐNG, hôm nay
# dẫn tới máy A, mai máy B — ghim theo địa chỉ thì đổi máy là ssh báo REMOTE HOST
# IDENTIFICATION HAS CHANGED và từ chối chạy (accept-new chỉ nhận host mới, không nhận
# host đổi). BatchMode=yes bắt buộc: một lời nhắc mật khẩu lạc vào stdout là hỏng mọi
# kênh dùng stdin/stdout của ssh (checker --bundle-gzip, gateway trình duyệt).
RP_SSH=(
  ssh -p "$RP_CTL"
  -o "UserKnownHostsFile=$RP_KNOWN_HOSTS"
  -o "HostKeyAlias=rp-$RP_RUNNER_HOST"
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=8
  -o BatchMode=yes
)
RP_TARGET="$RP_RUNNER_USER@127.0.0.1"

# Cổng có người nghe ở máy này không — không dùng `nc -G` (chỉ có ở macOS).
rp_port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

export RP_SLUG RP_MAP RP_CTL RP_RUNNER_USER RP_RUNNER_HOST RP_REPO_RUNNER RP_TARGET
