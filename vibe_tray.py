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
import clocks


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
        self.quality = 95         # จอ pace ~24fps คงที่ทุกขนาด → quality สูงได้ฟรี
        self.pid = 0x5408
        self.preview = None
        self.art = None
        self.always_lyrics = True  # ดึงเนื้อเพลงเสมอ → toggle โชว์ได้ทันที
        # ── โหมดวิดีโอ (เล่นไฟล์ local ลงจอแทน visualizer) ──
        self.video = False
        self.video_path = None
        self.video_fit = "band"    # band=คลิปกลางเต็มจอ · fit=ย่อทั้งคลิปมีแถบดำ
        self.video_pan = True      # band: แพนช้า ๆ ในส่วนที่ล้นจอ
        # ── ช่องปกอัลบั้ม: art=ปกจริง | clock | video | image (รูป/GIF) ──
        self.art_source = "art"
        self.art_clock_style = "lumo"   # สไตล์นาฬิกาของปก (แยกจากนาฬิกาเต็มจอ); "cycle"=วน
        self.art_video_path = None
        self.art_image_path = None
        # ── โหมดนาฬิกา (แนวนอนเสมอ) ──
        self.clock = False
        self.clock_style = "nixie"
        self.clock_cycle = False


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
    cfg.video = cfg.clock = False          # เลย์เอาต์คือของโหมดเพลง → สลับกลับให้เลย


def set_full(icon, item):
    cfg.portrait, cfg.full = False, True
    cfg.video = cfg.clock = False


def set_portrait(icon, item):
    cfg.portrait, cfg.full = True, False
    cfg.video = cfg.clock = False


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


# ── โหมดจอ (radio เดียว สลับได้ทันที ไม่ต้องปิดของเก่า) ──────────────────────
def set_mode_music(icon, item):
    cfg.video = False
    cfg.clock = False


def set_mode_clock(icon, item):
    cfg.clock = True
    cfg.video = False


