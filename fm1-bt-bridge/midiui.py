"""The box's MIDI matrix screens (mixed into bridge.App): which source goes to which destination, and per route
its channels, transpose, velocity curve and what to drop. The routing itself runs in the MIDI process
(blemidi.Route); these screens edit state["routes"] and send it there.

  Settings > MIDI > MIDI routes:  the grid; joystick moves, press opens a route, left from the first column: back
  a route's page:          up/down rows; press changes (on/off), or starts editing (up/down, then press/left)
  Channels > custom:       16 rows, input channel -> output channel or off
  Settings > MIDI > Route presets: four slots; press: Load / Save here / Clear
"""
import json
import time

import blemidi

SOURCE_NAMES = {"BT": "Bluetooth", "USB": "USB keys", "FM-1": "FM-1", "FM-1 B": "FM-1 B", "Box": "Box clock"}
SHORT_NAMES = {"BT": "BT", "USB": "USB", "FM-1": "FM-1 A", "FM-1 B": "FM-1 B", "Box": "Clock"}   # five rows
SHORT_DESTS = {"FM-1": "A", "FM-1 B": "B", "BT": "BT", "USB": "USB"}
VELOCITY = ["normal", "soft", "hard"] + [f"fixed {v}" for v in (40, 64, 80, 100, 127)]
CHANNELS = ["same"] + list(range(16)) + ["custom"]      # all to one channel (0-15), or a table
CYCLES = {"chord": list(blemidi.CHORDS), "arp": list(blemidi.ARPS), "arp_rate": list(blemidi.ARP_RATES),
          "arp_octaves": [1, 2, 3]}                     # route rows whose value steps through a list
PRESETS = ("1", "2", "3", "4")


