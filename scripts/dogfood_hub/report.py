#!/usr/bin/env python3
"""MỌI chuỗi tiếng Việt của hub sống ở đây.

Luật #7 chia hai ngôn ngữ hai chỗ: chữ cho người đọc là tiếng Việt CÓ DẤU, thứ máy đọc
(tên động từ, khoá JSON, tên file) là tiếng Anh. Gom chuỗi vào một module là cách rẻ nhất
để hook `check_lang.py` không bắt gặp tiếng Việt lẫn trong module xử lý — và để đọc lại
toàn bộ giọng văn của hub trong một lần mở file.

Nhật ký phải GỌN nhưng không được che mất quan sát thật: mỗi dòng `✗` luôn mang theo thứ
nhìn thấy trên màn, vì đó chính là bằng chứng của luật #8b.
"""

from __future__ import annotations

from typing import Any

TRUNCATED = "… (cắt bớt)"


def _ms(value: Any) -> str:
    try:
        return f"{int(value) / 1000:.1f}s"
    except (TypeError, ValueError):
        return "?"


def scenario_log(result: dict[str, Any], *, lane: str, base: str, screens: dict[str, str | None],
                 label: str = "") -> str:
    """Kết quả thô từ đầu kia → nhật ký cho người đọc."""
    if result.get("fatal") == "lane_cross_talk":
        detail = (result.get("steps") or [{}])[0].get("detail", "")
        return (
            f"✗ LẪN LÀN — {detail}.\n"
            f"  Làn {lane} đang dùng trình duyệt của làn khác. Dừng vai này lại và đọc lại định "
            f"nghĩa vai: mỗi vai chỉ được gọi df_* với `tag` của chính mình."
        )

    steps = result.get("steps") or []
    total = sum(int(s.get("ms") or 0) for s in steps)
    head = f"làn {lane} · {base} · {len(steps)} bước · {_ms(total)}"
    lines = [("KỊCH BẢN ✓ XONG" if result.get("ok") else "KỊCH BẢN ✗ DỪNG Ở BƯỚC HỎNG"), head, ""]

    for step in steps:
        mark = "✓" if step.get("ok") else "✗"
        num = f"{int(step.get('i', 0)):02d}"
        verb = step.get("verb", "?")
        detail = str(step.get("detail") or "")
        screen = ""
        if verb == "goto":
            code = screens.get(str(step.get("detail", "")).split(" ")[0])
            screen = f"  [{code}]" if code else "  [chưa gán mã màn]"
        lines.append(f"{mark} {num} {verb}{screen}  {detail}  {_ms(step.get('ms'))}".rstrip())
        if not step.get("ok"):
            if step.get("around"):
                lines.append(f"     DOM gần đó: {step['around']}")
            if step.get("shot"):
                lines.append(f"     ảnh: {step['shot']}")

    errors = result.get("consoleErrors") or []
    if errors:
        seen: list[str] = []
        for err in errors:
            if err not in seen:
                seen.append(err)
        lines.append("")
        lines.append(f"— lỗi console ({len(errors)} lượt, {len(seen)} loại): " + " | ".join(seen[:5]))

    measures = result.get("measures") or {}
    for key, got in measures.items():
        lines.append("")
        lines.append(f"— đo «{key}» · {got.get('count', 0)} phần tử khớp:")
        for row in (got.get("rows") or [])[:20]:
            css = " · ".join(f"{k}: {v}" for k, v in (row.get("css") or {}).items())
            lines.append(f"    {row.get('tag', '')} [{row.get('box')} @ {row.get('at')}] «{row.get('text', '')}»")
            lines.append(f"      {css}")

    visited = result.get("visited") or []
    if visited:
        codes = [screens.get(p) or "?" for p in visited]
        lines.append(f"— màn đã mở: {' '.join(codes)}")
    shots = result.get("shots") or []
    if shots:
        lines.append(f"— ảnh ({len(shots)}) ở máy chạy trình duyệt: {', '.join(shots[:5])}")
    lines.append(f"— URL cuối: {result.get('url', '')}")
    return "\n".join(lines)


