"""
Trofeo 9.16 LCD sender — โปรโตคอล LY (Thermalright Trofeo Vision 9.16)
ส่งภาพ/วิดีโอขึ้นจอกว้าง (ปกติ 1920x462) ผ่าน USB bulk ด้วย PyUSB + libusb/WinUSB

โครงเดียวกับ Kx87.py แต่คนละ transport:
  KX87  = USB HID (hidapi), จอ 51x5, เฟรม RGB888, จอวนเฟรมเอง
  Trofeo= USB bulk (pyusb), จอ ~1920x462, เฟรม "ภาพ JPEG", host ต้อง resend เอง

อ้างอิงโปรโตคอลจากโปรเจกต์ thermalright-trcc-linux (คลาส LyLcd) ซึ่ง
reverse-engineer มาจาก TRCC v2.1.2 (USBLCDNEW.dll / ThreadSendDeviceDataLY)

สรุปโปรโตคอล LY:
  - VID:PID = 0416:5408, endpoint OUT 0x01 / IN 0x81
  - handshake: write 2048 byte -> read 512 byte
      valid เมื่อ resp[0]=3, resp[1]=0xFF, resp[8]=1
      PM = 64 + resp[20]  (ถ้า resp[20] <= 3 ให้ถือเป็น 1) ; SUB = resp[22] + 1
      -> map PM เป็น resolution (FBL 192 = 1920x462, jpeg)
  - เฟรม = "ภาพ JPEG" ของภาพที่ compose แล้ว (หมุน 180 องศาก่อน encode)
      * ไม่มี header ครอบ payload — ตัว JPEG ล้วน ๆ คือ payload
  - หั่น payload เป็น chunk ละ 512 byte (header 16 + data 496)
      header: [0]=01 [1]=FF [2:6]=u32LE total [6:8]=u16LE data_len
              [8]=1(cmd LY) [9:11]=u16LE n_chunks [11:13]=u16LE index
      pad จำนวน chunk ให้เป็นทวีคูณของ 4 แล้วส่งเป็น burst ละ 4096 byte
      จบเฟรมอ่าน ACK 512 byte
  - ⚠️ firmware เด้งกลับ logo ภายใน ~2-3 วิ ถ้าไม่ส่งเฟรมใหม่
      -> ภาพนิ่งต้อง resend ทุก ~1.5 วิ (ดู KEEPALIVE_INTERVAL)

ติดตั้ง backend:
  pip install pyusb pillow
  + libusb-1.0.dll (เช่น `pip install libusb-package` หรือวาง dll ข้าง ๆ)
  + ใช้ Zadig ติดตั้งไดรเวอร์ WinUSB ให้ interface ของจอ (แทนไดรเวอร์ TRCC)
"""
from __future__ import annotations

import struct
import time

import usb.core
import usb.util

# backend libusb (เรียงลำดับความชอบ):
#   1) libusb-1.0.dll ที่วางไว้ข้าง ๆ ไฟล์นี้ (พึ่งตัวเอง ไม่ขึ้นกับเวอร์ชัน package)
#   2) libusb_package (ถ้าลงเวอร์ชันที่มี dll มาด้วย)
#   3) ปล่อยให้ pyusb หา libusb เองตาม PATH/System32
import os

def _load_backend():
    import usb.backend.libusb1 as _l1
    _here = os.path.dirname(os.path.abspath(__file__))
    _local = os.path.join(_here, "libusb-1.0.dll")
    if os.path.exists(_local):
        be = _l1.get_backend(find_library=lambda _n: _local)
        if be is not None:
            return be
    try:
        import libusb_package
        be = libusb_package.get_libusb1_backend()
        if be is not None:
            return be
    except Exception:
        pass
    return None

_BACKEND = _load_backend()

# ── ค่าคงที่ wire (ตรงกับ LyLcd) ─────────────────────────────────────────
VID = 0x0416
PID_LY = 0x5408    # Trofeo Vision 9.16
PID_LY1 = 0x5409   # ญาติใกล้เคียง (คนละ padding เล็กน้อย)

EP_OUT = 0x01
EP_IN = 0x81

HANDSHAKE_HEADER = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])
HANDSHAKE_PAYLOAD = HANDSHAKE_HEADER + bytes(2032)   # รวม 2048 byte
HANDSHAKE_READ = 512

CHUNK_SIZE = 512
CHUNK_HEADER = 16
CHUNK_DATA = 496
USB_WRITE = 4096

HANDSHAKE_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 1000

# ต้องส่งเฟรมใหม่ถี่กว่านี้ ไม่งั้นจอเด้งกลับโลโก้ (firmware revert ~2-3s)
KEEPALIVE_INTERVAL = 1.5


