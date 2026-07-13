"""
frame.py — แปลงภาพ/เฟรมใด ๆ ให้เป็น "payload JPEG" พร้อมส่งเข้าจอ Trofeo

ขั้นตอน (เหมือน pipeline ของ TRCC ฝั่ง widescreen JPEG):
  1. fit ภาพลงบน canvas ขนาดจอ (W x H) — contain / cover / stretch
  2. หมุนภาพตาม wire angle = (encode_base - orientation) % 360  (หมุนตามเข็ม)
       * Trofeo 9.16: encode_base = 180 (จอ mount กลับหัว) -> orientation 0 = หมุน 180°
  3. encode เป็น JPEG -> bytes (นี่คือ payload ที่ส่งเข้า TrofeoLCD.send_jpeg)
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def compose(img: Image.Image, w: int, h: int,
            fit: str = "contain", bg=(0, 0, 0)) -> Image.Image:
    """วางภาพลง canvas W x H ตามโหมด fit"""
    img = img.convert("RGB")
    if fit == "stretch":
        return img.resize((w, h), Image.LANCZOS)

    sw, sh = img.size
    scale = min(w / sw, h / sh) if fit == "contain" else max(w / sw, h / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def encode_frame(img: Image.Image, w: int, h: int,
                 encode_base: int = 180, orientation: int = 0,
                 fit: str = "contain", quality: int = 90,
                 bg=(0, 0, 0)) -> bytes:
    """ภาพ PIL -> payload JPEG พร้อมส่ง"""
    canvas = compose(img, w, h, fit=fit, bg=bg)

    angle = (encode_base - orientation) % 360
    if angle:
        # C# หมุนตามเข็ม; PIL.rotate หมุนทวนเข็ม -> ใส่ค่าลบ
        expand = angle in (90, 270)
        canvas = canvas.rotate(-angle, expand=expand)
        if canvas.size != (w, h):   # กัน 90/270 ทำขนาดสลับ
            canvas = canvas.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def paste_overlay(base: Image.Image, sprite: Image.Image, x: int, y: int,
                  center: bool = False) -> Image.Image:
    """แปะ sprite (ภาพเล็ก) ทับลงบน base ที่ตำแหน่ง (x, y) แล้วคืนภาพใหม่

    - จอทำ partial update เองไม่ได้ -> composite ฝั่ง host แล้วส่งเต็มเฟรม
    - รองรับ alpha: ถ้า sprite เป็น RGBA/มี transparency จะแปะแบบโปร่งใสให้
    - center=True: ให้ (x, y) เป็นจุดกึ่งกลางของ sprite แทนมุมบนซ้าย
    """
    out = base.convert("RGB").copy()
    if center:
        x -= sprite.width // 2
        y -= sprite.height // 2
    if sprite.mode in ("RGBA", "LA") or "transparency" in sprite.info:
        sp = sprite.convert("RGBA")
        out.paste(sp, (x, y), sp)          # ใช้ช่อง alpha เป็น mask
    else:
        out.paste(sprite.convert("RGB"), (x, y))
    return out


def test_pattern(w: int, h: int) -> Image.Image:
    """สร้างภาพทดสอบไว้เช็คทิศ/ขนาดจอ:
    ไล่เฉดสี + มุมมีป้ายกำกับ (บนซ้าย = แดง 'TL') — ถ้าจอกลับหัวจะเห็น TL ไปอยู่ล่างขวา"""
    img = Image.new("RGB", (w, h), (0, 0, 0))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (int(255 * x / w), int(255 * y / h), 60)
    d = ImageDraw.Draw(img)
    m = max(12, h // 8)
    d.rectangle([0, 0, m, m], fill=(255, 0, 0))            # บนซ้าย = แดง
    d.rectangle([w - m, 0, w, m], fill=(0, 255, 0))        # บนขวา = เขียว
    d.rectangle([0, h - m, m, h], fill=(0, 0, 255))        # ล่างซ้าย = น้ำเงิน
    d.rectangle([w - m, h - m, w, h], fill=(255, 255, 0))  # ล่างขวา = เหลือง
    d.text((m + 6, 6), "TL", fill=(255, 255, 255))
    d.text((w // 2 - 20, h // 2 - 6), f"{w}x{h}", fill=(255, 255, 255))
    return img
