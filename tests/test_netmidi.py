"""Network MIDI (RTP-MIDI): packets both ways, a session started from a 'computer' on this machine over UDP, and
the Mackie Control state a DAW sends. No network beyond localhost needed."""

import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fm1-bt-bridge"))

import netmidi  # noqa: E402


def test_packets():
    msgs = [b"\x90\x3c\x64", b"\xb0\x07\x50", b"\xf8", b"\xf0\x7d\x01\x02\xf7"]
    packet = netmidi.encode(msgs, 7, 0x1234)
    assert packet[:2] == b"\x80\x61" and struct.unpack(">H", packet[2:4])[0] == 7
    assert netmidi.decode(packet) == msgs
    long = [bytes([0x90, n, 100]) for n in range(10)]               # over 15 bytes: the long header
    assert netmidi.decode(netmidi.encode(long, 1, 1)) == long
    assert netmidi.encode([], 1, 1) is None
    # what a Mac sends: running status, a delta time before the first (Z), the journal flag (J) after
    body = bytes([0x90, 0x3c, 0x64, 0x00, 0x3e, 0x64, 0x00, 0xc0, 0x05])
    head = struct.pack(">BBHII", 0x80, 0x61, 1, 0, 9)
    assert netmidi.decode(head + bytes([0x60 | len(body) + 1, 0x00]) + body + b"\x20\x00") == [
        b"\x90\x3c\x64", b"\x90\x3e\x64", b"\xc0\x05"]
    assert netmidi.decode(b"\xff\xffIN") == []                      # a session command: not MIDI


def test_session():
    got = []
    session = netmidi.Session("FM-1 Companion", 15004, got.append)
    assert session.start()
    mac = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mac_data = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mac.settimeout(2)
    mac_data.settimeout(2)
    invite = b"\xff\xffIN" + struct.pack(">III", 2, 0xABCD, 0x5151) + b"Pat's Mac\0"
    mac.sendto(invite, ("127.0.0.1", 15004))
    reply, _ = mac.recvfrom(256)
    assert reply[:4] == b"\xff\xffOK" and struct.unpack(">I", reply[8:12])[0] == 0xABCD
    assert reply[16:].split(b"\0")[0] == b"FM-1 Companion"
    mac_data.sendto(invite, ("127.0.0.1", 15005))
    assert mac_data.recvfrom(256)[0][:4] == b"\xff\xffOK"
    time.sleep(0.05)
    assert session.names() == ["Pat's Mac"]
    mac_data.sendto(b"\xff\xffCK" + struct.pack(">IB3xQQQ", 0x5151, 0, 1000, 0, 0), ("127.0.0.1", 15005))
    ck, _ = mac_data.recvfrom(256)
    count, t1, t2 = struct.unpack(">B3xQQ", ck[8:28])
    assert ck[:4] == b"\xff\xffCK" and count == 1 and t1 == 1000 and t2 > 0
    mac_data.sendto(netmidi.encode([b"\x90\x40\x70"], 1, 0x5151), ("127.0.0.1", 15005))
    time.sleep(0.1)
    assert got == [b"\x90\x40\x70"]                                 # the DAW's note, through to the box
    session.send([b"\x80\x40\x00"])
    assert netmidi.decode(mac_data.recvfrom(256)[0]) == [b"\x80\x40\x00"]   # and the box's back
    mac.sendto(b"\xff\xffBY" + struct.pack(">III", 2, 0xABCD, 0x5151), ("127.0.0.1", 15004))
    time.sleep(0.1)
    assert session.names() == []


def test_mackie():
    m = netmidi.Mackie()
    m.feed(bytes([0xE2, 0x7F, 0x3F]))                               # strip 3's fader
    m.feed(bytes([0x90, 0x5E, 0x7F]))                               # Play lit
    m.feed(bytes([0xF0, 0x00, 0x00, 0x66, 0x14, 0x12, 0x00]) + b"BASS   LEAD" + b"\xf7")
    m.feed(bytes([0xF0, 0x00, 0x00, 0x66, 0x14, 0x12, 56]) + b"-6.0" + b"\xf7")
    s = m.state()
    assert s["faders"][2] == 0x7F | 0x3F << 7 and s["leds"] == [0x5E]
    assert s["lcd"][0].startswith("BASS   LEAD") and s["lcd"][1].startswith("-6.0") and len(s["lcd"][0]) == 56
    assert netmidi.Mackie.fader(8, 20000) == bytes([0xE8, 0x7F, 0x7F])
    # what Ableton Live adds: the handshake, the pan knobs' rings, the level meters
    query = m.feed(bytes([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7]))
    assert query == [bytes([0xF0, 0, 0, 0x66, 0x14, 0x01]) + b"FM1CMP1" + bytes(4) + b"\xf7"]
    assert m.feed(bytes([0xF0, 0, 0, 0x66, 0x14, 0x02]) + b"FM1CMP1" + bytes(4) + b"\xf7") == [
        bytes([0xF0, 0, 0, 0x66, 0x14, 0x03]) + b"FM1CMP1\xf7"]
    m.feed(bytes([0xB0, 0x31, 0x16]))                               # strip 2's ring at 6 (the middle)
    m.feed(bytes([0xD0, 0x2B]), now=100.0)                          # strip 3's meter at 11
    assert m.state()["rings"][1] == 6
    assert m.meter_levels(100.0)[2] == 11 and m.meter_levels(101.0)[2] == 11 - 3   # falls 1 step a 0.3 s
    assert netmidi.Mackie.pan(4, -3) == bytes([0xB0, 0x14, 67]) and netmidi.Mackie.pan(0, 2) == bytes([0xB0, 0x10, 2])
    assert netmidi.Mackie.press(0x5E, False) == bytes([0x90, 0x5E, 0])


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
