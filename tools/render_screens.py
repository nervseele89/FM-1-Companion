"""Render the bridge's OLED screens to PNG files with bridge.py's own drawing code, for the README.

  python tools/render_screens.py OUTDIR [--fonts DIR]

DIR holds DejaVuSans.ttf and DejaVuSans-Bold.ttf, the fonts the Pi uses
(default: /usr/share/fonts/truetype/dejavu). Needs Pillow; no Pi or HAT.
"""
import argparse
import sys
import time
from pathlib import Path

from PIL import Image, ImageFont

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

    def usb_label(self):
        return "Felucca"

    def activity(self):
        return time.monotonic()               # the MIDI dot on


class Loop:
    def running(self):
        return True

    def stop(self):
        pass


def scenes(app):
    """(file name, how to set the app up) for each screen worth showing."""
    def status():
        pass

    def settings():
        app.screen, app.setting = "settings", 0

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

    return [("status", status), ("settings", settings), ("speakers", speakers), ("pairing", pairing),
            ("screensaver", saver), ("library", library), ("job", job)]


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
    state = dict(bridge.DEFAULTS, last_name="JBL Go 3", last_mac="00:00:00:00:00:00")
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
    """The four meters side by side (2 x 2) as screen-meters.png."""
    meters.load_numpy()
    frames = []
    for i in range(len(meters.METERS)):
        display = Capture()
        app = bridge.App(display, dict(state, meter=i))
        app.loop, app.midi, app.wifi = Loop(), Midi(), False
        app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
        app.levels = Levels()
        app.library_state = lambda now=None: None
        app.open_meters()
        for _ in range(4):                    # let the needles and peak holds settle
            app.render(force=True)
            time.sleep(0.1)
        frames.append(oled(display.image))
    w, h, gap = frames[0].width, frames[0].height, 12
    sheet = Image.new("RGB", (2 * w + gap, 2 * h + gap), (30, 30, 30))
    for i, frame in enumerate(frames):
        sheet.paste(frame, ((i % 2) * (w + gap), (i // 2) * (h + gap)))
    sheet.save(out / "screen-meters.png", optimize=True)
    print("wrote", out / "screen-meters.png")


if __name__ == "__main__":
    main()
