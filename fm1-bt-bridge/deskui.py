"""The desk box's touch screens (480x320), in SLOOP's style and FM-1 A's colours (sloopstyle.py).

A tab bar along the bottom: HOME (the sources, tempo, record / start / tap), MIX (a slider a source, and the
speaker), MIDI (the routes: tap to switch one), METER (the meters, larger), DAW (a Mackie Control surface for a
DAW on a computer, such as Kraken, over network MIDI: netmidi.py) and MORE (recordings, the sample editor, Sloop,
FM-1, settings, the theme, shut down). Nothing of the Pocket's screens shows here: settings, lists,
confirmations, jobs and the rest are the Studio's own pages (deskpages.py), and the sample editor has a touch
screen of its own (draw_editor: drag the markers).

Drawing and touches run on the app's own thread (touches arrive through App.post): DeskUI calls the same App
methods the keys and the web page call. Widgets are rebuilt with every frame: (box, kind, action, key, long),
kind "tap" (acts on release, still on it; long: what a press of 0.6 s or more does instead) or "slide" (follows
the finger: action(fraction from the bottom)); key: which one is lit while held.
"""

import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

import blemidi
import library
import sloopstyle as st
from deskpages import HOLD, DeskPages

W, H = 480, 320
TAB_H, HDR_H = 44, 34
TABS = ("HOME", "MIX", "MIDI", "METER", "DAW", "MORE")
DAW_KEYS = [("<<", "rewind"), (">>", "forward"), ("STOP", "stop"), ("PLAY", "play"), ("REC", "record"),
            ("LOOP", "loop")]
MORE = [("RECORDINGS", ("pick", "recording")), ("SAMPLE EDITOR", ("pick", "editor")),
        ("SLOOP", ("menu", "sloop")), ("FM-1", ("menu", "fm1")), ("PLAYING", ("menu", "play")),
        ("AUDIO", ("menu", "audio")), ("MIDI SETTINGS", ("menu", "midi")), ("WI-FI", ("wifi", None)),
        ("THEME", ("theme", None)), ("SHUT DOWN", ("off", None))]
THEMES = ["FOLLOW FM-1 A"] + st.NAMES


