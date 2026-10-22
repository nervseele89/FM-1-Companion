"""The box's MIDI matrix screens (mixed into bridge.App): which source goes to which destination, and per route
its channels, transpose, velocity curve and what to drop. The routing itself runs in the MIDI process
(blemidi.Route); these screens edit state["routes"] and send it there.

  Settings > MIDI routes:  the grid; joystick moves, press opens a route, left from the first column: back
  a route's page:          up/down rows; press changes (on/off), or starts editing (up/down, then press/left)
  Channels > custom:       16 rows, input channel -> output channel or off
"""
import json

import blemidi

SOURCE_NAMES = {"BT": "Bluetooth", "FM-1": "FM-1", "Box": "Box clock"}
VELOCITY = ["normal", "soft", "hard"] + [f"fixed {v}" for v in (40, 64, 80, 100, 127)]
CHANNELS = ["same"] + list(range(16)) + ["custom"]      # all to one channel (0-15), or a table


class MidiScreens:
    def midi_init(self):
        self.mx = [0, 0]                      # matrix cursor: source row, destination column
        self.route_key, self.route_row, self.route_edit = None, 0, None
        self.chan_row, self.chan_edit = 0, False

    # ---- state

    def routes(self):
        saved = self.state.get("routes") or {}
        return {key: dict(blemidi.DEFAULT_ROUTE, **saved.get(key, {})) for key in blemidi.ROUTES}

    def route(self):
        return self.routes()[self.route_key]

    def set_route(self, **changes):
        routes = self.routes()
        routes[self.route_key].update(changes)
        self.state["routes"] = routes
        self.save_settings()
        if self.midi:
            self.midi.set_routes(routes)

    def cell(self):
        source, dest = blemidi.SOURCES[self.mx[0]], blemidi.DESTINATIONS[self.mx[1]]
        return f"{source}>{dest}" if source != dest else None

    def open_matrix(self):
        self.screen = "matrix"

    # ---- keys

    def midi_key(self, key, held):
        """True if a MIDI matrix screen took the key"""
        if self.screen == "matrix":
            if key in ("up", "down"):
                self.mx[0] = (self.mx[0] + (1 if key == "down" else -1)) % len(blemidi.SOURCES)
            elif key == "right":
                self.mx[1] = min(len(blemidi.DESTINATIONS) - 1, self.mx[1] + 1)
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
                if name in ("on", "drop_clock", "drop_aftertouch", "drop_bend"):
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
        if name == "transpose":
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
                     ("drop_bend", "Drop bend", onoff(r["drop_bend"]))]
        return rows

    def route_title(self):
        source, dest = self.route_key.split(">")
        return f"{'Box' if source == 'Box' else source} > {dest}"

    def midi_view(self):
        if self.screen not in ("matrix", "route", "chanmap"):
            return None
        return (tuple(self.mx), self.route_key, self.route_row, self.route_edit, self.chan_row, self.chan_edit,
                json.dumps(self.state.get("routes"), sort_keys=True))

    # ---- drawing

    def draw_matrix(self, d):
        d.text((0, 0), "MIDI routes", font=self.f_title, fill=1)
        d.text((127 - d.textlength("< back", font=self.f_small), 2), "< back", font=self.f_small, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        cols = (68, 104)
        for x, dest in zip(cols, blemidi.DESTINATIONS):
            w = d.textlength(dest, font=self.f_small)
            d.text((x + 6 - w / 2, 15), dest, font=self.f_small, fill=1)
        routes = self.routes()
        for row, source in enumerate(blemidi.SOURCES):
            y = 29 + row * 11
            d.text((0, y - 1), SOURCE_NAMES[source], font=self.f_small, fill=1)
            for col, (x, dest) in enumerate(zip(cols, blemidi.DESTINATIONS)):
                if source == dest:
                    d.line((x + 3, y + 4, x + 9, y + 4), fill=1)          # no route to itself
                elif routes[f"{source}>{dest}"]["on"]:
                    d.rectangle((x, y, x + 12, y + 8), fill=1)
                else:
                    d.rectangle((x, y, x + 12, y + 8), outline=1)
                if [row, col] == self.mx:
                    d.rectangle((x - 3, y - 2, x + 15, y + 10), outline=1)

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

    def draw_chanmap(self, d):
        table = self.route()["channels"]
        rows = [(f"Ch {i + 1}  >", "off" if out is None else str(out + 1)) for i, out in enumerate(table)]
        hint = "up/down = value  < done" if self.chan_edit else "press = change   < back"
        self.draw_rows(d, "Channels", rows, self.chan_row, self.chan_edit, hint)
