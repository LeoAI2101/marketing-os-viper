#!/usr/bin/env bash
# pull-generated.sh — kéo NGƯỢC một danh sách HẸP artifact SINH MÁY từ máy chạy về đây.
#
# Không phải sync ngược: allowlist cố định ngay dưới, chỉ chạy khi máy giữ code chủ
# động gọi (run.sh check), chỉ đụng thứ do máy sinh — không file nguồn nào.
# Vì sao cần: hợp đồng (openapi.json, schema.graphql), client sinh máy và migration SQL
# sinh ở máy chạy (cần node_modules) nhưng PHẢI nằm trong git ở đây — từ vòng 2 chúng là
# sổ hợp đồng của BACKWARD-COMPATIBILITY-CHECKLIST §1.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/remote/_map.sh

# Mẫu glob, nở Ở MÁY CHẠY (thư mục có thể chỉ tồn tại ở đó). Thêm mẫu thì thêm ở đây.
PATTERNS=(
  'srcroot/contracts'
  'srcroot/boundaries/*/src/shared/contracts'
  'srcroot/boundaries/*/prisma/migrations'
  'srcroot/web-experiences/*/src/shared/generated'
)

list="$("${RP_SSH[@]}" "$RP_TARGET" "cd '$RP_REPO_RUNNER' && for p in ${PATTERNS[*]}; do [ -d \"\$p\" ] && printf '%s\n' \"\$p\"; done; true")"
count=0
while IFS= read -r p; do
  [ -n "$p" ] || continue
  # tar qua stdout của ssh: một luồng, không cần cổng thứ hai, không cần rsync ngược.
  if "${RP_SSH[@]}" "$RP_TARGET" "cd '$RP_REPO_RUNNER' && tar cf - '$p'" | tar xf -; then
    echo "  ↩ kéo về: $p"; count=$((count + 1))
  else
    echo "  ▲ kéo $p trượt" >&2
  fi
done <<< "$list"
drift="$(git status --porcelain -- srcroot 2>/dev/null | grep -cE 'contracts|migrations|generated' || true)"
echo "✅ pull-generated XONG — $count thư mục, $drift file sinh máy lệch so với git" >&2
