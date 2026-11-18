#!/usr/bin/env python3
"""Bốn phép đo chạy TRONG TRANG, và phép đối chiếu chạy ở hub.

Hub giữ THUẬT TOÁN; chuẩn mực để đối chiếu là dữ liệu (đường dẫn lát giá trị thiết kế
truyền vào từ cấu hình). Nhờ vậy hub không biết gói design system nào tên gì.

Vì sao đối chiếu ở hub chứ không ở trang: byte đi giữa hub và trình duyệt KHÔNG tốn ngữ
cảnh của model — chỉ byte hub trả lên mới tốn. Nên cứ để trang trả danh sách thô, hub lọc,
model chỉ nhận VI PHẠM. Trước đây chính danh sách thô ấy đi thẳng vào ngữ cảnh: mọi cặp
`thuộc-tính|giá-trị @ selector` của mọi phần tử, đo được 42 843 byte cho một màn.

Thứ tự đọc kết quả: HÌNH HỌC trước. Bốn con số màu vẫn đúng hết khi cả trang bị một tấm
ảnh phủ kín — nét vẽ sai mà DOM đúng. Phép đo hình học bắt được chuyện đó.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ─────────────────────────────────────────────────────────── hình học của khung nhìn
# `center` phải là nội dung màn · `wide` chỉ được chứa khung bọc bố cục và lớp phủ hộp
# thoại ĐANG MỞ · `escaped` phải rỗng: phần tử `position:absolute` mà ô chứa không phải
# gốc toạ độ sẽ neo vào cả trang.
GEOMETRY_JS = """
(() => {
  const vw = innerWidth, vh = innerHeight, area = vw * vh;
  const name = (el) => el ? el.tagName.toLowerCase() + [...el.classList].map((c) => '.' + c).join('') : null;
  const covers = (el) => {
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0))
         * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
  };
  return {
    center: name(document.elementFromPoint(vw / 2, vh / 2)),
    wide: [...new Set([...document.querySelectorAll('body *')]
      .filter((el) => covers(el) > area * 0.6).map(name))],
    escaped: [...document.querySelectorAll('body *')].filter((el) => {
      const cs = getComputedStyle(el);
      if (cs.position !== 'absolute') return false;
      const par = el.offsetParent;
      if (!par || par === document.body) return true;
      const a = el.getBoundingClientRect(), b = par.getBoundingClientRect();
      return a.width > b.width * 1.5 || a.height > b.height * 1.5;
    }).map(name),
  };
})()
"""

# ───────────────────────────────────────────────────────────────── tương phản WCAG
# Nền thật: leo lên tổ tiên tới khi gặp nền đục, chồng dần các lớp trong suốt.
# Nền GRADIENT là một TẬP nền (mỗi điểm dừng màu một nền) — chấm theo điểm dừng XẤU NHẤT. Bản cũ chỉ đọc
# `background-color`, gradient có màu nền trong suốt nên nó leo tới `body` trắng và báo chữ trắng trên dải
# navy là 1,0:1 (R14, 2026-09-29). Nền ẢNH (`url(...)`) không biết điểm ảnh nào sau chữ ⇒ không đo, đếm
# riêng vào `skipped` thay vì đoán.
CONTRAST_JS = """
(() => {
  const lum = (r, g, b) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const parse = (c) => {
    const m = String(c).match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)(?:,\\s*([\\d.]+))?\\)/);
    return m ? { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] } : null;
  };
  // Chồng lớp `top` lên `bottom` (cả hai có thể trong suốt) — phép «source-over».
  const over = (top, bottom) => {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    const ch = (k) => (top[k] * top.a + bottom[k] * bottom.a * (1 - top.a)) / a;
    return { r: ch('r'), g: ch('g'), b: ch('b'), a };
  };
  const WHITE = { r: 255, g: 255, b: 255, a: 1 };
  const pageBase = () => {
    const body = parse(getComputedStyle(document.body).backgroundColor);
    return body && body.a >= 1 ? body : WHITE;
  };
  // Mảng nền đục có thể nằm sau chữ; null = nền ảnh, không đo được.
  const bgsOf = (el) => {
    let acc = null;
    const onto = (bases) => (acc ? bases.map((b) => over(acc, b)) : bases);
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const own = parse(cs.backgroundColor);
      const img = cs.backgroundImage;
      if (img && img !== 'none') {
        if (/url\\(/.test(img) || !/gradient\\(/.test(img)) return null;
        const stops = (img.match(/rgba?\\([^)]*\\)/g) || []).map(parse).filter(Boolean);
        if (!stops.length) return null;
        // `background-color` của CHÍNH phần tử nằm dưới gradient; trong suốt thì nhìn xuyên xuống tổ tiên.
        let under = own && own.a >= 1 ? [own] : (n.parentElement ? bgsOf(n.parentElement) : [pageBase()]);
        if (!under) return null;
        if (own && own.a > 0 && own.a < 1) under = under.map((b) => over(own, b));
        return onto(stops.flatMap((s) => under.map((b) => over(s, b))));
      }
      if (own && own.a >= 1) return onto([own]);
      if (own && own.a > 0) acc = acc ? over(acc, own) : own;
    }
    return onto([pageBase()]);
  };
  const ratioOf = (f, bg) => (Math.max(lum(f.r, f.g, f.b), lum(bg.r, bg.g, bg.b)) + 0.05)
                           / (Math.min(lum(f.r, f.g, f.b), lum(bg.r, bg.g, bg.b)) + 0.05);
  const seen = new Set(), fails = [];
  let skipped = 0;
  document.querySelectorAll('body *').forEach((el) => {
    if (el.children.length > 0 || !el.textContent.trim()) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    const fg = parse(cs.color);
    if (!fg) return;
    const bgs = bgsOf(el);
    if (!bgs) { skipped += 1; return; }
    const ratio = Math.min(...bgs.map((bg) => ratioOf(over(fg, bg), bg)));
    const px = parseFloat(cs.fontSize) || 16;
    const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
    // Chữ lớn theo WCAG: >=24px, hoặc >=18.66px khi in đậm.
    const need = (px >= 24 || (bold && px >= 18.66)) ? 3.0 : 4.5;
    const key = cs.color + '|' + JSON.stringify(bgs.map((b) => [b.r, b.g, b.b].map(Math.round))) + '|' + need;
    if (seen.has(key)) return;
    seen.add(key);
    if (ratio < need) fails.push({
      text: el.textContent.trim().slice(0, 40),
      color: cs.color, size: cs.fontSize,
      ratio: +ratio.toFixed(2), need,
      ...(bgs.length > 1 ? { bg: 'gradient' } : {}),
    });
  });
  return { pairs: seen.size, fails, skipped };
})()
"""

# ────────────────────────────────────────────── giá trị hình thức ĐANG RENDER (thô)
# Trả «thuộc-tính|giá-trị @ selector đầu tiên dùng nó». Hub lọc sau — xem `diff_values`.
RENDER_VALUES_JS = """
(() => {
  const seen = new Map();
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const sel = el.tagName.toLowerCase()
      + (el.id ? '#' + el.id : '')
      + (typeof el.className === 'string' && el.className.trim()
         ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
    for (const p of ['color', 'background-color', 'border-color', 'font-family', 'font-size',
                     'font-weight', 'padding', 'gap', 'border-radius', 'box-shadow']) {
      const v = cs.getPropertyValue(p);
      if (!v || v === 'none' || v === 'rgba(0, 0, 0, 0)' || v === '0px' || v === 'normal') continue;
      const k = p + '|' + v;
      if (!seen.has(k)) seen.set(k, sel);
    }
  }
  return [...seen].map(([k, sel]) => k + ' @ ' + sel).sort();
})()
"""

# ─────────────────────────────────────────────────────────────────── vùng chạm nhỏ
# Control gốc hệ điều hành (input/select chưa tạo kiểu) VẪN ĐẾM, chỉ gắn cờ `native` —
# bỏ qua chúng là bỏ lọt cả một loại hỏng.
TAP_TARGETS_JS = """
((min) => {
  const bad = [];
  document.querySelectorAll('button, a, [role=button], input, select, [tabindex]').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    if (r.height < min) bad.push({
      text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30),
      h: Math.round(r.height), w: Math.round(r.width),
      native: el.tagName === 'INPUT' || el.tagName === 'SELECT',
    });
  });
  return bad.slice(0, 20);
})(__MIN__)
"""


def tap_targets_js(min_height: float) -> str:
    return TAP_TARGETS_JS.replace("__MIN__", json.dumps(min_height))


# ───────────────────────────────────────────────── đối chiếu lát giá trị (ở hub)

_VALUE_SPLIT = re.compile(r"^(?P<prop>[a-z-]+)\|(?P<value>.*?) @ (?P<sel>.*)$")

# Thuộc tính mà `getComputedStyle` trả dạng VIẾT GỘP (`padding: 8px 16px`, `gap: 12px 8px`,
# `border-radius: 8px 8px 0px 0px`) trong khi lát thiết kế giữ từng giá trị đơn (`space.sm = 8px`).
# So nguyên chuỗi thì mọi padding hai chiều đều thành «giá trị lạ» dù từng số đều nằm trong lát (R14,
# 2026-09-29) — nên tách từng thành phần, chỉ báo thành phần ngoài lát.
_SHORTHAND_PROPS = {"padding", "margin", "gap", "border-radius"}


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    """`{"color": {"bg": "#fff"}}` → `{"color.bg": "#fff"}`."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif node is not None:
        out[prefix] = str(node)
    return out


