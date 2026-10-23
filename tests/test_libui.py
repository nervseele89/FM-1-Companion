"""Tests for the box's Library screens: key flows end in the right library.py job; every screen renders.

  python tests/test_libui.py [PNG_DIR]     (needs Pillow and mido; PNG_DIR gets a picture of each screen)
"""
import os
import sys
import tempfile
import time
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




def test_quality_of_life():
    """The firmware name from last time until the FM-1 is asked; the big volume bar; faster repeat when held."""
    class Midi:                                            # the FM-1's MIDI is there
        enabled, error, peer, pairing_until = True, None, None, 0.0

        def usb_label(self):
            return "Felucca"

        def activity(self):
            return 0.0

    library.forget_firmware()
    app = make_app()
    app.state["fw_label"] = "SLOOP 2.8"
    app.midi, app.job_busy = Midi(), lambda: None
    app.identify_fm1()
    assert app.fw_label == "SLOOP 2.8" and not app.fw_asked        # shown at once, asked after 5 s
    # volume: snapped, shown big on the home screen for a moment
    bridge.set_volume = lambda node, percent: None
    app.screen, app.sink, app.volume = "status", {"id": 2, "name": "bluez_output.jbl", "label": "JBL Go 3"}, 37
    app.on_key("up")
    assert app.volume == 38 and app.volume_shown > time.monotonic()
    snap(app, "status-volume")
    # holding up: 5 a second, then faster after 1.5 s
    posted = []
    timer = bridge.KeyTimer(lambda name, held=False: posted.append(name))
    timer.pressed("up", 0.0)
    for k in range(1, 61):                                 # 3 s in 50 ms ticks
        timer.tick(k * 0.05)
    assert len(posted) >= 20, len(posted)                  # 15 at an even 5 a second



def test_battery():
    """The PiSugar's charge on the home screen; a warning when low, a safe shutdown when empty."""
    import queue
    app = make_app()
    app.screen, app.message = "status", None
    app.battery = {"percent": 78, "mv": 3818, "plugged": True}
    snap(app, "status-battery")
    app.battery_check()
    assert app.events.empty()                              # plugged in: nothing to say
    for percent, expect in ((40, None), (15, "low"), (14, None), (12, None), (5, None), (4, "empty")):
        app.battery = {"percent": percent, "mv": 3500, "plugged": False}
        app.battery_check()
        try:
            got = app.events.get_nowait()
        except queue.Empty:
            got = None
        assert got == (("battery", expect) if expect else None), (percent, got)   # once each, empty twice
    app.battery = {"percent": 4, "mv": 3300, "plugged": True}                      # plugged back in
    app.battery_check()
    assert not app.battery_warned and app.battery_empty == 0
    app.recorder.recording = lambda: True                  # the REC timer needs the room: no battery then
    app.recorder.seconds = lambda now=None: 754
    snap(app, "status-battery-rec")

def test_volume_steps():
    """Up and down land on even steps (2 %), whatever the speaker had; a Bluetooth speaker's 1/127ths
    round back to the same whole percent."""
    snap = bridge.volume_snap
    assert [snap(37, 2), snap(38, 2), snap(99, 2), snap(100, 2)] == [38, 40, 100, 100]
    assert [snap(37, -2), snap(38, -2), snap(1, -2), snap(0, -2)] == [36, 36, 0, 0]
    assert all(round(round(p * 1.27) / 1.27) == p for p in range(0, 101, 2))


def at_row(app, menu, key):
    """Settings, on that row of that list (None: the top one)"""
    app.screen, app.settings_menu = "settings", menu
    app.setting = [r[0] for r in app.settings_rows()].index(key)


