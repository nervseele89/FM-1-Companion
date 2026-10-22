"""Tests for the meter screen (meters.py): the level and spectrum maths, the keys, and every meter renders.

  python tests/test_meters.py [PNG_DIR]     (needs Pillow; PNG_DIR gets a picture of each meter)
"""
import math
import os
import sys
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fm1-bt-bridge"))
TMP = Path(tempfile.mkdtemp(prefix="fm1-meters-"))
os.environ.update(FM1_LIBRARY=str(TMP / "library"), XDG_RUNTIME_DIR=str(TMP / "run"), FM1_SD_LIBRARY=str(TMP / "sd"))

import bridge  # noqa: E402
import meters  # noqa: E402
import numpy as np  # noqa: E402

meters.load_numpy()                         # the box imports it in the background; here, before the tests

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def tone(freqs, amp=0.5, frames=meters.SPECTRUM_N, right=1.0):
    """(frames, 2) s16 samples: the sum of sines at freqs, each amp of full scale; right channel scaled"""
    t = np.arange(frames) / meters.RATE
    v = sum(amp * np.sin(2 * math.pi * f * t) for f in freqs)
    return (np.stack([v, v * right], axis=1) * 32767).astype(np.int16)


class Capture:
    def __init__(self):
        self.image = None

    def show(self, img):
        self.image = img.copy()

    def contrast(self, value):
        pass

    def power(self, on):
        pass


class Loop:
    def running(self):
        return True

    def stop(self):
        pass


class FakeLevels:
    """LevelReader stand-in: hands out one block, never starts pw-cat."""

    def __init__(self, block):
        self.block, self.seq, self.target, self.proc, self.stopped = block, 1, None, None, 0

    def start(self, target):
        self.target, self.proc = target, True

    def running(self):
        return True

    def stop(self):
        self.stopped += 1
        self.proc = self.target = None

    def take(self):
        return self.block, self.block


def make_app(block):
    display = Capture()
    app = bridge.App(display, dict(bridge.DEFAULTS, last_name="JBL Go 3", last_mac="x"))
    app.loop = Loop()
    app.save_settings = lambda: None          # never this computer's real settings file
    app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
    app.levels = FakeLevels(block)
    return app, display


def test_levels():
    (pl, pr), (rl, rr) = meters.levels(tone([1000], amp=1.0, right=0.5, frames=1600))
    assert abs(meters.db(pl)) < 0.1 and abs(meters.db(pr) + 6.02) < 0.1           # peaks: 0 and -6 dBFS
    assert abs(meters.db(rl) + 3.01) < 0.1 and abs(meters.db(rr) + 9.03) < 0.1     # sine RMS is 3 dB lower
    assert meters.db(0) == -120.0
    assert meters.levels(tone([1000])[:0]) == ([0.0, 0.0], [0.0, 0.0])          # nothing since the last frame
    raw = tone([1000]).astype("<i2").tobytes()
    assert (meters.frames(raw) == tone([1000])).all()


def test_spectrum():
    for freq in (125, 1000, 4000):
        bands = meters.spectrum(tone([freq], amp=0.5))
        loudest = bands.index(max(bands))
        k = freq * meters.SPECTRUM_N / meters.RATE
        edges = meters.band_bins()
        assert edges[loudest] - 1 <= k <= edges[loudest + 1], (freq, loudest, edges)
        assert -9 < max(bands) < -3, max(bands)            # half scale: about -6 dBFS
    assert max(meters.spectrum(np.zeros((meters.SPECTRUM_N, 2), np.int16))) < meters.FLOOR
    assert len(meters.band_bins()) == 17 and all(b > a for a, b in zip(meters.band_bins(), meters.band_bins()[1:]))


def test_keys():
    app, _ = make_app(tone([440]))
    app.on_key("left")
    assert app.screen == "meter" and app.meter_index() == 0
    app.on_key("down")
    assert app.meter_index() == 1
    app.on_key("up")
    app.on_key("up")
    assert app.meter_index() == len(meters.METERS) - 1                 # wraps round
    app.on_key("right")
    assert app.screen == "status" and app.levels.stopped
    app.on_key("left")
    assert app.screen == "meter" and app.meter_index() == len(meters.METERS) - 1    # remembered


