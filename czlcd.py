"""
czlcd.py — ไดรเวอร์จอ AIO ChiZhu Tech "USBDISPLAY" (โปรโตคอล SPISCRM/CZ)
จอชุดน้ำ Thermalright ขนาด 320x320 (จอกลมบนหัวปั๊ม) ผ่าน USB bulk

คนละชิปกับ Trofeo (LY, VID 0416) — ตัวนี้ VID:PID = 87AD:70DB, ผลิตโดย ChiZhu Tech
handshake ตอบกลับมีสตริง "SPISCRM-V2" (โปรโตคอลฝั่ง TRCC เรียก USBLCDNEW)

สรุปโปรโตคอล CZ (อ้างอิง thermalright-trcc-linux คลาส BulkLcd +
rejeb/thermalright-lcd-control คลาส DisplayDevice87AD70DB320 — ยืนยันตรงกัน):
  - VID:PID = 87AD:70DB, endpoint OUT 0x01 / IN 0x81 (bulk ทั้งคู่)
  - handshake: write 64 byte (magic 12 34 56 78 + byte[56]=1) -> read 1024
      valid เมื่อ resp[24] != 0 ; PM = resp[24], SUB = resp[36]
      PM 32 = 320x320 ส่ง raw RGB565 big-endian (cmd=3)
      PM อื่น = จอ JPEG (cmd=2) เช่น 480x480 GrandVision
  - เฟรม = header 64 byte + payload ต่อท้าย ยิงรวดเดียว (หั่น 16KiB ต่อ write)
      header: [0:4]=12 34 56 78  [4:8]=u32LE cmd(2=jpeg,3=rgb565)
              [8:12]=u32LE width  [12:16]=u32LE height
              [56:60]=u32LE 2     [60:64]=u32LE payload_len
      ถ้าขนาดรวมหาร 512 ลงตัว ต้องปิดท้ายด้วย zero-length packet
      จบเฟรมหน่วง ~15ms (ไม่มี ACK ให้อ่าน)
  - payload RGB565: 320*320*2 = 204,800 byte, row-major, big-endian
  - ⚠️ firmware เด้งกลับ logo ถ้าหยุดส่ง (คอนเฟิร์มกับจอจริงแล้ว — เหมือน LY)
      -> ภาพนิ่งต้อง resend ทุก ~1.5 วิ (ดู KEEPALIVE_INTERVAL)
  - ทิศ mount ตรง (encode_base 0 — คอนเฟิร์มด้วย --test แล้ว TL อยู่บนซ้าย)

ใช้งาน:
    from czlcd import CzLCD, encode_frame
    lcd = CzLCD()
    info = lcd.open()                 # {'width':320,'height':320,'jpeg':False,...}
    payload = encode_frame(img, info["width"], info["height"], jpeg=info["jpeg"])
    lcd.send_frame(payload)

หรือทดสอบตรง ๆ:
    python czlcd.py --test            # ยิงภาพทดสอบขึ้นจอ (เช็คทิศ/ขนาด)
    python czlcd.py --test --preview out.png   # เรนเดอร์ดูเฉย ๆ ไม่แตะจอ
"""
from __future__ import annotations

import io
import struct
import time

import usb.core
import usb.util

from frame import compose, test_pattern
from trofeo import _BACKEND   # ใช้ backend libusb ตัวเดียวกัน (โหลด dll ข้างโฟลเดอร์)

# ── ค่าคงที่ wire ─────────────────────────────────────────────────────────
VID = 0x87AD
PID = 0x70DB

EP_OUT = 0x01
EP_IN = 0x81

MAGIC = bytes.fromhex("12345678")

HANDSHAKE_PAYLOAD = MAGIC + bytes(52) + bytes([1]) + bytes(7)   # 64 byte, byte[56]=1
HANDSHAKE_READ = 1024

HANDSHAKE_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 5000
WRITE_CHUNK = 16 * 1024
FRAME_GAP = 0.015          # หน่วงท้ายเฟรมตาม USBLCDNew.exe (15ms)

KEEPALIVE_INTERVAL = 1.5   # ต้อง resend ภาพนิ่งถี่กว่านี้ ไม่งั้นจอเด้งกลับโลโก้ (เหมือน LY)

# PM -> (width, height, jpeg)  — PM 32 คือจอ 320x320 raw RGB565, ที่เหลือ JPEG
_PM_PROFILES = {
    5:  (320, 240, True),
    7:  (640, 480, True),
    32: (320, 320, False),
    64: (1600, 720, True),
    65: (1920, 462, True),
}
_DEFAULT_PROFILE = (480, 480, True)   # ฐานของสาย bulk (GrandVision ฯลฯ)


class HandshakeError(Exception):
    pass


