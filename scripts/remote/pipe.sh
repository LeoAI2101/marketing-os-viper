#!/usr/bin/env bash
# pipe.sh — ★ CHẠY Ở MÁY CHẠY (Linux/WSL: ideapad hay máy bất kỳ). Thứ DUY NHẤT người dùng gõ ở đó.
#
#   một lần:   ssh -p <cổng> <user>@<máy-giữ-code> 'cat <repo>/scripts/remote/pipe.sh' \
#                | RP_REPO_HOST=<đường/repo/tính/từ/HOME> bash -s -- --install
#   mỗi ngày:  bash ~/.config/remote-pipe/<slug>.pipe.sh
#
# Ống mọc từ ĐÂY, không từ máy giữ code: máy chạy thường nằm sau NAT/firewall, kết nối
# duy nhất tồn tại là máy chạy → máy giữ code, nên đường ống phải mọc từ kết nối đó
# bằng `ssh -R <ctl>:127.0.0.1:22`. Máy giữ code (đầu chờ) chỉ cần sshd + repo.
#
# Cấu hình chung của máy chạy: ~/.config/remote-pipe/config
#   RP_HOST_CANDIDATES="<user>@<địa-chỉ>[:<cổng>] …"   lối vào máy giữ code, thử lần lượt
#   RP_SSH_KEY="$HOME/.ssh/<khoá>"                     tuỳ chọn, mặc định ~/.ssh/id_ed25519

set -uo pipefail

SELF="$0"
CFG_DIR="$HOME/.config/remote-pipe"

# Tên ống: RP_SLUG > tên bản cất `<slug>.pipe.sh` > tên thư mục repo ở máy giữ code.
SLUG="${RP_SLUG:-}"
if [ -z "$SLUG" ] && [ -f "$SELF" ]; then
  case "$(basename "$SELF")" in *.pipe.sh) SLUG="$(basename "$SELF" .pipe.sh)" ;; esac
fi
[ -f "$CFG_DIR/${SLUG:-_}.pipe.env" ] && . "$CFG_DIR/$SLUG.pipe.env"
: "${RP_REPO_HOST:=}"
[ -n "$SLUG" ] || SLUG="$(basename "${RP_REPO_HOST:-}")"

# RP_PROFILE — một user mở NHIỀU ống vào máy giữ code bằng nhiều tài khoản khác nhau. Mỗi
# profile có config, phiên tmux và bản sao repo RIÊNG: dùng chung repo thì `sync.sh --delete`
# của tài khoản này xoá file của tài khoản kia. Trống = một ống, hành vi mặc định.
RP_PROFILE="${RP_PROFILE:-}"
if [ -n "$RP_PROFILE" ]; then
  CFG="$CFG_DIR/config.$RP_PROFILE"
  REPO_RUNNER="${RP_REPO_RUNNER:-$HOME/$SLUG-$RP_PROFILE}"
else
  CFG="$CFG_DIR/config"
  REPO_RUNNER="${RP_REPO_RUNNER:-$HOME/$SLUG}"
fi
MAPNAME="$SLUG.$(hostname -s).map"

[ -f "$CFG" ] && . "$CFG"
: "${RP_HOST_CANDIDATES:=${RP_MAC_CANDIDATES:-}}"
: "${RP_SSH_KEY:=}"
# Cổng sshd CỦA MÁY CHẠY mà ống trỏ vào (-R <ctl>:127.0.0.1:<cổng>). Mặc định 22; đổi khi
# cổng 22 đã có chủ (WSL dùng chung với Windows) hoặc chạy sshd riêng không cần root.
: "${RP_RUNNER_SSHD_PORT:=22}"

