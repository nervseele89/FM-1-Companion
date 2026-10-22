"""Tests for the box's Library screens: key flows end in the right library.py job; every screen renders.

  python tests/test_libui.py [PNG_DIR]     (needs Pillow and mido; PNG_DIR gets a picture of each screen)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fm1-bt-bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
TMP = Path(tempfile.mkdtemp(prefix="fm1-ui-"))
os.environ.update(FM1_LIBRARY=str(TMP / "library"), XDG_RUNTIME_DIR=str(TMP / "run"), FM1_SD_LIBRARY=str(TMP / "sd"))

import bridge  # noqa: E402
import fake_fm1  # noqa: E402
import library  # noqa: E402
import sloop  # noqa: E402
from test_library import write_wav  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
shots = []


class Capture:
    def show(self, img):
        shots.append(img.copy())

    def contrast(self, value):
        pass

    def power(self, on):
        pass


class Loop:
    def running(self):
        return True

    def stop(self):
        pass


def make_app():
    app = bridge.App(Capture(), dict(bridge.DEFAULTS, last_name="JBL Go 3", last_mac="x"))
    app.loop, app.wifi = Loop(), True
    app.started = []
    app.start_job = lambda args, title, show=True: app.started.append(args)
    app.save_settings = lambda: None          # never this computer's real settings file
    return app


def snap(app, name):
    app.render(force=True)
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        shots[-1].convert("L").resize((512, 256)).save(OUT / f"{name}.png")


def keys(app, *names):
    for name in names:
        held = name.endswith("!")
        app.on_key(name.rstrip("!"), held)


def setup_library():
    fake = fake_fm1.FakeFM1()
    library.write_backup(sloop.Sloop(sloop.Link(mido=fake, time_scale=0.02)).backup(), "manual")
    write_wav(library.folder("samples") / "bass_C2.wav", 0.1)
    (library.folder("firmware") / "sloop-2.8.fwsc").write_bytes(b"x")
    backup = next(library.folder("backups").glob("*.json"))
    library.job_save_project(str(backup), "A", "Night Drive")
    return backup


def test_flows():
    backup = setup_library()
    app = make_app()
    keys(app, "right")
    assert app.screen == "settings" and [r[0] for r in app.settings_rows()] == ["g_audio", "g_midi", "g_clock",
                                                                                 "library", "wifi"]
    snap(app, "settings")
    keys(app, "press")                                     # Audio ›
    assert app.settings_menu == "audio" and [r[0] for r in app.settings_rows()] == ["speaker", "audio", "rate"]
    snap(app, "settings-audio")
    keys(app, "left")                                      # back to the top list, on Audio
    assert app.settings_menu is None and app.setting == 0 and app.screen == "settings"
    keys(app, "down", "down", "down")                      # Library ›
    keys(app, "press")
    assert app.screen == "library"
    snap(app, "library")
    keys(app, "press")                                     # Back up now
    assert app.started[-1] == ["backup"]
    keys(app, "down", "press")                             # Restore ›
    assert app.screen == "pick" and app.pick["title"] == "Restore"
    snap(app, "restore-pick")
    keys(app, "press")
    assert app.screen == "confirm"
    snap(app, "restore-confirm")
    keys(app, "press")                                     # a short press doesn't confirm
    assert app.started[-1] == ["backup"]
    keys(app, "press!")
    assert app.started[-1] == ["restore", str(backup)]
    # load project: from the backup's project A into C
    app.screen, app.lib_cursor = "library", 2
    keys(app, "press")
    assert app.screen == "pick" and app.pick["items"][0][0] == "Night Drive"
    snap(app, "project-source")
    keys(app, "down", "press")                             # the backup
    assert app.pick["title"] == "Which project"
    keys(app, "press")                                     # its first project
    assert app.pick["title"] == "Load into"
    snap(app, "project-target")
    keys(app, "down", "down", "down", "press", "press!")   # C
    assert app.started[-1][:2] == ["load-project", str(backup)] and app.started[-1][3] == "C", app.started[-1]
    # sample into USR2
    app.screen, app.lib_cursor = "library", 3
    keys(app, "press", "press", "down", "press")
    snap(app, "sample-confirm")
    keys(app, "press!")
    assert app.started[-1][:3] == ["load-sample", "2", "bass_C2"], app.started[-1]
    # firmware: the download row first (Wi-Fi on), then the file
    app.screen, app.lib_cursor = "library", 4
    keys(app, "press")
    assert app.pick["items"][0][0] == "Get SLOOP update"
    snap(app, "firmware-pick")
    keys(app, "press")
    assert app.started[-1] == ["fetch", "sloop"]
    app.screen, app.pick["cursor"] = "pick", 0
    keys(app, "down", "press")
    assert app.started[-1] == ["fetch", "clipper"]
    app.screen, app.pick["cursor"] = "pick", 0
    keys(app, "down", "down", "press")
    snap(app, "firmware-confirm")
    keys(app, "press!")
    assert app.started[-1][0] == "flash" and app.started[-1][1].endswith("sloop-2.8.fwsc")
    # left goes back up
    app.screen = "confirm"
    keys(app, "left")
    assert app.screen == "pick"
    keys(app, "left")
    assert app.screen == "library"
    keys(app, "left")
    assert app.screen == "settings"


def test_auto_backup_schedule():
    app = make_app()

    class Midi:                                            # the FM-1's MIDI is there
        enabled, error, peer, pairing_until = True, None, None, 0.0

        def usb_label(self):
            return "Felucca"

        def activity(self):
            return 0.0

    app.midi, app.job_busy = Midi(), lambda: None
    app.autobackup_flag.unlink(missing_ok=True)
    # the menu row cycles off -> at start -> 2 -> 5 -> 10 -> 30 -> off ...
    app.screen, app.lib_cursor = "library", 6
    assert app.library_rows()[6][2] == "at start"
    seen = []
    for _ in range(6):
        keys(app, "press")
        seen.append(app.library_rows()[6][2])
    assert seen == ["2 min", "5 min", "10 min", "30 min", "off", "at start"], seen
    snap(app, "library-auto")
    # at start: one backup, then none
    app.started.clear()
    app.auto_backup()
    app.auto_backup()
    assert app.started == [["backup", "--auto"]]
    # every 2 minutes
    app.state["auto_backup"] = 2
    app.last_auto -= 60
    app.auto_backup()
    assert len(app.started) == 1                           # only 1 minute so far
    app.last_auto -= 61
    app.auto_backup()
    assert len(app.started) == 2
    # off: nothing, not even at start-up
    app.state["auto_backup"] = "off"
    app.autobackup_flag.unlink()
    app.last_auto -= 1000
    app.auto_backup()
    assert len(app.started) == 2
    # no FM-1: nothing
    app.state["auto_backup"], app.midi = 5, None
    app.auto_backup()
    assert len(app.started) == 2


def test_job_screen_and_status():
    app = make_app()
    app.screen, app.job_title = "job", "Firmware"
    state = {"job": "flash", "state": "running", "step": "Writing", "percent": 42, "message": "", "pid": os.getpid()}
    library.STATE.parent.mkdir(parents=True, exist_ok=True)
    library.STATE.write_text(__import__("json").dumps(state))
    app.job_read = 0
    snap(app, "job-running")
    assert app.flash_running()
    keys(app, "left")
    assert app.screen == "job"                             # can't leave a running job's screen
    app.on_key("key3", True)
    assert app.message and "flashed" in app.message        # no shutdown mid-flash
    state.update(state="done", message="Installed FM-1_908 (SLOOP 2.8)")
    library.STATE.write_text(__import__("json").dumps(state))
    app.job_read = 0
    app.message = None
    snap(app, "job-done")
    keys(app, "press")
    assert app.screen != "job"
    # the status screen shows a running job in its corner
    state.update(job="backup", state="running", percent=63)
    library.STATE.write_text(__import__("json").dumps(state))
    app.screen, app.job_read = "status", 0
    snap(app, "status-backup")


def test_audio_rate():
    app = make_app()
    reopened = []
    app.loop.reopen = reopened.append
    app.screen, app.settings_menu = "settings", "audio"
    app.setting = [row[0] for row in app.settings_rows()].index("rate")
    assert app.settings_rows()[app.setting][2] == "48 kHz"
    app.on_key("press")
    assert app.state["audio_rate"] == 44100 and reopened == [44100]
    assert app.settings_rows()[app.setting][2] == "44.1 kHz"
    snap(app, "settings-rate")
    app.on_key("press")
    assert app.state["audio_rate"] == 48000 and reopened == [44100, 48000]
    # reopening: no new loopback until the FM-1's input has had time to close
    loop = bridge.Loopback(44100)
    loop.reopen(48000)
    loop.ensure("alsa_input.fm1", "bluez_output.x")
    assert loop.rate == 48000 and not loop.running()


class FakeMidi:
    enabled, error, pairing_until, peer = True, None, 0.0, None

    def __init__(self):
        self.commands, self.clock = [], None

    def set_clock(self, mode, bpm):
        self.commands.append(("clock", mode, bpm))

    def transport(self, command):
        self.commands.append(("transport", command))

    def set_routes(self, routes):
        self.commands.append(("routes", routes))

    def usb_label(self):
        return "Felucca"

    def activity(self):
        return 0.0


def test_clock():
    app = make_app()
    app.midi = FakeMidi()
    for i in range(3):                                     # tap, tap, tap: half a second apart
        app.tap(50.0 + i * 0.5)
    assert app.state["clock_mode"] == "box" and app.state["bpm"] == 120
    assert app.midi.commands[-1] == ("clock", "box", 120)
    app.tap(60.0)                                           # after a long pause: a new tap tempo
    assert app.taps == [60.0] and app.state["bpm"] == 120
    app.on_key("press", True)                               # hold on the status screen: Start
    assert app.midi.commands[-1] == ("transport", "start")
    app.on_key("press", True)
    assert app.midi.commands[-1] == ("transport", "stop")
    app.midi.clock = {"bpm": 120, "source": "box", "playing": True}
    assert app.clock_text() == "▶ 120 BPM"
    app.state["clock_mode"] = "follow"
    app.midi.clock = None
    assert app.clock_text() == "follow"                    # following, and no clock arrives
    app.midi.clock = {"bpm": 98, "source": "FM-1", "playing": False}
    assert app.clock_text() == "follow 98"
    app.state["clock_mode"] = "box"
    app.midi.clock = {"bpm": 120, "source": "box", "playing": True}
    app.message, app.job_busy = None, lambda: None        # no message box or job in the picture
    snap(app, "status-clock")
    # Settings: Clock row switches follow/box; Tempo row edits with up/down
    app.screen, app.settings_menu = "settings", "clock"
    rows = [row[0] for row in app.settings_rows()]
    app.setting = rows.index("clock")
    app.on_key("press")
    assert app.state["clock_mode"] == "follow" and app.midi.commands[-1] == ("clock", "follow", 120)
    app.setting = rows.index("tempo")
    app.on_key("press")
    assert app.editing == "tempo"
    app.on_key("up")
    app.on_key("up")
    app.on_key("down")
    assert app.state["bpm"] == 121 and app.screen == "settings"
    app.message = None
    snap(app, "settings-tempo")
    app.on_key("press")
    assert app.editing is None
    app.set_clock(bpm=999)
    assert app.state["bpm"] == bridge.BPM_RANGE[1]


def test_midi_matrix():
    app = make_app()
    app.midi = FakeMidi()
    app.screen, app.settings_menu = "settings", "midi"
    app.setting = [row[0] for row in app.settings_rows()].index("routes")
    app.on_key("press")
    assert app.screen == "matrix"
    snap(app, "matrix")
    app.on_key("right")
    app.on_key("left")
    app.on_key("left")
    assert app.screen == "settings" and app.settings_menu == "midi"     # left from the first column: back
    app.on_key("press")
    app.on_key("press")                                   # BT > FM-1
    assert app.screen == "route" and app.route_key == "BT>FM-1"
    snap(app, "route")
    app.on_key("press")                                   # Route on -> off
    assert app.routes()["BT>FM-1"]["on"] is False and app.midi.commands[-1][0] == "routes"
    app.on_key("press")
    app.on_key("down")                                    # Channels
    app.on_key("press")
    app.on_key("up")                                      # same -> all to 1
    app.on_key("up")                                      # -> all to 2
    app.on_key("press")
    assert app.routes()["BT>FM-1"]["channels"] == 1 and app.route_edit is None
    app.on_key("down")                                    # Transpose
    app.on_key("press")
    for _ in range(3):
        app.on_key("up")
    app.on_key("down")
    snap(app, "route-transpose")
    app.on_key("left")
    assert app.routes()["BT>FM-1"]["transpose"] == 2
    app.on_key("down")                                    # Velocity: normal -> soft -> hard -> fixed 40
    app.on_key("press")
    for _ in range(3):
        app.on_key("up")
    app.on_key("press")
    assert app.routes()["BT>FM-1"]["velocity"] == "fixed" and app.routes()["BT>FM-1"]["fixed"] == 40
    # Channels: custom opens the 16-channel table
    app.route_row = 1
    app.on_key("press")
    for _ in range(15):                                   # all to 2 -> all to 3 ... all to 16, then custom
        app.on_key("up")
    app.on_key("press")
    assert app.screen == "chanmap" and app.routes()["BT>FM-1"]["channels"] == list(range(16))
    app.on_key("press")
    app.on_key("down")                                    # channel 1 -> off (wraps below 1)
    app.on_key("press")
    assert app.routes()["BT>FM-1"]["channels"][0] is None
    snap(app, "chanmap")
    app.on_key("left")
    assert app.screen == "route" and app.route_rows()[1][2] == "custom ›"
    app.on_key("left")
    app.mx = [2, 1]                                       # Box > BT: only on/off
    app.on_key("press")
    assert [r[0] for r in app.route_rows()] == ["on"]
    app.on_key("left")
    app.mx = [0, 1]                                       # BT > BT: no such route
    app.on_key("press")
    assert app.screen == "matrix"


if __name__ == "__main__":
    for name, fn in [("test_flows", test_flows), ("test_auto_backup_schedule", test_auto_backup_schedule),
                     ("test_job_screen_and_status", test_job_screen_and_status), ("test_audio_rate", test_audio_rate),
                     ("test_clock", test_clock), ("test_midi_matrix", test_midi_matrix)]:
        fn()
        print("ok", name)
    print("all passed")
