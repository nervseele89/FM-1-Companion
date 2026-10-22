"""Render the bridge's OLED screens to PNG files with bridge.py's own drawing code, for the README.

  python tools/render_screens.py OUTDIR [--fonts DIR]

DIR holds DejaVuSans.ttf and DejaVuSans-Bold.ttf, the fonts the Pi uses
(default: /usr/share/fonts/truetype/dejavu). Needs Pillow; no Pi or HAT.
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageFont

TMP = Path(tempfile.mkdtemp(prefix="fm1-shots-"))      # an empty library: the same pictures on any computer
os.environ.update(FM1_LIBRARY=str(TMP / "library"), XDG_RUNTIME_DIR=str(TMP / "run"), FM1_SD_LIBRARY=str(TMP / "sd"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fm1-bt-bridge"))
import bridge  # noqa: E402
import meters  # noqa: E402

ON, OFF, SCALE = (95, 205, 255), (6, 9, 14), 5      # the HAT's blue OLED, 5x


def oled(img):
    """The 1-bit frame as the OLED shows it: blue on black, scaled up with square pixels."""
    big = img.resize((128 * SCALE, 64 * SCALE), Image.NEAREST)
    return Image.composite(Image.new("RGB", big.size, ON), Image.new("RGB", big.size, OFF), big.convert("L"))


class Capture:
    def __init__(self):
        self.image = None

    def show(self, img):
        self.image = img.copy()

    def contrast(self, value):
        pass

    def power(self, on):
        pass


class Midi:
    enabled, error, pairing_until, peer = True, None, 0.0, "SMK-37+VA_BLE"
    clock = {"bpm": 120, "source": "box", "playing": True}

    def set_routes(self, routes):
        pass

    def usb_label(self):
        return "Felucca"

    def activity(self):
        return time.monotonic()               # the MIDI dot on


class Recording:
    """meters.Recorder stand-in: 1 min 23 s into a recording"""
    proc, path = True, TMP / "rec.wav"

    def recording(self):
        return True

    def seconds(self, now=None):
        return 83.0

    def stop(self):
        return None, 0.0


class Loop:
    def running(self):
        return True

    def stop(self):
        pass


def scenes(app):
    """(file name, how to set the app up) for each screen worth showing."""
    def status():
        pass

    def recording():
        app.recorder = Recording()

    def settings():
        app.screen, app.setting = "settings", 0

    def settings_midi():
        app.screen, app.setting, app.settings_menu = "settings", 2, "midi"

    def matrix():
        app.screen, app.mx = "matrix", [0, 0]

    def route():
        import blemidi
        routes = blemidi.default_routes()
        routes["BT>FM-1"].update(transpose=12, velocity="soft")
        app.state["routes"] = routes
        app.screen, app.route_key, app.route_row = "route", "BT>FM-1", 2

    def library():
        app.screen, app.lib_cursor = "library", 0

    def job():
        app.screen, app.job_title = "job", "Firmware"
        state = {"job": "flash", "state": "running", "step": "Writing", "percent": 64}
        app.library_state = lambda now=None: state

    def speakers():
        app.screen, app.scanning, app.cursor = "scan", False, 1
        app.devices = [{"name": n, "mac": ""} for n in ("AirPods Pro", "JBL Go 3", "WH-1000XM4")]

    def pairing():
        speakers()
        app.busy = "Pairing JBL Go 3"

    def saver():
        app.mode = "saver"

    return [("status", status), ("status-rec", recording), ("settings", settings),
            ("settings-midi", settings_midi), ("matrix", matrix), ("route", route), ("speakers", speakers),
            ("pairing", pairing), ("screensaver", saver), ("library", library), ("job", job)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir")
    parser.add_argument("--fonts", default="/usr/share/fonts/truetype/dejavu")
    args = parser.parse_args()
    fonts = Path(args.fonts)
    bridge.font = lambda size, bold=False: ImageFont.truetype(
        str(fonts / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    state = dict(bridge.DEFAULTS, last_name="JBL Go 3", last_mac="00:00:00:00:00:00", clock_mode="box")
    for name, setup in scenes(bridge.App(Capture(), state)):
        display = Capture()
        app = bridge.App(display, dict(state))
        app.loop, app.midi, app.wifi = Loop(), Midi(), False
        app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
        app.sink = {"id": 2, "name": "bluez_output.jbl", "label": "JBL Go 3"}
        app.volume = 75
        app.library_state = lambda now=None: None     # no library job (whatever this computer has)
        dict(scenes(app))[name]()
        app.render()
        oled(display.image).save(out / f"screen-{name}.png", optimize=True)
        print("wrote", out / f"screen-{name}.png")
    display = Capture()
    bridge.splash(display)
    oled(display.image).save(out / "screen-starting.png", optimize=True)
    print("wrote", out / "screen-starting.png")
    meters_sheet(state, out)


class Levels:
    """meters.LevelReader stand-in: a bass note, a chord and some hiss, louder on the left"""

    def __init__(self):
        np = meters.numpy()
        t = np.arange(1600) / meters.RATE
        v = (0.35 * np.sin(2 * np.pi * 98 * t) + 0.15 * np.sin(2 * np.pi * 392 * t)
             + 0.12 * np.sin(2 * np.pi * 494 * t) + 0.06 * np.sin(2 * np.pi * 2350 * t)
             + 0.01 * np.random.default_rng(1).standard_normal(len(t)))
        self.block = (np.stack([v, v * 0.7], axis=1) * 32767).astype(np.int16)
        self.seq, self.target, self.proc = 1, None, None

    def start(self, target):
        self.target, self.proc = target, True

    def running(self):
        return True

    def stop(self):
        pass

    def take(self):
        return self.block, self.block


def meters_sheet(state, out):
    """Each meter on its own (screen-meter-NAME.png), and all of them on one sheet (screen-meters.png)."""
    meters.load_numpy()
    frames = []
    for i in range(len(meters.METERS)):
        display = Capture()
        app = bridge.App(display, dict(state, meter=i))
        app.loop, app.midi, app.wifi = Loop(), Midi(), False
        app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
        app.levels = Levels()
        app.library_state = lambda now=None: None
        if meters.METERS[i] == "Tape":        # the tape deck while it records
            app.recorder, app.tape_done = Recording(), 0.35
        app.open_meters()
        for _ in range(4):                    # let the needles and peak holds settle
            app.render(force=True)
            time.sleep(0.1)
        frames.append(oled(display.image))
        name = f"screen-meter-{meters.METERS[i].lower()}.png"
        frames[-1].save(out / name, optimize=True)
        print("wrote", out / name)
    w, h, gap, cols = frames[0].width, frames[0].height, 12, 2
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols - 1) * gap, rows * h + (rows - 1) * gap), (30, 30, 30))
    for i, frame in enumerate(frames):
        sheet.paste(frame, ((i % cols) * (w + gap), (i // cols) * (h + gap)))
    sheet.save(out / "screen-meters.png", optimize=True)
    print("wrote", out / "screen-meters.png")


if __name__ == "__main__":
    main()
