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
    clock = {"bpm": 120, "tempo": 120.0, "source": "box", "playing": True, "bar": time.monotonic() - 0.55}
    notes, chans = [60, 64, 67], (1 << 0) | (1 << 9)     # a C major chord; channels 1 and 10 busy
    controllers = ["SMK-37+VA"]                           # a USB keyboard on a hub

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


class Playing:
    """meters.Player stand-in: 12 s into a 47 s take"""
    length = 47.0

    def playing(self):
        return True

    def seconds(self, now=None):
        return 12.0

    def stop(self):
        pass


class Loop:
    def running(self):
        return True

    def stop(self):
        pass


def scenes(app):
    """(file name, how to set the app up) for each screen worth showing."""
    def status():
        app.battery = {"percent": 78, "mv": 3818, "plugged": False}
        app.midi.peer = None                  # the SMK-37 on USB alone

    def charging():
        app.battery = {"percent": 64, "mv": 3790, "plugged": True}

    def recording():
        app.recorder = Recording()

    def settings():
        app.screen, app.setting = "settings", 0

    def settings_play():
        app.screen, app.setting, app.settings_menu = "settings", 0, "play"

    def settings_sloop():
        app.screen, app.setting, app.settings_menu = "settings", 0, "sloop"

    def settings_midi():
        app.screen, app.setting, app.settings_menu = "settings", 2, "midi"

    def settings_fm1():
        app.screen, app.setting, app.settings_menu = "settings", 0, "fm1"

    def volume():
        app.volume, app.volume_shown = 64, time.monotonic() + 60

    def matrix():
        app.screen, app.mx = "matrix", [0, 0]

    def matrix_two():                         # a second FM-1 on the hub: its row and column
        app.midi.usb_b = "Felucca:Felucca MIDI 1 28:0"
        routes = blemidi_routes()
        routes["USB>FM-1 B"]["on"] = False    # the SMK-37 plays A, Bluetooth plays B
        routes["BT>FM-1"]["on"] = False
        app.state["routes"] = routes
        app.screen, app.mx = "matrix", [1, 1]

    def settings_mixer():                     # FM-1 A and B and the SMK-37's own synth
        app.source_b = dict(app.source, id=3, name="alsa_input.usb-Felucca-00.2")
        app.others = [{"id": 4, "name": "alsa_input.usb-SMK-37", "label": "SMK-37+VA"}]
        app.state["mix"] = {"a": 100, "b": 80, "usb": 175}
        app.screen, app.settings_menu, app.setting = "settings", "audio", 5

    def route():
        import blemidi
        routes = blemidi.default_routes()
        routes["BT>FM-1"].update(transpose=12, velocity="soft")
        app.state["routes"] = routes
        app.screen, app.route_key, app.route_row = "route", "BT>FM-1", 2

    def recordings():
        import library as lib
        take = "2026-10-22_081530_fm1.wav"
        (lib.folder("recordings") / take).write_bytes(b"RIFF" + bytes(40) + bytes(192000 * 47))
        app.pick_recording(take)

    def player():
        app.player, app.player_name, app.screen = Playing(), "2026-10-22_081530_fm1.wav", "player"

    def editor(field="start"):
        import sampleui
        app.ed = {"name": TAKE.name, "path": TAKE, "field": 0, "loading": True, "error": None}
        app.screen = "editor"
        app.editor_load(app.ed)               # here and now, not in the background
        app.ed["field"] = [f for f, _ in sampleui.FIELDS].index(field)
        app.ed["fade_out"] = 0.25

    def editor_note():
        editor("note")

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

    return [("status", status), ("status-charging", charging), ("status-rec", recording), ("settings", settings),
            ("settings-play", settings_play), ("settings-sloop", settings_sloop), ("settings-fm1", settings_fm1), ("settings-midi", settings_midi), ("status-volume", volume),
            ("matrix", matrix), ("matrix-two", matrix_two), ("settings-mixer", settings_mixer), ("route", route), ("speakers", speakers),
            ("pairing", pairing), ("screensaver", saver), ("recordings", recordings),
            ("editor", editor), ("editor-note", editor_note),
            ("player", player), ("job", job)]


