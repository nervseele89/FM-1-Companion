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
    assert app.screen == "settings" and app.settings_rows()[0][0] == "library"
    snap(app, "settings")
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
    assert app.started[-1] == ["fetch-sloop"]
    app.screen = "pick"
    keys(app, "down", "press")
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


if __name__ == "__main__":
    for name, fn in [("test_flows", test_flows), ("test_auto_backup_schedule", test_auto_backup_schedule),
                     ("test_job_screen_and_status", test_job_screen_and_status)]:
        fn()
        print("ok", name)
    print("all passed")
