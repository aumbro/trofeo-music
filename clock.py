"""
clock.py — นาฬิกา + วันที่ overlay บน wallpaper บนจอ Trofeo 9.16 (อัปเดตทุกวินาที)

ตัวอย่าง:
  python clock.py                     # พื้นหลัง gradient ในตัว + เวลาไทย
  python clock.py wall.jpg            # overlay บน wallpaper ของตัวเอง
  python clock.py wall.jpg --fit cover --lang en
  python clock.py --12h               # แสดงแบบ 12 ชม.
  python clock.py wall.jpg --rotate 0 # ถ้าจอกลับหัว

หลักการ: เก็บ wallpaper ไว้ใน RAM ครั้งเดียว -> ทุกวินาที copy + วาดเวลา/วันที่ทับ
(paste ฝั่ง host) -> encode JPEG -> ส่งเต็มเฟรม (การส่งทุก 1 วิ ทำหน้าที่ keepalive ด้วย)
กด Ctrl+C เพื่อออก
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image, ImageDraw, ImageFont

import frame as F
from trofeo import TrofeoLCD


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a)


# ── ฟอนต์ ────────────────────────────────────────────────────────────────
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def load_font(candidates, size):
    """ลองโหลด TTF ตามลำดับ candidates (ชื่อไฟล์ในโฟลเดอร์ Fonts หรือ path เต็ม)"""
    for name in candidates:
        path = name if os.path.isabs(name) else os.path.join(_FONT_DIR, name)
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)   # PIL แถมมา
    except OSError:
        return ImageFont.load_default()


# ── วันที่ภาษาไทย ─────────────────────────────────────────────────────────
_TH_DAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
_TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
              "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def date_string(now: dt.datetime, lang: str) -> str:
    if lang == "th":
        return f"{_TH_DAYS[now.weekday()]} {now.day} {_TH_MONTHS[now.month - 1]} {now.year + 543}"
    return now.strftime("%A %d %b %Y")


def build_background(source, w, h, fit):
    """คืนภาพพื้นหลังขนาดจอ (wallpaper หรือ gradient ในตัว)"""
    if source:
        return F.compose(Image.open(source), w, h, fit=fit)
    # gradient เข้ม ๆ ในตัว
    bg = Image.new("RGB", (w, h), (10, 12, 24))
    px = bg.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (10 + x * 30 // w, 12 + y * 40 // h, 24 + x * 60 // w)
    return bg


def main():
    ap = argparse.ArgumentParser(description="นาฬิกา+วันที่ overlay บนจอ Trofeo 9.16")
    ap.add_argument("wallpaper", nargs="?", help="ไฟล์ wallpaper (ไม่ใส่ = gradient ในตัว)")
    ap.add_argument("--lang", choices=["th", "en"], default="th", help="ภาษาวันที่")
    ap.add_argument("--fit", choices=["contain", "cover", "stretch"], default="cover")
    ap.add_argument("--quality", type=int, default=88)
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="บังคับมุมหมุน ถ้าจอกลับหัว/ตะแคง")
    ap.add_argument("--12h", dest="h12", action="store_true", help="แสดงแบบ 12 ชั่วโมง")
    ap.add_argument("--color", default="255,255,255", help="สีตัวอักษร R,G,B")
    args = ap.parse_args()

    fg = tuple(int(c) for c in args.color.split(","))

    lcd = TrofeoLCD()
    log("เปิด USB + handshake ...")
    info = lcd.open()
    w, h = info["width"], info["height"]
    base = args.rotate if args.rotate is not None else info["encode_base"]
    log(f"เชื่อมต่อ {w}x{h} encode_base={base}")

    bg = build_background(args.wallpaper, w, h, args.fit)

    # ฟอนต์: เวลาใช้ monospace (ตัวเลขไม่ขยับ), วันที่ใช้ฟอนต์ที่มีสระไทย
    time_font = load_font(["consolab.ttf", "consola.ttf", "segoeuib.ttf", "arialbd.ttf"],
                          int(h * 0.42))
    date_font = load_font(["leelawui.ttf", "tahoma.ttf", "segoeui.ttf"], int(h * 0.13))

    tx, ty = w // 2, int(h * 0.40)
    dx, dy = w // 2, int(h * 0.78)
    t_stroke = max(2, int(h * 0.42) // 30)
    d_stroke = max(1, int(h * 0.13) // 22)
    outline = (0, 0, 0)

    log(f"เริ่มนาฬิกา (lang={args.lang}, {'12h' if args.h12 else '24h'}) — Ctrl+C ออก")
    fmt = "%I:%M:%S" if args.h12 else "%H:%M:%S"
    try:
        while True:
            now = dt.datetime.now()
            frame = bg.copy()
            d = ImageDraw.Draw(frame)
            t_str = now.strftime(fmt).lstrip("0") if args.h12 else now.strftime(fmt)
            d.text((tx, ty), t_str, font=time_font, fill=fg, anchor="mm",
                   stroke_width=t_stroke, stroke_fill=outline)
            d.text((dx, dy), date_string(now, args.lang), font=date_font, fill=fg,
                   anchor="mm", stroke_width=d_stroke, stroke_fill=outline)

            lcd.send_jpeg(F.encode_frame(frame, w, h, encode_base=base,
                                         quality=args.quality))
            # sleep ให้ตรงขอบวินาทีถัดไป (ส่ง ~1 ครั้ง/วิ = keepalive พอดี)
            time.sleep(max(0.05, 1.0 - dt.datetime.now().microsecond / 1e6))
    except KeyboardInterrupt:
        log("ปิด ...")
    finally:
        lcd.close()


if __name__ == "__main__":
    main()