def test_reader_follows_the_screen():
    app, _ = make_app(tone([440]))
    now = 100.0
    assert app.meter_view(now) == (None, None) and not app.levels.proc  # status screen: no level stream
    app.open_meters()
    assert app.meter_view(now) == ((0, 1), None) and app.levels.target == app.source["name"]
    app.mode = "saver"
    app.meter_view(now)
    assert not app.levels.proc                                        # screen saver: stopped
    app.mode, app.source = "on", None
    assert app.meter_view(now) == ("ready", None) and not app.levels.proc   # no FM-1: no level stream


def test_every_meter_renders():
    block = tone([110, 880, 3000], amp=0.3, right=0.7)
    for i, name in enumerate(meters.METERS):
        app, display = make_app(block)
        app.state["meter"] = i
        app.open_meters()
        for step in range(3):                  # a few frames: ballistics and peak hold move
            app.render(force=True)
        lit = display.image.convert("L").histogram()[255]
        assert lit > 60, (name, lit)
        if OUT:
            OUT.mkdir(parents=True, exist_ok=True)
            display.image.convert("L").resize((512, 256)).save(OUT / f"meter-{name.lower()}.png")
    app, display = make_app(block)
    app.source = None
    app.open_meters()
    app.render(force=True)                     # no FM-1: a message, no crash


class FakePwRecord:
    """stands in for pw-record: a Python process that writes the file and waits to be stopped"""
    real = meters.subprocess.Popen

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        script = "import sys, time; open(sys.argv[1], 'wb').write(b'RIFF' + bytes(40)); time.sleep(60)"
        return self.real([sys.executable, "-c", script, cmd[-1]], **kw)


def test_recording():
    fake = FakePwRecord()
    meters.subprocess.Popen = fake
    try:
        app, display = make_app(tone([440]))
        app.open_meters()
        app.on_key("press")
        assert app.recorder.recording() and fake.cmd[0] == "pw-record" and "alsa_input.usb-Felucca" in fake.cmd
        path = app.recorder.path
        assert path.parent.name == "recordings" and path.suffix == ".wav"
        for _ in range(100):                               # the stand-in takes a moment to start
            if path.exists():
                break
            time.sleep(0.05)
        app.recorder.started -= 65                         # pretend it has run a while
        assert app.meter_view(time.monotonic())[1] == 65
        app.render(force=True)                             # REC 1:05 over the meter
        app.screen = "status"
        app.render(force=True)
        if OUT:
            display.image.convert("L").resize((512, 256)).save(OUT / "status-rec.png")
        app.on_key("left")
        app.on_key("press")                                # stop
        assert not app.recorder.recording() and "Saved" in app.message and path.exists()
        # the FM-1 goes away: pw-record ends, and the box says so
        app.on_key("press")
        app.recorder.proc.kill()
        app.recorder.proc.wait()
        app.meter_view(time.monotonic())
        assert not app.recorder.proc and "stopped" in app.message
        # a full SD card: no start; and a running recording stops
        real_usage = meters.shutil.disk_usage
        meters.shutil.disk_usage = lambda p: real_usage(p)._replace(free=meters.MIN_FREE - 1)
        try:
            app.on_key("press")
            assert not app.recorder.recording() and "full" in app.message
        finally:
            meters.shutil.disk_usage = real_usage
        app.on_key("press")
        meters.shutil.disk_usage = lambda p: real_usage(p)._replace(free=0)
        try:
            app.rec_space_check = 0
            app.meter_view(time.monotonic())
            assert not app.recorder.recording() and "full" in app.message
        finally:
            meters.shutil.disk_usage = real_usage
        app.recorder.stop()
        assert meters.clock(65) == "1:05" and meters.clock(3725) == "1:02:05"
    finally:
        meters.subprocess.Popen = FakePwRecord.real


