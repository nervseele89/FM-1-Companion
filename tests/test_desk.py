"""The desk box (Pi 3/4 + 3.5" touchscreen): which build a box is, the touch buttons, the screen writes.
No touchscreen needed (needs Pillow and numpy)."""

import sys
import time
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
    with tempfile.TemporaryDirectory() as tmp:                       # the DRM driver, no HDMI at boot: fb0
        root = Path(tmp)
        (root / "fb0").mkdir()
        (root / "fb0" / "virtual_size").write_text("480,320\n")
        (root / "fb0" / "name").write_text("ili9486drmfb\n")
        assert device.touchscreen(root) == ("/dev/fb0", 480, 320)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
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


def test_touch_calibration():
    """four targets tapped -> touch_cal that puts a finger where it is, whichever way the chip is turned; taps
    that don't line up start it again"""
    import bridge
    import deskui
    for real in ([3900, 200, 3800, 300, True], [150, 3950, 250, 3850, False]):
        def raw_of(x, y):                                            # the chip's reading at screen point (x, y)
            rx = real[0] + x * (real[1] - real[0]) / (desk.W - 1)
            ry = real[2] + y * (real[3] - real[2]) / (desk.H - 1)
            return (round(ry), round(rx)) if real[4] else (round(rx), round(ry))
        i = desk.CAL_INSET
        targets = [(i, i), (desk.W - 1 - i, i), (i, desk.H - 1 - i), (desk.W - 1 - i, desk.H - 1 - i)]
        cal = desk.calibration([raw_of(*p) for p in targets])
        assert cal[4] == real[4] and all(abs(a - b) <= 2 for a, b in zip(cal[:4], real[:4]))
        assert desk.to_screen(*raw_of(100, 200), cal) == (100, 200)
    assert desk.calibration([(2000, 2000)] * 4) is None
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS, touch_cal=list(bridge.DEFAULTS["touch_cal"])))
    app.save_settings = lambda: None
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    t = desk.Touch(FakeTimer(), FakeDisplay(), app.state["touch_cal"], ui=ui,
                   ui_touch=lambda kind, x, y: ui.touch(kind, x, y, 0.0))
    ui.calibrate()
    ui.render(0.0, force=True)
    assert not ui.widgets                                            # nothing to tap by mistake

    def finger(rx, ry):
        for down in (True, False):
            t.event(desk.EV_ABS, desk.ABS_X, rx, 0)
            t.event(desk.EV_ABS, desk.ABS_Y, ry, 0)
            t.event(desk.EV_KEY, desk.BTN_TOUCH, int(down), 0)
            t.event(desk.EV_SYN, 0, 0, 0)
    for _ in range(4):
        finger(2000, 2000)                                           # the same spot four times: again
    assert ui.cal == [] and "again" in app.message
    for raw in ((3700, 3800), (3700, 300), (350, 3800), (350, 300)):
        finger(*raw)
    assert ui.cal is None and app.state["touch_cal"] == t.cal and t.cal != bridge.DEFAULTS["touch_cal"]
    assert desk.to_screen(3700, 3800, t.cal) == (desk.CAL_INSET, desk.CAL_INSET)


def test_headphone_jack():
    """the Studio's 3.5 mm jack: found in PipeWire, the sound to the speaker, the jack or both, the click and
    previews to the jack only with cue on, its own level"""
    import bridge
    sink = {"id": 5, "name": "bluez_output.AA", "label": "Speaker"}
    objects = [{"type": "PipeWire:Interface:Node", "id": 9, "info": {"props": {
        "media.class": "Audio/Sink", "node.name": "alsa_output.platform-bcm2835_audio.stereo-fallback",
        "node.description": "Built-in Audio Stereo", "alsa.card_name": "bcm2835 Headphones"}}},
        {"type": "PipeWire:Interface:Node", "id": 10, "info": {"props": {
            "media.class": "Audio/Sink", "node.name": "alsa_output.platform-fef00700.hdmi.hdmi-stereo",
            "alsa.card_name": "vc4-hdmi"}}}]
    jack = bridge.jack_node(objects)
    assert jack["id"] == 9 and bridge.jack_node(objects[1:]) is None
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    app.sink, app.jack = sink, jack
    assert app.outputs() == (sink["name"], jack["name"])            # both: the speaker, and a copy on the jack
    app.state["output"] = "phones"
    assert app.outputs() == (jack["name"], None)
    app.state["output"] = "speaker"
    assert app.outputs() == (sink["name"], None) and app.cue_target() == sink["name"]
    app.state["cue"] = True
    assert app.cue_target() == jack["name"]                          # the click: the jack only
    app.sink = None
    assert app.outputs() == (jack["name"], None)                     # no speaker: the jack
    app.settings_menu = "audio"
    keys = [r[0] for r in app.settings_rows()]
    assert "output" in keys and "cue" in keys
    app.setting = keys.index("output")
    app.change_setting()
    assert app.state["output"] == "phones"
    app.jack = None
    assert "output" not in [r[0] for r in app.settings_rows()]      # the Pocket: no jack, no rows
    app.set_phones(150)
    assert app.state["phones"] == 100


