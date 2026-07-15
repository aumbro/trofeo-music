"""
race.py — Race dashboard บนจอ Thermalright Trofeo Vision 9.16 (1920x462, โปรโตคอล LY)

ดึง telemetry จาก SimHub (Custom Serial → virtual COM) แล้วเรนเดอร์แดชแข่งรถเอง
สไตล์เดียวกับ vibe.py: rev strip ไฟวิ่ง + เกียร์ตัวใหญ่ + speed/pos/lap + lap time/delta
+ อุณหภูมิยาง + ธง/DRS/TC/ABS. รองรับ AC/ACC/iRacing (ผ่าน SimHub ก็ normalize ให้แล้ว)

ตัวอย่าง:
  python race.py --demo --preview out.png    # เรนเดอร์ 1 เฟรมจากข้อมูลจำลอง (ไม่ต้องต่อจอ/เกม)
  python race.py --demo                       # วน demo ขึ้นจอ (เช็ค layout จริงบนจอ)
  python race.py --port COM7                  # อ่าน telemetry จาก SimHub บน COM7 → ขึ้นจอ
  python race.py --port COM7 --baud 115200 --fps 30

ตั้งค่าฝั่ง SimHub (สตริง Custom Serial + com0com): ดู docs/SIMHUB.md
กด Ctrl+C เพื่อออก
"""
from __future__ import annotations

import argparse
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import frame as F
from simhub import DemoTelemetry, SerialTelemetry, Telemetry, gear_label
# หมายเหตุ: TrofeoLCD (pyusb) import แบบ lazy ใน main() หลังสาขา --preview
# เพื่อให้เรนเดอร์/preview ใช้แค่ Pillow โดยไม่ต้องมี pyusb/จอ

W, H = 1920, 462


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a)


# ── ฟอนต์ (เลือกตัวที่มีสระไทย + ละติน เหมือน vibe.py) ────────────────────────
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_font_cache: dict = {}


def _load(candidates, size):
    for name in candidates:
        path = name if os.path.isabs(name) else os.path.join(_FONT_DIR, name)
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _font_cache:
        cands = (["leelawuib.ttf", "tahomabd.ttf", "segoeuib.ttf", "arialbd.ttf"]
                 if bold else ["leelawui.ttf", "tahoma.ttf", "segoeui.ttf", "arial.ttf"])
        _font_cache[key] = _load(cands, size)
    return _font_cache[key]


# ── ธีมสี ─────────────────────────────────────────────────────────────────
C_BG = (8, 9, 12)
C_INK = (238, 242, 250)
C_MUTE = (128, 138, 155)
C_ACCENT = (90, 180, 255)      # ฟ้า (เปลี่ยนเป็นแดงตอน redline)
C_GOOD = (110, 220, 140)       # เร็วกว่า/เย็นพอดี
C_WARN = (240, 120, 120)       # ช้ากว่า/ร้อน

FLAG_COLORS = {
    "GREEN": (60, 200, 90), "YELLOW": (240, 210, 60), "RED": (230, 60, 60),
    "BLUE": (70, 130, 240), "WHITE": (230, 235, 245), "BLACK": (30, 30, 34),
    "ORANGE": (235, 150, 50),
}


# ── ตัวช่วยสี/ฟอร์แมต ───────────────────────────────────────────────────────
def lerp(c1, c2, f):
    f = max(0.0, min(1.0, f))
    return tuple(int(round(a + (b - a) * f)) for a, b in zip(c1, c2))


def dim(c, f):
    return tuple(int(round(v * f)) for v in c)


def rev_color(pos: float):
    """สีไฟ rev strip ตามตำแหน่ง 0..1 (เขียว → เหลือง → แดง)"""
    if pos < 0.60:
        return (60, 210, 90)
    if pos < 0.85:
        return (240, 205, 55)
    return (235, 55, 55)


