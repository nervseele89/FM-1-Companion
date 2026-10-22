"""Tests for the meter screen (meters.py): the level and spectrum maths, the keys, and every meter renders.

  python tests/test_meters.py [PNG_DIR]     (needs Pillow; PNG_DIR gets a picture of each meter)
"""
import math
import os
import sys
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
    assert app.meter_view(now) is None and not app.levels.proc         # status screen: no recording
    app.open_meters()
    assert app.meter_view(now) == (0, 1) and app.levels.target == app.source["name"]
    app.mode = "saver"
    app.meter_view(now)
    assert not app.levels.proc                                        # screen saver: stopped
    app.mode, app.source = "on", None
    assert app.meter_view(now) == "ready" and not app.levels.proc       # no FM-1: nothing recorded


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
