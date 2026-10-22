"""Tests for the MIDI process's own logic (blemidi.py): BLE MIDI packets, clock tempo, the box as clock master.
No Bluetooth or FM-1 needed (needs mido).

  python tests/test_midi.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fm1-bt-bridge"))

import blemidi  # noqa: E402

TICK_120 = 60 / (120 * 24)


class FakeUsb:
    def __init__(self):
        self.sent = []

    def send_messages(self, messages):
        self.sent.extend(messages)

    def label(self):
        return "Felucca"


def make_bridge():
    bridge = blemidi.MidiBridge(["felucca"])
    bridge.usb = FakeUsb()
    bridge.to_ble_sent = []
    bridge.to_ble = bridge.to_ble_sent.append
    bridge.bluez = {"idle_add": lambda f: f(), "peer_name": lambda p: "Keys"}
    return bridge


def ble_packet(*messages):
    return blemidi.encode([bytes(m) for m in messages], 1000)[0]


def test_packets():
    msgs = [b"\x90\x3c\x64", b"\xf0\x7d\x01\x02\xf7", b"\xf8"]
    packets = blemidi.encode(msgs, 1234)
    stream = blemidi.MidiStream()
    out = []
    for p in packets:
        out += stream.feed(blemidi.strip_timestamps(p))
    assert out == msgs


def test_clock_watch():
    watch = blemidi.ClockWatch()
    t = 100.0
    for i in range(30):
        watch.message(blemidi.CLOCK, t + i * TICK_120)
    now = t + 29 * TICK_120
    assert abs(watch.bpm(now) - 120) < 0.01
    assert watch.bpm(now + 0.6) is None                 # clock stopped
    watch.message(blemidi.START, now)
    assert watch.playing
    watch.message(blemidi.STOP, now)
    assert not watch.playing


def test_follow_passes_clock_box_keeps_it():
    bridge = make_bridge()
    note, clock, start = b"\x90\x3c\x64", b"\xf8", b"\xfa"
    bridge.from_ble(ble_packet(note, clock, start), "/dev/keys")
    assert bridge.usb.sent == [note, clock, start]      # follow: everything through
    assert bridge.watch["BT"].playing
    bridge.usb.sent.clear()
    bridge.clock_mode = "box"
    bridge.from_ble(ble_packet(note, clock, b"\xfc"), "/dev/keys")
    assert bridge.usb.sent == [note]                     # box: its own clock only
    bridge.from_usb(clock)
    bridge.from_usb(note)
    assert bridge.to_ble_sent == [note]


def test_running_status_across_packets():
    bridge = make_bridge()
    first = bytes([0x80, 0x80, 0x90, 0x3C, 0x64])        # header, timestamp, note on
    second = bytes([0x80, 0x81, 0x40, 0x64])            # header, timestamp, running-status data
    bridge.from_ble(first, "/dev/keys")
    bridge.from_ble(second, "/dev/keys")
    assert bridge.usb.sent == [b"\x90\x3c\x64", b"\x90\x40\x64"]


def test_box_clock_timing():
    bridge = make_bridge()
    times = []
    bridge.send_all = lambda m: times.append((time.monotonic(), m))
    bridge.clock_mode, bridge.bpm = "box", 300.0
    threading.Thread(target=bridge.clock_loop, daemon=True).start()
    time.sleep(1.0)
    bridge.clock_mode = "follow"
    ticks = [t for t, m in times if m == b"\xf8"]
    assert 110 <= len(ticks) <= 125, len(ticks)          # 300 BPM: 120 ticks a second
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert abs(sum(gaps) / len(gaps) - 60 / (300 * 24)) < 0.0005
    status = bridge.clock_status(time.monotonic())
    assert status is None or status["source"] != "box"   # back to following
    bridge.clock_mode = "box"
    assert bridge.clock_status(time.monotonic()) == {"bpm": 300, "source": "box", "playing": False}
    bridge.clock_mode = "follow"


def test_transport():
    bridge = make_bridge()
    bridge.transport("start")
    assert bridge.usb.sent == [b"\xfa"] and bridge.to_ble_sent == [b"\xfa"] and bridge.playing
    bridge.transport("stop")
    assert bridge.usb.sent[-1] == b"\xfc" and not bridge.playing


def test_route_passes_everything_by_default():
    route = blemidi.Route()
    for m in (b"\x90\x3c\x64", b"\x80\x3c\x00", b"\xb3\x07\x64", b"\xe0\x00\x40", b"\xf8", b"\xf0\x01\xf7",
              b"\xd0\x30"):
        assert route.apply(m) == m, m


def test_route_channels_transpose_velocity():
    route = blemidi.Route({"channels": 4, "transpose": 12, "velocity": "fixed", "fixed": 90})
    assert route.apply(b"\x91\x3c\x20") == bytes([0x94, 0x48, 90])       # ch 2 -> 5, +12, fixed 90
    assert route.apply(b"\x81\x3c\x00") == bytes([0x84, 0x48, 0x00])
    assert route.apply(b"\xb1\x07\x64") == b"\xb4\x07\x64"               # CC follows the channel
    assert route.apply(b"\x90\x7a\x40") is None                         # 122 + 12: off the keyboard
    table = list(range(16))
    table[0], table[1] = 9, None
    route = blemidi.Route({"channels": table})
    assert route.apply(b"\x90\x3c\x40")[0] == 0x99 and route.apply(b"\x91\x3c\x40") is None
    soft, hard = blemidi.Route({"velocity": "soft"}), blemidi.Route({"velocity": "hard"})
    assert soft.apply(b"\x90\x3c\x20")[2] > 0x20 > hard.apply(b"\x90\x3c\x20")[2] >= 1
    assert soft.apply(b"\x90\x3d\x7f")[2] == 127


def test_route_drops():
    route = blemidi.Route({"drop_clock": True, "drop_aftertouch": True, "drop_bend": True})
    for m in (b"\xf8", b"\xfa", b"\xfc", b"\xd0\x30", b"\xa0\x3c\x30", b"\xe0\x00\x40"):
        assert route.apply(m) is None, m
    assert route.apply(b"\x90\x3c\x40") and route.apply(b"\xf0\x01\xf7")


def test_no_stuck_notes():
    route = blemidi.Route({"transpose": 5})
    assert route.apply(b"\x90\x3c\x40") == b"\x90\x41\x40"
    route.update({"transpose": -7, "channels": 3})                      # changed while the key is held
    assert route.apply(b"\x80\x3c\x00") == b"\x80\x41\x00"             # still stops the note it started
    route.apply(b"\x90\x3c\x40")                                       # now: ch 4, 60 - 7 = 53
    route.update({"on": False})                                         # route switched off mid-note
    assert route.apply(b"\x90\x3c\x00") == b"\x93\x35\x00"             # the note-off still gets out
    assert route.apply(b"\x90\x3e\x40") is None                         # new notes don't


def test_bridge_uses_the_matrix():
    bridge = make_bridge()
    routes = blemidi.default_routes()
    routes["BT>FM-1"]["transpose"] = 2
    routes["FM-1>BT"]["on"] = False
    routes["Box>BT"]["on"] = False
    bridge.set_routes(routes)
    bridge.from_ble(ble_packet(b"\x90\x3c\x40"), "/dev/keys")
    assert bridge.usb.sent == [b"\x90\x3e\x40"]
    bridge.from_usb(b"\x90\x3c\x40")
    assert bridge.to_ble_sent == []                                     # FM-1 > BT off
    bridge.transport("start")
    assert bridge.usb.sent[-1] == b"\xfa" and bridge.to_ble_sent == []  # Box > FM-1 on, Box > BT off


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
