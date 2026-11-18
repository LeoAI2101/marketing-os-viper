#!/usr/bin/env python3
"""Luồng đặt tên: một mảng step chung nằm ở `context/dogfood/flows/<tên>.json`.

Hub KHÔNG có động từ riêng cho việc nào của sản phẩm — kể cả việc vào được ứng dụng. Việc
ấy chỉ là một chuỗi `goto` + `fill` + `click` + `expect_*`, nên nó là DỮ LIỆU. Hub biết
đúng hai điều về một luồng:

  · nở nó ra thành các step chung trước khi biên dịch;
  · nếu file khai `serialize` thì không hai làn nào được chạy nó chồng nhau, giãn tối
    thiểu `min_interval_ms` giữa hai lượt.

Hub **không biết vì sao** phải giãn. Lý do nằm ngay cạnh giá trị, trong chính file luồng —
đó là chỗ người đọc sẽ tìm, và là chỗ nó không trôi khỏi giá trị nó giải thích.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .config import Config
from .lanes import LaneRegistry
from .rpc import RpcError, log

MAX_DEPTH = 3

# Tham số của luồng: `{"run_flow": {"name": "login", "with": {"user": "picky@dogfood.local"}}}` thay
# `{{arg.user}}` trong các step của luồng. Khe nhận vai theo đợt nên tài khoản đi theo VAI, không theo
# làn — một luồng đăng nhập dùng chung cho mọi vai, vai truyền tài khoản của mình vào.
_ARG = re.compile(r"\{\{arg\.([a-z0-9_]+)\}\}")


def _with_args(name: str, steps: list[Any], args: dict[str, Any]) -> list[Any]:
    text = json.dumps(steps, ensure_ascii=False)
    missing = sorted(set(_ARG.findall(text)) - set(args))
    if missing:
        raise RpcError(f"luồng «{name}» cần tham số: " + " · ".join(missing)
                       + ' — truyền qua {"run_flow": {"name": …, "with": {…}}}')
    # Thay trên từng chuỗi (không trên JSON thô) để giá trị có dấu nháy không làm hỏng cấu trúc.
    def sub(value: Any) -> Any:
        if isinstance(value, str):
            return _ARG.sub(lambda m: str(args[m.group(1)]), value)
        if isinstance(value, list):
            return [sub(v) for v in value]
        if isinstance(value, dict):
            return {k: sub(v) for k, v in value.items()}
        return value
    return sub(steps)


def expand(cfg: Config, steps: list[Any], *, lane: str | None = None, depth: int = 0) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Nở mọi `run_flow` thành step chung. Trả (danh sách phẳng, các luồng cần tuần tự hoá)."""
    if depth > MAX_DEPTH:
        raise RpcError(f"luồng lồng nhau quá {MAX_DEPTH} tầng — nhiều khả năng là vòng lặp")
    flat: list[dict[str, Any]] = []
    serialized: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            raise RpcError(f"step phải là object, gặp {type(step).__name__}")
        if "run_flow" in step:
            spec = step["run_flow"]
            name = spec if isinstance(spec, str) else spec.get("name", "")
            args = {} if isinstance(spec, str) else dict(spec.get("with") or {})
            flow = cfg.flow(name)
            inner, inner_serialized = expand(cfg, _with_args(name, flow["steps"], args),
                                             lane=lane, depth=depth + 1)
            flat.append({"note": f"— luồng «{name}» bắt đầu"})
            flat.extend(inner)
            flat.append({"note": f"— luồng «{name}» xong"})
            serialized.update(inner_serialized)
            if flow.get("serialize"):
                serialized[name] = flow
        else:
            flat.append(step)
    return cfg.expand(flat, lane=lane), serialized


class FlowGate:
    """Giữ khoá của mọi luồng khai `serialize` trong suốt một lượt gọi."""

    def __init__(self, registry: LaneRegistry, flows: dict[str, dict[str, Any]]) -> None:
        self._registry = registry
        self._flows = flows
        self._held: list[asyncio.Lock] = []

    async def __aenter__(self) -> FlowGate:
        for name in sorted(self._flows):  # sắp tên để nhiều làn khoá cùng thứ tự, không ôm chéo
            flow = self._flows[name]
            lock = self._registry.flow_lock(name)
            await lock.acquire()
            self._held.append(lock)
            min_gap = float(flow.get("min_interval_ms") or 0) / 1000.0
            if min_gap > 0:
                waited = time.monotonic() - self._registry.flow_last_run(name)
                if waited < min_gap:
                    log(f"▸ luồng «{name}» khai giãn nhịp — chờ thêm {min_gap - waited:.1f}s")
                    await asyncio.sleep(min_gap - waited)
            self._registry.mark_flow_run(name, time.monotonic())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for lock in reversed(self._held):
            lock.release()
        self._held.clear()