def _normalise(value: str) -> str:
    """So sánh giá trị CSS bằng dạng chuẩn hoá nhẹ: bỏ khoảng trắng thừa, chữ thường."""
    return re.sub(r"\s+", " ", value.strip().lower())


def _colour_forms(value: str) -> set[str]:
    """`#aabbcc` và `rgb(170, 187, 204)` là một giá trị — trình duyệt luôn trả dạng rgb."""
    forms = {_normalise(value)}
    match = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value.strip(), re.IGNORECASE)
    if match:
        hexa = match.group(1)
        if len(hexa) == 3:
            hexa = "".join(ch * 2 for ch in hexa)
        r, g, b = (int(hexa[i : i + 2], 16) for i in (0, 2, 4))
        forms.add(f"rgb({r}, {g}, {b})")
    return forms


def load_slice(path: str | Path) -> dict[str, set[str]]:
    """Đọc một lát giá trị thiết kế (`*.json`) → `{tên khoá: {các dạng viết}}`."""
    file = Path(path)
    if not file.is_absolute():
        file = REPO_ROOT / file
    if not file.exists():
        raise FileNotFoundError(f"không thấy lát giá trị thiết kế ở {file}")
    flat = _flatten(json.loads(file.read_text("utf-8")))
    return {key: _colour_forms(value) for key, value in flat.items()}


