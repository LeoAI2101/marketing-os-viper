#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VIPER luật #7 — máy kiểm: tên là tiếng Anh, chữ cho người đọc là tiếng Việt CÓ DẤU.

Ba chế độ:

    python3 scripts/check_lang.py check    quét cả repo, ĐỎ khi còn bất kỳ chỗ nào (dùng trong `make check`)
    python3 scripts/check_lang.py report   liệt kê đầy đủ mọi khoá kèm vị trí, luôn exit 0
    python3 scripts/check_lang.py hook     đọc payload PreToolUse ở stdin, exit 2 để CHẶN thứ edit thêm vào

Thêm `--root <thư mục>` để soi một cây khác (đo bắt nhầm trên code tiếng Anh).

CÁCH NHẬN DIỆN — tách tên thành âm tiết theo `_`, `-`, camelCase và chữ số, rồi tra ba
tập âm tiết dựng từ dữ liệu:

    STRONG   âm tiết Việt thật, không phải từ tiếng Anh, gần như không xuất hiện trong
             tên định danh của code tiếng Anh
    VN_LEAN  trùng một từ tiếng Anh nhưng trong tên code nghiêng hẳn về nghĩa Việt
    EN_LEAN  trùng tiếng Anh và code tiếng Anh dùng nhiều

Thêm hai tập phụ: SINGLE_OK (tên trần một âm tiết mà code tiếng Anh vẫn khai báo) và
VN_PLAIN (từ tiếng Việt VỐN ĐÚNG khi viết không dấu — "sau", "trong", "pha", "hai";
kết tội chúng trong chữ là bắt nhầm chính tả đúng).

Là tên tiếng Việt khi có ≥1 âm tiết STRONG, hoặc mọi âm tiết thuộc VN_LEAN ∪ EN_LEAN và
có ≥2 âm tiết VN_LEAN khác nhau. Tên MỘT âm tiết chỉ bắt ở chỗ khai báo và ở tên file,
trừ tên viết HOA, tên có chữ số, và SINGLE_OK — những từ code tiếng Anh vẫn dùng làm
tên trần.

BA TẬP DỰNG TỪ DỮ LIỆU, KHÔNG GÕ TAY (2026-09-14):
    819 âm tiết rút từ 706 file tài liệu tiếng Việt CÓ DẤU của ba dự án VIPER trên máy
    (bỏ dấu rồi đếm; giữ âm tiết xuất hiện ≥5 lần và đúng hình âm tiết Việt) — trừ đi
    234 428 từ tiếng Anh của /usr/share/dict/words — rồi đối chiếu tần suất trong tên
    định danh của 4 882 file code tiếng Anh (Python stdlib + site-packages, JavaScript
    node_modules): có mặt ở ≥3 % số file → EN_LEAN, ≥0,4 % → VN_LEAN, còn lại → STRONG.
    SINGLE_OK = âm tiết có mặt ở ≥0,4 % số file với tư cách tên TRẦN một âm tiết.
    VN_PLAIN = 108 từ ASCII hay gặp trên 62 057 dòng tiếng Việt CÓ DẤU — tức những từ
    vốn viết đúng khi không dấu.
    Thêm từ nghiệp vụ là được và nên làm, nhưng ĐỔI TẬP ÂM TIẾT THÌ PHẢI ĐO LẠI CẢ HAI
    PHÍA — vẫn bắt được ví dụ vi phạm, và không bắt nhầm thêm trên một cây code tiếng
    Anh lớn (`check_lang.py report --root <cây đó>`). Xem `viper-rule7-naming.md` §3.5.

NGOẠI LỆ — chỉ hai, đều để lại vết:
    `lang-ok: <lý do>`       trên cùng dòng     → bỏ qua đúng dòng đó
    `lang-ok-file: <lý do>`  trong 10 dòng đầu  → bỏ qua NỘI DUNG file (không bỏ tên file)
Không có lý do thì dấu đó không có hiệu lực. Chỉ dùng cho dữ liệu thật sự phải là tiếng
Việt (tên múi giờ, bảng nguồn của khách hàng, phép thử gấp dấu) và cho dòng tài liệu nêu
ví dụ vi phạm.

