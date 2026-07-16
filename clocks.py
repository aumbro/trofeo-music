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
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

STYLES = ["nixie", "flip", "vfd", "seg7", "lcd", "analog", "neon", "word", "minimal"]
STYLE_LABELS = {
    "nixie": "Nixie (หลอดเรืองส้ม)", "flip": "Flip (ป้ายพลิก)",
    "vfd": "VFD (จอเขียวเรือง)", "seg7": "7-Segment (LED แดง)",
    "lcd": "LCD (Casio เขียว)", "analog": "เข็ม (เลขโรมัน)",
    "neon": "Neon (นีออน)", "word": "Word (นาฬิกาคำ)", "minimal": "Minimal (เรียบ)",
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


def _digital_layout(W, H, dw, dh, th, gapd, colw):
    """วางเลข HH:MM:SS ให้อยู่กลางจอ → list ของ (kind, char/None, x) + dh, y"""
    tokens = ["d", "d", "c", "d", "d", "c", "d", "d"]
    total = 6 * dw + 2 * colw + 7 * gapd
    x = (W - total) / 2
    y = (H - dh) / 2
    out = []
    for tk in tokens:
        if tk == "d":
            out.append(("d", x, dw))
            x += dw + gapd
        else:
            out.append(("c", x, colw))
            x += colw + gapd
    return out, y


def _draw_digital(base, W, H, hh, mm, ss, on, off, glow_col, blur,
                  dw=178, dh=298, th=33, gapd=30, colw=52, colon=True):
    """วาดนาฬิกาดิจิทัล 7-seg ทับ base (in-place-ish) — คืน base ใหม่ที่บวก glow แล้ว"""
    layout, y = _digital_layout(W, H, dw, dh, th, gapd, colw)
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
    cell = min((W - 120) / cols, (H - 60) / rows)
    gx = (W - cell * cols) / 2
    gy = (H - cell * rows) / 2
    fnt = _f("mono", int(cell * 0.66))

    def build(W, H):
        img = Image.new("RGB", (W, H), (12, 12, 16))
        d = ImageDraw.Draw(img)
        for r in range(rows):
            for c in range(cols):
                d.text((gx + c * cell + cell / 2, gy + r * cell + cell / 2),
                       _WORD_ROWS[r][c], font=fnt, fill=(46, 48, 56), anchor="mm")
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
            xx = gx + c * cell + cell / 2
            yy = gy + r * cell + cell / 2
            db.text((xx, yy), _WORD_ROWS[r][c], font=fnt, fill=(255, 214, 140), anchor="mm")
            dg.text((xx, yy), _WORD_ROWS[r][c], font=fnt, fill=(200, 150, 70), anchor="mm")
    base = _glow_add(base, strokes, 14)
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


_RENDER = {
    "nixie": _r_nixie, "flip": _r_flip, "vfd": _r_vfd, "seg7": _r_seg7,
    "lcd": _r_lcd, "analog": _r_analog, "neon": _r_neon, "word": _r_word,
    "minimal": _r_minimal,
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
