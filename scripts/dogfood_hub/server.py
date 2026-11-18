#!/usr/bin/env python3
"""`dogfood_hub` — MỘT server MCP thay cho bảy server Playwright của `/viper-dogfood`.

Vì sao có file này: `.mcp.json` từng khai bảy server Playwright, mỗi server lộ trọn 24
tool ⇒ ~168 định nghĩa tool nạp vào MỌI phiên; mà harness không nạp `mcpServers` khai
trong frontmatter của subagent (đo 2026-09-14) nên mỗi vai gánh đủ 168. Cộng thêm thói
quen gọi ảnh chụp cây accessibility sau MỖI thao tác và phép dò giá trị hình thức không
giới hạn của vai khó tính — đó là hai nguồn làm tràn ngữ cảnh giữa lượt dogfood.

Hub lộ BỐN tool ý-định-cấp-cao, tự chạy cả kịch bản bằng một lời gọi xuống đầu kia rồi
chỉ trả kết luận cô đọng.

RANH GIỚI (đọc trước khi sửa): hub là HẠ TẦNG, không phải mô hình nghiệp vụ. Trong
`scripts/dogfood_hub/` không được có mã màn, tên route, tên nút của sản phẩm, địa chỉ cổng
đăng nhập hay tên tài khoản. Những thứ ấy đã có nguồn trong `context/` và `deployment/`;
chép vào đây là dựng nguồn sự thật thứ hai và biến hub thành cái phải sửa mỗi vòng.

    python3 scripts/dogfood_hub/server.py              # chạy như server MCP (stdio)
    python3 scripts/dogfood_hub/server.py --selftest   # đi trọn vòng với chính mình
    python3 scripts/dogfood_hub/server.py --dry-run    # in nguồn JS của một kịch bản mẫu
    python3 scripts/dogfood_hub/server.py --wire-report [N]  # chi phí ngữ cảnh của vòng N (mặc định vòng hiện tại)

⚠ stdout LÀ kênh JSON-RPC. Cấm `print()` trần trong cả gói này — mọi lời cho người đọc đi
  qua `log()` ra stderr, và mỗi khung gửi xong phải flush.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # chạy thẳng bằng đường dẫn file, không phải `-m`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    __package__ = "scripts.dogfood_hub"

from .client import text_of
from .config import Config
from .evidence import Ledger, loop_number
from .flows import FlowGate, expand
from .lanes import DEFAULT_LABEL_PREFIX, LANES, LaneRegistry, label_of
from .probes import diff_values, load_slice
from .report import audit_report, probe_report, scenario_log
from .rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    RpcError,
    decode,
    encode,
    error,
    log,
    result,
)
from .scenario import VERBS, compile_scenario

SERVER_NAME = "dogfood_hub"
SERVER_VERSION = "1.0.0"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Ảnh chụp nằm CÙNG thư mục `--output-dir` mà gateway đặt cho làn, nên `ps` vẫn nhận ra
# làn theo nhãn, và không một byte base64 nào phải đi qua kênh JSON-RPC.
SHOT_ROOT = "/tmp/viper-df"

ESCAPE_HATCH_CAP = 4000

_TAG = {
    "type": "string",
    "enum": sorted(LANES),
    "description": (
        "BẮT BUỘC — làn của vai đang chạy. Không có mặc định: rơi vào làn chung là hai vai "
        "dùng chung một trình duyệt, và cả hai lượt dùng thử mất giá trị."
    ),
}
_ROLE = {
    "type": "string",
    "description": "tên vai đang chạy (thẻ .claude/skills/viper-roles/roles/<vai>.md) — chỉ để sổ "
                   "wire.jsonl tính chi phí theo vai; khe đổi vai qua từng đợt nên làn không nói được.",
}
_TARGET = {
    "type": "string",
    "description": "tên experience khai ở context/dogfood/targets.json, hoặc URL đầy đủ",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "df_run_scenario",
        "description": (
            "Chạy nguyên một kịch bản dùng-thử-thật bằng một lượt gọi, trả nhật ký ✓/✗ từng bước. "
            "Đây là ĐƯỜNG MẶC ĐỊNH của /viper-dogfood cho experience web — rẻ hơn hẳn việc gọi "
            "từng thao tác rồi kéo về cả cây accessibility.\n"
            "ĐI LẠI: goto · back · reload · open_tab{path,share_session} · switch_tab.\n"
            "THAO TÁC: click · type · fill · select · press · hover · upload · dialog{accept} · "
            "double_submit (hai lần cùng một tích tắc — lần hai bị chặn là dấu hiệu TỐT). "
            "Mỗi thao tác trỏ vào phần tử bằng selector | role+name | label | placeholder | text.\n"
            "NHÌN: read_screen{selector,limit} (chữ người dùng thật đọc được — thay cho ảnh chụp cây) · "
            "read_storage{key,where} · screenshot{name,full} · expect_text · expect_no_text · "
            "expect_url · expect_count{equals|at_least — bỏ trống = ít nhất 1} · expect_enabled · "
            "wait_for{url|text|gone|selector|idle|ms}.\n"
            "ĐO: audit{checks:[geometry,contrast,values,tap_targets,console]} · "
            "measure{selector,props[],limit,as} (getComputedStyle + hộp bao của mọi phần tử khớp — số đo thật "
            "cho vai soi hình thức, thay cho cửa thoát).\n"
            "BÓP MÔI TRƯỜNG: offline · restore_net · throttle · abort_route · route_status · "
            "unroute · resize · theme.\n"
            "LUỒNG ĐẶT TÊN: run_flow{name} nở ra từ context/dogfood/flows/<name>.json.\n"
            "Bí mật: viết {{env.TÊN_BIẾN}} trong kịch bản — hub thay lúc chạy, không bao giờ in giá trị.\n"
            "Hỏng là dừng: bước hỏng được chụp ảnh + trích DOM quanh đó, vì bước hỏng chính là phát hiện."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["tag", "steps"],
            "properties": {
                "tag": _TAG,
                "role": _ROLE,
                "target": _TARGET,
                "steps": {"type": "array", "items": {"type": "object"}, "description": "mảng step theo DSL ở mô tả"},
                "fresh": {
                    "type": "boolean",
                    "description": "true = đóng trình duyệt của làn trước khi chạy, mở một trình duyệt "
                                   "--isolated TRẮNG (không cookie, localStorage, tab của vai trước). BẮT BUỘC ở "
                                   "lượt gọi ĐẦU của mỗi vai — khe được dùng lại qua nhiều vai.",
                },
                "viewport": {
                    "type": "string",
                    "description": "RỘNGxCAO, ví dụ 1280x800. Bỏ trống = mặc định của làn. Mỗi lượt gọi "
                                   "BẮT ĐẦU ở cỡ này — step `resize` chỉ giữ tới lượt df_run_scenario kế tiếp. "
                                   "Dạng dấu phẩy KHÔNG báo lỗi mà cho cửa sổ 0×0 — luôn dùng dấu nhân.",
                },
            },
        },
    },
    {
        "name": "df_audit_page",
        "description": (
            "Soi một trang ĐANG RENDER và chỉ trả VI PHẠM: hình học khung nhìn (tâm màn · khối phủ "
            ">60% · phần tử neo nhầm ô chứa), tương phản theo chuẩn WCAG, giá trị hình thức nằm ngoài "
            "lát giá trị thiết kế (và giá trị thuộc lát KHÁC — trộn hai bộ, nặng hơn), vùng chạm nhỏ "
            "hơn ngưỡng, lỗi console. Đọc HÌNH HỌC trước: bốn con số màu vẫn đúng hết khi cả trang bị "
            "một tấm ảnh phủ kín."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["tag"],
            "properties": {
                "tag": _TAG,
                "role": _ROLE,
                "target": _TARGET,
                "path": {"type": "string", "description": "mở path này trước khi soi; bỏ trống = soi trang đang mở"},
                "checks": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["geometry", "contrast", "values", "tap_targets", "console"]},
                    "description": "mặc định: geometry · contrast · console",
                },
                "slice": {"type": "string", "description": "tên lát giá trị thiết kế để đối chiếu; bỏ trống = lát mặc định của target"},
                "tap_min": {"type": "number", "description": "ngưỡng chiều cao vùng chạm (px); bỏ trống = lấy từ cấu hình"},
            },
        },
    },
    {
        "name": "df_api_probe",
        "description": (
            "Bắn một loạt lời gọi HTTP TỪ BÊN TRONG làn (dùng đúng ngữ cảnh trình duyệt của vai) và "
            "chỉ trả những lượt LỆCH kỳ vọng, không trả thân phản hồi. Hợp cho ma trận phân quyền, "
            "thử nhập bậy, thử gửi trùng. Cần chứng thư thì lấy trước bằng step read_storage rồi "
            "truyền qua `headers` — hub không biết khoá nào chứa gì."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["tag", "requests"],
            "properties": {
                "tag": _TAG,
                "role": _ROLE,
                "target": _TARGET,
                "headers": {"type": "object", "description": "header chung cho mọi lượt"},
                "requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["method", "path"],
                        "properties": {
                            "method": {"type": "string"},
                            "path": {"type": "string"},
                            "body": {},
                            "headers": {"type": "object"},
                            "expect": {"type": "integer", "description": "mã trạng thái kỳ vọng"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "df_browser_code",
        "description": (
            "CỬA THOÁT — chạy một hàm `async (page) => {…}` ở đầu kia, trả đúng giá trị hàm ấy return "
            f"(cắt cứng {ESCAPE_HATCH_CAP} ký tự). CHỈ dùng khi DSL của df_run_scenario không diễn đạt "
            "được thao tác. Mọi lượt gọi đều được ghi sổ để soát lại xem DSL có đang yếu không. "
            "KHÔNG dùng để đi tắt qua giao diện rồi kết luận «luồng chạy được» — đi tắt là hết dogfood."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["tag", "code", "note"],
            "properties": {
                "tag": _TAG,
                "role": _ROLE,
                "code": {"type": "string", "description": "nguyên văn `async (page) => { … }`, một tham số, có return"},
                "note": {
                    "type": "string",
                    "description": "BẮT BUỘC — DSL thiếu động từ nào mà phải dùng cửa thoát. Câu này vào sổ.",
                },
            },
        },
    },
]


class Hub:
    def __init__(self) -> None:
        self.cfg = Config()
        self.lanes = LaneRegistry(
            str(self.cfg.general.get("lane_label_prefix") or DEFAULT_LABEL_PREFIX),
            viewports=self.cfg.general.get("lane_viewports") or {},
        )
        self.ledger = Ledger(loop_number(self.cfg.general))

    # ------------------------------------------------------------------ tiện ích

    def _shot_dir(self, lane: str) -> str:
        return f"{SHOT_ROOT}/{label_of(lane, self.lanes.label_prefix)}"

    def _tap_min(self, given: float | None) -> float:
        if given is not None:
            return float(given)
        return float(self.cfg.general.get("tap_min") or 44)

    async def _run(self, lane: str, source: str) -> tuple[dict[str, Any], int, int]:
        async with self.lanes.use(lane) as client:
            payload = await client.run_code(source)
        text = text_of(payload)
        marker = "### Result"
        if marker in text:
            body = text.split(marker, 1)[1]
            body = body.split("### Ran", 1)[0]
        elif "### Error" in text:
            # Đầu kia tự báo lỗi (trình duyệt không khởi động được, trang treo modal…) — nói đúng
            # lỗi ấy, đừng đổ cho nguồn JS: bản cũ gộp hai ca nên «thiếu Chrome» đọc thành «JS hỏng».
            raise RpcError("đầu kia báo lỗi:\n" + text.split("### Error", 1)[1].strip()[:1500])
        else:
            body = text
        try:
            data = json.loads(body.strip())
        except json.JSONDecodeError as exc:
            raise RpcError(
                "đầu kia không trả JSON đọc được — nhiều khả năng nguồn JS sinh ra hỏng cú pháp. "
                f"Chạy lại với --dry-run để đọc nguồn.\n{text[:1500]}"
            ) from exc
        return data, len(source.encode()), len(text.encode())

    def _record_visits(self, lane: str, data: dict[str, Any]) -> dict[str, str | None]:
        screens: dict[str, str | None] = {}
        geometry_state = None
        for key, audit in (data.get("audits") or {}).items():
            del key
            geom = audit.get("geometry")
            if geom is not None:
                geometry_state = "ok" if not (geom.get("wide") or geom.get("escaped")) else "vi phạm"
        for path in data.get("visited") or []:
            screen = self.cfg.screen_of(path)
            screens[path] = screen
            self.ledger.visit(
                lane=lane, path=path, screen=screen,
                shot=(data.get("shots") or [None])[0], geometry=geometry_state,
            )
        return screens

    # ---------------------------------------------------------------------- tool

    async def df_run_scenario(self, args: dict[str, Any]) -> str:
        lane = self.lanes.check(args.get("tag"))
        base = self.cfg.base_url(args.get("target"))
        raw_steps = args.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise RpcError("`steps` phải là một mảng có ít nhất một step")
        steps, serialized = expand(self.cfg, raw_steps, lane=lane)
        viewport = args.get("viewport")
        if viewport and not __import__("re").fullmatch(r"\d+x\d+", str(viewport)):
            raise RpcError(
                f"viewport sai dạng: {viewport!r} — phải là RỘNGxCAO, ví dụ 1280x800. "
                "Dạng dấu phẩy KHÔNG báo lỗi mà cho cửa sổ 0×0, rồi mọi lượt bấm đều quá hạn."
            )
        source = compile_scenario(
            lane=lane, base=base, steps=steps,
            shot_dir=self._shot_dir(lane),
            forbidden_paths=self.cfg.forbidden_paths,
            tap_min=self._tap_min(None),
            viewport=str(viewport or self.lanes.viewports[lane]),
        )
        if args.get("fresh"):
            if await self.lanes.reset(lane):
                log(f"▸ làn {lane}: fresh — đóng trình duyệt cũ, lượt này mở trình duyệt trắng")
        started = time.monotonic()
        async with FlowGate(self.lanes, serialized):
            data, down, up = await self._run(lane, source)
        screens = self._record_visits(lane, data)
        text = self.cfg.redact(scenario_log(data, lane=lane, base=base, screens=screens,
                                           label=label_of(lane, self.lanes.label_prefix)))
        # Bước `audit` NẰM TRONG kịch bản trước nay thu số rồi vứt đi: nhật ký chỉ in
        # «✓ 01 audit values · geometry» và không gì thêm, nên vai nào đi theo đường tài liệu
        # khuyên («một kịch bản, một lượt gọi») nhận ĐIỂM ĐẬU IM LẶNG — đo được 2026-09-17 trên
        # một trang đang render lát tối: bước `audit` im, còn `df_audit_page` cùng trang đó báo
        # 15 giá trị trộn + 15 ngoài lát (dogfood picky vòng 4, §7.1).
        text += self._scenario_audits(lane=lane, data=data, args=args)
        self.ledger.wire(
            role=args.get("role"), tool="df_run_scenario", lane=lane, steps=len(steps), remote_calls=1,
            bytes_down=down, bytes_up=up, bytes_to_model=len(text.encode()),
            ms=int((time.monotonic() - started) * 1000),
        )
        return text

    def _scenario_audits(self, *, lane: str, data: dict[str, Any], args: dict[str, Any]) -> str:
        """Phần VI PHẠM của mọi bước `audit` trong một kịch bản — cùng khuôn với `df_audit_page`."""
        audits = data.get("audits") or {}
        if not audits:
            return ""
        tap_min = self._tap_min(args.get("tap_min"))
        slice_name = args.get("slice") or self.cfg.targets.get("default_slice")
        out: list[str] = []
        for key, got in audits.items():
            values = None
            if got.get("values") is not None:
                slices = self._load_slices()
                if slices:
                    values = diff_values(got.get("values") or [], slices, primary=str(slice_name))
            out.append("")
            out.append(f"── bước `audit` «{key}» ──")
            out.append(audit_report(
                lane=lane, url=data.get("url", ""),
                geometry=got.get("geometry"), contrast=got.get("contrast"),
                values=values, tap_targets=got.get("tap_targets"), tap_min=tap_min,
                console=got.get("console"), slice_name=str(slice_name) if values is not None else None,
            ))
        return self.cfg.redact("\n".join(out))

    async def df_audit_page(self, args: dict[str, Any]) -> str:
        lane = self.lanes.check(args.get("tag"))
        base = self.cfg.base_url(args.get("target"))
        checks = list(args.get("checks") or ["geometry", "contrast", "console"])
        tap_min = self._tap_min(args.get("tap_min"))
        steps: list[dict[str, Any]] = []
        if args.get("path"):
            steps.append({"goto": args["path"]})
        # Chờ trang VẼ XONG rồi mới đo. Bản cũ đo ngay: nhiều lượt trả «HÌNH HỌC ✓ tâm là html» và
        # «TƯƠNG PHẢN ✓ 1 cặp đều đạt» trên màn thật ra có 83 phần tử và 30 cặp — vì lúc ấy DOM còn là
        # `div.page > div.pat.busy`. Dấu ✗ thì đáng tin, dấu ✓ thì không (dogfood picky vòng 4, §7.2);
        # và cùng lý do đó, `tap_targets` báo «không phần tử nào thấp hơn 44px» khi chưa có nút nào (§7.4).
        steps.append({"wait_for": {"idle": True}})
        steps.append({"wait_for": {"ms": 350}})
        steps.append({"audit": {"checks": checks, "as": "page"}})
        source = compile_scenario(
            lane=lane, base=base, steps=steps,
            shot_dir=self._shot_dir(lane),
            forbidden_paths=self.cfg.forbidden_paths,
            tap_min=tap_min,
        )
        started = time.monotonic()
        data, down, up = await self._run(lane, source)
        got = (data.get("audits") or {}).get("page") or {}
        if not data.get("ok"):
            return self.cfg.redact(scenario_log(
                data, lane=lane, base=base, screens=self._record_visits(lane, data),
                label=label_of(lane, self.lanes.label_prefix)))
        self._record_visits(lane, data)

        values = None
        slice_name = args.get("slice") or self.cfg.targets.get("default_slice")
        if "values" in checks:
            slices = self._load_slices()
            if not slices:
                raise RpcError(
                    "phép đo `values` cần lát giá trị thiết kế, nhưng context/dogfood/targets.json "
                    "chưa khai `slices` (tên lát → đường dẫn tokens.json) — xem scripts/dogfood_hub/README.md."
                )
            values = diff_values(got.get("values") or [], slices, primary=str(slice_name))
        text = self.cfg.redact(audit_report(
            lane=lane, url=data.get("url", ""),
            geometry=got.get("geometry"), contrast=got.get("contrast"),
            values=values, tap_targets=got.get("tap_targets"), tap_min=tap_min,
            console=got.get("console"), slice_name=str(slice_name) if values is not None else None,
        ))
        self.ledger.wire(
            role=args.get("role"), tool="df_audit_page", lane=lane, steps=len(steps), remote_calls=1,
            bytes_down=down, bytes_up=up, bytes_to_model=len(text.encode()),
            ms=int((time.monotonic() - started) * 1000),
        )
        return text

    def _load_slices(self) -> dict[str, dict[str, set[str]]]:
        out: dict[str, dict[str, set[str]]] = {}
        for name, path in (self.cfg.targets.get("slices") or {}).items():
            try:
                out[name] = load_slice(path)
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                log(f"▲ bỏ qua lát {name}: {exc}")
        return out

    async def df_api_probe(self, args: dict[str, Any]) -> str:
        lane = self.lanes.check(args.get("tag"))
        base = self.cfg.base_url(args.get("target"))
        requests = self.cfg.expand(args.get("requests") or [], lane=lane)
        shared = self.cfg.expand(args.get("headers") or {}, lane=lane)
        source = _PROBE_JS.replace("__PARAMS__", json.dumps(
            {"base": base, "requests": requests, "shared": shared}, ensure_ascii=False))
        started = time.monotonic()
        data, down, up = await self._run(lane, source)
        text = self.cfg.redact(probe_report(data.get("rows") or []))
        self.ledger.wire(
            role=args.get("role"), tool="df_api_probe", lane=lane, steps=len(requests), remote_calls=1,
            bytes_down=down, bytes_up=up, bytes_to_model=len(text.encode()),
            ms=int((time.monotonic() - started) * 1000),
        )
        return text

    async def df_browser_code(self, args: dict[str, Any]) -> str:
        lane = self.lanes.check(args.get("tag"))
        code = str(args.get("code") or "")
        note = str(args.get("note") or "").strip()
        if not note:
            raise RpcError(
                "`note` bắt buộc: nói rõ DSL của df_run_scenario thiếu động từ nào mà phải dùng cửa "
                "thoát. Câu này vào sổ để cuối lượt soát lại — cửa thoát dùng nhiều nghĩa là DSL yếu, "
                "không phải là cách làm việc."
            )
        if not code.strip().startswith("async"):
            raise RpcError("`code` phải là nguyên văn `async (page) => { … }`")
        self.ledger.escape_hatch(lane=lane, note=note, code_len=len(code))
        async with self.lanes.use(lane) as client:
            payload = await client.run_code(code)
        text = self.cfg.redact(text_of(payload))
        if len(text) > ESCAPE_HATCH_CAP:
            text = text[:ESCAPE_HATCH_CAP] + f"\n… (cắt ở {ESCAPE_HATCH_CAP} ký tự — cửa thoát cố tình rẻ hơn thì không rẻ)"
        self.ledger.wire(
            role=args.get("role"), tool="df_browser_code", lane=lane, steps=0, remote_calls=1,
            bytes_down=len(code.encode()), bytes_up=len(text.encode()),
            bytes_to_model=len(text.encode()), ms=0,
        )
        return text

    async def close(self) -> None:
        await self.lanes.close()


_PROBE_JS = r"""
async (page) => {
  const P = __PARAMS__;
  const rows = [];
  for (const r of P.requests) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, P.shared, r.headers || {});
    let status = 0, message = '';
    try {
      const res = await page.request.fetch(P.base + r.path, {
        method: (r.method || 'GET').toUpperCase(),
        headers,
        data: r.body === undefined ? undefined : JSON.stringify(r.body),
      });
      status = res.status();
      const body = await res.text();
      try {
        const j = JSON.parse(body);
        message = (j && j.error && j.error.message) || j.message || '';
      } catch { message = body.slice(0, 120); }
    } catch (e) {
      message = String(e && e.message || e).slice(0, 160);
    }
    rows.push({
      method: r.method, path: r.path, status, expect: r.expect ?? null,
      message, mismatch: r.expect !== undefined && r.expect !== null && status !== r.expect,
    });
  }
  return { rows };
}
"""

HANDLERS = {
    "df_run_scenario": "df_run_scenario",
    "df_audit_page": "df_audit_page",
    "df_api_probe": "df_api_probe",
    "df_browser_code": "df_browser_code",
}


async def serve() -> None:
    hub = Hub()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    out = sys.stdout.buffer

    def send(frame: dict[str, Any]) -> None:
        out.write(encode(frame))
        out.flush()  # stdout đệm dòng khi là ống — thiếu flush là phiên treo, không lỗi nào

    log(f"▸ {SERVER_NAME} {SERVER_VERSION} sẵn sàng · {len(TOOLS)} tool · làn: {' '.join(sorted(LANES))}")
    missing = hub.cfg.lanes_preflight()
    if missing:
        log(
            "▲ chưa có biến làn cho: " + " · ".join(missing)
            + " — khai ở context/dogfood/vars/lanes.json (hoặc bỏ `lane_roles` khỏi config.json)."
        )
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                frame = decode(line)
            except RpcError as exc:
                send(error(None, PARSE_ERROR, str(exc)))
                continue
            req_id = frame.get("id")
            method = frame.get("method", "")
            params = frame.get("params") or {}
            try:
                if method == "initialize":
                    send(result(req_id, {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    }))
                elif method in ("notifications/initialized", "notifications/cancelled"):
                    continue
                elif method == "tools/list":
                    send(result(req_id, {"tools": TOOLS}))
                elif method == "tools/call":
                    name = params.get("name", "")
                    if name not in HANDLERS:
                        send(error(req_id, METHOD_NOT_FOUND, f"không có tool tên {name!r}"))
                        continue
                    handler = getattr(hub, HANDLERS[name])
                    try:
                        text = await handler(params.get("arguments") or {})
                        send(result(req_id, {"content": [{"type": "text", "text": text}]}))
                    except RpcError as exc:
                        send(result(req_id, {"content": [{"type": "text", "text": f"✗ {exc}"}], "isError": True}))
                elif method == "ping":
                    send(result(req_id, {}))
                elif req_id is not None:
                    send(error(req_id, METHOD_NOT_FOUND, f"chưa hiện thực {method!r}"))
            except RpcError as exc:
                send(error(req_id, INVALID_PARAMS, str(exc)))
            except Exception as exc:  # noqa: BLE001 — server không được chết vì một lời gọi
                log(f"✗ lỗi không lường trước ở {method}: {exc!r}")
                if req_id is not None:
                    send(error(req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
    finally:
        await hub.close()


# ───────────────────────────────────────────────────────────────── chế độ tự kiểm

def selftest() -> int:
    """Đi trọn vòng `initialize → tools/list → tools/call` với chính mình, không trình duyệt nào.

    Ba thứ được khẳng định: khung ra stdout luôn là JSON hợp lệ · catalog đúng bốn tool ·
    thiếu `tag` là LỖI CÓ TÊN chứ không phải rơi vào làn chung.
    """
    import io
    import subprocess

    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "df_run_scenario", "arguments": {"steps": [{"goto": "/"}]}}},
    ]
    payload = b"".join(encode(f) for f in frames)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        input=payload, capture_output=True, timeout=60, check=False,
    )
    failures: list[str] = []
    replies = []
    for raw in io.BytesIO(proc.stdout).read().splitlines():
        if not raw.strip():
            continue
        try:
            replies.append(json.loads(raw))
        except json.JSONDecodeError:
            failures.append(f"stdout có dòng KHÔNG phải JSON — kênh bị bẩn: {raw[:160]!r}")

    by_id = {r.get("id"): r for r in replies}
    if 1 not in by_id or "result" not in by_id[1]:
        failures.append("không bắt tay được (initialize)")
    tools = (by_id.get(2, {}).get("result") or {}).get("tools") or []
    if len(tools) != 4:
        failures.append(f"catalog phải có đúng 4 tool, đang có {len(tools)}")
    names = {t["name"] for t in tools}
    if names != set(HANDLERS):
        failures.append(f"tên tool lệch: {sorted(names)}")
    if any(v in json.dumps(tools, ensure_ascii=False) for v in ("login", "logout")):
        failures.append("catalog nhắc tới một động từ đăng nhập — hub không được biết khái niệm ấy")
    third = by_id.get(3, {}).get("result") or {}
    text = "".join(b.get("text", "") for b in third.get("content") or [])
    if "tag" not in text or not third.get("isError"):
        failures.append("thiếu `tag` phải là lỗi có tên, đang trả: " + text[:160])

    problem = _js_syntax_ok(sample_source())
    if problem:
        failures.append(f"nguồn JS sinh ra hỏng cú pháp: {problem}")

    for line in failures:
        log(f"  ✗ {line}")
    if failures:
        log(f"✗ selftest: {len(failures)} mục hỏng")
        return 1
    log(f"✓ selftest: {len(tools)} tool · stdout thuần JSON · thiếu `tag` bị chặn · nguồn JS sạch cú pháp")
    return 0


def sample_source() -> str:
    """Nguồn JS của một kịch bản mẫu — dùng cho cả `--dry-run` lẫn phép kiểm cú pháp."""
    cfg = Config()
    steps, _ = expand(cfg, [
        {"goto": "/"}, {"read_screen": {"limit": 400}}, {"audit": ["geometry", "contrast", "values", "tap_targets"]},
        {"click": {"role": "button", "name": "x"}}, {"open_tab": {"path": "/", "share_session": True}},
        {"throttle": {"latency_ms": 100}}, {"route_status": {"pattern": "**/x", "status": 500}},
        {"double_submit": {"selector": "#x"}}, {"read_storage": "k"}, {"screenshot": {"name": "a.png"}},
    ])
    return compile_scenario(
        lane="main", base="http://example.invalid", steps=steps,
        shot_dir=f"{SHOT_ROOT}/{DEFAULT_LABEL_PREFIX}main", forbidden_paths=cfg.forbidden_paths,
    )


def _js_syntax_ok(source: str) -> str | None:
    """`node --check` trên nguồn sinh ra. Trả None là sạch, hoặc thông báo lỗi.

    Nguồn JS do Python dựng bằng chuỗi nên KHÔNG linter nào của repo soi được — đây là lớp
    duy nhất bắt được một dấu ngoặc thiếu trước khi nó thành «cả kịch bản hỏng, không đọc
    được vì sao» giữa lượt dogfood. Không có node thì bỏ qua và NÓI RA.
    """
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("node"):
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        # Bọc thành một phép gán: `async (page) => {…}` đứng một mình là biểu thức, `node
        # --check` từ chối. Bọc lại thì vẫn kiểm đúng thân hàm.
        handle.write("const _ = " + source + ";\n")
        temp = handle.name
    try:
        done = subprocess.run(["node", "--check", temp], capture_output=True, text=True, timeout=30, check=False)
        return None if done.returncode == 0 else (done.stderr or done.stdout).strip()[:800]
    finally:
        Path(temp).unlink(missing_ok=True)


def dry_run() -> int:
    """Ghi nguồn JS của một kịch bản mẫu ra file — cách duy nhất đọc bằng mắt thứ Python sinh ra.

    Ghi ra file chứ không in ra stdout: ở chế độ server stdout là kênh JSON-RPC, và một chế
    độ in thẳng ra đó là lời mời cho thói quen sai.
    """
    source = sample_source()
    out = REPO_ROOT / ".viper" / "dogfood" / "dry-run.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source + "\n", encoding="utf-8")
    problem = _js_syntax_ok(source)
    log(f"▸ đã ghi {out.relative_to(REPO_ROOT)} · {len(source)} ký tự · {len(VERBS)} động từ hub hiểu")
    if problem:
        log(f"✗ nguồn JS sinh ra HỎNG cú pháp:\n{problem}")
        return 1
    log("✓ node --check: nguồn JS sinh ra sạch cú pháp")
    return 0


def wire_report(loop: str | None = None) -> int:
    """Tổng kết chi phí ngữ cảnh của một vòng dogfood từ sổ `.viper/dogfood/l<N>/` — ra STDERR.

    `bytes_to_model` là thứ DUY NHẤT tốn ngữ cảnh của khe; ước token ≈ byte / 4 (chữ Việt có dấu
    nhiều byte hơn, nên đây là trần trên). Cửa thoát dùng nhiều = DSL yếu hoặc vai đi tắt — xem lý do.
    """
    loop = loop or loop_number(Config().general)
    base = REPO_ROOT / ".viper" / "dogfood" / f"l{loop}"
    rows = []
    wire = base / "wire.jsonl"
    if wire.exists():
        rows = [json.loads(x) for x in wire.read_text("utf-8").splitlines() if x.strip()]
    if not rows:
        log(f"chưa có sổ {wire.relative_to(REPO_ROOT)} — vòng {loop} chưa có lượt df_* nào")
        return 1
    def table(key: str) -> None:
        agg: dict[str, list[int]] = {}
        for r in rows:
            a = agg.setdefault(str(r.get(key)), [0, 0, 0])
            a[0] += 1
            a[1] += int(r.get("steps") or 0)
            a[2] += int(r.get("bytes_to_model") or 0)
        log(f"  theo {key}:")
        for name, (calls, steps, size) in sorted(agg.items(), key=lambda kv: -kv[1][2]):
            log(f"    {name:<18} {calls:>4} lượt · {steps:>5} bước · {size:>8} byte vào model ≈ {size // 4:>7} token")
    total = sum(int(r.get("bytes_to_model") or 0) for r in rows)
    log(f"sổ dogfood vòng {loop}: {len(rows)} lượt gọi · {total} byte vào model ≈ {total // 4} token")
    table("role")
    table("lane")
    table("tool")
    hatch = base / "escape-hatch.jsonl"
    notes = [json.loads(x) for x in hatch.read_text("utf-8").splitlines() if x.strip()] if hatch.exists() else []
    log(f"  cửa thoát df_browser_code: {len(notes)} lượt")
    for n in notes[-10:]:
        log(f"    [{n.get('lane')}] {n.get('note')}")
    cov = base / "coverage.jsonl"
    if cov.exists():
        seen = {json.loads(x).get("screen") or json.loads(x).get("path")
                for x in cov.read_text("utf-8").splitlines() if x.strip()}
        log(f"  màn/path đã mở: {len(seen)} — {' '.join(sorted(str(x) for x in seen))[:400]}")
    return 0


def main() -> int:
    if "--wire-report" in sys.argv:
        i = sys.argv.index("--wire-report")
        return wire_report(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
    if "--selftest" in sys.argv:
        return selftest()
    if "--dry-run" in sys.argv:
        return dry_run()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
