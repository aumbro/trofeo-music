"""
trackmap.py — สร้าง/วาด track minimap จากพิกัดรถ (สำหรับ race.py)

สองโหมด:
  - live learning: ป้อน add(x, y, sector, lap) ทุกเฟรม → จดเส้นทาง (decimate ตามระยะ)
    แล้ว "ล็อก" รูปสนามเมื่อจบรอบแรก (lap เพิ่มขึ้น) → จากนั้นโชว์รูปคงที่ + จุดรถวิ่งตามเส้น
  - seeded: set_outline(points) ป้อนเส้นครบวงมาเลย (ใช้ตอน demo/preview ให้ map เต็มทันที)

วาด: render_into(d, box, tel, font_fn)
  - เส้นสนามสีตาม sector (S1/S2/S3) — ธงเหลือง/แดง = ย้อมทั้งวง
  - ขีด start/finish + จุดรถปัจจุบัน (ถ้า tel.has_pos)
  - ระหว่างเรียนรู้ = โชว์เส้นบางส่วน + "MAPPING…"
"""
from __future__ import annotations

import math

SECTOR_COLORS = {1: (90, 180, 255), 2: (120, 220, 140), 3: (240, 180, 80)}
FLAG_TINT = {"YELLOW": (240, 210, 60), "RED": (230, 70, 70)}
PANEL_EDGE = (46, 54, 66)
INK = (238, 242, 250)
MUTE = (120, 130, 146)