class DeskUI(DeskPages):
    def __init__(self, app, display):
        self.app, self.display = app, display
        self.tab, self.classic = "HOME", False      # (classic: never, now; desk.Touch still asks)
        self.page_init()
        self.widgets, self.held, self.shown = [], None, None
        self.theme, self.theme_seen, self.theme_check = st.Theme(), None, 0.0
        self.more_top = 0
        self.daw_local = {}                       # strip -> (fader value, when): ours, until the DAW's catches up
        self.ed_view, self.ed_wave, self.ed_drag = None, None, None   # the editor's zoom, its waveform, a marker
        import deskmeters
        self.meters = deskmeters.DeskMeters()     # the meters' own pictures and smoothing

    # ---- theme: FM-1 A's (library.read_theme saves it), or one picked in MORE > THEME

    def current_theme(self, now):
        if now >= self.theme_check:
            self.theme_check = now + 2
            pick = self.app.state.get("desk_theme", "follow")
            if pick != "follow":
                key = pick
            else:
                try:
                    key = library.THEME.read_text()
                except OSError:
                    key = None
            if key != self.theme_seen:
                self.theme_seen = key
                if pick != "follow":
                    self.theme = st.Theme(pick)
                else:
                    try:
                        self.theme = st.theme_load(json.loads(key)) if key else st.Theme()
                    except ValueError:
                        self.theme = st.Theme()
                self.display.theme = self.theme
        return self.theme

    # ---- what shows

    def sources(self):
        """[(mix key, label, detail, track colour index)] of the sound sources there are: the FM-1s, then any USB
        audio by its own name (detail: the input pair it takes, for an interface with more than two channels)"""
        a = self.app
        out = []
        for i, (key, node) in enumerate(a.mix_channels()):
            if key in ("a", "b"):
                out.append((key, a.channel_name(key), a.fw_label or "FM-1", 0 if key == "a" else 1))
            else:
                out.append((key, a.channel_name(key, node), a.input_label(key, node) or "USB", 2 + i % 2))
        return out

    def view(self, now):
        a = self.app
        if a.screen in self.PAGES:
            return ("page", self.page_view(now), self.theme.name, id(self.theme),
                    a.message if now < a.message_until else None, a.busy)
        if a.screen == "editor" and a.ed:
            return ("editor", self.held and self.held[3], self.theme.name, id(self.theme), self.ed_view,
                    a.editor_view(), a.message if now < a.message_until else None)
        rec = a.recording_tick(now) if hasattr(a, "recording_tick") else None
        base = (self.tab, self.held and self.held[0], self.theme.name, id(self.theme), rec, a.live(), a.volume,
                json.dumps(a.state.get("mix")), a.state.get("rec_parts"), str(a.battery), a.clock_text(),
                a.message if now < a.message_until else None, tuple(self.sources()),
                a.sink and a.sink["label"], a.state.get("desk_theme"), self.more_top)
        if self.tab == "MIDI":
            base += (json.dumps(a.routes(), sort_keys=True), tuple(a.matrix_lists()[0]))
        if self.tab == "METER":
            base += (a.view(now),)
        if self.tab == "DAW":
            base += (json.dumps(getattr(a.midi, "mcu", None)), tuple(getattr(a.midi, "net", None) or ()),
                     json.dumps(sorted(self.daw_local.items())))
        return base

    def settle(self):
        """screens the Studio shows as a tab instead: the Pocket's route grid is the MIDI tab, its settings
        list's top (where back leads) is MORE"""
        a = self.app
        if a.screen == "matrix":
            a.screen = "status"
            self.open_tab("MIDI")
        elif a.screen == "settings" and a.settings_menu is None:
            a.screen = "status"
            self.open_tab("MORE")

    def render(self, now, force=False):
        self.current_theme(now)
        self.settle()
        if self.held and self.held[1] == "hold" and now - self.held[5] >= HOLD:   # held long enough: confirmed
            held, self.held = self.held, None
            held[2]()
            self.settle()
        view = self.view(now)
        if view == self.shown and not force:
            return
        self.shown = view
        img = Image.new("RGB", (W, H), self.theme.bg)
        self.widgets = []
        if self.app.screen == "editor" and self.app.ed:     # the sample editor: a screen of its own, no tabs
            self.draw_editor(img, ImageDraw.Draw(img), now)
        elif self.app.screen in self.PAGES:                  # settings, lists, confirmations, jobs: no tabs
            self.draw_page(img, ImageDraw.Draw(img), now)
        else:
            getattr(self, "draw_" + self.tab.lower())(img, ImageDraw.Draw(img), now)
            self.draw_tabs(img)
        a = self.app
        if a.busy:
            self.draw_message(img, a.busy + "." * (int(now * 2) % 4))
        elif a.message and now < a.message_until:
            self.draw_message(img, a.message)
        self.display.show_rgb(img)

    # ---- pieces

    # ---- the sample editor: the take full width, markers to drag, big buttons for the rest

    def draw_editor(self, img, d, now):
        import meters
        a, t, ed = self.app, self.theme, self.app.ed
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< BACK", self.editor_back, key="ed-back")
        if ed["loading"] or ed["error"]:
            text = "LOADING THE TAKE..." if ed["loading"] else "CAN'T OPEN IT: " + str(ed["error"])[:30]
            st.text(img, (96, 9), ed["name"][:42], t.c[3])
            self.centre(img, (0, HDR_H, W, H), text, t.hi if ed["error"] else t.c[3])
            return
        length = ed["end"] - ed["start"]
        st.text(img, (96, 9), Path(ed["name"]).stem[:30], t.c[3])
        tag = f"{length:.2f}S"
        long = length > library.SLOT_SECONDS          # longer than a SLOOP slot holds
        tw = st.text_size(tag)[0]
        if long:
            d.rectangle((W - tw - 16, 4, W - 4, HDR_H - 6), fill=t.track[3])
        st.text(img, (W - tw - 10, 9), tag, t.bg if long else t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        # the waveform, in the view (zoomed in or the whole take)
        v0, v1 = self.ed_view or (0.0, ed["seconds"])
        top, bottom = HDR_H + 6, HDR_H + 132
        mid, half = (top + bottom) // 2, (bottom - top) // 2 - 4
        wave = self.editor_wave(ed, v0, v1)

        def x_of(sec):
            return int((sec - v0) / max(1e-6, v1 - v0) * (W - 1))
        sx, ex = x_of(ed["start"]), x_of(ed["end"])
        d.rectangle((max(0, sx), top, min(W - 1, ex), bottom), fill=t.grey[0])
        for i, (lo, hi) in enumerate(wave):
            inside = sx <= i <= ex
            if inside or i % 2 == 0:
                d.line((i, mid - int(hi * half), i, mid - int(lo * half)), fill=t.c[4] if inside else t.c[1])
        for fade, at, sign in ((ed["fade_in"], ed["start"], 1), (ed["fade_out"], ed["end"], -1)):
            if fade > 0:                              # the fades: a ramp from the marker
                d.line((x_of(at), bottom, x_of(at + sign * fade), top), fill=t.track[2], width=2)
        for x, which in ((sx, "start"), (ex, "end")):
            if 0 <= x < W:
                hot = self.ed_drag == which
                colour = t.hi if hot else t.track[0 if which == "start" else 3]
                d.rectangle((x - 1, top, x + 1, bottom), fill=colour)
                flag = (x - 14, top - 4, x + 14, top + 18) if which == "start" else (x - 14, bottom - 18, x + 14,
                                                                                      bottom + 4)
                d.rectangle(flag, fill=colour)
                st.text(img, (x - 4, flag[1] + 3), "S" if which == "start" else "E", t.bg)
        self.widget((0, top - 6, W - 1, bottom + 6), "drag", self.editor_drag, "ed-wave")
        st.text(img, (6, bottom + 6), f"{ed['start']:.2f}", t.track[0])
        end_text = f"{ed['end']:.2f}"
        st.text(img, (W - 6 - st.text_size(end_text)[0], bottom + 6), end_text, t.track[3])
        view_text = "ALL" if self.ed_view is None else f"{v0:.2f} - {v1:.2f}"
        st.text(img, ((W - st.text_size(view_text)[0]) // 2, bottom + 6), view_text, t.c[2])
        # the controls: three rows of big buttons
        y1, y2, y3 = bottom + 26, bottom + 66, bottom + 106
        self.button(img, d, (4, y1, 64, y1 + 36), "-", lambda: self.editor_zoom(2.0), key="ed-zoom-")
        self.button(img, d, (68, y1, 128, y1 + 36), "+", lambda: self.editor_zoom(0.5), key="ed-zoom+")
        self.button(img, d, (132, y1, 192, y1 + 36), "ALL", lambda: self.editor_zoom(None), key="ed-all")
        for x0, field, label in ((200, "fade_in", "IN"), (340, "fade_out", "OUT")):
            st.text(img, (x0, y1 + 2), label, t.c[2])
            st.text(img, (x0, y1 + 20), f"{round(ed[field] * 1000)}MS", t.hi)
            self.button(img, d, (x0 + 62, y1, x0 + 96, y1 + 36), "-",
                        lambda field=field: a.editor_change(field, -5), key=("ed-", field))
            self.button(img, d, (x0 + 100, y1, x0 + 134, y1 + 36), "+",
                        lambda field=field: a.editor_change(field, 5), key=("ed+", field))
        self.button(img, d, (4, y2, 154, y2 + 36), "NORMALIZE", lambda: a.editor_change("normalize", 1),
                    on=ed["normalize"], key="ed-norm")
        self.button(img, d, (160, y2, 270, y2 + 36), "REVERSE", lambda: a.editor_change("reverse", 1),
                    on=ed["reverse"], key="ed-rev")
        note = "NONE" if ed["note"] is None else meters.note_name(ed["note"])
        found = ed["note"] is not None and ed["note"] == ed["found"]
        self.button(img, d, (276, y2, 316, y2 + 36), "<", lambda: a.editor_change("note", -1), key="ed-note-")
        st.text(img, (322, y2 + 2), "NOTE", t.c[2])
        st.text(img, (322, y2 + 20), note + (" FOUND" if found else ""), t.hi)
        self.button(img, d, (436, y2, 476, y2 + 36), ">", lambda: a.editor_change("note", 1), key="ed-note+")
        playing = a.player.playing()
        self.button(img, d, (4, y3, 236, y3 + 36), "STOP" if playing else "PLAY",
                    lambda: a.player.stop() if a.player.playing() else a.editor_preview(), on=playing,
                    key="ed-play")
        self.button(img, d, (244, y3, 476, y3 + 36), "SAVE", a.editor_save, color=t.track[1], key="ed-save")


    @staticmethod
    def centre(img, box, text, colour, size=1):
        w, h = st.text_size(text, size)
        st.text(img, ((box[0] + box[2] - w) // 2, (box[1] + box[3] - h) // 2), text, colour, size)

    def editor_wave(self, ed, v0, v1):
        """(low, high) a screen column for the view, scaled to the whole take's peak (so zooming in doesn't
        make a quiet part look loud); kept until the view changes"""
        import numpy as np
        key = (id(ed["x"]), v0, v1)
        if self.ed_wave and self.ed_wave[0] == key:
            return self.ed_wave[1]
        x = ed["x"]
        if "peak" not in ed:
            ed["peak"] = max(1.0, float(np.abs(x).max()))
        part = x[int(v0 * ed["rate"]):max(int(v0 * ed["rate"]) + W, int(v1 * ed["rate"]))]
        edges = np.linspace(0, len(part), W + 1).astype(int)[:-1]
        if len(part) < W:
            wave = [(0.0, 0.0)] * W
        else:
            wave = list(zip((np.minimum.reduceat(part, edges) / ed["peak"]).tolist(),
                            (np.maximum.reduceat(part, edges) / ed["peak"]).tolist()))
        self.ed_wave = (key, wave)
        return wave

    def editor_drag(self, phase, x, y):
        """a finger on the waveform: takes the nearer marker (down) and moves it (move); lifted: lets it go"""
        import sampler
        ed = self.app.ed
        if phase == "up":
            self.ed_drag = None
            return
        v0, v1 = self.ed_view or (0.0, ed["seconds"])
        sec = v0 + max(0, min(W - 1, x)) / (W - 1) * (v1 - v0)
        if phase == "down":
            self.ed_drag = "start" if abs(sec - ed["start"]) <= abs(sec - ed["end"]) else "end"
        if self.ed_drag == "start":
            ed["start"] = round(min(max(0.0, sec), ed["end"] - sampler.SHORTEST), 3)
        elif self.ed_drag == "end":
            ed["end"] = round(max(min(ed["seconds"], sec), ed["start"] + sampler.SHORTEST), 3)

    def editor_zoom(self, factor):
        """factor 0.5: in (around the selection), 2: out; None: the whole take"""
        ed = self.app.ed
        if factor is None:
            self.ed_view = None
            return
        v0, v1 = self.ed_view or (0.0, ed["seconds"])
        width = min(ed["seconds"], max(0.05, (v1 - v0) * factor))
        centre = (ed["start"] + ed["end"]) / 2 if factor < 1 else (v0 + v1) / 2
        v0 = min(max(0.0, centre - width / 2), ed["seconds"] - width)
        self.ed_view = None if width >= ed["seconds"] else (round(v0, 4), round(v0 + width, 4))

    def editor_back(self):
        self.ed_view, self.ed_wave, self.ed_drag = None, None, None
        self.app.editor_close()

    def widget(self, box, kind, action, key=None, long=None):
        self.widgets.append((box, kind, action, key or box, long))

    def lit(self, key):
        return self.held is not None and self.held[3] == key

    def header(self, img, d, title, right=""):
        t = self.theme
        st.text(img, (10, 9), title, t.hi)
        if right:
            st.text(img, (W - 10 - st.text_size(right)[0], 9), right, t.c[3])
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)

    def status_right(self):
        a = self.app
        rec = a.recorder.seconds() if a.recorder.recording() else None
        live = f"REC {int(rec) // 60}:{int(rec) % 60:02}" if rec is not None else ("LIVE" if a.live() else "IDLE")
        bat = f"  {a.battery['percent']}%" if a.battery else ""
        return live + bat

    def button(self, img, d, box, label, action, color=None, on=False, key=None):
        t = self.theme
        key = key or label
        hot = on or self.lit(key)
        fill = (color or t.hi) if hot else t.grey[0]
        d.rectangle(box, fill=fill)
        w, h = st.text_size(label, 2 if box[3] - box[1] >= 48 else 1)
        size = 2 if box[3] - box[1] >= 48 and w < box[2] - box[0] - 8 else 1
        w, h = st.text_size(label, size)
        st.text(img, ((box[0] + box[2] - w) // 2, (box[1] + box[3] - h) // 2), label,
                t.bg if hot else (color or t.c[4]), size)
        self.widget(box, "tap", action, key)

    def draw_tabs(self, img):
        t, d = self.theme, ImageDraw.Draw(img)
        top = H - TAB_H
        d.rectangle((0, top, W, H), fill=t.bg)                # (whatever a screen drew past its bottom)
        d.line((0, top, W, top), fill=t.c[0], width=2)
        w = W // len(TABS)
        for i, name in enumerate(TABS):
            box = (i * w, top + 2, (i + 1) * w - 1, H - 1)
            on = name == self.tab and not self.classic
            if on:
                d.rectangle(box, fill=t.hi)
            tw, th = st.text_size(name)
            st.text(img, ((box[0] + box[2] - tw) // 2, (box[1] + box[3] - th) // 2), name, t.bg if on else t.c[2])
            self.widget(box, "tap", lambda name=name: self.open_tab(name), ("tab", name))

    def draw_message(self, img, message):
        t, d = self.theme, ImageDraw.Draw(img)
        s = message[:52]
        w, h = st.text_size(s)
        box = ((W - w) // 2 - 12, 120, (W + w) // 2 + 12, 120 + h + 16)
        d.rectangle(box, fill=t.bg, outline=t.hi, width=2)
        st.text(img, (box[0] + 12, box[1] + 8), s, t.hi)

    def open_tab(self, name):
        a = self.app
        if a.screen == "meter" and name != "METER":
            a.screen = "status"
            a.levels.stop()
        self.tab, self.classic = name, False
        if name == "METER":
            a.screen = "meter"

    # ---- HOME: the sources like SLOOP's TRACKS page, then record / start / tap

    def draw_home(self, img, d, now):
        a, t = self.app, self.theme
        bpm = (a.clock_text() or "").replace(" BPM", "").replace("follow", "").strip() or str(a.state["bpm"])
        st.text(img, (10, 2), bpm[:7], t.hi, 2)
        st.text(img, (12 + st.text_size(bpm[:7], 2)[0], 15), "BPM", t.c[2])
        right = self.status_right()
        st.text(img, (W - 10 - st.text_size(right)[0], 9), right, t.c[3])
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        sources = self.sources()
        rows = [(key, label, detail, k, a.mix_level(key) / 200) for key, label, detail, k in sources[:3]]
        speaker = a.sink["label"].upper()[:14] if a.sink else (a.state.get("last_name") or "NONE").upper()[:14]
        if len(sources) > 3:                  # more sources than rows: the rest are on MIX
            speaker = f"{speaker[:9]}  +{len(sources) - 3} ON MIX"
        rows.append(("spk", "SPEAKER", speaker, 3, (a.volume or 0) / 100))
        y = HDR_H + 4
        for key, label, detail, k, level in rows[:4]:
            tile = (8, y, 48, y + 38)
            d.rectangle(tile, fill=t.dim[k])
            letter = {"a": "A", "b": "B", "spk": ">"}.get(key, label[:1])
            st.text(img, (tile[0] + 12, y + 3), letter, t.track[k], 2)
            st.text(img, (58, y + 2), label, t.hi)
            st.text(img, (58 + st.text_size(label)[0] + 10, y + 2), detail.upper(), t.track[k])
            d.rectangle((58, y + 26, W - 10, y + 31), fill=t.c[0])
            d.rectangle((58, y + 26, 58 + int((W - 68) * max(0.0, min(1.0, level))), y + 31), fill=t.track[k])
            self.widget((0, y, W, y + 38), "tap", lambda: self.open_tab("MIX"), ("row", key))
            y += 42
        top = H - TAB_H - 62
        rec = a.recorder.recording()
        self.button(img, d, (8, top, 180, top + 54), "STOP REC" if rec else "REC", a.toggle_recording,
                    color=(255, 112, 92), on=rec)
        self.button(img, d, (188, top, 334, top + 54), "STOP" if a.playing else "START", a.start_stop,
                    on=a.playing)
        self.button(img, d, (342, top, 472, top + 54), "TAP", lambda: a.tap(time.monotonic()))

    # ---- MIX: a slider a source, and the speaker

    def draw_mix(self, img, d, now):
        a, t = self.app, self.theme
        parts = "ON" if a.state.get("rec_parts") else "OFF"
        self.header(img, d, "MIXER", self.status_right())
        cols = [(key, label, k, a.mix_level(key), 200) for key, label, _, k in self.sources()][:6]
        cols.append(("spk", "SPKR", 3, a.volume or 0, 100))
        nodes = dict(a.mix_channels())
        if len(self.sources()) > 1:          # Record parts: only with more than one source
            box = (150, 4, 290, HDR_H - 6)
            self.button(img, d, box, f"PARTS {parts}", self.toggle_parts, on=a.state.get("rec_parts"))
        width = W // len(cols)
        top, bottom = HDR_H + 26, H - TAB_H - 30
        for i, (key, label, k, value, most) in enumerate(cols):
            x0 = i * width
            if i:
                d.line((x0, HDR_H, x0, H - TAB_H), fill=t.c[0], width=2)
            cx = x0 + width // 2
            label = label[:max(3, width // 8 - 1)]
            st.text(img, (cx - st.text_size(label)[0] // 2, HDR_H + 5), label, t.track[k])
            pair = a.input_label(key, nodes[key]) if key in nodes and key.startswith("u-") else None
            if pair and len(a.input_pairs(nodes[key])) > 1:      # an interface: tap for its next input pair
                box = (x0 + 4, top - 8, x0 + width - 5, top + 12)
                self.button(img, d, box, pair[3:], lambda key=key: a.next_input_pair(key), key=("pair", key))
                top_k = top + 18
            else:
                top_k = top
            d.rectangle((cx - 4, top_k, cx + 4, bottom), fill=t.c[0])
            frac = max(0.0, min(1.0, value / most))
            y = bottom - int((bottom - top_k) * frac)
            d.rectangle((cx - 4, y, cx + 4, bottom), fill=t.track[k])
            hot = self.lit(("slide", key))
            d.rectangle((cx - 28, y - 9, cx + 28, y + 9), fill=t.hi if hot else t.track[k], outline=t.bg, width=2)
            v = f"{int(value)}%"
            st.text(img, (cx - st.text_size(v)[0] // 2, bottom + 8), v, t.hi)
            self.widget((x0, top_k - 10, x0 + width - 1, bottom + 10), "slide",
                        lambda f, key=key, most=most: self.slide(key, f, most), ("slide", key))

    def slide(self, key, frac, most):
        value = int(round(frac * most / (5 if key != "spk" else 2))) * (5 if key != "spk" else 2)
        value = max(0, min(most, value))
        if key == "spk":
            if self.app.sink and value != self.app.volume:
                self.app.volume_to(value)
        elif value != self.app.mix_level(key):
            self.app.set_mix_level(key, value)

    def toggle_parts(self):
        self.app.state["rec_parts"] = not self.app.state.get("rec_parts")
        self.app.save_settings()

    # ---- MIDI: the routes; tap one to switch it, hold to open its settings

    def draw_midi(self, img, d, now):
        a, t = self.app, self.theme
        self.header(img, d, "ROUTES", "HOLD = EDIT")
        sources, dests = a.matrix_lists()
        routes = a.routes()
        short = {"BT": "BT", "USB": "USB", "FM-1": "FM-1 A" if a.two_fm1s() else "FM-1", "FM-1 B": "FM-1 B",
                 "Box": "CLOCK"}
        dshort = {"FM-1": "A" if a.two_fm1s() else "FM-1", "FM-1 B": "B", "BT": "BT", "USB": "USB"}
        colour = {"BT": 0, "USB": 2, "FM-1": 1, "FM-1 B": 1, "Box": 3}
        left, top = 96, HDR_H + 26
        cw = (W - left - 8) // len(dests)
        rh = min(44, (H - TAB_H - top - 4) // len(sources))
        for j, dest in enumerate(dests):
            s = dshort[dest]
            st.text(img, (left + j * cw + (cw - st.text_size(s)[0]) // 2, HDR_H + 6), s, t.c[3])
        for i, src in enumerate(sources):
            y = top + i * rh
            st.text(img, (8, y + (rh - 16) // 2), short[src], t.track[colour[src]])
            for j, dest in enumerate(dests):
                box = (left + j * cw + 3, y + 3, left + (j + 1) * cw - 3, y + rh - 3)
                if src == dest:
                    st.text(img, ((box[0] + box[2]) // 2 - 4, (box[1] + box[3]) // 2 - 8), "-", t.c[1])
                    continue
                key = f"{src}>{dest}"
                on = routes[key]["on"]
                label = "ON" if on else "OFF"
                if on and routes[key].get("transpose"):
                    label = f"{routes[key]['transpose']:+d}"
                hot = self.lit(("route", key))
                fill = t.hi if hot else t.track[colour[src]] if on else t.grey[0]
                d.rectangle(box, fill=fill)
                tw = st.text_size(label)[0]
                st.text(img, ((box[0] + box[2] - tw) // 2, (box[1] + box[3]) // 2 - 8), label,
                        t.bg if (on or hot) else t.c[2])
                self.widget(box, "tap", lambda key=key: self.toggle_route(key), ("route", key),
                            long=lambda key=key: self.edit_route(key))

    def toggle_route(self, key):
        self.app.set_route_of(key, on=not self.app.routes()[key]["on"])

    def edit_route(self, key):
        a = self.app
        a.route_key, a.route_row, a.route_edit, a.screen = key, 0, None, "route"

    # ---- METER: the eleven meters drawn for this screen (deskmeters.py); < > in the header, or tap the meter

    def draw_meter(self, img, d, now):
        a, t = self.app, self.theme
        from meters import METERS
        name = METERS[a.meter_index()].upper()
        st.text(img, (10, 9), f"{name}  {a.meter_index() + 1}/{len(METERS)}", t.hi)
        right = self.status_right()
        st.text(img, (W - 120 - st.text_size(right)[0], 9), right, t.c[3])
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        self.button(img, d, (W - 108, 3, W - 58, HDR_H - 5), "<", lambda: self.next_meter(-1), key="meter-")
        self.button(img, d, (W - 54, 3, W - 4, HDR_H - 5), ">", lambda: self.next_meter(1), key="meter+")
        box = (0, HDR_H + 1, W, H - TAB_H - 1)
        self.meters.draw(a, img, box, t, now)
        self.widget(box, "tap", lambda: self.next_meter(1), "meter-area")

    def next_meter(self, step):
        a = self.app
        a.meter_key("down" if step > 0 else "up", False)

    # ---- DAW: a Mackie Control surface (Kraken, or any DAW that takes one) over network MIDI

    def draw_daw(self, img, d, now):
        import netmidi
        a, t = self.app, self.theme
        mcu = getattr(a.midi, "mcu", None)
        computers = getattr(a.midi, "net", None) or []
        st.text(img, (10, 9), "DAW", t.hi)
        st.text(img, (50, 9), (computers[0][:22] if computers else "NOT CONNECTED"), t.c[3] if mcu else t.c[2])
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        if not mcu:
            lines = ["PLAY KRAKEN FROM HERE:", "",
                     "1. ON THE COMPUTER, OPEN NETWORK MIDI", "   (MAC: AUDIO MIDI SETUP; WINDOWS: RTPMIDI)",
                     "2. CONNECT A SESSION TO", "   FM-1 COMPANION MCU",
                     "3. IN KRAKEN, SET THAT PORT TO MACKIE", "",
                     "FM-1 COMPANION (NO MCU) IS FOR MUSIC:", "IT IS NET IN THE MIDI ROUTES."]
            for i, line in enumerate(lines):
                st.text(img, (14, HDR_H + 8 + i * 20), line, t.hi if i == 0 else t.c[3])
            return
        self.button(img, d, (W - 108, 3, W - 58, HDR_H - 5), "<",
                    lambda: a.midi.mcu_send([netmidi.Mackie.press(0x2E)]), key="bank-")
        self.button(img, d, (W - 54, 3, W - 4, HDR_H - 5), ">",
                    lambda: a.midi.mcu_send([netmidi.Mackie.press(0x2F)]), key="bank+")
        leds = set(mcu.get("leds") or [])
        names, values = mcu["lcd"][0], mcu["lcd"][1]
        width = W // 8
        top, bottom = HDR_H + 50, HDR_H + 150
        rings, meter = mcu.get("rings") or [0] * 8, mcu.get("meters") or [0] * 8
        for n in range(8):
            x0, cx = n * width, n * width + width // 2
            if n:
                d.line((x0, HDR_H, x0, HDR_H + 196), fill=t.c[0], width=1)
            name = names[n * 7:n * 7 + 6].strip()
            selected = 24 + n in leds
            colour = t.track[n % 4]
            name_box = (x0 + 2, HDR_H + 4, x0 + width - 3, HDR_H + 24)
            if selected:
                d.rectangle(name_box, fill=colour)
            st.text(img, (cx - st.text_size(name)[0] // 2, HDR_H + 6), name, t.bg if selected else colour)
            self.widget(name_box, "tap", lambda n=n: a.midi.mcu_send([netmidi.Mackie.press(24 + n)]), ("sel", n))
            pan_box = (x0 + 4, HDR_H + 28, x0 + width - 5, HDR_H + 42)       # pan: tap left or right of the middle
            d.rectangle(pan_box, fill=t.grey[0])
            d.line((cx, pan_box[1], cx, pan_box[3]), fill=t.c[1])
            if rings[n]:
                px = pan_box[0] + 3 + (pan_box[2] - pan_box[0] - 6) * (rings[n] - 1) // 10
                d.rectangle((px - 3, pan_box[1] + 2, px + 3, pan_box[3] - 2), fill=colour)
            self.widget((pan_box[0], pan_box[1], cx - 1, pan_box[3]), "tap",
                        lambda n=n: a.midi.mcu_send([netmidi.Mackie.pan(n, -2)]), ("pan-", n))
            self.widget((cx, pan_box[1], pan_box[2], pan_box[3]), "tap",
                        lambda n=n: a.midi.mcu_send([netmidi.Mackie.pan(n, 2)]), ("pan+", n))
            local = self.daw_local.get(n)
            value = local[0] if local and now - local[1] < 1.0 else mcu["faders"][n]
            d.rectangle((cx - 3, top, cx + 3, bottom), fill=t.c[0])
            y = bottom - int((bottom - top) * value / 16383)
            d.rectangle((cx - 3, y, cx + 3, bottom), fill=colour)
            for k in range(12):                       # the DAW's level meter, beside the fader
                my = bottom - (k + 1) * (bottom - top) // 12
                lit = k < meter[n]
                level_colour = t.track[3] if k >= 11 else t.track[2] if k >= 9 else t.track[1]
                d.rectangle((x0 + width - 9, my + 1, x0 + width - 5, my + (bottom - top) // 12 - 1),
                            fill=level_colour if lit else t.c[0])
            hot = self.lit(("fader", n))
            d.rectangle((cx - 22, y - 8, cx + 22, y + 8), fill=t.hi if hot else colour, outline=t.bg, width=2)
            self.widget((x0, top - 10, x0 + width - 1, bottom + 10), "slide",
                        lambda f, n=n: self.daw_fader(n, f), ("fader", n))
            v = values[n * 7:n * 7 + 6].strip()
            st.text(img, (cx - st.text_size(v)[0] // 2, bottom + 12), v, t.c[3])
            for i, (label, note) in enumerate((("S", 8 + n), ("M", 16 + n))):
                box = (x0 + 4 + i * (width // 2 - 2), bottom + 32, x0 + (i + 1) * (width // 2 - 2), bottom + 52)
                self.button(img, d, box, label, lambda note=note: a.midi.mcu_send([netmidi.Mackie.press(note)]),
                            color=t.track[2] if label == "S" else t.track[3], on=note in leds, key=("sm", note))
        kw = (W - 16) // len(DAW_KEYS)
        y = H - TAB_H - 40
        for i, (label, what) in enumerate(DAW_KEYS):
            note = netmidi.Mackie.BUTTONS[what]
            colour = (255, 112, 92) if what == "record" else None
            self.button(img, d, (8 + i * kw, y, 8 + (i + 1) * kw - 6, y + 34), label,
                        lambda note=note: a.midi.mcu_send([netmidi.Mackie.press(note)]),
                        color=colour, on=note in leds, key=("daw", what))

    def grab(self, key, down):
        """a finger landing on (down) or lifting from a slider: on the DAW tab's faders, Mackie's touch notes"""
        if isinstance(key, tuple) and key[0] == "fader":
            import netmidi
            self.app.midi.mcu_send([netmidi.Mackie.press(netmidi.Mackie.TOUCH + key[1], down)])

    def daw_fader(self, n, frac):
        import netmidi
        value = int(frac * 16383)
        self.daw_local[n] = (value, time.monotonic())
        self.app.midi.mcu_send([netmidi.Mackie.fader(n, value)])

    # ---- MORE: what opens the pocket box's screens

    def draw_more(self, img, d, now):
        t = self.theme
        self.header(img, d, "MORE", self.status_right())
        items = MORE
        rows, rh = 5, 46
        top = HDR_H + 4
        shown = items[self.more_top:self.more_top + rows]
        for i, (label, what) in enumerate(shown):
            y = top + i * rh
            box = (8, y, W - 64, y + rh - 4)
            hot = self.lit(("more", label))
            d.rectangle(box, fill=t.hi if hot else t.grey[0])
            value = (self.theme_label() if what[0] == "theme" else
                     ("ON" if self.app.wifi else "OFF") if what[0] == "wifi" else "" if what[0] == "off" else ">")
            st.text(img, (box[0] + 12, y + 13), label, t.bg if hot else t.c[4])
            st.text(img, (box[2] - 12 - st.text_size(value)[0], y + 13), value, t.bg if hot else t.c[3])
            self.widget(box, "tap", lambda what=what: self.open_more(what), ("more", label))
        if len(items) > rows:
            self.button(img, d, (W - 56, top, W - 8, top + 2 * rh - 4), "^",
                        lambda: self.scroll_more(-1), key="more-up")
            self.button(img, d, (W - 56, top + 3 * rh, W - 8, top + 5 * rh - 4), "v",
                        lambda: self.scroll_more(1), key="more-down")

    def scroll_more(self, step):
        self.more_top = max(0, min(len(MORE) - 5, self.more_top + step * 3))

    def theme_label(self):
        pick = self.app.state.get("desk_theme", "follow")
        return "FM-1 A" if pick == "follow" else pick

    def open_more(self, what):
        a = self.app
        kind, arg = what
        if kind == "theme":                        # FOLLOW FM-1 A, then SLOOP's five palettes, round
            pick = a.state.get("desk_theme", "follow")
            names = ["follow"] + st.NAMES
            a.state["desk_theme"] = names[(names.index(pick) + 1) % len(names) if pick in names else 0]
            a.save_settings()
            self.theme_check = 0.0
            return
        if kind == "off":                          # the Studio has no power key: shut down, held to confirm
            a.ask("Shut down?", ["The Studio switches off.", "Recordings are finished first."], a.shutdown,
                  back="status")
            return
        a.settings_menu, a.setting = None, 0
        if kind == "menu":
            a.screen, a.settings_menu = "settings", arg
            return
        keys = [r[0] for r in a.settings_rows()]
        key = "wifi" if kind == "wifi" else arg
        if key in keys:                            # a top-level row: a list (recordings, editor) or Wi-Fi's switch
            a.setting = keys.index(key)
            a.change_setting()

    # ---- touch

    def touch(self, kind, x, y, now):
        """a finger down, moving or up at (x, y) (screen pixels)"""
        if kind == "down":
            self.held = None
            for w in self.widgets:
                box = w[0]
                if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                    self.held = w + (now,)            # box, kind, action, key, long, when
                    break
            if self.held and self.held[1] == "slide":
                self.grab(self.held[3], True)
                self.slide_to(y)
            elif self.held and self.held[1] == "drag":
                self.held[2]("down", x, y)
        elif kind == "move" and self.held:
            if self.held[1] == "slide":
                self.slide_to(y)
            elif self.held[1] == "drag":
                self.held[2]("move", x, y)
            elif self.held[4] and now - self.held[5] >= 0.6:    # held: its long press (a route's settings)
                long_press, self.held = self.held[4], None
                long_press()
        elif kind == "up" and self.held:
            held, self.held = self.held, None
            box = held[0]
            if held[1] == "hold":                  # let go too soon (held long enough fires in render)
                self.app.say("Hold to confirm", 1.5)
            if held[1] == "slide":
                self.grab(held[3], False)
            elif held[1] == "drag":
                held[2]("up", x, y)
            if held[1] == "tap" and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                if held[4] and now - held[5] >= 0.6:
                    held[4]()
                else:
                    held[2]()
        self.shown = None

    def slide_to(self, y):
        box = self.held[0]
        top, bottom = box[1] + 10, box[3] - 10
        self.held[2](max(0.0, min(1.0, (bottom - y) / max(1, bottom - top))))
