# thermalright-trofeo-916

ส่ง **ภาพนิ่ง / GIF / วิดีโอ** ขึ้นจอ **Thermalright Trofeo Vision 9.16** (จอ USB ~1920×462)
ด้วย Python — โครงสร้างแนวเดียวกับโปรเจกต์ `KeyboardDisplay/Kx87.py` (SARU KX87) แต่เปลี่ยน
transport จาก USB **HID** เป็น USB **bulk** (โปรโตคอล **LY**) และจอเป็นภาพ **JPEG** เต็มจอ

โปรโตคอล LY reverse-engineer มาจาก TRCC v2.1.2 (อ้างอิงโปรเจกต์
[thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux) คลาส `LyLcd`)

---

## KX87 ต่างจาก Trofeo ยังไง

| | KX87 (SARU keyboard) | Trofeo Vision 9.16 |
|---|---|---|
| ต่อผ่าน | USB HID (`hidapi`) | USB bulk (`pyusb` + libusb/WinUSB) |
| VID:PID | `0C45:8009` | `0416:5408` |
| จอ | 51×5 pixel, RGB888 | **1920×462**, ภาพ **JPEG** |
| endpoint | usage_page 0xFF67/0xFF68 | OUT `0x09` / IN `0x81` (auto-detect) |
| หั่นข้อมูล | packet 4104, header `AA 41`, ACK `55 41` | chunk 512 (header 16+data 496), burst 4096, ACK 512 |
| แอนิเมชัน | ส่งทุกเฟรมทีเดียว **จอวนเล่นเอง** | **host ต้องยิงทีละเฟรม** ตาม FPS |
| ภาพนิ่ง | ส่งครั้งเดียวค้างไว้ | **ต้อง resend ทุก ~1.5s** ไม่งั้น firmware เด้งกลับโลโก้ |

---

## ติดตั้ง

```
pip install -r requirements.txt
```

จำเป็น: `pyusb`, `pillow` · เล่นวิดีโอ (ไม่ใช่ GIF) ต้องมี `imageio` + `imageio-ffmpeg` + `av` ด้วย

**libusb backend:** โปรเจกต์นี้มากับ `libusb-1.0.dll` (64-bit) วางไว้ในโฟลเดอร์แล้ว
`trofeo.py` จะโหลดไฟล์นี้ก่อนอัตโนมัติ จึงไม่ต้องตั้งค่าอะไรเพิ่ม

### ไดรเวอร์จอ (WinUSB)

จอเครื่องนี้ผูกกับ **WinUSB อยู่แล้ว** (ตรวจด้วย `Get-PnpDevice` เห็น Service = `WINUSB`)
จึงใช้ได้เลย **ไม่ต้องรัน Zadig**

