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

## 🏁 SimHub / telemetry แข่งรถ — ✅ v1 เขียนแล้ว (รอทดสอบบนจอ)
เลือกทาง **native render + แหล่ง = SimHub Custom Serial** (รองรับ AC/ACC/iRacing).
ไฟล์ใหม่:
- `simhub.py` — ชั้นรับ telemetry: `Telemetry` dataclass, `parse_line` (key=value ทนพัง),
  `SerialTelemetry` (อ่าน COM + reconnect เอง), `DemoTelemetry` (จำลอง ไว้ทดสอบ)
- `race.py` — เรนเดอร์แดช (rev strip + เกียร์ใหญ่ + speed/pos/lap + lap time/delta + ยาง + ธง/DRS/TC/ABS)
  + main loop ส่งขึ้นจอ (สไตล์ `send.py`). มี `--demo` / `--preview PNG` / `--port COMx`
- `docs/SIMHUB.md` — วิธีต่อ com0com + สตริง JavaScript ที่ต้องวางใน SimHub Custom Serial

ทดสอบ (ไล่จากง่าย): `python race.py --demo --preview out.png` → `--demo` (ขึ้นจอ) → `--port COMx` (จริง)

**⚠️ ยังไม่ได้รันจริง** (เครื่องที่เขียนไม่มี Python) — ต้องลอง `--preview` ก่อน แล้วค่อยเทสต์บนจอ
**งานต่อ/ปรับได้:** ปรับ property name ใน SimHub ตามเกม, จูน layout/สี, เพิ่มธง yellow/blue จาก flag,
  ทางเลือกสำรอง = screen-mirror หน้าต่าง SimHub Dash (ยังไม่ทำ)

## 📦 repo / remotes
- personal (ของ Aum): `https://github.com/aumbro/trofeo-music` → `git push personal main`
- origin (upstream): iTeRy-Jaturawit/thermalright-trofeo-916 (**อย่า push ไป origin**)
- git identity: `Aum <panithi.sira@gmail.com>`
- ⚠️ `.gitignore` บล็อก `*.png` — รูปที่ตั้งใจใส่ใช้ `git add -f` (รูปโชว์ใช้ปกสังเคราะห์เลี่ยงลิขสิทธิ์)

## 💬 หมายเหตุ
- ตอบไทย, คอมเมนต์ไทย · เรียก Aum ว่า "ลูกพี่"
- ทดสอบ visualizer ได้โดยไม่ต้องมีจอ: `python vibe.py --viz X --preview out.png` (+`--full`/`--portrait`/`--lyrics`)
