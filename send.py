"""
send.py — โปรแกรมส่งภาพ/GIF/วิดีโอขึ้นจอ Thermalright
  default = Trofeo 9.16 (โปรโตคอล LY, 1920x462)
  --dev cz = จอชุดน้ำ ChiZhu 320x320 (โปรโตคอล CZ, ดู czlcd.py)

ตัวอย่าง:
  python send.py --test                 # ส่งภาพทดสอบ (เช็คทิศ/ขนาดจอ)
  python send.py --test --dev cz        # ภาพทดสอบขึ้นจอชุดน้ำ 320x320
  python send.py picture.png            # ภาพนิ่ง (resend อัตโนมัติกัน firmware เด้ง logo)
  python send.py clip.gif --dev cz      # GIF วนเล่นบนจอชุดน้ำ
  python send.py movie.mp4 --loop       # วิดีโอวนเล่น (ต้องมี imageio[ffmpeg])
  python send.py pic.jpg --fit cover    # เต็มจอแบบ crop (ไม่มีขอบดำ)
  python send.py pic.jpg --rotate 0     # ถ้าภาพกลับหัว ลองสั่งมุมหมุนเอง (0/90/180/270)

กด Ctrl+C เพื่อออก
"""
from __future__ import annotations

import argparse
import sys
import time

# ให้ข้อความไทยไม่เพี้ยนบน console ที่ code page ไม่ใช่ UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image, ImageSequence

import czlcd
import frame as F
from trofeo import KEEPALIVE_INTERVAL, TrofeoLCD

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a)


# ── โหลดเฟรมจากแหล่งต่าง ๆ -> generator ของ (PIL.Image, delay_seconds) ──
def gif_frames(path):
    img = Image.open(path)
    for fr in ImageSequence.Iterator(img):
        delay = fr.info.get("duration", 100) / 1000.0
        yield fr.convert("RGB"), max(0.02, delay)


def video_frames(path, fps_override=None):
    try:
        import imageio.v3 as iio
    except ImportError:
        log("[!] เล่นวิดีโอต้องติดตั้ง: pip install imageio imageio-ffmpeg")
        sys.exit(1)
    meta = {}
    try:
        meta = iio.immeta(path, plugin="pyav")
    except Exception:
        pass
    fps = fps_override or meta.get("fps") or 15
    delay = 1.0 / max(1.0, fps)
    for arr in iio.imiter(path, plugin="pyav"):
        yield Image.fromarray(arr).convert("RGB"), delay


