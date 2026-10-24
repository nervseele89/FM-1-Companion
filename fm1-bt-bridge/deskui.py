"""The desk box's touch screens (480x320), in SLOOP's style and FM-1 A's colours (sloopstyle.py).

A tab bar along the bottom: HOME (the sources, tempo, record / start / tap), MIX (a slider a source, and the
speaker), MIDI (the routes: tap to switch one), METER (the meters, larger) and MORE (what isn't redrawn for touch
yet: those open the pocket box's screens, scaled up, with on-screen keys; backing out of them comes back here).

Drawing and touches run on the app's own thread (touches arrive through App.post): DeskUI calls the same App
methods the keys and the web page call. Widgets are rebuilt with every frame: (box, kind, action, key, long),
kind "tap" (acts on release, still on it; long: what a press of 0.6 s or more does instead) or "slide" (follows
the finger: action(fraction from the bottom)); key: which one is lit while held.
"""

import json
import time

from PIL import Image, ImageDraw

import blemidi
import library
import sloopstyle as st

W, H = 480, 320
TAB_H, HDR_H = 44, 34
TABS = ("HOME", "MIX", "MIDI", "METER", "MORE")
MORE = [("RECORDINGS", ("pick", "recording")), ("SAMPLE EDITOR", ("pick", "editor")),
        ("SLOOP", ("menu", "sloop")), ("FM-1", ("menu", "fm1")), ("PLAYING", ("menu", "play")),
        ("AUDIO", ("menu", "audio")), ("MIDI SETTINGS", ("menu", "midi")), ("THEME", ("theme", None))]
THEMES = ["FOLLOW FM-1 A"] + st.NAMES


class DeskUI:
    def __init__(self, app, display):
        self.app, self.display = app, display
        self.tab, self.classic = "HOME", False      # classic: the pocket box's screens, scaled (MORE's items)
        self.widgets, self.held, self.shown = [], None, None
        self.theme, self.theme_seen, self.theme_check = st.Theme(), None, 0.0
        self.more_top = 0
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
        """[(mix key, label, detail, track colour index)] of the sound sources there are"""
        a = self.app
        out = []
        for key, node in a.mix_channels():
            if key == "a":
                out.append(("a", "FM-1 A" if a.source_b else "FM-1", a.fw_label or "FM-1", 0))
            elif key == "b":
                out.append(("b", "FM-1 B", a.fw_label or "FM-1", 1))
            else:
                out.append(("usb", "USB KEYS", (node.get("label") or "USB")[:12], 2))
        return out

    def view(self, now):
        a = self.app
        rec = a.recording_tick(now) if hasattr(a, "recording_tick") else None
        base = (self.tab, self.held and self.held[0], self.theme.name, id(self.theme), rec, a.live(), a.volume,
                json.dumps(a.state.get("mix")), a.state.get("rec_parts"), str(a.battery), a.clock_text(),
                a.message if now < a.message_until else None, tuple(self.sources()),
                a.sink and a.sink["label"], a.state.get("desk_theme"), self.more_top)
        if self.tab == "MIDI":
            base += (json.dumps(a.routes(), sort_keys=True), tuple(a.matrix_lists()[0]))
        if self.tab == "METER":
            base += (a.view(now),)
        return base

    def render(self, now, force=False):
        self.current_theme(now)
        view = self.view(now)
        if view == self.shown and not force:
            return
        self.shown = view
        img = Image.new("RGB", (W, H), self.theme.bg)
        self.widgets = []
        getattr(self, "draw_" + self.tab.lower())(img, ImageDraw.Draw(img), now)
        self.draw_tabs(img)
        a = self.app
        if a.message and now < a.message_until:
            self.draw_message(img, a.message)
        self.display.show_rgb(img)

    # ---- pieces

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
        rows = [(key, label, detail, k, a.mix_level(key) / 200) for key, label, detail, k in self.sources()]
        speaker = a.sink["label"].upper()[:14] if a.sink else (a.state.get("last_name") or "NONE").upper()[:14]
        rows.append(("spk", "SPEAKER", speaker, 3, (a.volume or 0) / 100))
        y = HDR_H + 4
        for key, label, detail, k, level in rows[:4]:
            tile = (8, y, 48, y + 38)
            d.rectangle(tile, fill=t.dim[k])
            letter = {"a": "A", "b": "B", "usb": "U", "spk": ">"}[key]
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
        cols = [(key, label, k, a.mix_level(key), 200) for key, label, _, k in self.sources()]
        cols.append(("spk", "SPKR", 3, a.volume or 0, 100))
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
            st.text(img, (cx - st.text_size(label)[0] // 2, HDR_H + 5), label, t.track[k])
            d.rectangle((cx - 4, top, cx + 4, bottom), fill=t.c[0])
            frac = max(0.0, min(1.0, value / most))
            y = bottom - int((bottom - top) * frac)
            d.rectangle((cx - 4, y, cx + 4, bottom), fill=t.track[k])
            hot = self.lit(("slide", key))
            d.rectangle((cx - 28, y - 9, cx + 28, y + 9), fill=t.hi if hot else t.track[k], outline=t.bg, width=2)
            v = f"{int(value)}%"
            st.text(img, (cx - st.text_size(v)[0] // 2, bottom + 8), v, t.hi)
            self.widget((x0, top - 10, x0 + width - 1, bottom + 10), "slide",
                        lambda f, key=key, most=most, top=top, bottom=bottom: self.slide(key, f, most),
                        ("slide", key))

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
        self.classic = True

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
            value = self.theme_label() if what[0] == "theme" else ">"
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
        a.screen, a.settings_menu, a.setting = "settings", None, 0
        if kind == "menu":
            a.settings_menu = arg
        else:
            keys = [r[0] for r in a.settings_rows()]
            if arg in keys:
                a.setting = keys.index(arg)
                a.change_setting()
        self.classic = True

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
                self.slide_to(y)
        elif kind == "move" and self.held:
            if self.held[1] == "slide":
                self.slide_to(y)
            elif self.held[4] and now - self.held[5] >= 0.6:    # held: its long press (a route's settings)
                long_press, self.held = self.held[4], None
                long_press()
        elif kind == "up" and self.held:
            held, self.held = self.held, None
            box = held[0]
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
