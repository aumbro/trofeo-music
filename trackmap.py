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

    # ── seed เส้นครบวง (demo/preview) ───────────────────────────────────────
    def set_outline(self, points):
        if not points:
            return
        self.outline = list(points)
        xs = [p[0] for p in self.outline]
        ys = [p[1] for p in self.outline]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))

    # ── live learning ──────────────────────────────────────────────────────
    def _append(self, x, y, sector):
        if not self.pts:
            self._lb = [x, y, x, y]
        else:
            self._lb[0] = min(self._lb[0], x)
            self._lb[1] = min(self._lb[1], y)
            self._lb[2] = max(self._lb[2], x)
            self._lb[3] = max(self._lb[3], y)
        self.pts.append((x, y, sector))
        self._last_added = (x, y)

    def add(self, x, y, sector, lap):
        if self.outline is not None:
            return
        if self._start_lap is None:
            self._start_lap = lap
        if self._last_added is None:
            self._append(x, y, sector)
        else:
            lx, ly = self._last_added
            diag = math.hypot(self._lb[2] - self._lb[0], self._lb[3] - self._lb[1]) or 1.0
            if math.hypot(x - lx, y - ly) >= diag * 0.003:   # เว้นระยะกันจุดถี่เกิน
                self._append(x, y, sector)
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

        minx, miny, maxx, maxy = bounds
        pad = 26
        bw = (maxx - minx) or 1.0
        bh = (maxy - miny) or 1.0
        scale = min((w - 2 * pad) / bw, (h - 2 * pad) / bh)
        offx = x0 + (w - bw * scale) / 2
        offy = y0 + (h - bh * scale) / 2

        def T(px, py):
            return (offx + (px - minx) * scale,
                    offy + (maxy - py) * scale)          # flip y ให้เหนืออยู่บน

        tint = FLAG_TINT.get((tel.flag or "").upper())
        learning = self.outline is None
        prev = None
        for (px, py, sc) in pts:
            cur = T(px, py)
            if prev is not None:
                col = tint or SECTOR_COLORS.get(sc, (200, 200, 200))
                if learning:
                    col = tuple(int(c * 0.5) for c in col)
                d.line([prev, cur], fill=col, width=4)
            prev = cur

        # start/finish = จุดแรกของเส้น
        sfx, sfy = T(pts[0][0], pts[0][1])
        d.line([(sfx, sfy - 9), (sfx, sfy + 9)], fill=INK, width=3)

        # จุดรถปัจจุบัน
        if tel.has_pos:
            cx, cy = T(tel.x, tel.y)
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(20, 22, 28))   # ขอบเข้ม
            d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], outline=(255, 255, 255), width=3)
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 255))

        if learning:
            d.text((x0 + w - 14, y0 + 10), "● REC",
                   font=font_fn(22), fill=(235, 70, 70), anchor="rm")
