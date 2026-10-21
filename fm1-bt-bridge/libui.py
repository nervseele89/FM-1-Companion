"""The box's Library screens: a menu, pickers, a hold-to-confirm step and the job screen.

Jobs are library.py commands run as their own systemd user units, so a flash carries on even if the bridge
restarts; their progress comes back through library.py's state file. Mixed into bridge.App, which provides the
fonts, fit(), say(), draw_box() and the screen machinery.
"""
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import library

LIBRARY_PY = str(Path(__file__).resolve().parent / "library.py")
SLOTS = ["USR1", "USR2", "USR3", "USR4"]
TARGETS = [("working", "Working project"), ("A", "Project A"), ("B", "Project B"), ("C", "Project C"),
           ("D", "Project D")]
WRITES = ("restore", "load-project", "load-sample", "flash")


def job_running(state):
    """the library job in state, if it's still running (its process alive)"""
    if not state or state.get("state") != "running":
        return None
    try:
        os.kill(state.get("pid", 0), 0)
    except (OSError, TypeError):
        return None                           # it died without saying: not running
    return state


def short_date(path_name):
    """"2026-10-21_171819_..." -> "10-21 17:18" """
    try:
        return datetime.strptime(path_name[:17], "%Y-%m-%d_%H%M%S").strftime("%m-%d %H:%M")
    except ValueError:
        return path_name[:11]


def short_firmware(text):
    """"FELUCCA SLOOP 2.7.1" -> "2.7.1" """
    return (text or "?").split()[-1]


