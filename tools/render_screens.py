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


if __name__ == "__main__":
    main()
