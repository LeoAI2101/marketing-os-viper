#!/usr/bin/env bash
#
# sonar_scan.sh — đẩy một (hoặc mọi) target lên SonarQube.
#
# VÌ SAO CÓ FILE NÀY
#   Tool ops đứng NGOÀI luồng VIPER. `make check` mới là hàng rào chặn cứng; Sonar là lớp
#   ĐỐI CHIẾU trên server (`.claude/skills/code-quality-gate`, intake/loops/l4/_PROPOSAL.md
#   — `make check` phải fail đúng những thứ SonarQube sẽ fail). Ngưỡng CHỈ có một nguồn là
#   skill đó; file này không đặt ngưỡng nào, chỉ nộp mã nguồn + báo cáo coverage.
#
# MỘT TARGET = MỘT PROJECT trên Sonar: <SONAR_PROJECT_KEY_PREFIX>-<tên target>.
#   Gộp 12 target vào một project thì quality gate của cả hệ đỏ vì một target, và
#   duplication đo chéo giữa các target — chúng CỐ Ý lặp khung NestJS.
#
# LOẠI TRỪ — thứ MÁY SINH thì không chấm điểm người
#   Target NestJS + ops-console-web: khai ở `<target>/sonar-project.properties` (script này
#   đọc, vì projectBaseDir trỏ vào target). Flutter: danh sách dưới đây, truyền tay.
#   src/shared/contracts/**      client sinh từ openapi.json (emit-client.mjs)
#   src/shared/generated/graphql*/**       hook + type sinh từ SDL (graphql-codegen)
#   lib/shared/graphql/**        model Dart sinh từ SDL
#   *.g.dart · *.graphql.dart    sinh máy phía Flutter
#   contracts/** · prisma/migrations/**
#   Không loại thì duplication và cognitive complexity đo trên mã không ai viết, và
#   quality gate đỏ vì một thứ sửa tay là sai (srcroot/contracts/README.md).
#
# MỘT CHẾ ĐỘ — quét theo cấu hình TRONG REPO. Từng có `--pipeline` mô phỏng jenkins-central
#   bằng bản chép file trung tâm của DevOps (2026-09-04, cùng ngày bỏ): file ấy là của
#   đội khác, chép về là một bản lệch âm thầm, và nó bắt mã sinh máy phải nằm đúng đường
#   họ loại. Thay vào đó: target nào cần nhìn riêng thì có project + token riêng trên
#   server (SONAR_PROJECT_KEY_MAP + SONAR_TOKEN_MAP), quét bằng đúng cấu hình của mình.
#
# DÙNG:
#   scripts/sonar_scan.sh --list          # in project key sẽ dùng, không chạm mạng
#   scripts/sonar_scan.sh                 # quét mọi target
#   scripts/sonar_scan.sh d-orders ops-console-web
#   scripts/sonar_scan.sh --coverage d-orders   # chạy `make test` lấy lcov trước khi quét
#
# CẤU HÌNH: .local/env/.env.sonar (mẫu: .local/templates/.env.sonar.example) — .gitignore chặn sẵn.
#   SONAR_PROJECT_KEY_MAP  "tên=key …"    project key riêng cho target lệch quy tắc prefix
#   SONAR_TOKEN_MAP        "tên=token …"  project token riêng cho target (thắng SONAR_TOKEN);
#                                         có token riêng = project đã tạo sẵn trên server,
#                                         script KHÔNG gửi sonar.projectName để khỏi đổi tên nó
#
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$ROOT"

DO_LIST=0
WITH_COVERAGE=0
ONLY=()
for arg in "$@"; do
  case "$arg" in
    --list) DO_LIST=1 ;;
    --coverage) WITH_COVERAGE=1 ;;
    # In trọn khối chú thích đầu file, dừng ở dòng code đầu tiên — không chép cứng số dòng.
    -h|--help) awk 'NR>1 && !/^#/ { exit } NR>1' "$0"; exit 0 ;;
    -*) echo "✗ cờ lạ: $arg" >&2; exit 2 ;;
    *) ONLY+=("$arg") ;;
  esac
done

CFG="$ROOT/.local/env/.env.sonar"
if [ -f "$CFG" ]; then
  if git ls-files --error-unmatch "$CFG" >/dev/null 2>&1; then
    echo "✗ .local/env/.env.sonar ĐANG ĐƯỢC GIT THEO DÕI — SONAR_TOKEN trong đó coi như đã lộ." >&2
    exit 1
  fi
  set -a; . "$CFG"; set +a
