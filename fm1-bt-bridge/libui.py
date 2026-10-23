"""The box's Library screens: a menu, pickers, a hold-to-confirm step and the job screen.

Jobs are library.py commands run as their own systemd user units, so a flash carries on even if the bridge
restarts; their progress comes back through library.py's state file. Mixed into bridge.App, which provides the
fonts, fit(), say(), draw_box() and the screen machinery.
"""
import os
import time
from datetime import datetime
from pathlib import Path

import library
from meters import Player, clips_file, clock, wav_seconds

SLOTS = ["USR1", "USR2", "USR3", "USR4"]
TARGETS = [("working", "Working project"), ("A", "Project A"), ("B", "Project B"), ("C", "Project C"),
           ("D", "Project D")]
WRITES = ("restore", "load-project", "load-sample", "flash")
AUTO_CHOICES = ["off", "boot", 2, 5, 10, 30]            # the "auto_backup" setting: minutes between backups
AUTO_LABELS = {"off": "off", "boot": "at start"}


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


LIBRARY_KEYS = ("backup", "restore", "project", "sample", "recording", "editor", "firmware", "sd", "auto")


class LibraryScreens:
    """The Settings rows that reach the library (LIBRARY_KEYS: Recordings, and the Sloop and FM-1 groups), and
    their screens: "pick" (a list), "confirm" (hold press 2 s), "player" and "job". Back goes to Settings."""

    def library_init(self):
        self.pick = None                      # {"title", "items": [(label, value, payload)], "cursor", "on", "back"}
        self.confirm = None                   # {"title", "lines", "action", "back"}
        self.player, self.player_name = Player(), None    # a recording playing on the speaker
        self.job_title, self.job_back = "", "settings"
        self.job_state, self.job_read = None, 0.0
        self.autobackup_flag = Path(library.RUNTIME) / "fm1-autobackup.done"
        self.last_auto = time.monotonic()     # the interval counts from start-up (or the last auto backup)
        self.fw_label = library.firmware_label() or self.state.get("fw_label")
        self.fw_seen, self.fw_asked = None, False

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
        error = library.spawn(args)           # its own unit: survives a restart of the bridge
        if error:
            self.say(f"Can't start: {error}"[:40])
            return
        self.job_read = 0.0
        if show:
            self.job_title, self.job_back = title, self.screen if self.screen != "confirm" else "settings"
            self.screen = "job"

    def auto_mode(self):
        mode = self.state.get("auto_backup", "boot")
        return mode if mode in AUTO_CHOICES else "boot"

    def auto_backup(self):
        """At start-up (once per boot) unless off, then every N minutes if set: when the FM-1's MIDI is there
        and no other job runs. library.py saves no new file when nothing changed."""
        mode = self.auto_mode()
        if mode == "off" or not (self.midi and self.midi.usb_label()) or self.job_busy():
            return
        now = time.monotonic()
        if not self.autobackup_flag.exists():
            try:
                self.autobackup_flag.parent.mkdir(parents=True, exist_ok=True)
                self.autobackup_flag.write_text(str(time.time()))
            except OSError:
                return
        elif not (isinstance(mode, int) and now - self.last_auto >= mode * 60):
            return
        self.last_auto = now
        self.start_job(["backup", "--auto"], "Auto backup", show=False)

    def identify_fm1(self):
        """Keep fw_label ("SLOOP 2.8") for the screens. Any job that talks to the FM-1 notes what it runs; if
        none has 5 s after the FM-1 shows up (the boot backup usually has), ask it once. Until then the screens
        show what it ran last time (kept in the settings), so the home screen isn't "Felucca" for the first
        seconds after a boot. Asked again whenever it comes back: a firmware update or another FM-1."""
        if not (self.midi and self.midi.usb_label()):
            if self.fw_seen is not None:
                library.forget_firmware()
            self.fw_label, self.fw_seen, self.fw_asked = None, None, False
            return
        now = time.monotonic()
        self.fw_seen = self.fw_seen or now
        label = library.firmware_label()
        self.fw_label = label or self.state.get("fw_label")
        if label and label != self.state.get("fw_label"):
            self.state["fw_label"] = label
            self.save_settings()
        if label or self.fw_asked or now - self.fw_seen < 5 or self.job_busy():
            return
        self.fw_asked = True
        self.start_job(["identify"], "Identify", show=False)

    def cycle_auto(self):
        mode = AUTO_CHOICES[(AUTO_CHOICES.index(self.auto_mode()) + 1) % len(AUTO_CHOICES)]
        self.state["auto_backup"] = mode
        self.last_auto = time.monotonic()
        self.save_settings()

    @staticmethod
    def auto_label(mode):
        return AUTO_LABELS.get(mode) or f"{mode} min"

    def web_request(self, request):
        """the web page asked to write to the FM-1: show it to be confirmed here (hold press)"""
        args, title = request.get("args") or [], request.get("title") or "Web request"
        if not args or args[0] not in WRITES:
            return
        self.pick = None
        self.ask(title, (request.get("lines") or [])[:2] + ["from the web page"],
                 lambda: self.start_job(args, title), back="status")
        self.last_activity = time.monotonic()
        self.wake()

    def flash_running(self):
        state = self.job_busy()
        return bool(state and state.get("job") == "flash")

    # ---- the menu
    def library_choose(self, key):
        """one of LIBRARY_KEYS, chosen in Settings"""
        if key == "backup":
            self.start_job(["backup"], "Backup")
            return
        if key == "auto":
            self.cycle_auto()
            return
        listing = library.listing()
        if key == "restore":
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
        elif key == "editor":
            self.open_pick("Sample editor", [("From a recording ›", "", "recordings"),
                                             ("From a sample ›", "", "samples")], self.pick_editor_source, "")
        elif key == "recording":
            items = [(short_date(r["name"]), clock(r.get("seconds", 0)), r["name"]) for r in listing["recordings"]]
            self.open_pick("Recordings", items, self.pick_recording, "No recordings yet")
        elif key == "firmware":
            items = [(Path(f["name"]).stem, "", f["name"]) for f in listing["firmware"]]
            if self.wifi:                     # isod89's two firmwares, from their GitHub releases
                items[:0] = [("Get SLOOP update", "web", ("fetch", "sloop")),
                             ("Get Clipper (exp.)", "web", ("fetch", "clipper"))]
            self.open_pick("Firmware", items, self.pick_firmware, "No firmware: Wi-Fi on for download")
        elif key == "sd":
            try:
                copied = library.import_sd()
                self.say(f"{len(copied)} new file{'s' if len(copied) != 1 else ''} from SD")
            except OSError as e:
                self.say(f"SD card: {e}"[:40])

    # ---- pickers
    def open_pick(self, title, items, on, empty, back="settings"):
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
                                                              "replaces what's there"],
                                             lambda: self.start_job(["load-sample", str(slot), Path(name).stem[:8],
                                                                     str(path)], "Load sample")),
                       "", back="pick")

    def pick_editor_source(self, folder):
        listing = library.listing()[folder]
        if folder == "recordings":
            items = [(short_date(r["name"]), clock(r.get("seconds", 0)), r["name"]) for r in listing]
        else:
            items = [(Path(s["name"]).stem, "wav", s["name"]) for s in listing]
        self.open_pick("Edit which", items, lambda name: self.open_editor(name, folder),
                       "No recordings yet" if folder == "recordings" else "No samples yet", back="pick")

    def pick_recording(self, name):
        """what to do with a recording: play it on the speaker, make a sample of it, open one of its parts (each
        source on its own, with Record parts on), or delete it"""
        parts = library.recording_parts(name)
        actions = ([("Play", "", "play"), ("Edit as sample ›", "", "edit")]
                   + ([("Parts ›", str(len(parts)), "parts")] if parts else []) + [("Delete", "", "delete")])
        self.open_pick(short_date(name), actions, lambda action: self.recording_action(name, action), "")

    def pick_part(self, name):
        """a part: play it or make a sample of it (it goes with its take when that's deleted)"""
        label = library.part_label(library.PART.match(name).group(2))
        self.open_pick(label, [("Play", "", "play"), ("Edit as sample ›", "", "edit")],
                       lambda action: self.recording_action(name, action), "", back="pick")

    def recording_action(self, name, action):
        path = library.folder("recordings") / name
        if action == "play":
            if not self.sink:
                self.say("No speaker connected")
            elif self.player.start(self.sink["name"], path):
                self.player_name, self.screen = name, "player"
            else:
                self.say("Can't play (pw-play?)")
        elif action == "edit":
            self.open_editor(name)
        elif action == "parts":
            items = [(library.part_label(library.PART.match(n).group(2)), clock(wav_seconds(library.folder(
                "recordings") / n)), n) for n in library.recording_parts(name)]
            self.open_pick("Parts", items, self.pick_part, "", back="pick")
        elif action == "delete":
            parts = library.recording_parts(name)

            def delete():
                library.delete_recording(name)
                self.screen = "settings"
                self.say("Deleted")
            seconds = wav_seconds(path) if path.exists() else 0
            self.ask("Delete?", [short_date(name), f"{clock(seconds)} recorded"]
                     + ([f"and its {len(parts)} parts"] if parts else []), delete)

    def pick_firmware(self, choice):
        if isinstance(choice, tuple):         # ("fetch", "sloop" / "clipper")
            title = library.FIRMWARES[choice[1]][0]
            self.start_job(["fetch", choice[1]], f"Get {title}")
            return
        path = library.folder("firmware") / choice
        warning = ("Experimental: see its README" if library.sloop.family(choice) == "clipper"
                   else "Keep the FM-1 plugged in")
        self.ask("Install?", [Path(choice).stem[:20], warning, "(backed up first)"],
                 lambda: self.start_job(["flash", str(path)], "Firmware"))

    # ---- keys
    def library_key(self, key, held):
        """True if a Library screen took the key"""
        if self.screen == "pick":
            p = self.pick
            if key in ("up", "down"):
                p["cursor"] = (p["cursor"] + (1 if key == "down" else -1)) % len(p["items"])
            elif key in ("press", "right") and not held:
                p["on"](p["items"][p["cursor"]][2])
            elif key == "left":
                self.screen = "settings"
            return True
        if self.screen == "confirm":
            if key == "press" and held:
                self.confirm["action"]()
            elif key == "press":
                self.say("Hold to confirm", 1.5)
            elif key == "left":
                back = self.confirm["back"]
                self.screen = "settings" if back == "pick" and not self.pick else back
            return True
        if self.screen == "player":
            if key in ("press", "left", "right") and not held:
                self.player.stop()
                self.screen = "settings"
            return True
        if self.screen == "job":
            state = self.library_state()
            if key in ("left", "press") and not job_running(state):
                self.screen = self.job_back if self.job_back in ("settings", "status") else "settings"
            return True
        return False

    def draw_player(self, d):
        d.text((0, 0), "Playing", font=self.f_title, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        d.text((0, 16), short_date(self.player_name or ""), font=self.f_body, fill=1)
        now, length = self.player.seconds(), self.player.length
        text = f"{clock(now)} / {clock(length)}"
        d.text((127 - d.textlength(text, font=self.f_body), 29), text, font=self.f_body, fill=1)
        d.rectangle((0, 42, 127, 49), outline=1)
        if length:
            d.rectangle((2, 44, 2 + int(123 * min(1.0, now / length)), 47), fill=1)
        d.text((0, 53), "press = stop", font=self.f_small, fill=1)

    # ---- drawing
    def library_view(self, now):
        state = self.library_state(now) if self.screen in ("job", "status") else None
        if self.screen == "pick":
            return ("pick", self.pick["title"], self.pick["cursor"], tuple(i[:2] for i in self.pick["items"]))
        if self.screen == "confirm":
            return ("confirm", self.confirm["title"], tuple(self.confirm["lines"]))
        if self.screen == "player":
            if not self.player.playing():     # played to the end: back to Settings
                self.player.stop()
                self.screen = "settings"
                self.say("Played to the end", 1.5)
                return ("settings",)
            return ("player", self.player_name, int(self.player.seconds(now) * 2))
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
