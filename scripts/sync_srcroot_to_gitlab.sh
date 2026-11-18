#!/usr/bin/env bash
#
# Đồng bộ MỘT CHIỀU srcroot/ → các repo GitLab con, để devops dựng Jenkins pipeline.
#
#   srcroot/<nhóm>/<tên>  →  <GROUP>/<đường dẫn tra ở deployment/gitlab-repo-map.tsv>
#
# Đường dẫn GitLab KHÔNG suy được từ tên thư mục srcroot (nhóm con viết hoa; web và
# mobile gộp chung nhóm EXPERIENCES; bốn BFF có thêm tiền tố `d-`), nên có bảng thật.
# Target vẫn dò từ đĩa — bảng chỉ trả lời "đẩy đi đâu". Target không có dòng trong bảng
# thì BỊ BỎ QUA, không đoán: đoán sai là đẩy ảnh chụp đè lên nhầm repo.
#
# NGUYÊN TẮC (theo yêu cầu Authority):
#   · KHÔNG mang lịch sử của monorepo này sang. Mỗi lượt đẩy là MỘT commit "ảnh chụp"
#     nội dung hiện tại, CHỒNG một nấc lên nhánh đích (fast-forward, KHÔNG force —
#     nhánh `main` của repo con được GitLab bảo vệ, force bị chặn cứng). Repo bên kia
#     là BẢN SAO: sửa tay ở đó sẽ bị ảnh chụp lượt sau ghi đè.
#   · Chỉ đẩy file ĐANG ĐƯỢC GIT THEO DÕI ở monorepo này. Nhờ vậy .env, .venv,
#     node_modules, __pycache__… tự động không lọt sang (đã bị .gitignore chặn).
#   · Mặc định CHẠY THỬ. Muốn đẩy thật phải thêm --push.
#
# DANH SÁCH TARGET — DÒ TỪ ĐĨA, không gõ tay: mọi thư mục `srcroot/<nhóm>/<tên>/` có
#   package.json hoặc pubspec.yaml. Thư mục `{{...-NAME}}` của template bị loại. Repo mọc
#   thêm boundary theo từng vòng, danh sách chép cứng là danh sách sẽ lệch.
#
# HỢP ĐỒNG SINH MÁY (SYNC_CONTRACTS=1, mặc định bật) — HAI loại, HAI bố cục
#   a) Hợp đồng target PHÁT HÀNH: `srcroot/contracts/<target>/` → `contracts/` (PHẲNG).
#      Giữ nguyên bố cục cũ vì repo con boundary đã có nó, pipeline có thể đang đọc.
#      `npm test` của ba BFF GraphQL (src/schema.spec.ts) so SDL dựng từ code với bản đã
#      commit: ở monorepo đọc `srcroot/contracts/<target>/`, ở repo con đọc `contracts/`
#      chép nhờ (a). Đặt SYNC_CONTRACTS=0 thì test đó ĐỎ ở repo con (đo 2026-09-04 trên
#      SIT: d-bff-ops-console và d-bff-driverplus-mobile chết vì ENOENT khi test chỉ biết
#      đường monorepo).
#   b) Hợp đồng target TIÊU THỤ: `srcroot/contracts/<bff>/` → `contracts/<bff>/` (LỒNG,
#      gương đúng srcroot/contracts/). Danh sách DÒ TỪ CODE, không gõ tay — quét tham
#      chiếu `contracts/<tên>/` trong file đang được theo dõi của target.
#
#   Nhờ (b), repo con chạy được codegen: code đọc gốc hợp đồng qua một biến dò tự động
#   (`./contracts` nếu có, ngược lại `../../contracts`; ép bằng CONTRACTS_DIR) — xem
#   srcroot/web-experiences/ops-console-web/codegen.ts.
#
#   CHƯA XONG ở boundary và app Flutter: `make check` của chúng gọi `make contract`, mà
#   scripts/emit-contract.mjs (11 target) và Makefile của app vẫn chép cứng đường ra ngoài
#   repo. Bù lại, file sinh ra ĐÃ ĐƯỢC COMMIT trong từng target (src/shared/contracts/*.d.ts,
#   src/shared/generated/graphql/*, lib/shared/graphql/*) nên repo con của chúng vẫn chạy được
#   `npm ci && npm run build && npm test` — pipeline devops gọi ba lệnh đó, KHÔNG gọi
#   `make check`, cho tới khi áp cùng cách dò gốc như codegen.ts.
#
# DÙNG:
#   scripts/sync_srcroot_to_gitlab.sh                      # chạy thử toàn bộ
#   scripts/sync_srcroot_to_gitlab.sh d-orders d-operations   # chạy thử vài target
#   scripts/sync_srcroot_to_gitlab.sh --push ops-console-web   # đẩy thật MỘT target
#   scripts/sync_srcroot_to_gitlab.sh --push --all         # đẩy thật toàn bộ — phải nói ra
#   scripts/sync_srcroot_to_gitlab.sh --list               # chỉ in bảng target rồi thoát
#
# CẤU HÌNH: .local/env/.env.gitlab-sync (mẫu: .local/templates/.env.gitlab-sync.example).
#   File đó khớp mẫu `.env.*` nên .gitignore CHẶN SẴN — không có đường nào commit nhầm.
#   Biến trong môi trường shell THẮNG giá trị trong file (đổi tài khoản một lượt chạy).
#
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$ROOT"