KHÔNG SOI: `intake/` (tài liệu Authority đưa vào — dự án không được sửa, kể cả tên file),
`.claude/skills/security-audit/` (skill vendor nguyên vẹn từ Cloudflare, xem UPSTREAM.md),
file khoá phụ thuộc, file nhị phân, và chính file này.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

VERSION = "1.0"

# 420 âm tiết Việt thật — một cái là đủ để kết luận tên tiếng Việt
STRONG = frozenset("""
    ang anh bai banh bao bech benh bia biet bieu boc boi bua buc buoc buoi buong cac cach cai
    cang canh cao cau chan chem chenh cheo chep chiec chiem chieu chim chinh chiu choi chon
    chong chot chu chua chuan chuc chui chung chuoc chuoi chuong chuot chup chuyen coc coi cong
    cua cuc cung cuoc cuoi cuong cuu dac dai danh dat dau dem deu dia dien diep dieu dinh doan
    doi dua duc duoc duoi duong dut duyet gac gach ganh ghe ghep gia giac giai giam gian giang
    gianh giao giat giau giay giet gioi giong giu giua giup goc gom gop gui guong hai hang hanh
    hiem hien hieu hinh hoa hoac hoach hoai hoan hoang hoat hoc hom hua huong huu huy huyen kem
    kenh keo keu khac khach khai kham khang khao khap khau khien khieu khit kho khoa khoan
    khoang khoanh khoet khoi khong khop khuc khuech khuon khuyen khuyet kich kiem kien kieu kinh
    lach lanh lau lenh liec liet loai loang loat loi lon lua luan luat luoc luoi luon luong luot
    lup luu mach mai manh menh mep mieng minh moc muc mui muoi muon nac nai nang nao nem nen neu
    nga ngam ngan nganh ngat ngau ngay nghe nghi nghia nghiem nghien nghiep nghin ngo ngoac
    ngoai ngoi ngon ngop ngu ngung nguoc nguoi nguon nguong nha nhac nham nhanh nhap nhat nhay
    nhe nhet nhi nhich nhiem nhien nhieu nhin nhip nho nhoi nhom nhot nhu nhuc nhung nhuom
    nhuong niem nien noi nong nop nua nuoc nuoi nuot ong pha phach phai pham phan phang phap
    phat phau phay phe phep pheu phia phien phieu phim phinh phoi phong phuc phuong quang quen
    quoc quy quyen quyet rac rai ranh rao rieng rui ruoi ruot sach sanh sau siet soan soat soi
    som sua suat suc suong suot suyt tac tep tham thang thanh thao thap thay thi thich thien
    thiet thieu thoa thoai thoang thoat thoi thu thua thuat thuc thue thuong thut thuyet tich
    tiem tieng tiep tiet tieu tinh toan toc trac trach trai trang tranh trao trat tre tren trich
    trien triet trieu trinh tro troi trong tru truc trung truoc truong truot truu truyen tuc
    tuoi tuong tuy tuyen tuyet ung uoc vach vanh vao vap vay ven viec vien viet vinh voi vom von
    vong vua vuc vung vuong vuot vut xac xai xam xang xau xay xen xep xet xoa xuat xung xuoc
    xuoi xuong xuyen yeu
""".split())

