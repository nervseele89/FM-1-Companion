"""A metronome click on the speaker, in time with the MIDI clock (the box's own or an incoming one).

It runs in the MIDI process, next to the clock, on a real-time thread: in the screen's process drawing a frame
holds Python up for ~75 ms, and the click stream ran dry hundreds of times. A pw-cat playback stream (mono, at
PipeWire's rate, so nothing is resampled) is fed 20 ms blocks: silence, with a short click wherever a beat falls
(higher on beat 1). The beats come straight from the clock (its exact tempo and the time its bar began).

When a block plays is worked out from the stream itself: block n plays at offset + n blocks + LATENCY, where
offset is the earliest "time a write returned minus audio written" seen (a write returns as soon as the short
pipe has room, so that's when the stream is caught up). A late wake-up doesn't move the clicks; a real gap in
the stream (an underrun) moves the offset, and a run of later readings then takes over.
"""
import math
import subprocess
import threading
import time
from array import array

BLOCK_SECONDS = 0.02                    # each write: 20 ms of audio
PIPE = 8192                             # bytes queued for pw-cat: ~85 ms at 48 kHz mono (room for a hiccup)
LATENCY = 0.21                          # seconds from the stream being caught up to hearing it (pipe, pw-cat, graph)
LEVELS = {"low": 0.15, "mid": 0.35, "high": 0.7}


def click(freq, level, rate, ms=25):
    """a short decaying sine, s16 mono bytes"""
    n = int(rate * ms / 1000)
    return array("h", [int(32767 * level * math.sin(2 * math.pi * freq * i / rate) * math.exp(-5 * i / n))
                       for i in range(n)]).tobytes()


def fill_block(start, clock, last, accent, normal, carry=b"", rate=48000):
    """One block of audio that starts playing at monotonic time `start`: silence and the clicks of the beats
    that fall in it (beat 1 of the bar: `accent`). last: when the last click was (so a beat isn't clicked twice
    when blocks overlap, whatever happens to the bar's start); carry: the end of a click that didn't fit the
    last block. -> (block bytes, last, carry). A beat at most 30 ms before `start` (a late block) still clicks,
    at once."""
    frames = int(rate * BLOCK_SECONDS)
    out = bytearray(frames * 2)
    out[:len(carry[:frames * 2])] = carry[:frames * 2]
    carry = carry[frames * 2:]
    tempo = clock and (clock.get("tempo") or clock.get("bpm"))
    if tempo and clock.get("bar"):
        beat, bar = 60.0 / tempo, clock["bar"]
        k, end = math.ceil((start - 0.03 - bar) / beat), start + frames / rate
        while bar + k * beat < end:
            t = bar + k * beat
            if t > last + beat / 2:
                at = max(0, int((t - start) * rate)) * 2
                sound = accent if k % 4 == 0 else normal
                fit = sound[:len(out) - at]
                out[at:at + len(fit)] = fit
                carry, last = sound[len(fit):] or carry, t
            k += 1
    return bytes(out), last, carry


class StreamClock:
    """When each block of the stream will be heard, from when the writes return (see the module's notes)."""

    def __init__(self, rate):
        self.rate, self.written, self.offset, self.later = rate, 0, None, []

    def block_start(self):
        """when the next block to be written will be heard"""
        base = self.offset if self.offset is not None else time.monotonic()
        return base + self.written / self.rate + LATENCY

    def wrote(self, frames, returned):
        self.written += frames
        reading = returned - self.written / self.rate
        if self.offset is None or reading < self.offset:
            self.offset, self.later = reading, []
        elif reading > self.offset + 0.02:             # consistently later: the stream really slipped
            self.later.append(reading)
            if len(self.later) >= 25:                   # half a second of it: believe it
                self.offset, self.later = min(self.later), []
        else:
            self.later = []


class Metronome:
    """ensure() is called a few times a second by the MIDI process with what the box wants."""

    def __init__(self, clock_source, on_start=lambda: None):
        self.clock_source = clock_source      # () -> {"tempo", "bar", ...} or None
        self.on_start = on_start              # called on the stream's thread first (real-time priority)
        self.proc, self.key = None, None

    def ensure(self, on, target, level="mid", rate=48000):
        key = (target, level, rate) if on and target else None
        if key == self.key and (key is None or (self.proc and self.proc.poll() is None)):
            return
        self.stop()
        if key:
            self.start(target, level, rate)

    def start(self, target, level, rate):
        try:
            self.proc = subprocess.Popen(
                ["pw-cat", "--playback", "--raw", "--target", target, "--rate", str(rate), "--channels", "1",
                 "--format", "s16", "--latency", "100ms", "-P", "{ node.dont-reconnect = true }", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return
        try:
            import fcntl
            fcntl.fcntl(self.proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, PIPE)   # a short queue: clicks on time
        except (ImportError, AttributeError, OSError):
            pass
        self.key = (target, level, rate)
        threading.Thread(target=self.run, args=(self.proc, LEVELS.get(level, 0.35), rate), daemon=True).start()

    def run(self, proc, level, rate):
        self.on_start()
        accent, normal = click(1760, level, rate), click(1175, level * 0.8, rate)
        stream, last, carry = StreamClock(rate), float("-inf"), b""
        frames = int(rate * BLOCK_SECONDS)
        while proc.poll() is None:
            block, last, carry = fill_block(stream.block_start(), self.clock_source(), last, accent, normal,
                                            carry, rate)
            try:
                proc.stdin.write(block)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return
            stream.wrote(frames, time.monotonic())

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        self.proc, self.key = None, None
