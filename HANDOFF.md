# 🤝 HANDOFF — ทำต่อบนอีกเครื่อง (สำหรับ Claude Code / Aum)

> ไฟล์นี้ให้ Claude Code เครื่องใหม่อ่านแล้วทำต่อได้ทันที (memory ของ Claude เป็นของแต่ละเครื่อง
> ไม่ sync ข้ามเครื่อง — บริบทฝากไว้ในไฟล์นี้ + README + คอมเมนต์โค้ด)

## ✅ สถานะปัจจุบัน (ทำอะไรไปแล้ว)
`vibe.py` = Now-Playing + Audio Visualizer บนจอ **Thermalright Trofeo Vision 9.16**
(USB strip 1920×462, โปรโตคอล LY). ทำครบ:
- now-playing จาก SMTC (winsdk) + spectrum จาก WASAPI loopback (soundcard→FFT)
- visualizer 6 สไตล์: `classic`(เงาสะท้อน)/`bars`/`ribbon`/`dots`(+`--invert` gravity-drops, dot แบน)/`wave`/`random`
- แนวตั้ง/แนวนอน/เต็มจอ (`--portrait`/`--full`), ธีมสีตามปก, glow/ประกาย/ClaudePix
- **AGC** (auto-gain), **เนื้อเพลงคาราโอเกะ** (`--lyrics`, LRCLIB), USB reconnect resilience
- ดูรายละเอียด flags/สไตล์ทั้งหมดใน **[README.md](README.md)** · โปรโตคอลจอใน **[PROTOCOL.md](PROTOCOL.md)**

## 🖥️ ตั้งค่าเครื่องใหม่
```bash
git clone https://github.com/aumbro/trofeo-music.git
cd trofeo-music
pip install -r requirements.txt          # pyusb + pillow (มี libusb-1.0.dll แถมมาแล้ว)
pip install soundcard winsdk numpy       # สำหรับ vibe.py (Windows)
python vibe.py --full --viz random       # ทดสอบ (ต่อจอ Trofeo ก่อน)
```
- จอ Trofeo มี MS OS descriptor → Windows โหลด **WinUSB อัตโนมัติ** (ไม่ต้องลง Zadig)
- ⚠️ ถ้า handshake ค้าง (Errno 10060 ทั้งที่ device OK): **drain IN pipe ก่อน** —
  `python -c "import usb.core; from trofeo import *; d=usb.core.find(idVendor=0x0416,idProduct=0x5408,backend=_BACKEND); [d.read(0x81,512,200) for _ in range(3)]"`

## 🏁 งานต่อไป: ต่อ SimHub / telemetry แข่งรถ
เป้า: โชว์แดชแข่งรถบนจอ strip (rev strip ไฟวิ่ง + เกียร์ตัวใหญ่ + speed/tire/lap)
SimHub ไม่รู้จักจอ Trofeo → บริดจ์ผ่าน Python (โครง `trofeo.py`). 2 ทาง:

1. **Screen mirror** (ง่าย): ออกแบบ dashboard ใน SimHub → capture หน้าต่างนั้น → สตรีมขึ้นจอ
   (เหมือน `send.py` แต่เป็นภาพสด). ทำ mirror อะไรก็ได้.
2. **Native telemetry dash** (สวยกว่า, แนะนำ): ดึง telemetry แล้วเรนเดอร์เอง สไตล์ vibe.py
   - แหล่งข้อมูล: **UDP telemetry ของเกมตรง ๆ** (AC/ACC/iRacing/Forza มี built-in — ไม่ต้องมี SimHub เลย)
     หรือ **SimHub Custom Serial output** (SimHub → virtual COM port → Python อ่าน)
   - เรนเดอร์: rev strip (ไฟ RPM วิ่ง + redline flash), เกียร์ตัวใหญ่กลาง, speed, gap/lap, tire temp

**ต้องถาม Aum ก่อนเริ่ม:** (1) เกม/sim อะไร? (2) มี SimHub แล้วไหม หรือดึง UDP ตรง? (3) mirror หรือ native?

## 📦 repo / remotes
- personal (ของ Aum): `https://github.com/aumbro/trofeo-music` → `git push personal main`
- origin (upstream): iTeRy-Jaturawit/thermalright-trofeo-916 (**อย่า push ไป origin**)
- git identity: `Aum <panithi.sira@gmail.com>`
- ⚠️ `.gitignore` บล็อก `*.png` — รูปที่ตั้งใจใส่ใช้ `git add -f` (รูปโชว์ใช้ปกสังเคราะห์เลี่ยงลิขสิทธิ์)

## 💬 หมายเหตุ
- ตอบไทย, คอมเมนต์ไทย · เรียก Aum ว่า "ลูกพี่"
- ทดสอบ visualizer ได้โดยไม่ต้องมีจอ: `python vibe.py --viz X --preview out.png` (+`--full`/`--portrait`/`--lyrics`)
