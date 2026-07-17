"""
vibe_tray.py — รัน vibe.py เป็นแอปใน System Tray ของ Windows

ไอคอนมุมจอ → คลิกขวาเลือกจอ + โหมด (แนว/visualizer/เนื้อเพลง/มัสคอต) เปลี่ยนได้สด
รองรับ 2 จอ (เลือกจากเมนู "จอ" — auto-detect ตัวที่เสียบอยู่ตอนเปิด):
  - Trofeo 9.16 (1920x462, LY)   - จอชุดน้ำเหลี่ยม 320x320 (CZ, ดู czlcd.py)
รัน render loop ของ vibe.run() ใน thread เบื้องหลัง แล้วเมนูแก้ config สด (vibe อ่านทุกเฟรม)
สลับจอ = สั่งหยุดรอบ run ปัจจุบัน → loop เปิดจอใหม่ให้เอง

รัน:  python vibe_tray.py
แพ็ก exe:  ดู build_exe.bat
"""
from __future__ import annotations

import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pystray
from pystray import Menu, MenuItem
from PIL import Image, ImageDraw

import vibe


def detect_dev() -> str:
    """เลือกจอเริ่มต้นจากที่เสียบอยู่: เจอ Trofeo ก่อน → ly, ไม่งั้นเจอจอชุดน้ำ → cz"""
    try:
        import usb.core
        import czlcd
        import trofeo
        if usb.core.find(idVendor=trofeo.VID, idProduct=trofeo.PID_LY,
                         backend=trofeo._BACKEND) is not None:
            return "ly"
        if usb.core.find(idVendor=czlcd.VID, idProduct=czlcd.PID,
                         backend=trofeo._BACKEND) is not None:
            return "cz"
    except Exception:
        pass
    return "ly"


# ── config (args-like) ที่เมนูแก้ได้สด — ชื่อ attr ต้องตรงกับที่ vibe.run ใช้ ──
class Cfg:
    def __init__(self):
        self.demo = False
        self.no_audio = False
        self.lyrics = False
        self.mascot = False
        self.sparkle = True
        self.glow = True
        self.viz = None            # None=ค่าเริ่มต้น (classic แนวนอน / wave แนวตั้ง)
        self.invert = False
        self.full = True           # เริ่มที่ viz เต็มจอแนวนอน
        self.portrait = False
        self.flip = False
        self.rotate = None
        self.gain = 1.0
        self.agc = True
        self.fps = 30.0
        self.quality = 86
        self.pid = 0x5408
        self.preview = None
        self.art = None
        self.always_lyrics = True  # ดึงเนื้อเพลงเสมอ → toggle โชว์ได้ทันที
        self.dev = detect_dev()    # "ly" = Trofeo · "cz" = จอชุดน้ำ 320x320
        if self.dev == "cz":
            self.full = False      # จอเล็กเริ่มที่ now-playing (เต็มจอเลือกจากเมนูได้)


cfg = Cfg()
stop_evt = threading.Event()
_run_stop = {"evt": None}          # event หยุดรอบ run ปัจจุบัน (ไว้สลับจอ)


