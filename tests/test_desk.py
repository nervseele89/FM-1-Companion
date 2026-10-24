"""The desk box (Pi 3/4 + 3.5" touchscreen): which build a box is, the touch buttons, the screen writes.
No touchscreen needed (needs Pillow and numpy)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fm1-bt-bridge"))

from PIL import Image, ImageDraw  # noqa: E402

import desk  # noqa: E402
import device  # noqa: E402

PI3 = "Raspberry Pi 3 Model B Plus Rev 1.3"
ZERO = "Raspberry Pi Zero W Rev 1.1"
TFT = ("/dev/fb1", 480, 320)


def test_which_box():
    assert device.detect(model=ZERO, screen=None)["build"] == "pocket"
    pocket = device.detect(model=ZERO, screen=TFT)                    # a Zero is the pocket box, screen or not
    assert pocket["build"] == "pocket" and "usb_ports" not in pocket["can"] and "touch" not in pocket["can"]
    box = device.detect(model=PI3, screen=TFT)
    assert box["build"] == "desk" and {"touch", "color", "usb_ports"} <= box["can"]
    assert device.detect(model=PI3, screen=None)["build"] == "pocket"   # no touchscreen yet: the OLED's code
    assert device.detect("desk", model=ZERO, screen=None)["build"] == "desk"     # the setting wins
    with tempfile.TemporaryDirectory() as tmp:                       # the framebuffer's size from sysfs
        root = Path(tmp)
        (root / "fb0").mkdir()
        (root / "fb0" / "virtual_size").write_text("1920,1080\n")    # HDMI: not the touchscreen
        assert device.touchscreen(root) is None
        (root / "fb1").mkdir()
        (root / "fb1" / "virtual_size").write_text("480,320\n")
        assert device.touchscreen(root) == ("/dev/fb1", 480, 320)
        model = root / "model"
        model.write_bytes(PI3.encode() + b"\0")
        assert device.board(model) == PI3


def test_buttons_and_touch_mapping():
    assert desk.button_at(80, 230) == "key1" and desk.button_at(240, 290) == "press"
    assert desk.button_at(240, 100) is None                           # the picture: no button
    cal = [3900, 200, 3900, 200, True]                                # chip x up the screen, both reversed
    assert desk.to_screen(3900, 3900, cal) == (0, 0)
    assert desk.to_screen(200, 200, cal) == (479, 319)
    assert desk.to_screen(9999, -50, cal) == (479, 0)                 # out of range: clamped
    assert desk.to_screen(3900, 3900, [0, 4000, 0, 4000, False]) == (467, 311)


class FakeTimer:
    def __init__(self):
        self.log = []

    def pressed(self, name, now):
        self.log.append(("down", name))

    def released(self, name, now):
        self.log.append(("up", name))


class FakeDisplay:
    def __init__(self):
        self.lit = []

    def press(self, name):
        self.lit.append(name)


def test_touch_presses_buttons():
    timer, display = FakeTimer(), FakeDisplay()
    cal = [0, 479, 0, 319, False]                                     # raw = screen, for the test
    t = desk.Touch(timer, display, cal)

    def touch(x, y, down=True):
        t.event(desk.EV_ABS, desk.ABS_X, x, 0)
        t.event(desk.EV_ABS, desk.ABS_Y, y, 0)
        t.event(desk.EV_KEY, desk.BTN_TOUCH, int(down), 0)
        t.event(desk.EV_SYN, 0, 0, 0)
    touch(240, 290)                                                   # on ●
    touch(242, 292)                                                   # the finger wobbles: still held
    touch(330, 290)                                                   # slides onto ▼
    touch(330, 290, down=False)
    assert timer.log == [("down", "press"), ("up", "press"), ("down", "down"), ("up", "down")]
    assert display.lit == ["press", "down", None]


def test_screen_writes():
    frame = Image.new("1", (128, 64))
    ImageDraw.Draw(frame).rectangle((0, 0, 9, 9), fill=1)
    out = desk.compose(frame, pressed="key2")
    assert out.size == (480, 320)
    assert out.getpixel((desk.SCREEN_AT[0] + 1, desk.SCREEN_AT[1] + 1)) == desk.FG     # lit pixel, scaled
    assert out.getpixel((240, 212)) == desk.KEY_ON                                      # key2 lit
    assert len(desk.rgb565(out)) == 480 * 320 * 2
    with tempfile.TemporaryDirectory() as tmp:                        # a file standing in for /dev/fb1
        fb = Path(tmp) / "fb1"
        fb.write_bytes(bytes(480 * 320 * 2))
        screen = desk.TftDisplay(str(fb))
        screen.show(frame)
        screen.press("key1")
        screen.fb.close()
        data = fb.read_bytes()
        assert len(data) == 480 * 320 * 2                             # written in place, never longer
        x, y = desk.SCREEN_AT[0] + 1, desk.SCREEN_AT[1] + 1
        lit = (((desk.FG[0] >> 3) << 11) | ((desk.FG[1] >> 2) << 5) | (desk.FG[2] >> 3)).to_bytes(2, "little")
        assert data[(y * 480 + x) * 2:(y * 480 + x) * 2 + 2] == lit


class RgbCapture:
    def __init__(self):
        self.image, self.theme = None, None

    def show_rgb(self, img):
        self.image = img.copy()


def test_touch_screens():
    """the desk UI by touch: tabs, a mixer slider dragged, a route tapped on and off, a theme picked"""
    import os
    os.environ.setdefault("FM1_SETTINGS", str(Path(tempfile.mkdtemp()) / "settings.json"))
    import bridge
    import deskui
    import sloopstyle
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
    app.others = [{"id": 4, "name": "alsa_input.usb-SMK", "label": "SMK-37+VA"}]
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12                                             # no theme file: GREEN
    now = 0.0

    def tap(x, y, hold=0.0):
        ui.touch("down", x, y, now)
        ui.touch("up", x, y, now + hold)
        ui.render(now, force=True)
    ui.render(now, force=True)
    assert app.display.image.size == (480, 320) and ui.theme == sloopstyle.Theme("GREEN")
    tap(144, 300)                                                     # the MIX tab
    assert ui.tab == "MIX"
    slider = next(w for w in ui.widgets if w[3] == ("slide", "usb"))
    box = slider[0]
    ui.touch("down", (box[0] + box[2]) // 2, box[3] - 10, now)        # the bottom: 0%
    assert app.mix_level("usb") == 0
    ui.touch("move", (box[0] + box[2]) // 2, box[1] + 10, now)        # dragged to the top: 200%
    ui.touch("up", (box[0] + box[2]) // 2, box[1] + 10, now)
    assert app.mix_level("usb") == 200
    tap(240, 300)                                                     # MIDI
    cell = next(w for w in ui.widgets if w[3] == ("route", "BT>USB"))
    tap((cell[0][0] + cell[0][2]) // 2, (cell[0][1] + cell[0][3]) // 2)
    assert app.routes()["BT>USB"]["on"] is True                      # off at first: tapped on
    tap((cell[0][0] + cell[0][2]) // 2, (cell[0][1] + cell[0][3]) // 2, hold=0.8)
    assert ui.classic and app.screen == "route" and app.route_key == "BT>USB"   # held: its settings
    ui.classic, app.screen = False, "status"
    tap(432, 300)                                                     # MORE, then THEME round to AMBER
    ui.more_top = 3
    ui.render(now, force=True)
    row = next(w for w in ui.widgets if w[3] == ("more", "THEME"))
    tap(row[0][0] + 20, row[0][1] + 10)
    tap(row[0][0] + 20, row[0][1] + 10)
    assert app.state["desk_theme"] == "AMBER"
    ui.render(now + 5, force=True)
    assert ui.theme.name == "AMBER"


def test_every_meter_draws():
    """each of the eleven desk meters, with sound (a chord) and with silence, without an error"""
    import math
    import numpy as np
    import deskmeters
    import meters
    import sloopstyle
    meters.load_numpy()
    t = np.arange(meters.RECENT_N) / meters.RATE
    wave = (sum(np.sin(2 * math.pi * f * t) for f in (220, 277, 330)) * 6000).astype("<i2")
    loud = np.stack([wave, wave // 2], axis=1)

    class Reader:
        def __init__(self, x):
            self.x = x

        def take(self):
            return self.x[-512:], self.x

    class Clock:
        clock = {"bpm": 120, "tempo": 120.0, "source": "box", "playing": True, "bar": 0.0}
        notes, chans = [60, 64, 67], 1 | (1 << 9)

    class App:
        source, midi, tape_done, last_activity = {"name": "x"}, Clock(), 0.4, 0.0
        recorder = type("R", (), {"recording": lambda self: True})()

        def __init__(self, i, x):
            self.i, self.levels = i, Reader(x)

        def meter_index(self):
            return self.i
    for x in (loud, np.zeros((meters.RECENT_N, 2), dtype="<i2")):
        dm = deskmeters.DeskMeters()
        for i, name in enumerate(meters.METERS):
            img = Image.new("RGB", (480, 320))
            for k in range(3):
                dm.draw(App(i, x), img, (0, 35, 480, 275), sloopstyle.Theme("AMBER"), 1.0 + k * 0.1)
    assert dm.tune is None                                            # silence: no note


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