class MidiScreens:
    def midi_init(self):
        self.mx = [0, 0]                      # matrix cursor: source row, destination column
        self.route_key, self.route_row, self.route_edit = None, 0, None
        self.chan_row, self.chan_edit = 0, False
        self.preset_row, self.preset_menu = 0, None     # Route presets: the slot, and its actions while open

    # ---- state

    def routes(self):
        saved = self.state.get("routes") or {}
        return {key: dict(blemidi.route_default(key), **saved.get(key, {})) for key in blemidi.ROUTES}

    def route(self):
        return self.routes()[self.route_key]

    def set_route(self, **changes):
        self.set_route_of(self.route_key, **changes)

    def set_route_of(self, key, **changes):
        routes = self.routes()
        routes[key].update(changes)
        self.state["routes"] = routes
        self.save_settings()
        if self.midi:
            self.midi.set_routes(routes)

    def two_fm1s(self):
        return bool(getattr(self.midi, "usb_b", None))

    def matrix_lists(self):
        """(sources, destinations) on the matrix: FM-1 B's row and column only while a second FM-1 is in"""
        if self.two_fm1s():
            return blemidi.SOURCES, blemidi.DESTINATIONS
        return ([s for s in blemidi.SOURCES if s != "FM-1 B"], [d for d in blemidi.DESTINATIONS if d != "FM-1 B"])

    def cell(self):
        sources, dests = self.matrix_lists()
        self.mx = [min(self.mx[0], len(sources) - 1), min(self.mx[1], len(dests) - 1)]
        source, dest = sources[self.mx[0]], dests[self.mx[1]]
        return f"{source}>{dest}" if source != dest else None

    def open_matrix(self):
        self.screen = "matrix"

    def keys_route(self):
        """the keyboard's route: Bluetooth to the FM-1 (what Settings > Playing's Arp and Chord switch, there and on
        USB keys to the FM-1 alike)"""
        return self.routes()["BT>FM-1"]

    def toggle_keys(self, name):
        on = not self.keys_route()[name]
        for key in ("BT>FM-1", "USB>FM-1", "BT>FM-1 B", "USB>FM-1 B"):
            self.set_route_of(key, **{name: on})
        r = self.keys_route()
        what = f"Arp {r['arp']} {r['arp_rate']}" if name == "arp_on" else f"Chord {r['chord']}"
        self.say(f"{what}: {'on' if on else 'off'}", 1.5)

    def open_presets(self):
        self.screen, self.preset_row, self.preset_menu = "presets", 0, None

    def presets(self):
        return self.state.get("route_presets") or {}

    def preset_actions(self):
        saved = PRESETS[self.preset_row] in self.presets()
        return (["Load"] if saved else []) + ["Save here"] + (["Clear"] if saved else [])

    def preset_do(self, action):
        self.preset_apply(PRESETS[self.preset_row], {"Load": "load", "Save here": "save", "Clear": "clear"}[action])
        self.preset_menu = None

    def preset_apply(self, slot, action, name=None):
        """load, save (with an optional name, from the web page) or clear a preset slot"""
        presets = dict(self.presets())
        title = (presets.get(slot) or {}).get("name") or f"preset {slot}"
        if action == "load" and slot in presets:
            self.state["routes"] = json.loads(json.dumps(presets[slot]["routes"]))
            if self.midi:
                self.midi.set_routes(self.routes())
            self.say(f"Loaded {title}")
        elif action == "save":
            name = (name if name is not None else (presets.get(slot) or {}).get("name") or "").strip()[:16]
            presets[slot] = {"routes": self.routes(), "saved": time.time(), "name": name}
            self.say(f"Saved as {name or f'preset {slot}'}")
        elif action == "clear":
            presets.pop(slot, None)
            self.say(f"Preset {slot} cleared")
        if action != "load":
            self.state["route_presets"] = presets
        self.save_settings()

    # ---- keys

    def midi_key(self, key, held):
        """True if a MIDI matrix screen took the key"""
        if self.screen == "presets":
            if self.preset_menu is not None:  # Load / Save here / Clear for the chosen slot
                actions = self.preset_actions()
                if key in ("up", "down"):
                    self.preset_menu = (self.preset_menu + (1 if key == "down" else -1)) % len(actions)
                elif key in ("press", "right") and not held:
                    self.preset_do(actions[self.preset_menu])
                elif key == "left":
                    self.preset_menu = None
                return True
            if key in ("up", "down"):
                self.preset_row = (self.preset_row + (1 if key == "down" else -1)) % len(PRESETS)
            elif key in ("press", "right") and not held:
                self.preset_menu = 0
            elif key == "left":
                self.screen = "settings"
            return True
        if self.screen == "matrix":
            sources, dests = self.matrix_lists()
            if key in ("up", "down"):
                self.mx[0] = (self.mx[0] + (1 if key == "down" else -1)) % len(sources)
            elif key == "right":
                self.mx[1] = min(len(dests) - 1, self.mx[1] + 1)
            elif key == "left":
                if self.mx[1]:
                    self.mx[1] -= 1
                else:
                    self.screen = "settings"
            elif key == "press" and not held and self.cell():
                self.route_key, self.route_row, self.route_edit = self.cell(), 0, None
                self.screen = "route"
            return True
        if self.screen == "route":
            rows = self.route_rows()
            if self.route_edit:
                if key in ("up", "down"):
                    self.route_adjust(self.route_edit, 1 if key == "up" else -1)
                elif key in ("press", "left", "right") and not held:
                    if self.route_edit == "channels" and self.route()["channels"] == "custom-pending":
                        self.set_route(channels=list(range(16)))
                        self.chan_row, self.chan_edit, self.screen = 0, False, "chanmap"
                    self.route_edit = None
                return True
            if key in ("up", "down"):
                self.route_row = (self.route_row + (1 if key == "down" else -1)) % len(rows)
            elif key == "left":
                self.screen = "matrix"
            elif key in ("press", "right") and not held:
                name = rows[self.route_row][0]
                if name in ("on", "drop_clock", "drop_aftertouch", "drop_bend", "chord_on", "arp_on"):
                    self.set_route(**{name: not self.route()[name]})
                elif name == "channels" and isinstance(self.route()["channels"], list):
                    self.chan_row, self.chan_edit, self.screen = 0, False, "chanmap"
                else:
                    self.route_edit = name
            return True
        if self.screen == "chanmap":
            table = self.route()["channels"]
            if self.chan_edit:
                if key in ("up", "down"):
                    choices = list(range(16)) + [None]
                    i = (choices.index(table[self.chan_row]) + (1 if key == "up" else -1)) % len(choices)
                    table[self.chan_row] = choices[i]
                    self.set_route(channels=table)
                elif key in ("press", "left", "right") and not held:
                    self.chan_edit = False
                return True
            if key in ("up", "down"):
                self.chan_row = (self.chan_row + (1 if key == "down" else -1)) % 16
            elif key == "left":
                self.screen = "route"
            elif key in ("press", "right") and not held:
                self.chan_edit = True
            return True
        return False

    def route_adjust(self, name, step):
        r = self.route()
        if name in CYCLES:
            options = CYCLES[name]
            now = r[name] if r[name] in options else options[0]
            self.set_route(**{name: options[(options.index(now) + step) % len(options)]})
        elif name == "transpose":
            self.set_route(transpose=max(-24, min(24, r["transpose"] + step)))
        elif name == "velocity":
            now = f"fixed {r['fixed']}" if r["velocity"] == "fixed" else r["velocity"]
            pick = VELOCITY[(VELOCITY.index(now) + step) % len(VELOCITY) if now in VELOCITY else 0]
            if pick.startswith("fixed"):
                self.set_route(velocity="fixed", fixed=int(pick.split()[1]))
            else:
                self.set_route(velocity=pick)
        elif name == "channels":
            now = r["channels"]
            current = "custom" if isinstance(now, list) or now == "custom-pending" else now
            pick = CHANNELS[(CHANNELS.index(current) + step) % len(CHANNELS)]
            # "custom" becomes a table when the edit ends (press): until then up/down keep cycling
            self.set_route(channels="custom-pending" if pick == "custom" else pick)

    # ---- rows and text

    def route_rows(self):
        """(name, label, value) for the open route's page"""
        r = self.route()
        onoff = lambda v: "on" if v else "off"      # noqa: E731
        rows = [("on", "Route", onoff(r["on"]))]
        if not self.route_key.startswith("Box"):
            ch = r["channels"]
            channels = ("same" if ch == "same" else f"all to {ch + 1}" if isinstance(ch, int)
                        else "custom ›" if isinstance(ch, list) else "custom")
            velocity = f"fixed {r['fixed']}" if r["velocity"] == "fixed" else r["velocity"]
            rows += [("channels", "Channels", channels), ("transpose", "Transpose", f"{r['transpose']:+d}" if r["transpose"] else "0"),
                     ("velocity", "Velocity", velocity), ("drop_clock", "Drop clock", onoff(r["drop_clock"])),
                     ("drop_aftertouch", "Drop aftert.", onoff(r["drop_aftertouch"])),
                     ("drop_bend", "Drop bend", onoff(r["drop_bend"])),
                     ("chord_on", "Chord", onoff(r["chord_on"]))]
            if r["chord_on"]:
                rows += [("chord", "Chord type", r["chord"])]
            rows += [("arp_on", "Arp", onoff(r["arp_on"]))]
            if r["arp_on"]:
                rows += [("arp", "Arp pattern", r["arp"]), ("arp_rate", "Arp rate", r["arp_rate"]),
                         ("arp_octaves", "Arp octaves", str(r["arp_octaves"]))]
        return rows

    def route_title(self):
        source, dest = self.route_key.split(">")
        name = (lambda n: "FM-1 A" if n == "FM-1" else n) if self.two_fm1s() else (lambda n: n)
        return f"{name(source)} > {name(dest)}"

    def midi_view(self):
        if self.screen not in ("matrix", "route", "chanmap", "presets"):
            return None
        return (tuple(self.mx), self.route_key, self.route_row, self.route_edit, self.chan_row, self.chan_edit,
                self.preset_row, self.preset_menu, json.dumps(self.state.get("routes"), sort_keys=True),
                json.dumps(self.presets(), sort_keys=True), self.two_fm1s())

    # ---- drawing

    def draw_matrix(self, d):
        d.text((0, 0), "Routes", font=self.f_title, fill=1)            # the columns go up here
        sources, dests = self.matrix_lists()
        two = len(dests) > 3                  # FM-1 A and B: five rows, four columns, short names
        cols, pitch, names = ((50, 70, 90, 110), 10, SHORT_NAMES) if two else ((58, 82, 106), 12, SOURCE_NAMES)
        self.mx = [min(self.mx[0], len(sources) - 1), min(self.mx[1], len(dests) - 1)]
        for x, dest in zip(cols, dests):
            label = SHORT_DESTS[dest] if two else dest
            w = d.textlength(label, font=self.f_small)
            d.text((x + 6 - w / 2, 2), label, font=self.f_small, fill=1)
        d.line((0, 13, 127, 13), fill=1)
        routes = self.routes()
        for row, source in enumerate(sources):
            y = (15 if two else 18) + row * pitch
            d.text((0, y - 1 - (1 if two else 0)), names[source], font=self.f_small, fill=1)
            for col, (x, dest) in enumerate(zip(cols, dests)):
                if source == dest:
                    d.line((x + 3, y + 3, x + 9, y + 3), fill=1)          # no route to itself
                elif routes[f"{source}>{dest}"]["on"]:
                    d.rectangle((x, y, x + 12, y + 6), fill=1)
                else:
                    d.rectangle((x, y, x + 12, y + 6), outline=1)
                if [row, col] == self.mx:
                    d.rectangle((x - 3, y - 2, x + 15, min(63, y + 8)), outline=1)

    def draw_rows(self, d, title, rows, cursor, editing, hint):
        """a scrolling list of (label, value) rows like Settings; the edited value marked with ±"""
        d.text((0, 0), title, font=self.f_title, fill=1)
        where = f"{cursor + 1}/{len(rows)}"
        d.text((127 - d.textlength(where, font=self.f_small), 2), where, font=self.f_small, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        top = min(max(0, cursor - 1), max(0, len(rows) - 3))
        for i, (label, value) in enumerate(rows[top:top + 3]):
            y, selected = 16 + i * 12, top + i == cursor
            if selected:
                d.rectangle((0, y, 127, y + 11), fill=1)
            value = ("± " + value) if selected and editing else value
            d.text((2, y), label, font=self.f_body, fill=0 if selected else 1)
            w = d.textlength(value, font=self.f_body)
            d.text((125 - w, y), value, font=self.f_body, fill=0 if selected else 1)
        d.text((0, 53), hint, font=self.f_small, fill=1)

    def draw_route(self, d):
        rows = [(label, value) for _, label, value in self.route_rows()]
        hint = "up/down = value  < done" if self.route_edit else "press = change   < back"
        self.draw_rows(d, self.route_title(), rows, self.route_row, bool(self.route_edit), hint)

    def draw_presets(self, d):
        if self.preset_menu is not None:
            actions = self.preset_actions()
            self.draw_rows(d, f"Preset {PRESETS[self.preset_row]}", [(a, "") for a in actions], self.preset_menu,
                           False, "press = do it   < back")
            return
        presets = self.presets()
        rows = []
        for slot in PRESETS:
            saved = presets.get(slot)
            when = time.strftime("%m-%d %H:%M", time.localtime(saved["saved"])) if saved else "empty"
            rows.append(((saved or {}).get("name") or f"Preset {slot}", when))
        self.draw_rows(d, "Route presets", rows, self.preset_row, False, "press = load/save  < back")

    def draw_chanmap(self, d):
        table = self.route()["channels"]
        rows = [(f"Ch {i + 1}  >", "off" if out is None else str(out + 1)) for i, out in enumerate(table)]
        hint = "up/down = value  < done" if self.chan_edit else "press = change   < back"
        self.draw_rows(d, "Channels", rows, self.chan_row, self.chan_edit, hint)
