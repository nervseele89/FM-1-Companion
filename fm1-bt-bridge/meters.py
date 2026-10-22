"""Audio meters for the bridge's OLED: the FM-1's sound as level bars, VU needles, a spectrum or a scope.

From the status screen, joystick left opens the meters; up/down picks one (remembered); right goes back;
press starts and stops a recording (Recorder: a 48 kHz WAV in the library's recordings folder).
While the meter screen is open, LevelReader records the FM-1's input at 16 kHz with pw-cat (a second,
read-only stream next to the headphone loopback). The sums run in numpy once per frame: plain Python
manages ~2 us a sample on a Zero W, too slow for 32,000 samples a second. numpy is imported the first time
the meters open (it takes a few seconds on a Zero W), so it doesn't slow the bridge's start-up.
"""
import math
import shutil
import subprocess
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw

import library

METERS = ("Bars", "VU", "Spectrum", "Scope", "Tape")
RATE, BLOCK = 16000, 512                # frames per second and per read (32 ms)
FULL = 32768.0
FLOOR = -60.0                           # dBFS shown as nothing
VU_REF = -18.0                          # dBFS (RMS) that reads 0 VU, the usual digital alignment
SPECTRUM_N = 512                        # frames per spectrum: 31 Hz bins at 16 kHz
BAND_EDGES = (50, 7500)                 # Hz: 16 bands evenly spaced in pitch between these
MIN_FREE = 200 * 1024 * 1024            # bytes left on the SD card: recording stops below this
TAPE_SECONDS = 30 * 60                  # the Tape meter's reel: a full side every 30 minutes
REELS = (30, 98)                        # Tape: the reels' centres (x), at y = REEL_Y
REEL_Y, REEL_R, HUB_R = 31, 19, 5
_np, _np_state = None, "idle"           # idle, loading, ready or missing
_window = None


def load_numpy():
    global _np, _np_state
    try:
        import numpy as module
        _np, _np_state = module, "ready"
    except ImportError:
        _np_state = "missing"


def numpy():
    """numpy once it's imported, else None; the first call starts importing it in the background
    (7-8 s on a Zero W: the screen keeps drawing meanwhile)"""
    global _np_state
    if _np_state == "idle":
        _np_state = "loading"
        threading.Thread(target=load_numpy, daemon=True).start()
    return _np


def db(amplitude):
    return 20 * math.log10(amplitude / FULL) if amplitude > 0 else -120.0


def frames(data):
    """raw s16 little-endian stereo bytes as an (n, 2) numpy array"""
    return _np.frombuffer(data, dtype="<i2").reshape(-1, 2)


def levels(samples):
    """([peak L, peak R], [RMS L, RMS R]) of (n, 2) samples, as amplitudes (0..32768).
    Every numpy call costs ~0.3 ms on a Zero W, so: integer peaks, and RMS from every 4th sample."""
    if not len(samples):
        return [0.0, 0.0], [0.0, 0.0]
    peaks, rms = [], []
    for ch in (0, 1):
        x = samples[:, ch]
        peaks.append(float(max(int(x.max()), -int(x.min()))))
        y = x[::4].astype(_np.float32)
        rms.append(math.sqrt(float(_np.dot(y, y)) / len(y)))
    return peaks, rms


_BANDS = None


def band_bins():
    """first FFT bin of each band and the end of the last (for numpy's add.reduceat); at least one bin each"""
    global _BANDS
    if _BANDS is None:
        hz = RATE / SPECTRUM_N
        lo, hi = BAND_EDGES
        starts, last = [], 0
        for i in range(17):
            k = max(last + 1, int(round(lo * (hi / lo) ** (i / 16) / hz)))
            starts.append(k)
            last = k
        _BANDS = starts
    return _BANDS


def spectrum(samples):
    """dBFS of each of the 16 bands for the newest SPECTRUM_N frames (L and R summed)"""
    global _window
    np = _np
    if len(samples) < SPECTRUM_N:
        return [FLOOR] * 16
    if _window is None:
        _window = np.hanning(SPECTRUM_N).astype(np.float32)
    tail = samples[-SPECTRUM_N:]
    f = np.fft.rfft((tail[:, 0].astype(np.float32) + tail[:, 1]) * _window)
    power = f.real ** 2 + f.imag ** 2
    edges = band_bins()
    bands = np.add.reduceat(power[:edges[-1]], edges[:-1])
    scale = (2 * FULL * SPECTRUM_N / 4) ** 2      # a full-scale sine in both channels, Hann window
    return (10 * np.log10(np.maximum(bands, 1e-12) / scale)).tolist()


