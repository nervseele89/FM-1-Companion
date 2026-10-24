"""The eleven meters drawn for the desk box's touchscreen: full width, in colour, in SLOOP's style and the theme's
colours (sloopstyle.Theme). The sound comes from the same reader as the pocket box's meters (meters.LevelReader,
the mix the box plays) and the sums are meters.py's own; only the pictures are new, and their smoothing (peak
holds, needles, the waterfall's history) lives here, apart from the pocket box's.

Levels colour like SLOOP's meters: the green track colour, yellow from -12 dBFS, orange from -3.
"""

import math

from PIL import Image, ImageDraw

import meters as m
import sloopstyle as st

BANDS = 32                                # the spectrum's bands (the pocket box has 16)
FALL = 26.0                               # dB a second a level bar falls back (rises at once): steady at any
                                          # frame rate, as a real peak meter's


class DeskMeters:
    def __init__(self):
        self.time = None
        self.hold = [[m.FLOOR, 0.0], [m.FLOOR, 0.0]]
        self.vu, self.clip = [0.0, 0.0], [0.0, 0.0]
        self.bands, self.caps = [m.FLOOR] * BANDS, [m.FLOOR] * BANDS
        self.reels, self.tune, self.base = [0.0, 0.0], None, 48
        self.wf, self.corr = None, 0.0
        self.hist, self.hacc, self.hnext = [], m.FLOOR, 0.0
        self.edges = None
        self.shown = [m.FLOOR, m.FLOOR]           # the level bars as drawn (Bars, Tape): their fall

    # ---- colours

    def ballistic(self, ch, peak, dt):
        """channel ch's level bar: up at once to a new peak, down at FALL dB a second"""
        self.shown[ch] = max(max(m.FLOOR, m.db(peak)), self.shown[ch] - FALL * dt)
        return self.shown[ch]

    @staticmethod
    def level_colour(t, level):
        return t.track[3] if level > -3 else t.track[2] if level > -12 else t.track[1]

    # ---- the frame

    def draw(self, app, img, box, t, now, style=None):
        """the meter app.state["meter"] picks (or style: the RECORD tab's Tape), in box (x0, y0, x1, y1) of img"""
        style = style or m.METERS[app.meter_index()]
        d = ImageDraw.Draw(img)
        if style == "MIDI":
            return self.draw_midi(app, img, d, box, t, now)
        if style == "Beat":
            return self.draw_beat(app, img, d, box, t, now)
        problem = ("FM-1 NOT FOUND" if not app.source else None if m.numpy() else
                   "NEEDS PYTHON3-NUMPY" if m._np_state == "missing" else "LOADING METERS...")
        if problem:
            self.centre(img, box, problem, t.c[3], 1)
            return
        pending, recent = app.levels.take()
        peak, rms = m.levels(pending)
        dt = min(0.5, now - self.time) if self.time else 0.1
        self.time = now
        if max(peak) > 100:
            app.last_activity = now
        getattr(self, "draw_" + style.lower())(app, img, d, box, t, now, dt, peak, rms, recent)

    @staticmethod
    def centre(img, box, text, colour, size, dy=0):
        w, h = st.text_size(text, size)
        st.text(img, ((box[0] + box[2] - w) // 2, (box[1] + box[3] - h) // 2 + dy), text, colour, size)

    # ---- Bars: two LED bars, a peak hold each, the dB scale under them

    def draw_bars(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        left, right = x0 + 36, x1 - 12

        def x_of(level):
            return left + int((right - left) * (max(-48.0, min(0.0, level)) + 48) / 48)
        clip = False
        for ch, y in ((0, y0 + 28), (1, y0 + 100)):
            level = self.ballistic(ch, peak[ch], dt)
            hold = self.hold[ch]
            if level >= hold[0] or now - hold[1] > 1.5:
                hold[:] = [level, now] if level >= hold[0] else [max(level, hold[0] - 30 * dt), hold[1]]
            st.text(img, (x0 + 10, y + 12), "LR"[ch], t.c[3], 2)
            seg, gap = 9, 3
            for x in range(left, right - seg + 1, seg + gap):
                at = -48 + 48 * (x + seg / 2 - left) / (right - left)
                lit = x + seg <= x_of(level)
                d.rectangle((x, y, x + seg - 1, y + 56), fill=self.level_colour(t, at) if lit else t.c[0])
            if hold[0] > -48:
                hx = x_of(hold[0])
                d.rectangle((hx - 2, y - 4, hx + 1, y + 60), fill=t.hi)
            clip |= hold[0] > -0.2
        for value in (-48, -36, -24, -12, -6, -3, 0):
            x = x_of(value)
            d.line((x, y0 + 168, x, y0 + 176), fill=t.c[2], width=2)
            label = str(value)
            w = st.text_size(label)[0]
            st.text(img, (min(x1 - w, max(x0, x - w // 2)), y0 + 182), label, t.c[2])
        if clip:
            st.text(img, (x1 - 60, y0 + 4), "CLIP", t.track[3])

    # ---- VU: two dials, needles with VU ballistics

    def draw_vu(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        k = 1 - math.exp(-dt / 0.3)
        width = (x1 - x0) // 2
        for ch in (0, 1):
            r = int((width // 2 - 34) / math.sin(0.5))              # the scale's ends just inside the dial
            cx, py = x0 + width * ch + width // 2, y0 + 70 + r       # its pivot below the dial, as on a real one
            d.rectangle((x0 + width * ch + 8, y0 + 6, x0 + width * (ch + 1) - 8, y1 - 6), outline=t.c[1], width=2)
            ang = lambda v: m.vu_angle(10 ** (v / 20))           # noqa: E731
            arc = [(cx + r * math.sin(a), py - r * math.cos(a)) for a in [m.vu_angle(0.1) + i * 0.02
                                                                          for i in range(51)]]
            red = [p for p, a in zip(arc, [m.vu_angle(0.1) + i * 0.02 for i in range(51)]) if a >= ang(0)]
            d.line(arc, fill=t.c[3], width=2)
            if len(red) > 1:
                d.line(red, fill=t.track[3], width=4)
            for v in (-20, -10, -7, -5, -3, -1, 0, 1, 3):
                a = ang(v)
                inner = r - (16 if v in (-20, -10, 0, 3) else 9)
                d.line((cx + inner * math.sin(a), py - inner * math.cos(a), cx + r * math.sin(a),
                        py - r * math.cos(a)), fill=t.track[3] if v > 0 else t.c[3], width=2)
                if v in (-20, -10, -5, 0, 3):
                    label = str(abs(v)) if v else "0"
                    lx, ly = cx + (r + 14) * math.sin(a), py - (r + 14) * math.cos(a)
                    w = st.text_size(label)[0]
                    st.text(img, (lx - w / 2, ly - 8), label, t.track[3] if v > 0 else t.c[2])
            target = rms[ch] / (m.FULL * 10 ** (m.VU_REF / 20))
            self.vu[ch] += (target - self.vu[ch]) * k
            a = m.vu_angle(self.vu[ch])
            reach = (py - (y1 - 8)) / max(0.3, math.cos(a))            # the needle shows from the dial's bottom
            d.line((cx + reach * math.sin(a), py - reach * math.cos(a), cx + (r + 4) * math.sin(a),
                    py - (r + 4) * math.cos(a)), fill=t.hi, width=3)
            st.text(img, (cx - 8, y1 - 34), "VU", t.c[2])
            st.text(img, (x0 + width * ch + 18, y1 - 30), "LR"[ch], t.c[3])
            if peak[ch] > m.FULL * 0.89:
                self.clip[ch] = now
            lamp = (x0 + width * (ch + 1) - 34, y0 + 16, x0 + width * (ch + 1) - 20, y0 + 30)
            d.ellipse(lamp, fill=t.track[3] if now - self.clip[ch] < 0.6 else t.c[0])

    # ---- Spectrum: 32 bands of LED blocks, falling caps

    def band_edges(self):
        if self.edges is None:
            hz, (lo, hi) = m.RATE / m.SPECTRUM_N, m.BAND_EDGES
            out, last = [], 0
            for i in range(BANDS + 1):
                k = max(last + 1, int(round(lo * (hi / lo) ** (i / BANDS) / hz)))
                out.append(k)
                last = k
            self.edges = out
        return self.edges

    def draw_spectrum(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        if len(recent) >= m.SPECTRUM_N:
            fresh = m.spectrum_bins(recent, self.band_edges()).tolist()
            self.bands = [max(f, b - 45 * dt) for f, b in zip(fresh, self.bands)]
        width = (x1 - x0 - 16) / BANDS
        rows, top, bottom = 20, y0 + 10, y1 - 26
        step = (bottom - top) / rows
        for i, level in enumerate(self.bands):
            self.caps[i] = max(level, self.caps[i] - 20 * dt)
            frac = (max(m.FLOOR, min(0.0, level)) - m.FLOOR) / -m.FLOOR
            x = x0 + 8 + i * width
            for r in range(rows):
                y = bottom - (r + 1) * step
                on = r < round(frac * rows)
                colour = t.c[1 + min(3, r * 4 // rows)] if on else t.grey[0]
                d.rectangle((x + 1, y + 1, x + width - 2, y + step - 2), fill=colour)
            cap = (max(m.FLOOR, min(0.0, self.caps[i])) - m.FLOOR) / -m.FLOOR
            if cap > frac + 0.04:
                y = bottom - cap * (bottom - top)
                d.rectangle((x + 1, y, x + width - 2, y + 2), fill=t.hi)
        for hz, label in ((100, "100"), (1000, "1K"), (5000, "5K")):
            lo, hi = m.BAND_EDGES
            x = x0 + 8 + (x1 - x0 - 16) * math.log(hz / lo) / math.log(hi / lo)
            st.text(img, (x - st.text_size(label)[0] / 2, y1 - 20), label, t.c[2])

    # ---- Scope: the wave across the screen, starting on a rising zero

    def draw_scope(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        cy = (y0 + y1) // 2
        for y in (y0 + 10, cy, y1 - 10):
            for x in range(x0, x1, 8):
                d.point((x, y), fill=t.c[1])
        for x in range(x0, x1, 60):
            for y in range(y0 + 10, y1 - 10, 8):
                d.point((x, y), fill=t.c[0])
        if len(recent) < 4:
            return
        np = m.numpy()
        mono = recent.astype(np.float32).mean(axis=1)
        rising = np.nonzero((mono[:-1] < 0) & (mono[1:] >= 0))[0]
        start = int(rising[0]) + 1 if len(rising) and rising[0] < len(mono) // 2 else 0
        n = x1 - x0
        part = mono[start:start + n]
        ys = (cy - part * (y1 - y0 - 24) / 2 / m.FULL).tolist()
        if len(ys) > 1:
            d.line([(x0 + i, y) for i, y in enumerate(ys)], fill=t.c[4], width=2)

    # ---- Tape: two reels winding, the head's level bars between them

    def draw_tape(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        cy, R, hub = y0 + 96, 82, 20
        centres = (x0 + 120, x1 - 120)
        moving = app.recorder.recording() or max(peak) > 100
        done = app.tape_done
        packs = (math.sqrt(hub ** 2 + (1 - done) * ((R - 6) ** 2 - hub ** 2)),
                 math.sqrt(hub ** 2 + done * ((R - 6) ** 2 - hub ** 2)))
        for i, (cx, pack) in enumerate(zip(centres, packs)):
            if moving:
                self.reels[i] += 160 * dt / max(pack, hub + 4)
            d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=t.c[2], width=3)                  # flange
            d.ellipse((cx - pack, cy - pack, cx + pack, cy + pack), fill=t.dim[3])               # tape pack
            for k in range(3):                                                                   # flange windows
                b = self.reels[i] + k * 2 * math.pi / 3 + math.pi / 3
                wx, wy = cx + (R - 22) * math.cos(b), cy + (R - 22) * math.sin(b)
                d.ellipse((wx - 12, wy - 12, wx + 12, wy + 12), outline=t.c[2], width=2)
            d.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=t.grey[1], outline=t.c[3], width=2)
            for k in range(3):
                b = self.reels[i] + k * 2 * math.pi / 3
                d.line((cx + 4 * math.cos(b), cy + 4 * math.sin(b), cx + hub * math.cos(b),
                        cy + hub * math.sin(b)), fill=t.hi, width=3)
            gx = x0 + 14 if i == 0 else x1 - 14
            side = -1 if i == 0 else 1
            d.line((cx + side * pack * 0.7, cy + pack * 0.7, gx, y1 - 22), fill=t.dim[3], width=3)
            d.ellipse((gx - 6, y1 - 28, gx + 6, y1 - 16), outline=t.c[3], width=2)
        d.line((x0 + 14, y1 - 16, x1 - 14, y1 - 16), fill=t.dim[3], width=3)                     # tape past the head
        mid = (x0 + x1) // 2
        d.rectangle((mid - 22, y1 - 34, mid + 22, y1 - 16), fill=t.grey[2])                       # the head
        for ch, x in ((0, mid - 18), (1, mid + 4)):
            level = max(-48.0, min(0.0, self.ballistic(ch, peak[ch], dt)))
            h = int(110 * (level + 48) / 48)
            d.rectangle((x, y0 + 40, x + 14, y0 + 150), fill=t.c[0])
            if h:
                d.rectangle((x, y0 + 150 - h, x + 14, y0 + 150), fill=self.level_colour(t, level))
        if app.recorder.recording():
            d.ellipse((mid - 8, y0 + 12, mid + 8, y0 + 28), fill=(255, 112, 92))

    # ---- Tuner: the note, big, a cents scale and pointer

    def draw_tuner(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        np = m.numpy()
        if len(recent) >= 256 and max(peak) > m.FULL * 0.01:
            x = recent[-1024:].astype(np.float32).mean(axis=1)
            freq, _ = m.pitch(x, m.RATE, hi=2500.0)
            if freq:
                name, octave, cents, _ = m.note_of(freq)
                if self.tune and self.tune[:2] == [name, octave]:
                    cents = self.tune[2] + (cents - self.tune[2]) * min(1.0, dt * 6)
                self.tune = [name, octave, cents, freq, now]
        cx = (x0 + x1) // 2
        scale_y = y1 - 46
        d.line((x0 + 40, scale_y, x1 - 40, scale_y), fill=t.c[2], width=2)
        for c in range(-50, 51, 10):
            x = cx + c * (x1 - x0 - 80) / 100
            h = 14 if c in (-50, 0, 50) else 7
            d.line((x, scale_y - h, x, scale_y + h), fill=t.track[1] if c == 0 else t.c[2], width=2)
        st.text(img, (x0 + 14, scale_y - 8), "b", t.c[3])
        st.text(img, (x1 - 24, scale_y - 8), "#", t.c[3])
        if not self.tune or now - self.tune[4] > 1.5:
            self.centre(img, (x0, y0, x1, scale_y - 20), "- -", t.c[2], 4)
            return
        name, octave, cents, freq = self.tune[:4]
        good = abs(cents) < 5
        colour = t.track[1] if good else t.track[2] if abs(cents) < 20 else t.track[3]
        text = f"{name}{octave}"
        w, h = st.text_size(text, 5)
        tx, ty = cx - w // 2, y0 + 16
        if good:
            d.rectangle((tx - 12, ty - 6, tx + w + 12, ty + h + 6), outline=colour, width=3)
        st.text(img, (tx, ty), text, colour, 5)
        st.text(img, (x1 - 120, y0 + 20), f"{freq:.1f} HZ", t.c[3])
        st.text(img, (x0 + 20, y0 + 20), f"{cents:+.0f} C", colour)
        px = cx + max(-50, min(50, cents)) * (x1 - x0 - 80) / 100
        d.polygon(((px - 10, scale_y - 28), (px + 10, scale_y - 28), (px, scale_y - 6)), fill=colour)

    # ---- MIDI: three octaves of keys, the notes lit, the chord, the 16 channels

    def draw_midi(self, app, img, d, box, t, now):
        x0, y0, x1, y1 = box
        midi = app.midi
        notes = sorted(getattr(midi, "notes", ()) or ())
        chans = getattr(midi, "chans", 0) if midi else 0
        if notes or chans:
            app.last_activity = now
        if notes and not (self.base <= notes[0] and notes[-1] < self.base + 36):
            self.base = max(0, min(84, 12 * (notes[0] // 12)))
        chord = m.chord_name(notes)
        if notes:
            st.text(img, (x0 + 12, y0 + 6), chord or "", t.hi, 2)
            st.text(img, (x0 + 24 + st.text_size(chord or "", 2)[0], y0 + 22),
                    " ".join(m.note_name(n) for n in notes[:6]), t.c[3])
        else:
            st.text(img, (x0 + 12, y0 + 14), "NO MIDI" if not midi else "PLAY SOMETHING", t.c[2])
        held = set(notes)
        top, kh, left = y0 + 48, 122, x0 + 9
        kw = (x1 - x0 - 18) // 21
        for i in range(21):
            note = self.base + 12 * (i // 7) + m.WHITE[i % 7]
            x = left + i * kw
            d.rectangle((x, top, x + kw - 2, top + kh), fill=t.track[0] if note in held else t.c[4])
        for i in range(21):
            if m.WHITE[i % 7] in (4, 11) or i == 20:
                continue
            note = self.base + 12 * (i // 7) + m.WHITE[i % 7] + 1
            x = left + i * kw + kw * 2 // 3
            d.rectangle((x, top, x + kw * 2 // 3, top + kh * 3 // 5), fill=t.track[0] if note in held else t.bg)
        st.text(img, (left, top + kh + 4), m.note_name(self.base), t.c[2])
        for ch in range(16):
            x = x1 - 16 * 17 - 8 + ch * 17
            on = chans & (1 << ch)
            d.rectangle((x, y1 - 26, x + 13, y1 - 6), fill=t.track[ch // 4] if on else t.grey[1])
        st.text(img, (x1 - 16 * 17 - 60, y1 - 24), "CH", t.c[2])

    # ---- Waterfall: a scrolling spectrogram in the theme's shades

    def draw_waterfall(self, app, img, d, box, t, now, dt, peak, rms, recent):
        np = m.numpy()
        x0, y0, x1, y1 = box
        cols, rows = (x1 - x0) // 4, (y1 - y0) // 3
        if self.wf is None or self.wf.shape != (rows, cols):
            self.wf = np.zeros((rows, cols), dtype=np.float32)
        if len(recent) >= m.SPECTRUM_N:
            hz, (lo, hi) = m.RATE / m.SPECTRUM_N, m.BAND_EDGES
            edges, last = [], 0
            for i in range(cols + 1):
                k = max(last + 1, int(round(lo * (hi / lo) ** (i / cols) / hz)))
                edges.append(min(k, m.SPECTRUM_N // 2))
                last = k
            edges = sorted(set(edges))
            level = np.clip((m.spectrum_bins(recent, edges) - m.FLOOR) / -m.FLOOR, 0, 1)
            row = np.interp(np.linspace(0, len(level) - 1, cols), np.arange(len(level)), level)
            self.wf = np.roll(self.wf, 1, axis=0)
            self.wf[0] = row
        ramp = np.array([t.bg, t.c[0], t.c[1], t.c[2], t.c[3], t.c[4], t.hi], dtype=np.uint8)
        idx = np.clip((self.wf * 6.99).astype(np.int32), 0, 6)
        pic = Image.fromarray(ramp[idx], "RGB").resize((cols * 4, rows * 3), Image.NEAREST)
        img.paste(pic, (x0, y0))

    # ---- Stereo: a goniometer and the correlation

    def draw_stereo(self, app, img, d, box, t, now, dt, peak, rms, recent):
        np = m.numpy()
        x0, y0, x1, y1 = box
        cx, cy, r = (x0 + x1) // 2, y0 + 92, 86
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=t.c[1], width=2)
        for k in range(0, int(r * 0.707), 5):         # the L and R axes: the diagonals up from the centre
            d.point((cx - k, cy - k), fill=t.c[1])
            d.point((cx + k, cy - k), fill=t.c[1])
        for k in range(-r, r + 1, 5):                  # mono: straight up and down
            d.point((cx, cy + k), fill=t.c[0])
        st.text(img, (cx - r - 6, cy - r), "L", t.c[3])
        st.text(img, (cx + r - 2, cy - r), "R", t.c[3])
        if len(recent) >= 8:
            x = recent[-1024::2].astype(np.float32)
            mid, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2
            gain = (r - 4) / max(float(np.abs(mid).max()), float(np.abs(side).max()), 300.0)
            pts = list(zip((cx + side * gain).tolist(), (cy - mid * gain).tolist()))
            d.point(pts, fill=t.c[4])
            self.corr += (m.correlation(recent) - self.corr) * min(1.0, dt * 4)
        by = y1 - 24
        d.rectangle((x0 + 60, by, x1 - 60, by + 10), fill=t.c[0])
        mid_x = (x0 + x1) // 2
        px = mid_x + int((x1 - x0 - 120) / 2 * max(-1.0, min(1.0, self.corr)))
        colour = t.track[1] if self.corr > 0.2 else t.track[2] if self.corr > -0.2 else t.track[3]
        d.rectangle((min(mid_x, px), by, max(mid_x, px), by + 10), fill=colour)
        st.text(img, (x0 + 24, by - 4), "-1", t.c[2])
        st.text(img, (x1 - 52, by - 4), "+1", t.c[2])

    # ---- History: the peak level over the last minute

    def draw_history(self, app, img, d, box, t, now, dt, peak, rms, recent):
        x0, y0, x1, y1 = box
        cols = (x1 - x0) // 3
        self.hacc = max(self.hacc, m.db(max(peak)))
        if now >= self.hnext:
            self.hist = (self.hist + [self.hacc])[-cols:]
            self.hacc, self.hnext = m.FLOOR, now + 60.0 / cols

        def y_of(level):
            return y1 - 6 - int((y1 - y0 - 30) * (max(-48.0, min(0.0, level)) + 48) / 48)
        start = x1 - len(self.hist) * 3
        for i, level in enumerate(self.hist):
            if level > -48:
                x = start + i * 3
                d.rectangle((x, y_of(level), x + 2, y1 - 6), fill=self.level_colour(t, level))
        for level in (-6, -12, -24, -36):
            y = y_of(level)
            for x in range(x0, x1 - 40, 6):
                d.point((x, y), fill=t.c[2])
            st.text(img, (x1 - 36, y - 8), str(level), t.c[2])

    # ---- Beat: the tempo, big, and four beats lighting in time

    def draw_beat(self, app, img, d, box, t, now):
        x0, y0, x1, y1 = box
        clock = getattr(app.midi, "clock", None) if app.midi else None
        if not clock:
            self.centre(img, box, "NO CLOCK COMING IN", t.c[3], 2, -30)
            self.centre(img, box, "TAP TEMPO ON HOME, OR PLAYING > CLOCK: BOX", t.c[2], 1, 20)
            return
        app.last_activity = now
        position = m.beat_position(clock, now)
        beat, flash = int(position) % 4, position % 1 < 0.15
        text = str(clock["bpm"])
        w, h = st.text_size(text, 5)
        tx, ty = (x0 + x1 - w) // 2, y0 + 12
        if flash:
            d.rectangle((tx - 14, ty - 6, tx + w + 14, ty + h + 6), fill=t.hi)
        st.text(img, (tx, ty), text, t.bg if flash else t.hi, 5)
        st.text(img, (tx + w + 22, ty + h - 20), "BPM", t.c[2])
        source = {"box": "BOX", "FM-1": "FM-1", "BT": "BT", "USB": "USB"}.get(clock.get("source"), "")
        st.text(img, (x0 + 16, ty + h - 20), ("PLAY  " if clock.get("playing") else "") + source, t.c[3])
        bw = (x1 - x0 - 50) // 4
        for i in range(4):
            x = x0 + 10 + i * (bw + 10)
            top = y1 - 76 if i == 0 else y1 - 62
            if i == beat:
                d.rectangle((x, top, x + bw, y1 - 8), fill=t.track[i])
            else:
                d.rectangle((x, top, x + bw, y1 - 8), outline=t.dim[i], width=3)
