#!/usr/bin/env python3
"""Khung JSON-RPC 2.0 trên ndjson — một khung một dòng, dùng cho cả hai đầu của hub.

Hub vừa **là** server MCP (nói chuyện với phiên Claude Code qua stdin/stdout của chính
mình), vừa **là** client MCP (nói chuyện với `browser_gateway.sh` qua stdin/stdout của
tiến trình con). Hai vai ấy dùng chung đúng một khung dây, nên nó nằm ở đây.

⚠ stdout LÀ kênh dữ liệu. Một dòng chữ lạc vào đó là hỏng cả phiên, và đây là loại lỗi
  không để lại dấu vết nào đọc được. Mọi lời cho người đọc đi qua `log()` → stderr.
  Python còn một bẫy riêng: `sys.stdout` đệm dòng khi là ống, nên **mỗi khung gửi xong
  phải flush** — thiếu một lần flush là phiên treo mà không lỗi nào.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

# Bản giao thức khai lúc bắt tay. Ghim theo `@playwright/mcp@0.0.79` mà
# `scripts/remote/browser_stdio.sh` đang ghim — hai đầu phải cùng bản thì `tools/list`
# mới trả đúng hình dạng. Đổi bản ở kia thì đổi luôn ở đây, đừng đoán.
PROTOCOL_VERSION = "2024-11-05"

# Trần một khung. Playwright MCP trả cả cây accessibility khi bị gọi sai đường, và một
# khung vài megabyte làm nghẽn vòng lặp đọc; cắt ở đây để hỏng thành lỗi đọc được.
MAX_FRAME_BYTES = 16 * 1024 * 1024


def log(message: str) -> None:
    """In một dòng cho người đọc. LUÔN ra stderr — xem cảnh báo đầu file."""
    print(message, file=sys.stderr, flush=True)


class RpcError(RuntimeError):
    """Đầu kia trả `error`, hoặc khung sai hình dạng."""

    def __init__(self, message: str, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def encode(frame: dict[str, Any]) -> bytes:
    """Một khung → một dòng. `ensure_ascii=False` để chữ có dấu không phình 6 lần."""
    return (json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_FRAME_BYTES:
        raise RpcError(f"khung dài {len(line)} byte, quá trần {MAX_FRAME_BYTES}")
    try:
        frame = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RpcError(f"khung không phải JSON: {exc}") from exc
    if not isinstance(frame, dict):
        raise RpcError("khung JSON-RPC phải là object")
    return frame


def request(req_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def result(req_id: Any, payload: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# Mã lỗi JSON-RPC dùng tới
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class Channel:
    """Một đầu dây: đọc khung từ `reader`, ghi khung ra `writer`, khớp id với câu trả lời.

    Dùng cho phía CLIENT (hub gọi xuống gateway). Phía server của hub không cần lớp này
    vì nó chỉ đọc-xử lý-trả theo thứ tự đến.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, name: str) -> None:
        self._reader = reader
        self._writer = writer
        self._name = name
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._pump: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        self._pump = asyncio.create_task(self._read_loop(), name=f"rpc-read:{self._name}")

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    frame = decode(line)
                except RpcError as exc:
                    log(f"▲ {self._name}: bỏ qua một khung hỏng — {exc}")
                    continue
                req_id = frame.get("id")
                if req_id is None or req_id not in self._pending:
                    # Thông báo một chiều (`notifications/*`) hoặc câu trả lời đã quá hạn.
                    continue
                future = self._pending.pop(req_id)
                if future.done():
                    continue
                if "error" in frame:
                    err = frame["error"] or {}
                    future.set_exception(
                        RpcError(str(err.get("message", "lỗi không tên")), err.get("code"), err.get("data"))
                    )
                else:
                    future.set_result(frame.get("result"))
        except (asyncio.CancelledError, ConnectionResetError):
            raise
        finally:
            self._fail_pending("đầu kia đã đóng kênh")

    def _fail_pending(self, reason: str) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RpcError(reason))
        self._pending.clear()

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float) -> Any:
        if self._closed:
            raise RpcError(f"{self._name}: kênh đã đóng")
        self._next_id += 1
        req_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self._write(request(req_id, method, params))
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise RpcError(f"{self._name}: quá hạn {timeout:.0f}s khi gọi {method}") from exc

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write(notification(method, params))

    async def _write(self, frame: dict[str, Any]) -> None:
        self._writer.write(encode(frame))
        await self._writer.drain()

    async def close(self) -> None:
        self._closed = True
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, RpcError):
                pass
        try:
            self._writer.close()
        except RuntimeError:
            pass
