"""The Studio sampler's punch-in FX: SLOOP's sixteen (punch.c, GPL-3.0), pocket-operator style. Hold one and
the pads' sound goes through it while it's held; let go and it comes back. Beat-synced to the tempo, the loops
on the grid of the running sequencer. One at a time; every change crossfades over 64 frames.

LOOP 4 / 8 / 16 / 32 (a beat, an 8th, a 16th, a 32nd), STUTTER (a beat / 6), REVERSE (the last beat
backwards), STOP (a tape stop over a beat), HALF (half speed), LOW and HIGH (filters closing / opening), PHONE
(a band, driven), CRUSH (bits), ALIAS (sample and hold), GATE (16ths), ECHO (a dotted 8th, fed back), WOBBLE
(a warbling tape).

Block by block with numpy (render() hands over each block): a stereo ring of what the pads played feeds the
loops, reverse, stop, half speed, wobble and echo; the filters are one-pole sections run a chunk at a time in
closed form (no scipy on the Pi, no Python loop a sample).
"""

import math

NAMES = ("LOOP 4", "LOOP 8", "LOOP 16", "LOOP 32", "STUTTER", "REVERSE", "STOP", "HALF",
         "LOW", "HIGH", "PHONE", "CRUSH", "ALIAS", "GATE", "ECHO", "WOBBLE")
LOOP4, LOOP8, LOOP16, LOOP32, STUT, REV, STOP, HALF, LOW, HIGH, PHONE, CRUSH, ALIAS, GATE, ECHO, WOBBLE = range(16)
DIV = (1, 2, 4, 8, 6, 1, 1, 2)          # LOOP 4 .. HALF: their span, a beat / DIV (seq.c punch_start)
RING = 1 << 16                          # frames of the ring (1.4 s at 48 kHz)
FADE = 64                               # frames of a crossfade
CHUNK = 64                              # one-pole sections: frames solved at once


def np():
    import numpy
    return numpy


def one_pole(x, a, z):
    """y[n] = a y[n-1] + (1 - a) x[n] along axis 0 of x (frames, channels), from state z (channels,):
    -> (y, last y). Closed form a chunk at a time: y = a^k (z + sum (1 - a) x[j] a^-j)"""
    num = np()
    a = min(max(a, 0.05), 0.99999)
    y = num.empty_like(x)
    for s in range(0, len(x), CHUNK):
        c = x[s:s + CHUNK]
        k = num.arange(1, len(c) + 1, dtype=num.float64)[:, None]
        up = a ** -k                          # (a >= 0.05, 64 frames: under 1e84, fine in float64)
        y[s:s + CHUNK] = (a ** k) * (z + num.cumsum((1 - a) * c * up, axis=0))
        z = y[s + len(c) - 1]
    return y, z


def coef(hz, rate):
    return math.exp(-2 * math.pi * hz / rate)