def test_flows():
    backup = setup_library()
    app = make_app()
    keys(app, "right")
    top = [r[0] for r in app.settings_rows()]
    assert app.screen == "settings" and top == ["g_play", "g_audio", "g_midi", "recording", "editor", "wifi",
                                                "g_sloop", "g_fm1"]
    keys(app, "press")                                     # Playing ›
    assert app.settings_menu == "play" and [r[0] for r in app.settings_rows()] == [
        "metronome", "tempo", "clock", "arp_on", "chord_on", "click"]
    snap(app, "settings-play")
    keys(app, "left", "down", "press")                     # Audio ›
    assert [r[0] for r in app.settings_rows()] == ["speaker", "audio", "rate"]
    keys(app, "left", "down", "press")                     # MIDI ›
    assert [r[0] for r in app.settings_rows()] == ["midi", "pairing", "usbkeys", "routes", "presets"]
    keys(app, "left", "up", "up")                          # back on Playing, the top
    assert app.settings_menu is None and app.setting == 0
    snap(app, "settings")
    keys(app, "up", "up", "press")                         # Sloop › (up from the top wraps to the end)
    assert app.settings_menu == "sloop" and [r[0] for r in app.settings_rows()] == [
        "backup", "restore", "project", "sample", "sample_level", "auto", "sd"]
    snap(app, "settings-sloop")
    keys(app, "left")                                      # back to the top list, on Sloop
    assert app.settings_menu is None and app.setting == top.index("g_sloop") and app.screen == "settings"
    keys(app, "down", "press")                             # FM-1 ›
    assert app.settings_menu == "fm1" and [r[0] for r in app.settings_rows()] == ["fw_info", "firmware"]
    snap(app, "settings-fm1")
    keys(app, "left", "up", "press")                       # Sloop › again
    assert app.settings_menu == "sloop" and app.setting == 0
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
    at_row(app, "sloop", "project")
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
    at_row(app, "sloop", "sample")
    keys(app, "press", "press", "down", "press")
    snap(app, "sample-confirm")
    keys(app, "press!")
    assert app.started[-1][:3] == ["load-sample", "2", "bass_C2"], app.started[-1]
    # firmware: the download row first (Wi-Fi on), then the file
    at_row(app, "fm1", "firmware")
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
    assert app.screen == "settings" and app.settings_menu == "fm1"
    keys(app, "left")
    assert app.screen == "settings" and app.settings_menu is None


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
    at_row(app, "sloop", "auto")
    assert app.settings_rows()[app.setting][2] == "at start"
    seen = []
    for _ in range(6):
        keys(app, "press")
        seen.append(app.settings_rows()[app.setting][2])
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
    at_row(app, "audio", "rate")
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
    app.screen, app.settings_menu = "settings", "play"
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


class FakePwPlay:
    """stands in for pw-play: a Python process that plays for `seconds`"""
    real = __import__("meters").subprocess.Popen

    def __init__(self, seconds=30):
        self.seconds, self.cmd = seconds, None

    def __call__(self, cmd, **kw):
        self.cmd = cmd
        return self.real([sys.executable, "-c", f"import time; time.sleep({self.seconds})"], **kw)


def test_recordings():
    import meters
    app = make_app()
    app.sink = {"id": 2, "name": "bluez_output.jbl", "label": "JBL Go 3"}
    rec = library.folder("recordings") / "2026-10-22_001217_fm1.wav"
    rec.write_bytes(b"RIFF" + bytes(40) + bytes(192000 * 5))           # 5 seconds
    at_row(app, None, "recording")
    keys(app, "press")
    assert app.screen == "pick" and app.pick["title"] == "Recordings" and app.pick["items"][0][:2] == ("10-22 00:12", "0:05")
    snap(app, "recordings-pick")
    keys(app, "press")                                     # its actions
    assert [i[2] for i in app.pick["items"]] == ["play", "edit", "delete"]
    snap(app, "recording-actions")
    fake = FakePwPlay()
    meters.subprocess.Popen = fake
    try:
        keys(app, "press")                                 # Play
        assert app.screen == "player" and fake.cmd[:3] == ["pw-play", "--target", "bluez_output.jbl"]
        assert app.player.playing() and app.player.length == 5
        app.player.started -= 2
        snap(app, "player")
        keys(app, "press")                                 # stop
        assert app.screen == "settings" and not app.player.playing()
        fake.seconds = 0                                   # plays to the end: back to the Library by itself
        app.player.start("bluez_output.jbl", rec)
        app.screen = "player"
        app.player.proc.wait()
        app.render(force=True)
        assert app.screen == "settings" and "end" in app.message
    finally:
        meters.subprocess.Popen = FakePwPlay.real
    app.sink = None                                        # no speaker: says so
    app.recording_action(rec.name, "play")
    assert app.screen != "player" and "speaker" in app.message
    # Delete, after a hold
    app.recording_action(rec.name, "delete")
    keys(app, "press")
    assert rec.exists()                                    # a short press doesn't delete
    keys(app, "press!")
    assert not rec.exists() and app.screen == "settings"


