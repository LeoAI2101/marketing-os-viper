#!/usr/bin/env bash
# run.sh — sync rồi chạy một lệnh hợp đồng ở MÁY CHẠY.  Makefile gọi cái này khi bật ống.
#
#   bash scripts/remote/run.sh <dev|check|test|migrate|doctor>
#
# Lớp remote BỌC 6 lệnh hợp đồng sẵn có, không thêm lệnh mới cho repo (gate.py chỉ biết
# 6 lệnh đó). `deploy` KHÔNG đi qua ống: nó chạy ở máy giữ code, nơi hook guard_bc đứng.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/remote/_map.sh

TARGET="${1:?dùng: run.sh <dev|check|test|migrate|doctor>}"

TMUX_SOCK="$RP_SLUG"                 # socket riêng — xem bootstrap
TMUX_DEV="$RP_SLUG-dev"
LOG="/tmp/dev-$RP_SLUG.log"

# RP_LOCAL=1 CẮT ĐỆ QUY: phía máy chạy thấy cờ này thì chạy thân local, không gọi ngược ống.
# PT đi theo (nhóm kiểm PT tách riêng — chỉ chạy khi người gọi bật).
case "${PT:-0}" in 0|1) ;; *) echo '✗ PT chỉ nhận 0 hoặc 1' >&2; exit 2 ;; esac
REMOTE_ENV="RP_LOCAL=1 PT=${PT:-0}"

# `bash -lc` đọc ~/.profile, KHÔNG đọc ~/.bashrc — mà nvm cài chính nó vào .bashrc. Máy chỉ
# dùng nvm thì mọi lệnh qua ống chết ở `npm: command not found`. Thêm bin node MỚI NHẤT của
# nvm vào PATH (không `source nvm.sh`: nó in ra stdout ở vài cấu hình). Không dùng nháy kép
# bên trong — chuỗi này còn chui qua một lớp `\"…\"` nữa khi vào tmux.
RP_NODE_PATH='PATH=$(ls -d ${NVM_DIR:-$HOME/.nvm}/versions/node/*/bin 2>/dev/null | sort -V | tail -1):$PATH; export PATH;'

# Dòng kết: mọi target KẾT THÚC ĐƯỢC in đúng một dòng ✅/❌ ra stderr rồi thoát đúng mã.
# `dev` không có: nó tail -f log cụm, Ctrl-C mới thoát.
STARTED=$(date +%s)
finish() {
  local rc=$1 secs=$(( $(date +%s) - STARTED ))
  if [ "$rc" = "0" ]; then echo "✅ run.sh $TARGET XONG ở $RP_RUNNER_HOST — ${secs}s · $(date '+%H:%M:%S')" >&2
  else echo "❌ run.sh $TARGET DỪNG ở $RP_RUNNER_HOST (mã $rc) — ${secs}s · $(date '+%H:%M:%S') — đọc dòng ✗ gần nhất phía trên" >&2; fi
  exit "$rc"
}

echo "▸ sync → $RP_RUNNER_HOST" >&2
bash scripts/remote/sync.sh

# Bootstrap idempotent: kiểm bằng SỰ TỒN TẠI của node_modules, không bằng file cờ (file cờ
# nói dối khi npm ci chết giữa chừng). node_modules không đi qua ống — máy chạy tự cài.
# Stack khác (uv, go, gradle) tự lo deps trong thân lệnh make của nó.
bootstrap() {
  "${RP_SSH[@]}" "$RP_TARGET" "bash -lc '
    set -e
    $RP_NODE_PATH
    cd \"$RP_REPO_RUNNER\"
    for d in ./ srcroot/boundaries/*/ srcroot/web-experiences/*/; do
      [ -f \"\$d/package-lock.json\" ] || continue
      [ -d \"\$d/node_modules\" ] && continue
      echo \"▸ npm ci \$d\" >&2
      (cd \"\$d\" && npm ci)
    done
  '"
}

# Lệnh chạy ở máy chạy, trong thư mục repo, với PATH node + cờ cắt đệ quy.
remote_make() {
  "${RP_SSH[@]}" "$RP_TARGET" "bash -lc '
    cd \"$RP_REPO_RUNNER\"
    echo \"▸ chạy ở: \$(hostname)\" >&2
    $RP_NODE_PATH $REMOTE_ENV make $*
  '"
}

case "$TARGET" in
  dev)
    bootstrap
    # Lệnh chạy dài → tmux ở máy chạy (sống qua lúc rớt mạng), rồi tail log về. tmux -L
    # socket RIÊNG: tmux server mặc định có thể khởi động TRƯỚC lúc user vào nhóm docker,
    # và mọi lệnh docker trong đó báo permission denied dù `ub 'docker ps'` chạy tốt.
    "${RP_SSH[@]}" "$RP_TARGET" "bash -lc '
      cd \"$RP_REPO_RUNNER\"
      tmux -L $TMUX_SOCK kill-session -t $TMUX_DEV 2>/dev/null || true
      tmux -L $TMUX_SOCK new-session -d -s $TMUX_DEV \
        \"$RP_NODE_PATH $REMOTE_ENV make dev 2>&1 | tee $LOG\"
      echo \"▸ cụm dev chạy trong tmux -L $TMUX_SOCK -t $TMUX_DEV ở \$(hostname)\" >&2
    '"
    echo "▸ tail log (Ctrl-C thoát tail, cụm VẪN CHẠY — dừng: scripts/remote/ub 'tmux -L $TMUX_SOCK kill-session -t $TMUX_DEV')" >&2
    exec "${RP_SSH[@]}" "$RP_TARGET" "tail -f $LOG"
    ;;

  check)
    bootstrap
    remote_make check || finish $?
    # Hợp đồng/migration sinh Ở MÁY CHẠY nhưng git nằm Ở ĐÂY — kéo về rồi mới so được.
    # Thiếu bước này thì phép so lệch luôn xanh: một phép kiểm KHÔNG BAO GIỜ ĐỎ ĐƯỢC.
    echo "▸ kéo artifact sinh máy về máy giữ code" >&2
    bash scripts/remote/pull-generated.sh || finish $?
    # `git status --porcelain`, KHÔNG `git diff`: artifact vừa sinh lần đầu (chưa git add)
    # thì git diff bỏ qua. if/fi chứ không `&&`: dưới set -e, AND-list trượt giết cả script.
    shopt -s nullglob
    GEN_PATHS=(srcroot/contracts srcroot/boundaries/*/src/shared/contracts \
               srcroot/boundaries/*/prisma/migrations srcroot/web-experiences/*/src/shared/generated)
    shopt -u nullglob
    if [ "${#GEN_PATHS[@]}" -gt 0 ] && [ -n "$(git status --porcelain -- "${GEN_PATHS[@]}" 2>/dev/null)" ]; then
      echo "✗ Artifact sinh máy LỆCH so với git:" >&2
      git status --short -- "${GEN_PATHS[@]}" >&2
      echo "  Đổi boundary/BFF mà bản đã commit chưa cập nhật — git add rồi commit CÙNG thay đổi gây ra nó." >&2
      finish 1
    fi
    finish 0
    ;;

  test|migrate|doctor)
    bootstrap
    remote_make "$TARGET" || finish $?
    finish 0
    ;;

  *)
    echo "✗ run.sh không biết target '$TARGET' — chỉ bọc dev/check/test/migrate/doctor." >&2
    echo "  Lệnh khác chạy thẳng: scripts/remote/ub 'RP_LOCAL=1 make <target>'" >&2
    exit 2
    ;;
esac