command -v git >/dev/null || { echo "✗ không có git" >&2; exit 1; }

# ── Cờ dòng lệnh ─────────────────────────────────────────────────────────────
DO_PUSH=0
DO_LIST=0
DO_ALL=0
ONLY=()
for arg in "$@"; do
  case "$arg" in
    --push) DO_PUSH=1 ;;
    --list) DO_LIST=1 ;;
    --all)  DO_ALL=1 ;;
    # In trọn khối chú thích đầu file, dừng ở dòng code đầu tiên — không chép cứng số
    # dòng, vì sửa phần mô tả là số dòng lệch ngay và help cụt giữa câu.
    -h|--help) awk 'NR>1 && !/^#/ { exit } NR>1' "$0"; exit 0 ;;
    -*) echo "✗ cờ lạ: $arg" >&2; exit 2 ;;
    *) ONLY+=("$arg") ;;
  esac
done

# Đẩy thật mà không nêu tên target = ghi đè MỌI repo con trong bảng ánh xạ, mỗi cái một
# commit thay toàn bộ nội dung. Trước khi có bảng, lệnh này hỏng vô hại vì đường dẫn suy
# ra không tồn tại; giờ nó trúng repo đang sống của đội khác. Nên đòi nói ra ý định.
# Chạy thử (không --push) thì vẫn quét tất, vì nó không ghi gì.
if [ $DO_PUSH -eq 1 ] && [ ${#ONLY[@]} -eq 0 ] && [ $DO_ALL -eq 0 ]; then
  echo "✗ --push không kèm tên target sẽ ghi đè TOÀN BỘ repo con đang sống." >&2
  echo "  Một target : $(basename "$0") --push ops-console-web" >&2
  echo "  Cố ý tất cả: $(basename "$0") --push --all" >&2
  echo "  Xem trước  : $(basename "$0")            (chạy thử, không đẩy gì)" >&2
  exit 2
fi

# ── Nạp cấu hình ─────────────────────────────────────────────────────────────
# Nạp vào biến TẠM rồi mới gán, để biến sẵn có trong shell vẫn thắng file.
CRED_FILE="$ROOT/.local/env/.env.gitlab-sync"
if [ -f "$CRED_FILE" ]; then
  if git ls-files --error-unmatch "$CRED_FILE" >/dev/null 2>&1; then
    echo "✗ .local/env/.env.gitlab-sync ĐANG ĐƯỢC GIT THEO DÕI — token trong đó coi như đã lộ." >&2
    echo "  Gỡ khỏi git: git rm --cached .local/env/.env.gitlab-sync" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$CRED_FILE"
  set +a
fi

# Không có mặc định: host/nhóm là của tổ chức — khai ở .local/env/.env.gitlab-sync.
: "${GITLAB_HOST:?chưa khai GITLAB_HOST ở .local/env/.env.gitlab-sync}"
: "${GITLAB_GROUP:?chưa khai GITLAB_GROUP ở .local/env/.env.gitlab-sync}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"
SYNC_CONTRACTS="${SYNC_CONTRACTS:-1}"

# ── Bảng ánh xạ đích ─────────────────────────────────────────────────────────
# Target vẫn DÒ TỪ ĐĨA (bên dưới); bảng này chỉ trả lời "target đó đẩy đi đâu".
# Đường dẫn GitLab không suy được từ tên thư mục srcroot — nhóm con viết hoa, web và
# mobile gộp một nhóm, bốn BFF có thêm tiền tố `d-`. Xem deployment/gitlab-repo-map.tsv.
MAP_FILE="${SYNC_MAP_FILE:-$ROOT/deployment/gitlab-repo-map.tsv}"

# $1=nhóm srcroot · $2=tên target → in đường dẫn con; rỗng nếu không có dòng nào.
map_lookup() {
  [ -f "$MAP_FILE" ] || return 0
  awk -v k="$1/$2" '
    { sub(/\r$/, "") }
    /^[[:space:]]*(#|$)/ { next }
    $1 == k { print $2; exit }
  ' "$MAP_FILE"
}

# GitLab bật 2FA thì MẬT KHẨU TÀI KHOẢN không đẩy được qua HTTPS — phải dùng Personal
# Access Token (scope `write_repository`) hoặc Deploy Token. Không có token → đi SSH
# bằng key sẵn có của máy.
GITLAB_USER="${GITLAB_USER:-}"
GITLAB_TOKEN="${GITLAB_TOKEN:-}"
GITLAB_PROTO="${GITLAB_PROTO:-}"
if [ -z "$GITLAB_PROTO" ]; then
  if [ -n "$GITLAB_TOKEN" ]; then GITLAB_PROTO=https; else GITLAB_PROTO=ssh; fi
fi

# ── Bảng target: dò từ đĩa ───────────────────────────────────────────────────
TARGETS=""
for group in boundaries web-experiences mobile-experiences; do
  [ -d "srcroot/$group" ] || continue
  for d in srcroot/"$group"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    case "$name" in *'{{'*) continue;; esac              # chỗ giữ nhịp của template
    if [ -f "$d/package.json" ] || [ -f "$d/pubspec.yaml" ]; then
      TARGETS="$TARGETS$group|$name"$'\n'
    fi
  done
done
[ -n "$TARGETS" ] || { echo "✗ không thấy target nào trong srcroot/ — chưa scaffold?" >&2; exit 1; }

if [ $DO_LIST -eq 1 ]; then
  echo "Đích: $GITLAB_GROUP  (proto: $GITLAB_PROTO · nhánh: $SYNC_BRANCH)"
  echo "Bảng : $([ -f "$MAP_FILE" ] && echo "${MAP_FILE#"$ROOT/"}" || echo 'không có — suy đường dẫn từ tên thư mục')"
  echo
  while IFS='|' read -r group name; do
    [ -z "${group:-}" ] && continue
    sub="$(map_lookup "$group" "$name")"
    if [ -z "$sub" ] && [ -f "$MAP_FILE" ]; then
      echo "  srcroot/$group/$name  ⊘  chưa có dòng ánh xạ — sẽ bị bỏ qua"
    else
      echo "  srcroot/$group/$name  →  $GITLAB_GROUP/${sub:-$group/$name}"
    fi
  done <<< "$TARGETS"
  exit 0
fi

mkdir -p "$ROOT/.local/tmp"
STAGE="$(mktemp -d "$ROOT/.local/tmp/sync-stage.XXXXXX")"
CRED_STORE="$(mktemp "$ROOT/.local/tmp/sync-credential.XXXXXX")"
chmod 600 "$CRED_STORE"
trap 'rm -rf "$STAGE" "$CRED_STORE"' EXIT

# Token đi qua FILE credential của git, không nhét vào URL remote và không truyền
# qua dòng lệnh — nếu nhét vào URL thì nó lộ ra `ps`, ra `git remote -v`, và ra
# nguyên văn trong thông báo lỗi khi push hỏng.
GIT_AUTH=()
if [ "$GITLAB_PROTO" = "https" ]; then
  if [ -z "$GITLAB_USER" ] || [ -z "$GITLAB_TOKEN" ]; then
    echo "✗ HTTPS cần cả GITLAB_USER và GITLAB_TOKEN (khai ở .local/env/.env.gitlab-sync)" >&2
    exit 1
  fi
  enc() { python3 -c 'import sys,urllib.parse as u;print(u.quote(sys.argv[1],safe=""))' "$1"; }
  printf 'https://%s:%s@%s\n' "$(enc "$GITLAB_USER")" "$(enc "$GITLAB_TOKEN")" "$GITLAB_HOST" > "$CRED_STORE"
  GIT_AUTH=(-c "credential.helper=store --file=$CRED_STORE")
fi

# Nội dung .gitignore sinh kèm cho repo con (repo con không thừa hưởng .gitignore gốc).
read -r -d '' GEN_GITIGNORE <<'IGN' || true
# Sinh tự động bởi scripts/sync_srcroot_to_gitlab.sh — đừng sửa tay ở repo này.
.env
.env.*
*.pem
*.key
__pycache__/
*.pyc
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.scannerwork/
coverage/
node_modules/
dist/
build/
*.tsbuildinfo
.dart_tool/
.flutter-plugins-dependencies
.DS_Store
IGN

# Hợp đồng mà target TIÊU THỤ (khác với hợp đồng nó phát hành).
# Dò từ chính code — quét tham chiếu `contracts/<tên>/` trong file đang được git theo dõi
# — chứ không gõ tay: ops-console-web đọc SDL của hai BFF (codegen.ts), app Flutter đọc
# của hai BFF khác (Makefile), và danh sách đó đổi theo vòng. Chép cứng là sẽ lệch.
# In mỗi dòng một tên, đã loại tên của chính target và tên không có thư mục thật.
consumed_contracts() {
  local src="$1" self="$2"
  # Bám vào TÊN FILE hợp đồng (`<tên>/schema.graphql`, `<tên>/openapi.json`) chứ không
  # vào tiền tố `contracts/`: code đọc gốc thư mục qua biến (xem codegen.ts) nên chuỗi
  # `contracts/` không còn chắc chắn xuất hiện, mà tên file thì luôn còn.
  git ls-files -z -- "$src" \
    | xargs -0 grep -hoIE '[a-z0-9][a-z0-9-]*/(schema\.graphql|openapi\.json)' 2>/dev/null \
    | sed -E 's#/.*$##' \
    | sort -u \
    | while read -r n; do
        [ -n "$n" ] || continue
        [ "$n" = "$self" ] && continue
        [ -d "srcroot/contracts/$n" ] || continue
        echo "$n"
      done
}

want() {
  [ ${#ONLY[@]} -eq 0 ] && return 0
  local n="$1"; local x
  for x in "${ONLY[@]}"; do [ "$x" = "$n" ] && return 0; done
  return 1
}

remote_url() {
  local sub; sub="$(map_lookup "$1" "$2")"
  local path="$GITLAB_GROUP/${sub:-$1/$2}"
  if [ "$GITLAB_PROTO" = "ssh" ]; then
    echo "git@${GITLAB_HOST}:${path}.git"
  else
    echo "https://${GITLAB_HOST}/${path}.git"
  fi
}

# ── Quét bí mật trước khi đẩy ────────────────────────────────────────────────
# Chặn cứng: private key, AWS access key, GitLab PAT. Cảnh báo: chuỗi giống mật khẩu.
scan_secrets() {
  local dir="$1" name="$2" hard=0
  local hits
  hits=$(grep -rIlE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|glpat-[A-Za-z0-9_-]{20}' "$dir" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "  ✗ CHẶN — nghi có bí mật thật trong $name:"
    echo "$hits" | sed "s|$dir/|      |"
    hard=1
  fi
  local warn
  warn=$(grep -rIlE -- '(password|passwd|secret|api[_-]?key|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{8,}' "$dir" 2>/dev/null || true)
  if [ -n "$warn" ]; then
    echo "  ⚠ có chuỗi giống mật khẩu/token (kiểm mắt trước khi đẩy thật):"
    echo "$warn" | sed "s|$dir/|      |" | head -10
  fi
  return $hard
}

echo "Nguồn      : $ROOT/srcroot"
echo "Đích       : $GITLAB_GROUP  (proto: $GITLAB_PROTO · nhánh: $SYNC_BRANCH)"
echo "Hợp đồng   : $([ "$SYNC_CONTRACTS" = "1" ] && echo 'chép srcroot/contracts/<target>/ → contracts/' || echo 'không chép')"
echo "Chế độ     : $([ $DO_PUSH -eq 1 ] && echo 'ĐẨY THẬT (commit chồng một nấc, không force)' || echo 'chạy thử — không đẩy gì')"
echo

BLOCKED=0
COUNT=0
SKIPPED=0

while IFS='|' read -r group name; do
  [ -z "${group:-}" ] && continue
  want "$name" || continue
  COUNT=$((COUNT+1))

  # Không có dòng ánh xạ thì BỎ QUA, không đoán: đoán sai nghĩa là đẩy ảnh chụp
  # (thay toàn bộ nội dung) vào nhầm repo của người khác.
  if [ -f "$MAP_FILE" ] && [ -z "$(map_lookup "$group" "$name")" ]; then
    echo "── $name"
    echo "  ⊘ chưa có dòng ánh xạ trong ${MAP_FILE#"$ROOT/"} — bỏ qua"
    echo
    SKIPPED=$((SKIPPED+1))
    continue
  fi

  src="srcroot/$group/$name"
  [ -d "$src" ] || { echo "✗ không có thư mục $src"; BLOCKED=1; continue; }

  dest="$STAGE/$name"
  mkdir -p "$dest"

  # Chỉ lấy file git đang theo dõi → artifact build và secret không lọt sang.
  n=0
  while IFS= read -r -d '' f; do
    rel="${f#"$src"/}"
    mkdir -p "$dest/$(dirname "$rel")"
    cp -p "$f" "$dest/$rel"
    n=$((n+1))
  done < <(git ls-files -z -- "$src")

  # Hợp đồng của CHÍNH target này (nếu có). Cũng chỉ lấy file đang được theo dõi.
  # Bố cục PHẲNG (`contracts/openapi.json`) — giữ nguyên như trước, vì repo con của các
  # boundary đã có nó và pipeline devops có thể đang đọc đúng đường ấy.
  c=0
  if [ "$SYNC_CONTRACTS" = "1" ] && [ -d "srcroot/contracts/$name" ]; then
    while IFS= read -r -d '' f; do
      rel="${f#srcroot/contracts/$name/}"
      mkdir -p "$dest/contracts/$(dirname "$rel")"
      cp -p "$f" "$dest/contracts/$rel"
      c=$((c+1))
      n=$((n+1))
    done < <(git ls-files -z -- "srcroot/contracts/$name")
  fi

  # Hợp đồng target TIÊU THỤ, bố cục LỒNG `contracts/<tên>/…` — giống hệt srcroot/contracts/
  # nên code chỉ cần đổi gốc thư mục là chạy được cả hai nơi (xem codegen.ts).
  # Thiếu chúng thì repo con không chạy nổi codegen: đúng lỗi ops-console-web ngày 2026-09-04.
  deps=""
  if [ "$SYNC_CONTRACTS" = "1" ]; then
    while read -r dep; do
      [ -n "$dep" ] || continue
      while IFS= read -r -d '' f; do
        rel="${f#srcroot/contracts/$dep/}"
        mkdir -p "$dest/contracts/$dep/$(dirname "$rel")"
        cp -p "$f" "$dest/contracts/$dep/$rel"
        c=$((c+1))
        n=$((n+1))
      done < <(git ls-files -z -- "srcroot/contracts/$dep")
      deps="$deps $dep"
    done < <(consumed_contracts "$src" "$name")
  fi

  [ -f "$dest/.gitignore" ] || printf '%s\n' "$GEN_GITIGNORE" > "$dest/.gitignore"

  url="$(remote_url "$group" "$name")"
  size=$(du -sh "$dest" 2>/dev/null | cut -f1)
  echo "── $name"
  echo "  $src → $url"
  echo "  $n file · $size"
  [ "$c" -gt 0 ] && echo "  · kèm $c file hợp đồng → contracts/"
  [ -n "$deps" ] && echo "  · hợp đồng tiêu thụ (dò từ code):$deps"

  # File chưa được theo dõi thì sẽ KHÔNG sang — nói rõ để không mất im lặng.
  untracked=$(git ls-files --others --exclude-standard -- "$src" | head -5)
  if [ -n "$untracked" ]; then
    echo "  ⚠ bỏ qua file chưa git add (sẽ KHÔNG sang repo mới):"
    echo "$untracked" | sed 's|^|      |'
  fi

  if ! scan_secrets "$dest" "$name"; then
    BLOCKED=1
    continue
  fi

  if [ $DO_PUSH -eq 1 ]; then
    # Commit CHỒNG LÊN lịch sử remote, KHÔNG dựng lịch sử mới rồi force-push.
    #
    # Bản đầu `git init` mỗi lượt → lịch sử không liên quan gì remote → buộc phải
    # `--force`, và GitLab chặn: "not allowed to force push to a protected branch".
    # Nhánh `main` của repo con được bảo vệ (đúng, đó là nhánh devops build từ đó).
    # Nên: lấy lịch sử remote về, thay TOÀN BỘ nội dung bằng ảnh chụp mới, commit
    # tiếp một nấc → fast-forward, push thường, không đụng gì tới cấu hình GitLab.
    # Thêm lợi: repo con có lịch sử đồng bộ thật, xem được lần trước đổi gì.
    (
      cd "$dest"
      git init -q -b "$SYNC_BRANCH"
      git remote add origin "$url"

      # Lấy nhánh đích nếu remote đã có; repo rỗng (lần đầu) thì bỏ qua, thành commit gốc.
      if git "${GIT_AUTH[@]+"${GIT_AUTH[@]}"}" fetch -q --depth 1 origin "$SYNC_BRANCH" 2>/dev/null; then
        # Đặt con trỏ nhánh vào commit remote nhưng GIỮ NGUYÊN file đang có ở đây:
        # `reset --soft` không đụng thư mục làm việc, nên ảnh chụp vừa dựng ở trên
        # thành "thay đổi so với remote" — kể cả file bị xoá cũng được ghi nhận.
        git reset -q --soft FETCH_HEAD
      fi

      git add -A
      # Không có gì đổi thì đừng đẻ commit rỗng — chạy lại script hai lần liên tiếp
      # không nên sinh ra hai nấc lịch sử giống hệt nhau.
      if git diff --cached --quiet; then
        echo "  = không đổi so với remote, bỏ qua"
        exit 0
      fi
      git -c user.name="srcroot-sync" -c user.email="noreply@local" \
          commit -q -m "sync từ $(basename "$ROOT")/srcroot/$group/$name"
      git "${GIT_AUTH[@]+"${GIT_AUTH[@]}"}" push -q origin "$SYNC_BRANCH"
      echo "  ✔ đã đẩy"
    )
  fi
  echo
done <<< "$TARGETS"

if [ "$COUNT" -eq 0 ]; then
  echo "✗ không target nào khớp: ${ONLY[*]:-}" >&2
  exit 2
fi

if [ "$SKIPPED" -gt 0 ]; then
  echo "⊘ bỏ qua $SKIPPED target chưa có dòng ánh xạ (thêm vào ${MAP_FILE#"$ROOT/"} nếu repo đã tồn tại)."
fi

if [ $BLOCKED -ne 0 ]; then
  echo "✗ có target bị chặn — sửa rồi chạy lại." >&2
  exit 1
fi

if [ $DO_PUSH -eq 0 ]; then
  echo "Chạy thử xong. Thêm --push để đẩy thật (commit chồng một nấc lên nhánh đích)."
fi
