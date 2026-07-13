"""
claw.py — Clawdmeter dashboard บนจอ Thermalright Trofeo Vision 9.16 (โปรโตคอล LY)

พอร์ต dashboard ของ Clawdmeter (github.com/HermannBjorgvin/Clawdmeter) มาวาดฝั่ง PC
ด้วย PIL แล้วยิงเป็นเฟรม JPEG ขึ้นจอ Trofeo (แทนที่ firmware ESP32 + Arduino_GFX)

ต่างจากของเดิม:
  Clawdmeter (ESP32) : จอวาดเอง, รับ usage จาก daemon ทาง BLE
  ตัวนี้ (Trofeo)     : PC วาดเต็มเฟรมเอง + ดึง usage เอง (ไม่ต้องมี BLE/daemon)

ข้อมูล usage: อ่าน OAuth token จาก ~/.claude/.credentials.json แล้วยิง API จิ๋ว
(Haiku 1 token) ไป api.anthropic.com/v1/messages เพื่ออ่าน rate-limit headers
(anthropic-ratelimit-unified-5h/7d-*) → % + reset time  (เหมือน daemon ของ Clawdmeter)
ถ้าไม่มี token / ออฟไลน์ → fallback เป็นตัวเลข demo อัตโนมัติ

การวางจอ:
  แนวตั้ง (default) : วาด canvas 462x1920 (เสาสูงแบบ Clawdmeter) แล้วหมุน 90° ลงจอ
  แนวนอน (--landscape) : วาด canvas 1920x462 (มัสคอตซ้าย เกจขวา)
  --flip : พลิกด้านถ้าแนวตั้งกลับหัว · --rotate : บังคับมุมหมุน wire เอง

ธงตามภาษาคีย์บอร์ด (ฟีเจอร์ kb ของ Clawdmeter):
  default --flag auto : อ่านภาษา input ของหน้าต่าง active (Windows) แล้วเปลี่ยนธงบนตัวมัสคอต
    TH→ธงไทย · JP→ธงญี่ปุ่น · FR→ธงฝรั่งเศส · อื่น ๆ→สีดินเผา (Claude ปกติ) + badge โชว์โค้ด
    สลับภาษาเมื่อไหร่ มัสคอตจะเต้นฉลอง ~3 วิ · บังคับเองได้ด้วย --flag th|jp|fr|clay

ตัวอย่าง:
  python claw.py                       # แนวตั้ง ดึง usage จริง + ธงตามคีย์บอร์ด (Ctrl+C ออก)
  python claw.py --landscape           # แนวนอน
  python claw.py --demo                 # ตัวเลขจำลอง (ไม่แตะ credential)
  python claw.py --flag th              # บังคับธงไทยตลอด
  python claw.py --preview out.png      # เรนเดอร์ 1 เฟรมเป็น PNG (ไม่ต้องมีจอ)
  python claw.py --landscape --preview out.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image, ImageDraw, ImageFont


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a)


# ── palette: แปลงค่าตรงจาก RGB565 ของ Clawdmeter.ino ให้เป็น RGB888 ─────────
def rgb565(v: int):
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (round(r * 255 / 31), round(g * 255 / 63), round(b * 255 / 31))


C_BG      = rgb565(0x0000)  # ดำ
C_CARD    = rgb565(0x2104)  # charcoal (แถบหัว)
C_TEXT    = rgb565(0xFFFF)  # ขาว
C_MUTE    = rgb565(0x8410)  # เทา
C_TRACK   = rgb565(0x39E7)  # พื้นหลังแถบเกจ
C_GREEN   = rgb565(0x4D6A)  # < 60%
C_ORANGE  = rgb565(0xFCC0)  # 60-84%
C_RED     = rgb565(0xF206)  # >= 85%
C_ACCENT  = rgb565(0xFCC0)  # amber (ชื่อ + ตัวมัสคอต)
C_CLAY    = rgb565(0xCBED)  # ตัว ClaudePix (#CD7F6A)
C_EYE     = rgb565(0x18E3)  # ตา
C_SHADOW  = rgb565(0x1082)  # เงาพื้น
C_TH_RED  = rgb565(0xA0C6)  # แดงธงไทย
C_TH_BLUE = rgb565(0x212F)  # น้ำเงินธงไทย
C_FR_BLUE = (0, 85, 164)    # น้ำเงินธงฝรั่งเศส
C_FR_RED  = (239, 65, 53)   # แดงธงฝรั่งเศส
C_JP_RED  = (188, 0, 45)    # แดงวงกลมธงญี่ปุ่น


# ── sprite ClaudePix 20x20 (จาก Clawdmeter.ino) '#'=ตัว 'X'=ตา '.'=โปร่งใส ──
CLAWD = [
    "....................",
    "....................",
    "....................",
    "....................",
    ".....###########....",
    ".....###########....",
    ".....##X#####X##....",
    "...####X#####X####..",
    "...###############..",
    "...###############..",
    "...#.###########.#..",
    ".....###########....",
    ".....###########....",
    ".....###########....",
    ".....#..#...#..#....",
    ".....#..#...#..#....",
    ".....#..#...#..#....",
    "....................",
    "....................",
    "....................",
]

MODE_IDLE, MODE_WORK, MODE_DANCE = 0, 1, 2


# ── ภาษาคีย์บอร์ด → ธงบนตัวมัสคอต (ฟีเจอร์ kb ของ Clawdmeter) ────────────────
# primary LANGID (Windows) → โค้ด 2 ตัว (โชว์เป็น badge)
LANG_PRIMARY = {
    0x09: "EN", 0x1E: "TH", 0x11: "JP", 0x0C: "FR", 0x07: "DE",
    0x0A: "ES", 0x10: "IT", 0x19: "RU", 0x12: "KO", 0x04: "ZH",
    0x16: "PT", 0x13: "NL", 0x15: "PL", 0x1D: "SV", 0x14: "NO",
}
# โค้ดที่วาดธงบนตัวมัสคอตได้ (นอกนั้น = สีดินเผา Claude ปกติ)
FLAG_DESIGNS = {"TH", "JP", "FR"}


class KbWatcher:
    """อ่านภาษา input ของหน้าต่างที่ active อยู่ (Windows) — คืนโค้ด 2 ตัว เช่น 'TH'/'EN'

    ใช้ GetKeyboardLayout ของ thread หน้าต่าง foreground → low word = LANGID
    บนแพลตฟอร์มอื่น (หรือเรียกล้ม) คืน None = ปิดฟีเจอร์
    """

    def __init__(self):
        self.ok = sys.platform == "win32"
        if not self.ok:
            return
        import ctypes
        self._u32 = ctypes.windll.user32
        self._u32.GetForegroundWindow.restype = ctypes.c_void_p
        self._u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._u32.GetWindowThreadProcessId.restype = ctypes.c_uint
        self._u32.GetKeyboardLayout.argtypes = [ctypes.c_uint]
        self._u32.GetKeyboardLayout.restype = ctypes.c_void_p

    def code(self):
        if not self.ok:
            return None
        try:
            hwnd = self._u32.GetForegroundWindow()
            tid = self._u32.GetWindowThreadProcessId(hwnd, None)
            hkl = self._u32.GetKeyboardLayout(tid) or 0
            primary = (hkl & 0xFFFF) & 0x3FF
            return LANG_PRIMARY.get(primary, "EN")
        except Exception:
            return None


# ── ฟอนต์ ────────────────────────────────────────────────────────────────
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_font_cache: dict = {}


def _load_font(candidates, size):
    for name in candidates:
        path = name if os.path.isabs(name) else os.path.join(_FONT_DIR, name)
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _font_cache:
        cands = (["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "consolab.ttf"]
                 if bold else ["segoeui.ttf", "arial.ttf", "consola.ttf"])
        _font_cache[key] = _load_font(cands, size)
    return _font_cache[key]


# ── ดึงข้อมูล usage (พอร์ตจาก daemon ของ Clawdmeter) ───────────────────────
API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.5",
}
API_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}
REFRESH_REAL = 60.0   # ดึงจริงทุก 60 วิ (ยิง API จิ๋ว)
REFRESH_DEMO = 8.0    # demo ขยับเลขทุก 8 วิ


class AuthError(Exception):
    pass


def _cred_paths():
    paths = []
    if os.environ.get("CLAUDE_CREDENTIALS_PATH"):
        paths.append(os.environ["CLAUDE_CREDENTIALS_PATH"])
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        paths.append(os.path.join(cfg, ".credentials.json"))
    paths.append(os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json"))
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env)
        if base:
            paths.append(os.path.join(base, "Claude", ".credentials.json"))
    return paths


def _extract_token(blob: str):
    """ดึง accessToken ออกจาก credential blob (รองรับ nested / regex / raw token)"""
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        tok = data.get("accessToken")
        if isinstance(tok, str) and tok.strip():
            return tok
        for v in data.values():
            if isinstance(v, dict):
                tok = v.get("accessToken")
                if isinstance(tok, str) and tok.strip():
                    return tok
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-.~+/=]{20,}", blob):
        return blob
    return None


def read_token():
    for p in _cred_paths():
        try:
            with open(p, "r", encoding="utf-8") as f:
                tok = _extract_token(f.read())
            if tok:
                return tok
        except (OSError, UnicodeDecodeError):
            continue
    return None


def poll_usage(token: str):
    """ยิง API จิ๋ว → อ่าน rate-limit headers → คืน dict usage (หรือ None ถ้าล้ม)"""
    req = urllib.request.Request(
        API_URL, data=json.dumps(API_BODY).encode("utf-8"), method="POST")
    for k, v in API_HEADERS.items():
        req.add_header(k, v)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            headers = resp.headers
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(e.code)
        headers = e.headers                 # 429 ก็ยังมี rate-limit headers ติดมา
        if headers is None:
            return None
    except (urllib.error.URLError, OSError):
        return None

    now = time.time()

    def hdr(name, default="0"):
        v = headers.get(name)
        return v if v is not None else default

    def reset_minutes(ts):
        try:
            r = float(ts)
        except (TypeError, ValueError):
            return -1
        mins = (r - now) / 60.0
        return int(round(mins)) if mins > 0 else 0

    def pct(util):
        try:
            return int(round(float(util) * 100))
        except (TypeError, ValueError):
            return -1

    if headers.get("anthropic-ratelimit-unified-5h-utilization"):
        return {
            "s":  pct(hdr("anthropic-ratelimit-unified-5h-utilization")),
            "sr": reset_minutes(hdr("anthropic-ratelimit-unified-5h-reset")),
            "w":  pct(hdr("anthropic-ratelimit-unified-7d-utilization")),
            "wr": reset_minutes(hdr("anthropic-ratelimit-unified-7d-reset")),
            "st": hdr("anthropic-ratelimit-unified-5h-status", "unknown"),
            "acct": "pro",
        }
    return {
        "s":  pct(hdr("anthropic-ratelimit-unified-overage-utilization")),
        "sr": reset_minutes(hdr("anthropic-ratelimit-unified-overage-reset")),
        "w":  -1,
        "wr": -1,
        "st": hdr("anthropic-ratelimit-unified-status", "unknown"),
        "acct": "ent",
    }


def demo_payload(t: float):
    """ตัวเลขจำลองที่ค่อย ๆ ขยับ (ให้เกจ/มัสคอตดูมีชีวิต)"""
    s = max(0, min(100, int(50 + 35 * math.sin(t / 13.0))))
    w = max(0, min(100, int(45 + 25 * math.sin(t / 37.0 + 1.0))))
    st = "allowed" if max(s, w) < 85 else "allowed_warning"
    return {"s": s, "sr": int(300 - (t % 300)),
            "w": w, "wr": int(7 * 24 * 60 - (t % (7 * 24 * 60))),
            "st": st, "acct": "pro"}


# ── สถานะที่แชร์กันระหว่าง poller thread กับ loop วาดภาพ ────────────────────
class State:
    def __init__(self, is_demo: bool):
        self.lock = threading.Lock()
        self.is_demo = is_demo
        self.valid = False
        self.s = self.w = self.sr = self.wr = -1
        self.st = ""
        self.acct = ""
        self.connected = False
        self.last_update = 0.0
        self.work_until = 0.0
        self.dance_until = 0.0
        self.greeted = False

    def apply(self, data: dict, real_ok: bool):
        now = time.time()
        with self.lock:
            changed = (not self.valid) or self.s != data["s"] or self.w != data["w"]
            self.s, self.sr = data["s"], data["sr"]
            self.w, self.wr = data["w"], data["wr"]
            self.st, self.acct = data["st"], data.get("acct", "")
            self.valid = True
            self.connected = real_ok
            self.last_update = now
            if not self.greeted:                 # ทักทายด้วยท่าเต้นตอนได้ค่าแรก
                self.dance_until = now + 4.0
                self.greeted = True
            elif changed:                        # ค่าเปลี่ยน → มัสคอต "ทำงาน" 10 วิ
                self.work_until = now + 10.0

    def snapshot(self):
        with self.lock:
            return {k: getattr(self, k) for k in
                    ("is_demo", "valid", "s", "w", "sr", "wr", "st", "acct",
                     "connected", "last_update", "work_until", "dance_until")}


def poller(state: State, token, stop_evt: threading.Event):
    while not stop_evt.is_set():
        data, real_ok, interval = None, False, REFRESH_DEMO
        if token:
            try:
                data = poll_usage(token)
                real_ok = data is not None
            except AuthError:
                log("token ใช้ไม่ได้ (401/403) — สลับไป demo ชั่วคราว")
                data = None
            interval = REFRESH_REAL if real_ok else REFRESH_DEMO
        if data is None:
            data = demo_payload(time.time())
            real_ok = False
        state.apply(data, real_ok)
        stop_evt.wait(interval)


# ── การวาด ────────────────────────────────────────────────────────────────
def pct_color(p):
    if p < 0:
        return C_MUTE
    if p >= 85:
        return C_RED
    if p >= 60:
        return C_ORANGE
    return C_GREEN


def fmt_reset(minutes):
    if minutes is None or minutes < 0:
        return "resets in --"
    if minutes >= 1440:
        return f"resets in {minutes // 1440}d {(minutes % 1440) // 60}h"
    h, m = minutes // 60, minutes % 60
    return f"resets in {h}h {m:02d}m" if h > 0 else f"resets in {m}m"


def flag_color(flag, sr, c):
    """สีของ 1 เซลล์ตัวมัสคอต (sr=แถวต้นฉบับ 0..19, c=คอลัมน์) ตามธง `flag`
    body อยู่แถว ~4..16 / คอลัมน์ ~3..16 — นอกเหนือธงที่รู้จัก = สีดินเผา"""
    if flag == "TH":                       # ธงไทย: แดง/ขาว/น้ำเงิน(2)/ขาว/แดง (แนวนอน)
        f = (sr - 4) / 13.0
        if f < 1 / 6:
            return C_TH_RED
        if f < 2 / 6:
            return C_TEXT
        if f < 4 / 6:
            return C_TH_BLUE
        if f < 5 / 6:
            return C_TEXT
        return C_TH_RED
    if flag == "FR":                       # ธงฝรั่งเศส: น้ำเงิน/ขาว/แดง (แนวตั้ง)
        f = (c - 3) / 14.0
        if f < 1 / 3:
            return C_FR_BLUE
        if f < 2 / 3:
            return C_TEXT
        return C_FR_RED
    if flag == "JP":                       # ธงญี่ปุ่น: ขาว + วงกลมแดงกลางตัว
        if (sr - 9.0) ** 2 + (c - 10.0) ** 2 <= 3.2 ** 2:
            return C_JP_RED
        return C_TEXT
    return C_CLAY


def _draw_creature(d, cx, oy, cell, dr, blink, flag):
    ox = cx - 10 * cell
    for r in range(20):
        sr = r - dr
        if sr < 0 or sr > 19:
            continue
        row = CLAWD[sr]
        for c in range(20):
            ch = row[c]
            if ch == "#":
                col = flag_color(flag, sr, c)
            elif ch == "X":
                col = flag_color(flag, sr, c) if blink else C_EYE
            else:
                continue
            x0, y0 = ox + c * cell, oy + r * cell
            d.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=col)


def draw_clawd(d, cx, oy, cell, t_ms, energy, mode, flag):
    """มัสคอต ClaudePix เด้ง/กระพริบ 3 อารมณ์ (พอร์ตจาก drawClawd ของ .ino)"""
    energy = max(0.0, min(1.0, energy))
    k = cell / 7.0                     # .ino ออกแบบที่ cell=7 → สเกลค่าพิกเซลตาม
    t = t_ms / 1000.0
    if mode == MODE_DANCE:
        sp = 9.0
        hop = round(2.4 * abs(math.sin(t * sp)))
        sway = round(9.0 * math.sin(t * sp * 0.5) * k)
        blink = False
    elif mode == MODE_WORK:
        sp = 7.0
        hop = round(0.5 + 0.5 * math.sin(t * sp))
        sway = round(1.5 * math.sin(t * sp * 2.0) * k)
        blink = (int(t_ms) % 1400) < 120
    else:
        sp = 3.0 + 6.0 * energy
        amp = 0.7 + 2.3 * energy
        hop = round(amp * abs(math.sin(t * sp)))
        sway = 0
        blink = (int(t_ms) % 2800) < 140

    foot_y = oy + 17 * cell
    rx = max(4, int((40 - hop * 4) * k))
    ry = max(3, int(6 * k))
    d.ellipse([cx + sway - rx, foot_y - ry, cx + sway + rx, foot_y + ry], fill=C_SHADOW)
    _draw_creature(d, cx + sway, oy, cell, -hop, blink, flag)


def draw_gauge(d, x, y, w, label, pct, reset_min, s_label, s_pct, s_reset, bar_h):
    """เกจ 1 อัน: label(ซ้าย) + %(ขวา) + แถบโค้ง + บรรทัด reset"""
    f_label, f_pct, f_reset = font(s_label), font(s_pct), font(s_reset)
    d.text((x, y), label, font=f_label, fill=C_MUTE, anchor="lt")
    num = "--" if pct < 0 else f"{pct}%"
    d.text((x + w, y), num, font=f_pct, fill=pct_color(pct), anchor="rt")

    by = y + int(s_pct * 1.15)
    r = bar_h // 2
    d.rounded_rectangle([x, by, x + w, by + bar_h], radius=r, fill=C_TRACK)
    if pct > 0:
        fw = w if pct >= 100 else int(w * pct / 100)
        if fw < bar_h:
            fw = bar_h
        d.rounded_rectangle([x, by, x + fw, by + bar_h], radius=r, fill=pct_color(pct))

    d.text((x, by + bar_h + int(bar_h * 0.35)), fmt_reset(reset_min),
           font=f_reset, fill=C_MUTE, anchor="lt")


def _updated_text(snap, now):
    if snap["is_demo"]:
        return "DEMO data"
    if not snap["valid"]:
        return "connecting..."
    if not snap["connected"]:
        return "reconnecting..."
    return f"updated {int(now - snap['last_update'])}s ago"


def _energy_mode(snap, now):
    lvl = max(snap["s"], snap["w"]) if snap["valid"] else -1
    energy = 0.14 if lvl < 0 else min(1.0, lvl / 100.0)
    if now < snap["dance_until"]:
        mode = MODE_DANCE
    elif now < snap["work_until"]:
        mode = MODE_WORK
    else:
        mode = MODE_IDLE
    return energy, mode


def draw_kb_badge(d, cx, cy, code, size):
    """pill เล็ก ๆ โชว์โค้ดภาษาคีย์บอร์ดปัจจุบัน (เช่น TH/EN) ที่จุดกึ่งกลาง (cx, cy)"""
    if not code:
        return
    f = font(size)
    tw = d.textlength(code, font=f)
    pad = int(size * 0.5)
    w, h = tw + pad * 2, int(size * 1.5)
    d.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                        radius=h / 2, fill=C_CARD, outline=C_MUTE, width=2)
    d.text((cx, cy), code, font=f, fill=C_TEXT, anchor="mm")


def render_portrait(snap, now_ms, flag, kb_code):
    """canvas แนวตั้ง 462x1920 (เสาสูงแบบ Clawdmeter)"""
    W, H = 462, 1920
    now = time.time()
    energy, mode = _energy_mode(snap, now)
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)

    # หัว
    d.rectangle([0, 0, W, 140], fill=C_CARD)
    d.text((28, 70), "Clawdmeter", font=font(48), fill=C_ACCENT, anchor="lm")
    dot = C_GREEN if snap["connected"] else C_MUTE
    d.ellipse([W - 52, 70 - 17, W - 18, 70 + 17], fill=dot)

    # มัสคอต + badge ภาษาคีย์บอร์ด (ใต้ตัวมัสคอต)
    draw_clawd(d, W // 2, 210, 19, now_ms, energy, mode, flag)
    draw_kb_badge(d, W // 2, 660, kb_code, 34)

    # เกจ
    gx, gw = 44, W - 88
    draw_gauge(d, gx, 800, gw, "SESSION", snap["s"], snap["sr"], 42, 78, 32, 56)
    draw_gauge(d, gx, 1200, gw, "WEEKLY", snap["w"], snap["wr"], 42, 78, 32, 56)

    # ท้าย
    d.line([gx, 1640, W - gx, 1640], fill=C_CARD, width=3)
    if snap["valid"]:
        d.text((gx, 1690), f"Status: {snap['st'] or '-'}", font=font(36),
               fill=C_TEXT, anchor="lt")
    else:
        d.text((gx, 1690), "Waiting for data...", font=font(36),
               fill=C_MUTE, anchor="lt")
    d.text((gx, 1758), _updated_text(snap, now), font=font(30, bold=False),
           fill=C_MUTE, anchor="lt")
    return img


def render_landscape(snap, now_ms, flag, kb_code):
    """canvas แนวนอน 1920x462 (มัสคอตซ้าย เกจขวา)"""
    W, H = 1920, 462
    now = time.time()
    energy, mode = _energy_mode(snap, now)
    img = Image.new("RGB", (W, H), C_BG)
    d = ImageDraw.Draw(img)

    # หัว
    d.rectangle([0, 0, W, 64], fill=C_CARD)
    d.text((30, 32), "Clawdmeter", font=font(38), fill=C_ACCENT, anchor="lm")
    dot = C_GREEN if snap["connected"] else C_MUTE
    d.ellipse([W - 52, 32 - 16, W - 20, 32 + 16], fill=dot)

    # มัสคอต (ซ้าย) + badge ภาษาคีย์บอร์ดใต้ตัว
    draw_clawd(d, 215, 90, 16, now_ms, energy, mode, flag)
    draw_kb_badge(d, 215, 438, kb_code, 28)

    # เกจ (ขวา) — จอสูงแค่ 462px ต้องบีบระยะให้ 2 เกจ + สถานะ ไม่ทับกัน
    gx, gw = 480, W - 480 - 40
    draw_gauge(d, gx, 76, gw, "SESSION", snap["s"], snap["sr"], 32, 52, 24, 36)
    draw_gauge(d, gx, 228, gw, "WEEKLY", snap["w"], snap["wr"], 32, 52, 24, 36)

    # สถานะ/updated (มุมล่าง)
    if snap["valid"]:
        d.text((gx, 398), f"Status: {snap['st'] or '-'}", font=font(26),
               fill=C_TEXT, anchor="lt")
    else:
        d.text((gx, 398), "Waiting for data...", font=font(26),
               fill=C_MUTE, anchor="lt")
    d.text((gx + gw, 400), _updated_text(snap, now), font=font(24, bold=False),
           fill=C_MUTE, anchor="rt")
    return img


# ── ต่อจอ / ส่งเฟรม ────────────────────────────────────────────────────────
def to_wire(canvas, panel_w, panel_h, angle):
    out = canvas.rotate(-angle, expand=True)   # C# หมุนตามเข็ม → ใส่ค่าลบ
    if out.size != (panel_w, panel_h):
        out = out.resize((panel_w, panel_h), Image.LANCZOS)
    return out


def to_jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description="Clawdmeter dashboard บนจอ Trofeo 9.16")
    ap.add_argument("--landscape", action="store_true",
                    help="วาดแนวนอน 1920x462 (default = แนวตั้ง 462x1920)")
    ap.add_argument("--flip", action="store_true", help="พลิกด้าน ถ้าแนวตั้งกลับหัว")
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="บังคับมุมหมุน wire เอง")
    ap.add_argument("--demo", action="store_true", help="ตัวเลขจำลอง (ไม่แตะ credential)")
    ap.add_argument("--flag", choices=["auto", "th", "jp", "fr", "clay"], default="auto",
                    help="ธงบนตัวมัสคอต: auto=ตามภาษาคีย์บอร์ด (default), "
                         "th/jp/fr=บังคับธง, clay=สีดินเผา (Claude ปกติ)")
    ap.add_argument("--quality", type=int, default=88, help="คุณภาพ JPEG 1-95")
    ap.add_argument("--fps", type=float, default=20.0, help="เฟรมต่อวินาที")
    ap.add_argument("--preview", metavar="PNG",
                    help="เรนเดอร์ 1 เฟรมเป็น PNG แล้วออก (ไม่ต้องต่อจอ)")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x5408)
    args = ap.parse_args()

    render = render_landscape if args.landscape else render_portrait
    watcher = KbWatcher()

    def resolve_flag():
        """คืน (flag, kb_code): flag=ธงที่วาดได้ (หรือ None), kb_code=โค้ดโชว์ badge"""
        if args.flag == "auto":
            code = watcher.code()
        elif args.flag == "clay":
            code = None
        else:
            code = args.flag.upper()
        return (code if code in FLAG_DESIGNS else None), code

    # ── โหมด preview: เรนเดอร์เฟรมเดียวออกไฟล์ (ไว้ดูดีไซน์/ทิศจอ) ──
    if args.preview:
        state = State(is_demo=True)
        state.apply(demo_payload(time.time()), real_ok=False)
        state.work_until = time.time() + 10   # โชว์ท่า "ทำงาน" ให้เห็นการเคลื่อนไหว
        flag, kb_code = resolve_flag()
        if args.flag == "auto" and not kb_code:   # กัน preview ว่างบนเครื่องที่ตรวจไม่ได้
            flag, kb_code = "TH", "TH"
        img = render(state.snapshot(), 700.0, flag, kb_code)
        img.save(args.preview)
        log(f"เขียน preview: {args.preview} ({img.width}x{img.height})")
        return

    # ── เปิดจอ + handshake ──
    from trofeo import TrofeoLCD
    lcd = TrofeoLCD(pid=args.pid)
    log("เปิด USB + handshake ...")
    info = lcd.open()
    base = info["encode_base"]
    if args.rotate is not None:
        angle = args.rotate
    elif args.landscape:
        angle = base                                 # 1920x462 ตรงจอ → แค่หมุน mount
    else:
        angle = (base + (90 if not args.flip else 270)) % 360   # 462x1920 → +90
    log(f"เชื่อมต่อ {info['width']}x{info['height']} encode_base={base} "
        f"→ {'แนวนอน' if args.landscape else 'แนวตั้ง'} wire_angle={angle}")

    # ── poller thread ──
    token = None if args.demo else read_token()
    if args.demo:
        log("โหมด demo — ใช้ตัวเลขจำลอง")
    elif token:
        log("พบ credential — จะดึง usage จริงทุก 60s")
    else:
        log("ไม่พบ credential (~/.claude/.credentials.json) — ใช้ demo")
    state = State(is_demo=(token is None))
    stop_evt = threading.Event()
    th = threading.Thread(target=poller, args=(state, token, stop_evt), daemon=True)
    th.start()

    if args.flag == "auto":
        log("ธงมัสคอต = auto (ตามภาษาคีย์บอร์ด) — TH→ธงไทย JP→ญี่ปุ่น FR→ฝรั่งเศส อื่น→ดินเผา"
            if watcher.ok else "ธง auto ใช้ได้เฉพาะ Windows — ใช้สีดินเผาแทน")

    t0 = time.time()
    period = 1.0 / max(1.0, args.fps)
    last_kb = "\x00"                       # sentinel: ยังไม่เคยอ่าน
    log(f"เริ่มแสดงผล {args.fps:.0f}fps — Ctrl+C ออก")
    try:
        while True:
            loop_t = time.time()
            now_ms = (loop_t - t0) * 1000.0
            flag, kb_code = resolve_flag()
            if args.flag == "auto" and kb_code != last_kb:
                if last_kb != "\x00":      # สลับภาษา → มัสคอตเต้นฉลอง ~3 วิ
                    with state.lock:
                        state.dance_until = time.time() + 3.0
                last_kb = kb_code
            canvas = render(state.snapshot(), now_ms, flag, kb_code)
            wire = to_wire(canvas, info["width"], info["height"], angle)
            lcd.send_jpeg(to_jpeg(wire, args.quality))
            dt = period - (time.time() - loop_t)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        log("ปิด ...")
    finally:
        stop_evt.set()
        lcd.close()


if __name__ == "__main__":
    main()
