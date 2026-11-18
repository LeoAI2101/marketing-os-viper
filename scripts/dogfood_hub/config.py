#!/usr/bin/env python3
"""Nạp dữ liệu của DỰ ÁN từ `context/dogfood/` — hub không mang hằng nào của sản phẩm.

Ranh giới: trong `scripts/dogfood_hub/` không được có mã màn, tên route, tên gói design
system, địa chỉ cổng đăng nhập hay tên tài khoản. Tất cả những thứ ấy ĐÃ có nguồn
(`context/PROTOTYPE.md §1`, `context/ARCHITECTURE.md §2/§3`, `intake/design-systems/*/tokens.json`,
`deployment/local/.env`); chép vào hub là dựng nguồn sự thật thứ hai.

Bốn file dưới đây là của dự án, meta viết ở pha I khi bật hub (scripts/dogfood_hub/README.md):

    context/dogfood/config.json    vài hằng chung (path bị cấm điều hướng, ngưỡng vùng chạm)
    context/dogfood/targets.json   experience → URL gốc · lát giá trị thiết kế
    context/dogfood/routes.json    route → mã màn (cho sổ phủ màn), tuỳ chọn
    context/dogfood/flows/*.json   luồng đặt tên (đăng nhập…), do người viết

Thiếu file thì hub VẪN CHẠY với mặc định rỗng và NÓI RA — dogfood không được chết chỉ vì
thiếu một bảng tra cứu; nhưng cũng không được im lặng, vì im lặng là cách sổ phủ màn bỏ
sót màn mà không ai biết.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .rpc import RpcError, log

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "context" / "dogfood"
FLOWS_DIR = DATA_DIR / "flows"

# File env của cụm local (deployment/README.md): `deployment/.env.example` là bản mẫu được commit;
# giá trị thật nằm ở `deployment/local/.env` và không bao giờ vào repo.
ENV_FILE = REPO_ROOT / "deployment" / "local" / ".env"

_ENV = re.compile(r"\{\{env\.([A-Z0-9_]+)\}\}")
# Biến RIÊNG CỦA LÀN: hub tra `vars/lanes.json` theo `tag` đang chạy. Hub không biết trường
# nào nghĩa là gì — nó chỉ thay chuỗi. Tuỳ chọn: tài khoản đi theo VAI (`<vai>@dogfood.local`).
_LANE = re.compile(r"\{\{lane\.([a-z0-9_]+)\}\}")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        log(f"▲ chưa có {path.relative_to(REPO_ROOT)} — khuôn và cách điền: scripts/dogfood_hub/README.md")
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise RpcError(f"{path.relative_to(REPO_ROOT)} hỏng JSON: {exc}") from exc


def _parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env() -> dict[str, str]:
    """Đọc `deployment/local/.env` thành dict. Không ghi đè biến đã có trong môi trường."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        values.update(_parse_dotenv(ENV_FILE.read_text("utf-8")))
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith("DF_") or k.startswith("DOGFOOD_")})
    return values


