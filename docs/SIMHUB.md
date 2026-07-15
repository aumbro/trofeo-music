# 🏁 ต่อ SimHub → จอ Trofeo 9.16 (`race.py`)

`race.py` ดึง telemetry จาก **SimHub Custom Serial** แล้วเรนเดอร์แดชแข่งรถเอง
(rev strip ไฟวิ่ง + เกียร์ตัวใหญ่ + speed/pos/lap + lap time/delta + อุณหภูมิยาง + ธง/DRS/TC/ABS)

รองรับทุกเกมที่ SimHub อ่านได้ (AC / ACC / iRacing / Forza / ฯลฯ) เพราะ SimHub normalize
property ให้เป็นชุดกลางอยู่แล้ว — ฝั่ง Python ไม่ต้องรู้ว่าเป็นเกมอะไร

```
┌────────┐  telemetry  ┌─────────┐  key=value line  ┌──────────┐  USB LY  ┌──────┐
│  เกม   │───────────▶│ SimHub  │──── COM (com0com)──▶│ race.py  │────────▶│ จอ   │
└────────┘             │ Custom  │                     │ (Python) │          │Trofeo│
                       │ Serial  │                     └──────────┘          └──────┘
                       └─────────┘
```

---

## 1) ทดสอบก่อน (ไม่ต้องมีเกม/SimHub เลย)

```bash
pip install -r requirements.txt          # ได้ pyserial มาด้วย
python race.py --demo --preview out.png   # เรนเดอร์ 1 เฟรมจากข้อมูลจำลอง → เปิด out.png ดู layout
python race.py --demo                     # วน demo ขึ้นจอจริง (ต้องต่อจอ Trofeo)
```

## 2) ต่อคู่ COM ปลอม (com0com)

SimHub เขียนออก COM ได้ แต่ Python ต้องอ่าน "อีกปลาย" ของสายเดียวกัน → ใช้ virtual null-modem:

1. ลง **com0com** (https://com0com.sourceforge.io) → เปิด *Setup* จะได้คู่พอร์ต เช่น `COM7 ⇄ COM8`
   (ถ้าเห็นชื่อ `CNCA0/CNCB0` ให้ rename เป็น `COM7/COM8` ในหน้า Setup)
2. จำไว้: **SimHub ใช้ปลายหนึ่ง, `race.py` ใช้ปลายอีกด้าน** (เช่น SimHub→COM7, race.py→COM8)

## 3) ตั้งค่า SimHub Custom Serial

SimHub → **Additional Plugins → Custom Serial device** → *Add* หนึ่งตัว:

| ช่อง | ค่า |
|---|---|
| Serial port | ปลายของ SimHub (เช่น `COM7`) |
| Baudrate | `115200` |
| Update / Refresh | ~`33 ms` (≈30fps) หรือ `16 ms` (≈60fps) |

ที่ช่อง **"Update messages" / "On data update"** เปิดโหมด **JavaScript** (ปุ่ม `</>`/JS)
แล้ววางสคริปต์นี้ (สร้าง 1 บรรทัด `key=value;...\n`):

```javascript
var mrpm = $prop('CarSettings_MaxRPM');
if (!mrpm || mrpm < 1) { mrpm = $prop('MaxRpm'); }
var dlt = $prop('DeltaToSessionBestLap');       // ถ้าเกมไม่มี ลอง 'DeltaToSessionBest' หรือ 'DeltaToAllTimeBest'
var dltS = (typeof dlt === 'number') ? ((dlt >= 0 ? '+' : '') + dlt.toFixed(3)) : '';
return 'spd='  + Math.round($prop('SpeedKmh'))
     + ';rpm='  + Math.round($prop('Rpms'))
     + ';mrpm=' + Math.round(mrpm)
     + ';gear=' + $prop('Gear')
     + ';lap='  + $prop('CurrentLap')
     + ';laps=' + $prop('TotalLaps')
     + ';pos='  + $prop('Position')
     + ';cars=' + $prop('OpponentsCount')
     + ';cur='  + $prop('CurrentLapTime')        // ส่ง TimeSpan ตรง ๆ — race.py ตัดให้สวยเอง
     + ';last=' + $prop('LastLapTime')
     + ';best=' + $prop('BestLapTime')
     + ';dlt='  + dltS
     + ';fuel=' + $prop('Fuel')
     + ';tc='   + $prop('TCLevel')
     + ';abs='  + $prop('ABSLevel')
     + ';drs='  + ($prop('DRSEnabled') ? 1 : 0)
     + ';pit='  + ($prop('IsInPitLane') ? 1 : 0)
     + ';tfl='  + Math.round($prop('TyreTemperatureFrontLeft'))
     + ';tfr='  + Math.round($prop('TyreTemperatureFrontRight'))
     + ';trl='  + Math.round($prop('TyreTemperatureRearLeft'))
     + ';trr='  + Math.round($prop('TyreTemperatureRearRight'))
     + '\n';
```

> **ชื่อ property อาจต่างกันบ้างในแต่ละเกม** — พิมพ์ในตัว editor แล้ว SimHub จะ autocomplete ให้
> ถ้าคีย์ไหนเกมไม่มี ค่าจะเป็น 0/ว่าง โดยไม่พัง (parser ฝั่ง Python ข้ามคีย์ที่ค่าเสียเงียบ ๆ)

## 4) รัน

```bash
python race.py --port COM8               # ปลายของ race.py (คู่กับ SimHub COM7)
python race.py --port COM8 --fps 30      # ปรับเฟรมเรตส่งขึ้นจอ
```

เข้าเกม → ควรเห็นแดชขึ้นจอ ถ้ายังไม่เข้าเกมจะโชว์ **"WAITING FOR SIMHUB…"** (ยัง keepalive จออยู่)

---

## รูปแบบสาย (สำหรับดีบัก / ต่อยอด)

หนึ่งบรรทัดต่อการอัปเดต ปิดท้าย `\n` — คีย์ไม่ต้องเรียงลำดับ, ขาดได้:

| key | ความหมาย | ชนิด | | key | ความหมาย | ชนิด |
|---|---|---|---|---|---|---|
| `spd` | ความเร็ว km/h | int | | `cur` | เวลารอบปัจจุบัน | str |
| `rpm` | รอบเครื่อง | int | | `last` | เวลารอบล่าสุด | str |
| `mrpm`| redline | int | | `best` | เวลารอบดีสุด | str |
| `gear`| เกียร์ (R/N/1..) | str | | `dlt` | delta วินาที (+ช้า/-เร็ว) | str |
| `lap` / `laps` | รอบ / รอบทั้งหมด | int | | `fuel` | น้ำมัน (ลิตร) | float |
| `pos` / `cars` | อันดับ / จำนวนรถ | int | | `tc` / `abs` | ระดับ TC / ABS | int |
| `drs` / `pit` | 0/1 | int | | `flag` | GREEN/YELLOW/RED/BLUE/WHITE | str |
| `tfl` `tfr` `trl` `trr` | อุณหภูมิยาง °C | int | | | | |

ทดสอบ parser ตรง ๆ ได้ด้วย null-modem: เขียน `spd=180;rpm=8500;gear=4;mrpm=9000\n` เข้าพอร์ต SimHub
แล้วดูจอ — ควรขยับตาม

## แก้ปัญหา

- **จอค้าง "WAITING FOR SIMHUB…"** → เช็คว่า `--port` เป็น *อีกปลาย* ของคู่ com0com (ไม่ใช่พอร์ตเดียวกับ SimHub),
  baud ตรงกัน, และ Custom Serial ใน SimHub ติ๊ก enable + อยู่ในเกมแล้ว
- **เวลาต่อรอบโชว์แปลก ๆ** (เช่น `00:01:31.85`) → ปกติ `race.py` ตัดให้เป็น `1:31.850` เอง;
  ถ้ายังเพี้ยนแปลว่า property คืนค่ารูปแบบอื่น ลองเปลี่ยน property ในสคริปต์ SimHub
- **จอเด้งกลับโลโก้** → เฟรมเรตต่ำไป firmware revert ~2-3s; `race.py` ส่งต่อเนื่องอยู่แล้ว
  อย่าให้ `--fps` ต่ำกว่า ~5