say()  { printf '\033[36m▸\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; }

# ── Chọn lối vào máy giữ code: DANH SÁCH, chọn lại MỖI LẦN nối ──────────────
# Ghim một địa chỉ là mất ống khi xách máy từ văn phòng về nhà (trong LAN ↔ ngoài LAN).
host_ssh_opts=()
pick_host() {
  [ -n "$RP_HOST_CANDIDATES" ] || die "Chưa khai RP_HOST_CANDIDATES trong $CFG — xem scripts/remote/README.md §2"
  local cand host port user err
  for cand in $RP_HOST_CANDIDATES; do
    user="${cand%%@*}"; host="${cand#*@}"; port=22
    case "$host" in *:*) port="${host##*:}"; host="${host%%:*}";; esac
    host_ssh_opts=(-p "$port" -o ConnectTimeout=5 -o BatchMode=yes)
    [ -n "$RP_SSH_KEY" ] && host_ssh_opts+=(-i "$RP_SSH_KEY" -o IdentitiesOnly=yes)
    # Giữ LỖI THẬT của ssh, đừng nuốt rồi in một câu đoán sẵn — "no matching host key type"
    # nghĩa là địa chỉ ấy là máy KHÁC (router…), không phải sai cổng.
    if err="$(ssh "${host_ssh_opts[@]}" "$user@$host" true 2>&1)"; then
      HOST_ADDR="$user@$host"; HOST_PORT="$port"
      ok "lối vào máy giữ code: $HOST_ADDR:$HOST_PORT"
      return 0
    fi
    printf '\033[33m▲\033[0m %s: %s\n' "$cand" "$(printf '%s' "$err" | tail -1)" >&2
  done
  {
    echo "  Lỗi THẬT của từng ứng viên in ở ngay trên — đọc nó, đừng đoán:"
    echo "    · 'Connection refused'        → cổng đó không ai nghe"
    echo "    · 'Permission denied'         → tới đúng máy, sai khoá"
    echo "    · 'no matching host key type' → tới NHẦM MÁY"
  } >&2
  die "Không ứng viên nào tới được máy giữ code. Đã thử: $RP_HOST_CANDIDATES"
}
on_host() { ssh "${host_ssh_opts[@]}" "$HOST_ADDR" "$@"; }

