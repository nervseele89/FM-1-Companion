"""The Studio's MPC-style sampler: 16 pads in a kit, played from MIDI (notes 36-51, as an MPC: pad 1 is C1) and
from the PADS tab. Or laid out as SLOOP's drum track (map "sloop"): pad 1 is SLOOP's drum key 1 (KICK), pad 3 its
SNARE ... pad 16 its COWBELL, played by the GM notes SLOOP sends on channel 10 (GLO > MIDI 2 > OUT = SEQ sends
what its sequencer plays), so a SLOOP drum pattern plays the kit's samples.

Each pad: a sample, its level, pan and tuning, one-shot (plays to its end), gate (stops when the note is let
go) or loop (loops while held), an attack and a release, a choke group (a pad cuts the others in its group: an
open hi-hat stopped by the closed one) and reverse. Up to VOICES at once; past that the oldest stops.

The engine runs in a process of its own (PadProcess) that renders blocks of BLOCK frames with numpy and
streams them into PipeWire: into a virtual speaker whose other side is a source named "fm1-sampler", so the box
treats the sampler like any other sound source: a slider in the mixer, a file in Record parts, the meters. The
MIDI process sends it notes (the routes' SMP), the app sends it taps and kits; it sends back which pads sound.
Kits are JSON files in ~/fm1-library/kits/.

Each kit has a step sequencer for its pads, SLOOP's drum sequencer (seq.c, GPL-3.0): up to 64 steps, a division
(1/4 .. 2 bars), swing (0 straight, 100: 75/25, odd steps late, not on the triplets or whole beats), and on each
step any of the pads, each with its level (NORM, GHOST, SOFT, HARD) and ratchet (x1-x4), the step's nudge in
1/64 of a step. It plays on the box's beat grid (the MIDI process sends it: the box's own clock or the one it
follows), each hit at its own frame in the block that is heard at that time. Live REC: pads played while it
runs go on the nearest step (QUANT off: the step's nudge keeps the timing).

Note repeat (an MPC's): while it's on, a pad held plays again at the repeat's division (1/4 .. 16T) on the same
grid (stopped: at the box's tempo, from the press), recorded with live REC as any hit.

Punch-in FX (padfx.py, SLOOP's sixteen): the pads' sound through one while it's held.
"""

import json
import math
import os
import re
import subprocess
import time
import wave
from pathlib import Path

PADS, FIRST_NOTE = 16, 36
MAPS = ("mpc", "sloop")
# SLOOP's 16 drum sounds, keys 1-16, and the GM notes its drum track sends for them (GUIDE.md: DR MAP GM)
SLOOP_DRUMS = ("KICK", "KICK 2", "SNARE", "CLAP", "HAT", "OPEN HAT", "PEDAL HAT", "RIM", "SNARE 2", "LOW TOM",
               "HI TOM", "CRASH", "RIDE", "SHAKER", "CONGA", "COWBELL")
SLOOP_NOTES = (36, 35, 38, 39, 42, 46, 44, 37, 40, 43, 48, 49, 51, 70, 63, 56)
SLOOP_CHANNEL = 9                           # channel 10: SLOOP's drum track (GLO > MIDI > CH4)
VOICES, BLOCK = 16, 512                     # voices at once (an MPC 2000: 32); frames a block (11 ms at 48 kHz:
                                            # numpy's cost a call, not a sample, is what a Pi 3 feels)
SOURCE, SINK = "fm1-sampler", "fm1-sampler-in"
MODES = ("one-shot", "gate", "loop")
DEFAULT_PAD = {"sample": None, "level": 100, "pan": 0, "tune": 0, "mode": "one-shot", "attack": 0,
               "release": 50, "choke": 0, "reverse": False}
LIMITS = {"level": (0, 200), "pan": (-50, 50), "tune": (-24, 24), "attack": (0, 2000), "release": (0, 5000),
          "choke": (0, 4)}