def main():
    ap = argparse.ArgumentParser(description="ส่งภาพ/วิดีโอขึ้นจอ Trofeo 9.16")
    ap.add_argument("source", nargs="?", help="ไฟล์ภาพ/GIF/วิดีโอ")
    ap.add_argument("--test", action="store_true", help="ส่งภาพทดสอบเช็คทิศ/ขนาดจอ")
    ap.add_argument("--fit", choices=["contain", "cover", "stretch"],
                    default="contain", help="วิธี fit ลงจอ (default contain)")
    ap.add_argument("--quality", type=int, default=90, help="คุณภาพ JPEG 1-95")
    ap.add_argument("--orientation", type=int, default=0, choices=[0, 90, 180, 270],
                    help="orientation ของผู้ใช้ (หมุนเนื้อภาพ)")
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="บังคับ encode_base เอง (ถ้าจอกลับหัว/ตะแคง) แทนค่า auto")
    ap.add_argument("--fps", type=float, default=None, help="บังคับ fps วิดีโอ")
    ap.add_argument("--loop", action="store_true", help="วนเล่น GIF/วิดีโอไม่รู้จบ")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x5408,
                    help="USB PID (default 0x5408 = LY)")
    ap.add_argument("--dev", choices=["ly", "cz"], default="ly",
                    help="รุ่นจอ: ly = Trofeo 9.16 (default), cz = จอชุดน้ำ 320x320")
    args = ap.parse_args()

    if not args.source and not args.test:
        ap.error("ต้องระบุไฟล์ หรือใช้ --test")

    lcd = czlcd.CzLCD() if args.dev == "cz" else TrofeoLCD(pid=args.pid)
    log("กำลังเปิด USB + handshake ...")
    info = lcd.open()
    w, h = info["width"], info["height"]
    base = args.rotate if args.rotate is not None else info["encode_base"]
    log(f"เชื่อมต่อสำเร็จ: {w}x{h} jpeg={info['jpeg']} PM={info['pm']} "
        f"SUB={info['sub']} encode_base={base}")

    def enc(img):
        if args.dev == "cz":
            # จอ CZ: RGB565 (หรือ JPEG ตามรุ่น) — encoder อยู่ใน czlcd.py
            return czlcd.encode_frame(img, w, h, jpeg=info["jpeg"],
                                      encode_base=base,
                                      orientation=args.orientation,
                                      fit=args.fit, quality=args.quality)
        return F.encode_frame(img, w, h, encode_base=base,
                              orientation=args.orientation, fit=args.fit,
                              quality=args.quality)

    try:
        # ── โหมดภาพทดสอบ / ภาพนิ่ง: ส่งเฟรมเดียวแล้ว keepalive ──
        ext = "" if args.test else "." + args.source.rsplit(".", 1)[-1].lower()
        if args.test or ext in IMAGE_EXTS:
            img = F.test_pattern(w, h) if args.test else Image.open(args.source)
            payload = enc(img)
            log(f"ส่งภาพนิ่ง ({len(payload)} byte) — resend ทุก {KEEPALIVE_INTERVAL}s "
                f"กัน firmware เด้ง logo (Ctrl+C ออก)")
            while True:
                lcd.send_jpeg(payload)
                time.sleep(KEEPALIVE_INTERVAL)

        # ── โหมด GIF: encode ทุกเฟรมล่วงหน้า (สั้น) แล้ววนเล่นให้ลื่น ──
        if ext == ".gif":
            log("กำลังโหลด/encode เฟรม GIF ...")
            frames = [(enc(im), d) for im, d in gif_frames(args.source)]
            log(f"พร้อมเล่น {len(frames)} เฟรม (loop={args.loop}, Ctrl+C ออก)")
            last = frames[-1][0]
            while True:
                for payload, delay in frames:
                    t0 = time.time()
                    lcd.send_jpeg(payload)
                    dt = delay - (time.time() - t0)
                    if dt > 0:
                        time.sleep(dt)
                if not args.loop:
                    break

        # ── โหมดวิดีโอ: สตรีมทีละเฟรม (ไม่เก็บทั้งไฟล์ใน RAM) ──
        elif ext in VIDEO_EXTS:
            log(f"เล่นวิดีโอแบบสตรีม (loop={args.loop}, Ctrl+C ออก)")
            last = None
            while True:
                for im, delay in video_frames(args.source, args.fps):
                    t0 = time.time()
                    last = enc(im)
                    lcd.send_jpeg(last)
                    dt = delay - (time.time() - t0)
                    if dt > 0:
                        time.sleep(dt)
                if not args.loop:
                    break
        else:
            log(f"[!] ไม่รู้จักนามสกุล {ext} — รองรับ: ภาพ {IMAGE_EXTS}, "
                f"gif, วิดีโอ {VIDEO_EXTS}")
            sys.exit(1)

        # เล่นจบ (ไม่ loop) — ค้างเฟรมสุดท้ายไว้ (ยังต้อง keepalive กัน logo)
        if last is not None:
            log("เล่นจบ — ค้างเฟรมสุดท้าย (Ctrl+C ออก)")
            while True:
                lcd.send_jpeg(last)
                time.sleep(KEEPALIVE_INTERVAL)

    except KeyboardInterrupt:
        log("ปิด ...")
    finally:
        lcd.close()


if __name__ == "__main__":
    main()
