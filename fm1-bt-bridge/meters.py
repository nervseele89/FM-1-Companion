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

METERS = ("Bars", "VU", "Spectrum", "Scope", "Tape", "Tuner", "MIDI", "Waterfall", "Stereo", "History", "Beat")
NO_AUDIO = ("MIDI", "Beat")             # meters drawn from MIDI alone: no audio stream while they show
RATE, BLOCK = 16000, 512                # frames per second and per read (32 ms)
FULL = 32768.0
FLOOR = -60.0                           # dBFS shown as nothing
VU_REF = -18.0                          # dBFS (RMS) that reads 0 VU, the usual digital alignment
SPECTRUM_N = 512                        # frames per spectrum: 31 Hz bins at 16 kHz
RECENT_N = 2048                         # frames kept for the spectrum, scope and tuner (128 ms)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
CHORDS = ((frozenset({0, 4, 7}), ""), (frozenset({0, 3, 7}), "m"), (frozenset({0, 4, 7, 10}), "7"),
          (frozenset({0, 4, 7, 11}), "maj7"), (frozenset({0, 3, 7, 10}), "m7"), (frozenset({0, 3, 6}), "dim"),
          (frozenset({0, 4, 8}), "aug"), (frozenset({0, 2, 7}), "sus2"), (frozenset({0, 5, 7}), "sus4"),
          (frozenset({0, 7}), "5"))
WHITE = (0, 2, 4, 5, 7, 9, 11)          # the white keys' places in an octave
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))   # 4x4 ordered dither: grey on 1 bit
HISTORY_STEP = 0.4                      # seconds per column of the History meter: 128 columns = 51 s
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


_BANDS, _WF = None, None


def waterfall_bins():
    """64 bands for the Waterfall (2 pixels each), from 50 Hz to 7.5 kHz, at least one FFT bin each"""
    global _WF
    if _WF is None:
        hz, (lo, hi) = RATE / SPECTRUM_N, BAND_EDGES
        starts, last = [], 0
        for i in range(65):
            k = max(last + 1, int(round(lo * (hi / lo) ** (i / 64) / hz)))
            starts.append(k)
            last = k
        _WF = starts
    return _WF


def spectrum_bins(samples, edges):
    """dBFS per band (FFT bins edges[i] .. edges[i+1]) for the newest SPECTRUM_N frames, L and R summed"""
    global _window
    np = _np
    if len(samples) < SPECTRUM_N:
        return np.full(len(edges) - 1, FLOOR - 20.0)
    if _window is None:
        _window = np.hanning(SPECTRUM_N).astype(np.float32)
    tail = samples[-SPECTRUM_N:]
    f = np.fft.rfft((tail[:, 0].astype(np.float32) + tail[:, 1]) * _window)
    power = f.real ** 2 + f.imag ** 2
    bands = np.add.reduceat(power[:edges[-1]], edges[:-1])
    scale = (2 * FULL * SPECTRUM_N / 4) ** 2
    return 10 * np.log10(np.maximum(bands, 1e-12) / scale)


def correlation(samples):
    """-1 (out of phase) .. 0 (unrelated) .. +1 (mono) for (n, 2) samples; 0 for silence"""
    np = _np
    if not len(samples):
        return 0.0
    x = samples.astype(np.float32)
    lr, ll, rr = float(np.dot(x[:, 0], x[:, 1])), float(np.dot(x[:, 0], x[:, 0])), float(np.dot(x[:, 1], x[:, 1]))
    return lr / math.sqrt(ll * rr) if ll > 0 and rr > 0 else 0.0


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
    if len(samples) < SPECTRUM_N:
        return [FLOOR] * 16
    return spectrum_bins(samples, band_bins()).tolist()


def pitch(x, rate, lo=40.0, hi=2000.0):
    """(Hz, clarity 0..1) of mono samples by autocorrelation, or (None, clarity) without a clear pitch.
    Takes the first strong peak (not the highest), so it doesn't jump an octave down."""
    np = _np
    x = np.asarray(x, dtype=np.float32)
    x = x - x.mean()
    n = len(x)
    if n < 64:
        return None, 0.0
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfft(x, size)
    r = np.fft.irfft(f.real * f.real + f.imag * f.imag, size)[:n]
    if r[0] <= 0:
        return None, 0.0
    r = r / r[0] * n / (n - np.arange(n))      # unbiased: longer lags overlap less
    kmin, kmax = max(2, int(rate / hi)), min(int(rate / lo), n // 2)
    if kmax <= kmin + 2:
        return None, 0.0
    seg = r[kmin:kmax]
    peaks = np.nonzero((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]))[0] + 1
    if not len(peaks):
        return None, 0.0
    best = seg[peaks].max()
    k = int(peaks[np.nonzero(seg[peaks] >= 0.9 * best)[0][0]])
    a, b, c = seg[k - 1], seg[k], seg[k + 1]
    shift = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0.0
    clarity = float(min(1.0, b))
    return (rate / (kmin + k + shift), clarity) if clarity > 0.5 else (None, clarity)