class CzLCD:
    """คุมจอ ChiZhu 87AD:70DB (จอชุดน้ำ 320x320) — API หน้าตาเดียวกับ TrofeoLCD"""

    def __init__(self, vid: int = VID, pid: int = PID):
        self.vid = vid
        self.pid = pid
        self.dev = None
        self.width = 0
        self.height = 0
        self.jpeg = False
        self.encode_base = 0
        self.pm = 0
        self.sub = 0

    def open(self, retries: int = 6, retry_delay: float = 0.5) -> dict:
        last_err = None
        for attempt in range(retries):
            self.dev = usb.core.find(idVendor=self.vid, idProduct=self.pid,
                                     backend=_BACKEND)
            if self.dev is None:
                raise HandshakeError(
                    f"หาไม่เจอ USB {self.vid:04x}:{self.pid:04x} — "
                    f"เสียบจอชุดน้ำอยู่ไหม / ปิด TRCC หรือยัง")
            try:
                try:
                    self.dev.set_configuration()
                except usb.core.USBError:
                    pass   # Windows/WinUSB มัก config ไว้แล้ว
                return self.handshake()
            except usb.core.USBError as e:
                last_err = e
                self.close()
                if attempt < retries - 1:
                    time.sleep(retry_delay)
        raise HandshakeError(
            f"เปิด USB ไม่ได้หลังลอง {retries} ครั้ง: {last_err} "
            f"(มีโปรแกรมอื่นถือจออยู่ไหม เช่น TRCC)")

    def handshake(self) -> dict:
        self.dev.write(EP_OUT, HANDSHAKE_PAYLOAD, HANDSHAKE_TIMEOUT_MS)
        resp = bytes(self.dev.read(EP_IN, HANDSHAKE_READ, HANDSHAKE_TIMEOUT_MS))
        if len(resp) < 41 or resp[24] == 0:
            raise HandshakeError(
                f"handshake ไม่ผ่าน (len={len(resp)}, "
                f"resp[24]={resp[24] if len(resp) > 24 else 'NA'})")

        self.pm = resp[24]
        self.sub = resp[36] if len(resp) > 36 else 0
        self.width, self.height, self.jpeg = _PM_PROFILES.get(self.pm, _DEFAULT_PROFILE)
        return {
            "width": self.width, "height": self.height,
            "jpeg": self.jpeg, "encode_base": self.encode_base,
            "pm": self.pm, "sub": self.sub,
        }

    # ── ส่ง 1 เฟรม (payload = RGB565 หรือ JPEG ตามรุ่นจอ) ────────────────
    def send_frame(self, payload: bytes) -> None:
        header = bytearray(64)
        header[0:4] = MAGIC
        struct.pack_into("<I", header, 4, 2 if self.jpeg else 3)
        struct.pack_into("<I", header, 8, self.width)
        struct.pack_into("<I", header, 12, self.height)
        struct.pack_into("<I", header, 56, 2)
        struct.pack_into("<I", header, 60, len(payload))

        buf = bytes(header) + payload
        for off in range(0, len(buf), WRITE_CHUNK):
            self.dev.write(EP_OUT, buf[off:off + WRITE_CHUNK], WRITE_TIMEOUT_MS)
        if len(buf) % 512 == 0:
            self.dev.write(EP_OUT, b"", WRITE_TIMEOUT_MS)   # ZLP ปิดเฟรม
        time.sleep(FRAME_GAP)

    # ให้ drop-in ใช้กับโค้ดที่เขียนไว้กับ TrofeoLCD ได้ (send.py ฯลฯ)
    send_jpeg = send_frame

    def close(self) -> None:
        if self.dev is not None:
            usb.util.dispose_resources(self.dev)
        self.dev = None


# ── encoder: ภาพ PIL -> payload ของจอ CZ ─────────────────────────────────
def to_rgb565_be(img) -> bytes:
    """RGB565 big-endian, row-major (ฟอร์แมตของ PM 32)"""
    import numpy as np
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    px = ((arr[:, :, 0].astype(np.uint16) >> 3) << 11) | \
         ((arr[:, :, 1].astype(np.uint16) >> 2) << 5) | \
         (arr[:, :, 2].astype(np.uint16) >> 3)
    return px.flatten().astype(">u2").tobytes()


def encode_frame(img, w: int, h: int, jpeg: bool = False,
                 encode_base: int = 0, orientation: int = 0,
                 fit: str = "contain", quality: int = 90, bg=(0, 0, 0)) -> bytes:
    """ภาพ PIL -> payload พร้อมส่ง (fit + หมุน + encode ตามรุ่นจอ)"""
    canvas = compose(img, w, h, fit=fit, bg=bg)
    angle = (encode_base - orientation) % 360
    if angle:
        canvas = canvas.rotate(-angle, expand=angle in (90, 270))
        if canvas.size != (w, h):
            canvas = canvas.resize((w, h))
    if jpeg:
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    return to_rgb565_be(canvas)


# ── ทดสอบเร็ว ๆ จาก command line ─────────────────────────────────────────
def main():
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="ทดสอบจอชุดน้ำ ChiZhu 320x320")
    ap.add_argument("--test", action="store_true", help="ส่งภาพทดสอบเช็คทิศ/ขนาดจอ")
    ap.add_argument("--preview", metavar="PNG", help="เซฟภาพเป็นไฟล์แทนส่งขึ้นจอ")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                    help="หมุนภาพก่อนส่ง (ถ้าจอ mount เอียง)")
    args = ap.parse_args()

    img = test_pattern(320, 320)
    if args.preview:
        img.save(args.preview)
        print(f"เซฟ {args.preview} แล้ว (ไม่แตะจอ)")
        return

    lcd = CzLCD()
    info = lcd.open()
    print(f"เชื่อมต่อสำเร็จ: {info['width']}x{info['height']} "
          f"jpeg={info['jpeg']} PM={info['pm']} SUB={info['sub']}")
    payload = encode_frame(img, info["width"], info["height"], jpeg=info["jpeg"],
                           encode_base=args.rotate)
    print(f"ส่งภาพทดสอบ ({len(payload)} byte) — resend ทุก {KEEPALIVE_INTERVAL}s, "
          f"Ctrl+C ออก | มุมบนซ้าย = แดง TL ถ้าทิศถูก")
    try:
        while True:
            lcd.send_frame(payload)
            time.sleep(KEEPALIVE_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        lcd.close()


if __name__ == "__main__":
    main()