def diff_values(rendered: list[str], slices: dict[str, dict[str, set[str]]], *, primary: str) -> dict[str, list[str]]:
    """Lọc danh sách thô thành VI PHẠM.

    `slices` là `{tên lát: lát}`; `primary` là lát mà màn này phải theo. Giá trị không có
    ở lát chính nhưng CÓ ở một lát khác của repo là **trộn hai bộ giá trị thiết kế** —
    phát hiện nặng hơn hẳn «giá trị lạ», nên tách riêng.
    """
    allowed = {form for forms in slices.get(primary, {}).values() for form in forms}
    others = {
        name: {form for forms in table.values() for form in forms}
        for name, table in slices.items()
        if name != primary
    }
    unknown: list[str] = []
    mixed: list[str] = []
    for entry in rendered:
        match = _VALUE_SPLIT.match(entry)
        if not match:
            continue
        value = _normalise(match.group("value"))
        if value in allowed:
            continue
        parts = [value]
        if match.group("prop") in _SHORTHAND_PROPS:
            parts = [p for p in re.split(r"[\s/]+", value) if p and p != "0px" and p not in allowed]
            if not parts:
                continue
        where = [name for name, table in others.items() if all(p in table for p in parts)]
        note = f"  ← ngoài lát: {' '.join(parts)}" if parts != [value] else ""
        (mixed if where else unknown).append(entry + (f"  ← thuộc lát {', '.join(where)}" if where else note))
    return {"unknown": unknown, "mixed": mixed}