elif [ $DO_LIST -eq 0 ]; then
  echo "✗ thiếu .local/env/.env.sonar" >&2
  echo "  Tạo: cp .local/templates/.env.sonar.example .local/env/.env.sonar   rồi điền SONAR_HOST_URL + SONAR_TOKEN" >&2
  exit 1
fi

# `${VAR-default}` (không có `:`): KHÔNG khai → mặc định; khai RỖNG → rỗng thật, key = tên target.
# Server dùng chung có thể đã tạo project sẵn với key không tiền tố (đo 2026-09-04).
# Mặc định = tên thư mục repo (không đóng cứng tên dự án nào).
KEY_PREFIX="${SONAR_PROJECT_KEY_PREFIX-$(basename "$ROOT")}"
NAME_PREFIX="${SONAR_PROJECT_NAME_PREFIX-$(basename "$ROOT")}"
# Nhánh CHỈ gửi khi .env.sonar khai TƯỜNG MINH SONAR_BRANCH. Server hiện tại là
# SonarQube **Community Build** — nó TỪ CHỐI cả lượt quét nếu thấy `sonar.branch.name`
# ("Developer Edition or above is required", đo 2026-09-04), nên lấy nhánh git làm mặc
# định là đảm bảo mọi lượt quét chết ngay ở bước validate. Lên Developer Edition thì
# điền SONAR_BRANCH (hoặc `SONAR_BRANCH=$(git rev-parse --abbrev-ref HEAD)`) là đủ.
BRANCH="${SONAR_BRANCH:-}"

# SONAR_PROJECT_KEY_MAP="tên=key tên2=key2" — ghi đè key cho target lệch quy tắc prefix
# (vd server đặt bff-auth-client là d-bff-auth-client).
project_key() {
  local name="$1" pair
  for pair in ${SONAR_PROJECT_KEY_MAP:-}; do
    [ "${pair%%=*}" = "$name" ] && { echo "${pair#*=}"; return; }
  done
  [ -n "$KEY_PREFIX" ] && echo "$KEY_PREFIX-$name" || echo "$name"
}
project_name() {
  [ -n "$NAME_PREFIX" ] && echo "$NAME_PREFIX $1" || echo "$1"
}
# SONAR_TOKEN_MAP="tên=token tên2=token2" — project token (sqp_…) riêng cho target có project
# riêng trên server; rỗng = dùng SONAR_TOKEN chung. Token KHÔNG bao giờ in ra.
project_token() {
  local name="$1" pair
  for pair in ${SONAR_TOKEN_MAP:-}; do
    [ "${pair%%=*}" = "$name" ] && { echo "${pair#*=}"; return 0; }
  done
  # `return 0` BẮT BUỘC: không có nó, lệnh cuối chạy trong hàm là phép `[ … ]` vừa SAI,
  # nên hàm trả mã 1 — và `token="$(project_token …)"` dưới `set -e` giết cả script,
  # IM LẶNG, exit 1 không một dòng chữ. Nghĩa là mọi target KHÔNG có token riêng đều
  # không quét được (đo 2026-09-17: chỉ `ops-console-web` chạy, 15 target còn lại chết).
  # `--list` không lộ ra vì ở đó hàm nằm trong `[ -n "$(…)" ]`, nơi mã trả về bị nuốt.
  return 0
}