# ── ตาราง PM/FBL -> resolution (ตัดมาเฉพาะที่เกี่ยวกับ LY) ────────────────
# ที่มา: core/protocol.py ของ thermalright-trcc-linux
# คืน (width, height, jpeg, encode_base)
#   encode_base = องศาที่ต้องหมุนภาพก่อน encode ที่ orientation 0 (mount offset)
_FBL_PROFILES = {
    #  fbl : (w,    h,    jpeg, base)
    54:  (360,  360,  True, 0),
    100: (320,  320,  False, 0),
    114: (1600, 720,  True, 180),
    128: (1280, 480,  True, 0),
    192: (1920, 462,  True, 180),   # Trofeo Vision 9.16 (ค่า default ของ FBL 192)
    224: (854,  480,  True, 0),
}
_DEFAULT_PROFILE = (1920, 462, True, 180)

# PM -> FBL (เฉพาะที่ต่างจาก PM=FBL)
_PM_TO_FBL = {
    5: 50, 7: 64, 9: 224, 10: 224, 11: 224, 12: 224, 13: 224, 14: 64,
    15: 224, 16: 224, 17: 224, 32: 100, 50: 50, 63: 114, 64: 114,
    65: 192, 66: 192, 68: 192, 69: 192,
}
# FBL 192 ใช้ร่วมหลาย resolution — PM เป็นตัวแยก
_FBL_192_BY_PM = {68: (1280, 480), 69: (1920, 440)}


def profile_for(pm: int, sub: int = 0):
    """คืน (width, height, jpeg, encode_base) จาก PM byte ของ handshake"""
    fbl = _PM_TO_FBL.get(pm, pm)
    if fbl == 192:
        w, h = _FBL_192_BY_PM.get(pm, (1920, 462))
        return (w, h, True, 180)
    return _FBL_PROFILES.get(fbl, _DEFAULT_PROFILE)


class HandshakeError(Exception):
    pass