def test_cue_sources():
    """a DJ mixer's cue: a source tapped CUE on the MIX tab plays on the jack before its fader, its own loopback;
    the blend of cue and main mix; the main mix copied to the jack even with output "speaker" while cueing"""
    import bridge
    import deskui
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS, cues=[]))
    app.save_settings = lambda: None
    app.sink = {"id": 5, "name": "bluez_output.AA", "label": "Speaker"}
    app.jack = {"id": 9, "name": "alsa_output.bcm2835", "label": "PHONES"}
    app.source = {"id": 1, "name": "fm1a", "label": "Felucca", "ports": []}
    app.source_b = {"id": 3, "name": "fm1b", "label": "Felucca", "ports": []}
    app.state["output"] = "speaker"
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("MIX")
    ui.render(0.0, force=True)
    assert app.outputs() == ("bluez_output.AA", None) and app.cue_levels() == (100, 100)

    def tap(key):
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
        ui.render(0.0, force=True)
    tap(("cue", "b"))
    assert app.cued() == ["b"] and app.outputs() == ("bluez_output.AA", "alsa_output.bcm2835")
    started = []
    app.cue_mixer.channels = {}
    ensure, bridge.Loopback.ensure = bridge.Loopback.ensure, (lambda self, source, sink, name=None, pair=None:
                                                              started.append((source, sink, name)))
    try:
        app.cue_mixer.ensure(app.jack["name"], [("b", "fm1b", None)])
    finally:
        bridge.Loopback.ensure = ensure
    assert started == [("fm1b", "alsa_output.bcm2835", "fm1-cue-b")]
    tap("cue-mix")                                                   # 50/50 -> 25/75: more of the main mix
    assert app.state["cue_mix"] == 75 and app.cue_levels() == (50, 100)
    for _ in range(2):
        tap("cue-mix")
    assert app.state["cue_mix"] == 0 and app.cue_levels() == (100, 0)   # the cued source only
    tap(("cue", "b"))
    assert app.cued() == [] and app.cue_levels() == (100, 100)
    app.state["cues"] = ["b"]
    app.jack = None
    assert app.cued() == []                                          # no jack: no cue


