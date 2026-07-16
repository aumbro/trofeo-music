"""
vibe.py — Now-Playing + Audio Visualizer บนจอ Thermalright Trofeo 9.16 (โปรโตคอล LY)

รวมของเล่น 2 อย่างในเฟรมเดียว บนจอ strip 1920x462:
  - ปกอัลบั้ม + ชื่อเพลง/ศิลปิน/อัลบั้ม + progress bar   (จาก SMTC ของ Windows)
  - spectrum bars เต้นตามเสียง output จริง               (จาก WASAPI loopback)

แหล่งข้อมูล (ดึงเองทั้งหมด ไม่ต้องมี daemon):
  Now-Playing : Windows SMTC (System Media Transport Controls) ผ่าน winsdk
                → ได้ title/artist/album/สถานะเล่น/ตำแหน่งเวลา/ปกอัลบั้ม
                จาก app ไหนก็ได้ที่คุม media key ได้ (Spotify/Apple Music/YouTube/...)
  Visualizer  : soundcard (WASAPI loopback) capture เสียงที่ลำโพงกำลังเล่น
                → FFT (numpy) → แบ่งเป็น N แถบความถี่ log-scale → smoothing attack/decay

โครง thread:
  - smtc thread  : poll now-playing ทุก ~0.5s, โหลดปกใหม่เฉพาะตอนเปลี่ยนเพลง
  - audio thread : record loopback ต่อเนื่อง → คิด band magnitudes → เก็บใน state
  - main loop    : render เฟรม → หมุน wire → JPEG → ส่งเข้าจอที่ fps ที่ตั้งไว้

โหมด (เล่นได้โดยไม่ต้องมีจอ/เสียง):
  python vibe.py                    # ของจริง: SMTC + loopback → ยิงลงจอ
  python vibe.py --demo             # เพลง/สเปกตรัมจำลอง (ไม่แตะ media/เสียง)
  python vibe.py --preview out.png  # เรนเดอร์ 1 เฟรมเป็น PNG แล้วออก (ไม่ต้องต่อจอ)
  python vibe.py --no-audio         # โชว์ now-playing อย่างเดียว (ไม่ capture เสียง)
  python vibe.py --rotate 0         # ถ้าจอกลับหัว/ตะแคง บังคับมุมหมุน wire เอง

deps เพิ่มจาก base: pip install soundcard winsdk numpy   (Windows เท่านั้น)
กด Ctrl+C เพื่อออก
"""
from __future__ import annotations

import argparse
import io
import gc
import math
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a)


# ── ธีมสี ────────────────────────────────────────────────────────────────
C_BG      = (8, 8, 12)
C_INK     = (255, 255, 255)
C_MUTE    = (150, 150, 165)
C_ACCENT  = (255, 190, 80)     # amber (โทนเดียวกับ claw.py)
C_TRACK   = (48, 48, 56)       # ราง progress
C_SHADOW  = (0, 0, 0)

# ── layout (จอ landscape 1920x462) ────────────────────────────────────────
PANEL_W, PANEL_H = 1920, 462
ART = 340                       # ด้านของกล่องปกอัลบั้ม
ART_X, ART_Y = 46, (PANEL_H - ART) // 2
PANEL_X = ART_X + ART + 56      # ขอบซ้ายของฝั่งขวา (ข้อความ/สเปกตรัม)
PANEL_R = PANEL_W - 54          # ขอบขวา
TITLE_Y = 74
META_Y = 158
SPEC_TOP, SPEC_BASE = 214, 356  # แถบสเปกตรัม (ก้นแท่ง = SPEC_BASE)
BAR_MAX = SPEC_BASE - SPEC_TOP
PROG_Y = 400                    # เส้น progress
N_BANDS = 60                    # จำนวนแท่งสเปกตรัม


# ── ฟอนต์ (เลือกตัวที่มีสระไทย) ────────────────────────────────────────────
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_font_cache: dict = {}


def _load(candidates, size):
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
        # Leelawadee UI / Tahoma รองรับไทย + ละติน
        cands = (["leelawuib.ttf", "tahomabd.ttf", "segoeuib.ttf", "arialbd.ttf"]
                 if bold else ["leelawui.ttf", "tahoma.ttf", "segoeui.ttf", "arial.ttf"])
        _font_cache[key] = _load(cands, size)
    return _font_cache[key]


# ══════════════════════════════════════════════════════════════════════════
#  Now-Playing (SMTC)
# ══════════════════════════════════════════════════════════════════════════
class MediaState:
    """สถานะเพลงที่แชร์กันระหว่าง smtc thread กับ main loop (thread-safe)

    progress เดินนาฬิกาเอง (anchor + เวลาที่ผ่านไป) เพื่อให้ลื่น 30fps —
    SMTC รายงาน position หยาบ (quantize ~วินาที) ถ้า resync ทุก poll จะกระตุก
    → แก้ anchor เฉพาะตอนต่างจริง >RESYNC_THR (seek/เปลี่ยนเพลง) เท่านั้น
    """

    RESYNC_THR = 0.75              # วินาที: ต่างเกินนี้ = seek/เปลี่ยนเพลงจริง

    def __init__(self):
        self.lock = threading.Lock()
        self.title = ""
        self.artist = ""
        self.album = ""
        self.app = ""
        self.playing = False
        self.have = False           # มีข้อมูลเพลงแล้วหรือยัง
        self.dur = 0.0
        self._apos = 0.0            # anchor: ตำแหน่งเวลา (วินาที)
        self._aat = time.monotonic()  # anchor: เวลา monotonic ตอนตั้ง anchor
        self.key = None             # (title, artist) ไว้ตรวจว่าเปลี่ยนเพลง
        self.art = None             # ปกอัลบั้ม PIL (crisp) หรือ None
        self.art_key = None
        self.lyrics = None          # list[(t,line)] เนื้อเพลงซิงค์ หรือ None
        self.lyrics_key = None

    def _extrap(self):
        """ตำแหน่งเวลาปัจจุบันจากนาฬิกาที่เดินเอง (ลื่น)"""
        if self.playing and self.dur > 0:
            return min(self.dur, self._apos + (time.monotonic() - self._aat))
        return self._apos

    def update_meta(self, title, artist, album, app, playing, pos, dur):
        with self.lock:
            track_changed = (title, artist) != self.key
            play_changed = playing != self.playing
            cur = self._extrap()                    # ตำแหน่งที่เราเดินไปถึงตอนนี้
            self.title, self.artist, self.album = title, artist, album
            self.app, self.dur = app, dur
            # แก้ anchor เฉพาะตอนจำเป็น — ไม่งั้นปล่อยนาฬิกาเดินเรียบ ๆ ต่อไป
            hard = (not self.have or track_changed or play_changed
                    or dur <= 0 or abs(pos - cur) > self.RESYNC_THR)
            if hard:
                self._apos, self._aat = pos, time.monotonic()
            self.playing = playing
            self.have = True
            self.key = (title, artist)

    def set_art(self, art, art_key):
        with self.lock:
            self.art, self.art_key = art, art_key

    def set_lyrics(self, lyrics, key):
        with self.lock:
            self.lyrics, self.lyrics_key = lyrics, key

    def snapshot(self):
        with self.lock:
            return {
                "title": self.title, "artist": self.artist, "album": self.album,
                "app": self.app, "playing": self.playing, "have": self.have,
                "pos": self._extrap(), "dur": self.dur, "art": self.art, "key": self.key,
                "lyrics": self.lyrics,
            }


def _pretty_app(app_id: str) -> str:
    """แปลง AppUserModelId ยาว ๆ ให้เป็นชื่อสั้นอ่านง่าย"""
    if not app_id:
        return ""
    a = app_id.lower()
    for needle, name in (("spotify", "Spotify"), ("apple", "Apple Music"),
                         ("chrome", "Chrome"), ("msedge", "Edge"), ("edge", "Edge"),
                         ("firefox", "Firefox"), ("vlc", "VLC"), ("foobar", "foobar2000"),
                         ("brave", "Brave"), ("youtube", "YouTube"), ("zune", "Groove")):
        if needle in a:
            return name
    return app_id.split("!")[-1][:14]


# ── เนื้อเพลงซิงค์เวลา (LRCLIB — ฟรี ไม่ต้อง key) ────────────────────────────
_LRC_RE = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]")


def clean_artist(artist, album):
    """Apple Music/บาง app ยัด 'Artist — Album' ในช่อง artist → แยกออก"""
    for sep in (" — ", " – ", " - "):
        if sep in artist:
            left, right = artist.split(sep, 1)
            return left.strip(), (album or right.strip())
    return artist, album


def parse_lrc(text):
    """LRC → list[(วินาที, บรรทัด)] เรียงตามเวลา (ข้าม tag meta/บรรทัดว่าง)"""
    out = []
    for line in text.splitlines():
        stamps = _LRC_RE.findall(line)
        if not stamps:
            continue
        lyric = _LRC_RE.sub("", line).strip()
        for mm, ss in stamps:
            out.append((int(mm) * 60 + float(ss), lyric))
    out.sort(key=lambda x: x[0])
    return out