class PunchFX:
    def __init__(self, rate=48000):
        num = np()
        self.rate = rate
        self.ring = num.zeros((RING, 2), dtype=num.float32)
        self.w = 0                            # frames written to the ring, ever
        self.want = None                      # the FX held (or latched), or None
        self.cur = None                       # the FX playing (fading out while want differs)
        self.g = 0.0                          # its wet gain, 0..1
        self.z = {}                           # filter states
        self.state = {}

    def press(self, fx):
        self.want = fx if fx is None or 0 <= fx < len(NAMES) else None

    def beat(self, grid):
        per = grid[0] if grid else 60.0 / (120 * 24)
        return max(64, int(per * 24 * self.rate))

    def phase(self, grid, t0, beat):
        """frames into the beat at t0 on the sequencer's grid while it plays, else None"""
        if not (grid and grid[2] and grid[1] is not None and t0 is not None):
            return None
        return int(round((t0 - grid[1]) * self.rate)) % beat

    def start(self, fx, grid, t0):
        b = self.beat(grid)
        ln = b // DIV[fx] if fx <= HALF else b
        ln = max(64, min(ln, RING - 4096))
        s = {"len": ln, "start": self.w, "k": 0, "pre": 0, "t": 0, "spd": 1.0, "rp": float(self.w),
             "lfo": 0.0, "cut": 1.0, "cap": None}
        ph = self.phase(grid, t0, b)
        if fx <= STUT and ph is not None:     # the loops: on the grid
            back = ph % ln
            if ln - back <= 1536:             # just before a division: from that one
                s["pre"] = ln - back
            else:                             # else from the last division's start
                s["start"], s["k"], s["t"] = self.w - back, back, back
        if fx == GATE:
            s["gp"] = (ph if ph is not None else 0) % max(1, b // 4)
        self.cur, self.state, self.z = fx, s, {}

    def at(self, idx):
        return self.ring[np().asarray(idx, dtype=np().int64) & (RING - 1)]

    def lerp(self, pos):
        num = np()
        i = num.floor(pos).astype(num.int64)
        f = (pos - i).astype(num.float32)[:, None]
        return self.at(i) * (1 - f) + self.at(i + 1) * f

    def process(self, x, grid=None, t0=None):
        """a block (frames, 2) float32 -> the same, through the FX held"""
        num = np()
        n = len(x)
        idx = self.w + num.arange(n)
        if self.cur is None and self.want is not None:
            self.start(self.want, grid, t0)
        fx, s = self.cur, self.state
        if fx is None:
            self.ring[idx & (RING - 1)] = x
            self.w += n
            return x
        if fx != ECHO:                        # (ECHO writes its own: the ring is its delay line)
            self.ring[idx & (RING - 1)] = x     # (loops read a copy once captured: the ring goes on)
        wet = self.wet(fx, s, x, idx, grid, n)
        target = 1.0 if self.want == fx else 0.0
        sign = 1.0 if target > self.g else -1.0
        ramp = num.clip(self.g + sign / FADE * num.arange(1, n + 1), min(self.g, target), max(self.g, target))
        self.g = float(ramp[-1])
        g = ramp.astype(num.float32)[:, None]
        out = x + (wet - x) * g
        self.w += n
        s["t"] += n
        if self.g <= 0.0 and self.want != fx:  # faded out: the next one (if any) starts on the next block
            self.cur = None
        return out

    def loop_read(self, s, n, speed=1):
        """the loops and HALF: the span [start, start + len) over and over (HALF: each frame twice); the first
        pass is what's coming in (k from the division's start), then a copy of it"""
        num = np()
        ln = s["len"]
        out = num.empty((n, 2), dtype=num.float32)
        for i in range(0, n, CHUNK):          # (a few chunks: the seam fade and the copy taken once)
            m = min(CHUNK, n - i)
            sub = s.get("sub", 0)
            k = (s["k"] + (num.arange(m) + sub) // speed) % ln
            if s["cap"] is None and s["t"] + i >= ln:
                s["cap"] = self.at(s["start"] + num.arange(ln)).copy()
            src = s["cap"][k] if s["cap"] is not None else self.at(s["start"] + k)
            seam = num.minimum(k, ln - k).astype(num.float32)
            out[i:i + m] = src * num.minimum(1.0, seam / FADE)[:, None]
            s["k"] = int((s["k"] + (m + sub) // speed) % ln)
            s["sub"] = (m + sub) % speed
        return out

    def wet(self, fx, s, x, idx, grid, n):
        num = np()
        if fx <= HALF and fx not in (REV, STOP) and s["pre"]:     # waiting for the division
            go = min(n, s["pre"])
            s["pre"] -= go
            out = x.copy()
            if not s["pre"]:
                s["start"], s["k"], s["t"] = self.w + go, 0, -go
                out[go:] = self.loop_read(s, n - go)
            return out
        if fx in (LOOP4, LOOP8, LOOP16, LOOP32, STUT):
            return self.loop_read(s, n)
        if fx == HALF:
            return self.loop_read(s, n, speed=2)
        if fx == REV:                         # the last beat, backwards, over and over
            if s["cap"] is None:
                s["cap"] = self.at(s["start"] - 1 - num.arange(s["len"])).copy()
            k = (s["k"] + num.arange(n)) % s["len"]
            s["k"] = int((s["k"] + n) % s["len"])
            seam = num.minimum(k, s["len"] - k).astype(num.float32)
            return s["cap"][k] * num.minimum(1.0, seam / FADE)[:, None]
        if fx == STOP:                        # the tape slows to a stop over a beat
            dec = 1.0 / s["len"]
            spd = num.maximum(0.0, s["spd"] - dec * num.arange(n))
            pos = s["rp"] + num.cumsum(spd) - spd
            s["rp"], s["spd"] = float(pos[-1] + spd[-1]), float(max(0.0, spd[-1] - dec))
            return self.lerp(pos) * spd.astype(num.float32)[:, None]
        if fx == WOBBLE:                      # read 700 +- 180 frames back, swayed at 4 Hz
            lfo = s["lfo"] + 2 * math.pi * 4 / self.rate * num.arange(n)
            s["lfo"] = float((lfo[-1] + 2 * math.pi * 4 / self.rate) % (2 * math.pi))
            return self.lerp(idx - (700 + 180 * num.sin(lfo)))
        if fx == ECHO:                        # a dotted 8th back, fed back (the ring is the delay line)
            d = min(RING - 64, max(n + 1, self.beat(grid) * 3 // 4))
            e = self.at(idx - d) * 0.55
            self.ring[idx & (RING - 1)] = x + e
            return x + e
        if fx in (LOW, HIGH):                 # closing over ~1 s / opening
            s["cut"] = max(0.0, s["cut"] - n / self.rate)
            hz = (300 + 15000 * s["cut"] ** 2) if fx == LOW else (60 + 4000 * (1 - s["cut"]) ** 2)
            y = x.astype(num.float64)
            for j in range(2):                # two sections: 12 dB an octave
                y, self.z[j] = one_pole(y, coef(hz, self.rate), self.z.get(j, num.zeros(2)))
            return (y if fx == LOW else x - y).astype(num.float32)
        if fx == PHONE:                       # mono, 500 Hz .. 2.5 kHz, driven
            m = x.mean(axis=1, keepdims=True).astype(num.float64)
            lp, self.z[0] = one_pole(m, coef(2500, self.rate), self.z.get(0, num.zeros(1)))
            lp, self.z[1] = one_pole(lp, coef(2500, self.rate), self.z.get(1, num.zeros(1)))
            lo, self.z[2] = one_pole(lp, coef(500, self.rate), self.z.get(2, num.zeros(1)))
            b = num.tanh((lp - lo) * 4) * 0.5
            return num.repeat(b, 2, axis=1).astype(num.float32)
        if fx == CRUSH:                       # 4 bits, towards zero
            return (num.trunc(x * 16) / 16).astype(num.float32)
        if fx == ALIAS:                       # each 8th frame held
            src = num.arange(n) - (s["t"] + num.arange(n)) % 8      # the frame each one holds
            out = x[num.maximum(src, 0)]
            out[src < 0] = s.get("held", x[0])                     # (held from the block before)
            s["held"] = out[-1].copy()
            return out
        if fx == GATE:                        # 16ths: open half of each, 64-frame ramps
            step = max(2, self.beat(grid) // 4)
            p = (s["gp"] + num.arange(n)) % step
            s["gp"] = int((s["gp"] + n) % step)
            half = step // 2
            gg = num.where(p < half, num.minimum(1.0, p / FADE), num.maximum(0.0, 1 - (p - half) / FADE))
            return x * gg.astype(num.float32)[:, None]
        return x
