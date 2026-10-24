"""The Studio's PADS tab: the MPC-style sampler (pads.py) by touch. Mixed into deskui.DeskUI.

The 16 pads in four rows of SLOOP's track colours, lit while they sound (from a tap or from MIDI: notes 36-51,
or SLOOP's drum track's notes with the SLOOP layout: its sequencer plays the pads).
EDIT, then a pad: its own page (sample, level, pan, tune, mode, attack, release, choke, reverse; PLAY, SAMPLE
from any source, TRIM in the touch editor, CLEAR). The kit's name (top left): KITS (load one, save this one,
start a new one). EDITOR: the sample editor (it lives here, with the sampler), with the file last edited; its OPEN
lists the SAMPLES and TAKES (the recordings) to edit, and TO PAD puts the edit on a pad. SETUP: tap a pad for its
page (an empty pad opens it anyway).
SEQ: SLOOP's drum sequencer for the pads (pads.Sequencer): pick a pad, tap steps; up to 64 steps, 16 a page;
hold a step for its level, ratchet and nudge; LEN, DIV, SWG, QUANT; PLAY, and REC to play pads into it.
RPT: note repeat, a pad held plays again at its division (recorded too while REC runs).
FX: the punch-in FX (padfx.py, SLOOP's sixteen) on the pads' sound: hold one, or LATCH it on.
"""

from pathlib import Path

import library
import pads
import sloopstyle as st

W, H, HDR_H, TAB_H = 480, 320, 34, 44
ROW_H, ROWS = 46, 4
PAD_ROWS = [("sample", "SAMPLE"), ("level", "LEVEL"), ("pan", "PAN"), ("tune", "TUNE"), ("mode", "MODE"),
            ("attack", "ATTACK"), ("release", "RELEASE"), ("choke", "CHOKE"), ("reverse", "REVERSE")]
STEPS = {"level": 5, "pan": 5, "tune": 1, "attack": 10, "release": 25}


