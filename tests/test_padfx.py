"""The sampler's punch-in FX (SLOOP's sixteen): what each does to the pads' sound, on the beat, the crossfades,
and their page in the sampler (PADS > FX). numpy only."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FM1_LIBRARY", str(Path(tempfile.mkdtemp(prefix="fm1-fx-")) / "library"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fm1-bt-bridge"))

import numpy as np  # noqa: E402

import padfx  # noqa: E402

RATE = 48000
BPM120 = (60 / (120 * 24), None, False)          # 120 BPM, stopped: a beat is 24000 frames


def run(fx, signal, which, block=512, release_at=None):
    """signal (frames, 2) through fx with `which` held from the start -> the output"""
    out = []
    fx.press(which)
    for i in range(0, len(signal), block):
        if release_at is not None and i >= release_at:
            fx.press(None)
        out.append(fx.process(signal[i:i + block].astype(np.float32), BPM120, None))
    return np.concatenate(out)


def ramp(frames):
    v = (np.arange(frames) % 10000) / 10000.0
    return np.stack([v, v], axis=1).astype(np.float32)


def test_one_pole_closed_form():
    x = np.random.default_rng(1).standard_normal((300, 2))
    a = padfx.coef(1000, RATE)
    y, z = padfx.one_pole(x, a, np.zeros(2))
    ref, prev = np.empty_like(x), np.zeros(2)
    for i in range(len(x)):
        prev = a * prev + (1 - a) * x[i]
        ref[i] = prev
    assert np.allclose(y, ref, atol=1e-9) and np.allclose(z, ref[-1])


def test_loops_repeat_their_span():
    """LOOP 16 (a quarter beat: 6000 frames at 120): the first pass is what comes in, then it repeats"""
    fx = padfx.PunchFX(RATE)
    sig = ramp(30000)
    out = run(fx, sig, padfx.LOOP16)
    span = 6000
    assert np.allclose(out[1000:5000], sig[1000:5000], atol=1e-6)            # the first pass
    assert np.allclose(out[span + 1000:span + 5000], sig[1000:5000], atol=1e-6)   # again
    assert np.allclose(out[3 * span + 100:3 * span + 200], sig[100:200], atol=1e-6)
    assert abs(out[span, 0]) < 0.01                                        # the seam faded


def test_loop_on_the_grid():
    """with the sequencer playing, a loop is the division the press fell in, from its start"""
    fx = padfx.PunchFX(RATE)
    sig = ramp(48000)
    grid = (60 / (120 * 24), 0.0, True)
    for i in range(0, 12000, 512):                                          # a quarter beat... and more, dry
        fx.process(sig[i:i + 512], grid, i / RATE)
    fx.press(padfx.LOOP8)                                                   # an 8th: 12000 frames
    w = fx.w
    out = [fx.process(sig[i:i + 512], grid, i / RATE) for i in range(w, w + 30000, 512)]
    out = np.concatenate(out)
    start = (w // 12000) * 12000                                            # the 8th it's in
    k = 12000 + 3000 - (w - start)                                          # its frame 3000, second time round
    assert np.allclose(out[k], sig[start + 3000], atol=1e-6)


def test_reverse_stop_half():
    fx = padfx.PunchFX(RATE)
    sig = ramp(24000)
    run(fx, sig, None)                                                      # a beat in the ring
    out = run(fx, ramp(24000) * 0, padfx.REV)
    assert np.allclose(out[1000], sig[24000 - 1001], atol=1e-6)            # the last beat backwards
    fx = padfx.PunchFX(RATE)
    out = run(fx, np.full((30000, 2), 0.5, np.float32), padfx.STOP)
    assert out[100, 0] > 0.45 and abs(out[12000, 0] - 0.25) < 0.02 and np.all(np.abs(out[24100:]) < 1e-6)
    fx = padfx.PunchFX(RATE)
    out = run(fx, sig, padfx.HALF)
    assert np.allclose(out[2000], sig[1000], atol=1e-6) and np.allclose(out[2001], sig[1000], atol=1e-6)


def test_filters_crush_gate_echo():
    t = np.arange(48000) / RATE
    hi = np.stack([np.sin(2 * np.pi * 8000 * t)] * 2, axis=1).astype(np.float32)
    lo = np.stack([np.sin(2 * np.pi * 80 * t)] * 2, axis=1).astype(np.float32)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))          # noqa: E731
    assert rms(run(padfx.PunchFX(RATE), hi, padfx.LOW)[-4800:]) < 0.05      # LOW closed: no 8 kHz
    assert rms(run(padfx.PunchFX(RATE), lo, padfx.HIGH)[-4800:]) < 0.15     # HIGH open: little 80 Hz
    assert rms(run(padfx.PunchFX(RATE), lo, padfx.LOW)[-4800:]) > 0.6       # (the lows stay)
    out = run(padfx.PunchFX(RATE), ramp(2048), padfx.CRUSH)
    assert set(np.round(out[200:2048, 0] * 16, 6)) <= set(range(17))        # 4 bits
    out = run(padfx.PunchFX(RATE), np.ones((24000, 2), np.float32), padfx.GATE)
    assert out[100, 0] > 0.99 and out[3500, 0] == 0.0 and out[6100, 0] > 0.9   # 16ths of 6000 frames
    sig = np.zeros((40000, 2), np.float32)
    sig[600] = 1.0
    out = run(padfx.PunchFX(RATE), sig, padfx.ECHO)
    assert abs(out[600 + 18000, 0] - 0.55) < 1e-3 and abs(out[600 + 36000, 0] - 0.55 ** 2) < 1e-3
    out = run(padfx.PunchFX(RATE), ramp(4096), padfx.ALIAS)
    assert out[1001, 0] == out[1000, 0] == ramp(4096)[1000, 0] and out[1008, 0] != out[1007, 0]


def test_crossfade_and_release():
    """an FX fades in and out over 64 frames; let go, the dry sound is back; another one waits for the fade"""
    fx = padfx.PunchFX(RATE)
    sig = np.full((4096, 2), 0.5, np.float32)
    out = run(fx, sig, padfx.CRUSH, block=512, release_at=2048)
    assert np.allclose(out[1000:2048, 0], 0.5) and fx.cur is None and np.allclose(out[3000:, 0], 0.5)
    fx = padfx.PunchFX(RATE)
    out = run(fx, np.ones((1024, 2), np.float32) * 0.9, padfx.GATE)
    assert np.allclose(out[:32, 0], 0.9 * (1 - np.arange(1, 33) / 64) + 0.9 * np.arange(1, 33) / 64 *
                       np.minimum(1, np.arange(32) / 64), atol=1e-5)        # dry into wet over the fade
    for name in padfx.NAMES:                                                 # all sixteen: sound, no blow-up
        fx = padfx.PunchFX(RATE)
        out = run(fx, ramp(30000), padfx.NAMES.index(name))
        assert np.isfinite(out).all() and np.abs(out).max() < 2.0, name


def test_in_the_engine_and_the_fx_page():
    import pads
    e = pads.Engine(RATE)
    e.fx.press(padfx.CRUSH)
    e.render(512)
    assert e.fx.cur == padfx.CRUSH
    import bridge
    import deskui

    class Display:
        image = theme = None

        def show_rgb(self, img):
            self.image = img

    class Pads:
        sounding, step = [], None

        def __init__(self):
            self.sent = []

        def send(self, *msg):
            self.sent.append(msg)
    app = bridge.App(Display(), dict(bridge.DEFAULTS))
    app.save_settings, app.pads = (lambda: None), Pads()
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("PADS")
    ui.render(0.0, force=True)
    fx_button = next(w[0] for w in ui.widgets if w[3] == "fx-open")        # PADS > FX: the sampler's own
    ui.touch("down", fx_button[0] + 4, fx_button[1] + 4, 0.0)
    ui.touch("up", fx_button[0] + 4, fx_button[1] + 4, 0.0)
    ui.render(0.0, force=True)
    assert ui.fx_open and ui.pads_screen() and "FX" not in deskui.TABS
    box = next(w[0] for w in ui.widgets if w[3] == ("fx", padfx.ECHO))
    ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
    assert app.pads.sent[-1] == ("fx", padfx.ECHO)                          # held: on
    ui.touch("up", box[0] + 4, box[1] + 4, 0.1)
    assert app.pads.sent[-1] == ("fx", None)                                # let go: off
    ui.render(0.0, force=True)
    latch = next(w[0] for w in ui.widgets if w[3] == "fx-latch")
    ui.touch("down", latch[0] + 4, latch[1] + 4, 0.0)
    ui.touch("up", latch[0] + 4, latch[1] + 4, 0.0)
    ui.render(0.0, force=True)
    for want in (padfx.GATE, None):                                         # LATCH: a tap holds it on,
        box = next(w[0] for w in ui.widgets if w[3] == ("fx", padfx.GATE))  # a tap again lets go
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
        ui.render(0.0, force=True)
        assert app.pads.sent[-1] == ("fx", want) and ui.fx_on == want


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
