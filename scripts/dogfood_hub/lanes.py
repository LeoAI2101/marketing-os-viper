#!/usr/bin/env python3
"""Làn: `tag` → viewport + nhãn + client. Một vai một làn, không ai dùng chung.

Vì sao `tag` bắt buộc và không có mặc định: bản tham chiếu cho `tag` tuỳ chọn rồi rơi về
một làn chung, và đó chính là đường để hai vai âm thầm dùng chung một trình duyệt. Repo
này đã dính đúng lỗi ấy hai lần (`.mcp.json`, đo 2026-08-21 và 2026-09-14). Năm lớp chặn:

  1. `tag` bắt buộc — thiếu là lỗi tool, không phải rơi nhánh;
  2. danh sách đóng, khai ở đây;
  3. làn `main` CŨNG mang nhãn — nhờ vậy mọi làn đều grep được từ `ps`
     (`--output-dir /tmp/viper-df/<nhãn>`), và trình duyệt không nhãn chỉ còn đúng một
     nguồn hợp lệ là server `browser` của phiên chính;
  4. dấu ở tầng trang (`window.name`), kiểm ở mỗi lượt gọi — xem `scenario.py`;
  5. một client một nhãn, vĩnh viễn: hàm dưới từ chối trao client của làn này cho làn kia.

Tên làn là tên KHE dogfood của quy trình VIPER (`viper-user-1..3` ↔ `slot-1..3`), không phải
nghiệp vụ sản phẩm. Khe nhận vai theo đợt (`context/DOGFOOD-PLAN.md`), nên tài khoản đi theo
VAI chứ không theo làn: kịch bản ghi thẳng `<vai>@dogfood.local`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator

from .client import LaneClient
from .rpc import RpcError, log

WIDE = "1280x800"
NARROW = "390x844"

# tag → viewport mặc định. Nhãn luôn là `viperdf-<tag>` để khớp `--output-dir
# /tmp/viper-df/<nhãn>` mà gateway đặt. Vai cần màn hẹp (`mobile`, …) truyền `viewport`
# cho từng lượt df_run_scenario — làn là khe, không biết vai nào đang ngồi trong nó.
LANES: dict[str, str] = {
    "slot-1": WIDE,
    "slot-2": WIDE,
    "slot-3": WIDE,
    "main": WIDE,
}

DEFAULT_LABEL_PREFIX = "viperdf-"

# Làn không ai gọi quá ngần này giây thì ĐÓNG gateway (trình duyệt ở đầu kia chết theo); lượt gọi sau
# tự mở lại. Hub sống bằng đời phiên Claude — tới 10 giờ — nên bản không hạn giữ mọi Chromium tới
# hết phiên: bảy gateway bỏ lại 9–13 giờ từng làm máy chạy cụm cạn RAM (15/15 GB), Docker treo
# (đo ở một dự án VIPER, 2026-09-29). Đặt `DF_LANE_IDLE_S=0` để giữ mãi.
DEFAULT_IDLE_S = 900.0


def idle_from_env() -> float:
    try:
        return max(0.0, float(os.environ.get("DF_LANE_IDLE_S", DEFAULT_IDLE_S)))
    except ValueError:
        return DEFAULT_IDLE_S


def label_of(lane: str, prefix: str = DEFAULT_LABEL_PREFIX) -> str:
    return prefix + lane


class LaneRegistry:
    """Bản đồ làn → client, cộng khoá tuần tự hoá theo TÊN LUỒNG.

    Khoá luồng sống ở đây chứ không ở `flows.py` vì nó dùng chung cho mọi làn: một luồng
    khai `serialize` thì không hai làn nào được chạy nó chồng nhau. Hub biết «luồng này
    không được chạy chồng», KHÔNG biết vì sao — lý do nằm cạnh dữ liệu trong chính file
    luồng (`context/dogfood/flows/<tên>.json`).
    """

    def __init__(
        self,
        label_prefix: str = DEFAULT_LABEL_PREFIX,
        viewports: dict[str, str] | None = None,
        idle_s: float | None = None,
    ) -> None:
        self.label_prefix = label_prefix
        # Khung nhìn mặc định theo làn: bảng ở trên là mặc định của hạ tầng; dự án ghi đè qua
        # `lane_viewports` ở context/dogfood/config.json — PERSONAS.md §3 quyết, hub không biết vì sao.
        self.viewports: dict[str, str] = {**LANES, **{k: v for k, v in (viewports or {}).items() if k in LANES}}
        self._clients: dict[str, LaneClient] = {}
        self._flow_locks: dict[str, asyncio.Lock] = {}
        self._flow_last_run: dict[str, float] = {}
        self.idle_s = idle_from_env() if idle_s is None else idle_s
        self._last_use: dict[str, float] = {}
        self._busy: dict[str, int] = {}
        self._reaper: asyncio.Task[None] | None = None

    def check(self, lane: str | None) -> str:
        if not lane:
            raise RpcError(
                "thiếu `tag`. Mỗi vai phải khai làn của mình — không có mặc định, vì "
                f"rơi vào làn chung là hai vai dùng chung một trình duyệt. Làn hợp lệ: "
                f"{' · '.join(sorted(LANES))}"
            )
        if lane not in LANES:
            raise RpcError(f"`tag` lạ: {lane!r}. Làn hợp lệ: {' · '.join(sorted(LANES))}")
        return lane

    async def client(self, lane: str, *, viewport: str | None = None) -> LaneClient:
        lane = self.check(lane)
        existing = self._clients.get(lane)
        if existing is not None:
            return existing
        client = LaneClient(lane=lane, label=label_of(lane, self.label_prefix), viewport=viewport or self.viewports[lane])
        self._clients[lane] = client
        self._last_use[lane] = time.monotonic()
        if self._reaper is None and self.idle_s > 0:
            self._reaper = asyncio.create_task(self._reap_loop(), name="lane-reaper")
        await client.ensure_started()
        return client

    @contextlib.asynccontextmanager
    async def use(self, lane: str) -> AsyncIterator[LaneClient]:
        """Mượn client của làn cho MỘT lượt gọi — làn đang bận không bao giờ bị đóng vì rảnh."""
        self._busy[lane] = self._busy.get(lane, 0) + 1
        try:
            yield await self.client(lane)
        finally:
            self._busy[lane] -= 1
            self._last_use[lane] = time.monotonic()

    async def reset(self, lane: str) -> bool:
        """Đóng gateway của làn để lượt gọi sau mở một trình duyệt `--isolated` TRẮNG.

        Cùng nghĩa với `browser_close` đầu lượt của khe `pw-slot-k`: khe được dùng lại qua nhiều
        vai, không đóng thì vai sau đọc được cookie/localStorage của vai trước. Trả True nếu làn
        đang mở.
        """
        lane = self.check(lane)
        if self._busy.get(lane, 0):
            raise RpcError(f"làn {lane} đang bận một lượt gọi khác — không làm mới được giữa chừng")
        client = self._clients.pop(lane, None)
        if client is None:
            return False
        await client.close()
        return True

    async def reap_idle(self, now: float | None = None) -> list[str]:
        """Đóng mọi làn rảnh quá `idle_s`; trả tên các làn đã đóng."""
        now = time.monotonic() if now is None else now
        closed: list[str] = []
        for lane, client in list(self._clients.items()):
            if self._busy.get(lane, 0) or now - self._last_use.get(lane, now) < self.idle_s:
                continue
            self._clients.pop(lane, None)
            await client.close()
            closed.append(lane)
        return closed

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(min(60.0, self.idle_s / 2))
            for lane in await self.reap_idle():
                log(f"▸ làn {lane}: rảnh quá {int(self.idle_s)} s — đóng gateway (lượt gọi sau tự mở lại)")

    def flow_lock(self, name: str) -> asyncio.Lock:
        return self._flow_locks.setdefault(name, asyncio.Lock())

    def flow_last_run(self, name: str) -> float:
        return self._flow_last_run.get(name, 0.0)

    def mark_flow_run(self, name: str, when: float) -> None:
        self._flow_last_run[name] = when

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        for client in list(self._clients.values()):
            await client.close()
        self._clients.clear()