want() {
  [ ${#ONLY[@]} -eq 0 ] && return 0
  local x; for x in "${ONLY[@]}"; do [ "$x" = "$1" ] && return 0; done
  return 1
}

TARGETS=""
for group in boundaries web-experiences mobile-experiences; do
  [ -d "srcroot/$group" ] || continue
  for d in srcroot/"$group"/*/; do
    [ -d "$d" ] || continue
    name="$(basename "$d")"
    case "$name" in *'{{'*) continue;; esac
    if [ -f "$d/package.json" ] || [ -f "$d/pubspec.yaml" ]; then
      TARGETS="$TARGETS$group|$name"$'\n'
    fi
  done
done

# Loại trừ dùng chung mọi target (xem khối chú thích đầu file).
EXCLUSIONS='**/node_modules/**,**/dist/**,**/build/**,**/coverage/**,**/*.d.ts,src/shared/contracts/**,src/shared/generated/graphql/**,src/shared/generated/graphql-auth/**,lib/shared/graphql/**,**/*.g.dart,**/*.graphql.dart,**/*.freezed.dart,contracts/**,prisma/migrations/**'

if [ $DO_LIST -eq 1 ]; then
  echo "Nhánh: ${BRANCH:-(không gửi — Community Build, Sonar ghi vào nhánh chính)}"
  while IFS='|' read -r group name; do
    [ -z "${group:-}" ] && continue
    want "$name" || continue
    tok=""; [ -n "$(project_token "$name")" ] && tok="  (token riêng)"
    printf '  %-24s %s%s\n' "$name" "$(project_key "$name")" "$tok"
  done <<< "$TARGETS"
  exit 0
fi

command -v sonar-scanner >/dev/null || {
  echo "✗ không có sonar-scanner trong PATH." >&2
  echo "  macOS: brew install sonar-scanner   ·   hoặc tải SonarScanner CLI từ SONAR_HOST_URL" >&2
  exit 1
}
[ -n "${SONAR_HOST_URL:-}" ] || { echo "✗ .local/env/.env.sonar thiếu SONAR_HOST_URL" >&2; exit 1; }
[ -n "${SONAR_TOKEN:-}" ] || [ -n "${SONAR_TOKEN_MAP:-}" ] || {
  echo "✗ .local/env/.env.sonar thiếu SONAR_TOKEN (hoặc SONAR_TOKEN_MAP cho target riêng)" >&2; exit 1; }

COUNT=0
FAIL=0
while IFS='|' read -r group name; do
  [ -z "${group:-}" ] && continue
  want "$name" || continue
  COUNT=$((COUNT+1))
  dir="srcroot/$group/$name"

  # Flutter dùng lib/, phần còn lại dùng src/. Không có thư mục nguồn thì bỏ qua CÓ TIẾNG.
  if [ -d "$dir/lib" ]; then sources="lib"; tests="test"
  elif [ -d "$dir/src" ]; then sources="src"; tests="src"
  else echo "▲ $name: không thấy src/ hay lib/ — bỏ qua"; continue
  fi

  key="$(project_key "$name")"
  token="$(project_token "$name")"
  if [ -n "$token" ]; then echo "── $name  →  $key  (token riêng)"
  else
    token="$SONAR_TOKEN"
    [ -n "$token" ] || { echo "  ✗ $name: không có SONAR_TOKEN chung lẫn token riêng — bỏ qua"; FAIL=1; continue; }
    echo "── $name  →  $key"
  fi
  if [ $WITH_COVERAGE -eq 1 ]; then
    echo "  · make test (lấy lcov)"
    ( cd "$dir" && make test ) || { echo "  ✗ make test đỏ — vẫn quét, nhưng KHÔNG có coverage mới"; FAIL=1; }
  fi

  args=(
    -Dsonar.host.url="$SONAR_HOST_URL"
    -Dsonar.token="$token"
    -Dsonar.projectKey="$key"
    -Dsonar.projectBaseDir="$ROOT/$dir"
    -Dsonar.working.directory="$ROOT/.local/sonar/$name"
    -Dsonar.sources="$sources"
    -Dsonar.sourceEncoding=UTF-8
  )
  # Token riêng ⇒ project đã được tạo sẵn (project token chỉ cấp cho project có sẵn), tên
  # của nó đặt trên server; gửi projectName là đổi tên nó ở mỗi lượt quét.
  [ -z "$(project_token "$name")" ] && args+=(-Dsonar.projectName="$(project_name "$name")")
  # Target chưa có sonar-project.properties → loại trừ truyền tay như cũ (-D thắng file).
  [ "$sources" = "lib" ] && [ ! -f "$dir/sonar-project.properties" ] && args+=(-Dsonar.exclusions="$EXCLUSIONS")
  [ -n "$BRANCH" ] && args+=(-Dsonar.branch.name="$BRANCH")
  [ -n "${SONAR_ORGANIZATION:-}" ] && args+=(-Dsonar.organization="$SONAR_ORGANIZATION")
  [ "${SONAR_QUALITYGATE_WAIT:-false}" = "true" ] && args+=(-Dsonar.qualitygate.wait=true)

  # Báo cáo coverage (đo lại 2026-09-04):
  #   · jest của 11 boundary/BFF: khai coverageDirectory=../coverage (rootDir=src) →
  #     coverage/lcov.info — pipeline SIT chạy `npm test -- --coverage` rồi đòi đúng file
  #     đó ở gốc repo con; trước đây 10 target thiếu khai nên lcov rơi vào src/coverage/
  #     và build đỏ "coverage/lcov.info is missing".
  #   · flutter test --coverage                          → coverage/lcov.info
  #   · vitest của ops-console-web KHÔNG khai reporter 'lcov' → hiện KHÔNG có lcov nào
  # Vẫn dò cả hai chỗ phòng target mới scaffold quên khai: không có file thì Sonar nhận 0%
  # coverage và quality gate đỏ vì một lý do sai (không phải "thiếu test", mà "không tìm
  # thấy báo cáo").
  lcov=""
  for c in coverage/lcov.info src/coverage/lcov.info; do
    [ -f "$dir/$c" ] && { lcov="$c"; break; }
  done
  if [ -z "$lcov" ]; then
    echo "  ⚠ không thấy lcov.info — Sonar sẽ ghi 0% coverage cho target này."
    echo "    Chạy lại với --coverage, hoặc kiểm nơi bộ test ghi báo cáo."
  fi

  if [ "$sources" = "lib" ]; then
    args+=(-Dsonar.tests="$tests")
    [ -n "$lcov" ] && args+=(-Dsonar.dart.lcov.reportPaths="$lcov")
    # Plugin Dart MẶC ĐỊNH đè analysis_options.yaml của target bằng bộ luật của quality profile
    # trên server (`dartanalyzer`, 279 luật = gần trọn bộ lint Dart: prefer_double_quotes,
    # lines_longer_than_80_chars, always_specify_types…). Đo 2026-09-21: 21 124 vi phạm "mới"
    # trên app mobile, toàn hình thức trái với `analysis_options.yaml` (flutter_lints +
    # prefer_single_quotes) mà `make check` đã canh. Tắt đè để Sonar chấm đúng bộ luật của
    # target — cùng thứ `flutter analyze` chấm ở local.
    args+=(-Dsonar.dart.analyzer.options.override=false)
    # Plugin Dart của Sonar gọi `flutter analyze` lấy từ PATH. Flutter của Homebrew trên máy
    # dùng chung KHÔNG ghi được cache (Permission denied, đo 2026-09-04) → dùng SDK riêng
    # như Makefile gốc (Flutter FVM `~/fvm/versions/<.fvmrc>`). Chưa `pub get`
    # thì analyze cũng chết → chạy trước.
    FVM_V=$(sed -n 's/.*"flutter"[^"]*"\([^"]*\)".*/\1/p' "$dir/.fvmrc" 2>/dev/null)
    FLUTTER_BIN="${FLUTTER:-$HOME/fvm/versions/${FVM_V:-3.44.7}/bin/flutter}"
    if [ -x "$FLUTTER_BIN" ]; then
      export PATH="$(dirname "$FLUTTER_BIN"):$PATH"
      ( cd "$dir" && "$FLUTTER_BIN" pub get >/dev/null ) || echo "  ⚠ flutter pub get đỏ — analyze có thể hỏng"
    fi
  elif [ -f "$dir/sonar-project.properties" ]; then
    # Target có sonar-project.properties (11 NestJS + ops-console-web + scaffold, từ
    # 2026-09-04): file ấy là nguồn loại trừ / tests / inclusions DUY NHẤT. Ở đây chỉ truyền
    # thêm lcov nếu tìm thấy ở chỗ khác file khai (-D thắng file).
    [ -n "$lcov" ] && args+=(-Dsonar.javascript.lcov.reportPaths="$lcov")
  else
    # Nest để spec NẰM CẠNH mã nguồn, nên `tests` trùng `sources`; phân biệt bằng inclusion.
    args+=(
      -Dsonar.exclusions="$EXCLUSIONS"
      -Dsonar.tests="$tests"
      -Dsonar.test.inclusions='**/*.spec.ts,**/*.test.ts,**/*.spec.tsx,**/*.test.tsx'
    )
    [ -n "$lcov" ] && args+=(-Dsonar.javascript.lcov.reportPaths="$lcov")
  fi

  sonar-scanner "${args[@]}" || { echo "  ✗ quét hỏng: $name"; FAIL=1; }
  echo
done <<< "$TARGETS"

[ "$COUNT" -eq 0 ] && { echo "✗ không target nào khớp: ${ONLY[*]:-}" >&2; exit 2; }
exit $FAIL
