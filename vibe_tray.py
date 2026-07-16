"""
vibe_tray.py — รัน vibe.py เป็นแอปใน System Tray ของ Windows

ไอคอนมุมจอ → คลิกขวาเลือกโหมด (แนว/visualizer/เนื้อเพลง/มัสคอต) เปลี่ยนได้สด ไม่ต้องพิมพ์ CLI
รัน render loop ของ vibe.run() ใน thread เบื้องหลัง แล้วเมนูแก้ config สด (vibe อ่านทุกเฟรม)

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
        # ── โหมดวิดีโอ (เล่นไฟล์ local ลงจอแทน visualizer) ──
        self.video = False
        self.video_path = None
        self.video_fit = "band"    # band=คลิปกลางเต็มจอ · fit=ย่อทั้งคลิปมีแถบดำ


cfg = Cfg()
stop_evt = threading.Event()


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


def pick_video(icon, item):
    """เปิด dialog เลือกไฟล์วิดีโอ → เปิดโหมดวิดีโอเลย"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="เลือกไฟล์วิดีโอ",
            filetypes=[("วิดีโอ", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
                       ("ทั้งหมด", "*.*")])
        root.destroy()
    except Exception as e:
        vibe.log("tray: เปิด dialog ไม่ได้:", e)
        return
    if path:
        cfg.video_path = path
        cfg.video = True


def toggle_video(icon, item):
    """สลับเปิด/ปิดโหมดวิดีโอ — เปิดครั้งแรกที่ยังไม่มีไฟล์ → เปิด dialog"""
    if cfg.video:
        cfg.video = False
    elif cfg.video_path:
        cfg.video = True
    else:
        pick_video(icon, item)


def make_set_fit(val):
    def f(icon, item):
        cfg.video_fit = val
    return f


def do_quit(icon, item):
    stop_evt.set()
    icon.stop()


def viz_item(label, val):
    return MenuItem(label, make_set_viz(val),
                    checked=lambda item, v=val: cfg.viz == v, radio=True)


MENU = Menu(
    MenuItem("แนวนอน (now-playing)", set_landscape,
             checked=lambda i: not cfg.portrait and not cfg.full, radio=True),
    MenuItem("แนวนอน เต็มจอ", set_full,
             checked=lambda i: cfg.full and not cfg.portrait, radio=True),
    MenuItem("แนวตั้ง", set_portrait,
             checked=lambda i: cfg.portrait, radio=True),
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
    MenuItem("โหมดวิดีโอ", Menu(
        MenuItem("เปิด/ปิดวิดีโอ", toggle_video, checked=lambda i: cfg.video),
        MenuItem("เลือกไฟล์วิดีโอ...", pick_video),
        Menu.SEPARATOR,
        MenuItem("คลิปกลาง (เต็มจอ)", make_set_fit("band"),
                 checked=lambda i: cfg.video_fit == "band", radio=True),
        MenuItem("ย่อทั้งคลิป (แถบดำข้าง)", make_set_fit("fit"),
                 checked=lambda i: cfg.video_fit == "fit", radio=True),
    )),
    Menu.SEPARATOR,
    MenuItem("เนื้อเพลง (คาราโอเกะ)", toggle_lyrics, checked=lambda i: cfg.lyrics),
    MenuItem("ClaudePix มัสคอต", toggle_mascot, checked=lambda i: cfg.mascot),
    MenuItem("dots กลับหัว", toggle_invert, checked=lambda i: cfg.invert),
    Menu.SEPARATOR,
    MenuItem("ออก", do_quit),
)


def render_loop():
    """รัน vibe.run() — มี USB reconnect ในตัว; ถ้าหลุดจริงรอแล้วเริ่มใหม่"""
    while not stop_evt.is_set():
        try:
            vibe.run(cfg, stop_evt)
        except Exception as e:
            vibe.log("tray: vibe.run หยุด:", type(e).__name__, e, "— เริ่มใหม่ใน 3s")
            stop_evt.wait(3.0)


def main():
    threading.Thread(target=render_loop, daemon=True).start()
    icon = pystray.Icon("vibe", make_icon(), "vibe — Trofeo Visualizer", MENU)
    icon.run()                # บล็อกจนกดออก
    stop_evt.set()


if __name__ == "__main__":
    main()