def audit_report(
    *,
    lane: str,
    url: str,
    geometry: dict[str, Any] | None,
    contrast: dict[str, Any] | None,
    values: dict[str, list[str]] | None,
    tap_targets: list[dict[str, Any]] | None,
    tap_min: float,
    console: list[str] | None,
    slice_name: str | None,
) -> str:
    """Chỉ in VI PHẠM. Sạch thì nói một câu là sạch — đó là thứ đếm được ở sổ cuối vòng."""
    lines = [f"soi màn — làn {lane} · {url}"]

    if geometry is not None:
        problems = []
        center = geometry.get("center")
        wide = geometry.get("wide") or []
        escaped = geometry.get("escaped") or []
        if not center:
            problems.append("tâm khung nhìn KHÔNG có phần tử nào — trang trống hoặc chưa vẽ xong")
        if wide:
            problems.append(f"khối phủ hơn 60% khung nhìn: {', '.join(wide[:6])}")
        if escaped:
            problems.append(f"phần tử neo nhầm ô chứa: {', '.join(escaped[:6])}")
        if problems:
            lines.append("HÌNH HỌC ✗ " + " · ".join(problems))
            lines.append(f"  (tâm đang là {center})")
        else:
            lines.append(f"HÌNH HỌC ✓ tâm là {center}, không khối nào phủ quá 60%, không phần tử nào neo nhầm")

    if contrast is not None:
        fails = contrast.get("fails") or []
        if fails:
            lines.append(f"TƯƠNG PHẢN ✗ {len(fails)}/{contrast.get('pairs', '?')} cặp hụt chuẩn:")
            for item in fails[:8]:
                on = " trên gradient (điểm dừng xấu nhất)" if item.get("bg") == "gradient" else ""
                lines.append(
                    f"  «{item.get('text', '')}» {item.get('color')} cỡ {item.get('size')}{on} "
                    f"= {item.get('ratio')} (cần {item.get('need')})"
                )
            if len(fails) > 8:
                lines.append(f"  {TRUNCATED} còn {len(fails) - 8} cặp")
        else:
            lines.append(f"TƯƠNG PHẢN ✓ {contrast.get('pairs', '?')} cặp đều đạt")
        if contrast.get("skipped"):
            lines.append(f"  ({contrast['skipped']} chữ nằm trên nền ẢNH — không đo được, soi bằng mắt)")

    if values is not None:
        unknown, mixed = values.get("unknown", []), values.get("mixed", [])
        where = f" (lát {slice_name})" if slice_name else ""
        if mixed:
            lines.append(f"TRỘN BỘ GIÁ TRỊ THIẾT KẾ ✗ {len(mixed)} giá trị{where} — nặng:")
            lines.extend(f"  {item}" for item in mixed[:10])
        if unknown:
            lines.append(f"GIÁ TRỊ NGOÀI LÁT ✗ {len(unknown)}{where}:")
            lines.extend(f"  {item}" for item in unknown[:20])
            if len(unknown) > 20:
                lines.append(f"  {TRUNCATED} còn {len(unknown) - 20} giá trị")
        if not mixed and not unknown:
            lines.append(f"GIÁ TRỊ HÌNH THỨC ✓ mọi giá trị đang render đều nằm trong lát{where}")

    if tap_targets is not None:
        if tap_targets:
            lines.append(f"VÙNG CHẠM ✗ {len(tap_targets)} phần tử thấp hơn {tap_min:g}px:")
            for item in tap_targets[:10]:
                native = " (control gốc hệ điều hành)" if item.get("native") else ""
                lines.append(f"  «{item.get('text', '')}» {item.get('w')}×{item.get('h')}px{native}")
        else:
            lines.append(f"VÙNG CHẠM ✓ không phần tử nào thấp hơn {tap_min:g}px")

    if console is not None:
        lines.append(f"LỖI CONSOLE ({len(console)}): " + (" | ".join(console[:5]) if console else "không có"))

    return "\n".join(lines)


def probe_report(rows: list[dict[str, Any]]) -> str:
    """Bảng của `df_api_probe`: CHỈ hàng lệch kỳ vọng, cộng một dòng tổng."""
    off = [r for r in rows if r.get("mismatch")]
    lines = [f"đã bắn {len(rows)} lượt · {len(off)} lượt lệch kỳ vọng"]
    for row in off:
        lines.append(
            f"  {row.get('method', ''):6} {str(row.get('path', ''))[:60]:60} → {row.get('status')} "
            f"(kỳ vọng {row.get('expect')})  {str(row.get('message', ''))[:100]}"
        )
    if not off:
        lines.append("  mọi lượt đều đúng kỳ vọng")
    return "\n".join(lines)


MISSING_TAG = (
    "thiếu `tag`. Mỗi vai phải khai làn của mình — không có mặc định, vì rơi vào làn chung "
    "là hai vai dùng chung một trình duyệt."
)