def temp_color(c: float):
    """สีบล็อกยางตามอุณหภูมิ °C: น้ำเงิน(เย็น) → เขียว(พอดี) → ส้ม → แดง(ร้อน)"""
    stops = [(50, (70, 130, 230)), (80, (70, 200, 140)), (95, (60, 210, 90)),
             (105, (235, 150, 50)), (120, (235, 55, 55))]
    if c <= stops[0][0]:
        return stops[0][1]
    if c >= stops[-1][0]:
        return stops[-1][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if c <= t1:
            return lerp(c0, c1, (c - t0) / (t1 - t0))
    return stops[-1][1]


def clean_laptime(s: str) -> str:
    """ทำเวลาต่อรอบให้สั้นสวย ไม่ว่า SimHub จะส่งมาแบบไหน

    รองรับทั้ง "1:31.850", "01:31.850" และ TimeSpan เต็ม "00:01:31.8500000"
    → คืน "1:31.850" (ตัดชั่วโมงที่เป็นศูนย์ + เศษวินาทีเหลือ 3 หลัก)
    """
    s = (s or "").strip()
    if not s:
        return "--:--.---"
    parts = s.split(":")
    if len(parts) == 3:                       # h:m:s -> ตัด h ถ้าเป็นศูนย์
        h, m, sec = parts
        parts = [m, sec] if h.strip("0 ") == "" else [h, m, sec]
    if "." in parts[-1]:                      # เศษวินาที: เหลือ 3 หลัก
        whole, frac = parts[-1].split(".", 1)
        parts[-1] = whole + "." + (frac + "000")[:3]
    try:                                      # กันเลขศูนย์นำหน้านาที (01 -> 1)
        parts[0] = str(int(parts[0]))
    except ValueError:
        pass
    return ":".join(parts)


# ── ชิ้นส่วนแดช ─────────────────────────────────────────────────────────────
def draw_rev_strip(d, frac: float, phase: float, glow: bool = False):
    """ไฟรอบเครื่องแนวนอน + redline flash

    glow=False: วาดเส้นคม (ไฟติด + ราง dim)
    glow=True : วาดเฉพาะไฟที่ติด (ลงบน glow layer ไว้เบลอเป็นแสงเรือง)
    """
    x0, y0, sw, sh = 40, 20, W - 80, 56
    segs, gap = 40, 6
    seg_w = (sw - gap * (segs - 1)) / segs
    lit = int(round(frac * segs))
    flash = frac >= 0.97 and (int(phase * 12) % 2 == 0)
    for i in range(segs):
        x = x0 + i * (seg_w + gap)
        on = i < lit
        col = (255, 255, 255) if flash else rev_color(i / segs)
        box = [x, y0, x + seg_w, y0 + sh]
        if on:
            d.rectangle(box, fill=col)
        elif not glow:
            d.rectangle(box, fill=dim(col, 0.14))


def draw_tires(d, t: Telemetry, x0: int, y0: int):
    """บล็อกอุณหภูมิยาง 2x2 (หน้า=แถวบน) สีตามความร้อน"""
    sz, gap = 52, 10
    temps = [[t.t_fl, t.t_fr], [t.t_rl, t.t_rr]]
    d.text((x0 + sz + gap // 2, y0 - 20), "TYRE °C", font=font(24), fill=C_MUTE, anchor="mm")
    for r in range(2):
        for c in range(2):
            tx, ty = x0 + c * (sz + gap), y0 + r * (sz + gap)
            temp = temps[r][c]
            d.rounded_rectangle([tx, ty, tx + sz, ty + sz], radius=8, fill=temp_color(temp))
            d.text((tx + sz / 2, ty + sz / 2), f"{int(temp)}",
                   font=font(24), fill=(15, 16, 20), anchor="mm")


def draw_badges(d, t: Telemetry, cx: int, y: int):
    """แถบบอกสถานะ: FUEL · DRS · TC · ABS · PIT (จัดกึ่งกลางที่ cx)"""
    pills = [(f"{t.fuel:.1f}L", C_MUTE, (40, 44, 52))]
    if t.drs:
        pills.append(("DRS", (15, 20, 16), C_GOOD))
    pills.append((f"TC{t.tc}", C_INK, (54, 60, 72)))
    pills.append((f"ABS{t.abs}", C_INK, (54, 60, 72)))
    if t.pit:
        pills.append(("PIT", (20, 16, 8), (235, 180, 60)))

    f_ = font(30)
    pad, gap = 16, 10
    widths = [d.textlength(txt, font=f_) + pad * 2 for txt, _, _ in pills]
    total = sum(widths) + gap * (len(pills) - 1)
    x = cx - total / 2
    for (txt, fg, bg), wpill in zip(pills, widths):
        d.rounded_rectangle([x, y - 24, x + wpill, y + 24], radius=12, fill=bg)
        d.text((x + wpill / 2, y), txt, font=f_, fill=fg, anchor="mm")
        x += wpill + gap


def render(t: Telemetry, phase: float = 0.0) -> Image.Image:
    """เรนเดอร์ 1 เฟรมแดชแข่งรถ (1920x462) จาก Telemetry"""
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)

    # ── ยังไม่เชื่อมต่อ SimHub: โชว์ป้ายรอ (แต่ยังส่งเฟรมเต็มไว้ keepalive) ──
    if not t.connected:
        draw_rev_strip(d, 0.0, phase)
        d.text((W / 2, H / 2 - 10), "WAITING FOR SIMHUB…",
               font=font(72), fill=C_MUTE, anchor="mm")
        d.text((W / 2, H / 2 + 60), "เปิดเกม + SimHub Custom Serial (ดู docs/SIMHUB.md)",
               font=font(34, bold=False), fill=dim(C_MUTE, 0.7), anchor="mm")
        return img

    redline = t.rpm_frac >= 0.97
    accent = C_WARN if redline else C_ACCENT
    gear_col = C_WARN if redline else C_INK
    gtext = gear_label(t.gear)

    # glow layer (ดำ): วาดไฟ rev ที่ติด + เกียร์ แล้วเบลอบวกกลับเข้าภาพ = นีออนบลูม
    glow_img = Image.new("RGB", (W, H), (0, 0, 0))
    glow = ImageDraw.Draw(glow_img)
    draw_rev_strip(glow, t.rpm_frac, phase, glow=True)
    glow.text((W / 2, 210), gtext, font=font(240), fill=gear_col, anchor="mm")
    img = ImageChops.add(img, glow_img.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)

    # เส้นคมทับ glow
    draw_rev_strip(d, t.rpm_frac, phase)
    d.text((W / 2, 210), gtext, font=font(240), fill=gear_col, anchor="mm")

    # SPEED (ซ้ายของเกียร์)
    sx = W / 2 - 300
    d.text((sx, 190), f"{int(t.speed)}", font=font(150), fill=C_INK, anchor="mm")
    d.text((sx, 272), "km/h", font=font(38), fill=C_MUTE, anchor="mm")

    # POSITION + LAP (ขวาของเกียร์)
    rx = W / 2 + 300
    pos_txt = f"P{t.pos}" + (f"/{t.cars}" if t.cars else "")
    lap_txt = f"LAP {t.lap}" + (f"/{t.laps}" if t.laps else "")
    d.text((rx, 165), pos_txt, font=font(92), fill=accent, anchor="mm")
    d.text((rx, 258), lap_txt, font=font(58), fill=C_INK, anchor="mm")

    # แถวล่าง: lap time (ซ้าย) · delta (กลาง) · badges · ยาง (ขวา)
    d.text((44, 372), "LAST", font=font(26), fill=C_MUTE, anchor="lm")
    d.text((44, 414), clean_laptime(t.last_lap), font=font(46), fill=C_INK, anchor="lm")
    d.text((330, 372), "BEST", font=font(26), fill=C_MUTE, anchor="lm")
    d.text((330, 414), clean_laptime(t.best_lap), font=font(46), fill=C_GOOD, anchor="lm")

    delta_col = C_WARN if t.delta.startswith("+") else C_GOOD
    d.text((760, 372), "Δ BEST", font=font(26), fill=C_MUTE, anchor="mm")
    d.text((760, 416), t.delta or "—", font=font(58), fill=delta_col, anchor="mm")

    draw_badges(d, t, cx=1180, y=400)
    draw_tires(d, t, x0=1710, y0=300)

    # ธง: กรอบสีบาง ๆ รอบจอ
    fc = FLAG_COLORS.get((t.flag or "").upper())
    if fc:
        d.rectangle([2, 2, W - 3, H - 3], outline=fc, width=6)

    return img


def main():
    ap = argparse.ArgumentParser(description="Race dashboard บนจอ Trofeo 9.16 (telemetry จาก SimHub)")
    ap.add_argument("--port", help="COM port ที่รับจาก SimHub Custom Serial (เช่น COM7)")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (ต้องตรงกับ SimHub)")
    ap.add_argument("--demo", action="store_true", help="ใช้ข้อมูลจำลอง (ไม่ต้องมีเกม/SimHub)")
    ap.add_argument("--preview", metavar="PNG", help="เรนเดอร์ 1 เฟรมเป็น PNG แล้วออก (ไม่ต้องต่อจอ)")
    ap.add_argument("--fps", type=float, default=30.0, help="เฟรมเรตส่งขึ้นจอ (default 30)")
    ap.add_argument("--orientation", type=int, default=0, choices=[0, 90, 180, 270],
                    help="หมุนเนื้อภาพ (จอ landscape ปกติใช้ 0)")
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="บังคับ encode_base เอง (ถ้าจอกลับหัว/ตะแคง) แทนค่า auto")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x5408, help="USB PID (default 0x5408)")
    args = ap.parse_args()

    # ── โหมด preview: ไม่ต้องต่อจอ/เกม ── เรนเดอร์เฟรมเดียวจาก demo ──
    if args.preview:
        src = DemoTelemetry().start()
        img = render(src.latest(), phase=time.time())
        img.save(args.preview)
        log(f"บันทึก preview → {args.preview} ({W}x{H})")
        return

    if not args.port and not args.demo:
        ap.error("ต้องระบุ --port COMx (อ่านจาก SimHub) หรือ --demo (ข้อมูลจำลอง) หรือ --preview")

    from trofeo import TrofeoLCD          # lazy: โหมดส่งขึ้นจอเท่านั้นที่ต้องใช้ pyusb

    # แหล่ง telemetry
    if args.demo:
        src = DemoTelemetry().start()
        log("โหมด demo: ใช้ข้อมูลจำลอง")
    else:
        src = SerialTelemetry(args.port, args.baud).start()
        log(f"อ่าน telemetry จาก {args.port} @ {args.baud} (SimHub Custom Serial)")

    lcd = TrofeoLCD(pid=args.pid)
    log("กำลังเปิด USB + handshake ...")
    info = lcd.open()
    w, h = info["width"], info["height"]
    base = args.rotate if args.rotate is not None else info["encode_base"]
    log(f"เชื่อมต่อจอ: {w}x{h} PM={info['pm']} SUB={info['sub']} encode_base={base}")
    if (w, h) != (W, H):
        log(f"[!] จอรายงาน {w}x{h} แต่แดชออกแบบไว้ {W}x{H} — จะ fit ให้ (layout อาจเพี้ยน)")

    interval = 1.0 / max(1.0, args.fps)
    log(f"เริ่มส่งแดช {args.fps:.0f} fps (Ctrl+C ออก)")
    try:
        while True:
            t0 = time.time()
            payload = F.encode_frame(render(src.latest(), phase=t0), w, h,
                                     encode_base=base, orientation=args.orientation,
                                     fit="contain", quality=90)
            lcd.send_jpeg(payload)
            dt = interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        log("ปิด ...")
    finally:
        src.stop()
        lcd.close()


if __name__ == "__main__":
    main()
