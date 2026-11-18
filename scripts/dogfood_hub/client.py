#!/usr/bin/env python3
"""Hub tự làm CLIENT MCP: mỗi làn một tiến trình gateway, nói chuyện qua stdio.

Hub KHÔNG tự khởi động Chromium. Gateway mặc định là `scripts/dogfood_hub/gateway_local.sh`
(`@playwright/mcp --isolated` ngay trên máy chạy hub); máy chạy ứng dụng ở chỗ khác thì trỏ
`DF_GATEWAY` vào gateway của mình (ví dụ một ống `ssh -T` sang máy ấy) — cùng hợp đồng hai
đối số `<RỘNGxCAO> <nhãn>`. Hub chỉ dùng ĐÚNG MỘT tool của đầu kia: `browser_run_code_unsafe`. Cả một kịch bản được
biên dịch thành một hàm JavaScript rồi gửi xuống chạy một lượt — đó là chỗ tiết kiệm.

Hai cái bẫy đã trả giá ở chỗ khác, chặn ngay tại đây:

1. **Ống stderr không ai đọc sẽ đầy và CHẶN ĐỨNG tiến trình con.** Gateway và
   `@playwright/mcp` đều in vài dòng ra stderr (đồ hoạ, viewport, cảnh báo headless).
   Đọc lên sẽ là «hub treo ở lượt gọi đầu», không một dòng log nào. Vì thế mỗi tiến trình
   con có một task hút stderr chạy suốt đời nó.
2. **Đầu kia chết lặng.** Gateway thoát bằng mã khác 0 kèm hướng dẫn ra stderr khi thiếu
   điều kiện (không có npm, viewport sai dạng). Bắt lấy mã thoát và trả nguyên văn mấy dòng stderr cuối, thay vì
   để lời gọi treo tới lúc quá hạn.
"""

from __future__ import annotations

import asyncio
import collections
import os
import re
from pathlib import Path
from typing import Any

from .rpc import PROTOCOL_VERSION, Channel, RpcError, log

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY = os.environ.get("DF_GATEWAY") or "scripts/dogfood_hub/gateway_local.sh"

# Tool duy nhất của đầu kia mà hub dùng. Mọi động từ DSL đều biên dịch xuống đây.
REMOTE_TOOL = "browser_run_code_unsafe"

# Lượt gọi đầu của một làn phải trả tiền khởi động Chromium (lần đầu còn tải gói npm). Hạn
# riêng, rộng hơn hẳn, để «chậm» không bị đọc thành «hub chết».
FIRST_CALL_TIMEOUT = 120.0
CALL_TIMEOUT = 90.0
HANDSHAKE_TIMEOUT = 60.0

# Đầu kia hỏng theo kiểu nối lại được: phiên cũ chết, hoặc trang đang treo một hộp thoại
# (`browser_run_code_unsafe` TỪ CHỐI chạy khi còn modal — kể cả đoạn mã định đóng chính
# hộp thoại ấy). Làn coi như chết; đường ra rẻ nhất là nối lại một phiên mới, vì đầu kia
# chạy `--isolated` nên phiên mới là ngữ cảnh mới, hộp thoại cũ đi theo ngữ cảnh cũ.
# `kênh đã đóng` (rpc.py — gateway chết/bị giết) cũng phải nối lại được: bản trước chỉ bắt
# `đã đóng kênh` (thứ tự chữ khác) nên một làn có gateway đã chết là chết tới hết phiên, dù hub vẫn sống
# — dogfood vòng 10 giết gateway `picky` (throttle kẹt) rồi mọi lượt sau đều "kênh đã đóng".
RECOVERABLE = re.compile(
    r"session not found|not connected|modal state|target closed|đã đóng kênh|kênh đã đóng",
    re.IGNORECASE,
)