def test_sample_editor():
    """Settings > Sample editor: a recording or a sample; markers, fades, note, Save into the samples."""
    from test_library import write_recording
    app = make_app()
    for f in library.folder("samples").glob("*"):
        f.unlink()
    rec = write_recording(library.folder("recordings") / "2026-10-22_001217_fm1.wav")
    at_row(app, None, "editor")
    keys(app, "press")
    assert app.screen == "pick" and [i[2] for i in app.pick["items"]] == ["recordings", "samples"]
    keys(app, "down", "press")                             # From a sample: none yet
    assert "No samples" in app.message
    keys(app, "up", "press", "press")                      # From a recording: the take
    assert app.screen == "editor"
    for _ in range(200):                                   # it loads in the background
        if not app.ed["loading"]:
            break
        time.sleep(0.05)
    ed = app.ed
    assert not ed["error"] and ed["note"] == 45 and 0.98 < ed["start"] < 1.0, ed["error"]
    snap(app, "editor")
    start = ed["start"]
    keys(app, "up", "up")                                  # Start later
    assert ed["start"] > start
    keys(app, "right", "down")                             # End earlier
    keys(app, "right", "up", "up")                         # Fade in: 20 ms
    assert ed["fade_in"] == 0.02
    keys(app, "right", "right", "right", "up")            # Reverse on
    assert ed["reverse"] and FIELD(app) == "reverse"
    keys(app, "right", "up")                               # Note: A2 -> A#2
    assert ed["note"] == 46
    snap(app, "editor-note")
    keys(app, "right", "press")                            # Save
    saved = list(library.folder("samples").glob("*.wav"))
    assert [p.name for p in saved] == ["rec-001217_A#2.wav"] and "Saved" in app.message, saved
    keys(app, *["left"] * 8)                               # back out, field by field, to the list
    assert app.screen == "pick" and app.ed is None
    app.pick = None                                        # the sample opens whole, its note from the name
    app.open_editor(saved[0].name, "samples")
    for _ in range(200):
        if not app.ed["loading"]:
            break
        time.sleep(0.05)
    assert app.ed["start"] == 0.0 and app.ed["note"] == 46 and app.ed["out_name"] == "rec-001217"
    keys(app, "left")
    assert app.screen == "settings"


def FIELD(app):
    import sampleui
    return sampleui.FIELDS[app.ed["field"]][0]


def test_midi_matrix():
    app = make_app()
    app.midi = FakeMidi()
    at_row(app, "midi", "routes")
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
    app.mx = [3, 1]                                       # Box > BT: only on/off
    app.on_key("press")
    assert [r[0] for r in app.route_rows()] == ["on"]
    app.on_key("left")
    app.mx = [0, 1]                                       # BT > BT: no such route
    app.on_key("press")
    assert app.screen == "matrix"


