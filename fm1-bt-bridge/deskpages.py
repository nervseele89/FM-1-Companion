"""The Studio's own pages for everything that isn't a tab: settings sections, lists (recordings, samples,
backups, firmware), a route's details and its channel table, route presets, the speaker scan, confirmations,
jobs and a take playing. Mixed into deskui.DeskUI.

They show the same state the Pocket's screens show and act through the same App methods (a row tapped is that row
chosen and pressed; - / + on a row being edited are up / down), so both boxes behave alike; only the drawing is
the Studio's, for touch: rows a fingertip wide, - / + buttons, a hold-to-confirm button, a back button.
"""

import sloopstyle as st
from libui import job_running

W, H, HDR_H = 480, 320, 34
ROW_H, ROWS = 46, 5                          # list rows: 5 to a page, 46 pixels each
HOLD = 2.0                                   # seconds to hold a confirm button, as the Pocket's 2 s press
SECTIONS = {None: "SETTINGS", "play": "PLAYING", "audio": "AUDIO", "midi": "MIDI SETTINGS", "sloop": "SLOOP",
            "fm1": "FM-1"}


class DeskPages:
    PAGES = ("settings", "pick", "confirm", "job", "player", "route", "chanmap", "presets", "scan")

    def page_init(self):
        self.page_top = {}                   # page -> its first row shown (scrolling)

    # ---- which page, and its rows

    def page_rows(self):
        """(title, [(label, value)], cursor or None, editing row or None, tap(i), back()) of the page showing"""
        a = self.app
        if a.screen == "settings":
            rows = a.settings_rows()
            editing = next((i for i, r in enumerate(rows) if a.editing == r[0]), None)
            return (SECTIONS.get(a.settings_menu, "SETTINGS"), [(r[1], r[2]) for r in rows], None, editing,
                    self.tap_setting, lambda: a.on_key("left"))
        if a.screen == "pick":
            p = a.pick
            return (p["title"].upper(), [i[:2] for i in p["items"]], None, None,
                    lambda i: (p.__setitem__("cursor", i), a.on_key("press")), lambda: a.on_key("left"))
        if a.screen == "route":
            rows = a.route_rows()
            editing = next((i for i, r in enumerate(rows) if a.route_edit == r[0]), None)
            return (a.route_title().upper(), [(r[1], r[2]) for r in rows], None, editing,
                    lambda i: (setattr(a, "route_row", i), a.on_key("press")), lambda: a.on_key("left"))
        if a.screen == "chanmap":
            table = a.route()["channels"]
            rows = [(f"IN CH {i + 1}", "OFF" if c is None else f"CH {c + 1}") for i, c in enumerate(table)]
            return ("CHANNELS", rows, None, a.chan_row if a.chan_edit else None,
                    lambda i: (setattr(a, "chan_row", i), a.on_key("press")), lambda: a.on_key("left"))
        if a.screen == "presets":
            import midiui
            if a.preset_menu is not None:
                actions = a.preset_actions()
                return (f"PRESET {midiui.PRESETS[a.preset_row]}", [(x, "") for x in actions], None, None,
                        lambda i: (setattr(a, "preset_menu", i), a.on_key("press")), lambda: a.on_key("left"))
            saved = a.presets()
            rows = [(f"PRESET {slot}", (saved[slot].get("name") or "saved") if slot in saved else "empty")
                    for slot in midiui.PRESETS]
            return ("ROUTE PRESETS", rows, None, None,
                    lambda i: (setattr(a, "preset_row", i), a.on_key("press")), lambda: a.on_key("left"))
        if a.screen == "scan":
            rows = [(dev["name"], "") for dev in a.devices]
            return ("SPEAKERS", rows, None, None,
                    lambda i: (setattr(a, "cursor", i), a.on_key("press")), lambda: a.on_key("left"))
        return None

    def tap_setting(self, i):
        a = self.app
        rows = a.settings_rows()
        key = rows[i][0]
        if key == "routes":                  # the Studio's routes are its MIDI tab
            a.screen = "status"
            self.open_tab("MIDI")
            return
        if a.editing == key:                 # tapped again: done
            a.on_key("press")
            return
        a.setting = i
        a.on_key("press")

    # ---- drawing

    def draw_page(self, img, d, now):
        a = self.app
        if a.screen == "confirm":
            return self.draw_confirm(img, d, now)
        if a.screen == "job":
            return self.draw_job(img, d, now)
        if a.screen == "player":
            return self.draw_player(img, d, now)
        page = self.page_rows()
        if page is None:
            return
        title, rows, _, editing, tap, back = page
        t = self.theme
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< BACK", back, key="page-back")
        st.text(img, (96, 9), title[:30], t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        if a.screen == "scan":
            self.draw_scan_extra(img, d, rows)
        top = self.page_top.get((a.screen, title), 0)
        if editing is not None:              # keep the row being edited on the page
            top = min(max(top, editing - ROWS + 1), editing)
        top = max(0, min(top, max(0, len(rows) - ROWS)))
        self.page_top[(a.screen, title)] = top
        width = W - 64 if len(rows) > ROWS else W - 8
        for k, (label, value) in enumerate(rows[top:top + ROWS]):
            i = top + k
            y = HDR_H + 4 + k * ROW_H
            box = (8, y, width, y + ROW_H - 6)
            hot = self.lit(("row", i))
            edit = i == editing
            d.rectangle(box, fill=t.hi if hot else t.grey[1] if edit else t.grey[0])
            fg = t.bg if hot else t.c[4]
            st.text(img, (box[0] + 12, y + 12), str(label).upper()[:34], fg)
            value = str(value or "").replace("± ", "").upper()
            if edit:                         # being changed: - / + beside it, the row again when done
                minus, plus = (box[2] - 160, y + 3, box[2] - 116, y + ROW_H - 9), (box[2] - 48, y + 3,
                                                                                   box[2] - 4, y + ROW_H - 9)
                self.button(img, d, minus, "-", lambda: a.on_key("down"), key=("minus", i))
                self.button(img, d, plus, "+", lambda: a.on_key("up"), key=("plus", i))
                vw = st.text_size(value[:7])[0]
                st.text(img, ((minus[2] + plus[0] - vw) // 2, y + 12), value[:7], t.hi)
            else:
                vw = st.text_size(value[:16])[0]
                st.text(img, (box[2] - 12 - vw, y + 12), value[:16], t.bg if hot else t.c[3])
            self.widget(box, "tap", lambda i=i: tap(i), ("row", i))
        if len(rows) > ROWS:
            key = (a.screen, title)
            self.button(img, d, (W - 56, HDR_H + 4, W - 8, HDR_H + 2 * ROW_H - 2), "^",
                        lambda: self.scroll_page(key, -3), key="page-up")
            self.button(img, d, (W - 56, HDR_H + 3 * ROW_H, W - 8, HDR_H + 5 * ROW_H - 2), "v",
                        lambda: self.scroll_page(key, 3), key="page-down")
        if editing is not None:
            hint = "TAP THE ROW AGAIN WHEN DONE"
            st.text(img, ((W - st.text_size(hint)[0]) // 2, H - 24), hint, t.c[2])

    def scroll_page(self, key, step):
        self.page_top[key] = max(0, self.page_top.get(key, 0) + step)

    def draw_scan_extra(self, img, d, rows):
        a, t = self.app, self.theme
        self.button(img, d, (W - 120, 3, W - 4, HDR_H - 5), "RESCAN", a.start_scan, key="rescan")
        if a.scanning or not rows:
            text = "SEARCHING: PUT THE SPEAKER IN PAIRING MODE" if a.scanning else "NONE FOUND: RESCAN"
            self.centre(img, (0, HDR_H, W, H), text, t.c[3])

    def draw_confirm(self, img, d, now):
        a, t = self.app, self.theme
        c = a.confirm
        st.text(img, (12, 9), str(c["title"]).upper()[:40], t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        for i, line in enumerate(c["lines"][:5]):
            st.text(img, (16, HDR_H + 14 + i * 24), str(line).upper()[:52], t.c[4])
        box = (8, H - 92, W - 8, H - 50)
        held = self.held is not None and self.held[3] == "confirm"
        progress = min(1.0, (now - self.held[5]) / HOLD) if held else 0.0
        d.rectangle(box, fill=t.grey[0])
        if progress:
            d.rectangle((box[0], box[1], box[0] + int((box[2] - box[0]) * progress), box[3]), fill=t.track[3])
        label = "KEEP HOLDING..." if held else "HOLD 2 S TO CONFIRM"
        self.centre(img, box, label, t.hi)
        self.widget(box, "hold", lambda: a.on_key("press", True), "confirm")
        self.button(img, d, (8, H - 44, W - 8, H - 6), "CANCEL", lambda: a.on_key("left"), key="cancel")

    def draw_job(self, img, d, now):
        a, t = self.app, self.theme
        s = a.library_state() or {}
        st.text(img, (12, 9), str(a.job_title).upper()[:40], t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        state = s.get("state")
        if state in ("running", None):
            st.text(img, (16, HDR_H + 20), str(s.get("step") or "STARTING").upper()[:40], t.c[4], 2)
            if s.get("percent") is not None:
                box = (16, HDR_H + 90, W - 16, HDR_H + 122)
                d.rectangle(box, outline=t.c[2], width=2)
                d.rectangle((box[0] + 4, box[1] + 4, box[0] + 4 + int((box[2] - box[0] - 8) *
                             min(100, s["percent"]) / 100), box[3] - 4), fill=t.track[1])
                pct = f"{int(s['percent'])}%"
                st.text(img, ((W - st.text_size(pct, 2)[0]) // 2, HDR_H + 132), pct, t.hi, 2)
            if s.get("job") == "flash":
                self.centre(img, (0, H - 60, W, H - 20), "DON'T UNPLUG THE FM-1", t.track[3])
            return
        ok = state == "done"
        st.text(img, (16, HDR_H + 20), "DONE" if ok else "FAILED", t.track[1] if ok else t.track[3], 2)
        words, lines, line = str(s.get("message") or "").upper().split(), [], ""
        for word in words:
            if len(line) + len(word) + 1 > 50:
                lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        for i, text in enumerate((lines + [line])[:5]):
            st.text(img, (16, HDR_H + 70 + i * 22), text, t.c[4])
        if not job_running(s):
            self.button(img, d, (8, H - 50, W - 8, H - 8), "OK", lambda: a.on_key("press"), key="job-ok")

    def draw_player(self, img, d, now):
        from libui import short_date
        from meters import clock
        a, t = self.app, self.theme
        st.text(img, (12, 9), "PLAYING", t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        st.text(img, (16, HDR_H + 20), short_date(a.player_name or "").upper(), t.c[4], 2)
        pos, length = a.player.seconds(), a.player.length
        text = f"{clock(pos)} / {clock(length)}"
        st.text(img, (W - 16 - st.text_size(text, 2)[0], HDR_H + 70), text, t.hi, 2)
        box = (16, HDR_H + 120, W - 16, HDR_H + 146)
        d.rectangle(box, outline=t.c[2], width=2)
        if length:
            d.rectangle((box[0] + 4, box[1] + 4, box[0] + 4 + int((box[2] - box[0] - 8) * min(1.0, pos / length)),
                         box[3] - 4), fill=t.track[0])
        self.button(img, d, (8, H - 54, W - 8, H - 8), "STOP", lambda: a.on_key("press"), key="player-stop")

    def page_view(self, now):
        """what a page shows (for DeskUI.view: redraw only when it changes)"""
        a = self.app
        held = self.held[3] if self.held else None
        hold = int((now - self.held[5]) * 8) if self.held and self.held[1] == "hold" else None
        if a.screen == "job":
            s = a.library_state(now) or {}
            return ("job", s.get("state"), s.get("step"), s.get("percent") and int(s["percent"]), s.get("message"),
                    held)
        if a.screen == "player":
            return ("player", a.player_name, int(a.player.seconds(now) * 2), held)
        if a.screen == "confirm":
            return ("confirm", a.confirm["title"], tuple(a.confirm["lines"]), held, hold)
        page = self.page_rows()
        return (a.screen, page and (page[0], tuple(page[1]), page[3]), held, a.screen == "scan" and a.scanning,
                tuple(sorted(self.page_top.items())))