def note_of(freq, a4=440.0):
    """Hz -> ("A", 4, cents off, MIDI note)"""
    midi = 69 + 12 * math.log2(freq / a4)
    near = int(round(midi))
    return NOTE_NAMES[near % 12], near // 12 - 1, (midi - near) * 100, near


def note_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def chord_name(notes):
    """"Cm7", "G", "D5"... for held MIDI notes; one note: its name; nothing recognised: None"""
    if not notes:
        return None
    if len(set(notes)) == 1:
        return note_name(notes[0])
    classes = {n % 12 for n in notes}
    for root in sorted(classes, key=lambda c: (c - min(notes)) % 12):    # the bass note first
        shape = frozenset((c - root) % 12 for c in classes)
        for template, suffix in CHORDS:
            if shape == template:
                return NOTE_NAMES[root] + suffix
    return None


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
                self.recent = (self.recent + data)[-RECENT_N * 4:]
                self.seq += 1

    def take(self):
        """(samples since the last take, the newest RECENT_N samples), each an (n, 2) array"""
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


class Player:
    """pw-play: a recording to the speaker, mixed with the FM-1's live sound by PipeWire."""

    def __init__(self):
        self.proc, self.path, self.started, self.length = None, None, 0.0, 0.0

    def start(self, target, path):
        self.stop()
        try:
            self.proc = subprocess.Popen(["pw-play", "--target", target, str(path)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return False
        self.path, self.started = path, time.monotonic()
        self.length = max(0, path.stat().st_size - 44) / 192000          # 48 kHz 16-bit stereo
        return True

    def playing(self):
        return self.proc is not None and self.proc.poll() is None

    def seconds(self, now=None):
        return min(self.length, (now or time.monotonic()) - self.started) if self.proc else 0.0

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


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
        self.m_tune = None                              # Tuner: [note, octave, cents, Hz, when last heard]
        self.m_base = 48                                # MIDI: the keyboard's lowest C (C3)
        self.m_wf = None                                # Waterfall: the picture so far (rows x 128, bool)
        self.m_corr = 0.0                               # Stereo: correlation, smoothed
        self.m_hist, self.m_hacc, self.m_hnext = [], FLOOR, 0.0   # History: dB per column, this column, its end

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
        style = METERS[self.meter_index()]
        if self.screen == "meter" and style in NO_AUDIO:                    # no audio needed: MIDI only
            if self.levels.proc:
                self.levels.stop()
            midi = self.midi
            if style == "Beat":               # redraw 8 times a beat
                clock = getattr(midi, "clock", None)
                step = int(beat_position(clock, now) * 8) if clock else None
                return (self.meter_index(), clock and clock.get("bpm"), step), rec
            return (self.meter_index(), tuple(getattr(midi, "notes", ()) or ()),
                    getattr(midi, "chans", 0)), rec
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
        self.m_frame = img                    # for meters that paste a picture (the Waterfall)
        d = ImageDraw.Draw(img)
        if style == "MIDI":
            self.draw_midi(d, now)
            return img
        if style == "Beat":
            self.draw_beat(d, now)
            return img
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
         "Scope": self.draw_scope, "Tape": self.draw_tape, "Tuner": self.draw_tuner,
         "Waterfall": self.draw_waterfall, "Stereo": self.draw_stereo, "History": self.draw_history}[style](
            d, now, dt, peak, rms, recent)
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
        elif style == "Stereo":               # the L and R axes (diagonals), the correlation scale
            for k in range(0, 23, 3):
                d.point((64 - k, 32 - k), fill=1)
                d.point((64 + k, 32 - k), fill=1)
            d.text((36, 9), "L", font=self.f_small, fill=1)
            d.text((88, 9), "R", font=self.f_small, fill=1)
            d.line((24, 58, 104, 58), fill=1)
            for x in (24, 64, 104):
                d.line((x, 56, x, 60), fill=1)
            d.text((10, 53), "-1", font=self.f_small, fill=1)
            d.text((108, 53), "+1", font=self.f_small, fill=1)
        elif style == "Tuner":                # the cents scale: -50 .. +50
            d.line((14, 56, 114, 56), fill=1)
            for c in (-50, -25, 0, 25, 50):
                x = 64 + c
                d.line((x, 53 if c else 50, x, 59 if c else 62), fill=1)
            d.text((2, 50), "b", font=self.f_small, fill=1)
            d.text((119, 50), "#", font=self.f_small, fill=1)
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


    def draw_tuner(self, d, now, dt, peak, rms, recent):
        """The note being played, big, its frequency, and how far off in cents (a pointer over -50..+50)."""
        if len(recent) >= 256 and max(peak) > FULL * 0.01:                 # above -40 dBFS
            x = recent[-1024:].astype(_np.float32).mean(axis=1)              # 64 ms: E1 and up
            freq, _ = pitch(x, RATE, hi=2500.0)
            if freq:
                name, octave, cents, _ = note_of(freq)
                if self.m_tune and self.m_tune[:2] == [name, octave]:
                    cents = self.m_tune[2] + (cents - self.m_tune[2]) * min(1.0, dt * 6)   # steady pointer
                self.m_tune = [name, octave, cents, freq, now]
        if not self.m_tune or now - self.m_tune[4] > 1.5:                   # nothing heard for a moment
            d.text((52, 20), "- -", font=self.f_big, fill=1)
            return
        name, octave, cents, freq = self.m_tune[:4]
        text = f"{name}{octave}"
        w = d.textlength(text, font=self.f_big)
        in_tune = abs(cents) < 5
        if in_tune:                           # in tune: the note in a box
            d.rectangle((60 - w / 2, 14, 68 + w / 2, 44), outline=1)
        d.text((64 - w / 2, 15), text, font=self.f_big, fill=1)
        hz = f"{freq:.1f} Hz"
        d.text((127 - d.textlength(hz, font=self.f_small), 40), hz, font=self.f_small, fill=1)
        c = f"{cents:+.0f}"
        d.text((0, 40), c, font=self.f_small, fill=1)
        x = 64 + max(-50, min(50, int(round(cents))))
        d.polygon(((x - 3, 47), (x + 3, 47), (x, 53)), fill=1)

    def draw_midi(self, d, now):
        """A three-octave keyboard lighting the notes played (to and from the FM-1), the chord's name, and
        which of the 16 channels have traffic."""
        midi = self.midi
        notes = sorted(getattr(midi, "notes", ()) or ())
        chans = getattr(midi, "chans", 0) if midi else 0
        if notes or chans:
            self.last_activity = now          # playing keeps the screen on, as sound does
            if self.mode == "dim":
                self.wake()
        if notes and not (self.m_base <= notes[0] and notes[-1] < self.m_base + 36):
            self.m_base = max(0, min(84, 12 * (notes[0] // 12)))          # follow the hands, an octave at a time
        chord = chord_name(notes)
        line = "  ".join(x for x in (chord, " ".join(note_name(n) for n in notes[:4])) if x) if notes else \
            ("no MIDI" if not midi else "")
        d.text((0, 10), line, font=self.f_small, fill=1)
        held, top, left = set(notes), 22, 1
        for i in range(21):                   # white keys
            note = self.m_base + 12 * (i // 7) + WHITE[i % 7]
            x = left + i * 6
            d.rectangle((x, top, x + 5, top + 26), fill=1 if note in held else 0, outline=1)
        for i in range(21):                   # black keys, over the gaps after C, D, F, G and A
            if WHITE[i % 7] in (4, 11) or i == 20:
                continue
            note = self.m_base + 12 * (i // 7) + WHITE[i % 7] + 1
            x = left + i * 6 + 4
            if note in held:
                d.rectangle((x, top, x + 3, top + 15), fill=0, outline=1)
                d.point((x + 1, top + 12), fill=1)
                d.point((x + 2, top + 12), fill=1)
            else:
                d.rectangle((x, top, x + 3, top + 15), fill=1)
        d.text((left, top + 27), note_name(self.m_base), font=self.f_small, fill=1)
        for ch in range(16):                  # the 16 channels
            x = 40 + ch * 5 + (ch // 4) * 2
            if chans & (1 << ch):
                d.rectangle((x, 54, x + 3, 62), fill=1)
            else:
                d.rectangle((x, 61, x + 3, 62), fill=1)


    def draw_waterfall(self, d, now, dt, peak, rms, recent):
        """A scrolling spectrogram: each frame adds a row at the top (64 bands, 2 pixels each, grey as dither
        density), the older rows move down. ~5 s of history."""
        np = _np
        rows = 53
        if self.m_wf is None:
            self.m_wf = np.zeros((rows, 128), dtype=bool)
        if len(recent) >= SPECTRUM_N:
            level = np.clip((spectrum_bins(recent, waterfall_bins()) - FLOOR) / -FLOOR, 0, 1)
            self.m_wf = np.roll(self.m_wf, 1, axis=0)
            row = np.repeat(level, 2) * 16
            self.m_wf[0] = row > np.array(BAYER[int(now * 10) % 4] * 32)
        self.m_frame.paste(Image.fromarray((self.m_wf * 255).astype(np.uint8)).convert("1"), (0, 11))

    def draw_stereo(self, d, now, dt, peak, rms, recent):
        """A goniometer: mono is a vertical line, wide stereo a cloud, out of phase a horizontal line; the
        correlation below, -1 .. +1."""
        np = _np
        if len(recent) < 8:
            return
        x = recent[-512::2].astype(np.float32)
        mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
        gain = 21 / max(float(np.abs(mid).max()), float(np.abs(side).max()), 300.0)      # fills the circle
        xs, ys = (64 + side * gain).tolist(), (32 - mid * gain).tolist()
        d.point(list(zip(xs, ys)), fill=1)
        self.m_corr += (correlation(recent) - self.m_corr) * min(1.0, dt * 4)
        cx = 64 + int(round(40 * max(-1.0, min(1.0, self.m_corr))))
        d.polygon(((cx - 3, 51), (cx + 3, 51), (cx, 56)), fill=1)

    def draw_history(self, d, now, dt, peak, rms, recent):
        """The peak level over the last ~51 s, one column per 0.4 s, newest on the right."""
        self.m_hacc = max(self.m_hacc, db(max(peak)))
        if now >= self.m_hnext:
            self.m_hist = (self.m_hist + [self.m_hacc])[-128:]
            self.m_hacc, self.m_hnext = FLOOR, now + HISTORY_STEP
        x0 = 128 - len(self.m_hist)
        for i, level in enumerate(self.m_hist):
            if level > -48:
                d.line((x0 + i, history_y(level), x0 + i, 63), fill=1)
        for level in (-12, -24, -36):         # the dB lines on top, inverted where the graph is filled
            y = history_y(level)
            for x in range(0, 112, 4):
                d.point((x, y), fill=0 if self.m_frame.getpixel((x, y)) else 1)
            label = str(level)
            w = d.textlength(label, font=self.f_small)
            d.rectangle((126 - w, y - 9, 127, y), fill=0)
            d.text((128 - w, y - 9), label, font=self.f_small, fill=1)

    def draw_beat(self, d, now):
        """The tempo, big, and four beat boxes lighting in time; the bar starts again on Start."""
        clock = getattr(self.midi, "clock", None) if self.midi else None
        if not clock:                         # following, and nothing sends clock: say how to get one
            for y, text, f in ((16, "No clock coming in", self.f_body), (34, "Tap tempo on the home", self.f_small),
                               (44, "screen, or Settings >", self.f_small), (54, "Clock > Clock: box", self.f_small)):
                d.text(((128 - d.textlength(text, font=f)) / 2, y), text, font=f, fill=1)
            return
        self.last_activity = now
        position = beat_position(clock, now)
        beat, flash = int(position) % 4, position % 1 < 0.15
        text = str(clock["bpm"])
        w = d.textlength(text, font=self.f_big)
        if flash:                             # the beat: the number flashes
            d.rectangle((60 - w / 2, 12, 68 + w / 2, 42), fill=1)
        d.text((64 - w / 2, 13), text, font=self.f_big, fill=0 if flash else 1)
        source = {"box": "box", "FM-1": "FM-1", "BT": "BT"}.get(clock.get("source"), "")
        d.text((0, 30), "BPM", font=self.f_small, fill=1)
        d.text((128 - d.textlength(source, font=self.f_small), 30), source, font=self.f_small, fill=1)
        for i in range(4):
            x = 8 + i * 30
            top = 46 if i == 0 else 49        # beat 1 is the big one
            if i == beat:
                d.rectangle((x, top, x + 22, 62), fill=1)
            else:
                d.rectangle((x, top, x + 22, 62), outline=1)
        if clock.get("playing"):
            d.text((0, 10), "▶", font=self.f_small, fill=1)


def beat_position(clock, now):
    """beats since the bar began (0 .. 4 and on), from the clock's tempo and the time its bar began"""
    bar = clock.get("bar") or now
    return max(0.0, (now - bar) * (clock.get("tempo") or clock["bpm"]) / 60.0)


def history_y(level):
    """the History meter's y for a level in dBFS: -48 at the bottom (y 63), 0 at y 12"""
    return int(round(63 - 51 * (max(-48.0, min(0.0, level)) + 48) / 48))


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
