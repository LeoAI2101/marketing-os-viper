"""Dựng nội dung file TRƯỚC/SAU một thao tác ghi từ payload hook PreToolUse.

Dùng chung cho các bộ luật của `scripts/guard_write.py` (data-lifecycle, data-security,
query-safety): mỗi bộ so lỗi của bản sau với bản trước, chỉ tính lỗi MỚI. Nhận
Write/Edit/MultiEdit của Claude Code và patch dạng `*** Begin Patch` (Codex apply_patch).
Tách từ lib/ds_check.py của driverplus-viper — phần design system ở VIPER nằm riêng trong
scripts/guard_ds.py.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# VIPER_GUARD_ROOT: test dựng repo mẫu tạm; mặc định là gốc repo này.
ROOT = Path(os.environ.get("VIPER_GUARD_ROOT") or Path(__file__).resolve().parents[2])
PATCH_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")


class FileChange:
    """Một file trước/sau tool call; ``after=None`` nghĩa là bị xoá."""

    def __init__(self, rel: str, before: str | None, after: str | None):
        self.rel = rel
        self.before = before
        self.after = after


def written_text(tool_input: dict, rel: str) -> str | None:
    """Nội dung file SAU khi tool ghi xong.

    Write: `content` đã là cả file. Edit/MultiEdit: dựng lại nội dung sau khi edit từ
    file trên đĩa — soi mỗi fragment `new_string` rời thì thiếu ngữ cảnh, sửa giá
    trị token NGAY TRONG :root cũng bị bắt oan là "mã màu thô ngoài :root", trong
    khi đó chính là thao tác spec khuyến khích (phản hồi của Authority → sửa MỘT
    token). Không dựng lại được (file chưa tồn tại, old_string không khớp) → None:
    chính lệnh Edit cũng sẽ lỗi ở tầng tool, không có gì sắp được ghi — fail-open.
    """
    if "content" in tool_input:
        return str(tool_input.get("content") or "")
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    try:
        text = (ROOT / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for e in edits:
        if not isinstance(e, dict):
            continue
        old = str(e.get("old_string") or "")
        if not old:
            continue
        if old not in text:
            return None
        new = str(e.get("new_string") or "")
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def relative_path(raw_path: str) -> str | None:
    """Chuẩn hoá path từ hook/patch và chặn path thoát khỏi project root."""
    if not raw_path.strip():
        return None
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = ROOT / path
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def disk_text(rel: str) -> str | None:
    """Đọc file UTF-8; None phân biệt file không tồn tại với file rỗng."""
    try:
        path = ROOT / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def find_lines(haystack: list[str], needle: list[str], start: int,
               *, require_eof: bool = False) -> int | None:
    """Tìm context của một hunk, exact trước rồi mới tolerant như apply_patch.

    Nhánh tolerant chỉ bỏ khoảng trắng hai đầu để nhận patch có indentation context
    hơi khác. Khi thay, dòng context thật từ file vẫn được giữ nguyên, nên tolerance
    không âm thầm reformat nội dung.
    """
    if not needle:
        return len(haystack) if require_eof else min(start, len(haystack))
    last = len(haystack) - len(needle)
    candidates = range(max(0, start), last + 1)
    for i in candidates:
        if haystack[i:i + len(needle)] == needle and (not require_eof or i + len(needle) == len(haystack)):
            return i
    for i in range(max(0, start), last + 1):
        if ([line.strip() for line in haystack[i:i + len(needle)]]
                == [line.strip() for line in needle]
                and (not require_eof or i + len(needle) == len(haystack))):
            return i
    return None


def apply_update_patch(text: str, body: list[str]) -> str | None:
    """Áp một Update block lên text mà không ghi đĩa; không chắc thì fail-open.

    Đây là subset đầy đủ cho format apply_patch của Codex: nhiều hunk, header `@@`
    có hoặc không có anchor, context/add/delete line, và `*** End of File`.
    `*** Move to:` cố tình chưa nhận vì delta của move cần baseline ở path nguồn;
    đoán sai sẽ chặn oan một rename hợp lệ.
    """
    if any(line.startswith("*** Move to:") for line in body):
        return None

    source = text.splitlines()
    trailing_newline = text.endswith(("\n", "\r"))
    cursor = 0
    i = 0
    saw_hunk = False

    while i < len(body):
        header = body[i]
        if not header.startswith("@@"):
            return None
        saw_hunk = True
        hint = header[2:].strip()
        i += 1

        ops: list[tuple[str, str]] = []
        require_eof = False
        while i < len(body) and not body[i].startswith("@@"):
            line = body[i]
            i += 1
            if line == "*** End of File":
                require_eof = True
                if i < len(body) and not body[i].startswith("@@"):
                    return None
                break
            if not line or line[0] not in " +-":
                return None
            ops.append((line[0], line[1:]))

        old = [line for tag, line in ops if tag in " -"]
        search_from = cursor
        # `@@ def foo():` là skip-ahead anchor của apply_patch, không phải unified
        # diff range. Nếu không tìm thấy anchor, vẫn thử context từ cursor.
        if hint and not re.fullmatch(r"-?\d+(?:,\d+)?(?:\s+\+\d+(?:,\d+)?)?\s*@@?", hint):
            anchor = find_lines(source, [hint], cursor)
            if anchor is not None:
                search_from = anchor + 1

        pos = find_lines(source, old, search_from, require_eof=require_eof)
        if pos is None and search_from != cursor:
            pos = find_lines(source, old, cursor, require_eof=require_eof)
        if pos is None:
            return None

        replacement: list[str] = []
        src_i = pos
        for tag, line in ops:
            if tag == "+":
                replacement.append(line)
                continue
            if src_i >= len(source):
                return None
            actual = source[src_i]
            if actual != line and actual.strip() != line.strip():
                return None
            if tag == " ":
                replacement.append(actual)
            src_i += 1

        source[pos:src_i] = replacement
        cursor = pos + len(replacement)

    if not saw_hunk:
        return None
    result = "\n".join(source)
    return result + ("\n" if trailing_newline else "")


def parse_apply_patch(command: str) -> list[FileChange] | None:
    """Dựng final state cho mọi file trong một Codex apply_patch command.

    Nhiều block chạm cùng file được áp tuần tự. Kết quả chỉ giữ một change/file,
    với ``before`` từ trước tool call và ``after`` sau block cuối cùng.
    """
    lines = command.splitlines()
    try:
        begin = lines.index("*** Begin Patch")
        end = len(lines) - 1 - lines[::-1].index("*** End Patch")
    except ValueError:
        return None
    if begin >= end or any(line.strip() for line in lines[:begin] + lines[end + 1:]):
        return None

    states: dict[str, str | None] = {}
    befores: dict[str, str | None] = {}
    order: list[str] = []

    def current(rel: str) -> str | None:
        if rel not in states:
            states[rel] = disk_text(rel)
            befores[rel] = states[rel]
            order.append(rel)
        return states[rel]

    i = begin + 1
    while i < end:
        match = PATCH_HEADER.match(lines[i])
        if match is None:
            return None
        kind, raw_path = match.groups()
        rel = relative_path(raw_path)
        if rel is None:
            return None
        i += 1
        body: list[str] = []
        while i < end and PATCH_HEADER.match(lines[i]) is None:
            body.append(lines[i])
            i += 1

        before_block = current(rel)
        if kind == "Add":
            if before_block is not None or any(not line.startswith("+") for line in body):
                return None
            states[rel] = "\n".join(line[1:] for line in body) + ("\n" if body else "")
        elif kind == "Delete":
            if before_block is None or body:
                return None
            states[rel] = None
        else:
            if before_block is None:
                return None
            destination = None
            if body and body[0].startswith("*** Move to: "):
                destination = relative_path(body.pop(0)[len("*** Move to: "):])
                if destination is None or destination == rel or current(destination) is not None:
                    return None
            updated = apply_update_patch(before_block, body) if body else before_block if destination else None
            if updated is None:
                return None
            if destination:
                states[rel] = None
                states[destination] = updated
            else:
                states[rel] = updated

    return [FileChange(rel, befores[rel], states[rel]) for rel in order]


def legacy_change(tool_input: dict) -> FileChange | None:
    """Adapter schema Write/Edit/MultiEdit cũ."""
    rel = relative_path(str(tool_input.get("file_path") or ""))
    if rel is None:
        return None
    after = written_text(tool_input, rel)
    if after is None:
        return None
    return FileChange(rel, disk_text(rel), after)