NSTEP = 64
DIVS = ("1/4", "1/8", "1/16", "1/32", "8T", "16T", "1/2", "1BAR", "2BAR")    # SLOOP's N_SDIV, in its order
DIV_DEN, DIV_BEATS = (1, 2, 4, 8, 3, 6), (2, 4, 8)   # steps a beat (the first six), beats a step (the rest)
LEVELS = ("NORM", "GHOST", "SOFT", "HARD")
LEVEL_VEL = (100, 42, 72, 127)              # SLOOP's lvl_vel (NORM: what the keys play)
GATE = 0.5                                  # a gate pad's hit lasts half its step (SLOOP's GATE 64 of 128)


def np():
    import numpy
    return numpy


def empty_seq():
    """steps: per step [[pad, level, ratchet], ...]; micro: each step's nudge, -32..31 sixty-fourths"""
    return {"len": 16, "div": 2, "swing": 0, "quant": True, "steps": [[] for _ in range(NSTEP)],
            "micro": [0] * NSTEP}


def clean_seq(seq):
    seq = seq if isinstance(seq, dict) else {}
    out = empty_seq()
    for key, lo, hi in (("len", 1, NSTEP), ("div", 0, len(DIVS) - 1), ("swing", 0, 100)):
        try:
            out[key] = int(max(lo, min(hi, int(seq.get(key, out[key])))))
        except (TypeError, ValueError):
            pass
    out["quant"] = bool(seq.get("quant", True))
    for i, step in enumerate(list(seq.get("steps") or [])[:NSTEP]):
        hits = {}
        for hit in step if isinstance(step, list) else []:
            try:
                pad, lvl, rat = (int(v) for v in hit)
            except (TypeError, ValueError):
                continue
            if 0 <= pad < PADS:
                hits[pad] = [pad, max(0, min(3, lvl)), max(1, min(4, rat))]
        out["steps"][i] = [hits[k] for k in sorted(hits)]
    for i, m in enumerate(list(seq.get("micro") or [])[:NSTEP]):
        try:
            out["micro"][i] = max(-32, min(31, int(m)))
        except (TypeError, ValueError):
            pass
    return out


def vel_level(vel):
    """a velocity -> the level it records as (SLOOP's vel_lvl)"""
    return 1 if vel < 56 else 2 if vel < 88 else 0 if vel < 116 else 3


def empty_kit(name="KIT 1"):
    return {"name": name, "map": "mpc", "pads": [dict(DEFAULT_PAD) for _ in range(PADS)], "seq": empty_seq()}


def clean_kit(kit):
    """a kit from a file or a page -> the same, complete and in range"""
    kit = kit if isinstance(kit, dict) else {}
    pads = list(kit.get("pads") or [])[:PADS]
    pads += [{}] * (PADS - len(pads))
    out = []
    for pad in pads:
        p = dict(DEFAULT_PAD, **{k: v for k, v in (pad or {}).items() if k in DEFAULT_PAD})
        for key, (lo, hi) in LIMITS.items():
            try:
                p[key] = int(max(lo, min(hi, int(p[key]))))
            except (TypeError, ValueError):
                p[key] = DEFAULT_PAD[key]
        p["mode"] = p["mode"] if p["mode"] in MODES else "one-shot"
        p["reverse"] = bool(p["reverse"])
        p["sample"] = str(p["sample"]) if p["sample"] else None
        out.append(p)
    return {"name": str(kit.get("name") or "KIT")[:16], "map": kit.get("map") if kit.get("map") in MAPS else "mpc",
            "pads": out, "seq": clean_seq(kit.get("seq"))}


def kits_folder():
    import library
    return library.folder("kits")


def kit_path(name):
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-")[:24] or "kit"
    return kits_folder() / f"{slug}.json"


def save_kit(kit):
    path = kit_path(kit["name"])
    path.write_text(json.dumps(clean_kit(kit), indent=1))
    return path