# 262 âm tiết trùng từ tiếng Anh nhưng nghiêng nghĩa Việt — cần ≥2 cái khác nhau
VN_LEAN = frozenset("""
    ac am ao ap ay ba bac bach bam ban bang bat bay be ben bi bien binh bit bo bom bon bong bop
    bot bu but ca cam cap cat cay chac cham chang chao chap chat chay che chen chet chi chia
    chien chin cho chum chut con cot cu cum cuon cut da dam dan dang dao dap day de den dep di
    dich diem doat don dong dot du dung ep gam gan gang gap gat gay gi gio go goi gon got ha ham
    han hat hau hay he hen hep het ho hoi hon hong hop hot hu hung hut ich ke kep ket kha khan
    khu kin kip ky la lac lai lam lan lang lap lat lay le lech lich lien lieu lo loc lop lot luc
    lui lung ly ma mam man mang mao mat mau may mem meo met mien mo moi mon mong mot mu nam nan
    nap nay ne nep net ngai nguyen nhan nut om op phi pho phu phut qua quan quat quay quet ra
    rang rap rat ri ro roi rong rot rua rung rut sac sai san sang sap sat se so soc son song sot
    su ta tach tai tam tan tang tao tap tat te ten tha thai than them tho thong thuan thung
    thuoc ti tien tim tin toa toi tom ton tong tot tra tram tran tri tron tu tua tuan tung tut
    ty uu uy va vai van vang vat ve vet vi vo vu xa xe xo xu yen yet
""".split())

# 19 âm tiết mà code tiếng Anh dùng nhiều — không bao giờ tự nó kết tội
EN_LEAN = frozenset("""
    an can co do doc it len long mac min my no not num on re that the to
""".split())

# tên trần một âm tiết mà code tiếng Anh vẫn khai báo — miễn cho luật một âm tiết
SINGLE_OK = frozenset("""
    an bi can co day doc got it lang len lo loc long mat may no not num on op re roi so that the
    to tri tu
""".split())

# từ tiếng Việt VỐN ĐÚNG khi viết không dấu ("sau", "trong") — không kết tội trong chữ
VN_PLAIN = frozenset("""
    ac anh ba ban bao bay be bien bo ca cam canh cao cap chat che chen chi chia cho chung coi
    con cung danh di dot dung duyet gap gia gian giao go gom hai hay hoa huy khai khang kho
    khoang khu kinh lai lan lo loi lui ly ma man mang mau minh mong nang nay ngay nghe nghi
    nhanh nhu pha phong phu qua quan quay quen quy ra ranh ro sai sang sau so soi song ta tai
    tam ten tep tham thang thanh thao thay thi thu thua tin tra trang tranh trao trong trung ty
    vai vap vi xa xe xung
""".split())

# --------------------------------------------------------------------------------------
# 1. Âm tiết và nhận diện tên
# --------------------------------------------------------------------------------------

_MARK = "Mn"