ถ้าเครื่องอื่นจอยังเป็นไดรเวอร์ผู้ผลิต ให้ใช้ [Zadig](https://zadig.akeo.ie/) ติดตั้ง
ไดรเวอร์ **WinUSB** ให้อุปกรณ์ `USB\VID_0416&PID_5408` (จะแทนที่ไดรเวอร์ที่ TRCC ใช้)

> ⚠️ **ปิดโปรแกรม TRCC ก่อนรัน** — อุปกรณ์ให้โปรแกรมเดียวถือ interface ได้ทีละตัว

---

## ใช้งาน

```bash
python send.py --test                # ภาพทดสอบ (เช็คทิศ/ขนาดจอ)
python send.py picture.png           # ภาพนิ่ง (resend อัตโนมัติ)
python send.py picture.jpg --fit cover   # เต็มจอแบบครอป (ไม่มีขอบดำ)
python send.py clip.gif              # GIF (loop เฟรมตาม duration ในไฟล์)
python send.py clip.gif --loop       # GIF วนไม่รู้จบ
python send.py movie.mp4 --loop      # วิดีโอวนเล่น (สตรีมทีละเฟรม)
```

กด **Ctrl+C** เพื่อออก (พอหยุดส่ง จอจะเด้งกลับโลโก้เองใน ~2-3 วิ)

### นาฬิกา + วันที่ overlay (clock.py)

```bash
python clock.py                  # พื้นหลัง gradient ในตัว + เวลา/วันที่ไทย (พ.ศ.)
python clock.py wall.jpg         # overlay บน wallpaper ของตัวเอง
python clock.py wall.jpg --lang en --fit cover
python clock.py --12h --color 0,255,180
```

อัปเดตทุกวินาที (การส่งทุก 1 วิ เป็น keepalive ในตัว) · เวลาใช้ฟอนต์ Consolas (ตัวเลขไม่ขยับ)
วันที่ไทยใช้ Leelawadee UI · แสดงพุทธศักราชเมื่อ `--lang th`

### ตัวเลือก

| flag | ความหมาย |
|---|---|
| `--fit contain\|cover\|stretch` | วิธี fit ภาพลงจอกว้าง (default `contain` = ใส่ขอบดำ) |
| `--quality 1..95` | คุณภาพ JPEG (default 90) |
| `--rotate 0\|90\|180\|270` | บังคับมุมหมุนเอง ถ้าภาพ **กลับหัว/ตะแคง** (แทนค่า auto 180) |
| `--orientation 0\|90\|180\|270` | หมุนเนื้อภาพตาม orientation ผู้ใช้ |
| `--fps N` | บังคับ FPS วิดีโอ |
| `--loop` | วน GIF/วิดีโอไม่รู้จบ |

> ถ้า `--test` แล้วเห็นช่องแดง **"TL"** อยู่ **มุมบนซ้าย** = ทิศถูก (encode_base 180 ตรงกับจอนี้)
> ถ้า TL ไปโผล่มุม **ล่างขวา** = จอ mount คนละทิศ ลอง `--rotate 0`

---

## ไฟล์ในโปรเจกต์

- **`trofeo.py`** — คลาส `TrofeoLCD`: เปิด USB, handshake (อ่าน resolution จริงจากจอ),
  หั่น chunk + ยิงเฟรม (โปรโตคอล LY ล้วน ๆ) — เอาไป import ใช้ต่อได้
- **`frame.py`** — แปลงภาพใด ๆ → payload JPEG (fit + หมุน + encode) + `paste_overlay` (แปะภาพเล็กทับ) + ภาพทดสอบ
- **`send.py`** — โปรแกรม CLI (ภาพนิ่ง/GIF/วิดีโอ + keepalive)
- **`clock.py`** — นาฬิกา + วันที่ overlay บน wallpaper (อัปเดตทุกวินาที)
- **`libusb-1.0.dll`** — backend libusb (64-bit) สำหรับ pyusb

## เขียนโปรแกรมของตัวเองต่อ

```python
from trofeo import TrofeoLCD
import frame as F
from PIL import Image

lcd = TrofeoLCD()
info = lcd.open()                     # {'width':1920,'height':462,'encode_base':180,...}
payload = F.encode_frame(Image.open("x.png"), info["width"], info["height"],
                         encode_base=info["encode_base"])
lcd.send_jpeg(payload)                # ต้องเรียกซ้ำทุก ~1.5s กัน firmware เด้ง logo
```

---

## License & credits

- โค้ดในโปรเจกต์นี้: **MIT** ([LICENSE](LICENSE))
- โปรโตคอล LY อ้างอิง/ต่อยอดจาก
  [thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux) (คลาส `LyLcd`)
- `libusb-1.0.dll` ที่แถมมา เป็นของโปรเจกต์ [libusb](https://libusb.info) — สัญญาอนุญาต
  **LGPL-2.1** (แจกจ่ายซ้ำได้ ดู source ที่ libusb.info)

> **ไม่มีส่วนเกี่ยวข้องกับ Thermalright** — "Thermalright" / "Trofeo Vision" เป็นเครื่องหมายการค้า
> ของเจ้าของแบรนด์ โปรเจกต์นี้เป็นงานอิสระที่ reverse-engineer โปรโตคอลเพื่อการทำงานร่วม
> (interoperability) เท่านั้น ใช้ความเสี่ยงเอง