class Config:
    def __init__(self) -> None:
        self.general: dict[str, Any] = _read_json(DATA_DIR / "config.json", {})
        self.targets: dict[str, Any] = _read_json(DATA_DIR / "targets.json", {})
        self.routes: dict[str, Any] = _read_json(DATA_DIR / "routes.json", {})
        self.lane_table: dict[str, Any] = (_read_json(DATA_DIR / "vars" / "lanes.json", {}) or {}).get("lanes") or {}
        self.env: dict[str, str] = load_env()

    # -------------------------------------------------------------- target/URL

    def base_url(self, target: str | None) -> str:
        """Tên experience hoặc URL đầy đủ → URL gốc, không có dấu `/` ở cuối.

        `targets.json` giữ TÊN BIẾN chứ không giữ giá trị (file ấy được commit, còn giá trị
        nằm ở `deployment/local/.env` không vào repo). Phân giải ở đây, lúc chạy.
        """
        if target and re.match(r"^https?://", target):
            return target.rstrip("/")
        table = self.targets.get("targets") or {}
        if not target:
            target = self.targets.get("default") or ""
        spec = table.get(target)
        if not spec:
            raise RpcError(
                f"target {target!r} không có trong context/dogfood/targets.json. "
                f"Đang khai: {' · '.join(sorted(table)) or '(rỗng)'}"
            )
        if isinstance(spec, str):  # dạng cũ: URL chốt sẵn
            return spec.rstrip("/")
        host = self.env.get(spec.get("host_env", "")) or spec.get("host_default") or "127.0.0.1"
        port = self.env.get(spec.get("port_env", "")) or spec.get("port_default")
        if not port:
            raise RpcError(
                f"target {target!r} cần cổng từ biến {spec.get('port_env')!r} nhưng biến ấy chưa có "
                "giá trị ở deployment/local/.env, và bảng cũng không khai cổng mặc định. "
                "Điền ở deployment/local/.env (deployment/README.md) rồi thử lại."
            )
        return f"http://{host}:{port}"

    # ------------------------------------------------------------------ sổ màn

    def screen_of(self, path: str) -> str | None:
        """Route → mã màn. Không tra được thì trả None và để bên gọi NÓI RA."""
        table: dict[str, str] = self.routes.get("routes") or {}
        clean = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        if clean in table:
            return table[clean]
        # Route động (`/xyz/:id`): so từng đoạn, đoạn mở đầu bằng dấu hai chấm khớp mọi giá trị.
        parts = clean.strip("/").split("/")
        for pattern, screen in table.items():
            pat = pattern.strip("/").split("/")
            if len(pat) != len(parts):
                continue
            if all(p.startswith(":") or p == q for p, q in zip(pat, parts, strict=True)):
                return screen
        return None

    @property
    def forbidden_paths(self) -> list[str]:
        return list(self.general.get("forbidden_paths") or [])

    # ------------------------------------------------------------- thay biến

    def expand(self, value: Any, lane: str | None = None) -> Any:
        """Thay `{{env.TÊN_BIẾN}}` bằng giá trị từ env. KHÔNG BAO GIỜ in giá trị ra log.

        Đây là cách duy nhất giữ luật #6 khi vai tự viết kịch bản: kịch bản mang TÊN biến,
        giá trị chỉ tồn tại trong tiến trình hub. Hub không biết biến nào là bí mật — nó
        chỉ thay chuỗi.

        Rà TRƯỚC toàn bộ rồi mới thay: thiếu biến thì nói ra MỘT LẦN đủ mọi tên còn thiếu,
        chứ không chết ở cái đầu tiên rồi bắt người ta chạy lại để biết cái thứ hai.
        """
        lane_vars: dict[str, str] = dict(self.lane_table.get(lane or "", {})) if lane else {}
        missing = sorted(self._placeholders(value) - set(self.env))
        if missing:
            raise RpcError(
                "kịch bản dùng biến môi trường chưa có giá trị: "
                + " · ".join(f"{{{{env.{name}}}}}" for name in missing)
                + ". Đặt giá trị ở deployment/local/.env (không vào repo) hoặc ngay trong môi trường. "
                "Tên biến khai ở context/dogfood/config.json (env_required) — KHÔNG khai ở "
                "deployment/.env.example, vì doctor coi mọi tên ở đó là bắt buộc cho cả người chỉ "
                "chạy ứng dụng."
            )
        missing_lane = sorted(self._lane_placeholders(value) - set(lane_vars))
        if missing_lane:
            raise RpcError(
                "kịch bản dùng biến của làn chưa có: "
                + " · ".join(f"{{{{lane.{name}}}}}" for name in missing_lane)
                + (f". Làn {lane!r} không có trong context/dogfood/vars/lanes.json — tài khoản "
                   "đi theo vai thì ghi thẳng `<vai>@dogfood.local` trong kịch bản." if not lane_vars
                   else f". Làn {lane!r} chỉ có: {' · '.join(sorted(lane_vars))}.")
            )
        return self._substitute(value, lane_vars)

    def _placeholders(self, value: Any) -> set[str]:
        if isinstance(value, str):
            return set(_ENV.findall(value))
        if isinstance(value, list):
            return {name for item in value for name in self._placeholders(item)}
        if isinstance(value, dict):
            return {name for item in value.values() for name in self._placeholders(item)}
        return set()

    def _lane_placeholders(self, value: Any) -> set[str]:
        if isinstance(value, str):
            return set(_LANE.findall(value))
        if isinstance(value, list):
            return {name for item in value for name in self._lane_placeholders(item)}
        if isinstance(value, dict):
            return {name for item in value.values() for name in self._lane_placeholders(item)}
        return set()

    def _substitute(self, value: Any, lane_vars: dict[str, str]) -> Any:
        if isinstance(value, str):
            out = _ENV.sub(lambda m: self.env[m.group(1)], value)
            return _LANE.sub(lambda m: str(lane_vars[m.group(1)]), out)
        if isinstance(value, list):
            return [self._substitute(item, lane_vars) for item in value]
        if isinstance(value, dict):
            return {key: self._substitute(item, lane_vars) for key, item in value.items()}
        return value

    def lanes_preflight(self) -> list[str]:
        """Làn khai ở `lane_roles` mà chưa có trong bảng suy ra — cảnh báo SỚM, trước lượt đầu."""
        return sorted(set(self.general.get("lane_roles") or {}) - set(self.lane_table))

    #: Tên biến mang BÍ MẬT. Che theo tên, không che theo độ dài: bản đầu che mọi giá trị dài ≥ 4 ký tự nên
    #: `localhost` thành «{{env.NOTI_ENV}}host», cổng `4100` thành «{{env.CONSOLE_PORT}}», và `false` trong
    #: `aria-pressed` thành «{{env.NOTI_REDIS_TLS}}» — nhật ký đọc lên như hub đang hỏng chỗ thay biến, còn
    #: URL thật thì không ai đọc được (đo 2026-09-17, lượt chạy thật đầu tiên).
    SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|APIKEY|API_KEY|_KEY|CREDENTIAL|PRIVATE)", re.I)

    def redact(self, text: str) -> str:
        """Che giá trị có thể lọt vào log hay báo cáo: biến môi trường MANG BÍ MẬT + mật khẩu của làn."""
        out = text
        for name, value in self.env.items():
            if value and len(value) >= 6 and self.SECRET_NAME.search(name) and value in out:
                out = out.replace(value, f"{{{{env.{name}}}}}")
        for spec in self.lane_table.values():
            for field, value in (spec or {}).items():
                # Che mật khẩu khi nó ĐỨNG RIÊNG, không che khi nó là một khúc bên trong chuỗi dài hơn:
                # mật khẩu realm mẫu là `123456`, mà `123456` cũng nằm trong VIN thật
                # (`MNAUMEF50NW123456`) — bản cũ biến VIN trên báo cáo thành `MNAUMEF50NW{{lane.password}}`
                # và vai dogfood đọc ra thành «seed bẩn, placeholder chưa thay» (dogfood breaker 2026-09-17).
                if field == "password" and value:
                    out = re.sub(
                        rf"(?<![0-9A-Za-z]){re.escape(str(value))}(?![0-9A-Za-z])",
                        "{{lane.password}}",
                        out,
                    )
        return out

    # -------------------------------------------------------------------- luồng

    def flow(self, name: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", name or ""):
            raise RpcError(f"tên luồng {name!r} chỉ được chữ thường, số, . _ -")
        path = FLOWS_DIR / f"{name}.json"
        if not path.exists():
            have = sorted(p.stem for p in FLOWS_DIR.glob("*.json")) if FLOWS_DIR.exists() else []
            raise RpcError(
                f"không có luồng {name!r} ở context/dogfood/flows/. "
                f"Đang có: {' · '.join(have) or '(chưa có luồng nào)'}"
            )
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            raise RpcError(f"{path.relative_to(REPO_ROOT)} phải là object có khoá `steps` là mảng")
        return data