def _lrc_http(url):
    req = urllib.request.Request(url, headers={"User-Agent": "vibe.py-trofeo (github.com/aumbro/trofeo-music)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        import json
        return json.load(r)


def fetch_lyrics(title, artist, album, dur):
    """คืน list[(t,line)] ของเนื้อเพลงซิงค์ หรือ None ถ้าไม่มี (LRCLIB: get เป๊ะ→ search หลวม)"""
    if not title:
        return None
    artist, album = clean_artist(artist, album)
    try:
        q = {"track_name": title, "artist_name": artist, "album_name": album,
             "duration": int(dur)}
        data = _lrc_http("https://lrclib.net/api/get?" + urllib.parse.urlencode(q))
    except urllib.error.HTTPError as e:
        data = None if e.code != 404 else None
    except Exception:
        return None
    if not data:                                      # get ไม่เจอ → search หลวม
        try:
            res = _lrc_http("https://lrclib.net/api/search?" + urllib.parse.urlencode(
                {"track_name": title, "artist_name": artist}))
            data = next((x for x in res if x.get("syncedLyrics")), None) if res else None
        except Exception:
            return None
    if data and data.get("syncedLyrics"):
        lines = parse_lrc(data["syncedLyrics"])
        return lines or None
    return None


def smtc_poller(state: MediaState, stop_evt: threading.Event, want_lyrics=False):
    """thread: อ่าน SMTC ทุก ~0.5s (winsdk เป็น async → รัน event loop ในเธรดนี้)"""
    import asyncio
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MM,
    )
    from winsdk.windows.storage.streams import (
        Buffer, InputStreamOptions, DataReader,
    )

    async def read_thumb(ref):
        stream = await ref.open_read_async()
        size = stream.size
        if not size:
            return None
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        reader = DataReader.from_buffer(buf)
        arr = bytearray(size)
        reader.read_bytes(arr)                # winsdk เติม bytes ลง arr ในที่
        return Image.open(io.BytesIO(bytes(arr))).convert("RGB")

    async def once(mgr):
        cur = mgr.get_current_session()
        if cur is None:
            state.update_meta("", "", "", "", False, 0, 0)
            return
        props = await cur.try_get_media_properties_async()
        tl = cur.get_timeline_properties()
        pb = cur.get_playback_info()
        playing = int(pb.playback_status) == 4        # 4 = Playing
        pos = tl.position.total_seconds() if tl.position else 0.0
        dur = tl.end_time.total_seconds() if tl.end_time else 0.0
        app = _pretty_app(cur.source_app_user_model_id or "")
        title = props.title or ""
        artist = props.artist or props.album_artist or ""
        album = props.album_title or ""
        state.update_meta(title, artist, album, app, playing, pos, dur)

        # โหลดปกใหม่เฉพาะตอนเปลี่ยนเพลง (ประหยัด)
        art_key = (title, artist)
        if art_key != state.art_key:
            art = None
            if props.thumbnail is not None:
                try:
                    art = await read_thumb(props.thumbnail)
                except Exception:
                    art = None
            state.set_art(art, art_key)

        # ดึงเนื้อเพลงใหม่เฉพาะตอนเปลี่ยนเพลง (เฉพาะโหมด --lyrics) — network ใน executor กันบล็อก
        if want_lyrics and title and art_key != state.lyrics_key:
            state.set_lyrics(None, art_key)           # เคลียร์ก่อน (โชว์ "กำลังโหลด")
            try:
                lines = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_lyrics, title, artist, album, dur)
            except Exception:
                lines = None
            state.set_lyrics(lines, art_key)

    async def run():
        mgr = await MM.request_async()
        while not stop_evt.is_set():
            try:
                await once(mgr)
            except Exception as e:
                log("smtc:", type(e).__name__, e)
            await asyncio.sleep(0.5)

    try:
        asyncio.run(run())
    except Exception as e:
        log("smtc thread ตาย:", type(e).__name__, e)


# ══════════════════════════════════════════════════════════════════════════
#  Visualizer (WASAPI loopback → FFT → bands)
# ══════════════════════════════════════════════════════════════════════════
class Spectrum:
    """band magnitudes 0..1 ที่แชร์กันระหว่าง audio thread กับ main loop"""

    def __init__(self, n=N_BANDS):
        self.lock = threading.Lock()
        self.bands = np.zeros(n, dtype=np.float32)
        self.n = n
        self.active = False        # capture ได้จริงไหม

    def set(self, bands, active=True):
        with self.lock:
            self.bands = bands
            self.active = active

    def get(self):
        with self.lock:
            return self.bands.copy(), self.active


# ขอบแบ่งแถบความถี่แบบ log (คำนวณครั้งเดียว)
def _band_edges(n, sr, fft_n, fmin=40.0, fmax=16000.0):
    fmax = min(fmax, sr / 2)
    freqs = np.fft.rfftfreq(fft_n, 1.0 / sr)
    edges = np.geomspace(fmin, fmax, n + 1)
    idx = np.searchsorted(freqs, edges)
    idx = np.clip(idx, 1, len(freqs) - 1)
    return idx


def audio_capture(spec: Spectrum, stop_evt: threading.Event, sr=48000, fft_n=2048,
                  gain=1.0, agc=True):
    """thread: record loopback ต่อเนื่อง → FFT → band magnitudes (attack เร็ว decay ช้า)
    gain = ตัวคูณความไว · agc = auto-gain (ยอดดังสุด→~0.85 อัตโนมัติไม่ว่าเพลงดัง/เบา)"""
    import warnings
    import soundcard as sc
    # WASAPI loopback แจ้ง "data discontinuity" บ่อยตอนมีช่วงเงียบ/บัฟเฟอร์สะดุด — ไม่อันตราย ปิดไว้
    warnings.filterwarnings("ignore", message="data discontinuity in recording")

    edges = _band_edges(spec.n, sr, fft_n)
    window = np.hanning(fft_n).astype(np.float32)
    ring = np.zeros(fft_n, dtype=np.float32)
    smoothed = np.zeros(spec.n, dtype=np.float32)
    ATTACK, DECAY = 0.55, 0.16     # ขึ้นเร็ว ตกช้า (นุ่มตา)
    tilt = np.linspace(1.0, 1.5, spec.n).astype(np.float32)  # ชดเชยย่านสูง (พลังงานน้อย)
    # ── AGC state ──
    agc_ref = 0.5                  # ระดับอ้างอิงยอด (ค่อย ๆ ปรับตามเพลง)
    AGC_TARGET = 0.85              # อยากให้ยอดดังสุดขึ้นไปแตะ ~นี้
    AGC_MIN, AGC_MAX = 0.35, 9.0   # จำกัดช่วง auto-gain (กันบูสต์เว่อร์)

    def open_recorder():
        spk = sc.default_speaker()
        mic = sc.get_microphone(str(spk.name), include_loopback=True)
        return mic.recorder(samplerate=sr, channels=2, blocksize=1024)

    # ── กัน "เปิดแอปก่อนเปิดเพลง" ──
    # WASAPI loopback ที่ arm ตอนไม่มีเสียงเล่น จะส่งแต่ frame ศูนย์ล้วน และค้างแบบนั้น
    # แม้เพลงจะเริ่มเล่นทีหลัง (ต้อง restart ถึงหาย) → ถ้าเงียบสนิทต่อเนื่องนานพอ ให้ reopen
    # recorder ใหม่ พอมีเสียงจริงไหลอยู่ตอนเปิด มันจับติดเอง
    REOPEN_AFTER = max(1, int(sr / 1024 * 2.0))   # เงียบสนิท ~2s → เปิด recorder ใหม่
    silent_blocks = 0

    while not stop_evt.is_set():
        try:
            with open_recorder() as rec:
                log("visualizer: capturing loopback ...")
                while not stop_evt.is_set():
                    data = rec.record(numframes=1024)          # (frames, ch)
                    # เงียบสนิท (ศูนย์ล้วน = ไม่มี stream เล่นอยู่) นานพอ → reopen ให้จับเสียงใหม่ติด
                    if float(np.abs(data).max()) == 0.0:
                        silent_blocks += 1
                        if silent_blocks >= REOPEN_AFTER:
                            silent_blocks = 0
                            spec.set(np.zeros(spec.n, dtype=np.float32), active=False)
                            break                              # ออกไป reopen recorder
                    else:
                        silent_blocks = 0
                    mono = data.mean(axis=1).astype(np.float32)
                    m = len(mono)
                    ring = np.roll(ring, -m)
                    ring[-m:] = mono

                    spectrum = np.abs(np.fft.rfft(ring * window))
                    raw = np.empty(spec.n, dtype=np.float32)
                    for i in range(spec.n):
                        lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
                        raw[i] = spectrum[lo:hi].mean()

                    # log-compress + normalize + tilt
                    mag = np.clip(np.log1p(raw * 5.0) / 5.6, 0.0, 1.0)
                    pre = mag * tilt                        # ก่อนปรับ gain (ยอด ~0..1.5)
                    peak = float(pre.max())
                    if agc:
                        # AGC: ref ตามยอด (ดังขึ้น=ไว, เบาลง=ช้า) → auto-gain ให้ยอด→~TARGET
                        rate = 0.30 if peak > agc_ref else 0.010
                        agc_ref += (peak - agc_ref) * rate
                        auto = min(AGC_MAX, max(AGC_MIN, AGC_TARGET / max(agc_ref, 0.03)))
                        gate = min(1.0, max(0.0, (peak - 0.03) / 0.06))   # เงียบ→เฟดหาย กันบูสต์ noise
                        mag = np.clip(pre * auto * gain, 0.0, 1.0) * 0.92 * gate
                    else:                                   # --no-agc: gain ตายตัว
                        mag = np.clip(pre * gain, 0.0, 1.0) * 0.92

                    up = mag > smoothed
                    smoothed = np.where(up, smoothed + (mag - smoothed) * ATTACK,
                                        smoothed * (1.0 - DECAY) + mag * DECAY)
                    spec.set(smoothed.astype(np.float32), active=True)
        except Exception as e:
            log("audio:", type(e).__name__, e, "— ลองใหม่ใน 2s")
            spec.set(np.zeros(spec.n, dtype=np.float32), active=False)
            stop_evt.wait(2.0)


def demo_bands(n, t):
    """แถบสเปกตรัมจำลอง (โหมด demo/preview) — ให้ดูมีชีวิต"""
    x = np.linspace(0, 1, n)
    b = (0.45 + 0.45 * np.sin(2 * math.pi * (x * 3 + t * 0.7)))
    b *= (0.6 + 0.4 * np.sin(2 * math.pi * (x * 7 - t * 1.3)))
    b *= np.exp(-x * 1.1) + 0.25          # เอียงให้เบสสูงกว่าปลาย
    return np.clip(b * 1.3, 0.02, 0.92).astype(np.float32)


# ══════════════════════════════════════════════════════════════════════════
#  วาดเฟรม
# ══════════════════════════════════════════════════════════════════════════
_FALLBACK_BG = None


def fallback_bg():
    """พื้นหลัง gradient เข้ม (ตอนไม่มีปก) — สร้างครั้งเดียวด้วย numpy แล้วแคช"""
    global _FALLBACK_BG
    if _FALLBACK_BG is None:
        yy = np.linspace(0, 1, PANEL_H)[:, None]
        xx = np.linspace(0, 1, PANEL_W)[None, :]
        r = (8 + xx * 10).astype(np.uint8)
        g = (10 + yy * 8 + xx * 4).astype(np.uint8)
        b = (18 + yy * 26 + xx * 14).astype(np.uint8)
        arr = np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)
        arr[..., 0] = r
        arr[..., 1] = g
        arr[..., 2] = b
        _FALLBACK_BG = Image.fromarray(arr, "RGB")
    return _FALLBACK_BG


_FALLBACK_BG_P = None