def test_hdmi_mirror():
    """a monitor on HDMI: the KMS framebuffer found (not the touchscreen's), the screen scaled up by a whole
    number and centred, in its pixel format; nothing while unplugged"""
    import numpy as np
    root = Path(tempfile.mkdtemp(prefix="fm1-fb-"))
    for name, fbname, size, bpp in (("fb0", "vc4drmfb", "1280,720", 32), ("fb1", "fb_ili9486", "480,320", 16)):
        (root / name).mkdir()
        (root / name / "name").write_text(fbname + "\n")
        (root / name / "virtual_size").write_text(size + "\n")
        (root / name / "bits_per_pixel").write_text(f"{bpp}\n")
    (root / "fb0" / "stride").write_text("5120\n")
    drm = root / "drm" / "card1-HDMI-A-1"
    drm.mkdir(parents=True)
    (drm / "status").write_text("disconnected\n")
    m = desk.HdmiMirror.find(skip="/dev/fb1", graphics=root, drm=root / "drm")
    assert m.device == "/dev/fb0" and m.scale == 2 and m.at == (160, 40) and m.bpp == 32
    assert not m.plugged(100.0)
    (drm / "status").write_text("connected\n")
    assert not m.plugged(101.0) and m.plugged(106.0)                 # looked at again after 5 s
    frame = np.zeros((desk.H, desk.W, 3), dtype=np.uint8)
    frame[0, 0] = (255, 128, 1)
    fb = root / "fb.bin"
    fb.write_bytes(bytes(5120 * 720))
    with open(fb, "r+b", buffering=0) as f:
        m.write(f, frame)
    data = fb.read_bytes()
    at = 40 * 5120 + 160 * 4
    assert data[at:at + 8] == bytes([1, 128, 255, 0, 1, 128, 255, 0])   # BGRX, twice as wide
    assert data[at + 5120:at + 5124] == bytes([1, 128, 255, 0])          # and twice as tall
    assert data[at + 8:at + 12] == bytes(4) and data[:4] == bytes(4)
    m16 = desk.HdmiMirror("/dev/fb0", (480, 320), 16, 960)
    assert m16.pixels(frame)[0, 0] == (31 << 11) | (32 << 5) | 0       # RGB565 when the monitor runs 16-bit


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
    def tab(name):                                                    # a tab, by the button that opens it
        box = next(w[0] for w in ui.widgets if w[3] == ("tab", name))
        tap((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    ui.render(now, force=True)
    assert app.display.image.size == (480, 320) and ui.theme == sloopstyle.Theme("GREEN")
    tab("MIX")
    assert ui.tab == "MIX"
    slider = next(w for w in ui.widgets if w[3] == ("slide", "u-smk37va"))
    box = slider[0]
    ui.touch("down", (box[0] + box[2]) // 2, box[3] - 10, now)        # the bottom: 0%
    assert app.mix_level("u-smk37va") == 0
    ui.touch("move", (box[0] + box[2]) // 2, box[1] + 10, now)        # dragged to the top: 200%
    ui.touch("up", (box[0] + box[2]) // 2, box[1] + 10, now)
    assert app.mix_level("u-smk37va") == 200
    tab("MIDI")
    cell = next(w for w in ui.widgets if w[3] == ("route", "BT>USB"))
    tap((cell[0][0] + cell[0][2]) // 2, (cell[0][1] + cell[0][3]) // 2)
    assert app.routes()["BT>USB"]["on"] is True                      # off at first: tapped on
    tap((cell[0][0] + cell[0][2]) // 2, (cell[0][1] + cell[0][3]) // 2, hold=0.8)
    assert app.screen == "route" and app.route_key == "BT>USB"            # held: its settings, a Studio page
    ui.render(now, force=True)
    assert not ui.classic and any(w[3] == "page-back" for w in ui.widgets)
    app.screen = "status"
    ui.render(now, force=True)
    tab("MORE")                                                       # then THEME round to AMBER
    ui.more_top = 5                                                    # scrolled to the end of MORE
    ui.render(now, force=True)
    row = next(w for w in ui.widgets if w[3] == ("more", "THEME"))
    tap(row[0][0] + 20, row[0][1] + 10)
    tap(row[0][0] + 20, row[0][1] + 10)
    assert app.state["desk_theme"] == "AMBER"
    ui.render(now + 5, force=True)
    assert ui.theme.name == "AMBER"


def test_tab_bar():
    """six tabs (MIXER, SAMPLER, RECORDER with the reel-to-reel meter ...); the meters open from MIXER and the
    DAW from MIDI, each with a back button, their tab lit under them"""
    import bridge
    import deskui
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.render(0.0, force=True)
    assert [w[3][1] for w in ui.widgets if isinstance(w[3], tuple) and w[3][0] == "tab"] ==         ["HOME", "MIX", "PADS", "REC", "MIDI", "MORE"] and deskui.TAB_LABEL["PADS"] == "SAMPLER"

    def press(key):
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, 0.0)
        ui.touch("up", box[0] + 4, box[1] + 4, 0.0)
        ui.render(0.0, force=True)
    for there, button, page, back in (("MIX", "meters", "METER", "meter-back"), ("MIDI", "daw", "DAW", "daw-back")):
        press(("tab", there))
        press(button)
        assert ui.tab == page and deskui.PARENT[page] == there
        assert any(w[3] == ("tab", there) for w in ui.widgets)      # the bar's still there, its tab lit
        press(back)
        assert ui.tab == there
    assert app.screen == "status"                                    # (the meters' reader let go)
    toggled = []
    app.toggle_recording = lambda: toggled.append(True)
    press(("tab", "REC"))                                            # RECORD: its own tab, the reels' levels on
    assert ui.tab == "REC" and app.screen == "meter" and deskui.TAB_LABEL["REC"] == "RECORDER" and deskui.TAB_LABEL["MIX"] == "MIXER"
    press("rec-go")
    assert toggled
    press("rec-takes")
    assert app.screen == "pick" or app.message == "No recordings yet"   # the takes (a Studio page), or none
    assert "RECORDINGS" not in [m[0] for m in deskui.MORE]


def test_four_usb_devices():
    """all four of the Studio's USB ports with sound and MIDI: every device its own mixer channel (two alike
    numbered), all of them and the sampler in Record parts (six sources), up to eight MIDI controllers"""
    import blemidi
    import bridge
    import deskui
    import meters

    def node(i, label, name):
        return {"type": "PipeWire:Interface:Node", "id": i, "info": {"props": {
            "media.class": "Audio/Source", "node.name": name, "alsa.card_name": label,
            "api.alsa.path": f"front:{i}"}}}
    objects = [node(10, "EP-136", "alsa_input.usb-EP-136-a"), node(11, "EP-136", "alsa_input.usb-EP-136-b"),
               node(12, "SMK-37+VA", "alsa_input.usb-SMK"), node(13, "Scarlett 2i2", "alsa_input.usb-Focusrite"),
               {"type": "PipeWire:Interface:Node", "id": 14, "info": {"props": {
                   "media.class": "Audio/Source", "node.name": "fm1-sampler"}}}]
    source, sink, source_b, others = bridge.pick_nodes(objects, ["felucca"])
    assert source is None and len(others) == 5
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    app.others = others
    keys = [k for k, _ in app.mix_channels()]
    assert len(set(keys)) == 5
    names = [app.channel_name(k) for k in keys]
    assert names[:2] == ["EP-136", "EP-136 #2"] and "SCARLETT 2" in names[3] and names[4] == "SAMPLER"
    assert len(meters.PARTS_MAP) // 2 >= len(keys)                    # Record parts: room for every source
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("MIX")
    ui.render(0.0, force=True)
    assert {("slide", k) for k in keys} <= {w[3] for w in ui.widgets}  # a fader each
    b = blemidi.MidiBridge(["felucca"], net=True)
    assert b.controllers.MAX == 8 and blemidi.MidiBridge(["felucca"]).controllers.MAX == 4


def test_flow_walk_fixes():
    """from the UI flow walk: header buttons touch the header's whole height, MORE fits one screen, HOME's
    SPEAKER row opens the speaker list, the tempo shows the one it plays at"""
    import bridge
    import deskui
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    scans = []
    app.start_scan = lambda: scans.append(True)
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("MIX")
    ui.render(0.0, force=True)
    meters_box = next(w[0] for w in ui.widgets if w[3] == "meters")
    assert meters_box[1] == 0 and meters_box[3] >= deskui.HDR_H                # the header's whole height
    ui.open_tab("MORE")
    ui.render(0.0, force=True)
    assert {m[0] for m in deskui.MORE} <= {w[3][1] for w in ui.widgets if isinstance(w[3], tuple)
                                           and w[3][0] == "more"}               # all of it, no scrolling
    ui.open_tab("HOME")
    ui.render(0.0, force=True)
    row = next(w[0] for w in ui.widgets if w[3] == ("row", "spk"))
    ui.touch("down", row[0] + 60, row[1] + 10, 0.0)
    ui.touch("up", row[0] + 60, row[1] + 10, 0.0)
    assert scans
    app.state["clock_mode"], app.state["bpm"] = "follow", 97
    assert ui.tempo_text() == "97 BPM"                                         # nothing to follow: its own


def test_theme_from_file_has_tuples():
    """a theme read back from its JSON file draws: its colours are tuples (Pillow 11 refuses lists: the
    Studio crashed on its first start with FM-1 A's theme saved)"""
    import json
    import sloopstyle
    t = sloopstyle.theme_load(json.loads(json.dumps(sloopstyle.theme_dict(sloopstyle.Theme("AMBER")))))
    assert all(isinstance(c, tuple) for c in t.c + t.track + [t.bg, t.hi] + t.dim + t.grey)


def test_clipper_smk37_next_to_fm1s():
    """the Studio's real cards (2026-10-24): two FM-1s and an SMK-37 running Clipper, which has the FM-1's name
    and USB ID. Next to real FM-1s a Clipper is an instrument: not FM-1 A, its MIDI a USB keyboard, its sound a
    mixer channel; alone it's the FM-1"""
    import blemidi
    import bridge
    import sloop
    cards = """ 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 2 [Felucca        ]: USB-Audio - Felucca
                      Hügelton Instruments Felucca at usb-3f980000.usb-1.2, full speed
 3 [Felucca_1      ]: USB-Audio - Felucca
                      Hügelton Instruments Felucca at usb-3f980000.usb-1.1.3, full speed
 4 [Clipper        ]: USB-Audio - Clipper
                      Hügelton Instruments Clipper at usb-3f980000.usb-1.1.2, full speed
"""
    assert sloop.fm1_cards(cards) == [3, 2]                          # A on the hub's port 3, B: no Clipper
    lines = cards.splitlines()
    alone = "\n".join(lines[:2] + lines[-2:]) + "\n"
    assert sloop.fm1_cards(alone) == [4]                             # a lone Clipper: the FM-1

    def node(i, card, label):
        return {"type": "PipeWire:Interface:Node", "id": i, "info": {"props": {
            "media.class": "Audio/Source", "node.name": f"alsa_input.usb-H__gelton_Instruments_{label}-0{card}",
            "node.description": "pid.codes Test PID Analog Stereo", "alsa.card_name": label,
            "api.alsa.path": f"front:{card}"}}}
    objects = [node(10, 2, "Felucca"), node(11, 3, "Felucca"), node(12, 4, "Clipper")]
    a, _, b, others = bridge.pick_nodes(objects, ["felucca", "sloop"], [3, 2])
    assert (a["card"], b["card"]) == (3, 2) and [o["label"] for o in others] == ["Clipper"]
    names = ["Felucca:Felucca MIDI 1 24:0", "Felucca:Felucca MIDI 1 28:0", "Clipper:Clipper MIDI 1 32:0"]
    fm1_a = blemidi.UsbMidi(["felucca"], lambda m: None)
    assert fm1_a.pick(names, [3, 2]) == names[1]                     # card 3's port
    assert blemidi.UsbMidi(["felucca"], lambda m: None).pick(names, [4]) == names[2]   # a lone Clipper's
    keys = blemidi.UsbControllers(fm1_a, lambda m: None)
    real = sloop.fm1_cards
    sloop.fm1_cards = lambda text=None: [3, 2]
    try:
        assert list(keys.wanted(names)) == ["Clipper"]               # the SMK-37: a keyboard
    finally:
        sloop.fm1_cards = real


def test_midi_tab_with_sampler_and_daw():
    """the MIDI tab as the real Studio has it: two FM-1s, the sampler (SMP) and a DAW (NET). It crashed the app
    on the Studio (2026-10-24: no label for SMP), restarting it every time MIDI was opened"""
    import bridge
    import deskui

    class Midi:
        pads, net, mcu, usb_b, clock = True, ["MacBook"], None, "Felucca", None
        controllers = ["Clipper"]

        def set_routes(self, routes):
            pass
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings, app.midi = (lambda: None), Midi()
    sources, dests = app.matrix_lists()
    assert "SMP" in dests and "NET" in dests and "NET" in sources and "FM-1 B" in dests
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("MIDI")
    ui.render(0.0, force=True)                                       # draws: no KeyError
    assert ("route", "USB>SMP") in [w[3] for w in ui.widgets] and ("route", "NET>FM-1") in [w[3] for w in ui.widgets]


def test_each_usb_midi_device_its_own_route():
    """the Studio: each USB MIDI device a row and column of its own, by name (the SMK-37 running Clipper and the
    Sidekick, EP-136), USB's routes to start with, routed apart, kept when unplugged; the Pocket keeps USB"""
    import blemidi
    import bridge
    import deskui
    assert blemidi.route_default("U:EP-136>FM-1")["on"] and not blemidi.route_default("FM-1>U:EP-136")["on"]
    assert not blemidi.route_default("U:Clipper>U:EP-136")["on"]               # device to device: off at first
    assert blemidi.matrix(["Clipper", "EP-136"])[0] == ("BT", "U:Clipper", "U:EP-136", "NET", "FM-1", "FM-1 B", "Box")
    assert blemidi.matrix()[0] == blemidi.SOURCES                               # the Pocket: USB, as one
    cleaned = blemidi.clean_routes({"U:EP-136>FM-1": {"on": False}})
    assert cleaned["U:EP-136>FM-1"]["on"] is False and "BT>FM-1" in cleaned
    for bad in ({"U:EP-136>U:EP-136": {}}, {"X:y>FM-1": {}}, {"U:>FM-1": {}}):
        try:
            blemidi.clean_routes(bad)
            assert False, bad
        except ValueError:
            pass
    b = blemidi.MidiBridge(["felucca"], net=True)                              # the MIDI process, the Studio's
    sent, to_dev = [], []
    b.usb.send_messages = lambda m: sent.extend(m)
    b.usb_b.send_messages = lambda m: None
    b.controllers.send_to = lambda name, m: to_dev.append((name, list(m)))
    b.controllers.names = lambda: ["Clipper", "EP-136"]
    b.to_ble = lambda m: None
    b.set_routes({"U:EP-136>FM-1": {"on": False}})
    b.from_controllers(bytes([0x90, 60, 100]), "EP-136")                     # the Sidekick: not to FM-1 A
    assert sent == []
    b.from_controllers(bytes([0x90, 62, 100]), "Clipper")                    # the SMK-37: to FM-1 A
    assert sent == [bytes([0x90, 62, 100])] or sent == [[0x90, 62, 100]]
    b.set_routes({"FM-1>U:Clipper": {"on": True}})
    b.from_fm1("FM-1", bytes([0x90, 64, 90]))                                 # FM-1 A back to the SMK-37 alone
    assert [n for n, _ in to_dev] == ["Clipper"]

    class Midi:
        pads, net, mcu, usb_b, clock, split = True, [], None, "Felucca", None, True
        controllers = ["Clipper", "EP-136"]
        peer, enabled, error = None, True, None

        def set_routes(self, routes):
            self.last = routes

        def usb_label(self):
            return "Felucca"
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings, app.midi = (lambda: None), Midi()
    sources, dests = app.matrix_lists()
    assert "U:EP-136" in sources and "U:Clipper" in dests and "USB" not in sources + dests
    app.set_route_of("U:EP-136>FM-1 B", on=False)
    app.midi.controllers = ["Clipper"]                                        # the Sidekick unplugged
    app.set_route_of("U:Clipper>FM-1", on=False)
    assert app.state["routes"]["U:EP-136>FM-1 B"]["on"] is False               # kept for when it's back
    app.midi.controllers = ["Clipper", "EP-136"]
    assert app.routes()["U:EP-136>FM-1 B"]["on"] is False
    app.route_key = "U:EP-136>FM-1 B"
    assert app.route_title() == "EP-136 > FM-1 B"
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("MIDI")
    ui.render(0.0, force=True)
    assert ("route", "U:Clipper>FM-1 B") in [w[3] for w in ui.widgets]
    web = app.web_status()                                                    # the web app gets the same grid
    assert "U:EP-136" in web["matrix"]["sources"] and "U:EP-136>FM-1 B" in web["routes"]
    app.midi.split = False                                                    # the Pocket: USB as before
    assert "USB" in app.matrix_lists()[0]


def test_meters_steady():
    """the level bars rise at once and fall back at FALL dB a second whatever the frame rate; the screen gets
    each changed strip on its own (a meter's two bars, not the scale between them)"""
    import deskmeters
    import meters as m
    dm = deskmeters.DeskMeters()
    assert dm.ballistic(0, 32768 * 0.5, 0.05) > -7                   # -6 dBFS: at once
    a = dm.ballistic(0, 1, 0.1)                                       # silence: falls 2.6 dB in 0.1 s
    assert abs(a - (dm.shown[0])) < 1e-9 and -9 < a < -8
    b = deskmeters.DeskMeters()
    b.ballistic(0, 32768 * 0.5, 0.05)
    for _ in range(4):                                                # four frames of 0.025 s = one of 0.1 s
        b.ballistic(0, 1, 0.025)
    assert abs(b.shown[0] - a) < 1e-6
    assert m.FLOOR == dm.ballistic(1, 0, 0.1)
    rows = [False] * 320
    for r in list(range(60, 120)) + list(range(130, 190)) + [195]:
        rows[r] = True
    assert desk.changed_strips(rows) == [(60, 120), (130, 196)]     # (a 10-row gap kept apart, a 4-row one joined)


def test_lost_input_pair_relinked():
    """an interface's chosen pair (the Sidekick's 1-2) whose links didn't get made (a busy restart) is linked
    again on the next look at PipeWire, at most every 5 s; linked, nothing is done"""
    import bridge
    loop = bridge.Loopback()
    loop.links = [("alsa_input.ep136", "capture_FL", "fm1-in-fm1-mix-u-ep136", "input_FL"),
                  ("alsa_input.ep136", "capture_FR", "fm1-in-fm1-mix-u-ep136", "input_FR")]
    loop.started = 100.0
    loop.running = lambda: True
    made = []
    real, bridge.link_ports = bridge.link_ports, lambda links: made.append(list(links))
    try:
        node = {"type": "PipeWire:Interface:Node", "id": 50,
                "info": {"props": {"node.name": "fm1-in-fm1-mix-u-ep136"}}}
        assert not loop.heal([node], now=101.0)                       # too soon after start
        assert loop.heal([node], now=103.0) and len(made) == 1        # no links: made again
        assert not loop.heal([node], now=105.0)                       # (not again within 5 s)
        links = [{"type": "PipeWire:Interface:Link", "id": 60, "info": {"input-node-id": 50, "state": "active"}},
                 {"type": "PipeWire:Interface:Link", "id": 61, "info": {"input-node-id": 50, "state": "active"}}]
        assert not loop.heal([node] + links, now=120.0) and len(made) == 1   # linked: nothing to do
        idle = [dict(link, info=dict(link["info"], state="paused")) for link in links]
        assert not loop.heal([node] + idle, now=130.0)                # both paused: nothing playing, left alone
        destroyed = []
        real_run, bridge.run = bridge.run, lambda cmd, timeout=None: (destroyed.append(cmd), (0, ""))[1]
        try:
            one_stuck = [links[0], idle[1]]                           # the right side stuck while the left plays
            assert loop.heal([node] + one_stuck, now=140.0) and len(made) == 2
            assert destroyed == [["pw-cli", "destroy", "61"]]
        finally:
            bridge.run = real_run
    finally:
        bridge.link_ports = real


def test_frames_paced_to_the_spi_link():
    """no frame is written while the last is still going to the screen (tearing): its time from the rows sent
    and the link's speed (the dtoverlay's speed=)"""
    cfg = Path(tempfile.mkdtemp()) / "config.txt"
    cfg.write_text("dtparam=spi=on\ndtoverlay=piscreen,speed=32000000,rotate=90\n")
    assert desk.spi_hz(cfg) == 32_000_000 and desk.spi_hz(cfg.parent / "none.txt") == 16_000_000
    with tempfile.TemporaryDirectory() as tmp:
        fb = Path(tmp) / "fb"
        fb.write_bytes(bytes(480 * 320 * 2))
        screen = desk.TftDisplay(str(fb))
        screen.hz = 16_000_000
        img = Image.new("RGB", (480, 320), (10, 20, 30))
        screen.show_rgb(img)
        full = screen.busy_until - time.monotonic()
        assert 0.15 < full < 0.25                                    # 320 rows at 16 MHz, + 1/30 s
        t0 = time.monotonic()
        img.paste((200, 0, 0), (0, 100, 480, 110))                    # 10 rows changed
        screen.show_rgb(img)
        assert time.monotonic() - t0 >= full - 0.02                   # waited for the first to be sent
        assert screen.busy_until - time.monotonic() < 0.06           # 10 rows: quick
        screen.fb.close()


def test_studio_has_no_tuner():
    """the Studio's meters step past the tuner both ways and count without it; left on it, the next shows"""
    import bridge
    import deskui
    import meters
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("METER")                                             # (meters change while their screen is open)
    seen = []
    for _ in range(len(meters.METERS) + 2):
        ui.next_meter(1)
        seen.append(meters.METERS[app.meter_index()])
    assert "Tuner" not in seen and len(set(seen)) == len(meters.METERS) - 1
    for _ in range(len(meters.METERS) + 2):
        ui.next_meter(-1)
        assert meters.METERS[app.meter_index()] != "Tuner"
    app.state["meter"] = meters.METERS.index("Tuner")
    ui.open_tab("METER")
    ui.render(0.0, force=True)
    assert meters.METERS[app.meter_index()] != "Tuner"


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


def test_native_pages():
    """no Pocket screens on the Studio: a settings section, a value with - / +, a confirmation held, back"""
    import bridge
    import deskui
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.save_settings = lambda: None
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12

    def press(key, now=0.0, hold=0.0):
        box = next(w[0] for w in ui.widgets if w[3] == key)
        ui.touch("down", box[0] + 4, box[1] + 4, now)
        if hold:
            ui.render(now + hold, force=True)                          # still held: the confirm fires
        else:
            ui.touch("up", box[0] + 4, box[1] + 4, now)
        ui.render(now + hold, force=True)
    ui.open_tab("MORE")
    ui.render(0.0, force=True)
    press(("more", "PLAYING"))
    assert app.screen == "settings" and app.settings_menu == "play"
    rows = [r[0] for r in app.settings_rows()]
    press(("row", rows.index("tempo")))                                # tempo: being edited, - / + on its row
    assert app.editing == "tempo"
    bpm = app.state["bpm"]
    press(("plus", rows.index("tempo")))
    press(("plus", rows.index("tempo")))
    assert app.state["bpm"] == bpm + 2
    press(("row", rows.index("tempo")))                                # tapped again: done
    assert app.editing is None
    press("page-back")                                                 # out of the section: MORE again
    assert app.screen == "status" and ui.tab == "MORE"
    ui.more_top = 6
    ui.render(0.0, force=True)
    done = []
    app.shutdown = lambda: done.append(True)
    press(("more", "SHUT DOWN"))
    assert app.screen == "confirm"
    press("confirm", now=10.0, hold=0.5)                               # let go too soon: nothing
    assert not done
    press("confirm", now=20.0, hold=2.1)                               # held 2 s: confirmed
    assert done


def test_daw_surface():
    """the DAW tab: Kraken's names and faders shown, a fader dragged and PLAY pressed sent back as Mackie Control"""
    import bridge
    import deskui

    class Midi:
        enabled, error, peer, usb_b, net, notes, chans, clock = True, None, None, None, ["Mac"], [], 0, None

        def __init__(self):
            names = "".join(f"{n:<6} " for n in ["BASS", "KEYS"] + [""] * 6)
            self.mcu = {"faders": [8192] * 9, "leds": [0x5E], "lcd": [names, " " * 56], "seen": 1.0}
            self.sent = []

        def mcu_send(self, messages):
            self.sent += messages

        def usb_label(self):
            return "Felucca"
    app = bridge.App(RgbCapture(), dict(bridge.DEFAULTS))
    app.midi = Midi()
    ui = deskui.DeskUI(app, app.display)
    ui.theme_check = 1e12
    ui.open_tab("DAW")
    ui.render(0.0, force=True)
    fader = next(w[0] for w in ui.widgets if w[3] == ("fader", 1))
    ui.touch("down", (fader[0] + fader[2]) // 2, fader[3] - 10, 0.0)              # KEYS to the bottom
    ui.touch("up", 0, 0, 0.0)
    play = next(w[0] for w in ui.widgets if w[3] == ("daw", "play"))
    ui.touch("down", play[0] + 5, play[1] + 5, 0.0)
    ui.touch("up", play[0] + 5, play[1] + 5, 0.0)
    assert app.midi.sent == [bytes([0x90, 0x69, 127]), bytes([0xE1, 0, 0]),     # finger on KEYS' fader, moved,
                             bytes([0x90, 0x69, 0]), bytes([0x90, 0x5E, 127])]    # lifted; then PLAY
    pan = next(w[0] for w in ui.widgets if w[3] == ("pan+", 0))
    ui.touch("down", pan[0] + 2, pan[1] + 2, 0.0)
    ui.touch("up", pan[0] + 2, pan[1] + 2, 0.0)
    assert app.midi.sent[-1] == bytes([0xB0, 0x10, 2])                          # BASS panned right


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