class TrofeoLCD:
    """คุมจอ Trofeo 9.16 ผ่านโปรโตคอล LY

    ใช้งาน:
        lcd = TrofeoLCD()
        info = lcd.open()          # เปิด USB + handshake, คืน dict {width,height,...}
        lcd.send_jpeg(jpeg_bytes)  # ส่ง 1 เฟรม (payload = ตัว JPEG ล้วน)
        lcd.close()
    """

    def __init__(self, vid: int = VID, pid: int = PID_LY):
        self.vid = vid
        self.pid = pid
        self.dev = None
        self.ep_out = None
        self.ep_in = None
        self.width = 0
        self.height = 0
        self.jpeg = True
        self.encode_base = 180
        self.pm = 0
        self.sub = 0
        self._cmd = 1 if pid == PID_LY else 2

    # ── เปิด USB + หา endpoint ───────────────────────────────────────────
    def _find_device(self):
        dev = usb.core.find(idVendor=self.vid, idProduct=self.pid, backend=_BACKEND)
        if dev is None:
            raise HandshakeError(
                f"หาไม่เจอ USB {self.vid:04x}:{self.pid:04x} — "
                f"เสียบจออยู่ไหม / ติดตั้งไดรเวอร์ WinUSB (Zadig) แล้วยัง / ปิด TRCC หรือยัง")
        return dev

    def _bind_endpoints(self):
        """หา bulk OUT/IN (พยายามใช้ 0x01/0x81 ก่อน ไม่งั้น auto-detect)"""
        cfg = self.dev.get_active_configuration()
        ep_out = ep_in = None
        for intf in cfg:
            for ep in intf:
                is_bulk = (usb.util.endpoint_type(ep.bmAttributes)
                           == usb.util.ENDPOINT_TYPE_BULK)
                if not is_bulk:
                    continue
                d = usb.util.endpoint_direction(ep.bEndpointAddress)
                if d == usb.util.ENDPOINT_OUT and ep_out is None:
                    ep_out = ep
                elif d == usb.util.ENDPOINT_IN and ep_in is None:
                    ep_in = ep
            if ep_out and ep_in:
                break
        # ถ้าเจอ address มาตรฐานก็ยึดตามนั้น (บาง backend list endpoint ไม่ครบ)
        self.ep_out = ep_out.bEndpointAddress if ep_out else EP_OUT
        self.ep_in = ep_in.bEndpointAddress if ep_in else EP_IN

    def open(self, retries: int = 6, retry_delay: float = 0.5) -> dict:
        # เปิด USB — เผื่อ handle รอบก่อนยังถูกปล่อยไม่ทัน (Access denied ชั่วคราว
        # ตอนรีสตาร์ตถี่ๆ) ให้ retry สักครู่ก่อนยอมแพ้
        last_err = None
        for attempt in range(retries):
            self.dev = self._find_device()
            try:
                try:
                    # Windows/WinUSB: อาจ config ไว้อยู่แล้ว — เงียบ error ไว้
                    self.dev.set_configuration()
                except usb.core.USBError:
                    pass
                self._bind_endpoints()     # จุดที่มัก Access denied ถ้า handle ยังค้าง
                return self.handshake()
            except usb.core.USBError as e:
                last_err = e
                self.close()
                if attempt < retries - 1:
                    time.sleep(retry_delay)
        raise HandshakeError(
            f"เปิด USB ไม่ได้หลังลอง {retries} ครั้ง: {last_err} "
            f"(มีโปรแกรมอื่น/instance เดิมถือจออยู่ไหม เช่น TRCC หรือสคริปต์ที่ยังไม่ปิด)")

    # ── handshake ────────────────────────────────────────────────────────
    def handshake(self) -> dict:
        self.dev.write(self.ep_out, HANDSHAKE_PAYLOAD, HANDSHAKE_TIMEOUT_MS)
        resp = bytes(self.dev.read(self.ep_in, HANDSHAKE_READ, HANDSHAKE_TIMEOUT_MS))
        if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
            raise HandshakeError(
                f"handshake ไม่ผ่าน (len={len(resp)}, "
                f"[0]={resp[0] if resp else 'NA'}, "
                f"[1]={resp[1] if len(resp) > 1 else 'NA'}, "
                f"[8]={resp[8] if len(resp) > 8 else 'NA'})")

        if self.pid == PID_LY:
            raw = resp[20]
            if raw <= 3:
                raw = 1
            self.pm = 64 + raw
            self.sub = resp[22] + 1 if len(resp) > 22 else 0
        else:
            self.pm = 50 + resp[36]
            self.sub = resp[22] if len(resp) > 22 else 0

        self.width, self.height, self.jpeg, self.encode_base = profile_for(self.pm, self.sub)
        return {
            "width": self.width, "height": self.height,
            "jpeg": self.jpeg, "encode_base": self.encode_base,
            "pm": self.pm, "sub": self.sub,
        }

    # ── สร้าง buffer ที่จะยิงลง USB (แยกออกมาเพื่อทดสอบได้โดยไม่ต้องมีจอ) ──
    def build_send_buffer(self, payload: bytes) -> bytes:
        total = len(payload)
        num = total // CHUNK_DATA + 1
        last = total % CHUNK_DATA

        chunks = bytearray(num * CHUNK_SIZE)
        for i in range(num):
            off = i * CHUNK_SIZE
            dlen = last if i == num - 1 else CHUNK_DATA
            chunks[off] = 0x01
            chunks[off + 1] = 0xFF
            struct.pack_into("<I", chunks, off + 2, total)
            struct.pack_into("<H", chunks, off + 6, dlen)
            chunks[off + 8] = self._cmd
            struct.pack_into("<H", chunks, off + 9, num)
            struct.pack_into("<H", chunks, off + 11, i)
            src = i * CHUNK_DATA
            chunks[off + CHUNK_HEADER:off + CHUNK_HEADER + dlen] = payload[src:src + dlen]

        # pad จำนวน chunk เป็นทวีคูณของ 4 (LY) — LY1 ไม่ pad
        pad_mult = 4 if self.pid == PID_LY else 1
        padded = num + (pad_mult - num % pad_mult) % pad_mult
        total_bytes = padded * CHUNK_SIZE
        return bytes(chunks) + bytes(total_bytes - len(chunks))

    # ── ส่ง 1 เฟรม (payload = ตัว JPEG ล้วน) ─────────────────────────────
    def send_jpeg(self, payload: bytes) -> None:
        buf = self.build_send_buffer(payload)
        total_bytes = len(buf)

        pos = 0
        while pos < total_bytes:
            remaining = total_bytes - pos
            if remaining >= USB_WRITE:
                ws = USB_WRITE
            else:
                ws = min(2048, remaining) if self.pid == PID_LY else remaining
            self.dev.write(self.ep_out, buf[pos:pos + ws], WRITE_TIMEOUT_MS)
            pos += USB_WRITE

        # อ่าน ACK (ไม่เช็คเนื้อหา — แค่เคลียร์ให้จอพร้อมรับเฟรมถัดไป)
        try:
            self.dev.read(self.ep_in, HANDSHAKE_READ, READ_TIMEOUT_MS)
        except usb.core.USBError:
            pass   # บางรอบ ACK มาช้า/ไม่มา — ไม่ถือว่าล้ม

    def close(self) -> None:
        if self.dev is not None:
            usb.util.dispose_resources(self.dev)
        self.dev = self.ep_out = self.ep_in = None