# ── Tiền kiểm: báo GỘP, không chết từng cái một ─────────────────────────────
preflight() {
  local miss=()
  command -v tmux    >/dev/null || miss+=("tmux              → sudo apt install -y tmux")
  command -v rsync   >/dev/null || miss+=("rsync             → sudo apt install -y rsync")
  command -v make    >/dev/null || miss+=("make              → sudo apt install -y build-essential")
  command -v python3 >/dev/null || miss+=("python3           → sudo apt install -y python3")
  command -v sshd >/dev/null || [ -x /usr/sbin/sshd ] || miss+=("openssh-server    → sudo apt install -y openssh-server")
  if [ "${#miss[@]}" -gt 0 ]; then
    printf '\033[31m✗ Thiếu %d thứ. Cài HẾT rồi chạy lại đúng lệnh này:\033[0m\n' "${#miss[@]}" >&2
    printf '    %s\n' "${miss[@]}" >&2
    exit 1
  fi
  ok "tiền kiểm: đủ công cụ của ống"
  # Công cụ theo STACK (TECHSTACK.md) — thiếu thì cảnh báo, không chặn dựng ống.
  command -v docker >/dev/null || printf '\033[33m▲\033[0m docker chưa có — cụm local (deployment/local/) sẽ không chạy được ở máy này\n' >&2
  # Node của LOGIN shell: mọi lệnh qua ống đi `bash -lc`, nên đó mới là node sẽ chạy.
  local nodever; nodever="$(bash -lc 'command -v node >/dev/null && node -p "process.versions.node" || true' 2>/dev/null)"
  [ -n "$nodever" ] && ok "node ở login shell: $nodever" \
    || printf '\033[33m▲\033[0m node chưa có ở login shell — stack Node/checker AST (query-safety, data-security) sẽ không chạy\n' >&2

  # sshd phải nghe ở 127.0.0.1:<RP_RUNNER_SSHD_PORT> NGAY TRONG máy này: -R trỏ vào chính nó.
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$RP_RUNNER_SSHD_PORT") 2>/dev/null; then
    [ "$RP_RUNNER_SSHD_PORT" = 22 ] || die "không có sshd nào nghe ở 127.0.0.1:$RP_RUNNER_SSHD_PORT (RP_RUNNER_SSHD_PORT)"
    if [ -d /run/systemd/system ]; then
      say "bật sshd: sudo systemctl enable --now ssh"; sudo systemctl enable --now ssh || true
    else
      say "bật sshd (không có systemd — WSL?): sudo service ssh start"
      sudo ssh-keygen -A 2>/dev/null || true
      sudo service ssh start || true
    fi
    (exec 3<>/dev/tcp/127.0.0.1/22) 2>/dev/null || die "sshd vẫn không nghe ở 127.0.0.1:22 — thiếu host key? sudo ssh-keygen -A"
  fi
  ok "sshd của máy này nghe ở 127.0.0.1:$RP_RUNNER_SSHD_PORT"

  if is_wsl; then
    case "$REPO_RUNNER" in
      /mnt/*)
        echo "  Ổ Windows nhìn qua WSL đi đường 9p, CHẬM HÀNG CHỤC LẦN — để repo ở filesystem native:" >&2
        echo "  RP_REPO_RUNNER=\$HOME/$SLUG" >&2
        die "Repo đang ở $REPO_RUNNER" ;;
    esac
  fi
}

# ── Cấp phát cổng ctl: per-user, động, tự né. CHẠY Ở MÁY GIỮ CODE ────────────
# Cổng TCP là tài nguyên TOÀN MÁY (máy giữ code có thể dùng chung nhiều user): cấm bảng
# cổng cố định, phải bind THẬT để biết cổng trống.
alloc_port() {
  on_host "python3 - <<'PY'
import os, socket, sys
base = int(os.environ.get('RP_BASE') or (20000 + (os.getuid() % 400) * 100))
p = base
while p < 65535:
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', p)); s.close(); print(p); break
    except OSError:
        s.close(); p += 1
else:
    sys.exit('hết cổng trống')
PY"
}

do_install() {
  if [ -z "$RP_REPO_HOST" ]; then
    echo "  RP_REPO_HOST = đường dẫn repo ở máy giữ code, tính từ \$HOME của tài khoản đó." >&2
    echo "  Ví dụ: … | RP_REPO_HOST=Workspace/AC/VENDOR-VIPER bash -s -- --install" >&2
    die "Thiếu RP_REPO_HOST"
  fi
  [ -n "$SLUG" ] || die "Không suy được tên ống — đặt RP_SLUG=<tên>"

  say "1/7 · tiền kiểm máy này"
  preflight

  say "2/7 · chọn lối vào máy giữ code"
  pick_host

  say "3/7 · trao đổi khoá (không copy tay)"
  [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519" -C "$(id -un)@$(hostname -s)" >/dev/null
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  # Chiều máy giữ code → máy chạy: kéo public key của máy giữ code về authorized_keys ở đây.
  # Đây là chiều ống thật sự dùng (đầu chờ gõ lệnh chạy ở đây qua cổng ctl).
  local host_pub
  host_pub="$(on_host 'test -f ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "$(id -un)@$(hostname -s)" >/dev/null 2>&1; cat ~/.ssh/id_ed25519.pub')"
  [ -n "$host_pub" ] || die "không lấy được public key của máy giữ code"
  touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"
  grep -qxF "$host_pub" "$HOME/.ssh/authorized_keys" || echo "$host_pub" >> "$HOME/.ssh/authorized_keys"
  ok "khoá của máy giữ code đã nằm trong authorized_keys ở đây"

  say "4/7 · tạo thư mục repo ở máy này"
  mkdir -p "$REPO_RUNNER" "$CFG_DIR"
  printf 'RP_REPO_HOST=%q\nRP_RUNNER_SSHD_PORT=%q\n' "$RP_REPO_HOST" "$RP_RUNNER_SSHD_PORT" > "$CFG_DIR/$SLUG.pipe.env"
  ok "$REPO_RUNNER"

  say "5/7 · cấp cổng điều khiển ở máy giữ code"
  CTL="$(RP_BASE="${RP_BASE:-}" alloc_port | tr -d ' \r')"
  [ -n "$CTL" ] || die "không cấp được cổng ở máy giữ code"
  ok "cổng điều khiển: $CTL"

  say "6/7 · ghi bản đồ ống vào máy giữ code"
  on_host "mkdir -p ~/.config/remote-pipe && printf '%s\n' '$(hostname -s)' > ~/.config/remote-pipe/$SLUG.active && cat > ~/.config/remote-pipe/$MAPNAME" <<MAP
ctl=$CTL
runner_user=$(id -un)
runner_host=$(hostname -s)
repo_runner=$REPO_RUNNER
base=${RP_BASE:-auto}
MAP
  ok "~/.config/remote-pipe/$MAPNAME ở máy giữ code (máy đang chọn: $(hostname -s))"

  say "7/7 · mở ống + kéo code lần đầu"
  start_tunnel_bg
  sleep 2
  # Nháy ĐÔI ở vế remote để shell bên kia nở $HOME (nháy đơn thì `cd` nhận chuỗi "$HOME/…").
  on_host "test -d \"\$HOME/$RP_REPO_HOST\"" || die "Không thấy repo ở máy giữ code: \$HOME/$RP_REPO_HOST"
  if on_host "cd \"\$HOME/$RP_REPO_HOST\" && bash scripts/remote/sync.sh"; then
    ok "code đã về $REPO_RUNNER"
  else
    die "kéo code lần đầu TRƯỢT — đọc thông báo NGAY TRÊN dòng này, nó nói rõ nguyên nhân."
  fi

  echo >&2
  ok "XONG. Từ giờ, mỗi ngày chỉ cần:  ${RP_PROFILE:+RP_PROFILE=$RP_PROFILE }bash ~/.config/remote-pipe/$SLUG.pipe.sh"
  say "Ở máy giữ code: mkdir -p .local/remote && touch .local/remote/.remote-on  rồi  bash scripts/remote/doctor.sh"
}

# Cài bằng ống (`… | bash -s -- --install`) thì `$0` là chuỗi "bash", KHÔNG phải file —
# `bash '$0' --loop` trong tmux sẽ chết ngay mà tmux vẫn báo tạo session thành công.
# Nên CẤT một bản thật ra ~/.config/remote-pipe/<slug>.pipe.sh (ngoài repo — sống qua mọi
# lượt `sync.sh --delete`) rồi chạy bản đó.
ensure_self() {
  local saved="$CFG_DIR/$SLUG.pipe.sh"
  mkdir -p "$CFG_DIR"
  if [ -f "$SELF" ]; then
    [ "$SELF" -ef "$saved" ] || cp -f "$SELF" "$saved"
  else
    on_host "cat \"\$HOME/$RP_REPO_HOST/scripts/remote/pipe.sh\"" > "$saved" || die "không kéo được bản pipe.sh về $saved"
    [ -s "$saved" ] || die "bản pipe.sh kéo về rỗng: $saved"
    ok "cất bản chạy được: $saved"
  fi
  SELF="$saved"
}

start_tunnel_bg() {
  ensure_self
  # Dọn tiến trình cũ CỦA CHÍNH MÌNH còn giữ cổng — nối lại mà quên bước này thì -R trượt.
  on_host "pkill -u \"\$(id -un)\" -f 'sshd.*:$CTL' 2>/dev/null; true" >/dev/null 2>&1 || true
  tmux kill-session -t "$TMUX_NAME" 2>/dev/null || true
  tmux new-session -d -s "$TMUX_NAME" "RP_PROFILE='$RP_PROFILE' RP_SLUG='$SLUG' RP_RUNNER_SSHD_PORT='$RP_RUNNER_SSHD_PORT' bash '$SELF' --loop $CTL '$HOST_ADDR' '$HOST_PORT'"
  ok "ống chạy trong tmux: $TMUX_NAME"
}

do_loop() {
  local ctl="$1" addr="$2" port="$3"
  while true; do
    # ExitOnForwardFailure: cổng kẹt thì báo lỗi thay vì chạy tiếp với ống thủng.
    # ServerAlive*: rớt mạng thì client chết NHANH để vòng lặp nối lại, không treo cổng.
    local opts=(-N -p "$port"
      -o ExitOnForwardFailure=yes
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3
      -o BatchMode=yes
      -R "$ctl:127.0.0.1:$RP_RUNNER_SSHD_PORT")
    [ -n "$RP_SSH_KEY" ] && opts+=(-i "$RP_SSH_KEY" -o IdentitiesOnly=yes)
    echo "$(date '+%F %T') nối ống → $addr:$port (-R $ctl)" >&2
    ssh "${opts[@]}" "$addr"
    echo "$(date '+%F %T') ống rớt, nối lại sau 3s" >&2
    sleep 3
  done
}

TMUX_NAME="$(id -un)-$SLUG-pipe${RP_PROFILE:+-$RP_PROFILE}"

case "${1:-}" in
  --install) do_install ;;
  --loop)    shift; do_loop "$@" ;;
  "")
    [ -n "$SLUG" ] && [ -n "$RP_REPO_HOST" ] || die "Máy này chưa --install ống nào (thiếu $CFG_DIR/<slug>.pipe.env)"
    preflight
    pick_host
    CTL="$(on_host "sed -n 's/^ctl=//p' ~/.config/remote-pipe/$MAPNAME 2>/dev/null | head -1" | tr -d ' \r')"
    [ -n "$CTL" ] || die "Máy này chưa có bản đồ ở máy giữ code ($MAPNAME) — chạy MỘT lần: --install"
    # Chỉ trỏ `.active`; KHÔNG đụng bản đồ của máy khác — nhiều ống sống song song được.
    on_host "printf '%s\n' '$(hostname -s)' > ~/.config/remote-pipe/$SLUG.active"
    ok "máy giữ code giờ trỏ tới máy này: $(id -un)@$(hostname -s) (cổng $CTL)"
    start_tunnel_bg
    sleep 2
    ok "ống đã lên (cổng $CTL). Kiểm ở máy giữ code: bash scripts/remote/doctor.sh"
    ;;
  *) die "dùng: pipe.sh [--install]" ;;
esac
