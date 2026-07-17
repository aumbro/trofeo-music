"""
clocks.py — โหมดนาฬิกาหลายสไตล์สำหรับจอ Trofeo (เน้นวินเทจ)

เรียกจาก vibe.py:  clocks.render(style, W, H, now, t) -> PIL.Image (RGB ขนาด W×H)
สไตล์ (clocks.STYLES) เรียงเน้นวินเทจก่อน:
  nixie   หลอด Nixie เรืองส้ม (วินเทจสุด)      flip   ป้ายพลิก split-flap
  vfd     จอ VFD เขียว-ฟ้าเรือง                seg7   LED 7-segment แดง (นาฬิกาปลุก)
  lcd     LCD เขียวมะกอก (Casio)               analog เข็ม เลขโรมัน หน้าครีมวินเทจ
  neon    หลอดนีออนเรือง                        word   นาฬิกาคำ (IT IS ... O'CLOCK)
  minimal ตัวเลขบางโมเดิร์น

ออกแบบสำหรับแนวนอน 1920×462 (vibe บังคับแนวนอนเมื่ออยู่โหมดนาฬิกา)
เก็บ static layer (พื้น/หลอดแก้ว/หน้าปัด/ตารางอักษร) แคชไว้ วาดเฉพาะส่วนที่ขยับต่อเฟรม
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

STYLES = ["nixie", "flip", "vfd", "seg7", "lcd", "analog", "lumo", "mech", "sun",
          "neon", "word", "world", "cyberpunk", "minimal"]
STYLE_LABELS = {
    "nixie": "Nixie (หลอดเรืองส้ม)", "flip": "Flip (ป้ายพลิก)",
    "vfd": "VFD (จอเขียวเรือง)", "seg7": "7-Segment (LED แดง)",
    "lcd": "LCD (Casio เขียว)", "analog": "เข็ม (เลขโรมัน วินเทจ)",
    "lumo": "เข็มเรืองแสง (Casio กลางคืน)", "mech": "กลไก (เฟืองหมุน)",
    "sun": "ตะวัน (ไล่แสงตามเวลาจริง)", "neon": "Neon (นีออน)",
    "word": "Word (นาฬิกาคำ)", "world": "World (นาฬิกาโลก)",
    "cyberpunk": "Cyberpunk (นีออน HUD)", "minimal": "Minimal (เรียบ)",
}

# ── fonts ────────────────────────────────────────────────────────────────────
_font_cache: dict = {}


def _load(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _f(kind, size):
    key = (kind, size)
    if key not in _font_cache:
        cands = {
            "sans":  ["arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf"],
            "nixie": ["bahnschrift.ttf", "arialbd.ttf", "segoeuib.ttf"],
            "thin":  ["segoeuil.ttf", "ARIALN.TTF", "arial.ttf"],
            "serif": ["georgiab.ttf", "timesbd.ttf", "georgia.ttf", "times.ttf"],
            "serifi": ["georgia.ttf", "times.ttf"],
            "mono":  ["consolab.ttf", "cour.ttf", "consola.ttf"],
        }[kind]
        _font_cache[key] = _load(cands, size)
    return _font_cache[key]


def _text_c(d, cx, cy, s, font, fill, anchor="mm"):
    d.text((cx, cy), s, font=font, fill=fill, anchor=anchor)


# ── static-layer cache ───────────────────────────────────────────────────────
_BG: dict = {}


def _bg(style, W, H, builder):
    key = (style, W, H)
    if key not in _BG:
        _BG[key] = builder(W, H)
    return _BG[key].copy()


def _glow_add(base, strokes, blur, gain=1.0):
    """เพิ่ม bloom: เบลอ strokes (RGB พื้นดำ+เส้นสี) แล้วบวกเข้า base"""
    g = strokes.filter(ImageFilter.GaussianBlur(blur))
    if gain != 1.0:
        g = g.point(lambda v: min(255, int(v * gain)))
    return ImageChops.add(base, g)


def _vignette(W, H, inner, outer, cx=None, cy=None, rad=None):
    """สร้าง overlay รัศมีมืดขอบ (คูณกับพื้น)"""
    cx = W / 2 if cx is None else cx
    cy = H / 2 if cy is None else cy
    rad = math.hypot(W, H) / 2 if rad is None else rad
    m = Image.new("L", (W, H), 0)
    dm = ImageDraw.Draw(m)
    steps = 60
    for i in range(steps, 0, -1):
        r = rad * i / steps
        v = int(inner + (outer - inner) * (i / steps))
        dm.ellipse([cx - r, cy - r, cx + r, cy + r], fill=v)
    return m


# ── 7-segment engine ─────────────────────────────────────────────────────────
SEG = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcfgd",
    " ": "", "-": "g",
}


def _seg_polys(x, y, w, h, t):
    """คืน dict a..g เป็น polygon ของแต่ละ segment (ปลายเฉียง)"""
    g = t * 0.55                      # ช่องว่างปลาย segment
    xl, xr, xm = x, x + w, x + w / 2
    yt, ym, yb = y, y + h / 2, y + h
    h2 = t / 2

    def hbar(x0, x1, cy):
        return [(x0, cy), (x0 + h2, cy - h2), (x1 - h2, cy - h2),
                (x1, cy), (x1 - h2, cy + h2), (x0 + h2, cy + h2)]

    def vbar(cx, y0, y1):
        return [(cx, y0), (cx + h2, y0 + h2), (cx + h2, y1 - h2),
                (cx, y1), (cx - h2, y1 - h2), (cx - h2, y0 + h2)]

    return {
        "a": hbar(xl + g, xr - g, yt),
        "g": hbar(xl + g, xr - g, ym),
        "d": hbar(xl + g, xr - g, yb),
        "f": vbar(xl, yt + g, ym - g),
        "b": vbar(xr, yt + g, ym - g),
        "e": vbar(xl, ym + g, yb - g),
        "c": vbar(xr, ym + g, yb - g),
    }


def _digital_layout(W, H, dw, dh, gi, gm, colw):
    """วางเลข HH:MM:SS: ในคู่ชิด (gi) เว้นรอบ colon กว้าง (gm) → จัดกลุ่มชัด
    ลำดับ: d gi d gm : gm d gi d gm : gm d gi d"""
    seq = [("d", dw), ("s", gi), ("d", dw), ("s", gm), ("c", colw), ("s", gm),
           ("d", dw), ("s", gi), ("d", dw), ("s", gm), ("c", colw), ("s", gm),
           ("d", dw), ("s", gi), ("d", dw)]
    total = sum(w for _, w in seq)
    x = (W - total) / 2
    y = (H - dh) / 2
    out = []
    for kind, w in seq:
        if kind == "d":
            out.append(("d", x, dw))
        elif kind == "c":
            out.append(("c", x, colw))
        x += w
    return out, y


def _draw_digital(base, W, H, hh, mm, ss, on, off, glow_col, blur,
                  dw=176, dh=300, th=33, gi=14, gm=30, colw=40, colon=True):
    """วาดนาฬิกาดิจิทัล 7-seg ทับ base (in-place-ish) — คืน base ใหม่ที่บวก glow แล้ว"""
    layout, y = _digital_layout(W, H, dw, dh, gi, gm, colw)
    digits = f"{hh:02d}{mm:02d}{ss:02d}"
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    db = ImageDraw.Draw(base)
    dg = ImageDraw.Draw(strokes)
    di = 0
    for kind, x, w in layout:
        if kind == "d":
            ch = digits[di]; di += 1
            polys = _seg_polys(x, y, dw, dh, th)
            segs = SEG[ch]
            for name, poly in polys.items():
                if name in segs:
                    db.polygon(poly, fill=on)
                    dg.polygon(poly, fill=glow_col)
                elif off:
                    db.polygon(poly, fill=off)
        else:
            cy1, cy2 = y + dh * 0.32, y + dh * 0.68
            r = th * 0.42
            cx = x + w / 2
            vis = colon and (ss % 2 == 0)
            for cy in (cy1, cy2):
                col = on if vis else (off or on)
                db.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
                if vis:
                    dg.ellipse([cx - r, cy - r, cx + r, cy + r], fill=glow_col)
    if blur:
        base = _glow_add(base, strokes, blur)
        base.paste(Image.new("RGB", (W, H)), (0, 0), None) if False else None
        # วาด core คมทับ glow อีกที
        db2 = ImageDraw.Draw(base)
        di = 0
        for kind, x, w in layout:
            if kind == "d":
                ch = digits[di]; di += 1
                polys = _seg_polys(x, y, dw, dh, th)
                for name, poly in polys.items():
                    if name in SEG[ch]:
                        db2.polygon(poly, fill=on)
            else:
                cy1, cy2 = y + dh * 0.32, y + dh * 0.68
                r = th * 0.42; cx = x + w / 2
                if colon and (ss % 2 == 0):
                    for cy in (cy1, cy2):
                        db2.ellipse([cx - r, cy - r, cx + r, cy + r], fill=on)
    return base


# ── สไตล์: seg7 (LED แดง) ─────────────────────────────────────────────────────
def _r_seg7(W, H, now, t):
    def build(W, H):
        img = Image.new("RGB", (W, H), (8, 6, 6))
        v = _vignette(W, H, 90, 255)
        img = ImageChops.multiply(img, Image.merge("RGB", (v, v, v)))
        return img
    base = _bg("seg7", W, H, build)
    base = _draw_digital(base, W, H, now.hour, now.minute, now.second,
                         on=(255, 42, 38), off=(40, 8, 8), glow_col=(150, 20, 18),
                         blur=20)
    d = ImageDraw.Draw(base)
    _text_c(d, W / 2, H - 42, now.strftime("%a  %d %b  %Y").upper(),
            _f("mono", 26), (120, 30, 26))
    return base


# ── สไตล์: vfd (เขียว-ฟ้าเรือง) ────────────────────────────────────────────────
def _r_vfd(W, H, now, t):
    def build(W, H):
        img = Image.new("RGB", (W, H), (4, 12, 12))
        # ตารางเส้นละเอียด (mesh) จาง ๆ
        d = ImageDraw.Draw(img)
        for gx in range(0, W, 6):
            d.line([(gx, 0), (gx, H)], fill=(6, 20, 20))
        v = _vignette(W, H, 120, 255)
        return ImageChops.multiply(img, Image.merge("RGB", (v, v, v)))
    base = _bg("vfd", W, H, build)
    base = _draw_digital(base, W, H, now.hour, now.minute, now.second,
                         on=(90, 240, 210), off=(12, 46, 44), glow_col=(40, 150, 130),
                         blur=18)
    d = ImageDraw.Draw(base)
    _text_c(d, W / 2, H - 40, now.strftime("%A  %d %B").upper(),
            _f("mono", 26), (70, 190, 170))
    return base


# ── สไตล์: lcd (Casio เขียวมะกอก) ─────────────────────────────────────────────
def _r_lcd(W, H, now, t):
    BGC = (150, 168, 78)
    OFF = (132, 150, 66)
    ONC = (26, 34, 12)

    def build(W, H):
        img = Image.new("RGB", (W, H), BGC)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([16, 16, W - 16, H - 16], radius=26,
                            outline=(60, 72, 30), width=6)
        return img
    base = _bg("lcd", W, H, build)
    # LCD ไม่มี glow — วาด off ทุก segment เป็น ghost แล้ว on ทับ
    base = _draw_digital(base, W, H, now.hour, now.minute, now.second,
                         on=ONC, off=OFF, glow_col=(0, 0, 0), blur=0)
    d = ImageDraw.Draw(base)
    _text_c(d, W / 2, H - 52, now.strftime("%a %d/%m").upper(),
            _f("mono", 30), (40, 52, 18))
    _text_c(d, 120, 70, "ALARM", _f("mono", 22), (60, 72, 28))
    return base


# ── สไตล์: nixie (หลอดเรืองส้ม — วินเทจสุด) ───────────────────────────────────
_NIXIE_MESH: dict = {}


def _r_nixie(W, H, now, t):
    dw, dh, gap = 150, 300, 34
    tokens = ["d", "d", "c", "d", "d", "c", "d", "d"]
    colw = 44
    total = 6 * dw + 2 * colw + 7 * gap
    x0 = (W - total) / 2
    y = (H - dh) / 2
    fnt = _f("nixie", 250)

    # ตำแหน่งหลอด (สำหรับ mesh + glass)
    tubes = []
    x = x0
    for tk in tokens:
        if tk == "d":
            tubes.append((x - 12, y - 30, x + dw + 12, y + dh + 24, x + dw / 2, y + dh / 2))
            x += dw + gap
        else:
            x += colw + gap

    def build(W, H):
        img = Image.new("RGB", (W, H), (12, 9, 8))
        v = _vignette(W, H, 60, 255)
        img = ImageChops.multiply(img, Image.merge("RGB", (v, v, v)))
        d = ImageDraw.Draw(img, "RGBA")
        for (gx0, gy0, gx1, gy1, cx, cy) in tubes:
            # แก้วหลอด + โดมบน + เงาสะท้อนแนวตั้ง + ฐานเบกาไลต์ + ขาพิน
            d.rounded_rectangle([gx0, gy0, gx1, gy1], radius=66,
                                fill=(18, 20, 26, 170), outline=(44, 48, 58, 210), width=3)
            d.ellipse([gx0 + 6, gy0 + 4, gx1 - 6, gy0 + 74], fill=(70, 82, 96, 55))
            d.rectangle([cx - 26, gy0 + 30, cx - 14, gy1 - 40], fill=(120, 140, 160, 26))
            d.rounded_rectangle([gx0 + 6, gy1 - 40, gx1 - 6, gy1 + 6], radius=12,
                                fill=(34, 28, 22, 255), outline=(64, 54, 44, 255), width=2)
            for px in range(int(gx0) + 22, int(gx1) - 10, 26):
                d.line([(px, gy1 + 2), (px, gy1 + 18)], fill=(90, 78, 60, 255), width=3)
        return img

    # mesh ลวด (ตะแกรง anode) — overlay RGBA แคชไว้
    mkey = (W, H)
    if mkey not in _NIXIE_MESH:
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dm = ImageDraw.Draw(ov)
        for (gx0, gy0, gx1, gy1, cx, cy) in tubes:
            for yy in range(int(gy0) + 40, int(gy1) - 46, 15):
                dm.line([(gx0 + 16, yy), (gx1 - 16, yy)], fill=(18, 11, 7, 105), width=1)
        _NIXIE_MESH[mkey] = ov

    base = _bg("nixie", W, H, build).convert("RGB")
    digits = f"{now.hour:02d}{now.minute:02d}{now.second:02d}"

    # ── ghost เลขที่ไม่ติด (จาง ๆ เห็นเป็นลวดในหลอด) ──
    dgh = ImageDraw.Draw(base, "RGBA")
    di = 0
    for (gx0, gy0, gx1, gy1, cx, cy) in tubes:
        ch = digits[di]; di += 1
        for gd in ("8", str((int(ch) + 5) % 10)):
            if gd != ch:
                dgh.text((cx, cy), gd, font=fnt, fill=(90, 44, 20, 34), anchor="mm")

    # ── glow หลายชั้น (แดง-ส้มอุ่น) ──
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    di = 0
    for (gx0, gy0, gx1, gy1, cx, cy) in tubes:
        ch = digits[di]; di += 1
        dg.text((cx, cy), ch, font=fnt, fill=(255, 96, 20), anchor="mm")
    x = x0
    for tk in tokens:                                # จุด colon ลง strokes
        if tk == "d":
            x += dw + gap
        else:
            cx = x + colw / 2
            for cy in (y + dh * 0.34, y + dh * 0.66):
                dg.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(255, 110, 30))
            x += colw + gap
    base = _glow_add(base, strokes, 30, 0.85)        # halo กว้าง แดง
    base = _glow_add(base, strokes, 13, 1.0)         # ชั้นกลาง

    # ── core ร้อน คมทับ glow ──
    dc = ImageDraw.Draw(base)
    di = 0
    for (gx0, gy0, gx1, gy1, cx, cy) in tubes:
        ch = digits[di]; di += 1
        dc.text((cx, cy), ch, font=fnt, fill=(255, 120, 34), anchor="mm")
        dc.text((cx, cy), ch, font=_f("nixie", 232), fill=(255, 186, 120), anchor="mm")
    x = x0
    for tk in tokens:
        if tk == "d":
            x += dw + gap
        else:
            cx = x + colw / 2
            for cy in (y + dh * 0.34, y + dh * 0.66):
                dc.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(255, 150, 70))
            x += colw + gap

    # ── ตะแกรงลวดหน้าหลอด ──
    base = Image.alpha_composite(base.convert("RGBA"), _NIXIE_MESH[mkey]).convert("RGB")
    d = ImageDraw.Draw(base)
    _text_c(d, W / 2, H - 30, now.strftime("%A  %d %B %Y").upper(),
            _f("mono", 24), (150, 84, 36))
    return base


# ── สไตล์: flip (ป้ายพลิก split-flap + อนิเมชันพลิกจริง) ───────────────────────
_FLIP: dict = {}          # pos → {"d":เลขปัจจุบัน, "old":เลขก่อน, "ct":เวลาเปลี่ยน}
_FLIP_DUR = 0.30
_FLIP_FCACHE: dict = {}   # (digit,w,h) → ภาพหน้าการ์ด


def _card_face(digit, w, h, fnt):
    key = (digit, w, h)
    if key not in _FLIP_FCACHE:
        c = Image.new("RGB", (w, h), (22, 22, 26))
        d = ImageDraw.Draw(c)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=20, fill=(28, 28, 33))
        d.rounded_rectangle([0, 0, w - 1, h // 2], radius=20, fill=(40, 40, 47))
        d.rectangle([0, h // 2 - 20, w - 1, h // 2], fill=(40, 40, 47))
        # ไล่เงาบน-ล่างนิด ๆ
        d.rectangle([0, h // 2, w - 1, h // 2 + 3], fill=(16, 16, 19))
        d.text((w / 2, h / 2), digit, font=fnt, fill=(240, 240, 246), anchor="mm")
        _FLIP_FCACHE[key] = c
    return _FLIP_FCACHE[key]


def _draw_flip(base, x, y, w, h, old_d, new_d, p, fnt):
    xi, yi, mid = int(x), int(y), h // 2
    face_new = _card_face(new_d, w, h, fnt)
    if p >= 1.0 or old_d == new_d:
        base.paste(face_new, (xi, yi))
    else:
        face_old = _card_face(old_d, w, h, fnt)
        base.paste(face_new.crop((0, 0, w, mid)), (xi, yi))            # บน = เลขใหม่ (รอเผย)
        base.paste(face_old.crop((0, mid, w, h)), (xi, yi + mid))      # ล่าง = เลขเก่า
        if p < 0.5:                                                    # แผ่นบนเก่าพลิกลง (สูง→0)
            fh = max(1, int(mid * math.cos(p * math.pi)))
            flap = face_old.crop((0, 0, w, mid)).resize((w, fh))
            base.paste(flap, (xi, yi + mid - fh))
        else:                                                          # แผ่นล่างใหม่พลิกขึ้น (0→สูง)
            fh = max(1, int(mid * math.cos((1.0 - p) * math.pi)))
            flap = face_new.crop((0, mid, w, h)).resize((w, fh))
            base.paste(flap, (xi, yi + mid))
    d = ImageDraw.Draw(base)
    d.rectangle([x, y + mid - 2, x + w, y + mid + 2], fill=(12, 12, 14))
    d.ellipse([x - 6, y + mid - 7, x + 6, y + mid + 7], fill=(52, 52, 58))
    d.ellipse([x + w - 6, y + mid - 7, x + w + 6, y + mid + 7], fill=(52, 52, 58))


def _r_flip(W, H, now, t):
    digits = f"{now.hour:02d}{now.minute:02d}{now.second:02d}"
    ch = 320
    gap_in, gap_out = 10, 54
    dcw = 176
    group_w = 2 * dcw + gap_in
    total = 3 * group_w + 2 * gap_out
    x0 = (W - total) / 2
    y = (H - ch) / 2
    fnt = _f("sans", 220)

    def build(W, H):
        return Image.new("RGB", (W, H), (14, 14, 16))
    base = _bg("flip", W, H, build)

    for i, cur in enumerate(digits):
        st = _FLIP.get(i)
        if st is None:
            _FLIP[i] = {"d": cur, "old": cur, "ct": t - 1.0}
        elif st["d"] != cur:
            st["old"] = st["d"]; st["d"] = cur; st["ct"] = t
        st = _FLIP[i]
        p = 1.0 if _FLIP_DUR <= 0 else min(1.0, (t - st["ct"]) / _FLIP_DUR)
        g, j = divmod(i, 2)
        x = x0 + g * (group_w + gap_out) + j * (dcw + gap_in)
        _draw_flip(base, x, y, dcw, ch, st["old"], st["d"], p, fnt)

    _text_c(ImageDraw.Draw(base), W / 2, H - 30, now.strftime("%A  %d %B").upper(),
            _f("sans", 26), (150, 150, 160))
    return base


# ── สไตล์: analog (เข็ม เลขโรมัน หน้าครีมวินเทจ) ──────────────────────────────
_ROMAN = ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"]


def _r_analog(W, H, now, t):
    cx, cy = W / 2, H / 2
    R = H / 2 - 24

    def build(W, H):
        img = Image.new("RGB", (W, H), (18, 16, 14))
        d = ImageDraw.Draw(img, "RGBA")
        # หน้าปัดครีม + ขอบทอง
        d.ellipse([cx - R - 10, cy - R - 10, cx + R + 10, cy + R + 10],
                  fill=(120, 96, 52))
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(238, 230, 208))
        d.ellipse([cx - R + 8, cy - R + 8, cx + R - 8, cy + R - 8],
                  outline=(170, 150, 110), width=3)
        # foxing/คราบวินเทจจาง
        vig = _vignette(W, H, 255, 150, cx, cy, R)
        img2 = ImageChops.multiply(img, Image.merge("RGB", (vig, vig, vig)))
        d2 = ImageDraw.Draw(img2, "RGBA")
        # ขีดนาที + เลขโรมัน
        for m in range(60):
            a = math.radians(m * 6 - 90)
            r1 = R - 14 if m % 5 else R - 24
            w = 2 if m % 5 else 4
            x1, y1 = cx + (R - 6) * math.cos(a), cy + (R - 6) * math.sin(a)
            x2, y2 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
            d2.line([(x1, y1), (x2, y2)], fill=(60, 50, 34), width=w)
        fnt = _f("serifi", 46)
        for i, rn in enumerate(_ROMAN):
            a = math.radians(i * 30 - 90)
            rx, ry = cx + (R - 56) * math.cos(a), cy + (R - 56) * math.sin(a)
            d2.text((rx, ry), rn, font=fnt, fill=(50, 40, 28), anchor="mm")
        return img2
    base = _bg("analog", W, H, build)
    d = ImageDraw.Draw(base, "RGBA")
    sec = now.second + now.microsecond / 1e6
    mn = now.minute + sec / 60
    hr = (now.hour % 12) + mn / 60

    def hand(ang_deg, length, width, color, back=18):
        a = math.radians(ang_deg - 90)
        x2, y2 = cx + length * math.cos(a), cy + length * math.sin(a)
        xb, yb = cx - back * math.cos(a), cy - back * math.sin(a)
        d.line([(xb, yb), (x2, y2)], fill=color, width=width)

    hand(hr * 30, R * 0.52, 12, (30, 34, 56))       # ชั่วโมง — เหล็กอมน้ำเงิน
    hand(mn * 6, R * 0.76, 8, (30, 34, 56))         # นาที
    hand(sec * 6, R * 0.82, 3, (176, 44, 52))       # วินาที — แดง
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(150, 120, 60))
    d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(90, 70, 34))
    # แผงข้างวินเทจเติมจอกว้าง: ซ้าย=วัน ขวา=วันที่+เดือน+ปี
    lx, rx = cx - R - (cx - R) * 0.52, cx + R + ((W - (cx + R)) * 0.48)
    _text_c(d, lx, cy - 30, now.strftime("%A").upper(), _f("serif", 62), (150, 128, 92))
    _text_c(d, lx, cy + 40, "TODAY", _f("serifi", 30), (110, 94, 66))
    _text_c(d, rx, cy - 40, now.strftime("%d"), _f("serif", 110), (150, 128, 92))
    _text_c(d, rx, cy + 44, now.strftime("%B %Y").upper(), _f("serifi", 34), (120, 102, 72))
    return base


# ── สไตล์: neon (หลอดนีออนเรือง) ──────────────────────────────────────────────
def _r_neon(W, H, now, t):
    def build(W, H):
        img = Image.new("RGB", (W, H), (14, 10, 20))
        top = Image.new("RGB", (W, H), (30, 16, 40))
        m = Image.linear_gradient("L").resize((W, H))
        img = Image.composite(img, top, m)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([24, 24, W - 24, H - 24], radius=30,
                            outline=(60, 40, 80), width=3)
        return img
    base = _bg("neon", W, H, build)
    txt = now.strftime("%H:%M")
    fnt = _f("sans", 300)
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    # หลอดสองสี: ชมพู core + ฟ้าขอบ
    cx, cy = W / 2, H / 2 - 20
    dg.text((cx, cy), txt, font=fnt, fill=(255, 60, 170), anchor="mm")
    base = _glow_add(base, strokes, 26, 1.2)
    base = _glow_add(base, strokes, 12, 1.0)
    d = ImageDraw.Draw(base)
    # core ขาวอมชมพู
    d.text((cx, cy), txt, font=fnt, fill=(255, 190, 230), anchor="mm",
           stroke_width=2, stroke_fill=(255, 90, 180))
    sec = now.strftime(":%S")
    d.text((cx + 470, cy + 70), sec, font=_f("sans", 90), fill=(120, 230, 255), anchor="mm")
    _text_c(d, cx, H - 40, now.strftime("%A %d %B").upper(),
            _f("sans", 26), (150, 120, 200))
    return base


# ── สไตล์: word (นาฬิกาคำ) ────────────────────────────────────────────────────
_WORD_ROWS = [
    "ITLISASTIME",
    "ACQUARTERDC",
    "TWENTYFIVEX",
    "HALFBTENFTO",
    "PASTERUNINE",
    "ONESIXTHREE",
    "FOURFIVETWO",
    "EIGHTELEVEN",
    "SEVENTWELVE",
    "TENZOCLOCKZ",
]
# (row, col_start, length)
_W = {
    "IT": (0, 0, 2), "IS": (0, 3, 2),
    "A": (1, 0, 1), "QUARTER": (1, 2, 7),
    "TWENTY": (2, 0, 6), "MFIVE": (2, 6, 4),
    "HALF": (3, 0, 4), "MTEN": (3, 5, 3), "TO": (3, 9, 2),
    "PAST": (4, 0, 4), "NINE": (4, 7, 4),
    "ONE": (5, 0, 3), "SIX": (5, 3, 3), "THREE": (5, 6, 5),
    "FOUR": (6, 0, 4), "FIVE": (6, 4, 4), "TWO": (6, 8, 3),
    "EIGHT": (7, 0, 5), "ELEVEN": (7, 5, 6),
    "SEVEN": (8, 0, 5), "TWELVE": (8, 5, 6),
    "TEN": (9, 0, 3), "OCLOCK": (9, 4, 6),
}
_HOURWORD = ["TWELVE", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX",
             "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE"]


def _word_lit(now):
    m5 = (now.minute // 5) * 5
    words = ["IT", "IS"]
    hr = now.hour % 12
    mapping = {
        0: ([], "OCLOCK"), 5: (["MFIVE", "PAST"], None), 10: (["MTEN", "PAST"], None),
        15: (["QUARTER", "PAST"], None), 20: (["TWENTY", "PAST"], None),
        25: (["TWENTY", "MFIVE", "PAST"], None), 30: (["HALF", "PAST"], None),
        35: (["TWENTY", "MFIVE", "TO"], None), 40: (["TWENTY", "TO"], None),
        45: (["QUARTER", "TO"], None), 50: (["MTEN", "TO"], None), 55: (["MFIVE", "TO"], None),
    }
    mods, tail = mapping[m5]
    words += mods
    hour_for = hr if m5 <= 30 else (hr + 1) % 12
    words.append(_HOURWORD[hour_for if hour_for != 0 else 12] if False else _HOURWORD[hour_for])
    if tail:
        words.append(tail)
    return set(words)


def _r_word(W, H, now, t):
    cols, rows = 11, 10
    # เซลล์ไม่จัตุรัส: กว้างเต็มจอ (อ่านง่าย) สูงพอดี
    cw = (W - 140) / cols
    chh = (H - 40) / rows
    gx = (W - cw * cols) / 2 + cw / 2
    gy = (H - chh * rows) / 2 + chh / 2
    fnt = _f("mono", int(chh * 0.78))

    def pos(r, c):
        return gx + c * cw, gy + r * chh

    def build(W, H):
        img = Image.new("RGB", (W, H), (10, 10, 14))
        d = ImageDraw.Draw(img)
        for r in range(rows):
            for c in range(cols):
                x, y = pos(r, c)
                d.text((x, y), _WORD_ROWS[r][c], font=fnt, fill=(30, 31, 37), anchor="mm")
        return img
    base = _bg("word", W, H, build)
    lit = _word_lit(now)
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    db = ImageDraw.Draw(base)
    for wkey in lit:
        if wkey not in _W:
            continue
        r, c0, ln = _W[wkey]
        for c in range(c0, c0 + ln):
            x, y = pos(r, c)
            db.text((x, y), _WORD_ROWS[r][c], font=fnt, fill=(255, 236, 190), anchor="mm")
            dg.text((x, y), _WORD_ROWS[r][c], font=fnt, fill=(255, 176, 80), anchor="mm")
    base = _glow_add(base, strokes, 20, 1.1)
    base = _glow_add(base, strokes, 8, 1.0)          # core คมชัด
    return base


# ── สไตล์: minimal (เรียบ บาง โมเดิร์น) ───────────────────────────────────────
def _r_minimal(W, H, now, t):
    def build(W, H):
        return Image.new("RGB", (W, H), (10, 10, 12))
    base = _bg("minimal", W, H, build)
    d = ImageDraw.Draw(base)
    d.text((W / 2, H / 2 - 24), now.strftime("%H:%M"), font=_f("thin", 260),
           fill=(238, 240, 246), anchor="mm")
    d.text((W / 2, H / 2 + 150), now.strftime("%A, %d %B %Y").upper(),
           font=_f("thin", 34), fill=(120, 124, 134), anchor="mm")
    # วินาทีเป็นแถบบาง ๆ ล่างสุด
    frac = (now.second + now.microsecond / 1e6) / 60
    d.rectangle([0, H - 6, int(W * frac), H], fill=(90, 140, 220))
    return base


# ── สไตล์: mech (นาฬิกากลไก — เฟืองทองเหลืองหมุนจริง + balance wheel) ──────────
_GEARS: dict = {}          # (teeth, r_out) → sprite RGBA (วาดครั้งเดียว หมุนต่อเฟรม)

_BRASS = (196, 158, 84)
_BRASS_D = (140, 108, 52)
_BRASS_L = (232, 198, 120)
_STEEL = (150, 155, 165)


def _gear_sprite(teeth, r_out, hole=0.30, spokes=5, color=_BRASS, pointed=False):
    """วาดเฟือง 1 ตัวเป็น (sprite, shadow) RGBA — มี shading โลหะ (แสงบนซ้าย+เงาริม)
    pointed=True = ฟันแหลมเอียงแบบ escape wheel"""
    key = (teeth, r_out, hole, spokes, color, pointed)
    if key in _GEARS:
        return _GEARS[key]
    S = int(r_out * 2 + 12)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    r_root = r_out * 0.84
    tw = math.pi * 2 / teeth
    for i in range(teeth):
        a0 = i * tw
        if pointed:                            # ฟันเลื่อยเอียง (escape)
            shape = ((r_root, -0.30 * tw), (r_out, -0.02 * tw), (r_root, 0.14 * tw))
        else:                                  # ฟันสี่เหลี่ยมคางหมู
            shape = ((r_root, -0.45 * tw), (r_out, -0.20 * tw),
                     (r_out, 0.20 * tw), (r_root, 0.45 * tw))
        pts = [(c + rr * math.cos(a0 + da), c + rr * math.sin(a0 + da))
               for rr, da in shape]
        d.polygon(pts, fill=color)
    d.ellipse([c - r_root, c - r_root, c + r_root, c + r_root], fill=color)
    r_in = r_root * 0.80
    dk = tuple(int(v * 0.72) for v in color)
    d.ellipse([c - r_in, c - r_in, c + r_in, c + r_in], fill=dk + (255,))
    r_h = r_out * hole
    if spokes > 0:                             # เจาะช่องระหว่างซี่ล้อ
        for i in range(spokes):
            a0 = math.degrees(i * 2 * math.pi / spokes)
            d.pieslice([c - r_in * 0.94, c - r_in * 0.94, c + r_in * 0.94, c + r_in * 0.94],
                       a0, a0 + 360 / spokes * 0.62, fill=(0, 0, 0, 0))
    d.ellipse([c - r_h - 12, c - r_h - 12, c + r_h + 12, c + r_h + 12], fill=color)
    d.ellipse([c - r_h, c - r_h, c + r_h, c + r_h], fill=(16, 13, 10, 255))
    # ── shading โลหะ (numpy): แสงบนซ้าย + มืดตามรัศมี + glint ขอบ + ลาย brushed วง ──
    a = np.asarray(img).astype(np.float32)
    yy, xx = np.mgrid[0:S, 0:S]
    dx = (xx - c) / r_out
    dy = (yy - c) / r_out
    rr = np.sqrt(dx * dx + dy * dy)
    shade = (1.06 - 0.16 * np.clip(rr, 0, 1.1)
             + 0.16 * (-(dx * 0.55 + dy * 0.83))
             + 0.20 * np.exp(-((rr - 0.92) ** 2) / 0.0015)
             + 0.035 * np.sin(rr * 70.0))
    a[..., :3] = np.clip(a[..., :3] * shade[..., None], 0, 255)
    spr = Image.fromarray(a.astype(np.uint8), "RGBA")
    # เงาใต้เฟือง (precompute จาก alpha — ไม่ต้องหมุนตาม เพราะทรงเกือบกลม)
    al = spr.getchannel("A").filter(ImageFilter.GaussianBlur(8)).point(lambda v: int(v * 0.45))
    zero = al.point(lambda v: 0)
    shadow = Image.merge("RGBA", (zero, zero, zero, al))
    _GEARS[key] = (spr, shadow)
    return _GEARS[key]


def _paste_gear(base, cx, cy, gs, angle_deg):
    spr, shadow = gs
    g = spr.rotate(-angle_deg, resample=Image.BICUBIC)
    x, y = int(cx - g.width / 2), int(cy - g.height / 2)
    base.paste(shadow, (x + 7, y + 12), shadow)
    base.paste(g, (x, y), g)


def _rp(r_out):
    return r_out * 0.90                        # pitch radius (วงขบ)


def _mesh(x1, y1, r1, n1, a1, r2, n2, theta):
    """วางเฟืองลูกให้ขบเฟืองแม่ที่มุม theta (องศา) — คืน (x2, y2, a2)
    a2 จัดเฟสให้ฟันสอดร่องพอดี และหมุนสวนทางตามอัตราทด n1/n2"""
    dist = _rp(r1) + _rp(r2)
    x2 = x1 + dist * math.cos(math.radians(theta))
    y2 = y1 + dist * math.sin(math.radians(theta))
    a2 = theta + 180 + 180.0 / n2 - (n1 / n2) * (a1 - theta)
    return x2, y2, a2


def _r_mech(W, H, now, t):
    cx, cy = W / 2, H / 2
    R = H / 2 - 26

    def build(W, H):
        # แผ่น movement เข้ม + bridge บน-ล่าง + perlage (ลายวงกลมขัด) + สกรู chaton
        img = Image.new("RGB", (W, H), (17, 14, 11))
        v = _vignette(W, H, 70, 255)
        img = ImageChops.multiply(img, Image.merge("RGB", (v, v, v)))
        d = ImageDraw.Draw(img, "RGBA")
        for (y0, y1) in ((30, 102), (H - 102, H - 30)):
            d.rounded_rectangle([36, y0, W - 36, y1], radius=32, fill=(33, 27, 21, 255),
                                outline=(62, 51, 38, 255), width=2)
            d.line([(48, y0 + 6), (W - 48, y0 + 6)], fill=(80, 66, 48, 90), width=2)
            # perlage สองแถว
            for row, yy in enumerate((y0 + 24, y0 + 50)):
                for gx in range(60 + row * 15, W - 50, 30):
                    d.ellipse([gx - 17, yy - 17, gx + 17, yy + 17],
                              outline=(255, 232, 180, 9), width=5)
        for sx in (72, W - 72, cx - 430, cx + 430):
            for sy in (66, H - 66):
                ha = (sx * 7 + sy) % 180                     # มุมร่องสกรูต่างกัน
                d.ellipse([sx - 13, sy - 13, sx + 13, sy + 13],
                          outline=(150, 122, 70, 255), width=3)   # chaton ทอง
                d.ellipse([sx - 9, sy - 9, sx + 9, sy + 9], fill=(118, 122, 132, 255))
                dx0 = 7 * math.cos(math.radians(ha))
                dy0 = 7 * math.sin(math.radians(ha))
                d.line([(sx - dx0, sy - dy0), (sx + dx0, sy + dy0)],
                       fill=(52, 54, 62, 255), width=3)
        return img
    base = _bg("mech", W, H, build).convert("RGBA")

    sec = now.second + now.microsecond / 1e6
    mn = now.minute + sec / 60
    hr = (now.hour % 12) + mn / 60
    jewels = []                                  # เก็บจุดแกนไว้วาดทับทิมทีหลัง

    # ── ฝั่งซ้าย: ขบวนเฟืองขบกันจริง (ระยะ=ผลรวม pitch radius, เฟสocked) ──
    n1, r1 = 30, 168
    x1, y1 = W * 0.150, cy + 6
    a1 = -sec * 3.0                              # 1 รอบ/2 นาที
    n2, r2 = 14, 80
    x2, y2, a2 = _mesh(x1, y1, r1, n1, a1, r2, n2, -30)
    n3, r3 = 22, 118
    x3, y3, a3 = _mesh(x2, y2, r2, n2, a2, r3, n3, 48)
    _paste_gear(base, x3, y3, _gear_sprite(n3, r3, hole=0.22, spokes=5), a3)
    _paste_gear(base, x2, y2, _gear_sprite(n2, r2, hole=0.30, spokes=0), a2)
    _paste_gear(base, x1, y1, _gear_sprite(n1, r1, hole=0.16, spokes=5), a1)
    jewels += [(x1, y1), (x2, y2), (x3, y3)]

    # ── ฝั่งขวา: escapement — escape wheel เดินกระตุก + เฟืองส่งกำลัง ──
    nE, rE = 15, 110
    xE, yE = W * 0.845 + 40, cy + 8
    beats = t * 5.0                              # 2.5Hz × 2 จังหวะ
    frac = beats % 1.0
    snap = min(1.0, frac * 7.0)                  # สแนปเร็วแล้วหยุดรอ (จังหวะ escapement จริง)
    aE = -(math.floor(beats) + snap) * (360.0 / nE) / 2
    n4, r4 = 24, 96
    x4, y4, a4 = _mesh(xE, yE, rE, nE, aE, r4, n4, 148)
    _paste_gear(base, x4, y4, _gear_sprite(n4, r4, hole=0.26, spokes=5), a4)
    _paste_gear(base, xE, yE, _gear_sprite(nE, rE, hole=0.14, spokes=4,
                                           color=_STEEL, pointed=True), aE)
    jewels += [(xE, yE), (x4, y4)]

    # ── balance wheel + hairspring (จักร 2.5Hz) ──
    bx, by = W * 0.845 - 150, cy - 118
    rb = 90
    phase = t * math.pi * 2 * 2.5
    swing = math.sin(phase) * 42
    d = ImageDraw.Draw(base)
    d.ellipse([bx - rb - 6, by - rb - 6, bx + rb + 6, by + rb + 6],
              fill=(0, 0, 0, 70))                          # เงาใต้จักร
    # hairspring ก้นหอย (หายใจตามจังหวะ)
    breath = 1.0 + 0.07 * math.sin(phase + math.pi / 2)
    coils, steps = 4.2, 96
    pts = []
    for i in range(steps + 1):
        ph = i / steps * coils * 2 * math.pi
        rr = 7 + (ph / (coils * 2 * math.pi)) * rb * 0.52 * breath
        pts.append((bx + rr * math.cos(ph + math.radians(swing)),
                    by + rr * math.sin(ph + math.radians(swing))))
    d.line(pts, fill=(150, 136, 104), width=2)
    # ขอบจักรหนา + สกรูถ่วงบนขอบ + ก้าน
    d.ellipse([bx - rb, by - rb, bx + rb, by + rb], outline=_BRASS_L, width=10)
    d.ellipse([bx - rb + 12, by - rb + 12, bx + rb - 12, by + rb - 12],
              outline=(120, 96, 52, 140), width=2)
    for k in range(8):
        a = math.radians(swing + k * 45)
        px, py = bx + (rb - 5) * math.cos(a), by + (rb - 5) * math.sin(a)
        d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=(70, 58, 40))
    a = math.radians(swing)
    d.line([(bx - (rb - 6) * math.cos(a), by - (rb - 6) * math.sin(a)),
            (bx + (rb - 6) * math.cos(a), by + (rb - 6) * math.sin(a))],
           fill=_BRASS_L, width=8)
    jewels.append((bx, by))

    # ── หน้าปัด skeleton กลาง: เฟืองใหญ่โปร่งหมุนใต้เข็ม ──
    _paste_gear(base, cx, cy, _gear_sprite(36, 130, hole=0.42, spokes=6), -mn * 0.75)
    d = ImageDraw.Draw(base)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=_BRASS, width=6)
    d.ellipse([cx - R + 13, cy - R + 13, cx + R - 13, cy + R - 13],
              outline=(120, 96, 52), width=2)
    for i in range(60):                          # ขีดนาทีรอบวง
        a = math.radians(i * 6 - 90)
        r1_, r2_ = (R - 36, R - 15) if i % 5 == 0 else (R - 25, R - 15)
        w = 6 if i % 15 == 0 else (4 if i % 5 == 0 else 2)
        d.line([(cx + r1_ * math.cos(a), cy + r1_ * math.sin(a)),
                (cx + r2_ * math.cos(a), cy + r2_ * math.sin(a))],
               fill=_BRASS_L if i % 5 == 0 else (150, 128, 92), width=w)

    # ── เข็ม breguet น้ำเงินเหล็ก (โพลิกอนเรียวปลาย + วงพระจันทร์) ──
    BLUE, BLUE_L = (46, 72, 152), (92, 122, 205)

    def hand(ang, L, wid, back=26):
        a = math.radians(ang - 90)
        ux, uy = math.cos(a), math.sin(a)
        px, py = -uy, ux
        d.polygon([(cx - back * ux + wid * .5 * px, cy - back * uy + wid * .5 * py),
                   (cx + L * ux, cy + L * uy),
                   (cx - back * ux - wid * .5 * px, cy - back * uy - wid * .5 * py)],
                  fill=BLUE)
        d.line([(cx - back * ux, cy - back * uy),
                (cx + L * 0.97 * ux, cy + L * 0.97 * uy)], fill=BLUE_L, width=2)
        rr = L * 0.70                            # วงพระจันทร์ breguet
        hx, hy = cx + rr * ux, cy + rr * uy
        d.ellipse([hx - 11, hy - 11, hx + 11, hy + 11], outline=BLUE, width=6)

    hand(hr * 30, R * 0.52, 20)
    hand(mn * 6, R * 0.78, 13)
    a = math.radians(sec * 6 - 90)
    d.line([(cx - 34 * math.cos(a), cy - 34 * math.sin(a)),
            (cx + R * 0.86 * math.cos(a), cy + R * 0.86 * math.sin(a))],
           fill=(198, 56, 60), width=3)
    d.ellipse([cx - 34 * math.cos(a) - 8, cy - 34 * math.sin(a) - 8,
               cx - 34 * math.cos(a) + 8, cy - 34 * math.sin(a) + 8],
              fill=(198, 56, 60))                # ตุ้มถ่วงเข็มวินาที

    # ── ทับทิม jewel bearing + chaton ทองที่แกนทุกจุด ──
    jewels.append((cx, cy))
    for (jx, jy) in jewels:
        d.ellipse([jx - 11, jy - 11, jx + 11, jy + 11], outline=(170, 140, 74), width=3)
        d.ellipse([jx - 7, jy - 7, jx + 7, jy + 7], fill=(168, 32, 52))
        d.ellipse([jx - 3, jy - 5, jx + 1, jy - 1], fill=(235, 120, 130))  # ประกายบนทับทิม
    return base.convert("RGB")


# ── สไตล์: sun (ไล่แสงตะวัน — ท้องฟ้าเปลี่ยนสีตามเวลาจริง) ─────────────────────
# keyframe ท้องฟ้า: (ชั่วโมง, สีบน, สีกลาง, สีขอบฟ้า)
_SKY = [
    (0.0,  (3, 6, 20),    (8, 12, 34),    (14, 20, 48)),
    (4.7,  (6, 10, 32),   (24, 22, 58),   (60, 40, 70)),
    (6.0,  (30, 50, 110), (120, 80, 120), (255, 150, 90)),    # รุ่งอรุณ
    (7.5,  (70, 130, 215), (130, 180, 235), (220, 210, 190)),
    (12.0, (52, 120, 230), (120, 180, 245), (170, 215, 250)),  # เที่ยง
    (16.5, (60, 110, 215), (140, 170, 230), (215, 190, 160)),
    (18.3, (70, 60, 140), (180, 90, 110), (255, 130, 70)),     # อาทิตย์ตก
    (19.6, (18, 20, 64),  (50, 36, 86),   (110, 60, 86)),
    (21.0, (6, 9, 28),    (12, 16, 42),   (20, 26, 56)),
    (24.0, (3, 6, 20),    (8, 12, 34),    (14, 20, 48)),
]
_SUNRISE, _SUNSET = 6.0, 18.75
_SUN_SCENE = {"key": None, "img": None}
_SUN_MTN: dict = {}


def _sky_cols(td):
    for i in range(len(_SKY) - 1):
        h0, *c0 = _SKY[i]
        h1, *c1 = _SKY[i + 1]
        if h0 <= td <= h1:
            f = (td - h0) / (h1 - h0)
            return [tuple(int(a + (b - a) * f) for a, b in zip(ca, cb))
                    for ca, cb in zip(c0, c1)]
    return list(_SKY[0][1:])


def _sun_mountains(W, H, horizon):
    key = (W, H)
    if key not in _SUN_MTN:
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        far = [(0, horizon)]
        for x in range(0, W + 40, 40):
            far.append((x, horizon - 34 - 52 * abs(math.sin(x * 0.0043 + 1.3))
                        - 20 * abs(math.sin(x * 0.011 + 0.4))))
        far += [(W, horizon)]
        d.polygon(far, fill=(24, 28, 42, 255))
        near = [(0, horizon)]
        for x in range(0, W + 30, 30):
            near.append((x, horizon - 10 - 30 * abs(math.sin(x * 0.006 + 0.2))))
        near += [(W, horizon)]
        d.polygon(near, fill=(10, 12, 18, 255))
        _SUN_MTN[key] = ov
    return _SUN_MTN[key]


def _r_sun(W, H, now, t):
    td = now.hour + now.minute / 60 + now.second / 3600
    horizon = H - 104
    key = (now.hour, now.minute)
    if _SUN_SCENE["key"] != key:                 # แคชฉากต่อ 1 นาที (ฟ้า+ตะวัน+ภูเขา)
        top, mid, bot = _sky_cols(td)
        h2 = horizon // 2
        col = np.vstack([np.linspace(top, mid, h2, endpoint=False),
                         np.linspace(mid, bot, horizon - h2)]).astype(np.float32)
        sky = np.repeat(col[:, None, :], W, axis=1).astype(np.uint8)
        img = Image.new("RGB", (W, H), (9, 11, 15))
        img.paste(Image.fromarray(sky, "RGB"), (0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        if _SUNRISE <= td <= _SUNSET:            # ดวงอาทิตย์โคจรตามเวลาจริง
            f = (td - _SUNRISE) / (_SUNSET - _SUNRISE)
            elev = math.sin(f * math.pi)
            sx = 90 + f * (W - 180)
            sy = horizon - 26 - elev * (horizon - 150)
            warm = 1 - elev                      # ใกล้ขอบฟ้า = ส้มอุ่น
            scol = tuple(int(a + (b - a) * warm) for a, b in zip((255, 216, 130), (255, 128, 56)))
            strokes = Image.new("RGB", (W, H), (0, 0, 0))
            dg = ImageDraw.Draw(strokes)
            dg.ellipse([sx - 36, sy - 36, sx + 36, sy + 36], fill=scol)
            img = _glow_add(img, strokes, 44, 1.0)
            img = _glow_add(img, strokes, 15, 0.9)
            d = ImageDraw.Draw(img, "RGBA")
            d.ellipse([sx - 30, sy - 30, sx + 30, sy + 30], fill=(255, 246, 216))
        else:                                    # พระจันทร์เสี้ยว
            nf = ((td - _SUNSET) % 24) / (24 - (_SUNSET - _SUNRISE))
            elev = math.sin(nf * math.pi)
            sx = 90 + nf * (W - 180)
            sy = horizon - 26 - elev * (horizon - 160)
            strokes = Image.new("RGB", (W, H), (0, 0, 0))
            ImageDraw.Draw(strokes).ellipse([sx - 26, sy - 26, sx + 26, sy + 26],
                                            fill=(120, 130, 160))
            img = _glow_add(img, strokes, 30, 0.8)
            d = ImageDraw.Draw(img, "RGBA")
            d.ellipse([sx - 24, sy - 24, sx + 24, sy + 24], fill=(232, 236, 246))
            bgc = tuple(int(v) for v in col[min(horizon - 1, max(0, int(sy - 10)))])
            d.ellipse([sx - 24 + 15, sy - 24 - 7, sx + 24 + 15, sy + 24 - 7], fill=bgc)
        img.paste(_sun_mountains(W, H, horizon), (0, 0), _sun_mountains(W, H, horizon))
        d = ImageDraw.Draw(img)
        d.rectangle([0, horizon, W, H], fill=(9, 11, 15))
        _SUN_SCENE["key"], _SUN_SCENE["img"] = key, img
    frame = _SUN_SCENE["img"].copy()
    d = ImageDraw.Draw(frame, "RGBA")
    # ดาวระยิบ (เฉพาะกลางคืน) — วาดต่อเฟรมให้กะพริบ
    if td < 5.0 or td >= 20.0:
        nf = 1.0
    elif 5.0 <= td < 6.5:
        nf = 1.0 - (td - 5.0) / 1.5
    elif 18.5 <= td < 20.0:
        nf = (td - 18.5) / 1.5
    else:
        nf = 0.0
    if nf > 0.03:
        for i in range(110):
            sx = (i * 397 + 53) % W
            sy = (i * 211 + 31) % (horizon - 70)
            tw = 0.5 + 0.5 * math.sin(t * 2.4 + i * 1.7)
            aa = int(210 * nf * tw)
            if aa > 12:
                d.ellipse([sx - 1, sy - 1, sx + 1, sy + 1], fill=(255, 255, 255, aa))
    # เวลา (ขอบเข้มบาง ๆ ให้อ่านออกทุกสีฟ้า)
    d.text((W / 2, H / 2 - 26), now.strftime("%H:%M"), font=_f("thin", 216),
           fill=(250, 250, 252), anchor="mm", stroke_width=4, stroke_fill=(14, 17, 26))
    d.text((W / 2 + 350, H / 2 + 46), now.strftime(":%S"), font=_f("thin", 72),
           fill=(235, 237, 242), anchor="mm", stroke_width=3, stroke_fill=(14, 17, 26))
    d.text((W / 2, H - 46), now.strftime("%A  %d %B %Y").upper(), font=_f("thin", 32),
           fill=(215, 218, 226), anchor="mm", stroke_width=2, stroke_fill=(10, 12, 18))
    return frame


# ── สไตล์: lumo (analog เข็มเรืองแสง — Casio กลางคืน / ana-digi) ───────────────
def _r_lumo(W, H, now, t):
    cx, cy = W / 2, H / 2
    R = H / 2 - 22
    ACC = (130, 255, 205)                       # lume เขียว-ฟ้า

    def build(W, H):
        img = Image.new("RGB", (W, H), (6, 10, 12))
        d = ImageDraw.Draw(img, "RGBA")
        d.ellipse([cx - R - 12, cy - R - 12, cx + R + 12, cy + R + 12], fill=(24, 30, 34))
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(9, 13, 16))
        d.ellipse([cx - R + 6, cy - R + 6, cx + R - 6, cy + R - 6],
                  outline=(38, 48, 54), width=3)
        return img
    base = _bg("lumo", W, H, build)
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    d = ImageDraw.Draw(base, "RGBA")

    for i in range(12):                          # หลักชั่วโมง = แท่ง lume
        a = math.radians(i * 30 - 90)
        r1, r2 = R - 42, R - 16
        w = 13 if i % 3 == 0 else 7
        x1, y1 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
        x2, y2 = cx + r2 * math.cos(a), cy + r2 * math.sin(a)
        d.line([(x1, y1), (x2, y2)], fill=ACC, width=w)
        dg.line([(x1, y1), (x2, y2)], fill=(70, 190, 150), width=w + 3)

    sec = now.second + now.microsecond / 1e6
    mn = now.minute + sec / 60
    hr = (now.hour % 12) + mn / 60

    def hand(ang, length, width, back=22):
        a = math.radians(ang - 90)
        x2, y2 = cx + length * math.cos(a), cy + length * math.sin(a)
        xb, yb = cx - back * math.cos(a), cy - back * math.sin(a)
        d.line([(xb, yb), (x2, y2)], fill=ACC, width=width)
        dg.line([(xb, yb), (x2, y2)], fill=(70, 190, 150), width=width + 3)

    hand(hr * 30, R * 0.50, 16)                  # ชั่วโมง lume
    hand(mn * 6, R * 0.72, 11)                   # นาที lume
    a = math.radians(sec * 6 - 90)               # วินาที ส้ม (ไม่ lume)
    d.line([(cx - 24 * math.cos(a), cy - 24 * math.sin(a)),
            (cx + R * 0.80 * math.cos(a), cy + R * 0.80 * math.sin(a))],
           fill=(255, 140, 60), width=3)
    base = _glow_add(base, strokes, 15, 1.15)    # เรืองแสง
    d = ImageDraw.Draw(base, "RGBA")
    d.ellipse([cx - 11, cy - 11, cx + 11, cy + 11], fill=(150, 220, 190))
    d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(20, 30, 26))
    # ana-digi: ซ้าย=วัน ขวา=เวลาดิจิทัล lume (Casio)
    lx = cx - R - (cx - R) * 0.5
    rx = cx + R + (W - (cx + R)) * 0.5
    _text_c(d, lx, cy - 26, now.strftime("%a").upper(), _f("sans", 66), (120, 210, 180))
    _text_c(d, lx, cy + 40, now.strftime("%d %b").upper(), _f("mono", 34), (80, 150, 130))
    _text_c(d, rx, cy - 20, now.strftime("%H:%M"), _f("nixie", 96), (150, 255, 210))
    _text_c(d, rx, cy + 48, now.strftime(":%S"), _f("nixie", 44), (90, 180, 150))
    return base


# ── สไตล์: world (นาฬิกาโลก หลายเมือง) ────────────────────────────────────────
_CITIES = [("BANGKOK", 7), ("TOKYO", 9), ("LONDON", 1), ("NEW YORK", -4), ("LOS ANGELES", -7)]
# offset โดยประมาณ (ฤดูร้อน เหนือ) — ไม่คิด DST อัตโนมัติ


def _r_world(W, H, now, t):
    n = len(_CITIES)
    colw = W / n
    utc = datetime.now(timezone.utc)

    def build(W, H):
        img = Image.new("RGB", (W, H), (8, 12, 20))
        d = ImageDraw.Draw(img)
        for i in range(1, n):                    # เส้นแบ่งคอลัมน์
            d.line([(i * colw, 40), (i * colw, H - 40)], fill=(26, 34, 48), width=2)
        _text_c(d, W / 2, 26, "WORLD CLOCK", _f("mono", 24), (70, 110, 140))
        return img
    base = _bg("world", W, H, build)
    d = ImageDraw.Draw(base, "RGBA")
    for i, (city, off) in enumerate(_CITIES):
        cx = colw * (i + 0.5)
        lt = utc + timedelta(hours=off)
        local = (off == 7)
        accent = (255, 210, 130) if local else (200, 220, 235)
        # ไอคอนกลางวัน/คืน (วงกลม=แดด, วงแหวน=จันทร์)
        day = 6 <= lt.hour < 18
        iy = 92
        if day:
            d.ellipse([cx - 13, iy - 13, cx + 13, iy + 13], fill=(255, 200, 90))
        else:
            d.ellipse([cx - 13, iy - 13, cx + 13, iy + 13], outline=(150, 175, 210), width=3)
        _text_c(d, cx, 150, city, _f("mono", 28), accent)
        _text_c(d, cx, H / 2 + 20, lt.strftime("%H:%M"), _f("sans", 96), accent)
        _text_c(d, cx, H / 2 + 96, lt.strftime(":%S  %a %d").upper(),
                _f("mono", 26), (120, 145, 170))
        if local:
            d.line([(cx - 90, 178), (cx + 90, 178)], fill=(255, 210, 130), width=3)
            _text_c(d, cx, H - 34, "★ LOCAL", _f("mono", 22), (255, 210, 130))
    return base


# ── สไตล์: cyberpunk (นีออน HUD + chromatic glitch) ───────────────────────────
_CYBER: dict = {}


def _r_cyberpunk(W, H, now, t):
    def build(W, H):
        # ไล่เฉดม่วง→ดำ + กริด + สแกนไลน์
        top = Image.new("RGB", (W, H), (26, 8, 40))
        bot = Image.new("RGB", (W, H), (4, 2, 10))
        m = Image.linear_gradient("L").resize((W, H))
        img = Image.composite(bot, top, m)
        d = ImageDraw.Draw(img, "RGBA")
        for gx in range(0, W, 60):
            d.line([(gx, 0), (gx, H)], fill=(120, 40, 160, 26), width=1)
        for gy in range(0, H, 60):
            d.line([(0, gy), (W, gy)], fill=(120, 40, 160, 26), width=1)
        for sy in range(0, H, 4):                # scanlines
            d.line([(0, sy), (W, sy)], fill=(0, 0, 0, 40), width=1)
        # กรอบ HUD + มุม
        d.rectangle([20, 20, W - 20, H - 20], outline=(0, 230, 255, 120), width=2)
        for (mx, my, dx, dy) in [(20, 20, 1, 1), (W - 20, 20, -1, 1),
                                 (20, H - 20, 1, -1), (W - 20, H - 20, -1, -1)]:
            d.line([(mx, my), (mx + dx * 46, my)], fill=(255, 40, 170, 220), width=4)
            d.line([(mx, my), (mx, my + dy * 46)], fill=(255, 40, 170, 220), width=4)
        return img
    base = _bg("cyberpunk", W, H, build)
    cx, cy = W / 2, H / 2 - 10
    txt = now.strftime("%H:%M:%S")
    fnt = _f("nixie", 250)
    # chromatic aberration (แดง/ฟ้าเหลื่อม) + glow
    strokes = Image.new("RGB", (W, H), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    dg.text((cx, cy), txt, font=fnt, fill=(0, 200, 255), anchor="mm")
    base = _glow_add(base, strokes, 22, 1.0)
    d = ImageDraw.Draw(base)
    off = 6 + 3 * math.sin(t * 6)                # เหลื่อมสั่นเล็กน้อย
    d.text((cx - off, cy), txt, font=fnt, fill=(255, 30, 120), anchor="mm")     # magenta
    d.text((cx + off, cy), txt, font=fnt, fill=(0, 220, 255), anchor="mm")      # cyan
    d.text((cx, cy), txt, font=fnt, fill=(235, 245, 255), anchor="mm")          # core
    # accent HUD text
    _text_c(d, 60, 60, "SYS.TIME", _f("mono", 30), (0, 230, 255), anchor="lm")
    _text_c(d, W - 60, 60, now.strftime("%Y.%m.%d"), _f("mono", 30), (255, 40, 170), anchor="rm")
    _text_c(d, cx, H - 52, "// NEO-BANGKOK  " + now.strftime("%A").upper() + "  2077 //",
            _f("mono", 30), (0, 230, 255))
    # แถบสแกนวิ่ง
    sy = int((t * 120) % H)
    d.line([(20, sy), (W - 20, sy)], fill=(255, 40, 170), width=2)
    return base


# ═════════════════════════════════════════════════════════════════════════════
#  render_art — นาฬิกาแบบจัตุรัส S×S สำหรับช่องปกอัลบั้มของ vibe
#  หน้าปัดกลม (analog/lumo/mech) = ครอปกลางจากเรนเดอร์เต็ม; ที่เหลือมีเวอร์ชันย่อ
# ═════════════════════════════════════════════════════════════════════════════
def _a_digital(S, now, on, off, glow, blur, base, date_col):
    gi, gm, colw = 4, 7, 12
    # ผังคือ dd:dd:dd → กว้างรวม = 6dw + 2colw + 4gi + 4gm (ประมาณ) — เผื่อขอบ 20
    dw = (S - 2 * colw - 4 * gi - 4 * gm - 20) // 6
    dh, th = int(dw * 1.9), max(6, dw // 4)
    base = _draw_digital(base, S, S, now.hour, now.minute, now.second,
                         on=on, off=off, glow_col=glow, blur=blur,
                         dw=dw, dh=dh, th=th, gi=gi, gm=gm, colw=colw)
    _text_c(ImageDraw.Draw(base), S / 2, S / 2 + dh / 2 + 32,
            now.strftime("%a %d %b").upper(), _f("mono", 18), date_col)
    return base


def _a_seg7(S, now, t):
    base = Image.new("RGB", (S, S), (8, 6, 6))
    v = _vignette(S, S, 100, 255)
    base = ImageChops.multiply(base, Image.merge("RGB", (v, v, v)))
    return _a_digital(S, now, (255, 42, 38), (40, 8, 8), (150, 20, 18), 10,
                      base, (120, 30, 26))


def _a_vfd(S, now, t):
    base = Image.new("RGB", (S, S), (4, 12, 12))
    d = ImageDraw.Draw(base)
    for gx in range(0, S, 5):
        d.line([(gx, 0), (gx, S)], fill=(6, 20, 20))
    return _a_digital(S, now, (90, 240, 210), (12, 46, 44), (40, 150, 130), 9,
                      base, (70, 190, 170))


def _a_lcd(S, now, t):
    base = Image.new("RGB", (S, S), (150, 168, 78))
    ImageDraw.Draw(base).rounded_rectangle([8, 8, S - 8, S - 8], radius=18,
                                           outline=(60, 72, 30), width=4)
    return _a_digital(S, now, (26, 34, 12), (132, 150, 66), (0, 0, 0), 0,
                      base, (40, 52, 18))


def _a_nixie(S, now, t):
    base = Image.new("RGB", (S, S), (12, 9, 8))
    v = _vignette(S, S, 90, 255)
    base = ImageChops.multiply(base, Image.merge("RGB", (v, v, v)))
    dw, colw, gap = int(S * 0.19), int(S * 0.068), int(S * 0.024)
    dh = int(S * 0.44)
    total = 4 * dw + colw + 4 * gap
    x0, y = (S - total) / 2, (S - dh) / 2 - 8
    digits = f"{now.hour:02d}{now.minute:02d}"
    fnt = _f("nixie", int(dh * 0.82))
    d = ImageDraw.Draw(base, "RGBA")
    xs, x = [], x0
    for i in range(5):                            # d d c d d
        if i == 2:
            x += colw + gap
            continue
        gx0, gy0, gx1, gy1 = x - 5, y - 16, x + dw + 5, y + dh + 12
        d.rounded_rectangle([gx0, gy0, gx1, gy1], radius=26,
                            fill=(18, 20, 26, 170), outline=(44, 48, 58, 210), width=2)
        d.rounded_rectangle([gx0 + 3, gy1 - 18, gx1 - 3, gy1 + 3], radius=6,
                            fill=(34, 28, 22, 255))
        xs.append(x + dw / 2)
        x += dw + gap
    strokes = Image.new("RGB", (S, S), (0, 0, 0))
    dg = ImageDraw.Draw(strokes)
    for cx_, ch in zip(xs, digits):
        d.text((cx_, y + dh / 2), "8", font=fnt, fill=(90, 44, 20, 36), anchor="mm")
        dg.text((cx_, y + dh / 2), ch, font=fnt, fill=(255, 96, 20), anchor="mm")
    ccx = x0 + 2 * dw + gap + colw / 2 + gap / 2
    if now.second % 2 == 0:
        for cy_ in (y + dh * 0.35, y + dh * 0.65):
            dg.ellipse([ccx - 5, cy_ - 5, ccx + 5, cy_ + 5], fill=(255, 110, 30))
    base = _glow_add(base, strokes, 14, 0.9)
    base = _glow_add(base, strokes, 6, 1.0)
    d2 = ImageDraw.Draw(base)
    for cx_, ch in zip(xs, digits):
        d2.text((cx_, y + dh / 2), ch, font=fnt, fill=(255, 130, 40), anchor="mm")
        d2.text((cx_, y + dh / 2), ch, font=_f("nixie", int(dh * 0.76)),
                fill=(255, 190, 126), anchor="mm")
    if now.second % 2 == 0:
        for cy_ in (y + dh * 0.35, y + dh * 0.65):
            d2.ellipse([ccx - 5, cy_ - 5, ccx + 5, cy_ + 5], fill=(255, 150, 70))
    _text_c(d2, S / 2, S - 26, now.strftime("%a %d %b").upper(),
            _f("mono", 17), (150, 84, 36))
    return base


def _a_flip(S, now, t):
    base = Image.new("RGB", (S, S), (14, 14, 16))
    g = 14
    cw = chh = (S - 3 * g) // 2
    fnt = _f("sans", int(chh * 0.66))
    digits = f"{now.hour:02d}{now.minute:02d}"
    pos = [(g, g), (2 * g + cw, g), (g, 2 * g + chh), (2 * g + cw, 2 * g + chh)]
    for i, cur in enumerate(digits):
        key = ("art", i)
        st = _FLIP.get(key)
        if st is None:
            _FLIP[key] = st = {"d": cur, "old": cur, "ct": t - 1.0}
        elif st["d"] != cur:
            st["old"] = st["d"]; st["d"] = cur; st["ct"] = t
        p = min(1.0, (t - st["ct"]) / _FLIP_DUR)
        _draw_flip(base, pos[i][0], pos[i][1], cw, chh, st["old"], st["d"], p, fnt)
    return base


def _a_neon(S, now, t):
    img = Image.new("RGB", (S, S), (14, 10, 20))
    top = Image.new("RGB", (S, S), (30, 16, 40))
    m = Image.linear_gradient("L").resize((S, S))
    base = Image.composite(img, top, m)
    txt = now.strftime("%H:%M")
    fnt = _f("sans", int(S * 0.30))
    strokes = Image.new("RGB", (S, S), (0, 0, 0))
    ImageDraw.Draw(strokes).text((S / 2, S / 2 - 14), txt, font=fnt,
                                 fill=(255, 60, 170), anchor="mm")
    base = _glow_add(base, strokes, 16, 1.2)
    base = _glow_add(base, strokes, 7, 1.0)
    d = ImageDraw.Draw(base)
    d.text((S / 2, S / 2 - 14), txt, font=fnt, fill=(255, 190, 230), anchor="mm",
           stroke_width=2, stroke_fill=(255, 90, 180))
    d.text((S / 2, S / 2 + int(S * 0.20)), now.strftime(":%S"), font=_f("sans", int(S * 0.11)),
           fill=(120, 230, 255), anchor="mm")
    return base


def _a_minimal(S, now, t):
    base = Image.new("RGB", (S, S), (10, 10, 12))
    d = ImageDraw.Draw(base)
    d.text((S / 2, S / 2 - 16), now.strftime("%H:%M"), font=_f("thin", int(S * 0.30)),
           fill=(238, 240, 246), anchor="mm")
    d.text((S / 2, S / 2 + int(S * 0.17)), now.strftime("%a %d %b").upper(),
           font=_f("thin", 20), fill=(120, 124, 134), anchor="mm")
    frac = (now.second + now.microsecond / 1e6) / 60
    d.rectangle([0, S - 5, int(S * frac), S], fill=(90, 140, 220))
    return base


def _a_sun(S, now, t):
    td = now.hour + now.minute / 60 + now.second / 3600
    horizon = S - 44
    top, mid, bot = _sky_cols(td)
    h2 = horizon // 2
    col = np.vstack([np.linspace(top, mid, h2, endpoint=False),
                     np.linspace(mid, bot, horizon - h2)]).astype(np.uint8)
    base = Image.new("RGB", (S, S), (9, 11, 15))
    base.paste(Image.fromarray(np.repeat(col[:, None, :], S, axis=1), "RGB"), (0, 0))
    d = ImageDraw.Draw(base, "RGBA")
    if _SUNRISE <= td <= _SUNSET:
        f = (td - _SUNRISE) / (_SUNSET - _SUNRISE)
        sx = 36 + f * (S - 72)
        sy = horizon - 14 - math.sin(f * math.pi) * (horizon - 70)
        strokes = Image.new("RGB", (S, S), (0, 0, 0))
        warm = 1 - math.sin(f * math.pi)
        scol = tuple(int(a + (b - a) * warm) for a, b in zip((255, 216, 130), (255, 128, 56)))
        ImageDraw.Draw(strokes).ellipse([sx - 15, sy - 15, sx + 15, sy + 15], fill=scol)
        base = _glow_add(base, strokes, 18, 1.0)
        d = ImageDraw.Draw(base, "RGBA")
        d.ellipse([sx - 12, sy - 12, sx + 12, sy + 12], fill=(255, 246, 216))
    else:
        nf = ((td - _SUNSET) % 24) / (24 - (_SUNSET - _SUNRISE))
        sx = 36 + nf * (S - 72)
        sy = horizon - 14 - math.sin(nf * math.pi) * (horizon - 74)
        d.ellipse([sx - 11, sy - 11, sx + 11, sy + 11], fill=(232, 236, 246))
        bgc = tuple(int(v) for v in col[min(horizon - 1, max(0, int(sy - 4)))])
        d.ellipse([sx - 11 + 7, sy - 11 - 3, sx + 11 + 7, sy + 11 - 3], fill=bgc)
        for i in range(36):
            zx, zy = (i * 397 + 53) % S, (i * 211 + 31) % (horizon - 50)
            aa = int(190 * (0.5 + 0.5 * math.sin(t * 2.4 + i * 1.7)))
            d.ellipse([zx - 1, zy - 1, zx + 1, zy + 1], fill=(255, 255, 255, aa))
    for (colr, amp, ph0) in (((24, 28, 42, 255), 26, 1.3), ((10, 12, 18, 255), 16, 0.2)):
        pts = [(0, horizon)]
        for x in range(0, S + 20, 20):
            pts.append((x, horizon - 8 - amp * abs(math.sin(x * 0.012 + ph0))))
        pts += [(S, horizon)]
        d.polygon(pts, fill=colr)
    d.rectangle([0, horizon, S, S], fill=(9, 11, 15))
    d.text((S / 2, S / 2 - 6), now.strftime("%H:%M"), font=_f("thin", int(S * 0.26)),
           fill=(250, 250, 252), anchor="mm", stroke_width=3, stroke_fill=(14, 17, 26))
    return base


def _a_word(S, now, t):
    return _r_word(S, S, now, t)


_ART_RENDER = {
    "seg7": _a_seg7, "vfd": _a_vfd, "lcd": _a_lcd, "nixie": _a_nixie,
    "flip": _a_flip, "neon": _a_neon, "minimal": _a_minimal,
    "sun": _a_sun, "word": _a_word,
}


def render_art(style, S, now, t=0.0):
    """นาฬิกาจัตุรัส S×S สำหรับช่องปกอัลบั้ม — รองรับทุกสไตล์ (world/cyberpunk → หน้าปัดเรืองแสง)"""
    try:
        fn = _ART_RENDER.get(style)
        if fn is not None:
            return fn(S, now, t)
        dial = style if style in ("analog", "lumo", "mech") else "lumo"
        full = _RENDER[dial](1920, 462, now, t)
        x0 = (1920 - 462) // 2
        return full.crop((x0, 0, x0 + 462, 462)).resize((S, S), Image.LANCZOS)
    except Exception:
        img = Image.new("RGB", (S, S), (10, 8, 7))
        ImageDraw.Draw(img).text((S / 2, S / 2), now.strftime("%H:%M"),
                                 font=_f("sans", int(S * 0.28)),
                                 fill=(255, 138, 40), anchor="mm")
        return img


_RENDER = {
    "nixie": _r_nixie, "flip": _r_flip, "vfd": _r_vfd, "seg7": _r_seg7,
    "lcd": _r_lcd, "analog": _r_analog, "lumo": _r_lumo, "mech": _r_mech,
    "sun": _r_sun, "neon": _r_neon, "word": _r_word, "world": _r_world,
    "cyberpunk": _r_cyberpunk, "minimal": _r_minimal,
}


def render(style, W, H, now, t=0.0):
    fn = _RENDER.get(style, _r_nixie)
    try:
        return fn(W, H, now, t)
    except Exception:
        img = Image.new("RGB", (W, H), (10, 8, 7))
        ImageDraw.Draw(img).text((W / 2, H / 2), now.strftime("%H:%M:%S"),
                                 font=_f("sans", 200), fill=(255, 138, 40), anchor="mm")
        return img