class LibraryScreens:
    """Screens: "library" (the menu), "pick" (a list), "confirm" (hold press 2 s) and "job"."""

    def library_init(self):
        self.lib_cursor = 0
        self.pick = None                      # {"title", "items": [(label, value, payload)], "cursor", "on", "back"}
        self.confirm = None                   # {"title", "lines", "action", "back"}
        self.job_title, self.job_back = "", "library"
        self.job_state, self.job_read = None, 0.0
        self.autobackup_flag = Path(library.RUNTIME) / "fm1-autobackup.done"

    # ---- jobs
    def library_state(self, now=None):
        """library.py's state file, read at most twice a second"""
        now = now or time.monotonic()
        if now - self.job_read > 0.5:
            self.job_state, self.job_read = library.read_state(), now
        return self.job_state

    def job_busy(self):
        return job_running(self.library_state())

    def start_job(self, args, title, show=True):
        if self.job_busy():
            self.say("A library job is running")
            return
        command = [sys.executable, LIBRARY_PY, *args]
        if shutil.which("systemd-run"):       # its own unit: survives a restart of the bridge
            command = ["systemd-run", "--user", "--collect", "--quiet",
                       f"--unit=fm1-library-{int(time.time())}", *command]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self.say(f"Can't start: {e}"[:40])
            return
        self.job_read = 0.0
        if show:
            self.job_title, self.job_back = title, self.screen if self.screen != "confirm" else "library"
            self.screen = "job"

    def auto_backup(self):
        """once per boot, when the FM-1's MIDI is there and nothing else runs"""
        if self.autobackup_flag.exists() or not (self.midi and self.midi.usb_label()) or self.job_busy():
            return
        try:
            self.autobackup_flag.write_text(str(time.time()))
        except OSError:
            return
        self.start_job(["backup", "--auto"], "Auto backup", show=False)

    def flash_running(self):
        state = self.job_busy()
        return bool(state and state.get("job") == "flash")

    # ---- the menu
    def library_rows(self):
        rows = [("backup", "Back up now"), ("restore", "Restore ›"), ("project", "Load project ›"),
                ("sample", "Load sample ›"), ("firmware", "Firmware ›"), ("sd", "Import SD card")]
        return rows

    def open_library(self):
        self.screen, self.lib_cursor = "library", 0

    def library_choose(self):
        key = self.library_rows()[self.lib_cursor][0]
        listing = library.listing()
        if key == "backup":
            self.start_job(["backup"], "Backup")
        elif key == "restore":
            items = [(short_date(b["name"]), short_firmware(b.get("firmware")), b["name"])
                     for b in listing["backups"] if not b.get("damaged")]
            self.open_pick("Restore", items, self.pick_restore, "No backups yet")
        elif key == "project":
            items = [(f"{short_date(b['name'])} backup", "›", ("backup", b["name"]))
                     for b in listing["backups"] if b.get("projects") and not b.get("damaged")]
            items = [(p.get("title") or p["name"], "file", ("project", p["name"]))
                     for p in listing["projects"] if not p.get("damaged")] + items
            self.open_pick("Project from", items, self.pick_project_source, "No projects yet")
        elif key == "sample":
            items = [(Path(s["name"]).stem, "wav", s["name"]) for s in listing["samples"]]
            self.open_pick("Sample", items, self.pick_sample, "No WAV files yet")
        elif key == "firmware":
            items = [(Path(f["name"]).stem, "", f["name"]) for f in listing["firmware"]]
            if self.wifi:
                items.insert(0, ("Get SLOOP update", "web", None))
            self.open_pick("Firmware", items, self.pick_firmware, "No firmware: Wi-Fi on for download")
        elif key == "sd":
            try:
                copied = library.import_sd()
                self.say(f"{len(copied)} new file{'s' if len(copied) != 1 else ''} from SD")
            except OSError as e:
                self.say(f"SD card: {e}"[:40])

    # ---- pickers
    def open_pick(self, title, items, on, empty, back="library"):
        if not items:
            self.say(empty)
            return
        self.pick = {"title": title, "items": items, "cursor": 0, "on": on, "back": back}
        self.screen = "pick"

    def ask(self, title, lines, action, back="pick"):
        self.confirm = {"title": title, "lines": lines, "action": action, "back": back}
        self.screen = "confirm"

    def pick_restore(self, name):
        path = library.folder("backups") / name
        self.ask("Restore?", [short_date(name) + "  " + short_firmware(library.sloop.load_json(path).get("firmware")),
                              "replaces the FM-1's data", "(backed up first)"],
                 lambda: self.start_job(["restore", str(path)], "Restore"))

    def pick_project_source(self, payload):
        kind, name = payload
        if kind == "project":
            self.pick_project_target(library.folder("projects") / name, "-", Path(name).stem)
            return
        path = library.folder("backups") / name
        try:
            inside = library.sloop.projects_in(library.sloop.load_json(path))
        except library.sloop.SloopError as e:
            self.say(str(e)[:40])
            return
        items = [("Working project" if p == "working" else f"Project {p}", "", p) for p in inside]
        self.open_pick("Which project", items, lambda p: self.pick_project_target(path, p, f"backup {p}"),
                       "No projects in it", back="pick")

    def pick_project_target(self, path, source, label):
        items = [(text, "", key) for key, text in TARGETS]
        self.open_pick("Load into", items,
                       lambda target: self.ask("Load project?", [label[:20], f"into {dict(TARGETS)[target]}",
                                                                  "(backed up first)"],
                                               lambda: self.start_job(["load-project", str(path), source, target],
                                                                      "Load project")),
                       "", back="pick")

    def pick_sample(self, name):
        path = library.folder("samples") / name
        items = [(slot, "", k + 1) for k, slot in enumerate(SLOTS)]
        self.open_pick("Into slot", items,
                       lambda slot: self.ask("Load sample?", [Path(name).stem[:20], f"into USR{slot}",
                                                              "(backed up first)"],
                                             lambda: self.start_job(["load-sample", str(slot), Path(name).stem[:8],
                                                                     str(path)], "Load sample")),
                       "", back="pick")

    def pick_firmware(self, name):
        if name is None:
            self.start_job(["fetch-sloop"], "SLOOP update")
            return
        path = library.folder("firmware") / name
        self.ask("Install?", [Path(name).stem[:20], "Keep the FM-1 plugged in", "(backed up first)"],
                 lambda: self.start_job(["flash", str(path)], "Firmware"))

    # ---- keys
    def library_key(self, key, held):
        """True if a Library screen took the key"""
        if self.screen == "library":
            rows = self.library_rows()
            if key in ("up", "down"):
                self.lib_cursor = (self.lib_cursor + (1 if key == "down" else -1)) % len(rows)
            elif key in ("press", "right") and not held:
                self.library_choose()
            elif key == "left":
                self.screen = "settings"
            return True
        if self.screen == "pick":
            p = self.pick
            if key in ("up", "down"):
                p["cursor"] = (p["cursor"] + (1 if key == "down" else -1)) % len(p["items"])
            elif key in ("press", "right") and not held:
                p["on"](p["items"][p["cursor"]][2])
            elif key == "left":
                self.screen = "library"
            return True
        if self.screen == "confirm":
            if key == "press" and held:
                self.confirm["action"]()
            elif key == "press":
                self.say("Hold to confirm", 1.5)
            elif key == "left":
                self.screen = self.confirm["back"] if self.pick else "library"
            return True
        if self.screen == "job":
            state = self.library_state()
            if key in ("left", "press") and not job_running(state):
                self.screen = self.job_back if self.job_back in ("library", "status") else "library"
            return True
        return False

    # ---- drawing
    def library_view(self, now):
        state = self.library_state(now) if self.screen in ("job", "status") else None
        if self.screen == "library":
            return ("library", self.lib_cursor)
        if self.screen == "pick":
            return ("pick", self.pick["title"], self.pick["cursor"], tuple(i[:2] for i in self.pick["items"]))
        if self.screen == "confirm":
            return ("confirm", self.confirm["title"], tuple(self.confirm["lines"]))
        if self.screen == "job":
            s = state or {}
            return ("job", s.get("state"), s.get("step"), s.get("percent") and int(s["percent"]), s.get("message"))
        running = job_running(state)
        return running and (running.get("job"), running.get("percent") and int(running["percent"]))

    def draw_list(self, d, title, items, cursor, footer):
        d.text((0, 0), title, font=self.f_title, fill=1)
        where = f"{cursor + 1}/{len(items)}"
        d.text((127 - d.textlength(where, font=self.f_small), 2), where, font=self.f_small, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        top = min(max(0, cursor - 1), max(0, len(items) - 3))
        for i, (label, value) in enumerate(items[top:top + 3]):
            y, selected = 16 + i * 12, top + i == cursor
            if selected:
                d.rectangle((0, y, 127, y + 11), fill=1)
            value = self.fit(d, value or "", self.f_small, 44)
            w = d.textlength(value, font=self.f_small)
            d.text((2, y), self.fit(d, label, self.f_body, 120 - w), font=self.f_body, fill=0 if selected else 1)
            d.text((125 - w, y + 2), value, font=self.f_small, fill=0 if selected else 1)
        d.text((0, 53), footer, font=self.f_small, fill=1)

    def draw_library(self, d):
        self.draw_list(d, "Library", [(label, "") for _, label in self.library_rows()], self.lib_cursor,
                       "press = choose   < back")

    def draw_pick(self, d):
        p = self.pick
        self.draw_list(d, p["title"], [i[:2] for i in p["items"]], p["cursor"], "press = choose   < back")

    def draw_confirm(self, d):
        c = self.confirm
        d.text((0, 0), c["title"], font=self.f_title, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        for i, line in enumerate(c["lines"][:3]):
            d.text((0, 16 + i * 12), self.fit(d, line, self.f_body, 127), font=self.f_body, fill=1)
        d.rectangle((0, 52, 127, 63), fill=1)
        d.text((4, 53), "HOLD press 2 s · < back", font=self.f_small, fill=0)

    def draw_job(self, d):
        s = self.library_state() or {}
        d.text((0, 0), self.fit(d, self.job_title, self.f_title, 127), font=self.f_title, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        state = s.get("state")
        if state == "running" or (state is None):
            percent = "" if s.get("percent") is None else f"{int(s['percent'])}%"
            d.text((127 - d.textlength(percent, font=self.f_body), 17), percent, font=self.f_body, fill=1)
            d.text((0, 17), self.fit(d, s.get("step") or "Starting", self.f_body, 95), font=self.f_body, fill=1)
            if s.get("percent") is not None:
                d.rectangle((0, 32, 127, 41), outline=1)
                d.rectangle((2, 34, 2 + int(123 * min(100, s["percent"]) / 100), 39), fill=1)
            if s.get("job") == "flash":
                d.text((0, 53), "Don't unplug the FM-1", font=self.f_small, fill=1)
        else:
            d.text((0, 17), "Done" if state == "done" else "Failed", font=self.f_body, fill=1)
            words, lines, line = (s.get("message") or "").split(), [], ""
            for word in words:
                trial = (line + " " + word).strip()
                if d.textlength(trial, font=self.f_small) <= 127 or not line:
                    line = trial
                else:
                    lines.append(line)
                    line = word
            for i, text in enumerate((lines + [line])[:2]):
                d.text((0, 31 + i * 10), text, font=self.f_small, fill=1)
            d.text((0, 53), "press = OK", font=self.f_small, fill=1)