def load_kit(path):
    try:
        return clean_kit(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return None


def list_kits():
    return sorted(p.stem for p in kits_folder().glob("*.json"))


def pad_of(note, channel=None, mapping="mpc"):
    """a MIDI note -> its pad (0-15), or None. "mpc": notes 36-51 on any channel; "sloop": the GM note of a
    SLOOP drum key on its drum channel"""
    if mapping == "sloop":
        return SLOOP_NOTES.index(note) if channel == SLOOP_CHANNEL and note in SLOOP_NOTES else None
    return note - FIRST_NOTE if FIRST_NOTE <= note < FIRST_NOTE + PADS else None


def note_of(index, mapping="mpc"):
    """a pad -> the note that plays it"""
    return SLOOP_NOTES[index] if mapping == "sloop" else FIRST_NOTE + index


# ---------------------------------------------------------------- the sequencer

class Sequencer:
    """SLOOP's drum sequencer on a beat grid: (seconds a clock tick, when tick 0 was, playing). Times are when a
    sound is heard (time.monotonic()); events() hands out the hits due in a stretch of time, in order"""

    def __init__(self):
        self.grid = None                        # (seconds a tick, time of tick 0 or None, playing)
        self.next = None                        # the absolute step (since tick 0) to fire next
        self.pending = []                       # (time, "on" / "off", pad, velocity) not yet due
        self.skip = set()                       # (step, pad): recorded live, already heard: not again
        self.rec = False
        self.step = None                        # the step fired last (the playhead)
        self.repeat = None                      # note repeat: a division (DIVS index, 0-5), or None: off
        self.held = {}                          # pads held while repeating: pad -> [velocity, next hit's time]
        self.dirty = False                      # live REC changed the pattern since the app was told

    def playing(self):
        return bool(self.grid and self.grid[2] and self.grid[1] is not None)

    @staticmethod
    def unit(seq, per):
        beat = per * 24
        div = seq["div"]
        return beat / DIV_DEN[div] if div < len(DIV_DEN) else beat * DIV_BEATS[div - len(DIV_DEN)]

    def swing(self, seq, u):
        return seq["swing"] * u / 200 if seq["div"] < 4 else 0.0     # (seq.c swings(): straight grids only)

    def base(self, seq, a):
        """when grid step a begins (swung), and its length"""
        per, origin, _ = self.grid
        u = self.unit(seq, per)
        sw = self.swing(seq, u)
        return (origin + a * u + sw, u - sw) if a % 2 else (origin + a * u, u + sw)

    def time(self, seq, a):
        """when step a fires: its grid time and its nudge"""
        t, _ = self.base(seq, a)
        return t + seq["micro"][a % seq["len"]] * self.unit(seq, self.grid[0]) / 64

    def repeat_unit(self):
        per = self.grid[0] if self.grid else 60.0 / (120 * 24)
        return per * 24 / DIV_DEN[self.repeat]

    def hold(self, pad, vel, now):
        """a pad held with note repeat on: its next hit on the repeat's grid after this one"""
        if self.repeat is None:
            return
        u = self.repeat_unit()
        origin = self.grid[1] if self.playing() else now
        self.held[pad] = [vel, origin + (math.floor((now - origin) / u + 0.5) + 1) * u]

    def let_go(self, pad):
        self.held.pop(pad, None)

    def repeats(self, seq, t0, t1):
        out = []
        if self.repeat is None:
            self.held = {}
            return out
        u = self.repeat_unit()
        for pad, held in self.held.items():
            held[1] = max(held[1], t0 - u)      # (a held pad far behind: no burst of catch-up hits)
            while held[1] < t1:
                out.append((max(held[1], t0), "on", pad, held[0]))
                self.record(seq, pad, held[0], held[1])
                held[1] += u
        return out

    def events(self, seq, t0, t1):
        due = self.steps_due(seq, t0, t1)        # (first: a repeat recorded now lands on a step already played)
        rep = self.repeats(seq, t0, t1)
        return sorted(due + rep) if rep else due

    def steps_due(self, seq, t0, t1):
        if not self.playing():
            if self.next is not None:           # stopped: what's held ends
                self.next, self.step, self.skip = None, None, set()
                offs = [(t0, "off", p[2], 0) for p in self.pending if p[1] == "off"]
                self.pending = []
                return offs
            return []
        u = self.unit(seq, self.grid[0])
        if self.next is None or self.time(seq, self.next) < t0 - 2 * u:   # PLAY, or the grid jumped: catch up
            a = max(0, math.floor((t0 - self.grid[1]) / u))
            while self.time(seq, a) < t0 - 0.002:
                a += 1
            self.next = a
        while self.time(seq, self.next) < t1:
            self.fire(seq, self.next)
            self.next += 1
        due = sorted(p for p in self.pending if p[0] < t1)
        self.pending = [p for p in self.pending if p[0] >= t1]
        return due

    def fire(self, seq, a):
        idx = a % seq["len"]
        self.step = idx
        t = self.time(seq, a)
        _, slen = self.base(seq, a)
        for pad, lvl, rat in seq["steps"][idx]:
            if (a, pad) in self.skip:
                continue
            for h in range(rat):                # ratchets: each hit its share of the step, with the nudge
                at = t + h * slen / rat
                self.pending += [(at, "on", pad, LEVEL_VEL[lvl]), (at + slen * GATE / rat, "off", pad, 0)]
        self.skip = {k for k in self.skip if k[0] > a}

    def record(self, seq, pad, vel, t):
        """live REC: the pad on the step nearest t -> True if the pattern changed"""
        if not (self.rec and self.playing()):
            return False
        u = self.unit(seq, self.grid[0])
        a0 = round((t - self.grid[1]) / u)
        a = min((a for a in (a0 - 1, a0, a0 + 1) if a >= 0), key=lambda a: abs(t - self.base(seq, a)[0]),
                default=None)
        if a is None:
            return False
        idx = a % seq["len"]
        seq["steps"][idx] = sorted([h for h in seq["steps"][idx] if h[0] != pad] + [[pad, vel_level(vel), 1]])
        if not seq["quant"]:                    # QUANT off: the step's nudge keeps where it was played
            seq["micro"][idx] = max(-32, min(31, round((t - self.base(seq, a)[0]) / u * 64)))
        if self.next is not None and a >= self.next:
            self.skip.add((a, pad))             # heard as it was played: not again when its step comes
        self.dirty = True
        return True


# ---------------------------------------------------------------- the engine

class Engine:
    """renders the pads' voices; everything on one thread (the pad process's)"""

    def __init__(self, rate=48000):
        self.rate = rate
        self.kit = empty_kit()
        self.samples = {}                       # path -> (float32 stereo frames, its rate)
        self.voices = []                        # dicts: pad, data, pos, step, gl, gr, env, target, rate_env
        self.master = 1.0
        self.seq = Sequencer()
        import padfx
        self.fx = padfx.PunchFX(rate)           # the punch-in FX, on everything the pads play

    def set_kit(self, kit):
        self.kit = clean_kit(kit)
        wanted = {p["sample"] for p in self.kit["pads"] if p["sample"]}
        for path in [k for k in self.samples if k not in wanted]:
            del self.samples[path]

    def sample(self, path):
        if path not in self.samples:
            n = np()
            try:
                with wave.open(str(path)) as w:
                    rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
                    data = w.readframes(min(w.getnframes(), rate * 30))        # 30 s at most a pad
                if width != 2:
                    raise ValueError("not 16-bit")
                x = n.frombuffer(data, dtype="<i2").reshape(-1, ch).astype(n.float32) / 32768.0
                x = n.repeat(x, 2, axis=1) if ch == 1 else x[:, :2]
                if rate != self.rate and len(x) > 1:        # once, at load: untuned pads then play by plain copy
                    t = n.arange(int(len(x) * self.rate / rate)) * (rate / self.rate)
                    x = n.stack([n.interp(t, n.arange(len(x)), x[:, c]) for c in (0, 1)], axis=1).astype(n.float32)
                    rate = self.rate
                self.samples[path] = (n.ascontiguousarray(x), rate)
            except (OSError, ValueError, EOFError, wave.Error):
                self.samples[path] = None
        return self.samples[path]

    def trigger(self, index, velocity=100):
        pad = self.kit["pads"][index]
        found = pad["sample"] and self.sample(pad["sample"])
        if not found:
            return
        data, rate = found
        if pad["reverse"]:
            data = data[::-1]
        if pad["choke"]:                        # its group goes quiet, fast
            for v in self.voices:
                if v["choke"] == pad["choke"] and v["pad"] != index:
                    self.release_voice(v, 5)
        if pad["mode"] != "one-shot":           # a held pad again: a fresh voice, the old one fades
            for v in self.voices:
                if v["pad"] == index:
                    self.release_voice(v, 5)
        gain = pad["level"] / 100 * (velocity / 127) ** 1.5
        pan = pad["pan"] / 50
        attack = max(1, pad["attack"] * self.rate // 1000)
        self.voices.append({"pad": index, "data": data, "pos": 0.0, "mode": pad["mode"], "choke": pad["choke"],
                            "step": rate / self.rate * 2 ** (pad["tune"] / 12),
                            "gl": gain * min(1.0, 1 - pan), "gr": gain * min(1.0, 1 + pan),
                            "env": 0.0, "target": 1.0, "slope": 1.0 / attack,
                            "release": max(1, pad["release"] * self.rate // 1000), "done": False})
        while len(self.voices) > VOICES:        # too many: the oldest stops
            self.voices.pop(0)

    @staticmethod
    def release_voice(v, frames):
        v["target"], v["slope"] = 0.0, max(v["env"], 1e-6) / max(1, frames)

    def release(self, index):
        """the pad's note let go: gate and loop pads fade out over their release; one-shots play on"""
        for v in self.voices:
            if v["pad"] == index and v["mode"] != "one-shot":
                self.release_voice(v, v["release"])

    def hit(self, index, velocity=100, now=None):
        """a pad played (a tap, a MIDI note): heard, and recorded while live REC runs -> True if recorded"""
        now = time.monotonic() if now is None else now
        self.trigger(index, velocity)
        self.seq.hold(index, velocity, now)
        return self.seq.record(self.kit["seq"], index, velocity, now)

    def midi(self, message, now=None):
        """-> True if a note was recorded"""
        if len(message) == 3 and message[0] & 0xF0 in (0x80, 0x90):
            pad = pad_of(message[1], message[0] & 0x0F, self.kit["map"])
            if pad is not None:
                if message[0] & 0xF0 == 0x90 and message[2]:
                    return self.hit(pad, message[2], now)
                self.seq.let_go(pad)
                self.release(pad)
        elif len(message) == 3 and message[0] & 0xF0 == 0xB0 and message[1] in (120, 123):   # all sound/notes off
            for v in self.voices:
                self.release_voice(v, 20)

    def render(self, n, t0=None):
        """the next n frames -> float32 (n, 2). t0: when the first of them is heard: the sequencer's hits due
        in them start at their own frames"""
        return self.fx.process(self.render_steps(n, t0), self.seq.grid, t0)

    def render_steps(self, n, t0):
        if t0 is None:
            return self.render_voices(n)
        events = self.seq.events(self.kit["seq"], t0, t0 + n / self.rate)
        if not events:
            return self.render_voices(n)
        num = np()
        out, done = num.zeros((n, 2), dtype=num.float32), 0
        for at, kind, pad, vel in events:
            frame = max(done, min(n, int(round((at - t0) * self.rate))))
            if frame > done:
                out[done:frame] = self.render_voices(frame - done)
                done = frame
            self.trigger(pad, vel) if kind == "on" else self.release(pad)
        if done < n:
            out[done:] = self.render_voices(n - done)
        return out

    def render_voices(self, n):
        num = np()
        out = num.zeros((n, 2), dtype=num.float32)
        steps = num.arange(n, dtype=num.float64)
        for v in self.voices:
            data, length = v["data"], len(v["data"])
            if v["step"] == 1.0 and v["mode"] != "loop":    # at its own rate, untuned: a plain copy
                start = int(v["pos"])
                chunk = data[start:start + n]
                live = len(chunk)
                if live < n:
                    v["done"] = True
                if not live:
                    continue
                env = self.envelope(v, live)
                out[:live, 0] += chunk[:, 0] * env * v["gl"]
                out[:live, 1] += chunk[:, 1] * env * v["gr"]
                v["pos"] = float(start + live)
                continue
            idx = v["pos"] + steps * v["step"]
            if v["mode"] == "loop":
                idx = num.mod(idx, length - 1)
                live = n
            else:
                live = int(num.searchsorted(idx, length - 1))
                if live < n:
                    v["done"] = True
                idx = idx[:live]
            if not live:
                v["done"] = True
                continue
            i0 = idx.astype(num.int64)
            frac = (idx - i0).astype(num.float32)[:, None]
            i1 = num.minimum(i0 + 1, length - 1)
            chunk = data[i0] * (1 - frac) + data[i1] * frac
            env = self.envelope(v, live)
            out[:live, 0] += chunk[:, 0] * env * v["gl"]
            out[:live, 1] += chunk[:, 1] * env * v["gr"]
            v["pos"] = float(idx[-1] + v["step"]) if live else v["pos"]
        self.voices = [v for v in self.voices if not v["done"]]
        return out * self.master

    @staticmethod
    def envelope(v, n):
        """the voice's level for its next n frames: a straight ramp toward its target, then steady"""
        num = np()
        env, target, slope = v["env"], v["target"], v["slope"]
        if env == target:
            levels = num.full(n, env, dtype=num.float32)
        else:
            direction = 1.0 if target > env else -1.0
            levels = env + direction * slope * num.arange(1, n + 1, dtype=num.float32)
            levels = num.minimum(levels, target) if direction > 0 else num.maximum(levels, target)
        v["env"] = float(levels[-1])
        if v["target"] == 0.0 and v["env"] <= 1e-4:
            v["done"] = True
        return levels

    def sounding(self):
        """the pads sounding now"""
        return sorted({v["pad"] for v in self.voices})


def to_pcm(block):
    """float32 (n, 2) -> s16 bytes, clipped"""
    num = np()
    return (num.clip(block, -1.0, 1.0) * 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------- the process

def run(app_conn, midi_conn, rate):
    """the pad process: render, stream into the virtual speaker SINK (pw-loopback makes it, and the SOURCE the
    box hears), take the app's and the MIDI process's messages between blocks, tell the app which pads sound"""
    import fcntl
    # a sampler left running (an app killed without its children, one started by hand): gone, or the box gets
    # two SAMPLER sources. (Only command lines that start with pw-loopback or pw-cat: never a shell that merely
    # mentions it.)
    subprocess.run(["pkill", "-f", f"^pw-(loopback|cat) .*{SINK}"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    loop = subprocess.Popen(
        ["pw-loopback", "--capture-props", f"media.class=Audio/Sink node.name={SINK} audio.position=[FL,FR] "
                                           "node.description=Sampler priority.session=1",
         "--playback-props", f"media.class=Audio/Source node.name={SOURCE} audio.position=[FL,FR] "
                             "node.description=SAMPLER"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    cat = subprocess.Popen(
        ["pw-cat", "--playback", "--raw", "--target", SINK, "--rate", str(rate), "--channels", "2",
         "--format", "s16", "--latency", "10ms", "-P", "{ node.name = fm1-pads node.dont-reconnect = true }", "-"],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:                                        # a small pipe: a block written is heard ~20 ms later, not 300
        fcntl.fcntl(cat.stdin.fileno(), 1031, 4096)                 # F_SETPIPE_SZ
    except OSError:
        pass
    engine, shown, step_shown = Engine(rate), None, None
    ahead = 1024 / rate + 0.010                 # a block written now is heard after the pipe (4096 B) and pw-cat
    base, frames = None, 0                      # heard-time of frame 0; frames written since
    try:
        while True:
            for conn in (app_conn, midi_conn):
                while conn is not None and conn.poll():
                    try:
                        msg = conn.recv()
                    except (EOFError, OSError):
                        os._exit(0)
                    if msg[0] == "kit":
                        engine.set_kit(msg[1])
                    elif msg[0] == "tap":
                        if engine.hit(msg[1], msg[2]):
                            engine.seq.dirty = False
                            app_conn.send(("seq", engine.kit["seq"]))
                    elif msg[0] == "untap":
                        engine.seq.let_go(msg[1])
                        engine.release(msg[1])
                    elif msg[0] == "midi":
                        if any([engine.midi(bytes(m)) for m in msg[1]]):
                            engine.seq.dirty = False
                            app_conn.send(("seq", engine.kit["seq"]))
                    elif msg[0] == "grid":              # ("grid", seconds a tick, tick 0's time or None, playing)
                        engine.seq.grid = tuple(msg[1:4])
                    elif msg[0] == "rec":
                        engine.seq.rec = bool(msg[1])
                    elif msg[0] == "repeat":            # ("repeat", a DIVS index 0-5, or None: off)
                        engine.seq.repeat = msg[1]
                    elif msg[0] == "fx":                # ("fx", a punch-in FX 0-15, or None: let go)
                        engine.fx.press(msg[1])
                    elif msg[0] == "master":
                        engine.master = float(msg[1])
            heard = time.monotonic() + ahead
            if base is None or abs(base + frames / rate - heard) > 0.015:     # (steady between: frame-exact)
                base, frames = heard, 0
            cat.stdin.write(to_pcm(engine.render(BLOCK, base + frames / rate)))   # blocks while PipeWire is full
            frames += BLOCK
            if engine.seq.dirty:                    # note repeat recorded (taps and notes report at once)
                engine.seq.dirty = False
                app_conn.send(("seq", engine.kit["seq"]))
            if engine.seq.step != step_shown:
                step_shown = engine.seq.step
                app_conn.send(("step", step_shown))
            sounding = engine.sounding()
            if sounding != shown:
                shown = sounding
                app_conn.send(("sounding", sounding))
    except (BrokenPipeError, OSError):
        pass
    finally:
        for proc in (cat, loop):
            proc.terminate()


class PadProcess:
    """the sampler in a process of its own; the app's side. midi_conn: the end the MIDI process sends to"""

    def __init__(self, rate=48000):
        import multiprocessing
        ctx = multiprocessing.get_context("fork")
        self.conn, child = ctx.Pipe()
        self.midi_in, self.midi_conn = ctx.Pipe(duplex=False)     # (the MIDI process writes midi_conn)
        self.proc = ctx.Process(target=run, args=(child, self.midi_in, rate), daemon=True, name="fm1-pads")
        self.sounding = []
        self.step = None                        # the sequencer's playhead
        self.on_seq = None                      # called with the pattern live REC changed (from this thread)
        self.on_step = None                     # called as the playhead moves

    def start(self):
        import threading
        self.proc.start()
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        while True:
            try:
                msg = self.conn.recv()
            except (EOFError, OSError):
                return
            if msg[0] == "sounding":
                self.sounding = msg[1]
            elif msg[0] == "step":
                self.step = msg[1]
                if self.on_step:
                    self.on_step(msg[1])
            elif msg[0] == "seq" and self.on_seq:
                self.on_seq(msg[1])

    def send(self, *msg):
        try:
            self.conn.send(msg)
        except (BrokenPipeError, OSError):
            pass
