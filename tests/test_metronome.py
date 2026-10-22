"""Tests for the metronome (metronome.py): where the clicks land, the stream's timing, starting and stopping.

  python tests/test_metronome.py
"""
import subprocess
import sys
import time
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fm1-bt-bridge"))

import metronome as m  # noqa: E402

RATE = 48000
FRAMES = int(RATE * m.BLOCK_SECONDS)
ACCENT, NORMAL = m.click(1760, 0.5, RATE), m.click(1175, 0.4, RATE)


def clicks_in(blocks, start):
    """(time, accent?) of each click in a run of consecutive blocks starting at `start`"""
    audio = array("h", b"".join(blocks))
    found, i = [], 0
    while i < len(audio):
        if audio[i]:
            piece = audio[i:i + 480]                      # 10 ms: count zero crossings for the pitch
            crossings = sum(1 for a, b in zip(piece, piece[1:]) if (a < 0) != (b < 0))
            found.append((round(start + (i - 1) / RATE, 3), crossings > 29))    # 1760 Hz: ~35, 1175 Hz: ~23
            i += len(ACCENT) // 2 + 10
        else:
            i += 1
    return found


def test_clicks_on_the_beats():
    clock = {"bpm": 120, "tempo": 120.0, "bar": 100.0}
    start, last, carry, blocks = 100.0, float("-inf"), b"", []
    t = start
    for _ in range(int(2.1 / m.BLOCK_SECONDS)):           # 2.1 s of consecutive blocks
        block, last, carry = m.fill_block(t, clock, last, ACCENT, NORMAL, carry, RATE)
        blocks.append(block)
        t += FRAMES / RATE
    found = clicks_in(blocks, start)
    assert [x for x, _ in found] == [100.0, 100.5, 101.0, 101.5, 102.0], found
    assert [a for _, a in found] == [True, False, False, False, True]   # beat 1 of each bar is the high one
    clock = {"bpm": 96, "tempo": 96.4, "bar": 200.0}      # the exact tempo, not the rounded one, sets the beat
    block, last, _ = m.fill_block(200.0 + 60 / 96.4 - 0.001, clock, float("-inf"), ACCENT, NORMAL, b"", RATE)
    assert abs(last - (200.0 + 60 / 96.4)) < 1e-9


def test_overlap_late_block_and_carry():
    clock = {"bpm": 120, "bar": 100.0}
    block, last, carry = m.fill_block(100.49, clock, float("-inf"), ACCENT, NORMAL, b"", RATE)
    assert last == 100.5 and carry                         # the click starts 10 ms in and runs over the block
    again, last2, _ = m.fill_block(100.495, clock, last, ACCENT, NORMAL, b"", RATE)
    assert last2 == last and not any(again)                # overlapping block: no second click
    late, last3, _ = m.fill_block(101.02, clock, last, ACCENT, NORMAL, b"", RATE)
    assert last3 == 101.0 and any(late[:40])               # 20 ms late: it clicks at once
    nothing, _, _ = m.fill_block(101.2, None, last3, ACCENT, NORMAL, b"", RATE)
    assert not any(nothing)                                # no clock: silence


def test_stream_clock():
    s = m.StreamClock(RATE)
    t = 1000.0
    for n in range(50):                                    # a steady stream: writes return 20 ms apart
        late = 0.03 if n % 7 == 3 else 0.0                 # now and then the thread wakes late
        s.wrote(FRAMES, t + (n + 1) * m.BLOCK_SECONDS + late)
    assert abs(s.offset - t) < 1e-9                        # the late wake-ups don't move the grid
    assert abs(s.block_start() - (t + 50 * m.BLOCK_SECONDS + m.LATENCY)) < 1e-9    # the 51st block
    for n in range(50, 80):                                # a real slip: the stream fell 100 ms behind
        s.wrote(FRAMES, t + (n + 1) * m.BLOCK_SECONDS + 0.1)
    assert abs(s.offset - (t + 0.1)) < 1e-9                # after half a second of it, the grid follows


class FakePwCat:
    """stands in for pw-cat: a Python process that reads its input and throws it away"""
    real = subprocess.Popen

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        return self.real([sys.executable, "-c", "import sys\nwhile sys.stdin.buffer.read(4096): pass"], **kw)


def test_stream_follows_the_setting():
    fake = FakePwCat()
    m.subprocess.Popen = fake
    try:
        clock = {"bpm": 150, "bar": time.monotonic()}
        started = []
        metro = m.Metronome(lambda: clock, on_start=lambda: started.append(1))
        metro.ensure(False, "bluez_output.jbl")
        assert not metro.running() and not fake.cmds
        metro.ensure(True, None)                           # no speaker: nothing
        assert not metro.running()
        metro.ensure(True, "bluez_output.jbl", "high", 44100)
        cmd = fake.cmds[-1]
        assert metro.running() and cmd[:2] == ["pw-cat", "--playback"] and "bluez_output.jbl" in cmd
        assert cmd[cmd.index("--rate") + 1] == "44100"     # PipeWire's own rate: nothing to resample
        time.sleep(0.3)                                    # blocks flow, on a thread that asked for priority
        assert started
        metro.ensure(True, "bluez_output.jbl", "high", 44100)
        assert len(fake.cmds) == 1                         # nothing changed: same stream
        metro.ensure(True, "bluez_output.other", "high", 44100)
        assert len(fake.cmds) == 2 and metro.running()     # another speaker: a new stream
        metro.ensure(False, "bluez_output.other")
        assert not metro.running()
    finally:
        m.subprocess.Popen = FakePwCat.real


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