class DeskPads:
    def pads_init(self):
        self.pad_edit, self.pad_mode = None, False     # the pad whose page is open; EDIT on
        self.pad_top, self.kits_open = 0, False
        self.browse, self.browse_top = "samples", 0    # the editor's OPEN list: samples or takes, its scroll
        self.ed_files = False                          # the editor showing that list (OPEN) instead of the take
        self.ed_from_browse, self.ed_to_pad = False, False   # the editor: opened from BROWSE; choosing a pad
        self.pad_rec = None                            # sampling into a pad: {"pad", "rec", "path", "source"}
        self.pad_trim = None                           # the pad whose sample the editor is trimming
        self.pad_pick_source = None                    # choosing what to sample: the pad
        self.seq_open, self.seq_pad, self.seq_page = False, 0, 0   # the SEQ page: the pad being placed, the page
        self.seq_edit, self.seq_rec = None, False      # the step whose hit is being edited; live REC
        self.fx_open = False                           # the FX page
        self.fx_on, self.fx_latch = None, False        # the punch-in FX on; LATCH (a tap holds it)

    def pads_view(self):
        a = self.app
        sounding = tuple(a.pads.sounding) if a.pads else ()
        rec = self.pad_rec and (self.pad_rec["pad"], int(self.pad_rec["rec"].seconds()))
        seq = (self.seq_pad, self.seq_page, self.seq_edit, self.seq_rec, a.seq_playing(),
               a.pads and a.pads.step, a.clock_text()) if self.seq_open else None
        seq = (seq, self.fx_open, self.fx_on, self.fx_latch)
        return (str(a.state.get("kit")), sounding, self.pad_edit, self.pad_mode, self.pad_top, self.kits_open,
                self.browse, self.browse_top,
                rec, self.pad_pick_source, self.held and self.held[3], seq)

    def pads_screen(self):
        """True when a pads page (not the grid) is up: no tab bar under it"""
        return self.tab == "PADS" and (self.pad_edit is not None or self.kits_open or self.pad_rec is not None
                                       or self.pad_pick_source is not None or self.seq_open or self.fx_open)

    # ---- the grid

    def draw_pads(self, img, d, now):
        if self.pad_rec is not None:
            return self.draw_pad_rec(img, d, now)
        if self.pad_pick_source is not None:
            return self.draw_pick_source(img, d)
        if self.kits_open:
            return self.draw_kits(img, d)
        if self.pad_edit is not None:
            return self.draw_pad_page(img, d)
        if self.seq_open:
            return self.draw_seq(img, d)
        if self.fx_open:
            return self.draw_fx(img, d, now)
        a, t = self.app, self.theme
        kit = a.kit()
        self.button(img, d, (4, 3, 96, HDR_H - 5), kit["name"][:10], self.open_kits, key="kits")   # its kits
        sloop = kit["map"] == "sloop"
        repeat = a.state.get("repeat")
        buttons = [("RPT" if repeat is None else pads.DIVS[repeat], self.toggle_repeat, repeat is not None,
                    "pad-repeat"),
                   ("FX", self.open_fx, self.fx_on is not None, "fx-open"),
                   ("SLOOP" if sloop else "MPC", self.toggle_map, sloop, "pad-map"),
                   ("SEQ", self.open_seq, a.seq_playing(), "seq"),
                   ("EDITOR", self.open_sample_editor, False, "editor"),
                   ("SETUP", self.toggle_pad_mode, self.pad_mode, "pad-mode")]
        for k, (label, action, on, key) in enumerate(buttons):     # six, right to left from SETUP
            x1 = W - 4 - (len(buttons) - 1 - k) * 63
            self.button(img, d, (x1 - 60, 3, x1, HDR_H - 5), label, action, on=on, key=key)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        sounding = set(a.pads.sounding) if a.pads else set()
        gw, gh = (W - 10) // 4, (H - TAB_H - HDR_H - 8) // 4
        for i in range(pads.PADS):
            row, col = 3 - i // 4, i % 4                   # pad 1 bottom left, as on an MPC
            x0, y0 = 5 + col * gw, HDR_H + 4 + row * gh
            box = (x0 + 2, y0 + 2, x0 + gw - 3, y0 + gh - 3)
            pad = kit["pads"][i]
            colour = t.track[row % 4]
            lit = i in sounding or self.lit(("pad", i))
            fill = t.hi if lit else (t.dim[row % 4] if pad["sample"] else t.grey[0])
            d.rectangle(box, fill=fill, outline=colour if pad["sample"] else t.c[0], width=2)
            st.text(img, (box[0] + 6, box[1] + 4), str(i + 1), t.bg if lit else colour)
            name = (Path(pad["sample"]).stem[:12] if pad["sample"]
                    else f"({pads.SLOOP_DRUMS[i]})" if sloop else "TAP TO SET")   # SLOOP's drum on this key
            st.text(img, (box[0] + 6, box[3] - 20), name.upper(), t.bg if lit else (t.c[4] if pad["sample"] else t.c[1]))
            if self.pad_mode or not pad["sample"]:         # EDIT, or an empty pad: its page (to sample it)
                self.widget(box, "tap", lambda i=i: self.open_pad(i), ("pad", i))
            else:
                self.widget(box, "gate", lambda down, i=i: self.hit(i, down), ("pad", i))

    def hit(self, i, down):
        if self.app.pads:
            self.app.pads.send("tap", i, 110) if down else self.app.pads.send("untap", i)

    def toggle_repeat(self):
        """note repeat: off, 1/8, 1/16, 1/32, 16T, 8T, 1/4, off: a pad held plays again at it"""
        a = self.app
        order = [None, 1, 2, 3, 5, 4, 0]
        now = a.state.get("repeat")
        a.state["repeat"] = order[(order.index(now) + 1) % len(order)] if now in order else None
        a.save_settings()
        if a.pads:
            a.pads.send("repeat", a.state["repeat"])

    def toggle_map(self):
        """the pads as an MPC (notes 36-51) or as SLOOP's drum track: its sequencer plays them. For SLOOP the
        FM-1 > SMP route goes on (its notes to the pads)"""
        a = self.app
        kit = a.kit()
        kit["map"] = "mpc" if kit["map"] == "sloop" else "sloop"
        a.set_kit(kit)
        if kit["map"] == "sloop":
            if not a.routes()["FM-1>SMP"]["on"]:
                a.set_route_of("FM-1>SMP", on=True)
            a.say("On SLOOP: GLO > MIDI 2 > OUT = SEQ", 4)

    def toggle_pad_mode(self):
        self.pad_mode = not self.pad_mode

    def open_pad(self, i):
        self.pad_edit, self.pad_top = i, 0

    # ---- a pad's page

    def pad_value(self, pad, key):
        v = pad[key]
        if key == "sample":
            return Path(v).stem.upper()[:16] if v else "NONE"
        if key in ("attack", "release"):
            return f"{v}MS"
        if key == "pan":
            return "C" if v == 0 else f"{'L' if v < 0 else 'R'}{abs(v)}"
        if key == "tune":
            return f"{v:+d} ST"
        if key == "level":
            return f"{v}%"
        if key == "choke":
            return "OFF" if not v else f"GROUP {v}"
        if key == "reverse":
            return "ON" if v else "OFF"
        return str(v).upper()

    def draw_pad_page(self, img, d):
        a, t = self.app, self.theme
        i = self.pad_edit
        kit = a.kit()
        pad = kit["pads"][i]
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< BACK", self.close_pad, key="pad-back")
        st.text(img, (96, 9), f"PAD {i + 1}", t.track[(3 - i // 4) % 4])
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        top = max(0, min(self.pad_top, len(PAD_ROWS) - ROWS))
        for k, (key, label) in enumerate(PAD_ROWS[top:top + ROWS]):
            y = HDR_H + 4 + k * ROW_H
            box = (8, y, W - 64, y + ROW_H - 6)
            d.rectangle(box, fill=t.grey[0])
            st.text(img, (box[0] + 12, y + 12), label, t.c[4])
            value = self.pad_value(pad, key)
            if key in STEPS:                                 # numbers: - value +
                minus, plus = (box[2] - 160, y + 3, box[2] - 116, y + ROW_H - 9), (box[2] - 48, y + 3, box[2] - 4,
                                                                                   y + ROW_H - 9)
                self.button(img, d, minus, "-", lambda key=key: self.pad_step(key, -1), key=("pad-", key))
                self.button(img, d, plus, "+", lambda key=key: self.pad_step(key, 1), key=("pad+", key))
                vw = st.text_size(value[:7])[0]
                st.text(img, ((minus[2] + plus[0] - vw) // 2, y + 12), value[:7], t.hi)
            else:                                            # a choice or the sample: tap the row
                vw = st.text_size(value)[0]
                st.text(img, (box[2] - 12 - vw, y + 12), value, t.c[3])
                self.widget(box, "tap", lambda key=key: self.pad_choose(key), ("pad-row", key))
        self.button(img, d, (W - 56, HDR_H + 4, W - 8, HDR_H + 2 * ROW_H - 2), "^",
                    lambda: self.pad_scroll(-2), key="pad-up")
        self.button(img, d, (W - 56, HDR_H + 2 * ROW_H + 2, W - 8, HDR_H + 4 * ROW_H - 6), "v",
                    lambda: self.pad_scroll(2), key="pad-down")
        y = H - 92
        actions = [("PLAY", lambda: self.hit(i, True)), ("SAMPLE", lambda: self.pick_source(i)),
                   ("TRIM", lambda: self.trim(i)), ("CLEAR", lambda: self.clear_pad(i))]
        bw = (W - 16) // len(actions)
        for k, (label, action) in enumerate(actions):
            self.button(img, d, (8 + k * bw, y, 8 + (k + 1) * bw - 6, y + 40), label, action,
                        color=(255, 112, 92) if label == "SAMPLE" else None, key=("pad-act", label))
        if kit["map"] == "sloop":
            tip = f"SLOOP'S {pads.SLOOP_DRUMS[i]} (DRUM KEY {i + 1}) PLAYS IT"
        else:
            tip = "NOTE " + __import__("meters").note_name(pads.note_of(i)) + " PLAYS IT"
        st.text(img, ((W - st.text_size(tip)[0]) // 2, H - 40), tip, t.c[2])

    def pad_scroll(self, step):
        self.pad_top = max(0, min(len(PAD_ROWS) - ROWS, self.pad_top + step))

    def edit_pad(self, **changes):
        kit = self.app.kit()
        kit["pads"][self.pad_edit].update(changes)
        self.app.set_kit(kit)

    def pad_step(self, key, step):
        pad = self.app.kit()["pads"][self.pad_edit]
        self.edit_pad(**{key: pad[key] + step * STEPS[key]})          # (clean_kit keeps it in range)

    def pad_choose(self, key):
        a = self.app
        pad = a.kit()["pads"][self.pad_edit]
        if key == "mode":
            self.edit_pad(mode=pads.MODES[(pads.MODES.index(pad["mode"]) + 1) % len(pads.MODES)])
        elif key == "choke":
            self.edit_pad(choke=(pad["choke"] + 1) % 5)
        elif key == "reverse":
            self.edit_pad(reverse=not pad["reverse"])
        elif key == "sample":                                # the samples folder, newest first
            items = [(Path(s["name"]).stem, "", s["name"]) for s in library.listing()["samples"]]
            a.open_pick("Sample for pad", items, self.assign_sample, "No samples yet: SAMPLE one")

    def assign_sample(self, name):
        self.edit_pad(sample=str(library.folder("samples") / name))
        self.app.screen = "status"

    def clear_pad(self, i):
        kit = self.app.kit()
        kit["pads"][i] = dict(pads.DEFAULT_PAD)
        self.app.set_kit(kit)

    def close_pad(self):
        self.pad_edit = None

    def trim(self, i):
        a = self.app
        sample = a.kit()["pads"][i]["sample"]
        if not sample or not Path(sample).exists():
            a.say("No sample on this pad", 2)
            return
        path = Path(sample)
        if path.parent != library.folder("samples"):
            a.say("Only samples in the samples folder", 2)
            return
        self.pad_trim = i
        a.state["editor_last"] = [path.name, "samples"]
        a.open_editor(path.name, "samples")

    def trimmed(self):
        """the editor closed: a trimmed pad's new sample goes back on its pad"""
        a = self.app
        if self.pad_trim is not None and a.ed and a.ed.get("saved"):
            kit = a.kit()
            kit["pads"][self.pad_trim]["sample"] = str(a.ed["saved"])
            a.set_kit(kit)
        if self.pad_trim is not None:
            self.tab, self.pad_edit = "PADS", self.pad_trim
        self.pad_trim = None

    # ---- sampling into a pad: choose a source, record, stop

    def pick_source(self, i):
        if not self.app.mix_channels():
            self.app.say("Nothing to sample: no sound source", 2)
            return
        self.pad_pick_source = i

    def draw_pick_source(self, img, d):
        a, t = self.app, self.theme
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< BACK", self.cancel_pick_source, key="src-back")
        st.text(img, (96, 9), f"SAMPLE PAD {self.pad_pick_source + 1} FROM", t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        sources = [(k, n) for k, n in a.mix_channels() if k != "u-sampler"][:5]
        for k, (key, node) in enumerate(sources):
            y = HDR_H + 4 + k * ROW_H
            box = (8, y, W - 8, y + ROW_H - 6)
            hot = self.lit(("src", key))
            d.rectangle(box, fill=t.hi if hot else t.grey[0])
            st.text(img, (box[0] + 12, y + 12), a.channel_name(key, node), t.bg if hot else t.c[4])
            self.widget(box, "tap", lambda key=key, node=node: self.start_pad_rec(key, node), ("src", key))

    def cancel_pick_source(self):
        self.pad_pick_source = None

    def start_pad_rec(self, key, node):
        from meters import Recorder
        a = self.app
        i, self.pad_pick_source = self.pad_pick_source, None
        path = Path(library.RUNTIME) / f"fm1-pad-{i + 1}.wav"
        rec = Recorder()
        if not rec.start(node["name"], path, a.state["audio_rate"]):
            a.say("Can't record (pw-record?)", 2)
            return
        self.pad_rec = {"pad": i, "rec": rec, "path": path, "source": a.channel_name(key, node)}

    def draw_pad_rec(self, img, d, now):
        t, r = self.theme, self.pad_rec
        st.text(img, (12, 9), f"SAMPLING PAD {r['pad'] + 1}", (255, 112, 92))
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        st.text(img, (16, HDR_H + 20), "FROM " + r["source"], t.c[3])
        secs = r["rec"].seconds()
        text = f"{int(secs) // 60}:{int(secs) % 60:02}"
        self.centre(img, (0, HDR_H + 40, W, HDR_H + 150), text, t.hi, 4)
        st.text(img, (16, HDR_H + 160), "PLAY THE SOUND, THEN STOP. THE SILENCE", t.c[2])
        st.text(img, (16, HDR_H + 180), "AROUND IT IS TRIMMED OFF.", t.c[2])
        self.button(img, d, (8, H - 54, W - 8, H - 8), "STOP", self.stop_pad_rec, color=(255, 112, 92),
                    on=True, key="pad-stop")

    def stop_pad_rec(self):
        import sampler
        a, r = self.app, self.pad_rec
        self.pad_rec = None
        r["rec"].stop()
        try:
            rate, x = sampler.load(r["path"])
            start, end, note = sampler.opening(r["path"], x, rate)
            out = sampler.save_from(x, rate, f"pad{r['pad'] + 1}", note, start, end, **sampler.DEFAULT_EDIT)
        except (OSError, ValueError, EOFError) as e:
            a.say(f"Couldn't sample: {e}"[:40], 3)
            return
        finally:
            Path(r["path"]).unlink(missing_ok=True)
        kit = a.kit()
        kit["pads"][r["pad"]]["sample"] = str(out)
        a.set_kit(kit)
        self.pad_edit = r["pad"]
        a.say(f"Pad {r['pad'] + 1}: {out.stem[:16]}", 2.5)

    # ---- SEQ: SLOOP's drum sequencer for the pads

    def open_seq(self):
        self.seq_open, self.seq_edit = True, None

    def close_seq(self):
        self.seq_open, self.seq_edit = False, None

    def seq(self):
        return self.app.kit()["seq"]

    def set_seq(self, seq):
        kit = self.app.kit()
        kit["seq"] = seq
        self.app.set_kit(kit)

    def draw_seq(self, img, d):
        a, t = self.app, self.theme
        seq, kit = self.seq(), a.kit()
        playing = a.seq_playing()
        step = a.pads.step if a.pads and playing else None
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< PADS", self.close_seq, key="seq-back")
        sample = kit["pads"][self.seq_pad]["sample"]
        name = f"PAD {self.seq_pad + 1} " + (Path(sample).stem.upper()[:6] if sample else "EMPTY")
        st.text(img, (92, 3), name, t.track[(3 - self.seq_pad // 4) % 4])
        where = f"{step + 1}/{seq['len']}" if step is not None else f"{seq['len']} STEPS"
        st.text(img, (92, 18), f"{where}  {self.tempo_text()[:10]}", t.c[2])
        self.button(img, d, (W - 172, 3, W - 90, HDR_H - 5), "REC", self.toggle_seq_rec, color=(255, 112, 92),
                    on=self.seq_rec, key="seq-rec")
        self.button(img, d, (W - 86, 3, W - 4, HDR_H - 5), "STOP" if playing else "PLAY", a.seq_play,
                    on=playing, key="seq-play")
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        # the pads, in a row: the one the steps place
        pw = W // 8                                         # two rows of eight: a fingertip each
        for i in range(pads.PADS):
            r = 1 - i // 8                                  # (pads 1-8 the bottom row, as on the grid)
            box = (i % 8 * pw + 1, HDR_H + 3 + r * 32, (i % 8 + 1) * pw - 2, HDR_H + 33 + r * 32)
            colour = t.track[(3 - i // 4) % 4]
            on = i == self.seq_pad
            used = any(h[0] == i for st_ in seq["steps"][:seq["len"]] for h in st_)
            d.rectangle(box, fill=colour if on else t.grey[0], outline=colour if used else t.c[0])
            label = str(i + 1)
            st.text(img, ((box[0] + box[2] - st.text_size(label)[0]) // 2, box[1] + 7), label,
                    t.bg if on else colour)
            self.widget(box, "tap", lambda i=i: self.seq_pick(i), ("seq-pad", i))
        if self.seq_edit is not None:
            return self.draw_seq_hit(img, d, seq)
        # 16 steps of the page, two rows of 8
        pages = (seq["len"] + 15) // 16
        self.seq_page = min(self.seq_page, pages - 1)
        colour = t.track[(3 - self.seq_pad // 4) % 4]
        sw, sh, top = W // 8, 46, HDR_H + 68
        for k in range(16):
            idx = self.seq_page * 16 + k
            box = (k % 8 * sw + 3, top + k // 8 * (sh + 4), (k % 8 + 1) * sw - 4, top + k // 8 * (sh + 4) + sh)
            if idx >= seq["len"]:
                d.rectangle(box, outline=t.c[0])
                continue
            hits = seq["steps"][idx]
            mine = next((h for h in hits if h[0] == self.seq_pad), None)
            here = idx == step
            fill = t.hi if here else colour if mine else t.grey[1] if k % 4 == 0 else t.grey[0]
            if mine and mine[1] == 1 and not here:          # a ghost hit: dimmer
                fill = t.dim[(3 - self.seq_pad // 4) % 4]
            d.rectangle(box, fill=fill)
            st.text(img, (box[0] + 4, box[1] + 3), str(idx + 1), t.bg if (mine or here) else t.c[2])
            if mine and mine[2] > 1:                        # ratchet notches
                for r in range(mine[2]):
                    d.rectangle((box[2] - 8 - r * 7, box[3] - 9, box[2] - 4 - r * 7, box[3] - 4), fill=t.bg)
            if mine and mine[1] == 3:
                st.text(img, (box[2] - 16, box[1] + 3), "!", t.bg)
            others = len([h for h in hits if h[0] != self.seq_pad])
            for o in range(min(others, 6)):                 # the other pads on this step: dots
                d.rectangle((box[0] + 4 + o * 6, box[3] - 8, box[0] + 7 + o * 6, box[3] - 5),
                            fill=t.bg if (mine or here) else t.c[3])
            if seq["micro"][idx]:
                d.line((box[0] + 2, box[1] + 1, box[2] - 2, box[1] + 1), fill=t.track[2], width=2)
            self.widget(box, "tap", lambda idx=idx: self.seq_toggle(idx), ("seq-step", idx),
                        lambda idx=idx: self.seq_hold(idx))
        y = top + 2 * (sh + 4) + 4
        for pg in range(4):                                 # the pages
            box = (4 + pg * 50, y, 50 + pg * 50, y + 36)
            if pg < pages:
                self.button(img, d, box, str(pg + 1), lambda pg=pg: self.seq_go_page(pg), on=pg == self.seq_page,
                            key=("seq-page", pg))
        self.button(img, d, (210, y, 316, y + 36), "DIV " + pads.DIVS[seq["div"]], self.seq_div, key="seq-div")
        self.button(img, d, (320, y, 476, y + 36), "QUANT " + ("ON" if seq["quant"] else "OFF"), self.seq_quant,
                    on=seq["quant"], key="seq-quant")
        y += 42
        self.button(img, d, (4, y, 48, y + 36), "-", lambda: self.seq_change("len", -1), key="seq-len-")
        st.text(img, (54, y + 11), f"LEN {seq['len']}", t.hi)
        self.button(img, d, (118, y, 162, y + 36), "+", lambda: self.seq_change("len", 1), key="seq-len+")
        self.button(img, d, (170, y, 214, y + 36), "-", lambda: self.seq_change("swing", -5), key="seq-swg-")
        st.text(img, (220, y + 11), f"SWG {seq['swing']}", t.hi)
        self.button(img, d, (284, y, 328, y + 36), "+", lambda: self.seq_change("swing", 5), key="seq-swg+")
        self.widget((336, y, 476, y + 36), "hold", self.seq_clear, "seq-clear")
        d.rectangle((336, y, 476, y + 36), fill=t.hi if self.lit("seq-clear") else t.grey[0])
        self.centre(img, (336, y, 476, y + 36), "HOLD: CLEAR", t.bg if self.lit("seq-clear") else t.c[4])

    def draw_seq_hit(self, img, d, seq):
        """a hit's sheet: the selected pad on step seq_edit: level, ratchet, the step's nudge"""
        t = self.theme
        idx = self.seq_edit
        hit = next((h for h in seq["steps"][idx] if h[0] == self.seq_pad), None)
        top = HDR_H + 68
        st.text(img, (12, top), f"STEP {idx + 1}  PAD {self.seq_pad + 1}", t.hi)
        if hit is None:
            st.text(img, (12, top + 30), "NO HIT HERE: TAP + TO PLACE ONE", t.c[3])
            self.button(img, d, (12, top + 60, 120, top + 100), "+", lambda: self.seq_toggle(idx), key="seq-place")
        rows = [("LEVEL", pads.LEVELS[hit[1]] if hit else "-", lambda: self.seq_hit_change(1)),
                ("RATCHET", f"X{hit[2]}" if hit else "-", lambda: self.seq_hit_change(2)),
                ("NUDGE", f"{seq['micro'][idx]:+d}/64", None)] if hit else []
        for k, (label, value, action) in enumerate(rows):
            y = top + 22 + k * 44
            box = (8, y, W - 8, y + 40)
            d.rectangle(box, fill=t.grey[0])
            st.text(img, (box[0] + 12, y + 14), label, t.c[4])
            if action:
                st.text(img, (box[2] - 12 - st.text_size(value)[0], y + 14), value, t.hi)
                self.widget(box, "tap", action, ("seq-row", label))
            else:                                           # the nudge: - value +
                self.button(img, d, (box[2] - 200, y + 2, box[2] - 150, y + 38), "-", lambda: self.seq_nudge(-4),
                            key="seq-nudge-")
                self.button(img, d, (box[2] - 54, y + 2, box[2] - 4, y + 38), "+", lambda: self.seq_nudge(4),
                            key="seq-nudge+")
                st.text(img, (box[2] - 140, y + 12), value, t.hi)
        self.button(img, d, (8, H - 50, W - 8, H - 8), "DONE", self.seq_done, key="seq-done")

    def seq_pick(self, i):
        self.seq_pad = i
        if self.app.pads and not self.app.seq_playing():
            self.app.pads.send("tap", i, 100)               # (stopped: heard as it's picked)

    def seq_toggle(self, idx):
        seq = self.seq()
        hits = seq["steps"][idx]
        if any(h[0] == self.seq_pad for h in hits):
            seq["steps"][idx] = [h for h in hits if h[0] != self.seq_pad]
        else:
            seq["steps"][idx] = sorted(hits + [[self.seq_pad, 0, 1]])
        self.set_seq(seq)

    def seq_hold(self, idx):
        self.seq_edit = idx

    def seq_done(self):
        self.seq_edit = None

    def seq_hit_change(self, field):
        """LEVEL: NORM, HARD, GHOST, SOFT round; RATCHET: x1..x4 round"""
        seq, idx = self.seq(), self.seq_edit
        for h in seq["steps"][idx]:
            if h[0] == self.seq_pad:
                h[field] = ({0: 3, 3: 1, 1: 2, 2: 0}[h[1]] if field == 1 else h[2] % 4 + 1)
        self.set_seq(seq)

    def seq_nudge(self, step):
        seq, idx = self.seq(), self.seq_edit
        seq["micro"][idx] = max(-32, min(31, seq["micro"][idx] + step))
        self.set_seq(seq)

    def seq_go_page(self, pg):
        self.seq_page = pg

    def seq_div(self):
        seq = self.seq()
        seq["div"] = (seq["div"] + 1) % len(pads.DIVS)
        self.set_seq(seq)

    def seq_quant(self):
        seq = self.seq()
        seq["quant"] = not seq["quant"]
        self.set_seq(seq)

    def seq_change(self, key, step):
        seq = self.seq()
        seq[key] += step                                    # (clean_seq keeps it in range)
        self.set_seq(seq)

    def seq_clear(self):
        seq = self.seq()
        seq["steps"], seq["micro"] = [[] for _ in range(pads.NSTEP)], [0] * pads.NSTEP
        self.set_seq(seq)
        self.app.say("Pattern cleared", 1.5)

    def toggle_seq_rec(self):
        self.seq_rec = not self.seq_rec
        if self.app.pads:
            self.app.pads.send("rec", self.seq_rec)
        if self.seq_rec and not self.app.seq_playing():
            self.app.say("REC: press PLAY, then play the pads", 2)

    # ---- FX: the punch-in FX (padfx.py) on the pads' sound, held (or latched: one finger on this screen)

    def open_fx(self):
        self.fx_open = True

    def close_fx(self):
        self.fx_open = False                           # (a latched FX stays on: play the pads under it)

    def draw_fx(self, img, d, now):
        import padfx
        a, t = self.app, self.theme
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< PADS", self.close_fx, key="fx-back")
        st.text(img, (96, 9), "PUNCH-IN FX", t.hi)
        st.text(img, (210, 9), self.tempo_text()[:12], t.c[2])
        self.button(img, d, (W - 104, 3, W - 4, HDR_H - 5), "LATCH", self.toggle_fx_latch, on=self.fx_latch,
                    key="fx-latch")
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        gw, gh = (W - 10) // 4, (H - HDR_H - 8) // 4
        for i, name in enumerate(padfx.NAMES):
            row, col = i // 4, i % 4
            x0, y0 = 5 + col * gw, HDR_H + 4 + row * gh
            box = (x0 + 2, y0 + 2, x0 + gw - 3, y0 + gh - 3)
            colour = t.track[row % 4]
            on = self.fx_on == i
            d.rectangle(box, fill=colour if on else t.dim[row % 4], outline=colour, width=2)
            st.text(img, (box[0] + 6, box[1] + 4), str(i + 1), t.bg if on else colour)
            st.text(img, (box[0] + 6, box[3] - 20), name, t.bg if on else t.c[4])
            if self.fx_latch:
                self.widget(box, "tap", lambda i=i: self.fx_tap(i), ("fx", i))
            else:
                self.widget(box, "gate", lambda down, i=i: self.fx_hold(i, down), ("fx", i))

    def fx_send(self, fx):
        self.fx_on = fx
        if self.app.pads:
            self.app.pads.send("fx", fx)

    def fx_hold(self, i, down):
        self.fx_send(i if down else None)

    def fx_tap(self, i):
        """LATCH: a tap holds it on, a tap on it again (or another) lets go / changes"""
        self.fx_send(None if self.fx_on == i else i)

    def toggle_fx_latch(self):
        self.fx_latch = not self.fx_latch
        if not self.fx_latch and self.fx_on is not None:
            self.fx_send(None)

    # ---- kits

    def open_kits(self):
        self.kits_open = True

    def draw_kits(self, img, d):
        a, t = self.app, self.theme
        self.button(img, d, (4, 3, 84, HDR_H - 5), "< PADS", self.close_kits, key="kits-back")
        st.text(img, (96, 9), "KITS", t.hi)
        d.line((0, HDR_H - 1, W, HDR_H - 1), fill=t.c[0], width=2)
        kits = pads.list_kits()[:4]
        current = a.kit()["name"]
        for k, name in enumerate(kits):
            y = HDR_H + 4 + k * ROW_H
            box = (8, y, W - 8, y + ROW_H - 6)
            hot = self.lit(("kit", name))
            on = name.replace("-", " ").upper() == current.upper()
            d.rectangle(box, fill=t.hi if hot else t.grey[1] if on else t.grey[0])
            st.text(img, (box[0] + 12, y + 12), name.upper(), t.bg if hot else t.c[4])
            st.text(img, (box[2] - 60, y + 12), "LOAD", t.bg if hot else t.c[3])
            self.widget(box, "tap", lambda name=name: self.load_kit(name), ("kit", name))
        if not kits:
            self.centre(img, (0, HDR_H, W, H - 60), "NO KITS SAVED YET", t.c[2])
        y = H - 54
        self.button(img, d, (8, y, W // 2 - 4, H - 8), "SAVE " + current[:10], self.save_kit, key="kit-save")
        self.button(img, d, (W // 2 + 4, y, W - 8, H - 8), "NEW KIT", self.new_kit, key="kit-new")

    def close_kits(self):
        self.kits_open = False

    def browse_to(self, name):
        self.browse, self.browse_top = name, 0

    def browse_files(self):
        """[(file name, its folder, what shows on the right)] of the section, newest first"""
        from meters import clock
        listing = library.listing()
        if self.browse == "samples":
            return [(s["name"], "samples", "") for s in listing["samples"]]
        return [(r["name"], "recordings", clock(r.get("seconds", 0))) for r in listing["recordings"]]

    def draw_files(self, img, d):
        """the editor's OPEN: SAMPLES or TAKES, newest first; tap one to edit it"""
        t = self.theme
        for k, (name, label) in enumerate((("samples", "SAMPLES"), ("takes", "TAKES"))):
            self.button(img, d, (8 + k * 120, HDR_H + 4, 122 + k * 120, HDR_H + 38), label,
                        lambda name=name: self.browse_to(name), on=self.browse == name, key=("browse", name))
        files = self.browse_files()
        rows, top = 5, HDR_H + 44
        self.browse_top = max(0, min(self.browse_top, len(files) - rows))
        for k, (name, folder, right) in enumerate(files[self.browse_top:self.browse_top + rows]):
            y = top + k * ROW_H
            box = (8, y, W - 64, y + ROW_H - 6)
            hot = self.lit(("file", name))
            d.rectangle(box, fill=t.hi if hot else t.grey[0])
            st.text(img, (box[0] + 12, y + 12), Path(name).stem.upper()[:26], t.bg if hot else t.c[4])
            tag = (right + "  EDIT").strip()
            st.text(img, (box[2] - 12 - st.text_size(tag)[0], y + 12), tag, t.bg if hot else t.c[3])
            self.widget(box, "tap", lambda name=name, folder=folder: self.browse_edit(name, folder), ("file", name))
        if not files:
            self.centre(img, (0, top, W, H - 20), "NO SAMPLES YET: SAMPLE ONE ON A PAD" if self.browse == "samples"
                        else "NO RECORDINGS YET: RECORD ONE ON RECORDER", t.c[2])
        if len(files) > rows:
            self.button(img, d, (W - 56, top, W - 8, top + 2 * ROW_H - 6), "^",
                        lambda: self.browse_scroll(-3), key="browse-up")
            self.button(img, d, (W - 56, top + 3 * ROW_H, W - 8, top + 5 * ROW_H - 6), "v",
                        lambda: self.browse_scroll(3), key="browse-down")

    def browse_scroll(self, step):
        self.browse_top = max(0, self.browse_top + step)

    def browse_edit(self, name, folder):
        """a sample or a take from OPEN into the touch editor (remembered: the EDITOR button opens it again)"""
        a = self.app
        self.ed_from_browse, self.ed_files = True, False
        self.ed_view, self.ed_wave, self.ed_drag = None, None, None
        a.state["editor_last"] = [name, folder]
        a.save_settings()
        a.open_editor(name, folder)

    def open_sample_editor(self):
        """EDITOR: the sample editor with the file last edited, or its OPEN list the first time"""
        a = self.app
        last = a.state.get("editor_last")
        self.ed_from_browse = True
        if last and (library.folder(last[1]) / last[0]).exists():
            self.ed_files = False
            a.open_editor(*last)
        else:
            a.ed, a.screen, self.ed_files = None, "editor", True

    def editor_files(self):
        """the editor's OPEN: the list, or back to the take"""
        self.ed_files = not self.ed_files or not self.app.ed

    def to_pad(self, i):
        """the editor's TO PAD: the edit saved as a new sample, on pad i, and its page"""
        a = self.app
        self.ed_to_pad = False
        a.editor_save()
        saved = a.ed and a.ed.get("saved")
        if not saved:
            return
        kit = a.kit()
        kit["pads"][i]["sample"] = str(saved)
        a.set_kit(kit)
        self.pad_trim, self.ed_from_browse = None, False
        self.ed_view, self.ed_wave, self.ed_drag = None, None, None
        a.editor_close()
        a.screen, self.tab, self.kits_open, self.pad_edit, self.pad_mode = "status", "PADS", False, i, False
        a.say(f"Pad {i + 1}: {Path(saved).stem[:16]}", 2)

    def load_kit(self, name):
        kit = pads.load_kit(pads.kits_folder() / f"{name}.json")
        if kit:
            self.app.set_kit(kit)
            self.app.say(f"Kit {kit['name']}", 1.5)
        self.kits_open = False

    def save_kit(self):
        path = pads.save_kit(self.app.kit())
        self.app.say(f"Saved {path.stem}", 1.5)

    def new_kit(self):
        names = {n.upper() for n in pads.list_kits()}
        n = 1
        while f"KIT-{n}" in names:
            n += 1
        self.app.set_kit(pads.empty_kit(f"KIT {n}"))
        self.kits_open = False