def fallback_bg_portrait():
    """พื้นหลัง gradient เข้มแนวตั้ง 462x1920 (ตอนไม่มีปก) — แคชครั้งเดียว"""
    global _FALLBACK_BG_P
    if _FALLBACK_BG_P is None:
        w, h = PANEL_H, PANEL_W          # 462 x 1920
        yy = np.linspace(0, 1, h)[:, None]
        xx = np.linspace(0, 1, w)[None, :]
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        arr[..., 0] = (8 + xx * 8).astype(np.uint8)
        arr[..., 1] = (10 + yy * 10).astype(np.uint8)
        arr[..., 2] = (18 + yy * 30).astype(np.uint8)
        _FALLBACK_BG_P = Image.fromarray(arr, "RGB")
    return _FALLBACK_BG_P


def _round_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def make_art_assets(art: Image.Image):
    """คืน (art_crisp 340x340 มุมมน, bg_blur 1920x462 มืด) — คิดครั้งเดียวต่อเพลง"""
    # ปก crisp (cover-crop เป็นสี่เหลี่ยมจัตุรัส)
    sw, sh = art.size
    s = min(sw, sh)
    sq = art.crop(((sw - s) // 2, (sh - s) // 2, (sw - s) // 2 + s, (sh - s) // 2 + s))
    crisp = sq.resize((ART, ART), Image.LANCZOS)
    crisp.putalpha(_round_mask((ART, ART), 24))

    # พื้นหลัง: ขยายปกเต็มจอ → เบลอ → หรี่แสง
    bg = art.resize((PANEL_W, PANEL_W), Image.LANCZOS)  # cover พอ ๆ
    bg = bg.crop((0, (PANEL_W - PANEL_H) // 2, PANEL_W,
                  (PANEL_W - PANEL_H) // 2 + PANEL_H))
    bg = bg.filter(ImageFilter.GaussianBlur(38))
    dark = Image.new("RGB", (PANEL_W, PANEL_H), (0, 0, 0))
    bg = Image.blend(bg, dark, 0.62)

    # พื้นหลังแนวตั้ง (462 กว้าง x 1920 สูง): ขยายปกเป็นจัตุรัสใหญ่ → crop กลางแนวตั้ง → เบลอ → หรี่
    bp = art.resize((PANEL_W, PANEL_W), Image.LANCZOS)   # square ใหญ่ (1920x1920)
    cx0 = (PANEL_W - PANEL_H) // 2                       # (1920-462)/2
    bp = bp.crop((cx0, 0, cx0 + PANEL_H, PANEL_W))       # 462 x 1920
    bp = bp.filter(ImageFilter.GaussianBlur(40))
    bp = Image.blend(bp, Image.new("RGB", (PANEL_H, PANEL_W), (0, 0, 0)), 0.62)

    accent, hue = dominant_accent(art)
    return crisp, bg, accent, hue, bp


def fmt_time(sec: float) -> str:
    if sec is None or sec <= 0:
        return "0:00"
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _fit_text(d, text, f, max_w):
    """ตัดข้อความ + ใส่ … ถ้ายาวเกิน max_w (คืน string ที่พอดี)"""
    if d.textlength(text, font=f) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        if d.textlength(text[:mid] + ell, font=f) <= max_w:
            lo = mid + 1
        else:
            hi = mid
    return text[:max(0, lo - 1)] + ell


def draw_marquee(img, d, x, y, w, text, f, fill, t, speed=70, gap=90, center=False):
    """วาดข้อความที่ y — ถ้ายาวเกิน w จะเลื่อนวน (marquee) ไม่ตัดทิ้ง
    center=True: ถ้าพอดี w ให้จัดกลาง (portrait) แทนชิดซ้าย"""
    tw = d.textlength(text, font=f)
    if tw <= w:
        if center:
            d.text((x + w / 2, y), text, font=f, fill=fill, anchor="mt")
        else:
            d.text((x, y), text, font=f, fill=fill, anchor="lt")
        return
    asc, desc = f.getmetrics()
    strip = Image.new("RGBA", (int(w), asc + desc + 6), (0, 0, 0, 0))
    sd = ImageDraw.Draw(strip)
    off = (t * speed) % (tw + gap)
    sd.text((-off, 0), text, font=f, fill=fill, anchor="lt")
    sd.text((-off + tw + gap, 0), text, font=f, fill=fill, anchor="lt")  # ชุดที่ 2 ให้ต่อเนียน
    img.paste(strip, (int(x), int(y)), strip)


def band_color(frac_x: float, mag: float, base_hue: float = 175.0, rng: float = 150.0):
    """สีแท่ง: ไล่ hue ตามความถี่ (เริ่มที่ base_hue) ความสว่างตาม magnitude
    default = ฟ้า→ชมพู (175°→325°) · ถ้ามีธีมจากปกจะส่ง base_hue มาแทน"""
    hue = base_hue + frac_x * rng
    v = 0.45 + 0.55 * mag
    return _hsv(hue, 0.72, v)


def _lerp(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def dominant_accent(art):
    """ดูดสี accent สด ๆ จากปกอัลบั้ม — median ของพิกเซลที่อิ่มสี แล้วปั๊ม S/V ให้จี๊ด
    คืน (accent_rgb, hue_deg) เพื่อเอาไปทำธีมทั้งเฟรม (progress/meta/ไอคอน/แท่ง)"""
    import colorsys
    small = np.asarray(art.resize((48, 48)).convert("RGB")).reshape(-1, 3).astype(np.float32)
    mx = small.max(1)
    s = np.where(mx > 0, (mx - small.min(1)) / np.maximum(mx, 1), 0.0)
    v = mx / 255.0
    mask = (s > 0.35) & (v > 0.25)
    pick = small[mask] if int(mask.sum()) > 30 else small   # ไม่มีสีจัดเลยก็เฉลี่ยทั้งภาพ
    r, g, b = (np.median(pick, axis=0) / 255.0)
    h, sv, vv = colorsys.rgb_to_hsv(float(r), float(g), float(b))
    accent = _hsv(h * 360, min(1.0, max(sv, 0.6)), min(1.0, max(vv, 0.9)))
    return accent, h * 360


def _hsv(h, s, v):
    h = (h % 360) / 60.0
    i = int(h)
    f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return (int(r * 255), int(g * 255), int(b * 255))


# ── มัสคอต ClaudePix (พอร์ต sprite จาก claw.py) เด้งตามบีต ──────────────────
# '#'=ตัว 'X'=ตา '.'=โปร่งใส  (20x20)
CLAWD = [
    "....................", "....................",
    "....................", "....................",
    ".....###########....", ".....###########....",
    ".....##X#####X##....", "...####X#####X####..",
    "...###############..", "...###############..",
    "...#.###########.#..", ".....###########....",
    ".....###########....", ".....###########....",
    ".....#..#...#..#....", ".....#..#...#..#....",
    ".....#..#...#..#....", "....................",
    "....................", "....................",
]


class Beat:
    """ตรวจจับบีตจากเบส (low bands) → energy (ต่อเนื่อง) + kick (แรงกระแทกบีต)"""

    def __init__(self):
        self.env = 0.0        # envelope เบส (ตกเร็ว)
        self.slow = 0.0       # ค่าเฉลี่ยช้า (baseline)

    def update(self, bands):
        n = min(8, len(bands))
        bass = float(np.mean(bands[:n])) if n else 0.0
        self.env = max(bass, self.env * 0.86)
        self.slow = self.slow * 0.98 + bass * 0.02
        kick = max(0.0, min(1.0, (self.env - self.slow - 0.10) * 3.5))
        energy = min(1.0, self.env * 1.25)
        return energy, kick


class MascotAnim:
    """สเตตการเต้นของ ClaudePix ให้ลื่น:
    - สะสม 'phase' เอง (phase += speed*dt) → ต่อเนื่องแม้ speed เปลี่ยนทุกเฟรม
      (ถ้าใช้ sin(t*speed) ตรง ๆ เฟสจะกระโดดตอน speed ขยับ = กระตุก)
    - low-pass energy/kick + ease ค่า hop/sway → ไม่มีสะดุด
    ค่า hop/sway เป็น 'base px' (ที่ cell=7) ให้ draw คูณ k=cell/7 เอง"""

    def __init__(self):
        self.phase = 0.0
        self.energy = 0.14
        self.kick = 0.0
        self.hop = 0.0
        self.sway = 0.0
        self.blink_t = 0.0
        self.blink = False

    def step(self, energy, kick, dt):
        dt = max(0.0, min(0.1, dt))                 # กัน dt เพี้ยน (เฟรมแรก/สะดุด)
        self.energy += (energy - self.energy) * min(1.0, dt * 6.0)
        self.kick = max(kick, self.kick - dt * 2.5)  # kick พุ่งขึ้นทันที ตกนุ่ม
        speed = 4.0 + 6.0 * self.energy
        self.phase += speed * dt
        amp = 2.0 + 9.0 * self.energy + 14.0 * self.kick
        raw_hop = amp * abs(math.sin(self.phase))
        self.hop += (raw_hop - self.hop) * min(1.0, dt * 16.0)
        raw_sway = 6.0 * math.sin(self.phase * 0.5) * (0.4 + 0.6 * self.energy)
        self.sway += (raw_sway - self.sway) * min(1.0, dt * 9.0)
        self.blink_t += dt
        self.blink = (self.blink_t % 2.6) < 0.13
        return self


def draw_mascot(d, cx, foot_y, cell, anim, body, eye=(22, 26, 36)):
    """วาด ClaudePix จากสเตต MascotAnim (เต้นลื่น)"""
    k = cell / 7.0
    hop = int(anim.hop * k)
    sway = int(anim.sway * k)
    blink = anim.blink
    top = foot_y - 17 * cell - hop
    # เงาพื้น (จางลง/แคบลงตอนลอยสูง)
    rx = max(4, int((34 - hop * 0.5) * k))
    ry = max(3, int(5 * k))
    d.ellipse([cx + sway - rx, foot_y - ry, cx + sway + rx, foot_y + ry], fill=(0, 0, 0, 110))
    ox = cx + sway - 10 * cell
    for r in range(20):
        row = CLAWD[r]
        for c in range(20):
            ch = row[c]
            if ch == ".":
                continue
            col = (body if blink else eye) if ch == "X" else body
            x0, y0 = ox + c * cell, top + r * cell
            d.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=col)


# ── ประกายวิบวับบนปก (ดาวกระพริบ + วิบแรงตอนบีต) ─────────────────────────────
# (rx, ry, phase, speed, scale) — ตำแหน่งสัมพัทธ์ 0..1 บนปก
_SPARKLES = [
    (0.14, 0.20, 0.0, 2.1, 1.0), (0.78, 0.12, 1.3, 1.7, 0.8),
    (0.30, 0.62, 2.6, 2.3, 1.1), (0.62, 0.40, 0.7, 1.9, 0.9),
    (0.86, 0.55, 3.1, 2.0, 0.85), (0.20, 0.85, 1.9, 1.6, 0.7),
    (0.50, 0.16, 2.2, 2.4, 1.0), (0.71, 0.80, 0.4, 1.8, 0.95),
    (0.40, 0.34, 3.5, 2.0, 0.8), (0.90, 0.28, 1.1, 1.5, 0.7),
    (0.10, 0.50, 2.9, 1.7, 0.75), (0.55, 0.72, 0.9, 2.2, 1.0),
]


def draw_sparkles(d, x, y, w, h, t, kick, tint=(255, 255, 255)):
    """วาดดาวประกายกระพริบทั่วกล่องปก (x,y,w,h) — วิบแรงขึ้นตาม kick (บีต)"""
    for rx, ry, ph, spd, sc in _SPARKLES:
        p = math.sin(t * spd + ph)
        b = p * p if p > 0 else 0.0                         # วิบ (ครึ่งหนึ่งติดพร้อมกัน)
        b = min(1.0, b + kick * 0.5 * max(0.0, math.sin(t * 9.0 + ph)))
        if b < 0.06:
            continue
        sx, sy = x + rx * w, y + ry * h
        rad = 0.024 * w * sc * (0.5 + 0.5 * b)
        a = int(215 * b)
        c1, c2 = tint + (a,), tint + (a // 2,)
        d.line([(sx - rad, sy), (sx + rad, sy)], fill=c1, width=2)     # แกนตั้ง/นอน
        d.line([(sx, sy - rad), (sx, sy + rad)], fill=c1, width=2)
        r2 = rad * 0.5
        d.line([(sx - r2, sy - r2), (sx + r2, sy + r2)], fill=c2, width=1)  # ทแยง
        d.line([(sx - r2, sy + r2), (sx + r2, sy - r2)], fill=c2, width=1)
        d.ellipse([sx - 2, sy - 2, sx + 2, sy + 2],
                  fill=(255, 255, 255, min(255, a + 40)))                # แกนกลางสว่าง


# ── ขอบปกเรืองแสง (glow) เต้นตามบีต ─────────────────────────────────────────
_GLOW_CACHE: dict = {}


def _glow_base(size, pad):
    """base glow ขาว-เบลอ (rounded rect ขนาด size, ขยาย pad รอบด้าน) — สร้างครั้งเดียวต่อ (size,pad)"""
    key = (size, pad)
    if key not in _GLOW_CACHE:
        wh = size + 2 * pad
        g = Image.new("RGBA", (wh, wh), (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle(
            [pad, pad, pad + size, pad + size], radius=int(size * 0.08),
            fill=(255, 255, 255, 255))
        _GLOW_CACHE[key] = g.filter(ImageFilter.GaussianBlur(pad * 0.55))
    return _GLOW_CACHE[key]


def glow_strength(m):
    """ความเข้ม glow 0..1 จาก energy/kick ของ MascotAnim (ฐาน ~0.22 พรึบตอนบีต)"""
    if m is None:
        return 0.28
    return max(0.0, min(1.0, 0.22 + 0.35 * m.energy + 0.75 * m.kick))


def draw_art_glow(img, ax, ay, size, pad, accent, strength):
    """แปะ halo สี accent รอบกล่องปกที่ (ax,ay) ขนาด size — ความเข้มตาม strength"""
    if strength <= 0.02:
        return
    base = _glow_base(size, pad)
    alpha = base.getchannel("A").point(lambda v: int(v * strength))
    glow = Image.new("RGBA", base.size, accent + (0,))
    glow.putalpha(alpha)
    img.paste(glow, (ax - pad, ay - pad), glow)


def _render_full(snap, img, bands, t, peaks, mascot, accent, viz, have):
    """โหมด --full: viz เต็มจอ 1920x462 + now-playing แถบเล็กล่าง"""
    W, H = PANEL_W, PANEL_H
    img = Image.blend(img, Image.new("RGB", (W, H), (4, 5, 12)), 0.5)   # มืดลงให้นีออนพุ่ง
    d = ImageDraw.Draw(img, "RGBA")
    art_assets = snap.get("_assets")
    wbase = wave_base_hue(art_assets[3] if art_assets else 210.0)
    e = getattr(mascot, "energy", 0.28)
    kk = getattr(mascot, "kick", 0.0)
    vy, vh = 6, H - 104                              # โซน viz (เว้นล่างให้แถบ now-playing)
    if snap.get("_lyrics_mode"):
        draw_lyrics(img, d, 40, vy, W - 80, vh, snap.get("lyrics"), snap["pos"], accent)
    elif viz == "dots":
        draw_dot_matrix(d, 24, vy, W - 48, vh, bands, peaks, wbase,
                        cols=64, invert=snap.get("_invert", False))
    elif viz == "bars":
        draw_mirror_bars(d, 0, vy, W, vh, bands, wbase, e, orient="h")
    elif viz == "ribbon":
        draw_ribbon_wave(img, 0, vy, W, vh, t, bands, e, wbase, orient="h")
    elif viz == "classic":
        draw_classic_bars(d, 24, vy, W - 48, vh, bands, peaks, wbase)
    else:
        particle_field().draw(img, 0, vy, W, vh, t, bands, e, kk, wbase)
    # ── แถบ now-playing ล่าง ──
    d.rectangle([0, H - 96, W, H], fill=(0, 0, 0, 120))
    if have:
        tx = 30
        if art_assets:
            cov = art_assets[0].resize((66, 66), Image.LANCZOS)
            img.paste(cov, (28, H - 82), cov)
            tx = 112
        draw_marquee(img, d, tx, H - 84, W - tx - 260, snap["title"], font(30),
                     C_INK, t, speed=55)
        meta = " · ".join(x for x in (snap["artist"], snap["album"]) if x)
        if meta:
            draw_marquee(img, d, tx, H - 44, W - tx - 260, meta, font(22, bold=False),
                         accent, t, speed=45)
        pos, dur = snap["pos"], snap["dur"]
        if dur > 0:
            d.text((W - 26, H - 56), f"{fmt_time(pos)} / {fmt_time(dur)}",
                   font=font(24, bold=False), fill=C_MUTE, anchor="rm")
            frac = max(0.0, min(1.0, pos / dur))
            d.rectangle([0, H - 6, W, H], fill=C_TRACK)
            d.rectangle([0, H - 6, int(W * frac), H], fill=accent)
    return img


def render(snap, bands, audio_active, t, peaks=None, mascot=None):
    """วาด 1 เฟรม landscape 1920x462 คืน PIL.Image
    peaks=ยอดค้างของแต่ละแท่ง · mascot=MascotAnim โชว์ ClaudePix แทนปก (หรือ None)"""
    have = snap["have"] and (snap["title"] or snap["artist"])
    art_assets = snap.get("_assets")

    # พื้นหลัง + ธีมสี (accent/base_hue ดูดจากปกอัลบั้ม ถ้ามี)
    if art_assets:
        img = art_assets[1].copy()
        accent = art_assets[2]
        base_hue = art_assets[3] - 40.0     # ให้แท่งกวาด hue เริ่มก่อนสีปกนิดหน่อย
    else:
        img = fallback_bg().copy()
        accent = C_ACCENT
        base_hue = 175.0
    d = ImageDraw.Draw(img, "RGBA")

    # ── โหมด --full: viz/เนื้อเพลง เต็มจอ (ไม่มีเลย์เอาต์ปกซ้าย) ──
    if snap.get("_full") and (snap.get("_lyrics_mode")
                              or snap.get("_viz") in ("wave", "dots", "bars", "ribbon", "classic")):
        return _render_full(snap, img, bands, t, peaks, mascot, accent, snap.get("_viz"), have)

    # ── visualizer (วาดก่อน ให้ข้อความอยู่หน้า) — default=แท่งคลาสสิก · --viz เปลี่ยนสไตล์ ──
    span = PANEL_R - PANEL_X
    viz = snap.get("_viz")
    if snap.get("_lyrics_mode"):
        draw_lyrics(img, d, PANEL_X, 184, span, 210, snap.get("lyrics"), snap["pos"], accent)
    elif viz in ("wave", "dots", "bars", "ribbon", "classic"):
        vx, vy, vh = PANEL_X, 190, 190              # โซน visualizer ฝั่งขวา (กว้าง×สูง)
        wbase = wave_base_hue(art_assets[3] if art_assets else 210.0)
        e = getattr(mascot, "energy", 0.28)
        kk = getattr(mascot, "kick", 0.0)
        if viz == "dots":
            draw_dot_matrix(d, vx, vy, span, vh, bands, peaks, wbase,
                            cols=48, invert=snap.get("_invert", False))
        elif viz == "bars":
            draw_mirror_bars(d, vx, vy, span, vh, bands, wbase, e, orient="h")
        elif viz == "ribbon":
            draw_ribbon_wave(img, vx, vy, span, vh, t, bands, e, wbase, orient="h")
        elif viz == "classic":
            draw_classic_bars(d, vx, vy, span, vh, bands, peaks, wbase)
        else:
            particle_field().draw(img, vx, vy, span, vh, t, bands, e, kk, wbase)
    else:
        n = len(bands)
        gap = 4
        bw = (span - gap * (n - 1)) / n
        for i in range(n):
            mag = float(bands[i])
            h = int(BAR_MAX * mag)
            x0 = PANEL_X + i * (bw + gap)
            x1 = x0 + bw
            col = band_color(i / max(1, n - 1), mag, base_hue)
            d.rounded_rectangle([x0, SPEC_BASE - h, x1, SPEC_BASE],
                                radius=int(bw / 2), fill=col + (235,))
            rh = h // 3
            if rh > 1:
                d.rectangle([x0, SPEC_BASE + 2, x1, SPEC_BASE + 2 + rh],
                            fill=col + (40,))
            if peaks is not None:
                ph = int(BAR_MAX * float(peaks[i]))
                if ph > 2:
                    cap_y = SPEC_BASE - ph
                    cap_col = _lerp(col, (255, 255, 255), 0.6)
                    d.rounded_rectangle([x0, cap_y - 4, x1, cap_y], radius=2,
                                        fill=cap_col + (255,))

    # ── ปกอัลบั้ม / มัสคอต ClaudePix (glow ขอบเต้นตามบีตก่อน) ──
    force_m = snap.get("_force_mascot")
    if snap.get("_glow", True):
        draw_art_glow(img, ART_X, ART_Y, ART, 44, accent, glow_strength(mascot))
    if art_assets and not force_m:
        img.paste(art_assets[0], (ART_X, ART_Y), art_assets[0])
        if snap.get("_sparkle", True):
            draw_sparkles(d, ART_X, ART_Y, ART, ART, t, getattr(mascot, "kick", 0.0))
    elif mascot is not None:                      # ไม่มีปก (หรือบังคับ) → ClaudePix เต้น
        d.rounded_rectangle([ART_X, ART_Y, ART_X + ART, ART_Y + ART],
                            radius=24, fill=(18, 18, 26))
        draw_mascot(d, ART_X + ART // 2, ART_Y + ART - 34, 14, mascot, accent)
    else:
        d.rounded_rectangle([ART_X, ART_Y, ART_X + ART, ART_Y + ART],
                            radius=24, fill=(30, 30, 38))
        note = font(160)
        d.text((ART_X + ART / 2, ART_Y + ART / 2), "♪", font=note,
               fill=C_MUTE, anchor="mm")

    # ── ข้อความ now-playing ──
    if have:
        title = snap["title"]
        meta = " · ".join(x for x in (snap["artist"], snap["album"]) if x)
        f_title = font(60)
        f_meta = font(32, bold=False)
        draw_marquee(img, d, PANEL_X, TITLE_Y, span, title, f_title, C_INK, t, speed=70)
        if meta:
            draw_marquee(img, d, PANEL_X, META_Y, span - 200, meta, f_meta, accent, t, speed=55)
    else:
        d.text((PANEL_X, TITLE_Y), "ไม่มีเพลงเล่นอยู่", font=font(56),
               fill=C_MUTE, anchor="lt")
        d.text((PANEL_X, META_Y), "เปิดเพลงใน Spotify / Apple Music / YouTube",
               font=font(30, bold=False), fill=C_MUTE, anchor="lt")

    # ── progress bar + เวลา ──
    pos, dur = snap["pos"], snap["dur"]
    if have and dur > 0:
        tcur, ttot = fmt_time(pos), fmt_time(dur)
        f_t = font(28, bold=False)
        tw = d.textlength(f"{tcur} / {ttot}", font=f_t)
        bar_r = PANEL_R - tw - 22
        frac = max(0.0, min(1.0, pos / dur))
        d.rounded_rectangle([PANEL_X, PROG_Y, bar_r, PROG_Y + 10], radius=5, fill=C_TRACK)
        fx = PANEL_X + int((bar_r - PANEL_X) * frac)
        if fx > PANEL_X + 10:
            d.rounded_rectangle([PANEL_X, PROG_Y, fx, PROG_Y + 10], radius=5, fill=accent)
        d.ellipse([fx - 8, PROG_Y - 3, fx + 8, PROG_Y + 13], fill=C_INK)
        d.text((PANEL_R, PROG_Y + 5), f"{tcur} / {ttot}", font=f_t,
               fill=C_MUTE, anchor="rm")

    # ── badge: app + สถานะเล่น/หยุด (วาดไอคอนเอง กันฟอนต์ไม่มี glyph) ──
    if have:
        f_b = font(26)
        app_txt = snap["app"] or ""
        tw2 = d.textlength(app_txt, font=f_b) if app_txt else 0
        icon = 20                                   # ขนาดไอคอน play/pause
        padL, padR, gap = 20, 22, 12
        bw2 = padL + icon + (gap + tw2 if app_txt else 0) + padR
        bx1, by0, bh = PANEL_R, 20, 44
        cy = by0 + bh / 2
        d.rounded_rectangle([bx1 - bw2, by0, bx1, by0 + bh], radius=bh / 2,
                            fill=(0, 0, 0, 120), outline=C_MUTE + (140,), width=2)
        ix = bx1 - bw2 + padL
        if snap["playing"]:                         # สามเหลี่ยม ▶
            d.polygon([(ix, cy - icon / 2), (ix, cy + icon / 2),
                       (ix + icon * 0.86, cy)], fill=accent)
        else:                                       # สองขีด ⏸
            bw3 = icon * 0.32
            d.rectangle([ix, cy - icon / 2, ix + bw3, cy + icon / 2], fill=accent)
            d.rectangle([ix + icon - bw3, cy - icon / 2, ix + icon, cy + icon / 2],
                        fill=accent)
        if app_txt:
            d.text((ix + icon + gap, cy), app_txt, font=f_b, fill=C_INK, anchor="lm")

    # จุดสถานะ visualizer (มุมซ้ายบนของแถบ) — เขียว=มีเสียง
    if not audio_active and have:
        d.text((PANEL_X, SPEC_BASE + 6), "ไม่ได้ capture เสียง (--no-audio หรือเงียบ)",
               font=font(20, bold=False), fill=C_MUTE, anchor="lt")
    return img


# ── เส้นเสียงแบบ particle wave (ริบบิ้นจุดไหลลื่น ไล่สี — สไตล์ภาพอ้างอิง) ──────
class ParticleField:
    """สนามอนุภาคไหลลื่นหลายเลนคลื่นนอน จุดกระจายรอบเส้น (gaussian → หนาแน่นกลาง)
    reactive: amplitude/เลน จาก spectrum · การฟุ้ง จาก energy · brightness พรึบ จาก kick
    สุ่ม param ครั้งเดียว (seed คงที่) แล้ว flow ตาม t → ไม่กระพริบมั่ว"""

    def __init__(self, n=220, lanes=4):
        rng = np.random.default_rng(7)
        self.n, self.lanes = n, lanes
        self.lane = rng.integers(0, lanes, n)
        self.fx0 = rng.random(n).astype(np.float32)
        self.perp = rng.normal(0.0, 1.0, n).astype(np.float32)     # ระยะตั้งฉากเส้น
        self.size = rng.uniform(1.0, 3.4, n).astype(np.float32)
        self.bph = rng.uniform(0.0, 6.283, n).astype(np.float32)   # เฟส twinkle
        self.spd = rng.uniform(0.75, 1.25, n).astype(np.float32)   # ความเร็วไหลต่างกัน

    def draw(self, img, x0, y0, w, h, t, bands, energy, kick, base_hue):
        # คอขวด = จำนวนครั้งเรียก ellipse → จำกัดจำนวนจุด + วาดวงเดียว (halo เฉพาะจุดสว่างจัด)
        d = ImageDraw.Draw(img, "RGBA")
        L, nb = self.lanes, len(bands)
        amp = []
        for li in range(L):
            seg = bands[li * nb // L:(li + 1) * nb // L]
            m = float(seg.mean()) if len(seg) else 0.0
            amp.append((h / L) * 0.34 * (0.25 + 1.1 * m + 0.5 * energy))
        spread = 20.0 + 62.0 * energy
        for i in range(self.n):
            li = int(self.lane[i])
            fx = (self.fx0[i] + t * 0.05 * self.spd[i]) % 1.0
            b = 0.45 + 0.55 * math.sin(t * 2.0 + self.bph[i])
            b = min(1.0, max(0.0, b) + kick * 0.4)
            if b < 0.12:                                   # ข้ามจุดจาง (ลดจำนวนวาด)
                continue
            px = x0 + fx * w
            wave = (math.sin(fx * 9.4 + t * (0.7 + 0.16 * li) + li * 1.7) * 0.6
                    + math.sin(fx * 4.1 - t * 0.9 + li) * 0.4)
            py = y0 + h * (li + 0.5) / L + wave * amp[li] + self.perp[i] * spread
            sz = self.size[i] * (0.7 + 0.9 * b)            # ใหญ่ขึ้นเล็กน้อยให้ดูฟุ้ง
            col = _hsv(base_hue + fx * WAVE_SPAN, 0.78, 0.55 + 0.45 * b)
            if b > 0.6:                                    # halo เฉพาะจุดสว่างจัด (น้อยครั้ง)
                d.ellipse([px - sz * 2.3, py - sz * 2.3, px + sz * 2.3, py + sz * 2.3],
                          fill=col + (int(50 * b),))
            d.ellipse([px - sz, py - sz, px + sz, py + sz], fill=col + (int(235 * b),))


WAVE_HUE = 186.0            # พาเลตนีออนอ้างอิง: cyan(186) → น้ำเงิน → ม่วง → ชมพู(336)
WAVE_SPAN = 150.0           # ความกว้าง gradient (องศา)
_PFIELD = None
_WAVE_DARK = None


def wave_base_hue(accent_hue):
    """คืน base hue ของ arc สวย ที่ 'เอียงไปหาสีปก' บางส่วน — ธีมตามปกแต่ไม่หลุดโซนหม่น
    (arc นีออน center ~261° หมุนเข้าหา accent 55% แล้วยังกวาด +WAVE_SPAN ต่อ)"""
    center = WAVE_HUE + WAVE_SPAN / 2.0           # ~261
    d = ((accent_hue - center + 180) % 360) - 180  # ผลต่างสั้นสุด (มีเครื่องหมาย)
    return WAVE_HUE + d * 0.55


_PWAVE_COL: dict = {}


def _pwave_colmap(w2, base_hue):
    """colormap ไล่ hue ตามคอลัมน์ (w2,3) สำหรับ particle raster — cache ต่อ (w2,hue)"""
    key = (w2, round(base_hue))
    if key not in _PWAVE_COL:
        xs = np.linspace(0.0, 1.0, w2)
        _PWAVE_COL[key] = np.array([_hsv(base_hue + f * WAVE_SPAN, 0.78, 0.98) for f in xs],
                                   dtype=np.uint8)
    return _PWAVE_COL[key]


def particle_field():
    global _PFIELD
    if _PFIELD is None:
        _PFIELD = ParticleField()
    return _PFIELD


def wave_darken(w, h):
    """overlay นาวีเข้ม ไล่จางบน→เข้มล่าง (ให้นีออนพุ่งแบบภาพอ้างอิง) — cache"""
    global _WAVE_DARK
    if _WAVE_DARK is None:
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[..., 0], arr[..., 1], arr[..., 2] = 8, 10, 26
        arr[..., 3] = np.linspace(70, 185, h).astype(np.uint8)[:, None]
        _WAVE_DARK = Image.fromarray(arr, "RGBA")
    return _WAVE_DARK


# ── ดอตกริดสเปกตรัม (LED matrix — คอลัมน์พิกเซลสี่เหลี่ยมซ้อน ไล่สีรุ้ง) ──────
def draw_classic_bars(d, x0, y0, w, h, bands, peaks, base_hue):
    """แท่งคลาสสิก: ยืนบนฐาน + เงาสะท้อนจาง ๆ ใต้ฐาน + peak cap (region-based ใช้ได้ทุกแนว)"""
    n = len(bands)
    gap = max(2, int(w / n * 0.18))
    bw = (w - gap * (n - 1)) / n
    base = y0 + h * 0.72                          # ฐาน (เว้นล่าง ~28% ให้เงาสะท้อน)
    barmax = base - y0
    refl_max = int(h * 0.26)
    for i in range(n):
        mag = float(bands[i])
        bh = int(barmax * mag)
        bx0 = x0 + i * (bw + gap)
        bx1 = bx0 + bw
        col = band_color(i / max(1, n - 1), mag, base_hue)
        d.rounded_rectangle([bx0, base - bh, bx1, base], radius=int(bw / 2), fill=col + (235,))
        rh = min(int(bh * 0.5), refl_max)         # เงาสะท้อน
        if rh > 1:
            d.rectangle([bx0, base + 2, bx1, base + 2 + rh], fill=col + (45,))
        if peaks is not None:
            ph = int(barmax * float(peaks[i]))
            if ph > 2:
                cy = base - ph
                d.rounded_rectangle([bx0, cy - 4, bx1, cy], radius=2,
                                    fill=_lerp(col, (255, 255, 255), 0.6) + (255,))


def _rebin(a, n):
    """เฉลี่ย array ให้เหลือ n ช่อง (60 bands → n คอลัมน์)"""
    a = np.asarray(a, dtype=np.float32)
    if len(a) == n:
        return a
    idx = np.linspace(0, len(a), n + 1).astype(int)
    return np.array([a[idx[i]:max(idx[i] + 1, idx[i + 1])].mean() for i in range(n)],
                    dtype=np.float32)


class PeakDrops:
    """หยดพีคแบบแรงโน้มถ่วง (สำหรับ inverted): **1 คอลัมน์ = 1 หยด** — spawn หยดใหม่
    ที่ปลายแท่งเฉพาะตอนหยดเก่าของคอลัมน์นั้นร่วงหายที่ก้นแล้ว (ไม่รัวหลายลูกพร้อมกัน)"""

    def __init__(self, g=2.2, cap=200):
        self.g, self.cap = g, cap
        self.drops = []              # [col, pos, vel]

    def update(self, cols, mag, dt=0.033):
        active = set(int(dp[0]) for dp in self.drops)   # คอลัมน์ที่ยังมีหยดร่วงอยู่
        for c in range(cols):
            m = float(mag[c])
            # spawn เฉพาะคอลัมน์ที่ 'ว่าง' (หยดเก่าร่วงหายแล้ว) + แท่งสูงพอ
            if m > 0.12 and c not in active and len(self.drops) < self.cap:
                self.drops.append([c, m, 0.0])
        alive = []
        for dp in self.drops:
            dp[2] += self.g * dt      # เร่งความเร็ว
            dp[1] += dp[2] * dt
            if dp[1] < 1.0:
                alive.append(dp)
        self.drops = alive
        return self.drops


_PEAKDROPS = None


def peak_drops():
    global _PEAKDROPS
    if _PEAKDROPS is None:
        _PEAKDROPS = PeakDrops()
    return _PEAKDROPS


def draw_dot_matrix(d, x0, y0, w, h, bands, peaks, base_hue, cols=18, invert=False):
    """สเปกตรัมแบบ LED matrix: แต่ละคอลัมน์ = ย่านความถี่, ก่อพิกเซลสี่เหลี่ยมตาม mag
    ไล่ hue ตามคอลัมน์ (รุ้ง-ตามปก) + peak cap (ขาว) ตกช้า
    invert=True → กลับหัว: แท่งห้อยจากบนลงล่าง, พีคร่วงลงล่าง
    (บน region เตี้ย (แนวนอน) จะลดความสูงแต่ละ dot ให้ได้แถวพอ → กลับหัวอ่านรู้เรื่อง)"""
    cell = w / cols
    pad = cell * 0.15
    cw = cell - 2 * pad
    ch = cell * 0.62                              # dot แบน (สูง ~0.62 ของกว้าง) ดูดีกว่าจัตุรัส
    if h / ch < 16:                               # region เตี้ย → แบนเพิ่มให้ได้ ≥16 แถว (normal เท่า invert)
        ch = h / 16
    vpad = min(pad, ch * 0.18)
    cht = ch - 2 * vpad                           # ความสูงตัว dot (ลบ padding แนวตั้ง)
    rows = max(1, int(h / ch))
    cm = _rebin(bands, cols)
    pk = _rebin(peaks, cols) if peaks is not None else cm
    drops = peak_drops().update(cols, cm) if invert else None  # หยดพีคหลายลูก (invert)
    for c in range(cols):
        lit = int(round(float(cm[c]) * rows))
        hue = base_hue + (c / max(1, cols - 1)) * 210.0
        cx = x0 + c * cell + pad
        for r in range(lit):
            cyt = (y0 + r * ch + vpad) if invert else (y0 + h - (r + 1) * ch + vpad)
            v = 0.5 + 0.5 * (r / rows)
            d.rectangle([cx, cyt, cx + cw, cyt + cht], fill=_hsv(hue, 0.85, v) + (240,))
        if not invert:                                # peak-hold ปกติ (เด้งขึ้น ตกช้า)
            pr = int(round(float(pk[c]) * rows))
            if 0 < pr <= rows:
                cyt = y0 + h - pr * ch + vpad
                cap = _lerp(_hsv(hue, 0.85, 1.0), (255, 255, 255), 0.55)
                d.rectangle([cx, cyt, cx + cw, cyt + cht], fill=cap + (255,))
    if invert:                                        # หยดพีคร่วงลง — วาดเฉพาะตอนพ้นปลายแท่ง (ไม่ทับแท่ง)
        for col, pos, _v in drops:
            if pos <= float(cm[col]) + 0.5 / rows:    # ยังอยู่ในแท่ง → ข้าม (ดันให้โผล่ใต้แท่ง)
                continue
            pr = int(round(pos * rows))
            if 0 < pr <= rows:
                hue = base_hue + (col / max(1, cols - 1)) * 210.0
                cx = x0 + col * cell + pad
                cyt = y0 + (pr - 1) * ch + vpad
                cap = _lerp(_hsv(hue, 0.85, 1.0), (255, 255, 255), 0.55)
                d.rectangle([cx, cyt, cx + cw, cyt + cht], fill=cap + (255,))


# ── waveform มิเรอร์ (เส้นบางยื่นซ้าย-ขวารอบเส้นกลางเรืองแสง — สไตล์ภาพอ้างอิง) ──
_BAR_NBARS = 100
_BAR_NOISE = np.random.default_rng(11).uniform(0.5, 1.0, _BAR_NBARS).astype(np.float32)


def _sample(a, f):
    """สุ่มค่าจาก array ที่ตำแหน่ง f (0..1) แบบ interpolate"""
    x = f * (len(a) - 1)
    i = int(x)
    if i + 1 < len(a):
        return float(a[i] * (1 - (x - i)) + a[i + 1] * (x - i))
    return float(a[i])


def draw_mirror_bars(d, x0, y0, w, h, bands, base_hue, energy, orient="v"):
    """waveform เส้นบางมิเรอร์รอบเส้นกลางที่เรืองแสง — ความยาว=สเปกตรัม×noise (สไปก์)
    orient 'v'=เส้นกลางตั้ง บาร์ยื่นซ้าย-ขวา (แนวตั้ง) · 'h'=เส้นกลางนอน บาร์ตั้งขึ้น-ลง (แนวนอน)"""
    n = _BAR_NBARS
    tint = _hsv(base_hue + 75.0, 0.35, 1.0)              # ขาวอมสีของเส้นกลาง
    if orient == "h":
        cy = y0 + h / 2
        step = w / n
        bt = max(1.0, step * 0.5)
        maxlen = h * 0.46
        for i in range(n):
            x = x0 + (i + 0.5) * step
            f = i / (n - 1)
            mag = _sample(bands, f) * _BAR_NOISE[i]
            ln = maxlen * (0.04 + min(1.0, mag) * (0.55 + 0.45 * energy))
            col = _hsv(base_hue + f * 150.0, 0.85, 0.55 + 0.45 * min(1.0, mag))
            d.rectangle([x - bt / 2, cy - ln, x + bt / 2, cy + ln], fill=col + (235,))
        for hw, a in ((9, 40), (4, 110), (1.5, 235)):    # เส้นกลางนอนเรืองแสง
            d.rectangle([x0, cy - hw, x0 + w, cy + hw], fill=tint + (a,))
    else:
        cx = x0 + w / 2
        step = h / n
        bt = max(1.0, step * 0.5)
        maxlen = w * 0.46
        for i in range(n):
            y = y0 + (i + 0.5) * step
            f = i / (n - 1)
            mag = _sample(bands, f) * _BAR_NOISE[i]
            ln = maxlen * (0.04 + min(1.0, mag) * (0.55 + 0.45 * energy))
            col = _hsv(base_hue + f * 150.0, 0.85, 0.55 + 0.45 * min(1.0, mag))
            d.rectangle([cx - ln, y - bt / 2, cx + ln, y + bt / 2], fill=col + (235,))
        for hw, a in ((9, 40), (4, 110), (1.5, 235)):    # เส้นกลางตั้งเรืองแสง
            d.rectangle([cx - hw, y0, cx + hw, y0 + h], fill=tint + (a,))


# ── ribbon wave (คลื่นริบบิ้นโปร่งแสงซ้อนกัน gradient — สไตล์ภาพอ้างอิง) ──────────
_RIBBON_G: dict = {}


def _ribbon_grad(n, hue):
    """gradient สีของริบบิ้น (n ค่าตามแกน amplitude) — cache ต่อ (n,hue)"""
    key = (n, round(hue))
    if key not in _RIBBON_G:
        vs = np.linspace(0.92, 0.58, n)
        _RIBBON_G[key] = np.array([_hsv(hue, 0.62, float(v)) for v in vs], dtype=np.uint8)
    return _RIBBON_G[key]


def draw_ribbon_wave(img, x0, y0, w, h, t, bands, energy, base_hue, orient="h", ribs=4):
    """คลื่นริบบิ้นโปร่งแสงซ้อนกัน (สมมาตรรอบเส้นกลาง) — overlap แล้วสีผสมกันเนียน
    เรนเดอร์ที่ความละเอียดต่ำ (SC) แล้วขยาย — ริบบิ้นเนียน upscale ไม่เสียรูป (เร็วกว่า ~6x)
    orient 'h'=เส้นกลางนอน (แนวนอน, เหมือนภาพ) · 'v'=เส้นกลางตั้ง (แนวตั้ง)"""
    SC = 0.32
    w2, h2 = max(6, int(w * SC)), max(6, int(h * SC))
    if orient == "h":
        flow, amp_n, cc = w2, h2, h2 / 2.0
    else:
        flow, amp_n, cc = h2, w2, w2 / 2.0
    fs = np.linspace(0.0, 1.0, flow, dtype=np.float32)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * fs)) ** 0.55            # กลางสูง ปลายเรียว
    bandv = np.interp(fs, np.linspace(0, 1, len(bands)), np.asarray(bands, np.float32))
    perp = np.abs(np.arange(amp_n, dtype=np.float32) - cc)
    maxamp = amp_n * 0.46
    acc = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
    for r in range(ribs):
        detail = 0.55 + 0.45 * np.sin(fs * (5 + 2 * r) * np.pi + t * (0.5 + 0.2 * r) + r * 1.7)
        H = maxamp * (0.12 + 0.88 * window) * detail * (0.32 + 0.85 * bandv + 0.5 * energy)
        H = np.clip(H, 2.0, amp_n * 0.5).astype(np.float32)
        A = np.clip((H[None, :] - perp[:, None]) / 5.0, 0.0, 1.0) * 0.42   # (amp_n, flow)
        hue = base_hue + (r / max(1, ribs - 1)) * 110.0
        grad = _ribbon_grad(amp_n, hue)
        rgba = np.dstack([np.broadcast_to(grad[:, None, :], (amp_n, flow, 3)),
                          (A * 255).astype(np.uint8)])
        if orient == "v":
            rgba = np.transpose(rgba, (1, 0, 2))         # (amp_n,flow) → (h2,w2)
        acc = Image.alpha_composite(acc, Image.fromarray(rgba, "RGBA"))
    acc = acc.resize((w, h), Image.BILINEAR)
    img.paste(acc, (x0, y0), acc)


def draw_lyrics(img, d, x0, y0, w, h, lines, pos, accent):
    """เนื้อเพลงคาราโอเกะ: บรรทัดปัจจุบัน (accent, ใหญ่) กึ่งกลาง, ข้างเคียงจางลง
    เลื่อน (scroll) แบบ float + ease → บรรทัดไหลขึ้นเนียน ไม่โดดทีละบรรทัด"""
    cxc = x0 + w / 2
    big = int(min(54, max(22, w * 0.052 + h * 0.02)))   # อิงความกว้างเป็นหลัก (กันตัดคำ)
    small = int(big * 0.66)
    if not lines:
        d.text((cxc, y0 + h / 2), "♪  ไม่มีเนื้อเพลงซิงค์ (LRCLIB)",
               font=font(small), fill=C_MUTE, anchor="mm")
        return
    idx = 0
    for i, (tt, _) in enumerate(lines):
        if tt <= pos:
            idx = i
        else:
            break
    # scroll เป็น float: ตอนเปลี่ยนบรรทัด ค่อย ๆ ไหลจาก idx-1 → idx ภายใน TRANS วิ (ease-out)
    TRANS = 0.42
    t_cur = lines[idx][0]
    seg = (lines[idx + 1][0] - t_cur) if idx + 1 < len(lines) else 4.0
    frac = min(1.0, max(0.0, (pos - t_cur) / min(TRANS, max(0.2, seg))))
    scroll = (idx - 1) + (1.0 - (1.0 - frac) * (1.0 - frac))   # ease-out quad
    center_j = int(scroll + 0.5)                               # บรรทัดที่ถือว่า "ปัจจุบัน"
    lh = int(big * 1.5)
    n = int((h / 2) // lh) + 2
    cy = y0 + h / 2
    for j in range(idx - n, idx + n + 1):
        if j < 0 or j >= len(lines):
            continue
        y = cy + (j - scroll) * lh
        if y < y0 - lh or y > y0 + h + lh:
            continue
        txt = lines[j][1] or "♪"
        if j == center_j:
            d.text((cxc, y), _fit_text(d, txt, font(big), w - 16),
                   font=font(big), fill=accent, anchor="mm")
        else:
            fade = max(0.28, 1.0 - abs(j - scroll) * 0.30)
            col = tuple(int(c * fade) for c in (210, 210, 220))
            d.text((cxc, y), _fit_text(d, txt, font(small, bold=False), w - 16),
                   font=font(small, bold=False), fill=col, anchor="mm")


def render_portrait(snap, bands, audio_active, t, peaks=None, mascot=None):
    """วาด 1 เฟรมแนวตั้ง 462x1920 (mount จอตั้ง):
    ปกบน → ชื่อเพลง/ศิลปิน → progress → visualizer / เนื้อเพลง (--lyrics)"""
    W, H = PANEL_H, PANEL_W          # 462 กว้าง x 1920 สูง
    MG = 26
    ART_P = W - 2 * MG               # 410
    ax, ay = MG, 40
    title_y, meta_y, prog_y = 498, 574, 648

    art_assets = snap.get("_assets")
    have = snap["have"] and (snap["title"] or snap["artist"])
    if art_assets:
        img = art_assets[4].copy()               # bg แนวตั้ง
        accent = art_assets[2]
        base_hue = art_assets[3] - 40.0
    else:
        img = fallback_bg_portrait().copy()
        accent = C_ACCENT
        base_hue = 175.0
    d = ImageDraw.Draw(img, "RGBA")

    # ── ปกอัลบั้ม บนสุด (glow ขอบเต้นตามบีตก่อน; มัสคอตอยู่กลางจอ) ──
    force_m = snap.get("_force_mascot")
    if snap.get("_glow", True):
        draw_art_glow(img, ax, ay, ART_P, 50, accent, glow_strength(mascot))
    if art_assets and not force_m:
        cover = art_assets[0].resize((ART_P, ART_P), Image.LANCZOS)
        img.paste(cover, (ax, ay), cover)
        if snap.get("_sparkle", True):
            draw_sparkles(d, ax, ay, ART_P, ART_P, t, getattr(mascot, "kick", 0.0))
    else:
        d.rounded_rectangle([ax, ay, ax + ART_P, ay + ART_P], radius=28, fill=(22, 22, 30))
        d.text((ax + ART_P / 2, ay + ART_P / 2), "♪", font=font(200),
               fill=C_MUTE, anchor="mm")

    # ── ชื่อเพลง/ศิลปิน (จัดกลาง, marquee ถ้ายาว) ──
    if have:
        title = snap["title"]
        meta = " · ".join(x for x in (snap["artist"], snap["album"]) if x)
        draw_marquee(img, d, MG, title_y, W - 2 * MG, title, font(42),
                     C_INK, t, speed=60, center=True)
        if meta:
            draw_marquee(img, d, MG, meta_y, W - 2 * MG, meta, font(26, bold=False),
                         accent, t, speed=48, center=True)
    else:
        d.text((W / 2, title_y), "ไม่มีเพลงเล่นอยู่", font=font(42),
               fill=C_MUTE, anchor="mt")

    # ── progress + เวลา (กลาง) ──
    pos, dur = snap["pos"], snap["dur"]
    if have and dur > 0:
        frac = max(0.0, min(1.0, pos / dur))
        d.rounded_rectangle([MG, prog_y, W - MG, prog_y + 10], radius=5, fill=C_TRACK)
        fx = MG + int((W - 2 * MG) * frac)
        if fx > MG + 10:
            d.rounded_rectangle([MG, prog_y, fx, prog_y + 10], radius=5, fill=accent)
        d.ellipse([fx - 8, prog_y - 3, fx + 8, prog_y + 13], fill=C_INK)
        d.text((W / 2, prog_y + 26), f"{fmt_time(pos)} / {fmt_time(dur)}",
               font=font(24, bold=False), fill=C_MUTE, anchor="mt")

    # ── visualizer ล่าง (บนนาวีเข้มให้สีพุ่ง) — เลือก particle wave / dot matrix ──
    wy, wh = 712, 1180
    img.paste(wave_darken(W, wh), (0, wy), wave_darken(W, wh))
    a_hue = art_assets[3] if art_assets else 210.0              # ธีมสีตามปก (fallback เย็น)
    base = wave_base_hue(a_hue)
    viz = snap.get("_viz") or "wave"
    e = getattr(mascot, "energy", 0.28)
    kk = getattr(mascot, "kick", 0.0)
    if snap.get("_lyrics_mode"):
        draw_lyrics(img, d, MG, wy, W - 2 * MG, wh, snap.get("lyrics"), snap["pos"], accent)
    elif viz == "dots":
        draw_dot_matrix(d, 0, wy, W, wh, bands, peaks, base, invert=snap.get("_invert", False))
    elif viz == "bars":
        draw_mirror_bars(d, 0, wy, W, wh, bands, base, e, orient="v")
    elif viz == "ribbon":
        draw_ribbon_wave(img, 0, wy, W, wh, t, bands, e, base, orient="v")
    elif viz == "classic":
        draw_classic_bars(d, MG, wy, W - 2 * MG, wh, bands, peaks, base)
    else:
        particle_field().draw(img, 0, wy, W, wh, t, bands, e, kk, base)
    return img


# ══════════════════════════════════════════════════════════════════════════
#  ส่งเข้าจอ
# ══════════════════════════════════════════════════════════════════════════
def to_wire(canvas, panel_w, panel_h, angle):
    out = canvas.rotate(-angle, expand=True)
    if out.size != (panel_w, panel_h):
        out = out.resize((panel_w, panel_h), Image.LANCZOS)
    return out


def to_jpeg(img, quality):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _demo_media(t):
    return {
        "title": "Where Is the Love", "artist": "Olivia Ong",
        "album": "a girl meets bossanova", "app": "Apple Music",
        "playing": True, "have": True,
        "pos": 40 + (t % 180), "dur": 225, "art": None, "key": ("demo", "demo"),
    }


def main():
    ap = argparse.ArgumentParser(description="Now-Playing + Visualizer บนจอ Trofeo 9.16")
    ap.add_argument("--demo", action="store_true", help="เพลง/สเปกตรัมจำลอง")
    ap.add_argument("--portrait", action="store_true", help="วาดแนวตั้ง 462x1920 (mount จอตั้ง)")
    ap.add_argument("--flip", action="store_true", help="พลิกด้าน (แนวตั้งกลับหัว)")
    ap.add_argument("--mascot", action="store_true",
                    help="โชว์ ClaudePix เต้นตามบีตแทนปก (landscape); แนวตั้งโชว์อยู่แล้ว")
    ap.add_argument("--no-sparkle", dest="sparkle", action="store_false",
                    help="ปิดประกายวิบวับบนปก")
    ap.add_argument("--no-glow", dest="glow", action="store_false",
                    help="ปิดขอบปกเรืองแสงเต้นตามบีต")
    ap.add_argument("--viz", choices=["wave", "dots", "bars", "ribbon", "classic", "random"],
                    default=None,
                    help="visualizer: wave=particle, dots=LED matrix, bars=waveform มิเรอร์, "
                         "ribbon=คลื่นริบบิ้นโปร่งแสง, classic=แท่งมีเงาสะท้อน, random=สุ่มสลับ "
                         "(แนวตั้ง default=wave, แนวนอน default=classic)")
    ap.add_argument("--invert", action="store_true",
                    help="สเปกตรัม dots กลับหัว: แท่งห้อยจากบน พีคร่วงลงล่าง")
    ap.add_argument("--full", action="store_true",
                    help="แนวนอน: viz เต็มจอ 1920x462 + now-playing แถบเล็กล่าง (ต้องมี --viz)")
    ap.add_argument("--lyrics", action="store_true",
                    help="โหมดเนื้อเพลงคาราโอเกะ (ดึงจาก LRCLIB ซิงค์เวลา; ไม่มีเนื้อ→โชว์ viz)")
    ap.add_argument("--no-audio", action="store_true", help="ไม่ capture เสียง (now-playing อย่างเดียว)")
    ap.add_argument("--gain", type=float, default=1.0,
                    help="ความไวเสียง (บน AGC = ปรับ target; --no-agc = gain ตายตัว; default 1.0)")
    ap.add_argument("--no-agc", dest="agc", action="store_false",
                    help="ปิด auto-gain (ใช้ --gain ตายตัวแทน)")
    ap.add_argument("--rotate", type=int, default=None, choices=[0, 90, 180, 270],
                    help="บังคับมุมหมุน wire เอง (ถ้าจอกลับหัว/ตะแคง)")
    ap.add_argument("--quality", type=int, default=86, help="คุณภาพ JPEG 1-95")
    ap.add_argument("--fps", type=float, default=30.0, help="เฟรมต่อวินาที")
    ap.add_argument("--preview", metavar="PNG", help="เรนเดอร์ 1 เฟรมเป็น PNG แล้วออก")
    ap.add_argument("--art", metavar="IMG", help="ใช้รูปนี้เป็นปก (ไว้เทสต์ preview/demo)")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x5408)
    args = ap.parse_args()
    run(args)


def run(args, stop_evt=None):
    """รัน render loop — แยกจาก main() ให้ tray app เรียกได้ + เปลี่ยนโหมด/แนวสด (อ่าน args ทุกเฟรม)"""
    state = MediaState()
    spec = Spectrum()
    if stop_evt is None:
        stop_evt = threading.Event()
    art_cache = {"key": None, "assets": None}

    def build_snapshot(t):
        if args.demo:
            snap = _demo_media(t)
        else:
            snap = state.snapshot()
        # เตรียม art assets (blur bg + ปกมุมมน) แคชตาม key
        art = snap.get("art")
        key = snap.get("key")
        if art is not None and key != art_cache["key"]:
            try:
                art_cache["assets"] = make_art_assets(art)
            except Exception:
                art_cache["assets"] = None
            art_cache["key"] = key
        elif art is None:
            art_cache["assets"] = None
            art_cache["key"] = key
        snap["_assets"] = art_cache["assets"]
        if args.mascot:
            snap["_force_mascot"] = True
        snap["_sparkle"] = args.sparkle
        snap["_glow"] = args.glow
        snap["_viz"] = args.viz
        snap["_invert"] = args.invert
        snap["_full"] = args.full
        snap["_lyrics_mode"] = args.lyrics
        return snap

    render_fn = render_portrait if args.portrait else render

    # ── โหมด preview: เฟรมเดียวออกไฟล์ ──
    if args.preview:
        t = 0.7
        snap = _demo_media(t)
        snap["_assets"] = None
        if args.art:
            try:
                snap["_assets"] = make_art_assets(Image.open(args.art).convert("RGB"))
            except Exception as e:
                log("โหลด --art ไม่ได้:", e)
        if args.mascot:
            snap["_force_mascot"] = True
        snap["_sparkle"] = args.sparkle
        snap["_glow"] = args.glow
        snap["_viz"] = args.viz
        snap["_invert"] = args.invert
        snap["_full"] = args.full
        snap["_lyrics_mode"] = args.lyrics
        if args.lyrics:                              # เนื้อเพลง demo (ไว้ดูหน้าตา)
            snap["lyrics"] = [(34, "Look at the stars"), (37, "Look how they shine for you"),
                              (40, "And everything you do"), (43, "Yeah, they were all yellow"),
                              (46, "I came along"), (49, "I wrote a song for you"),
                              (52, "And all the things you do")]
        bands = demo_bands(spec.n, t)
        peaks = np.clip(bands + 0.08, 0.0, 0.92)     # ยกยอดให้เห็น cap ลอยเหนือแท่ง
        manim = MascotAnim()
        for _ in range(30):                          # settle ให้ได้ท่ากลาง ๆ
            manim.step(0.6, 0.35, 1.0 / 30)
        img = render_fn(snap, bands, True, t=t, peaks=peaks, mascot=manim)
        img.save(args.preview)
        log(f"เขียน preview: {args.preview} ({img.width}x{img.height})")
        return

    # ── start threads ──
    want_lyrics = args.lyrics or getattr(args, "always_lyrics", False)
    if not args.demo:
        threading.Thread(target=smtc_poller, args=(state, stop_evt, want_lyrics),
                         daemon=True).start()
        log("เริ่ม SMTC poller (now-playing)" + (" + เนื้อเพลง (LRCLIB)" if want_lyrics else ""))
    if not args.demo and not args.no_audio:
        threading.Thread(target=audio_capture,
                         args=(spec, stop_evt, 48000, 2048, args.gain, args.agc),
                         daemon=True).start()

    # ── เปิดจอ (รอจนกว่าจะเสียบ/เปิดได้ — เผื่อ tray เปิดตอนจอยังไม่พร้อม) ──
    from trofeo import TrofeoLCD
    lcd = TrofeoLCD(pid=args.pid)
    info = None
    while info is None and not stop_evt.is_set():
        try:
            log("เปิด USB + handshake ...")
            info = lcd.open()
        except Exception as e:
            log("ยังเปิดจอไม่ได้ (เสียบจอ / ปิด TRCC?):", e, "— รอ 3s")
            stop_evt.wait(3.0)
    if info is None:
        return
    base = info["encode_base"]
    if args.rotate is not None:
        angle = args.rotate
    elif args.portrait:
        angle = (base + (270 if args.flip else 90)) % 360   # 462x1920 → หมุน +90
    else:
        angle = base
    log(f"เชื่อมต่อ {info['width']}x{info['height']} encode_base={base} "
        f"→ {'แนวตั้ง' if args.portrait else 'แนวนอน'} wire_angle={angle}")

    t0 = time.time()
    period = 1.0 / max(1.0, args.fps)
    peaks = np.zeros(spec.n, dtype=np.float32)
    peak_fall = 0.5 / max(1.0, args.fps)      # ยอดตกจาก 1.0 ถึงพื้นใน ~2 วิ
    beat = Beat()
    manim = MascotAnim()
    prev_t = 0.0
    # โหมด --viz random: สุ่มสลับสไตล์ทุก ~8-14 วิ (มี dots-inv = กลับหัว gravity-drops)
    RVIZ = ["wave", "dots", "dots-inv", "bars", "ribbon", "classic"]
    rnd_viz = random.choice(RVIZ)
    rnd_next = 10.0
    if args.viz == "random":
        log(f"โหมด random — สุ่มสลับ visualizer (เริ่ม {rnd_viz})")
    gc.disable()                          # กัน GC pause กลางลูป (กระตุก) — เก็บเองเป็นระยะ
    gc_next = 20.0
    log(f"เริ่มแสดงผล {args.fps:.0f}fps — Ctrl+C ออก")
    try:
        while not stop_evt.is_set():
            loop_t = time.time()
            t = loop_t - t0
            dt = t - prev_t
            prev_t = t
            # เลือก render_fn + มุมหมุน จาก args สด ๆ (เปลี่ยนแนวได้ live จาก tray)
            render_fn = render_portrait if args.portrait else render
            if args.rotate is not None:
                angle = args.rotate
            elif args.portrait:
                angle = (base + (270 if args.flip else 90)) % 360
            else:
                angle = base
            snap = build_snapshot(t)
            if args.demo:
                bands = demo_bands(spec.n, t)
                active = True
            else:
                bands, active = spec.get()
            peaks = np.maximum(bands, peaks - peak_fall)   # เด้งขึ้นทันที ตกช้า
            energy, kick = beat.update(bands)              # บีตจากเบส
            manim.step(energy, kick, dt)                   # → เต้นลื่น (สะสมเฟส+smooth)
            if args.viz == "random":                       # สุ่มสลับสไตล์
                if t >= rnd_next:
                    rnd_viz = random.choice([v for v in RVIZ if v != rnd_viz])
                    rnd_next = t + random.uniform(8.0, 14.0)
                    log(f"random → {rnd_viz}")
                if rnd_viz == "dots-inv":                   # กลับหัว gravity-drops
                    snap["_viz"], snap["_invert"] = "dots", True
                else:
                    snap["_viz"] = rnd_viz
            canvas = render_fn(snap, bands, active, t, peaks, mascot=manim)
            wire = to_wire(canvas, info["width"], info["height"], angle)
            try:
                lcd.send_jpeg(to_jpeg(wire, args.quality))
            except Exception as e:            # USB glitch (I/O error ฯลฯ) → reconnect ไม่ crash
                log("USB error:", type(e).__name__, e, "— reconnect ...")
                try:
                    lcd.close()
                except Exception:
                    pass
                stop_evt.wait(0.6)
                try:
                    info = lcd.open()
                    log("reconnect สำเร็จ")
                except Exception as e2:
                    log("reconnect ล้ม:", e2, "— รอ 2s")
                    stop_evt.wait(2.0)
                continue
            if t >= gc_next:                  # เก็บขยะเป็นระยะ (ครั้งเดียว/20s) แทน GC อัตโนมัติ
                gc.collect()
                gc_next = t + 20.0
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