def set_mode_video(icon, item):
    if not cfg.video_path:
        path = _pick_file("เลือกไฟล์วิดีโอ",
                          [("วิดีโอ", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("ทั้งหมด", "*.*")])
        if not path:
            return
        cfg.video_path = path
    cfg.video = True
    cfg.clock = False


def pick_video(icon, item):
    """เลือกไฟล์วิดีโอใหม่ → สลับไปโหมดวิดีโอเลย"""
    path = _pick_file("เลือกไฟล์วิดีโอ",
                      [("วิดีโอ", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("ทั้งหมด", "*.*")])
    if path:
        cfg.video_path = path
        cfg.video = True
        cfg.clock = False


def make_set_fit(val):
    def f(icon, item):
        cfg.video_fit = val
    return f


def toggle_video_pan(icon, item):
    cfg.video_pan = not cfg.video_pan


# ── ช่องปกอัลบั้ม ────────────────────────────────────────────────────────────
def _pick_file(title, types):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title=title, filetypes=types)
        root.destroy()
        return path or None
    except Exception as e:
        vibe.log("tray: เปิด dialog ไม่ได้:", e)
        return None


def make_set_art(val):
    def f(icon, item):
        cfg.art_source = val
    return f


def make_set_art_clock(val):
    """เลือกสไตล์นาฬิกาปกตรง ๆ — เปิด art_source=clock ให้เลย"""
    def f(icon, item):
        cfg.art_clock_style = val
        cfg.art_source = "clock"
    return f


def art_clock_item(key, label=None):
    return MenuItem(label or clocks.STYLE_LABELS.get(key, key), make_set_art_clock(key),
                    checked=lambda i, k=key: cfg.art_source == "clock" and cfg.art_clock_style == k,
                    radio=True)


def pick_art_video(icon, item):
    path = _pick_file("เลือกวิดีโอแทนปกอัลบั้ม",
                      [("วิดีโอ", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("ทั้งหมด", "*.*")])
    if path:
        cfg.art_video_path = path
        cfg.art_source = "video"


def pick_art_image(icon, item):
    path = _pick_file("เลือกรูป/GIF แทนปกอัลบั้ม",
                      [("รูป/GIF", "*.gif *.png *.jpg *.jpeg *.webp *.bmp"), ("ทั้งหมด", "*.*")])
    if path:
        cfg.art_image_path = path
        cfg.art_source = "image"


def toggle_clock_cycle(icon, item):
    cfg.clock_cycle = not cfg.clock_cycle
    if cfg.clock_cycle:            # เปิดหมุนเวียน = สลับไปโหมดนาฬิกาเลย
        cfg.clock = True
        cfg.video = False


def make_set_clock(val):
    """เลือกสไตล์นาฬิกาเต็มจอ = สลับไปโหมดนาฬิกาเลย"""
    def f(icon, item):
        cfg.clock_style = val
        cfg.clock = True
        cfg.video = False
        cfg.clock_cycle = False
    return f


def clock_item(key):
    return MenuItem(clocks.STYLE_LABELS.get(key, key), make_set_clock(key),
                    checked=lambda i, k=key: cfg.clock_style == k and not cfg.clock_cycle,
                    radio=True)


_QUIT = {"q": False}       # แยก "ผู้ใช้สั่งออก" ออกจาก "run() ตายเอง" (ต้องรีสตาร์ต)


def do_quit(icon, item):
    _QUIT["q"] = True
    stop_evt.set()
    icon.stop()


def viz_item(label, val):
    return MenuItem(label, make_set_viz(val),
                    checked=lambda item, v=val: cfg.viz == v, radio=True)


MENU = Menu(
    # ── โหมดจอ: radio เดียว คลิกสลับได้ทันที ──
    MenuItem("จอ: เพลง (now-playing)", set_mode_music,
             checked=lambda i: not cfg.clock and not cfg.video, radio=True),
    MenuItem("จอ: นาฬิกาเต็มจอ", set_mode_clock,
             checked=lambda i: cfg.clock, radio=True),
    MenuItem("จอ: วิดีโอเต็มจอ", set_mode_video,
             checked=lambda i: cfg.video, radio=True),
    Menu.SEPARATOR,
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
    MenuItem("สไตล์นาฬิกาเต็มจอ", Menu(
        *[clock_item(k) for k in clocks.STYLES],
        Menu.SEPARATOR,
        MenuItem("หมุนเวียนทุกสไตล์ (45วิ)", toggle_clock_cycle,
                 checked=lambda i: cfg.clock_cycle),
    )),
    MenuItem("ตั้งค่าวิดีโอ", Menu(
        MenuItem("เลือกไฟล์วิดีโอ...", pick_video),
        Menu.SEPARATOR,
        MenuItem("คลิปกลาง (เต็มจอ)", make_set_fit("band"),
                 checked=lambda i: cfg.video_fit == "band", radio=True),
        MenuItem("ย่อทั้งคลิป (แถบดำข้าง)", make_set_fit("fit"),
                 checked=lambda i: cfg.video_fit == "fit", radio=True),
        Menu.SEPARATOR,
        MenuItem("แพนช้า ๆ (ส่วนที่ล้นจอ)", toggle_video_pan,
                 checked=lambda i: cfg.video_pan),
    )),
    Menu.SEPARATOR,
    MenuItem("ปกอัลบั้ม", Menu(
        MenuItem("ปกจริง (จากเพลง)", make_set_art("art"),
                 checked=lambda i: cfg.art_source == "art", radio=True),
        MenuItem("นาฬิกา (เลือกสไตล์)", Menu(
            *[art_clock_item(k) for k in clocks.STYLES],
            Menu.SEPARATOR,
            art_clock_item("cycle", "หมุนเวียนทุกสไตล์ (45วิ)"),
        )),
        MenuItem("วิดีโอ...", pick_art_video,
                 checked=lambda i: cfg.art_source == "video", radio=True),
        MenuItem("รูป / GIF...", pick_art_image,
                 checked=lambda i: cfg.art_source == "image", radio=True),
    )),
    Menu.SEPARATOR,
    MenuItem("เนื้อเพลง (คาราโอเกะ)", toggle_lyrics, checked=lambda i: cfg.lyrics),
    MenuItem("ClaudePix มัสคอต", toggle_mascot, checked=lambda i: cfg.mascot),
    MenuItem("dots กลับหัว", toggle_invert, checked=lambda i: cfg.invert),
    Menu.SEPARATOR,
    MenuItem("ออก", do_quit),
)


def render_loop():
    """รัน vibe.run() — ถ้าตายเอง (exception) รีสตาร์ตเสมอ; ออกจริงเฉพาะผู้ใช้กดออก
    หมายเหตุ: run() ตั้ง stop_evt ใน finally ของมัน (พฤติกรรม CLI) → ต้อง clear ก่อนรอบใหม่
    ไม่งั้นลูปนี้ตายถาวร = อาการ 'จอนิ่งไปเลย'"""
    import time as _t
    import traceback
    while True:
        try:
            vibe.run(cfg, stop_evt)
        except Exception:
            vibe.log("tray: vibe.run พัง:\n" + traceback.format_exc())
        if _QUIT["q"]:
            break
        vibe.log("tray: รีสตาร์ต render ใน 2s")
        stop_evt.clear()
        _t.sleep(2.0)


def main():
    threading.Thread(target=render_loop, daemon=True).start()
    icon = pystray.Icon("vibe", make_icon(), "vibe — Trofeo Visualizer", MENU)
    icon.run()                # บล็อกจนกดออก
    stop_evt.set()


if __name__ == "__main__":
    main()