class TrackMap:
    def __init__(self, min_points: int = 24):
        self.min_points = min_points
        self.pts = []                 # [(x, y, sector)] ระหว่างเรียนรู้
        self.outline = None           # ล็อกแล้ว = [(x, y, sector)]
        self.bounds = None            # (minx, miny, maxx, maxy)
        self._start_lap = None
        self._last_added = None
        self._lb = None               # live bounds [minx, miny, maxx, maxy]
        self._rot = 0.0               # หมุนตอนวาด (เรเดียน) — heading integration ไม่รู้ทิศเหนือ
        self._flip = False            # กลับด้านซ้าย-ขวา

    # ── ปรับทิศตอนวาด (ไม่ต้องเรียนรู้ใหม่) ─────────────────────────────────
    def set_orientation(self, rotate_deg: float = 0.0, flip: bool = False):
        self._rot = math.radians(rotate_deg)
        self._flip = flip

    # ── seed เส้นครบวง (demo/preview) ───────────────────────────────────────
    def set_outline(self, points):
        if not points:
            return
        n = len(points)
        # normalize เป็น (x, y, sector, ncp) — เผื่อ input เป็น 3-tuple (demo/ไฟล์เก่า)
        # → ใส่ ncp = สัดส่วน index (เส้น demo/preview เรียงตามรอบอยู่แล้ว)
        self.outline = [(p[0], p[1], p[2], p[3] if len(p) > 3 else i / n)
                        for i, p in enumerate(points)]
        xs = [p[0] for p in self.outline]
        ys = [p[1] for p in self.outline]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))

    # ── เซฟ/โหลดเส้นสนามที่เรียนรู้แล้ว (ไม่ต้องขับใหม่ทุกครั้ง) ──────────────
    def save(self, path):
        if self.outline is None:
            return
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"outline": self.outline}, f)

    def load(self, path):
        import json
        import os
        if not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.set_outline([tuple(p) for p in data["outline"]])
            return True
        except Exception:
            return False

    # ── live learning ──────────────────────────────────────────────────────
    # จุดในเส้นสนาม = (x, y, sector, ncp)  (ncp = ตำแหน่งบนสนาม 0..1 ตอนจดจุดนั้น)
    def _append(self, x, y, sector, ncp):
        if not self.pts:
            self._lb = [x, y, x, y]
        else:
            self._lb[0] = min(self._lb[0], x)
            self._lb[1] = min(self._lb[1], y)
            self._lb[2] = max(self._lb[2], x)
            self._lb[3] = max(self._lb[3], y)
        self.pts.append((x, y, sector, ncp))
        self._last_added = (x, y)

    def add(self, x, y, sector, lap, ncp=-1.0):
        if self.outline is not None:
            return
        if self._start_lap is None:
            self._start_lap = lap
        if self._last_added is None:
            self._append(x, y, sector, ncp)
        else:
            lx, ly = self._last_added
            diag = math.hypot(self._lb[2] - self._lb[0], self._lb[3] - self._lb[1]) or 1.0
            if math.hypot(x - lx, y - ly) >= diag * 0.003:   # เว้นระยะกันจุดถี่เกิน
                self._append(x, y, sector, ncp)
        # จบรอบแรก (lap เดินไปข้างหน้า) + จุดพอ → ล็อกรูปสนาม
        if lap > self._start_lap and len(self.pts) >= self.min_points:
            self.outline = list(self.pts)
            self.bounds = tuple(self._lb)

    @property
    def ready(self) -> bool:
        return self.outline is not None

    # ── วาดลงกล่อง box=(x0, y0, w, h) ───────────────────────────────────────
    def render_into(self, d, box, tel, font_fn):
        x0, y0, w, h = box
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=10, outline=PANEL_EDGE, width=2)
        d.text((x0 + 14, y0 + 10), "TRACK", font=font_fn(24), fill=MUTE, anchor="lm")

        pts = self.outline if self.outline is not None else self.pts
        bounds = self.bounds if self.outline is not None else (
            tuple(self._lb) if self._lb else None)

        if not pts or len(pts) < 2 or bounds is None:
            d.text((x0 + w / 2, y0 + h / 2), "MAPPING…",
                   font=font_fn(34), fill=MUTE, anchor="mm")
            if tel.has_pos:
                d.text((x0 + w / 2, y0 + h / 2 + 36), "● REC",
                       font=font_fn(22), fill=(235, 70, 70), anchor="mm")
            return

        # หมุน/กลับด้านตอนวาด (heading integration ไม่รู้ทิศเหนือ — ปรับที่นี่ได้เลย)
        cr, sr = math.cos(self._rot), math.sin(self._rot)
        fx = -1.0 if self._flip else 1.0

        def orient(px, py):
            px *= fx
            return (px * cr - py * sr, px * sr + py * cr)

        opts = [(*orient(px, py), sc) for (px, py, sc, _n) in pts]
        ncps = [p[3] for p in pts]
        xs = [p[0] for p in opts]
        ys = [p[1] for p in opts]
        minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
        pad = 26
        bw = (maxx - minx) or 1.0
        bh = (maxy - miny) or 1.0
        scale = min((w - 2 * pad) / bw, (h - 2 * pad) / bh)
        offx = x0 + (w - bw * scale) / 2
        offy = y0 + (h - bh * scale) / 2

        def to_screen(ox, oy):
            return (offx + (ox - minx) * scale,
                    offy + (maxy - oy) * scale)          # flip y ให้เหนืออยู่บน

        def ncp_dist(a, b):                              # ระยะบนสนาม (วนรอบ 0..1)
            if a < 0:
                return 9.0
            dd = abs((a % 1.0) - (b % 1.0))
            return min(dd, 1.0 - dd)

        tint = FLAG_TINT.get((tel.flag or "").upper())
        learning = self.outline is None
        has_ncp = any(n >= 0 for n in ncps)
        prev = None
        for (ox, oy, sc) in opts:
            cur = to_screen(ox, oy)
            if prev is not None:
                col = tint or SECTOR_COLORS.get(sc, (200, 200, 200))
                if learning:
                    col = tuple(int(c * 0.5) for c in col)
                d.line([prev, cur], fill=col, width=4)
            prev = cur

        # start/finish = จุดที่ ncp ใกล้ 0 (เส้นสตาร์ทจริง) ไม่งั้นจุดแรกของเส้น
        sf_i = min(range(len(pts)), key=lambda i: ncp_dist(ncps[i], 0.0)) if has_ncp else 0
        sfx, sfy = to_screen(*orient(pts[sf_i][0], pts[sf_i][1]))
        d.line([(sfx, sfy - 9), (sfx, sfy + 9)], fill=INK, width=3)

        # จุดรถปัจจุบัน: วางตาม ncp บนเส้น (ทนต่อ integration frame) ไม่งั้นใช้พิกัดตรง
        dot = None
        if self.outline is not None and has_ncp and tel.ncp is not None and tel.ncp >= 0:
            ci = min(range(len(pts)), key=lambda i: ncp_dist(ncps[i], tel.ncp))
            dot = orient(pts[ci][0], pts[ci][1])
        elif tel.has_pos:
            dot = orient(tel.x, tel.y)
        if dot is not None:
            cx, cy = to_screen(*dot)
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(20, 22, 28))   # ขอบเข้ม
            d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline=(255, 255, 255), width=3)
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255))

        if learning:
            d.text((x0 + w - 14, y0 + 10), "● REC",
                   font=font_fn(22), fill=(235, 70, 70), anchor="rm")