def strip_accents(s: str) -> str:
    """Bỏ dấu tiếng Việt: 'nguồn' → 'nguon', 'Đơn' → 'Don'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != _MARK)


def has_diacritic(s: str) -> bool:
    """Có chữ tiếng Việt CÓ DẤU không (kể cả đ/Đ)."""
    if "đ" in s or "Đ" in s:
        return True
    return any(unicodedata.category(c) == _MARK for c in unicodedata.normalize("NFD", s))


_SPLIT_CASE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+")


def syllables(name: str) -> list[str]:
    """Tách tên thành âm tiết: 'soDong_23' → ['so', 'dong']. Âm tiết một chữ cái bị bỏ."""
    out: list[str] = []
    for part in re.split(r"[_\-0-9]+", strip_accents(name)):
        if part:
            out.extend(m.lower() for m in _SPLIT_CASE.findall(part))
    return [s for s in out if len(s) >= 2]


def is_vn_name(name: str) -> bool:
    """Tên này là tên tiếng Việt?"""
    syl = syllables(name)
    if not syl:
        return False
    if any(s in STRONG for s in syl):
        return True
    if all(s in VN_LEAN or s in EN_LEAN for s in syl):
        return len({s for s in syl if s in VN_LEAN}) >= 2
    return False


def is_vn_single(name: str) -> bool:
    """Tên MỘT âm tiết tiếng Việt — chỉ kết tội ở chỗ khai báo và ở tên file."""
    if name.isupper() or any(c.isdigit() for c in name):
        return False
    if name.startswith("__") and name.endswith("__"):
        return False  # tên dunder của Python (__ne__, __le__) — ngôn ngữ đặt, không phải người
    syl = syllables(name)
    if len(syl) != 1:
        return False
    s = syl[0]
    return s not in SINGLE_OK and (s in STRONG or s in VN_LEAN)


def is_vn_prose_token(tok: str) -> bool:
    """Token đứng trong CHỮ (backtick, sát dấu chấm, từ đơn) — chặt hơn trong code.

    Một âm tiết thì phải là STRONG và không nằm trong VN_PLAIN: "sau", "trong", "pha"
    là tiếng Việt viết ĐÚNG khi không dấu, kết tội chúng là bắt nhầm chữ của người đọc.
    """
    syl = syllables(tok)
    if len(syl) >= 2:
        return is_vn_name(tok)
    if len(syl) == 1:
        return syl[0] in STRONG and syl[0] not in VN_PLAIN
    return False


def is_junk(tok: str) -> bool:
    """Băm, mã base64, chuỗi ngẫu nhiên — không phải tên ai đặt."""
    if "_" in tok:
        return False
    return sum(c.isdigit() for c in tok) >= 3 or len(tok) > 24


# --------------------------------------------------------------------------------------
# 2. Chia file thành vùng code · chuỗi · chú thích
# --------------------------------------------------------------------------------------

_LANG_BY_EXT = {
    ".py": "py", ".pyi": "py",
    ".rs": "c", ".go": "c", ".java": "c", ".kt": "c", ".kts": "c", ".swift": "c",
    ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cs": "c", ".m": "c",
    ".js": "c", ".mjs": "c", ".cjs": "c", ".jsx": "c", ".ts": "c", ".tsx": "c",
    ".dart": "c", ".scala": "c", ".php": "c", ".gradle": "c", ".proto": "c", ".scss": "c",
    ".sql": "sql",
    ".sh": "sh", ".bash": "sh", ".zsh": "sh", ".rb": "sh", ".tf": "c",
}
# File cấu hình: GIÁ TRỊ là dữ liệu cho người đọc, không phải code — soi như chữ, nếu
# không thì một giá trị tiếng Việt CÓ DẤU (`STACK_SKILL ?= chưa-chốt`) bị bắt oan là
# "tên có dấu". Khoá cấu hình là token ghép nên luật chữ vẫn bắt được.
_LANG_BY_NAME = {"Dockerfile": "sh"}

_LINE_CMT = {"py": ("#",), "c": ("//",), "sql": ("--",), "sh": ("#",)}
_BLOCK_CMT = {"py": (), "c": (("/*", "*/"),), "sql": (("/*", "*/"),), "sh": ()}
_QUOTES = {"py": ('"""', "'''", '"', "'"), "c": ('"', "'", "`"),
           "sql": ("'", '"'), "sh": ('"', "'")}


def lang_of(rel: str) -> str:
    """'py' · 'c' · 'sql' · 'sh' (có vùng code) hay 'prose' (soi như chữ từ đầu đến cuối)."""
    name = os.path.basename(rel)
    if name in _LANG_BY_NAME:
        return _LANG_BY_NAME[name]
    return _LANG_BY_EXT.get(os.path.splitext(name)[1].lower(), "prose")


def segments(text: str, lang: str) -> list[tuple[str, int, int]]:
    """Chia thành các đoạn (loại, đầu, cuối); loại: 'code' | 'str' | 'cmt'."""
    if lang == "prose":
        return [("cmt", 0, len(text))]
    line_cmt, block_cmt, quotes = _LINE_CMT[lang], _BLOCK_CMT[lang], _QUOTES[lang]
    out: list[tuple[str, int, int]] = []
    i = code_start = 0
    n = len(text)
    while i < n:
        hit = False
        for mark in line_cmt:
            if text.startswith(mark, i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                out += [("code", code_start, i), ("cmt", i, j)]
                i = code_start = j
                hit = True
                break
        if hit:
            continue
        for op, cl in block_cmt:
            if text.startswith(op, i):
                j = text.find(cl, i + len(op))
                j = n if j < 0 else j + len(cl)
                out += [("code", code_start, i), ("cmt", i, j)]
                i = code_start = j
                hit = True
                break
        if hit:
            continue
        for q in quotes:
            if text.startswith(q, i):
                j = i + len(q)
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text.startswith(q, j):
                        j += len(q)
                        break
                    if len(q) == 1 and text[j] == "\n":
                        break
                    j += 1
                else:
                    j = n
                out += [("code", code_start, i), ("str", i, j)]
                i = code_start = j
                hit = True
                break
        if not hit:
            i += 1
    out.append(("code", code_start, n))
    return [seg for seg in out if seg[2] > seg[1]]


_JSX_TEXT = re.compile(r"(?<=[>}])([^<>{}]*[^\W\d_][^<>{}]*)(?=[<{])")


def with_jsx_text(segs: list[tuple[str, int, int]], text: str) -> list[tuple[str, int, int]]:
    """React: chữ hiển thị nằm TRẦN giữa thẻ (`<b>Đang gửi</b>`, `Vào {name}`), không trong dấu nháy.
    Coi các đoạn đó là chuỗi (soi như chữ cho người đọc — vẫn phải có dấu), không phải tên."""
    out: list[tuple[str, int, int]] = []
    for kind, a, b in segs:
        if kind != "code":
            out.append((kind, a, b))
            continue
        pos = a
        for m in _JSX_TEXT.finditer(text, a, b):
            if m.start() > pos:
                out.append(("code", pos, m.start()))
            out.append(("str", m.start(), m.end()))
            pos = m.end()
        if pos < b:
            out.append(("code", pos, b))
    return out


# --------------------------------------------------------------------------------------
# 3. Các phép soi
# --------------------------------------------------------------------------------------

IDENT = r"[A-Za-z_\u00c0-\u1ef9][A-Za-z0-9_\u00c0-\u1ef9]*"
_CODE_IDENT = re.compile(IDENT)
_ASCII_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_DECL = [re.compile(p, re.M) for p in (
    r"\b(?:def|class|fn|func|function|let|const|var|val|mut|struct|enum|interface|type|"
    r"trait|impl|module|namespace|lambda|as)\s+(" + _ASCII_IDENT + r")",
    r"\bfor\s+(?:let\s+|const\s+|var\s+|mut\s+)?(" + _ASCII_IDENT + r")\s+in\b",
    r"\bfor\s*\(\s*(?:let|const|var|int|auto)\s+(" + _ASCII_IDENT + r")",
    r"^\s*(?:pub\s+|export\s+)?(" + _ASCII_IDENT + r")\s*(?::[^=\n]{0,40})?=(?!=)",
    r"\b(" + _ASCII_IDENT + r")\s*:=",
    r"^\s*(?:pub\s+)?(" + _ASCII_IDENT + r")\s*:\s*[A-Za-z_&<\[\*]",
    r"\b(?:Some|Ok|Err)\(\s*(" + _ASCII_IDENT + r")\s*\)",
)]
_PARAMS = re.compile(r"\b(?:def|fn|func|function)\s+" + _ASCII_IDENT + r"\s*\(([^)]*)\)", re.S)
_PARAM_NAME = re.compile(r"(?:^|,)\s*(?:\*{0,2}|&|mut\s+|const\s+|final\s+)?(" + _ASCII_IDENT + r")")
_FMT = re.compile(r"\{\s*(" + _ASCII_IDENT + r")\s*[:!}]|\$\{\s*(" + _ASCII_IDENT + r")\s*\}")

_BACKTICK = re.compile(r"`([^`\n]{1,120})`")
_COMPOUND = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[_\-][A-Za-z0-9]+)+\b"
                       r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"
                       r"|\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b")