def test_arp_rows_and_presets():
    app = make_app()
    app.midi = FakeMidi()
    # the quick switches: Arp and Chord in Settings > Playing, for the keyboard's route
    app.screen, app.settings_menu = "settings", "play"
    rows = [r[0] for r in app.settings_rows()]
    assert rows[3:5] == ["arp_on", "chord_on"]
    app.setting = 3
    keys(app, "press")
    assert app.routes()["BT>FM-1"]["arp_on"] is True and app.settings_rows()[3][2] == "on"
    assert "Arp up 1/8: on" in app.message and app.midi.commands[-1][0] == "routes"
    keys(app, "down", "press")
    assert app.routes()["BT>FM-1"]["chord_on"] is True
    snap(app, "settings-midi-quick")
    keys(app, "up", "press")                                 # Arp off again: the pattern is kept
    assert app.routes()["BT>FM-1"]["arp_on"] is False and app.routes()["BT>FM-1"]["arp"] == "up"
    # the details on the route page
    app.screen, app.route_key, app.route_row = "route", "BT>FM-1", 0
    rows = [r[0] for r in app.route_rows()]
    assert rows[-3:] == ["chord_on", "chord", "arp_on"]      # the chord's type shows while it's on
    app.route_row = rows.index("chord")
    keys(app, "press", "up", "press")                        # maj -> min
    assert app.routes()["BT>FM-1"]["chord"] == "min"
    app.route_row = rows.index("arp_on")
    keys(app, "press")                                       # Arp on: pattern, rate and octaves appear
    assert [r[0] for r in app.route_rows()][-4:] == ["arp_on", "arp", "arp_rate", "arp_octaves"]
    snap(app, "route-arp")
    # presets: save this setup into 2, change it, load 2 back
    at_row(app, "midi", "presets")
    keys(app, "press")
    assert app.screen == "presets"
    keys(app, "down", "press")                               # preset 2 (empty): only Save here
    assert app.preset_actions() == ["Save here"]
    keys(app, "press")
    assert app.state["route_presets"]["2"]["routes"]["BT>FM-1"]["chord"] == "min"
    snap(app, "presets")
    app.set_route_of("BT>FM-1", chord="maj", arp_on=False)
    keys(app, "press")
    assert app.preset_actions() == ["Load", "Save here", "Clear"]
    keys(app, "press")                                       # Load
    assert app.routes()["BT>FM-1"]["chord"] == "min" and app.routes()["BT>FM-1"]["arp_on"] is True
    keys(app, "press", "down", "down", "press")              # Clear
    assert "2" not in app.state["route_presets"]
    keys(app, "left")
    assert app.screen == "settings"


def test_web_link():
    app = make_app()
    app.midi = FakeMidi()
    app.loop.reopen = lambda rate: None
    status = app.web_status()
    assert status["settings"]["bpm"] == 120 and status["routes"]["BT>FM-1"]["on"] and status["recording"] is None
    app.web_publish()
    assert library.read_status()["settings"]["metronome"] is False
    for command in ({"cmd": "routes", "routes": {"BT>FM-1": {"transpose": 5}}},   # the page sends all of it
                    {"cmd": "set", "key": "metronome", "value": True}, {"cmd": "set", "key": "bpm", "value": 97},
                    {"cmd": "set", "key": "clock_mode", "value": "box"}, {"cmd": "set", "key": "click", "value": "high"},
                    {"cmd": "set", "key": "audio_rate", "value": 44100}, {"cmd": "set", "key": "arp_on", "value": True},
                    {"cmd": "transport", "value": "start"},
                    {"cmd": "preset", "slot": "3", "action": "save", "name": "Night"}):
        library.queue_command(command)
    library.queue_command({"cmd": "set", "key": "bpm", "value": 9999})   # refused, with a message
    app.web_commands()
    s = app.state
    assert s["metronome"] and s["bpm"] == 97 and s["clock_mode"] == "box" and s["click"] == "high"
    assert s["audio_rate"] == 44100 and app.routes()["BT>FM-1"]["arp_on"] is True
    assert app.routes()["BT>FM-1"]["transpose"] == 5 and s["route_presets"]["3"]["name"] == "Night"
    assert ("transport", "start") in app.midi.commands and "bad setting" in app.message
    app.web_publish()
    assert library.read_status()["settings"]["metronome"] is True       # the page sees it


if __name__ == "__main__":
    for name, fn in [("test_flows", test_flows), ("test_auto_backup_schedule", test_auto_backup_schedule),
                     ("test_job_screen_and_status", test_job_screen_and_status), ("test_audio_rate", test_audio_rate),
                     ("test_clock", test_clock), ("test_recordings", test_recordings),
                     ("test_midi_matrix", test_midi_matrix), ("test_arp_rows_and_presets", test_arp_rows_and_presets),
                     ("test_web_link", test_web_link)]:
        fn()
        print("ok", name)
    print("all passed")