def make_icon():
    """ไอคอน tray: แท่ง waveform สีนีออนบนพื้นเข้ม"""
    img = Image.new("RGBA", (64, 64), (18, 14, 34, 255))
    d = ImageDraw.Draw(img)
    heights = [18, 34, 50, 30, 46, 26, 40]
    cols = [(90, 200, 255), (120, 150, 255), (170, 120, 255),
            (220, 110, 230), (255, 110, 170), (255, 150, 120), (140, 220, 160)]
    for i, hh in enumerate(heights):
        x = 6 + i * 8
        d.rounded_rectangle([x, 32 - hh // 2, x + 5, 32 + hh // 2], radius=2, fill=cols[i])
    return img


# ── actions ────────────────────────────────────────────────────────────────
def set_landscape(icon, item):
    cfg.portrait, cfg.full = False, False


def set_full(icon, item):
    cfg.portrait, cfg.full = False, True


def set_portrait(icon, item):
    cfg.portrait, cfg.full = True, False


def _is_ly(item):
    return cfg.dev == "ly"


def _is_cz(item):
    return cfg.dev == "cz"


def make_set_dev(val):
    def f(icon, item):
        if cfg.dev == val:
            return
        cfg.dev = val
        if val == "cz":
            cfg.portrait = False           # จอเหลี่ยมไม่มีแนวตั้ง
        ev = _run_stop["evt"]              # หยุดรอบ run ปัจจุบัน → loop เปิดจอใหม่
        if ev is not None:
            ev.set()
    return f


def make_set_viz(val):
    def f(icon, item):
        cfg.viz = val
    return f


def toggle_lyrics(icon, item):
    cfg.lyrics = not cfg.lyrics


def toggle_mascot(icon, item):
    cfg.mascot = not cfg.mascot


def toggle_invert(icon, item):
    cfg.invert = not cfg.invert


def do_quit(icon, item):
    stop_evt.set()
    ev = _run_stop["evt"]
    if ev is not None:
        ev.set()
    icon.stop()


def viz_item(label, val):
    return MenuItem(label, make_set_viz(val),
                    checked=lambda item, v=val: cfg.viz == v, radio=True)


MENU = Menu(
    MenuItem("จอ", Menu(
        MenuItem("Trofeo 9.16 (1920x462)", make_set_dev("ly"),
                 checked=_is_ly, radio=True),
        MenuItem("จอชุดน้ำ (320x320)", make_set_dev("cz"),
                 checked=_is_cz, radio=True),
    )),
    Menu.SEPARATOR,
    # ── โหมดของ Trofeo (โชว์เฉพาะตอนเลือกจอ ly) ──
    MenuItem("แนวนอน (now-playing)", set_landscape,
             checked=lambda i: not cfg.portrait and not cfg.full, radio=True,
             visible=_is_ly),
    MenuItem("แนวนอน เต็มจอ", set_full,
             checked=lambda i: cfg.full and not cfg.portrait, radio=True,
             visible=_is_ly),
    MenuItem("แนวตั้ง", set_portrait,
             checked=lambda i: cfg.portrait, radio=True, visible=_is_ly),
    # ── โหมดของจอชุดน้ำ (โชว์เฉพาะตอนเลือกจอ cz) ──
    MenuItem("now-playing (ปก+สเปกตรัม)", set_landscape,
             checked=lambda i: not cfg.full, radio=True, visible=_is_cz),
    MenuItem("viz เต็มจอ", set_full,
             checked=lambda i: cfg.full, radio=True, visible=_is_cz),
    Menu.SEPARATOR,
    MenuItem("Visualizer", Menu(
        viz_item("ค่าเริ่มต้น", None),
        viz_item("classic (เงาสะท้อน)", "classic"),
        viz_item("dots (LED matrix)", "dots"),
        viz_item("bars (waveform)", "bars"),
        viz_item("ribbon (คลื่นโปร่งแสง)", "ribbon"),
        viz_item("wave (particle)", "wave"),
        viz_item("random (สุ่มสลับ)", "random"),
    )),
    Menu.SEPARATOR,
    MenuItem("เนื้อเพลง (คาราโอเกะ)", toggle_lyrics, checked=lambda i: cfg.lyrics),
    MenuItem("ClaudePix มัสคอต", toggle_mascot, checked=lambda i: cfg.mascot),
    MenuItem("dots กลับหัว", toggle_invert, checked=lambda i: cfg.invert),
    Menu.SEPARATOR,
    MenuItem("ออก", do_quit),
)


def render_loop():
    """รัน vibe.run() — มี USB reconnect ในตัว; ถ้าหลุดจริงรอแล้วเริ่มใหม่
    ใช้ event ต่อรอบ (_run_stop) แทน stop_evt ตรง ๆ → เมนูสลับจอสั่งจบรอบได้
    โดยไม่ปิดแอป แล้วรอบถัดไปเปิดจอตาม cfg.dev ใหม่"""
    while not stop_evt.is_set():
        run_stop = threading.Event()
        _run_stop["evt"] = run_stop
        try:
            vibe.run(cfg, run_stop)
        except Exception as e:
            vibe.log("tray: vibe.run หยุด:", type(e).__name__, e, "— เริ่มใหม่ใน 3s")
            stop_evt.wait(3.0)
        stop_evt.wait(0.3)        # เว้นให้ USB handle ถูกปล่อยก่อนเปิดจอถัดไป


def main():
    threading.Thread(target=render_loop, daemon=True).start()
    icon = pystray.Icon("vibe", make_icon(), "vibe — Thermalright Visualizer", MENU)
    icon.run()                # บล็อกจนกดออก
    stop_evt.set()
    ev = _run_stop["evt"]
    if ev is not None:
        ev.set()              # ปลด render_loop ที่ค้างรอ (เช่น รอเปิดจอ)


if __name__ == "__main__":
    main()