_DOTTED = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_ESCAPE = re.compile(r"\\[nrt0abfv\"'\\]|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}")

LANG_OK = re.compile(r"lang-ok:\s*\S")
LANG_OK_FILE = re.compile(r"lang-ok-file:\s*\S")


def _code_findings(chunk: str, base: int) -> list[tuple[int, str, str]]:
    out = []
    for m in _CODE_IDENT.finditer(chunk):
        name = m.group(0)
        if is_junk(name):
            continue
        if has_diacritic(name):
            out.append((base + m.start(), name, "tên có dấu trong code"))
        elif is_vn_name(name):
            out.append((base + m.start(), name, "tên tiếng Việt trong code"))
    declared: list[tuple[int, str]] = []
    for rx in _DECL:
        for m in rx.finditer(chunk):
            declared.append((m.start(1), m.group(1)))
    for m in _PARAMS.finditer(chunk):
        blk = m.group(1)
        for pm in _PARAM_NAME.finditer(blk):
            declared.append((m.start(1) + pm.start(1), pm.group(1)))
    for pos, name in declared:
        if is_vn_single(name):
            out.append((base + pos, name, "tên một âm tiết ở chỗ khai báo"))
    return out


def _fmt_findings(chunk: str, base: int) -> list[tuple[int, str, str]]:
    out = []
    for m in _FMT.finditer(chunk):
        name = m.group(1) or m.group(2)
        if name and not is_junk(name) and is_vn_name(name):
            out.append((base + m.start(), name, "tên trong chuỗi format"))
    return out


