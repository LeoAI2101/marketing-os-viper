#!/usr/bin/env bash
# Nhóm kiểm PT chạy riêng (`make check-pt` / `make test-pt`, hoặc thêm vào cuối `make check PT=1`
# / `make test PT=1`). Không seed/reset, không dựng cụm, không phát tải.
#   pt-gates.sh <check|test> <target make>...
# Danh sách target khai ở Makefile (PT_CHECK_TARGETS / PT_TEST_TARGETS). Mọi target đều chạy dù
# target trước đỏ, để một lượt thấy hết; log ở .viper/pt-gates/. Exit 0 xanh · 1 đỏ ·
# 2 chưa khai target nào — "chưa kiểm được" không bao giờ là xanh.
set -euo pipefail
cd "$(dirname "$0")/../.."
lane="${1:-}"
case "$lane" in
  check|test) shift ;;
  *) echo '✗ Cần nhóm check hoặc test' >&2; exit 2 ;;
esac
if [[ $# == 0 ]]; then
  echo "✗ make $lane-pt: chưa khai target nào ở PT_$(tr a-z A-Z <<<"$lane")_TARGETS (Makefile)." >&2
  echo "  Checker theo skill đã bật (query-safety · data-security · data-lifecycle · connection-budget ·" >&2
  echo "  api-contract) được hiện thực ở pha I rồi khai vào đây — chưa có thì là CHƯA KIỂM, không phải xanh." >&2
  exit 2
fi

# Bật ống remote (.local/remote/.remote-on) thì nhóm PT chạy ở MÁY CHẠY: đồng bộ kèm snapshot mốc
# hợp đồng (máy chạy không có .git), rồi gọi chính script này bên đó với RP_LOCAL=1 (cắt đệ quy).
if [[ -f .local/remote/.remote-on && -z "${RP_LOCAL:-}" ]]; then
  PT=1 bash scripts/remote/sync.sh
  exec bash scripts/remote/ub "RP_LOCAL=1 PT=0 bash scripts/perf/pt-gates.sh $lane $*"
fi

# SSH không tương tác có thể chưa nạp nvm: dùng bản node đã cài, không cài dependency.
if ! command -v node >/dev/null 2>&1; then
  node_bin=$(ls -d "${NVM_DIR:-$HOME/.nvm}"/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
  if [[ -n "$node_bin" ]]; then export PATH="$node_bin:$PATH"; fi
fi

umask 077
mkdir -p .viper/pt-gates
folder=$(mktemp -d ".viper/pt-gates/$lane-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
printf 'target\texit_code\tseconds\n' > "$folder/summary.tsv"
failed=0
start=$SECONDS
for target in "$@"; do
  echo "▸ PT: $target"
  before=$SECONDS
  # Tuần tự, không kế thừa -j/-i/-k của make ngoài; PT=0 cắt đệ quy check → check-pt → check;
  # ENV/env=PT: checker theo môi trường (connection-budget) đo đúng cụm PT, không phải SIT mặc định.
  if MAKEFLAGS= MFLAGS= make --no-print-directory -j1 RP_LOCAL=1 PT=0 ENV=PT env=PT "$target" 2>&1 | tee "$folder/$target.log"; then
    rc=0
  else
    rc=${PIPESTATUS[0]}
    failed=1
  fi
  printf '%s\t%s\t%s\n' "$target" "$rc" "$((SECONDS-before))" >> "$folder/summary.tsv"
done
cat "$folder/summary.tsv"
if [[ $failed == 0 ]]; then
  echo "✅ make $lane-pt XONG — $((SECONDS-start))s · $(hostname) · $folder" >&2
else
  echo "❌ make $lane-pt ĐỎ — $((SECONDS-start))s · $(hostname) · $folder" >&2
fi
exit "$failed"