class LaneClient:
    """Một làn: một tiến trình gateway, một trình duyệt riêng ở đầu kia."""

    def __init__(self, *, lane: str, label: str, viewport: str) -> None:
        self.lane = lane
        self.label = label
        self.viewport = viewport
        self._proc: asyncio.subprocess.Process | None = None
        self._channel: Channel | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self._calls = 0
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- vòng đời

    async def ensure_started(self) -> None:
        if self._channel is not None:
            return
        async with self._lock:
            if self._channel is not None:
                return
            await self._spawn()

    async def _spawn(self) -> None:
        argv = ["bash", GATEWAY, self.viewport, self.label]
        log(f"▸ làn {self.lane}: mở gateway — {' '.join(argv)}")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(REPO_ROOT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Một thông điệp JSON-RPC của gateway là MỘT dòng stdout; mặc định 64 KB của
            # StreamReader thì một ảnh chụp cây/URL dài (vai `breaker` gõ 10.000 ký tự vào ô tìm
            # ⇒ `?q=` lên URL) làm làn chết hẳn: "Separator is found, but chunk is longer than
            # limit" (dogfood vòng 11, 2026-09-29). 16 MB đủ cho mọi phản hồi hub đang cắt ngắn.
            limit=16 * 1024 * 1024,
        )
        self._proc = proc
        assert proc.stdout is not None and proc.stdin is not None and proc.stderr is not None
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr), name=f"stderr:{self.lane}")

        channel = Channel(proc.stdout, proc.stdin, name=f"làn {self.lane}")
        channel.start()
        try:
            await channel.call(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "dogfood_hub", "version": "1.0.0"},
                },
                timeout=HANDSHAKE_TIMEOUT,
            )
            await channel.notify("notifications/initialized")
        except RpcError as exc:
            await self._teardown()
            raise RpcError(f"làn {self.lane}: bắt tay hỏng — {exc}\n{self._stderr_hint()}") from exc
        self._channel = channel
        self._calls = 0

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        """Hút stderr của tiến trình con suốt đời nó. Xem bẫy #1 ở đầu file."""
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            self._stderr_tail.append(text)
            log(f"  [{self.lane}] {text}")

    def _stderr_hint(self) -> str:
        if not self._stderr_tail:
            return "  (đầu kia không nói gì ra stderr)"
        return "\n".join(f"  | {line}" for line in self._stderr_tail)

    async def _teardown(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except TimeoutError:
                self._proc.kill()
        self._proc = None

    async def close(self) -> None:
        await self._teardown()

    # ------------------------------------------------------------------ lời gọi

    async def run_code(self, source: str) -> dict[str, Any]:
        """Chạy một hàm `async (page) => {…}` ở đầu kia, trả nguyên khối `result` của MCP.

        Hỏng theo kiểu nối lại được thì nối lại MỘT lần rồi thử lại — không lặp vô hạn,
        vì một làn cứ mọc lại là một làn trả tiền khởi động mỗi lượt và phép kiểm cô lập
        (`ps` theo nhãn làn) sẽ thấy pid nhảy liên tục.
        """
        await self.ensure_started()
        try:
            return await self._call_once(source)
        except RpcError as exc:
            if not RECOVERABLE.search(str(exc)):
                raise
            log(f"▲ làn {self.lane}: {exc} — nối lại một lần rồi thử lại")
            await self._teardown()
            await self.ensure_started()
            return await self._call_once(source)

    async def _call_once(self, source: str) -> dict[str, Any]:
        channel = self._channel
        if channel is None:
            raise RpcError(f"làn {self.lane}: chưa mở được gateway\n{self._stderr_hint()}")
        timeout = FIRST_CALL_TIMEOUT if self._calls == 0 else CALL_TIMEOUT
        self._calls += 1
        payload = await channel.call(
            "tools/call",
            {"name": REMOTE_TOOL, "arguments": {"code": source}},
            timeout=timeout,
        )
        if not isinstance(payload, dict):
            raise RpcError(f"làn {self.lane}: đầu kia trả kiểu lạ ({type(payload).__name__})")
        return payload

    async def list_tools(self) -> list[dict[str, Any]]:
        """Chỉ dùng cho `--probe` và phép đo catalog — không nằm trên đường chạy thật."""
        await self.ensure_started()
        channel = self._channel
        assert channel is not None
        payload = await channel.call("tools/list", {}, timeout=HANDSHAKE_TIMEOUT)
        return list((payload or {}).get("tools", []))


def text_of(payload: dict[str, Any]) -> str:
    """Gộp mọi khối `text` trong `result` của MCP thành một chuỗi."""
    return "\n".join(
        block.get("text", "")
        for block in (payload.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