def _prose_findings(chunk: str, base: int) -> list[tuple[int, str, str]]:
    """Chữ cho người đọc: chuỗi, chú thích, dòng tài liệu."""
    out = []
    line_base = base
    for raw in chunk.split("\n"):
        line_base_cur, line_base = line_base, line_base + len(raw) + 1
        line = _ESCAPE.sub(lambda m: " " * len(m.group(0)), raw)
        accented = has_diacritic(line)
        for m in _COMPOUND.finditer(line):
            tok = m.group(0)
            if tok.isascii() and not is_junk(tok) and is_vn_name(tok):
                out.append((line_base_cur + m.start(), tok, "token ghép trong chữ"))
        for m in _DOTTED.finditer(line):
            for gi in (1, 2):
                tok = m.group(gi)
                if tok.isascii() and not is_junk(tok) and is_vn_prose_token(tok):
                    out.append((line_base_cur + m.start(gi), m.group(0), "tên sát dấu chấm"))
                    break
        for m in _BACKTICK.finditer(line):
            span = m.group(1)
            if has_diacritic(span):
                continue
            for wm in _WORD.finditer(span):
                w = wm.group(0)
                if not is_junk(w) and is_vn_prose_token(w):
                    out.append((line_base_cur + m.start(1) + wm.start(), w, "token trong backtick"))
        if accented:
            continue
        words = [m.group(0) for m in _WORD.finditer(line)]
        for m in _WORD.finditer(line):
            w = m.group(0)
            if not is_junk(w) and len(syllables(w)) == 1 and is_vn_prose_token(w):
                out.append((line_base_cur + m.start(), w, "từ tiếng Việt không dấu"))
        low = [w.lower() for w in words if len(w) >= 2]
        if len(low) >= 4:
            vn = [w for w in low if w in STRONG or w in VN_LEAN]
            if len(vn) >= 0.6 * len(low) and any(w in STRONG and w not in VN_PLAIN for w in vn):
                out.append((line_base_cur, " ".join(words[:8]), "câu tiếng Việt viết không dấu"))
    return out


class Finding:
    __slots__ = ("rel", "line", "key", "rule", "ctx")

    def __init__(self, rel: str, line: int, key: str, rule: str, ctx: str) -> None:
        self.rel, self.line, self.key, self.rule, self.ctx = rel, line, key, rule, ctx

    def where(self) -> str:
        return f"{self.rel}:{self.line}"


def _line_starts(text: str) -> list[int]:
    starts, pos = [0], text.find("\n")
    while pos >= 0:
        starts.append(pos + 1)
        pos = text.find("\n", pos + 1)
    return starts


