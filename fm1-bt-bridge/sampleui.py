"""The sample editor on the box's screen: Settings > Sample editor > a recording or a sample (also
Settings > Recordings > a take > Edit as sample).

The take's waveform with the selection between two markers (outside it, dotted). Left/right steps through
the fields, up/down changes the one named at the top (held, it speeds up), press plays the selection on the
speaker as it will be saved, and press on Save writes it to the samples folder, where Sloop > Load sample
takes it from there. Left from the first field goes back. The sums are sampler.py's, shared with the web
page's editor.
"""
import threading
import wave
from pathlib import Path

import library
import meters
import sampler

FIELDS = (("start", "Start"), ("end", "End"), ("fade_in", "Fade in"), ("fade_out", "Fade out"),
          ("normalize", "Normalize"), ("reverse", "Reverse"), ("note", "Note"), ("save", "Save"))
WAVE_MID, WAVE_HALF = 32, 13                 # the waveform: rows 19..45
MARK_TOP, MARK_BOTTOM = 15, 50                # the markers reach past it, so they show over a loud take
FADE_STEP, FADE_MAX = 0.01, 2.0               # seconds


class SampleEditor:
    def editor_init(self):
        self.ed = None                         # the take being edited (see open_editor)

    def open_editor(self, name, folder="recordings"):
        """a recording or a sample; loads in the background: numpy's first import and a long take take a few
        seconds on a Zero W"""
        self.ed = {"name": name, "path": library.folder(folder) / name, "field": 0,
                   "loading": True, "error": None}
        self.screen = "editor"
        threading.Thread(target=self.editor_load, args=(self.ed,), daemon=True).start()

    def editor_load(self, ed):
        try:
            rate, x = sampler.load(ed["path"])
            start, end, note = sampler.opening(ed["path"], x, rate)
            ed.update(sampler.DEFAULT_EDIT, rate=rate, x=x, seconds=len(x) / rate, start=start, end=end,
                      note=note, found=note, wave=sampler.waveform(x, 128),
                      out_name=sampler.default_name(ed["path"]))
        except (OSError, ValueError, EOFError, wave.Error) as e:
            ed["error"] = str(e)
        ed["loading"] = False

    def editor_close(self):
        self.player.stop()
        self.ed = None                         # a long take is a lot of memory on a Zero W
        self.screen = "pick" if self.pick else "settings"

    def editor_edit(self):
        return {k: self.ed[k] for k in sampler.DEFAULT_EDIT}

    # ---- keys
    def editor_key(self, key, held):
        """True if the editor took the key"""
        if self.screen != "editor":
            return False
        ed = self.ed
        if ed["loading"]:
            if key == "left":
                self.editor_close()
            return True
        if ed["error"]:
            if key in ("left", "press"):
                self.editor_close()
            return True
        field = FIELDS[ed["field"]][0]
        if key == "left":
            if ed["field"] == 0:
                self.editor_close()
            else:
                ed["field"] -= 1
        elif key == "right":
            ed["field"] = min(len(FIELDS) - 1, ed["field"] + 1)
        elif key in ("up", "down"):
            self.editor_change(field, 1 if key == "up" else -1)
        elif key == "press" and not held:
            self.editor_save() if field == "save" else self.editor_preview()
        return True

    def editor_change(self, field, step):
        ed = self.ed
        if field in ("start", "end"):
            move = step * max(0.005, ed["seconds"] / 512)        # half a pixel of the waveform a press
            if field == "start":
                ed["start"] = round(min(max(0.0, ed["start"] + move), ed["end"] - sampler.SHORTEST), 3)
            else:
                ed["end"] = round(max(min(ed["seconds"], ed["end"] + move), ed["start"] + sampler.SHORTEST), 3)
        elif field in ("fade_in", "fade_out"):
            ed[field] = round(min(FADE_MAX, max(0.0, ed[field] + step * FADE_STEP)), 2)
        elif field in ("normalize", "reverse"):
            ed[field] = not ed[field]
        elif field == "note":
            note = ed["note"] if ed["note"] is not None else (ed["found"] or 60) - step
            ed["note"] = min(sampler.NOTES[1], max(sampler.NOTES[0], note + step))

    def editor_preview(self):
        ed = self.ed
        if not self.sink:
            self.say("No speaker connected")
            return
        try:
            path = Path(library.RUNTIME) / "fm1-preview.wav"
            sampler.write(path, ed["rate"], sampler.render(ed["x"], ed["rate"], ed["start"], ed["end"],
                                                           **self.editor_edit()))
        except (OSError, ValueError) as e:
            self.say(str(e)[:32])
            return
        if not self.player.start(self.sink["name"], path):
            self.say("Can't play (pw-play?)")

    def editor_save(self):
        ed = self.ed
        try:
            out = sampler.save_from(ed["x"], ed["rate"], ed["out_name"], ed["note"], ed["start"], ed["end"],
                                    **self.editor_edit())
        except (OSError, ValueError) as e:
            self.say(str(e)[:32])
            return
        self.say(f"Saved {out.stem[:16]}", 2.5)

    # ---- drawing
    def editor_value(self, field):
        ed = self.ed
        if field in ("start", "end"):
            return f"{ed[field]:.2f}s"
        if field in ("fade_in", "fade_out"):
            return f"{round(ed[field] * 1000)} ms"
        if field in ("normalize", "reverse"):
            return "on" if ed[field] else "off"
        if field == "note":
            if ed["note"] is None:
                return "none (C4)"
            name = meters.note_name(ed["note"])
            return f"{name} found" if ed["note"] == ed["found"] else name
        return sampler.file_name(ed["out_name"], ed["note"])[:-4]

    def editor_view(self):
        ed = self.ed
        if not ed:
            return None
        if ed["loading"] or ed["error"]:
            return ("editor", ed["loading"], ed["error"])
        return ("editor", ed["field"], ed["start"], ed["end"], ed["fade_in"], ed["fade_out"], ed["normalize"],
                ed["reverse"], ed["note"], self.player.playing())

    def draw_editor(self, d):
        ed = self.ed
        if ed["loading"] or ed["error"]:
            d.text((0, 0), "Sample editor", font=self.f_title, fill=1)
            d.line((0, 14, 127, 14), fill=1)
            lines = ["Loading the take..."] if ed["loading"] else ["Can't open it:", ed["error"]]
            for i, line in enumerate(lines):
                d.text((0, 18 + i * 12), self.fit(d, line, self.f_body, 127), font=self.f_body, fill=1)
            d.text((0, 53), "< back", font=self.f_small, fill=1)
            return
        field, label = FIELDS[ed["field"]]
        length = ed["end"] - ed["start"]
        tag = f"{length:.1f}s"
        w = d.textlength(tag, font=self.f_small)
        if length > library.SLOT_SECONDS:     # longer than a SLOOP slot holds: the tag inverted
            d.rectangle((125 - w, 0, 127, 11), fill=1)
        d.text((126 - w, 1), tag, font=self.f_small, fill=0 if length > library.SLOT_SECONDS else 1)
        top = "Save to samples" if field == "save" else f"{label} {self.editor_value(field)}"
        d.text((0, 0), self.fit(d, top, self.f_body, int(120 - w)), font=self.f_body, fill=1)
        d.line((0, 13, 127, 13), fill=1)
        sx = round(ed["start"] / ed["seconds"] * 127)
        ex = round(ed["end"] / ed["seconds"] * 127)
        for i, (lo, hi) in enumerate(ed["wave"]):
            if sx <= i <= ex or i % 2 == 0:   # outside the selection: every other column
                d.line((i, WAVE_MID - round(hi * WAVE_HALF), i, WAVE_MID - round(lo * WAVE_HALF)), fill=1)
        for x, active in ((sx, field == "start"), (ex, field == "end")):
            for side in (x - 1, x + 1):        # a dark edge each side
                d.line((side, MARK_TOP, side, MARK_BOTTOM), fill=0)
            d.line((x, MARK_TOP, x, MARK_BOTTOM), fill=1)
            if active:                         # the marker being moved: a flag at its top
                d.polygon([(x - 3, MARK_TOP), (x + 3, MARK_TOP), (x, MARK_TOP + 3)], fill=1)
        if field == "save":
            hint = self.fit(d, "press: " + self.editor_value("save"), self.f_small, 127)
        else:
            hint = ("playing  " if self.player.playing() else "press = play  ") + "<> field"
        d.text((0, 53), hint, font=self.f_small, fill=1)
