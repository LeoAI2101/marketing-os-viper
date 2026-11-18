#!/usr/bin/env python3
"""Sổ ghi-thêm của một vòng dogfood: phủ màn · byte trên dây · lượt dùng cửa thoát.

Ba sổ, cùng một dạng ndjson, cùng một thư mục `.viper/dogfood/l<N>/`:

    coverage.jsonl      mỗi lượt điều hướng một dòng → bảng «sổ phủ màn» của Bước 3b
    wire.jsonl          mỗi lượt gọi tool một dòng   → phép đo tiết kiệm token
    escape-hatch.jsonl  mỗi lượt `df_browser_code`   → soát xem DSL có đang yếu không

Vì sao phải là sổ máy ghi chứ không phải người nhớ: sổ phủ màn vòng 4 lập bằng tay, và đó
đúng là cách hai màn hỏng nặng lọt lưới — không vai nào mở, không ai nhận ra. Máy ghi thì
màn không ai mở vẫn hiện tên trong bảng.

Khoá JSON để tiếng Anh (luật #7) vì `.viper/**` cũng bị `check_lang.py` soi.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Ledger:
    def __init__(self, loop: int | str) -> None:
        self.dir = REPO_ROOT / ".viper" / "dogfood" / f"l{loop}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, row: dict[str, Any]) -> None:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}
        path = self.dir / name
        # Ba làn có thể ghi cùng lúc. Mở chế độ append rồi ghi TRỌN một dòng trong một lời
        # gọi `write` — ở kích cỡ này POSIX bảo đảm không xen dòng của nhau.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def visit(self, *, lane: str, path: str, screen: str | None, shot: str | None, geometry: str | None,
              via: str = "scenario") -> None:
        self._append("coverage.jsonl", {
            "lane": lane, "path": path, "screen": screen, "shot": shot,
            "geometry": geometry, "via": via,
        })

    def wire(self, *, tool: str, lane: str, steps: int, remote_calls: int,
             bytes_down: int, bytes_up: int, bytes_to_model: int, ms: int, role: str | None = None) -> None:
        """`bytes_to_model` là con số DUY NHẤT tốn ngữ cảnh; hai số kia chứng minh việc đã chạy thật."""
        self._append("wire.jsonl", {
            "tool": tool, "lane": lane, "role": role or "?", "steps": steps, "remote_calls": remote_calls,
            "bytes_to_playwright": bytes_down, "bytes_from_playwright": bytes_up,
            "bytes_to_model": bytes_to_model, "ms": ms,
        })

    def escape_hatch(self, *, lane: str, note: str, code_len: int) -> None:
        self._append("escape-hatch.jsonl", {"lane": lane, "note": note, "code_len": code_len})


def loop_number(config_general: dict[str, Any]) -> str:
    """Số vòng: biến môi trường thắng, rồi `context/dogfood/config.json`, rồi `Vòng : N` của STATE.md.

    Đọc STATE.md bằng ĐÚNG regex của `gate.py current_round()` — bản cũ chỉ đọc config.json và
    phải nhớ tăng tay mỗi vòng; quên một lần là sổ của vòng mới rơi vào thư mục vòng cũ.
    """
    given = os.environ.get("DF_DOGFOOD_LOOP") or config_general.get("loop")
    if given:
        return str(given)
    try:
        state = (REPO_ROOT / "STATE.md").read_text("utf-8")
    except OSError:
        return "0"
    m = re.search(r"Vòng\s*:\s*(\d+)", state)
    return m.group(1) if m else "0"