def blemidi_routes():
    import blemidi
    return blemidi.default_routes()


def write_take(path):
    """a recording to edit: a breath of silence, four plucked notes dying away, silence"""
    import wave
    np = meters.numpy() or __import__("numpy")
    rate, out = 44100, [np.zeros(int(44100 * 0.7))]
    for hz in (110.0, 146.8, 130.8, 98.0):
        t = np.arange(int(rate * 0.9)) / rate
        out.append(sum(np.sin(2 * np.pi * hz * k * t) / k for k in range(1, 5)) * np.exp(-t * 4.5) * 14000)
    out.append(np.zeros(int(rate * 1.2)))
    x = np.concatenate(out).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(x, 2).tobytes())
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir")
    parser.add_argument("--fonts", default="/usr/share/fonts/truetype/dejavu")
    parser.add_argument("--desk", action="store_true", help="also the desk box's touch screens and meters (desk-*.png)")
    args = parser.parse_args()
    fonts = Path(args.fonts)
    bridge.font = lambda size, bold=False: ImageFont.truetype(
        str(fonts / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    state = dict(bridge.DEFAULTS, last_name="JBL Go 3", last_mac="00:00:00:00:00:00", clock_mode="box")
    global TAKE
    import library as lib
    meters.load_numpy()
    TAKE = write_take(lib.folder("recordings") / "2026-10-22_101544_fm1.wav")
    for name, setup in scenes(bridge.App(Capture(), state)):
        display = Capture()
        app = bridge.App(display, dict(state))
        app.loop, app.midi, app.wifi = Loop(), Midi(), False
        app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
        app.fw_label = "SLOOP 2.8"          # what the FM-1 says it runs: IN shows this, not its USB name
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
    if args.desk:
        desk_screens(state, out)


class RgbCapture:
    """the desk box's touchscreen stand-in: keeps the last full-colour frame"""

    def __init__(self):
        self.image, self.theme = None, None

    def show_rgb(self, img):
        self.image = img.copy()

    def show(self, img):
        pass


def desk_screens(state, out):
    """The Studio's touch screens (desk-ui-NAME.png), in SLOOP's GREEN and in a custom theme like FM-1 A's
    (desk-ui-NAME-theme.png), as the real Studio runs: two FM-1s, an SMK-37 running Clipper, a K.O. Sidekick
    (EP-136), the sampler with a kit and a beat, the headphone jack, a speaker. The tabs, then the sampler's pages
    (SEQ, FX, the sample editor), the recorder and the boot screen"""
    import deskboot
    import sloopstyle
    meters.load_numpy()
    custom = sloopstyle.Theme("MY THEME", [(8, 36, 48), (24, 76, 96), (40, 124, 168), (56, 180, 240),
                                           (144, 212, 248)], None, (40, 60, 64), (248, 252, 248))
    samples = studio_samples()
    pages = [("home", "HOME", {}), ("mix", "MIX", {}), ("pads", "PADS", {}), ("rec", "REC", {}),
             ("midi", "MIDI", {}), ("more", "MORE", {}), ("seq", "PADS", {"seq_open": True}),
             ("fx", "PADS", {"fx_open": True, "fx_on": 1, "fx_latch": True}), ("editor", "PADS", {"editor": True})]
    for suffix, theme in (("", sloopstyle.Theme("GREEN")), ("-theme", custom)):
        for name, tab, attrs in pages:
            attrs = dict(attrs)                       # (each theme's pass: its own copy)
            display = RgbCapture()
            app, ui = studio_app(state, display, theme, samples)
            ui.open_tab(tab)
            if attrs.pop("editor", False):
                ui.browse_edit(samples[9], "samples")
                while app.ed["loading"]:
                    time.sleep(0.05)
            for key, value in attrs.items():
                setattr(ui, key, value)
            for _ in range(4 if tab in ("METER", "REC") else 1):
                ui.render(time.monotonic(), force=True)
                time.sleep(0.1)
            display.image.save(out / f"desk-ui-{name}{suffix}.png", optimize=True)
            print("wrote", out / f"desk-ui-{name}{suffix}.png")
        boot = deskboot.frame(theme, 3)
        boot.save(out / f"desk-ui-boot{suffix}.png", optimize=True)
        print("wrote", out / f"desk-ui-boot{suffix}.png")
    desk_meters_sheet(state, out, custom)


def studio_samples():
    """a kit's worth of short samples in the (empty) library: their names show on the pads and in the editor"""
    import numpy as np
    import wave
    import library as lib
    names = ["kick_C2", "snare", "clap", "chat", "ohat", "rim", "tom-lo", "tom-hi", "crash", "vox-chop_A3",
             "stab_C3", "bass_E1"]
    t = np.arange(24000) / 48000
    for i, name in enumerate(names):
        path = lib.folder("samples") / f"{name}.wav"
        if not path.exists():
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(48000)
                tone = np.sin(2 * np.pi * (60 + 40 * i) * t * (1 + t)) * np.exp(-t * (4 + i))
                w.writeframes((tone * 20000).astype("<i2").tobytes())
    return [f"{n}.wav" for n in names]


def studio_app(state, display, theme, samples):
    import deskui
    import library as lib
    import pads

    class Pads:
        sounding, step = [0, 5], 6

        def send(self, *msg):
            pass
    app = bridge.App(display, dict(state, rec_parts=True, cues=["u-clipper"], cue_mix=50, desk_theme="follow"))
    app.loop, app.midi, app.wifi = Loop(), Midi(), True
    app.midi.usb_b = "Felucca:Felucca MIDI 1 28:0"
    app.midi.split, app.midi.pads, app.midi.net = True, True, []
    app.midi.controllers = ["Clipper", "EP-136"]
    app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
    app.source_b = {"id": 3, "name": "alsa_input.usb-Felucca.2", "label": "Felucca"}
    app.others = [{"id": 4, "name": "alsa_input.usb-Clipper", "label": "Clipper"},
                  {"id": 5, "name": "alsa_input.usb-EP-136", "label": "EP-136", "ports": [f"capture_AUX{i}" for i in range(8)]},
                  {"id": 6, "name": "fm1-sampler", "label": "SAMPLER"}]
    app.jack = {"id": 9, "name": "alsa_output.bcm2835", "label": "PHONES"}
    app.fw_label = "SLOOP 2.8"
    app.sink = {"id": 2, "name": "bluez_output.jbl", "label": "JBL Go 3"}
    app.volume, app.playing = 64, True
    app.battery = {"percent": 78, "mv": 3818, "plugged": False}
    app.levels = Levels()
    app.recorder, app.tape_done = Recording(), 0.35
    app.library_state = lambda now=None: None
    app.save_settings = lambda: None
    app.pads = Pads()
    kit = pads.empty_kit("BOOM BAP")
    for i, name in enumerate(samples):
        kit["pads"][i]["sample"] = str(lib.folder("samples") / name)
    seq = kit["seq"]
    seq["len"], seq["swing"] = 32, 55
    for i in range(0, 32, 4):
        seq["steps"][i].append([0, 0, 1])
    for i in range(2, 32, 2):
        seq["steps"][i].append([3, 1 if i % 4 else 0, 1])
    for i in (4, 12, 20, 28):
        seq["steps"][i].append([2, 0, 1])
    seq["steps"][14] = sorted(seq["steps"][14] + [[0, 1, 2]])
    app.state["kit"] = kit
    app.state["routes"] = {"U:EP-136>FM-1": {"on": False}, "U:EP-136>FM-1 B": {"transpose": 12}}
    ui = deskui.DeskUI(app, display)
    ui.current_theme = lambda now, theme=theme: setattr(ui, "theme", theme) or theme
    ui.theme = theme
    return app, ui


def desk_meters_sheet(state, out, theme):
    """The desk box's eleven meters (desk-meter-NAME.png) and all of them on one sheet (desk-meters.png)"""
    import deskui
    frames = []
    for i, name in enumerate(meters.METERS):
        if name in deskui.HIDDEN_METERS:              # (the Studio leaves these out)
            continue
        display = RgbCapture()
        app = bridge.App(display, dict(state, meter=i))
        app.loop, app.midi, app.wifi = Loop(), Midi(), False
        app.source = {"id": 1, "name": "alsa_input.usb-Felucca", "label": "Felucca"}
        app.sink = {"id": 2, "name": "bluez_output.jbl", "label": "JBL Go 3"}
        app.levels = Levels()
        app.library_state = lambda now=None: None
        if name == "Tape":
            app.recorder, app.tape_done = Recording(), 0.35
        ui = deskui.DeskUI(app, display)
        ui.current_theme = lambda now, theme=theme: setattr(ui, "theme", theme) or theme
        ui.theme = theme
        ui.open_tab("METER")
        if name == "History":
            ui.meters.hist = [-6 - 18 * abs(((k % 32) - 16) / 16) - (8 if k % 7 == 0 else 0) for k in range(160)]
            ui.meters.hnext = time.monotonic() + 60
        count = 60 if name == "Waterfall" else 5
        for _ in range(count):
            ui.render(time.monotonic(), force=True)
            if name != "Waterfall":
                time.sleep(0.1)
        frames.append(display.image)
        display.image.save(out / f"desk-meter-{name.lower()}.png", optimize=True)
        print("wrote", out / f"desk-meter-{name.lower()}.png")
    w, h, gap, cols = 480, 320, 12, 3
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols - 1) * gap, rows * h + (rows - 1) * gap), (255, 255, 255))
    for k, f in enumerate(frames):
        sheet.paste(f, ((k % cols) * (w + gap), (k // cols) * (h + gap)))
    sheet.save(out / "desk-meters.png", optimize=True)
    print("wrote", out / "desk-meters.png")


class Levels:
    """meters.LevelReader stand-in: a bass note, a chord and some hiss, louder on the left"""

    CHORDS = ((98, 392, 494), (110, 440, 554), (87, 349, 440), (131, 523, 659))   # G, A, F, C: moving music

    def __init__(self):
        self.frame, self.seq, self.target, self.proc = 0, 1, None, None
        self.block = self.make(0)

    def make(self, frame):
        np = meters.numpy()
        t = np.arange(meters.RECENT_N) / meters.RATE
        bass, a, b = self.CHORDS[(frame // 10) % len(self.CHORDS)]
        fade = 0.35 + 0.65 * (1 - (frame % 10) / 10)                 # each chord struck, then fading
        v = fade * (0.35 * np.sin(2 * np.pi * bass * t) + 0.15 * np.sin(2 * np.pi * a * t)
                    + 0.12 * np.sin(2 * np.pi * b * t) + 0.06 * np.sin(2 * np.pi * 2350 * t))             + 0.01 * np.random.default_rng(frame).standard_normal(len(t))
        return (np.stack([v, v * 0.7], axis=1) * 32767).astype(np.int16)

    def start(self, target):
        self.target, self.proc = target, True

    def running(self):
        return True

    def stop(self):
        pass

    def take(self):
        self.block = self.make(self.frame)
        self.frame += 1
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
        app.fw_label = "SLOOP 2.8"          # what the FM-1 says it runs: IN shows this, not its USB name
        app.levels = Levels()
        app.library_state = lambda now=None: None
        app.midi = Midi()
        if meters.METERS[i] == "Tape":        # the tape deck while it records
            app.recorder, app.tape_done = Recording(), 0.35
        app.open_meters()
        name = meters.METERS[i]
        if name == "History":                 # 50 s of a song's loudness, as if it had been playing
            app.m_hist = [-6 - 18 * abs(((k % 32) - 16) / 16) - (8 if k % 7 == 0 else 0) for k in range(128)]
            app.m_hnext = time.monotonic() + 60
        count = 54 if name == "Waterfall" else 4                     # the waterfall fills its 54 rows
        for _ in range(count):
            app.render(force=True)
            if name != "Waterfall":
                time.sleep(0.1)               # needles, peak holds and smoothing settle
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