def check_text(rel: str, text: str, line_offset: int = 0) -> list[Finding]:
    """Soi NỘI DUNG một file (hoặc một mẩu vừa được ghi vào file đó)."""
    lines = text.split("\n")
    if LANG_OK_FILE.search("\n".join(lines[:10])):
        return []
    raw: list[tuple[int, str, str]] = []
    lang = lang_of(rel)
    segs = segments(text, lang)
    if rel.endswith((".tsx", ".jsx")):
        segs = with_jsx_text(segs, text)
    for kind, s, e in segs:
        chunk = text[s:e]
        if kind == "code":
            raw += _code_findings(chunk, s)
        elif kind == "str":
            raw += _fmt_findings(chunk, s) + _prose_findings(chunk, s)
        else:
            raw += _prose_findings(chunk, s)
    starts = _line_starts(text)
    seen, out = set(), []
    for pos, key, rule in sorted(raw):
        ln = bisect.bisect_right(starts, pos)
        line = lines[ln - 1] if 0 < ln <= len(lines) else ""
        if LANG_OK.search(line):
            continue
        sig = (ln, key, rule)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(Finding(rel, ln + line_offset, key, rule, line.strip()[:150]))
    return out


def check_path(rel: str) -> list[Finding]:
    """Soi TÊN file và mọi thư mục trên đường dẫn."""
    out, seen = [], set()
    parts = Path(rel).parts
    for idx, part in enumerate(parts):
        name = part.strip("{}")
        if idx == len(parts) - 1:
            # Đuôi file (.go, .rs, .py) là do ngôn ngữ quy định, không phải tên ai đặt
            stem, dot, ext = name.rpartition(".")
            if dot and stem and len(ext) <= 6 and ext.isalnum():
                name = stem
        for piece in re.split(r"[.\s]+", name):
            if not piece or piece in seen or is_junk(piece):
                continue
            seen.add(piece)
            if has_diacritic(piece):
                out.append(Finding(rel, 0, piece, "tên file/thư mục có dấu", rel))
            elif is_vn_name(piece) or is_vn_single(piece):
                out.append(Finding(rel, 0, piece, "tên file/thư mục tiếng Việt", rel))
    return out


# --------------------------------------------------------------------------------------
# 4. Chọn file để soi
# --------------------------------------------------------------------------------------

SELF = os.path.relpath(os.path.abspath(__file__), os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".nuxt", "target",
             ".dart_tool", ".gradle", ".idea", ".vscode-test", "vendor", "Pods",
             ".terraform", "coverage", ".turbo", "site-packages"}
# Tài liệu Authority đưa vào: dự án KHÔNG sửa nội dung lẫn tên file (viper-rule7-naming.md §8)
# Skill vendor từ bên thứ ba (UPSTREAM.md ghim commit) — như intake/, dự án không sửa tay;
# code tiếng Anh của họ có tên một âm tiết (`cap`) mà máy kiểm sẽ bắt nhầm.
SKIP_PREFIX = ("intake/", ".claude/skills/security-audit/")
LOCK_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "uv.lock",
              "poetry.lock", "go.sum", "Gemfile.lock", "composer.lock", "pubspec.lock",
              "flake.lock", "bun.lockb", "Podfile.lock"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
              ".tar", ".jar", ".class", ".so", ".dylib", ".dll", ".exe", ".bin", ".woff",
              ".woff2", ".ttf", ".otf", ".mp4", ".mp3", ".wav", ".mov", ".sqlite", ".db",
              ".pyc", ".pack", ".idx", ".keystore", ".jks", ".p12", ".lockb"}
MAX_BYTES = 2_000_000


def repo_root(argv_root: str | None) -> Path:
    if argv_root:
        return Path(argv_root).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def skipped(rel: str) -> bool:
    if rel.startswith(SKIP_PREFIX) or rel == SELF or rel.endswith("/" + os.path.basename(SELF)):
        return True
    parts = Path(rel).parts
    if any(p in SKIP_DIRS for p in parts):
        return True
    name = os.path.basename(rel)
    return name in LOCK_FILES or os.path.splitext(name)[1].lower() in BINARY_EXT


def list_files(root: Path) -> list[str]:
    """Mọi file git theo dõi + file chưa track mà không bị ignore; không có git thì duyệt cây."""
    rels: list[str] = []
    try:
        res = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                             capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            rels = [ln for ln in res.stdout.splitlines() if ln]
    except Exception:
        rels = []
    if not rels:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                rels.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return [r for r in rels if not skipped(r)]


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        blob = path.read_bytes()
    except OSError:
        return None
    if b"\0" in blob[:8192]:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan(root: Path) -> list[Finding]:
    out: list[Finding] = []
    for rel in list_files(root):
        out += check_path(rel)
        text = read_text(root / rel)
        if text is not None:
            out += check_text(rel, text)
    return out