class LevelReader:
    """The FM-1's input through pw-cat, while the meter screen is open."""

    def __init__(self):
        self.proc, self.target, self.lock = None, None, threading.Lock()
        self.pending, self.recent, self.seq = bytearray(), b"", 0

    def start(self, target):
        self.stop()
        self.pending, self.recent = bytearray(), b""
        try:
            self.proc = subprocess.Popen(
                ["pw-cat", "--record", "--raw", "--target", target, "--rate", str(RATE), "--channels", "2",
                 "--format", "s16", "--latency", "32ms", "-P", "{ node.dont-reconnect = true }", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return
        self.target = target
        threading.Thread(target=self.read, args=(self.proc,), daemon=True).start()

    def read(self, proc):
        size = BLOCK * 4
        while True:
            data = proc.stdout.read(size)
            if len(data) < size:
                return
            with self.lock:
                self.pending += data
                del self.pending[:-RATE * 4]                    # at most a second if nobody takes it
                self.recent = (self.recent + data)[-SPECTRUM_N * 4:]
                self.seq += 1

    def take(self):
        """(samples since the last take, the newest SPECTRUM_N samples), each an (n, 2) array"""
        with self.lock:
            pending, recent = bytes(self.pending), self.recent
            self.pending.clear()
        return frames(pending), frames(recent)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.target = None, None


class Recorder:
    """pw-record writing the FM-1's input to a WAV file (48 kHz, 16-bit stereo), with PipeWire doing the
    writing: next to nothing for the Zero W's CPU. Stopping it (SIGTERM) finishes the WAV header."""

    def __init__(self):
        self.proc, self.path, self.started = None, None, 0.0

    def start(self, target, path):
        self.stop()
        try:
            self.proc = subprocess.Popen(
                ["pw-record", "--target", target, "--rate", "48000", "--channels", "2", "--format", "s16",
                 "-P", "{ node.dont-reconnect = true }", str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return False
        self.path, self.started = path, time.monotonic()
        return True

    def recording(self):
        return self.proc is not None and self.proc.poll() is None

    def seconds(self, now=None):
        return (now or time.monotonic()) - self.started if self.proc else 0.0

    def stop(self):
        """-> (the file, how long it ran in seconds), or (None, 0) if nothing was recording"""
        if not self.proc:
            return None, 0.0
        path, seconds = self.path, self.seconds()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        return path, seconds


def clock(seconds):
    """1:05 or 1:02:05"""
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MeterScreens:
    """The meter screen, mixed into bridge.App (uses its state, fonts, source and screen)."""

    def meter_init(self):
        self.levels = LevelReader()
        self.meter_bg = {}                    # style -> background image (header and scale), drawn once
        self.meter_restart = 0.0
        self.recorder = Recorder()
        self.rec_space_check = 0.0
        self.tape_done = 0.0                  # Tape: how far through the reel the last recording got (0..1)
        self.meter_reset()

    def meter_reset(self):
        self.m_time = None
        self.m_hold = [[FLOOR, 0.0], [FLOOR, 0.0]]      # bars: peak-hold dB and when it was set
        self.m_vu = [0.0, 0.0]                          # VU: needle position (linear amplitude)
        self.m_clip = [0.0, 0.0]                        # VU: when each channel last peaked near 0 dBFS
        self.m_bands = [FLOOR] * 16
        self.m_caps = [FLOOR] * 16
        self.m_reels = [0.0, 0.0]                       # Tape: the reels' angles (radians)

    def meter_index(self):
        return self.state.get("meter", 0) % len(METERS)

    def open_meters(self):
        self.screen = "meter"
        self.meter_reset()

    def meter_key(self, key, held):
        """True if the meter screen took the key"""
        if self.screen != "meter":
            return False
        if key in ("up", "down"):
            self.state["meter"] = (self.meter_index() + (1 if key == "down" else -1)) % len(METERS)
            self.save_settings()
            self.meter_reset()
        elif key == "right":
            self.screen = "status"
            self.levels.stop()
        elif key == "press" and not held:
            self.toggle_recording()
        return True

    # ---- recording

    def toggle_recording(self):
        if self.recorder.recording():
            _, seconds = self.recorder.stop()
            self.say(f"Saved: {clock(seconds)} recorded")
            return
        if not self.source:
            self.say("FM-1 not found")
            return
        folder = library.folder("recordings")
        if shutil.disk_usage(folder).free < MIN_FREE:
            self.say("SD card full")
            return
        path = folder / f"{datetime.now():%Y-%m-%d_%H%M%S}_fm1.wav"
        if not self.recorder.start(self.source["name"], path):
            self.say("Can't record (pw-record?)")

    def recording_tick(self, now):
        """Stops a recording whose pw-record died (the FM-1 went away) or that would fill the SD card;
        -> whole seconds recorded so far, or None"""
        if not self.recorder.proc:
            return None
        if not self.recorder.recording():
            _, seconds = self.recorder.stop()
            self.say(f"Recording stopped at {clock(seconds)}")
            return None
        if now >= self.rec_space_check:
            self.rec_space_check = now + 5
            try:
                full = shutil.disk_usage(self.recorder.path.parent).free < MIN_FREE
            except OSError:
                full = False
            if full:
                _, seconds = self.recorder.stop()
                self.say(f"SD card full: {clock(seconds)} saved")
                return None
        seconds = self.recorder.seconds(now)
        self.tape_done = seconds % TAPE_SECONDS / TAPE_SECONDS
        return int(seconds)

    def meter_view(self, now):
        """Part of App.view(): keeps the reader running while the meters show, and watches a recording;
        changes with each block, and each second of a recording."""
        rec = self.recording_tick(now)
        want = (self.screen == "meter" and self.mode != "saver" and self.source and numpy()
                and self.source["name"])
        if not want:
            if self.levels.proc:
                self.levels.stop()
            return (_np_state if self.screen == "meter" else None), rec      # redraw once numpy has loaded
        if (self.levels.target != want or not self.levels.running()) and now >= self.meter_restart:
            self.meter_restart = now + 2      # the FM-1 went away or pw-cat failed: try again in 2 s
            self.levels.start(want)
        return (self.meter_index(), self.levels.seq), rec

    # ---- drawing

    def meter_image(self, now):
        """The whole meter frame (render() puts message boxes on top)."""
        style = METERS[self.meter_index()]
        img = self.meter_background(style).copy()
        d = ImageDraw.Draw(img)
        problem = ("FM-1 not found" if not self.source else None if numpy() else
                   "Needs python3-numpy" if _np_state == "missing" else "Loading meters...")
        if problem:
            d.text(((128 - d.textlength(problem, font=self.f_body)) / 2, 30), problem, font=self.f_body, fill=1)
            return img
        pending, recent = self.levels.take()
        peak, rms = levels(pending)
        dt = min(0.5, now - self.m_time) if self.m_time else 0.1
        self.m_time = now
        if max(peak) > 100:                   # sound (above -50 dBFS): keep the screen on while it plays
            self.last_activity = now
            if self.mode == "dim":
                self.wake()
        {"Bars": self.draw_bars, "VU": self.draw_vu, "Spectrum": self.draw_spectrum,
         "Scope": self.draw_scope, "Tape": self.draw_tape}[style](d, now, dt, peak, rms, recent)
        if self.recorder.recording():         # REC and the time over the title, the dot blinking
            seconds = self.recorder.seconds(now)
            text = f"REC {clock(seconds)}"
            d.rectangle((0, 0, 12 + d.textlength(text, font=self.f_small), 9), fill=0)
            if int(seconds) % 2 == 0:
                d.ellipse((1, 2, 6, 7), fill=1)
            d.text((9, 0), text, font=self.f_small, fill=1)
        return img

    def meter_background(self, style):
        if style in self.meter_bg:
            return self.meter_bg[style]
        img = Image.new("1", (128, 64))
        d = ImageDraw.Draw(img)
        if style != "VU":                     # VU fills the screen; its header would cover the dials
            d.text((0, 0), f"{style} {self.meter_index() + 1}/{len(METERS)}", font=self.f_small, fill=1)
            d.text((128 - d.textlength("home >", font=self.f_small), 0), "home >", font=self.f_small, fill=1)
        if style == "Bars":
            d.text((0, 15), "L", font=self.f_small, fill=1)
            d.text((0, 31), "R", font=self.f_small, fill=1)
            for value in (-48, -36, -24, -12, -6, 0):
                x = bar_x(value)
                d.line((x, 45, x, 47), fill=1)
                label = str(value)
                w = d.textlength(label, font=self.f_small)
                d.text((min(127 - w, max(0, x - w / 2)), 50), label, font=self.f_small, fill=1)
        elif style == "VU":
            for cx, name, x in ((31, "L", 0), (96, "R", 122)):
                vu_scale(d, cx)
                d.text((x, 53), name, font=self.f_small, fill=1)
        elif style == "Scope":
            for x in range(0, 128, 4):
                d.point((x, 37), fill=1)
        elif style == "Tape":
            for cx in REELS:                  # flanges, guide rollers, the tape across the head
                d.ellipse((cx - REEL_R, REEL_Y - REEL_R, cx + REEL_R, REEL_Y + REEL_R), outline=1)
            for x in (5, 122):
                d.ellipse((x - 2, 54, x + 2, 58), outline=1)
            d.line((5, 58, 122, 58), fill=1)
            d.rectangle((57, 52, 70, 58), fill=1)                       # the record head
            d.ellipse((80, 54, 84, 58), fill=1)                         # capstan
        self.meter_bg[style] = img
        return img

    def draw_bars(self, d, now, dt, peak, rms, recent):
        for ch, y in ((0, 14), (1, 30)):
            level = max(FLOOR, db(peak[ch]))
            hold = self.m_hold[ch]
            if level >= hold[0] or now - hold[1] > 1.5:  # peak hold for 1.5 s, then it falls
                hold[:] = [level, now] if level >= hold[0] else [max(level, hold[0] - 30 * dt), hold[1]]
            for x in range(12, bar_x(level), 4):            # LED-style segments
                d.rectangle((x, y, x + 2, y + 10), fill=1)
            if hold[0] > -48:
                x = bar_x(hold[0])
                d.rectangle((x - 1, y, x, y + 10), fill=1)
            if hold[0] > -0.2:
                d.text((58, 0), "CLIP", font=self.f_small, fill=1)

    def draw_vu(self, d, now, dt, peak, rms, recent):
        k = 1 - math.exp(-dt / 0.3)           # VU ballistics: ~300 ms to rise and fall
        for ch, cx in ((0, 31), (1, 96)):
            target = rms[ch] / (FULL * 10 ** (VU_REF / 20))           # 1.0 = 0 VU
            self.m_vu[ch] += (target - self.m_vu[ch]) * k
            angle = vu_angle(self.m_vu[ch])
            d.line((cx + 16 * math.sin(angle), 80 - 16 * math.cos(angle),
                    cx + 60 * math.sin(angle), 80 - 60 * math.cos(angle)), fill=1, width=2)
            if peak[ch] > FULL * 0.89:        # within 1 dB of full scale
                self.m_clip[ch] = now
            if now - self.m_clip[ch] < 0.6:
                d.ellipse((cx + 24, 2, cx + 29, 7), fill=1)

    def draw_spectrum(self, d, now, dt, peak, rms, recent):
        if len(recent):
            fresh = spectrum(recent)
            self.m_bands = [max(f, b - 45 * dt) for f, b in zip(fresh, self.m_bands)]   # falls 45 dB/s
        for i, level in enumerate(self.m_bands):
            h = spectrum_height(level)
            self.m_caps[i] = max(level, self.m_caps[i] - 20 * dt)
            x = i * 8
            if h:
                d.rectangle((x, 63 - h, x + 6, 63), fill=1)
            cap = spectrum_height(self.m_caps[i])
            if cap > h + 1:
                d.line((x, 63 - cap, x + 6, 63 - cap), fill=1)

    def draw_scope(self, d, now, dt, peak, rms, recent):
        if len(recent) < 4:
            return
        np = numpy()
        mono = recent.astype(np.float32).mean(axis=1)
        rising = np.nonzero((mono[:-1] < 0) & (mono[1:] >= 0))[0]     # start on a rising zero: a steady trace
        start = int(rising[0]) + 1 if len(rising) and rising[0] < len(mono) // 2 else 0
        ys = (37 - mono[start:start + 256:2] * 26 / FULL).tolist()
        if len(ys) > 1:
            d.line(list(enumerate(ys)), fill=1)


    def draw_tape(self, d, now, dt, peak, rms, recent):
        """Two reels: the tape winds from left to right while recording (the reels empty and fill over
        TAPE_SECONDS), and the reels turn whenever sound plays; the head's level bars between them."""
        moving = self.recorder.recording() or max(peak) > 100
        done = self.tape_done
        packs = (math.sqrt(HUB_R ** 2 + (1 - done) * ((REEL_R - 1) ** 2 - HUB_R ** 2)),
                 math.sqrt(HUB_R ** 2 + done * ((REEL_R - 1) ** 2 - HUB_R ** 2)))
        for i, (cx, pack) in enumerate(zip(REELS, packs)):
            if moving:                        # constant tape speed: the smaller pack turns faster
                self.m_reels[i] += 40 * dt / max(pack, HUB_R + 1)
            a = self.m_reels[i]
            d.ellipse((cx - pack, REEL_Y - pack, cx + pack, REEL_Y + pack), fill=1)          # wound tape
            d.ellipse((cx - HUB_R, REEL_Y - HUB_R, cx + HUB_R, REEL_Y + HUB_R), fill=0, outline=1)
            for k in range(3):
                b = a + k * 2 * math.pi / 3
                d.line((cx + 1.5 * math.cos(b), REEL_Y + 1.5 * math.sin(b),
                        cx + HUB_R * math.cos(b), REEL_Y + HUB_R * math.sin(b)), fill=1)       # spokes
                h = b + math.pi / 3
                hx, hy = cx + 12 * math.cos(h), REEL_Y + 12 * math.sin(h)
                d.ellipse((hx - 2.5, hy - 2.5, hx + 2.5, hy + 2.5), fill=0, outline=1)       # flange holes
            side = -1 if i == 0 else 1        # the tape leaves each pack at its outer bottom to a guide
            d.line((cx + side * pack * 0.7, REEL_Y + pack * 0.7, 5 if i == 0 else 122, 56), fill=1)
        for ch, x in ((0, 57), (1, 66)):      # the head's level, L and R
            h = int(round(32 * (max(-48.0, min(0.0, db(peak[ch]))) + 48) / 48))
            if h:
                d.rectangle((x, 46 - h, x + 4, 46), fill=1)


def bar_x(level):
    """x of a level in dBFS on the bars' scale (-48..0 dBFS across x 12..124)"""
    return 12 + int(round(112 * (max(-48.0, min(0.0, level)) + 48) / 48))


def vu_angle(position):
    """needle angle (radians from straight up) for a VU position (1.0 = 0 VU): -20 VU at -0.5, +3 VU at +0.5.
    Like a real VU meter the scale is linear in voltage, so -20..-10 is short and the top is wide."""
    lo, hi = 10 ** (-20 / 20), 10 ** (3 / 20)
    return -0.5 + (min(hi, max(lo, position)) - lo) / (hi - lo)


def vu_scale(d, cx):
    """One VU dial's arc and ticks, the needle's pivot below the screen at (cx, 80)."""
    d.arc((cx - 58, 22, cx + 58, 138), 241, 299, fill=1)
    d.arc((cx - 56, 24, cx + 56, 136), 270 + int(math.degrees(vu_angle(1.0))), 299, fill=1)  # red zone
    for value in (-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3):
        a = vu_angle(10 ** (value / 20))
        inner = 50 if value in (-20, -10, 0, 3) else 54
        d.line((cx + inner * math.sin(a), 80 - inner * math.cos(a), cx + 58 * math.sin(a), 80 - 58 * math.cos(a)),
               fill=1)


def spectrum_height(level):
    """bar height in pixels (0..52) for a band level in dBFS, FLOOR..0"""
    return int(round(52 * (max(FLOOR, min(0.0, level)) - FLOOR) / -FLOOR))