def test_pitch_and_chords():
    for hz, name in ((55.0, "A1"), (110.0, "A2"), (261.63, "C4"), (440.0, "A4"), (880.0, "A5"), (1318.5, "E6")):
        t = np.arange(1024) / meters.RATE
        saw = sum(np.sin(2 * math.pi * hz * k * t) / k for k in range(1, 6))   # harmonics, like a synth
        freq, clarity = meters.pitch(saw, meters.RATE, hi=2500.0)
        note, octave, cents, _ = meters.note_of(freq)
        assert f"{note}{octave}" == name and abs(cents) < 6 and clarity > 0.9, (hz, freq, cents)
    noise = np.random.default_rng(2).standard_normal(1024)
    assert meters.pitch(noise, meters.RATE)[0] is None                       # no pitch in noise
    assert meters.chord_name([60, 64, 67]) == "C" and meters.chord_name([57, 60, 64]) == "Am"
    assert meters.chord_name([55, 59, 62, 65]) == "G7" and meters.chord_name([62]) == "D4"
    assert meters.chord_name([60, 61]) is None and meters.chord_name([]) is None


class FakeMidi:
    enabled, error, pairing_until, peer = True, None, 0.0, "SMK-37"
    clock, notes, chans = None, [60, 64, 67, 72], (1 << 0) | (1 << 9)

    def usb_label(self):
        return "Felucca"

    def activity(self):
        return 0.0


def test_tuner_and_midi_meters():
    t = np.arange(meters.RECENT_N) / meters.RATE
    a3 = (np.stack([np.sin(2 * math.pi * 220 * t)] * 2, axis=1) * 12000).astype(np.int16)
    app, display = make_app(a3)
    app.state["meter"] = meters.METERS.index("Tuner")
    app.open_meters()
    app.render(force=True)
    assert app.m_tune[:2] == ["A", 3] and abs(app.m_tune[2]) < 5
    if OUT:
        display.image.convert("L").resize((512, 256)).save(OUT / "meter-tuner.png")
    app, display = make_app(a3)
    app.midi = FakeMidi()
    app.state["meter"] = meters.METERS.index("MIDI")
    app.open_meters()
    view = app.meter_view(time.monotonic())
    assert view[0] == (meters.METERS.index("MIDI"), (60, 64, 67, 72), (1 << 0) | (1 << 9)) and not app.levels.proc
    app.render(force=True)
    assert app.m_base == 48                               # C4..C5 fit the keyboard's C3..B5
    if OUT:
        display.image.convert("L").resize((512, 256)).save(OUT / "meter-midi.png")
    app.midi.notes = [90]                                 # F#6: the keyboard moves up an octave at a time
    app.render(force=True)
    assert app.m_base == 84


def test_beat_and_level_meters():
    assert meters.beat_position({"bpm": 120, "bar": 10.0}, 11.25) == 2.5    # 1.25 s at 120 BPM
    assert meters.history_y(-48) == 63 and meters.history_y(0) == 12
    t = np.arange(meters.RECENT_N) / meters.RATE
    mono = np.sin(2 * math.pi * 330 * t) * 9000
    same = np.stack([mono, mono], axis=1).astype(np.int16)
    assert abs(meters.correlation(same) - 1) < 1e-6
    assert abs(meters.correlation(np.stack([mono, -mono], axis=1).astype(np.int16)) + 1) < 1e-6
    assert len(meters.waterfall_bins()) == 65
    app, display = make_app(same)
    app.midi = FakeMidi()
    app.midi.clock = {"bpm": 120, "source": "box", "playing": True, "bar": time.monotonic() - 0.6}
    drawn = {"Waterfall": lambda: app.m_wf is not None and app.m_wf.any(),   # each meter's own state
             "Stereo": lambda: app.m_corr > 0.5, "History": lambda: bool(app.m_hist),
             "Beat": lambda: display.image.convert("L").histogram()[255] > 200}
    for name, check in drawn.items():
        app.state["meter"] = meters.METERS.index(name)
        app.open_meters()
        for _ in range(4):
            app.render(force=True)
            time.sleep(0.1)                               # as the screen does: smoothing needs time
        assert check(), name
        if OUT:
            display.image.convert("L").resize((512, 256)).save(OUT / f"meter-{name.lower()}.png")


def test_pack_pages():
    import random
    from PIL import Image
    img = Image.frombytes("1", (128, 64), bytes(random.getrandbits(8) for _ in range(1024)))
    slow = [img.crop((0, p * 8, 128, p * 8 + 8)).transpose(Image.Transpose.ROTATE_270).tobytes() for p in range(8)]
    assert bridge.pack_pages(img) == slow


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