# --------------------------------------------------------------------------------------
# 5. Ba chế độ
# --------------------------------------------------------------------------------------

HINT = ("Luật #7: tên (file, thư mục, hàm, biến, field, khoá log, tên trong chuỗi format) "
        "là TIẾNG ANH; log/lỗi/comment/tài liệu viết tiếng Việt thì phải CÓ DẤU.\n"
        "Dữ liệu thật sự phải là tiếng Việt → thêm `lang-ok: <lý do>` trên đúng dòng đó "
        "(không lý do thì không có hiệu lực). Không có baseline, không nợ được miễn.")


def mode_check(root: Path) -> int:
    found = scan(root)
    if not found:
        print("✓ luật #7: không còn tiếng Việt không dấu trong repo")
        return 0
    by_file: dict[str, list[Finding]] = {}
    for f in found:
        by_file.setdefault(f.rel, []).append(f)
    print(f"✗ luật #7: {len(found)} chỗ vi phạm ở {len(by_file)} file\n")
    for rel in sorted(by_file):
        print(f"  {rel}")
        for f in sorted(by_file[rel], key=lambda x: x.line)[:20]:
            where = f"dòng {f.line}" if f.line else "tên file"
            print(f"    {where:>10}  {f.key}   ({f.rule})")
        extra = len(by_file[rel]) - 20
        if extra > 0:
            print(f"                  … còn {extra} chỗ nữa")
    print("\n" + HINT)
    return 1


def mode_report(root: Path) -> int:
    found = scan(root)
    keys: dict[tuple[str, str], list[str]] = {}
    for f in found:
        keys.setdefault((f.key, f.rule), []).append(f.where())
    print(f"# check_lang {VERSION} — {len(found)} chỗ · {len(keys)} khoá · gốc {root}")
    for (key, rule), where in sorted(keys.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"{len(where):>4}  {key}\t[{rule}]\t{' '.join(where[:8])}"
              + (" …" if len(where) > 8 else ""))
    return 0


def _hook_pieces(payload: dict) -> tuple[str, list[str]]:
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    pieces: list[str] = []
    for key in ("content", "new_string", "new_source"):
        if isinstance(ti.get(key), str):
            pieces.append(ti[key])
    for edit in ti.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            pieces.append(edit["new_string"])
    return path, pieces


def mode_hook(root: Path) -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    path, pieces = _hook_pieces(payload)
    if not path:
        return 0
    try:
        rel = os.path.relpath(os.path.abspath(path), root)
    except ValueError:
        rel = os.path.basename(path)
    if rel.startswith(".."):
        rel = os.path.basename(path)
    if skipped(rel):
        return 0
    found = check_path(rel)
    for piece in pieces:
        found += check_text(rel, piece)
    if not found:
        return 0
    lines = ["✗ luật #7 — chỗ này thêm tiếng Việt không dấu vào repo:"]
    for f in found[:12]:
        where = f"dòng {f.line} của phần mới" if f.line else "tên file"
        lines.append(f"    {where}: {f.key}   ({f.rule})")
    if len(found) > 12:
        lines.append(f"    … còn {len(found) - 12} chỗ nữa")
    lines.append("")
    lines.append(HINT)
    sys.stderr.write("\n".join(lines) + "\n")
    return 2


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    root_arg = None
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            root_arg = argv[i + 1]
        elif a.startswith("--root="):
            root_arg = a.split("=", 1)[1]
    args = [a for a in args if a != root_arg]
    mode = args[0] if args else "check"
    root = repo_root(root_arg)
    if mode == "check":
        return mode_check(root)
    if mode == "report":
        return mode_report(root)
    if mode == "hook":
        return mode_hook(root)
    sys.stderr.write(f"dùng: {os.path.basename(__file__)} [check|report|hook] [--root <thư mục>]\n")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
