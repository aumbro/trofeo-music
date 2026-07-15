"""
simhub.py — รับ telemetry จาก SimHub (หรือแหล่งอื่น) เข้ามาให้ race.py เรนเดอร์

โมเดล (ทำไมออกแบบแบบนี้):
  - SimHub ไม่รู้จักจอ Trofeo → บริดจ์ผ่าน Python (โครงเดียวกับ vibe.py/send.py)
  - SimHub "Custom Serial device" ยิงข้อความ 1 บรรทัดต่อการอัปเดต ผ่าน COM port
    ต่อ Python ด้วยคู่ virtual COM (com0com): SimHub เขียน COM_A → Python อ่าน COM_B
  - รูปแบบข้อความ = key=value คั่นด้วย ';' ปิดท้าย '\n' เช่น
      spd=182;rpm=8450;mrpm=9200;gear=4;lap=3;laps=12;pos=5;cars=20;...\n
    (สตริงที่ต้องวางในช่อง Custom Serial อยู่ใน docs/SIMHUB.md)
  - parser ทนทานตั้งใจ: ไม่สนลำดับคีย์, คีย์ขาดได้, ค่าพังก็ข้าม, กันบรรทัดครึ่ง ๆ
    (อ่านทีละ readline → ถ้าเจอ line ครึ่งจากตอนเชื่อมต่อจะ resync เองรอบถัดไป)
    → ปรับ field ใน SimHub ได้อิสระโดยไม่ต้องแก้โค้ดฝั่ง Python

ผู้ให้ข้อมูลทุกตัวมี interface เดียวกัน:  .start() -> self ; .latest() -> Telemetry ; .stop()
  - SerialTelemetry(port, baud) : อ่านจริงจาก COM (thread เบื้องหลัง, reconnect เอง)
  - DemoTelemetry()             : จำลอง (รอบเครื่องกวาดขึ้น-ลง + ไล่เกียร์) ทดสอบไม่ต้องมีเกม
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace

# ถือว่าไม่มีค่าใหม่เกินเท่านี้ = ขาดการเชื่อมต่อ (SimHub หยุดส่ง/ออกจากเกม)
STALE_AFTER = 1.5


@dataclass
class Telemetry:
    """สแนปช็อต telemetry หนึ่งเฟรม — ค่า default = "ยังไม่มีข้อมูล" ปลอดภัยเมื่อคีย์ขาด"""
    ts: float = 0.0            # time.time() ตอนได้ค่านี้ (ใช้เช็ค stale)
    connected: bool = False    # เพิ่ง parse สำเร็จและยังไม่ stale
    speed: float = 0.0         # km/h
    rpm: float = 0.0
    max_rpm: float = 0.0       # redline (ใช้คำนวณสัดส่วนไฟ rev strip)
    gear: str = "N"            # "R"/"N"/"1".."8" (ปล่อยเป็น string ตาม SimHub)
    lap: int = 0
    laps: int = 0              # 0 = ไม่รู้จำนวนรอบ (เช่น practice)
    pos: int = 0
    cars: int = 0
    # เวลาต่อรอบ = string สำเร็จรูปจาก SimHub (เช่น "1:31.850") — เลี่ยงยุ่งเรื่อง
    # แปลง TimeSpan→ms ที่ต่างกันในแต่ละเกม; โชว์ตรง ๆ ได้เลย
    cur_lap: str = ""
    last_lap: str = ""
    best_lap: str = ""
    delta: str = ""            # เทียบ best เป็นวินาที เช่น "-0.320" / "+0.495"
    fuel: float = 0.0          # ลิตร
    tc: int = 0                # ระดับ traction control
    abs: int = 0               # ระดับ ABS
    drs: int = 0               # 0/1
    pit: int = 0               # 0/1 อยู่ในพิต/พิตเลน
    flag: str = ""             # GREEN/YELLOW/RED/BLUE/WHITE/BLACK/"" (ไม่มีธง)
    t_fl: float = 0.0          # อุณหภูมิยาง °C (หน้าซ้าย/หน้าขวา/หลังซ้าย/หลังขวา)
    t_fr: float = 0.0
    t_rl: float = 0.0
    t_rr: float = 0.0

    @property
    def rpm_frac(self) -> float:
        """สัดส่วนรอบเทียบ redline 0..1 (0 ถ้าไม่รู้ max_rpm)"""
        if self.max_rpm <= 0:
            return 0.0
        return max(0.0, min(1.0, self.rpm / self.max_rpm))


# ── ตัวแปลงค่าแบบทนพัง (คืน None ถ้าแปลงไม่ได้ → parser จะข้ามคีย์นั้น) ──────
def _to_float(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: str):
    try:
        return int(float(v))       # เผื่อ SimHub ส่ง "5.0"
    except (TypeError, ValueError):
        return None


def _to_str(v: str):
    return v


# key ในสาย ← (attribute ใน Telemetry, ตัวแปลง)
_KEYS = {
    "spd": ("speed", _to_float),   "rpm": ("rpm", _to_float),
    "mrpm": ("max_rpm", _to_float), "gear": ("gear", _to_str),
    "lap": ("lap", _to_int),       "laps": ("laps", _to_int),
    "pos": ("pos", _to_int),       "cars": ("cars", _to_int),
    "cur": ("cur_lap", _to_str),   "last": ("last_lap", _to_str),
    "best": ("best_lap", _to_str), "dlt": ("delta", _to_str),
    "fuel": ("fuel", _to_float),   "tc": ("tc", _to_int),
    "abs": ("abs", _to_int),       "drs": ("drs", _to_int),
    "pit": ("pit", _to_int),       "flag": ("flag", _to_str),
    "tfl": ("t_fl", _to_float),    "tfr": ("t_fr", _to_float),
    "trl": ("t_rl", _to_float),    "trr": ("t_rr", _to_float),
}


def parse_line(line: str, base: Telemetry) -> Telemetry:
    """แตกบรรทัด key=value;... ทับลง base แล้วคืน Telemetry ใหม่

    - คีย์ที่ไม่รู้จัก/ค่าพัง = ข้ามเงียบ (ทนต่อฟอร์แมตที่ผู้ใช้ปรับใน SimHub)
    - ถ้าไม่มีคีย์ที่ parse ได้เลย = คืน base เดิม (ไม่นับว่าเชื่อมต่อ)
    """
    vals = {}
    for tok in line.strip().split(";"):
        k, sep, v = tok.partition("=")
        if not sep:
            continue
        spec = _KEYS.get(k.strip().lower())
        if spec is None:
            continue
        attr, conv = spec
        cv = conv(v.strip())
        if cv is None:
            continue
        vals[attr] = cv
    if not vals:
        return base
    return replace(base, ts=time.time(), connected=True, **vals)


def gear_label(g: str) -> str:
    """normalize เกียร์เป็นตัวอักษรที่โชว์บนจอ: N / R / 1..n"""
    g = (g or "").strip().upper()
    if g in ("", "N", "0"):
        return "N"
    if g in ("R", "-1"):
        return "R"
    return g


class SerialTelemetry:
    """อ่าน telemetry จริงจาก COM port (SimHub Custom Serial → virtual COM → ที่นี่)

    ทำงานใน thread เบื้องหลัง: readline → parse → เก็บ latest (มี lock).
    พอร์ตหลุด/เปิดไม่ได้ → ปิดแล้ววนลองเปิดใหม่เอง (resilient เหมือน USB reconnect)
    """

    def __init__(self, port: str, baud: int = 115200):
        import serial                    # lazy: ให้ preview/demo ไม่ต้องมี pyserial
        self._serial = serial
        self.port = port
        self.baud = baud
        self._lock = threading.Lock()
        self._t = Telemetry()
        self._stop = threading.Event()
        self._th = None

    def start(self) -> "SerialTelemetry":
        self._th = threading.Thread(target=self._run, name="simhub-serial", daemon=True)
        self._th.start()
        return self

    def _run(self):
        ser = None
        while not self._stop.is_set():
            try:
                if ser is None:
                    ser = self._serial.Serial(self.port, self.baud, timeout=1)
                raw = ser.readline()
                if not raw:
                    continue               # timeout: ยังไม่มีบรรทัด — latest() จัดการ stale เอง
                line = raw.decode("utf-8", "ignore")
                with self._lock:
                    self._t = parse_line(line, self._t)
            except Exception:
                # พอร์ตหลุด/ยังไม่ถูกสร้าง/สาย SimHub ปิด → ปิดแล้วลองใหม่ช้า ๆ
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = None
                with self._lock:
                    self._t = replace(self._t, connected=False)
                self._stop.wait(0.5)
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def latest(self) -> Telemetry:
        with self._lock:
            t = self._t
        if t.connected and (time.time() - t.ts) > STALE_AFTER:
            t = replace(t, connected=False)   # ไม่มีค่าใหม่มานาน = ถือว่าขาด
        return t

    def stop(self):
        self._stop.set()


class DemoTelemetry:
    """จำลอง telemetry ไว้ทดสอบ preview/loop โดยไม่ต้องมีเกม/SimHub

    รอบเครื่องกวาดขึ้น-ลงเป็นจังหวะ ~7 วิ + ไล่เกียร์ตามรอบ + delta แกว่ง
    (start เลื่อนเวลาเริ่มไป -3s ให้ preview เฟรมแรกติดจังหวะรอบกลาง ๆ ดูมีชีวิต)
    """

    def __init__(self):
        self._t0 = time.time() - 3.0

    def start(self) -> "DemoTelemetry":
        return self

    def stop(self):
        pass

    def latest(self) -> Telemetry:
        t = time.time() - self._t0
        cycle = math.sin(t * 0.9) * 0.5 + 0.5          # 0..1 ขึ้น-ลงนุ่ม ๆ
        max_rpm = 9000.0
        rpm = 1200 + cycle * (max_rpm - 1200)
        gear = min(8, 1 + int(cycle * 7))
        speed = 40 + cycle * 260
        sec = t % 92                                   # เวลารอบปัจจุบัน (จำลอง)
        cur = f"{int(sec // 60)}:{sec % 60:06.3f}"     # เช่น "0:32.345"
        return Telemetry(
            ts=time.time(), connected=True,
            speed=speed, rpm=rpm, max_rpm=max_rpm, gear=str(gear),
            lap=3, laps=12, pos=5, cars=20,
            cur_lap=cur, last_lap="1:32.345", best_lap="1:31.850",
            delta=f"{math.sin(t * 0.5) * 0.8:+.3f}",
            fuel=34.2, tc=3, abs=2,
            drs=1 if cycle > 0.7 else 0, pit=0, flag="GREEN",
            t_fl=88 + cycle * 12, t_fr=90 + cycle * 12,
            t_rl=85 + cycle * 9, t_rr=86 + cycle * 9,
        )
