#!/usr/bin/env bash
# Gateway CỤC BỘ của dogfood_hub: một làn = một tiến trình @playwright/mcp --isolated trên CHÍNH máy
# chạy hub, nói chuyện qua stdio. Hub gọi: bash scripts/dogfood_hub/gateway_local.sh <RỘNGxCAO> <nhãn>
#
# Máy chạy ứng dụng ở chỗ khác (ống ssh, máy Linux riêng)? Viết gateway của mình theo cùng hợp đồng
# (hai đối số, stdio là kênh MCP, mọi lời cho người đọc ra stderr) rồi trỏ DF_GATEWAY vào nó.
#
# stdout LÀ kênh JSON-RPC — script này không được in gì ra stdout.
set -euo pipefail
VP="${1:-1280x800}"
LABEL="${2:-}"
if ! [[ "$VP" =~ ^[0-9]+x[0-9]+$ ]]; then
  echo "✗ viewport sai dạng: '$VP' — phải là <RỘNG>x<CAO>, ví dụ 1280x800." >&2
  echo "  Dạng dấu phẩy (1280,800) KHÔNG báo lỗi mà cho cửa sổ 0×0 — mọi click quá hạn." >&2
  exit 2
fi
OUTDIR=()
if [ -n "$LABEL" ]; then
  if ! [[ "$LABEL" =~ ^[A-Za-z0-9_-]+$ ]]; then echo "✗ nhãn làn sai dạng: '$LABEL'" >&2; exit 2; fi
  mkdir -p "/tmp/viper-df/$LABEL"
  OUTDIR=(--output-dir "/tmp/viper-df/$LABEL")
fi
HEADLESS=()
# DF_HEADLESS=1 ép headless; =0 ép có cửa sổ. Bỏ trống: Linux không có phiên đồ hoạ → headless.
if [ "${DF_HEADLESS:-}" = "1" ]; then
  HEADLESS=(--headless)
elif [ -z "${DF_HEADLESS:-}" ] && [ "$(uname)" = "Linux" ] && [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
  HEADLESS=(--headless)
fi
command -v npm >/dev/null 2>&1 || { echo "✗ không tìm thấy npm — cài Node.js rồi thử lại" >&2; exit 127; }
echo "▸ làn ${LABEL:-(không nhãn)} · viewport $VP${HEADLESS:+ · headless} · $(hostname)" >&2
# `npm exec` thay `npx`: trên vài máy npx trong PATH là gói standalone đời cũ không hiểu -y.
# --browser chromium: bản Chromium của Playwright (~/.cache/ms-playwright) có sẵn trên mọi máy đã
# chạy Playwright; mặc định của @playwright/mcp là kênh `chrome` — máy không cài Google Chrome thì
# mọi lượt gọi chết với «Chromium distribution 'chrome' is not found». Đổi bằng DF_BROWSER.
exec npm exec -y -- "@playwright/mcp@${DF_PLAYWRIGHT_MCP_VERSION:-0.0.79}" \
  "${HEADLESS[@]}" --isolated --browser "${DF_BROWSER:-chromium}" --viewport-size "$VP" "${OUTDIR[@]}"
