"""A pretend FM-1 running SLOOP, for testing the bridge's library without hardware.

It answers SLOOP's editor protocol the way firmware/src/editor.c does for the commands the bridge uses (INFO,
BK_LIST / BK_GET / BK_PUT, SMP_*, LIVE op 0) and looks like mido to sloop.Link: pass it as Link(mido=fake).
"""
import struct
import zlib

import mido

import sloop

HDR = list(sloop.HDR)


def project(fmt=6, fill=0, size=4096):
    """a project object: "FUN<fmt>" magic (stored low byte first) and some content"""
    return bytes([0x30 + fmt]) + b"NUF" + bytes((fill + i) & 0xFF for i in range(size - 4))


def sample_slot(name="TEST", n=3000):
    """a valid user sample slot object: 480-byte header padded to 512, then data"""
    data = bytes((i * 7) & 0xFF for i in range(n))
    hdr = struct.pack("<IHBB8sII8x", 0x504D5346, 1, 1, 0, name.encode().ljust(8, b"\0"), len(data), zlib.crc32(data))
    return hdr.ljust(512, b"\0") + data


class Port:
    def __init__(self, device, callback=None):
        self.device, self.callback, self.closed = device, callback, False

    def send(self, msg):
        self.device.handle(list(msg.data))

    def close(self):
        self.closed = True


class FakeFM1:
    def __init__(self, version="SLOOP 2.7.1", proto=12, pcount=71):
        self.version, self.proto, self.pcount = version, proto, pcount
        self.playing = False
        self.objects = {0: project(6, 1), 1: b"PER3" + bytes(200), 2: project(6, 2), 3: b"", 4: b"", 5: project(6, 5),
                        6: b"UPB1" + bytes(3000), 7: b"", 8: bytes(27 * 128), 9: b"DSU1" + bytes(1460),
                        32: sample_slot("KICK"), 33: b"", 34: b"", 35: b""}
        self.input, self.snapshot, self.put = None, None, None
        self.smp_open, self.smp_data = {}, {}
        self.requests, self.drop_every, self.lose_reply_every, self.mute = 0, 0, 0, False

    # mido's module interface
    Message = mido.Message

    def get_input_names(self):
        return ["Midi Through:Midi Through Port-0 14:0", "Felucca:Felucca MIDI 1 24:0"]

    get_output_names = get_input_names

    def open_input(self, name, callback=None):
        self.input = Port(self, callback)
        return self.input

    def open_output(self, name):
        return Port(self)

    # the firmware
    def reply(self, cmd, args):
        if not self.mute:
            self.input.callback(mido.Message("sysex", data=HDR + [cmd] + [a & 0x7F for a in args]))

    def handle(self, data):
        if data[:3] != HDR:
            return
        self.requests += 1
        if self.drop_every and self.requests % self.drop_every == 0:
            return                                   # a lost frame: the bridge must retry
        cmd, a = data[3], data[4:]
        # carried out, but the reply is lost (the harder case: the device moved on)
        self.mute = bool(self.lose_reply_every and self.requests % self.lose_reply_every == 0)
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler:
            handler(a)
        self.mute = False

    def cmd_1(self, a):                               # INFO
        out = [*self.version.encode(), 0, 2, self.pcount, 45, 64, self.pcount - 8, *b"ANALOG", 0, *b"FM6", 0, 4]
        if self.proto:
            out.append(self.proto)
        self.reply(1, out)

    def cmd_77(self, a):                              # LIVE op 0: flags bit 0 = playing
        self.reply(77, [a[0], int(self.playing), 0] + [0] * 47)

    def listed(self):
        return [i for i in sorted(self.objects) if not (i in (10, 11) and not self.objects[i])]

    def cmd_34(self, a):                              # BK_LIST
        self.snapshot, self.put = {i: self.objects[i] for i in self.listed()}, None
        out = [0, len(self.snapshot)]
        for i, d in self.snapshot.items():
            out += [i, *sloop.u35(len(d)), *sloop.u35(zlib.crc32(d))]
        self.reply(34, out)

    def cmd_35(self, a):                              # BK_GET
        obj, off, count = a[0], sum(a[1 + k] << (7 * k) for k in range(5)), a[6] | (a[7] << 7)
        d = (self.snapshot or {}).get(obj)
        rc = 1 if d is None or not count or count > 256 or off + count > len(d) else 5 if self.snapshot is None else 0
        out = [obj, rc, *sloop.u35(off), 0 if rc else count & 127, 0 if rc else count >> 7]
        if not rc:
            out += sloop.pack7(d[off:off + count])
        self.reply(35, out)

    def cmd_36(self, a):                              # BK_PUT
        op, obj, rc = a[0], a[1], 1
        if op == 0 and obj <= 11:
            length, crc = (sum(a[s + k] << (7 * k) for k in range(5)) for s in (2, 7))
            if obj >= 2 or length:
                self.put, self.snapshot, rc = {"id": obj, "len": length, "crc": crc, "data": bytearray()}, None, 0
            if obj in (10, 11) and self.proto < 11:
                rc = 5                                  # an older device doesn't know it
        elif not self.put or self.put["id"] != obj:
            rc = 5
        elif op == 1 and sum(a[2 + k] << (7 * k) for k in range(5)) == len(self.put["data"]):
            chunk = sloop.unpack7(a[7:])
            if chunk and len(self.put["data"]) + len(chunk) <= self.put["len"]:
                self.put["data"] += chunk
                rc = 0
        elif op == 2:
            d, p = bytes(self.put["data"]), self.put
            self.put = None
            if len(d) != p["len"] or zlib.crc32(d) != p["crc"]:
                rc = 2
            elif obj in (0, 2, 3, 4, 5) and d and sloop.project_format(d) == 0:
                rc = 2
            elif self.playing and obj in (0, 1, 2, 3, 4, 5, 6, 7):
                rc = 3
            else:
                self.objects[obj], rc = d, 0
        self.reply(36, [op, obj, rc])

    def cmd_11(self, a):                              # SMP_BEGIN
        self.smp_open[a[0]], self.smp_data[a[0]] = True, bytearray()
        self.objects[32 + a[0]] = b""
        self.reply(11, [a[0], 0])

    def cmd_14(self, a):                              # SMP_ERASE
        self.objects[32 + a[0]] = b""
        self.reply(14, [a[0], 0])

    def cmd_12(self, a):                              # SMP_WRITE
        slot, off = a[0], a[1] | (a[2] << 7) | (a[3] << 14)
        rc = 4 if not self.smp_open.get(slot) else 1 if off != 512 + len(self.smp_data[slot]) else 0
        if not rc:
            self.smp_data[slot] += sloop.unpack7(a[4:])
        self.reply(12, [slot, a[1], a[2], a[3], rc])

    def cmd_13(self, a):                              # SMP_END
        slot, hdr = a[0], sloop.unpack7(a[1:])
        data = bytes(self.smp_data.get(slot, b""))
        magic, _, _, _, _, length, crc = struct.unpack_from("<IHBB8sII", hdr)
        rc = 2 if magic != 0x504D5346 else 1 if length != len(data) else 3 if crc != zlib.crc32(data) else 0
        if not rc:
            self.objects[32 + slot] = hdr[:480].ljust(512, b"\0") + data
            self.smp_open[slot] = False
        self.reply(13, [slot, rc])

    def cmd_15(self, a):                              # SMP_INFO
        out = [4, 80]
        for k in range(4):
            d = self.objects[32 + k]
            out += [d[6] if d else 0, *(d[8:16].rstrip(b"\0") if d else b""), 0, (len(d) - 512) // 1024 if d else 0]
        self.reply(15, out)
