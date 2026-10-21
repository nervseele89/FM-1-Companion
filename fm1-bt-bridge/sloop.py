"""SLOOP's editor protocol over USB MIDI: backups, projects and sample slots.

The protocol is SLOOP's (web/EDITOR_PROTOCOL.md in isod89/sloop-fm1): requests F0 7D 46 4C <cmd> <args> F7,
one at a time, each answered by a frame with the same cmd. The backup and restore follow SLOOP's web editor
(editor.html backupCapture / backupObjects / backupRestore), and its file format, so backups made here open in
SLOOP's editor and the other way round:

  {"format": "sloop-backup", "version": 1, "firmware": "...", "date": "...",
   "objects": [{"id": n, "len": n, "crc": n, "data": base64}, ...]}

A single project travels as {"format": "sloop-project", "version": 1, "firmware", "date", "name", "source",
"len", "crc", "data"}: this bridge's own, for projects shared between backups.
"""
import base64
import json
import queue
import re
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "sloop"))

HDR = (0x7D, 0x46, 0x4C)
INFO, SMP_BEGIN, SMP_WRITE, SMP_END, SMP_ERASE, SMP_INFO = 1, 11, 12, 13, 14, 15
BK_LIST, BK_GET, BK_PUT, LIVE = 34, 35, 36, 77
PUSHES = {23, 24, 26, 32}                 # frames the device sends unasked: never replies
FORMAT, VERSION, PROJECT_FORMAT = "sloop-backup", 1, "sloop-project"
RESTORE = [6, 7, 8, 9, 10, 11, 2, 3, 4, 5, 0, 1]   # banks first, then projects, the working project, the settings
SAMPLES = [32, 33, 34, 35]
OPTIONAL = {8, 9, 10, 11}                 # objects older firmware does not know: skipped there
PROJECT_IDS = {"working": 0, "A": 2, "B": 3, "C": 4, "D": 5}
SMP_DATA, SMP_HDR = 512, 480
CHUNK = 256
NAMES = {0: "working project", 1: "settings", 2: "project A", 3: "project B", 4: "project C", 5: "project D",
         6: "user presets 1-16", 7: "user presets 17-32", 8: "FM6 patch bank", 9: "SYN drum kits", 10: "MIDI map",
         11: "theme", 32: "sample USR1", 33: "sample USR2", 34: "sample USR3", 35: "sample USR4"}
PORT_MATCH = ("felucca", "sloop", "fm-1", "fm1", "clipper")
PORT_SKIP = ("through", "rtmidi", "pipewire", "midi-bridge")


class SloopError(Exception):
    """code: notfound, noreply, old, stop (the song plays), rc, bad (a damaged file), newer"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- encodings

def pack7(data):
    """groups of up to 7 bytes, each preceded by one byte with their top bits"""
    out = []
    for i in range(0, len(data), 7):
        group = data[i:i + 7]
        out.append(sum(((b >> 7) & 1) << j for j, b in enumerate(group)))
        out += [b & 0x7F for b in group]
    return out


def unpack7(args):
    out = bytearray()
    for i in range(0, len(args), 8):
        top, group = args[i], args[i + 1:i + 8]
        out += bytes(b | (((top >> j) & 1) << 7) for j, b in enumerate(group))
    return bytes(out)


def u35(value):
    return [(value >> (7 * k)) & 0x7F for k in range(5)]


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


class Reader:
    def __init__(self, args):
        self.a, self.i = args, 0

    def b(self):
        if self.i >= len(self.a):
            raise SloopError("bad", "short reply")
        self.i += 1
        return self.a[self.i - 1]

    def s(self):
        chars = []
        while (c := self.b()) != 0:
            chars.append(chr(c))
        return "".join(chars)

    def u35(self):
        return sum(self.b() << (7 * k) for k in range(5))

    def more(self):
        return self.i < len(self.a)


# ---------------------------------------------------------------- the MIDI link

def find_ports(names):
    """the FM-1's port among mido's names (the FM-1 is called "Felucca" under SLOOP and Felucca)"""
    names = [n for n in names if not any(s in n.lower() for s in PORT_SKIP)]
    return next((n for n in names if any(m in n.lower() for m in PORT_MATCH)), None)


class Link:
    """The FM-1's MIDI in and out, with a queue of the editor frames it sends."""

    def __init__(self, mido=None, time_scale=1.0):
        if mido is None:
            import mido
        self.mido, self.frames, self.time_scale = mido, queue.Queue(), time_scale    # (tests shorten timeouts)
        inp, out = find_ports(mido.get_input_names()), find_ports(mido.get_output_names())
        if not inp or not out:
            raise SloopError("notfound", "FM-1 not found on USB")
        self.out = mido.open_output(out)
        self.inp = mido.open_input(inp, callback=self.received)

    def received(self, msg):
        if msg.type == "sysex" and tuple(msg.data[:3]) == HDR and len(msg.data) > 3:
            self.frames.put((msg.data[3], list(msg.data[4:])))

    def request(self, cmd, args=(), timeout=1.0, retries=1):
        args = list(args)
        if any(not 0 <= b <= 0x7F for b in args):
            raise ValueError("data bytes must be 7 bit")
        for _ in range(retries + 1):
            while not self.frames.empty():          # a late reply to an earlier try
                self.frames.get_nowait()
            self.out.send(self.mido.Message("sysex", data=[*HDR, cmd, *args]))
            end = time.monotonic() + timeout * self.time_scale
            while (left := end - time.monotonic()) > 0:
                try:
                    got, reply = self.frames.get(timeout=left)
                except queue.Empty:
                    break
                if got == cmd and got not in PUSHES:
                    return reply
        raise SloopError("noreply", f"no reply from the FM-1 (command {cmd})")

    def close(self):
        for port in (self.inp, self.out):
            try:
                port.close()
            except Exception:
                pass


# ---------------------------------------------------------------- the device

class Sloop:
    """SLOOP's editor commands on a Link. progress(done, total) is called as data moves."""

    def __init__(self, link):
        self.link = link
        self.info = self.read_info()

    def read_info(self):
        for attempt in range(3):              # the first frame after opening a port can be lost
            try:
                r = Reader(self.link.request(INFO, timeout=1.0, retries=0))
                break
            except SloopError:
                if attempt == 2:
                    raise
        info = {"version": r.s(), "nengines": r.b(), "pcount": r.b(), "gcount": r.b(), "nstep": r.b(), "pe0": r.b()}
        info["engines"] = [r.s() for _ in range(info["nengines"])]
        info["ntrk"] = r.b() if r.more() else 0
        info["proto"] = r.b() if r.more() else 0
        return info

    @property
    def version(self):
        return self.info["version"]

    def can_backup(self):
        return self.info["proto"] >= 6

    def require_backup(self):
        if not self.can_backup():
            raise SloopError("old", f"{self.version}: backups need SLOOP 2.3 or later")

    def playing(self):
        """True while the song plays (SLOOP 2.7+, LIVE op 0), None if the firmware can't say"""
        if self.info["proto"] < 12:
            return None
        r = self.link.request(LIVE, [0], timeout=1.0)
        return bool(r[1] & 1) if len(r) > 1 else None

    # ---- backup
    def list_objects(self):
        r = Reader(self.link.request(BK_LIST, timeout=5.0, retries=0))
        rc, n = r.b(), r.b()
        if rc:
            raise SloopError("rc", "the FM-1 has no flash" if rc == 4 else f"backup list failed (rc {rc})")
        return [{"id": r.b(), "len": r.u35(), "crc": r.u35()} for _ in range(n)]

    def backup(self, progress=lambda done, total: None):
        """everything the FM-1 stores, as a sloop-backup file (dict)"""
        self.require_backup()
        items = self.list_objects()
        total, done, objects = sum(it["len"] for it in items), 0, []
        for it in items:
            data = bytearray()
            for off in range(0, it["len"], CHUNK):
                n = min(CHUNK, it["len"] - off)
                r = Reader(self.link.request(BK_GET, [it["id"], *u35(off), n & 0x7F, n >> 7], timeout=1.5, retries=2))
                _, rc, _, _ = r.b(), r.b(), r.u35(), r.b() | (r.b() << 7)
                if rc:
                    raise SloopError("rc", "the FM-1 changed during the backup: try again" if rc == 5
                                     else f"reading {NAMES.get(it['id'], it['id'])} failed (rc {rc})")
                chunk = unpack7(r.a[r.i:])
                if len(chunk) != n:
                    raise SloopError("bad", f"{NAMES.get(it['id'], it['id'])}: short read")
                data += chunk
                done += n
                progress(done, total)
            if crc32(data) != it["crc"]:
                raise SloopError("rc", "the FM-1 changed during the backup: try again")
            objects.append({"id": it["id"], "len": it["len"], "crc": it["crc"],
                            "data": base64.b64encode(bytes(data)).decode()})
        return {"format": FORMAT, "version": VERSION, "firmware": self.version,
                "date": datetime.now().astimezone().isoformat(timespec="seconds"), "objects": objects}

    # ---- writing
    def check_stopped(self):
        if self.playing():
            raise SloopError("stop", "stop the song on the FM-1 first")

    def send_chunk(self, cmd, args, rc_at, what, timeout):
        """One data chunk that the device takes only at the next offset (BK_PUT data, SMP_WRITE). If a reply is
        lost the chunk is sent again; "wrong offset" (rc 1) then means the first one landed."""
        lost = False
        for _ in range(3):
            try:
                rc = self.link.request(cmd, args, timeout=timeout, retries=0)[rc_at]
            except SloopError:
                lost = True
                continue
            if rc == 0 or (rc == 1 and lost):
                return
            raise SloopError("rc", f"writing {what} failed (rc {rc})")
        raise SloopError("noreply", f"writing {what}: no reply from the FM-1")

    def put_object(self, obj_id, data, progress=lambda n: None):
        """BK_PUT begin, data, commit; False if the firmware doesn't know an optional object. If the data still
        fails, the object is aborted and written once more from the start."""
        what = NAMES.get(obj_id, obj_id)
        for attempt in (1, 2):
            r = self.link.request(BK_PUT, [0, obj_id, *u35(len(data)), *u35(crc32(data))], timeout=1.5, retries=1)
            if r[2] in (1, 5) and obj_id in OPTIONAL:
                return False
            if r[2]:
                raise SloopError("rc", f"writing {what} failed (begin rc {r[2]})")
            try:
                for off in range(0, len(data), CHUNK):
                    self.send_chunk(BK_PUT, [1, obj_id, *u35(off), *pack7(data[off:off + CHUNK])], 2, what, 1.5)
                    progress(min(CHUNK, len(data) - off))
                break
            except SloopError:
                self.link.request(BK_PUT, [3, obj_id], timeout=1.0, retries=1)     # abort
                if attempt == 2:
                    raise
        try:
            r = self.link.request(BK_PUT, [2, obj_id], timeout=5.0, retries=0)
        except SloopError:
            # the commit's reply was lost: it can't be sent again, so look at what the FM-1 now holds
            if any(it["id"] == obj_id and it["len"] == len(data) and it["crc"] == crc32(data)
                   for it in self.list_objects()):
                return True
            raise
        if r[2] == 3:
            raise SloopError("stop", "stop the song on the FM-1 first")
        if r[2] == 2:
            raise SloopError("bad", f"the FM-1 refused {NAMES.get(obj_id, obj_id)}: not valid for it")
        if r[2]:
            raise SloopError("rc", f"saving {NAMES.get(obj_id, obj_id)} failed (rc {r[2]})")
        return True

    def write_sample_slot(self, slot, header, data, progress=lambda n: None):
        """slot 0..3: a header (480 bytes) and ADPCM data into USR1..4 (started again from SMP_BEGIN once if a
        write fails: the slot takes writes only in increasing order)"""
        for attempt in (1, 2):
            if self.link.request(SMP_BEGIN, [slot], timeout=1.0, retries=1)[1]:
                raise SloopError("rc", f"USR{slot + 1}: could not start the write")
            try:
                for off in range(0, len(data), CHUNK):
                    a = SMP_DATA + off
                    self.send_chunk(SMP_WRITE, [slot, a & 0x7F, (a >> 7) & 0x7F, (a >> 14) & 0x7F,
                                                *pack7(data[off:off + CHUNK])], 4, f"USR{slot + 1}", 1.0)
                    progress(min(CHUNK, len(data) - off))
                break
            except SloopError:
                if attempt == 2:
                    raise
        try:
            rc = self.link.request(SMP_END, [slot, *pack7(header[:SMP_HDR])], timeout=2.0, retries=0)[1]
        except SloopError:
            # the reply was lost: SMP_BEGIN emptied the slot, so a slot with our zones and name took it
            info = self.sample_slots()[slot]
            name = header[8:16].rstrip(b"\0").decode("ascii", "replace")
            if info["zones"] == header[6] and info["name"] == name:
                return
            raise
        if rc:
            raise SloopError("rc", f"USR{slot + 1}: the FM-1 refused the sample (rc {rc})")

    def erase_sample_slot(self, slot):
        if self.link.request(SMP_ERASE, [slot], timeout=2.5, retries=1)[1]:
            raise SloopError("rc", f"USR{slot + 1}: erase failed")

    def sample_slots(self):
        """[{zones, name, kib}] for USR1..n"""
        r = Reader(self.link.request(SMP_INFO, timeout=1.0))
        n, _ = r.b(), r.b()
        return [{"zones": r.b(), "name": r.s(), "kib": r.b()} for _ in range(n)]

    def restore(self, file, progress=lambda done, total: None):
        """a whole sloop-backup back onto the FM-1, as SLOOP's editor does"""
        self.require_backup()
        objects = objects_of(file)
        newest = max([project_format(objects[i]) for i in (0, 2, 3, 4, 5) if i in objects] or [0])
        if newest > device_format(self.info):
            raise SloopError("newer", f"the backup ({file.get('firmware', '?')}) is newer than {self.version}")
        if 10 not in objects and firmware_at_least(file.get("firmware"), (2, 5, 1)):
            midi = bytearray(40)                 # a 2.6 backup without object 10 had the MIDI defaults
            midi[32:37] = bytes([1, 2, 3, 10, 2])
            objects[10] = bytes(midi)
        self.check_stopped()
        total, done = sum(len(d) for d in objects.values()) or 1, [0]

        def moved(n):
            done[0] += n
            progress(done[0], total)

        for obj_id in RESTORE:
            if obj_id in objects:
                self.put_object(obj_id, objects[obj_id], moved)
        for obj_id in SAMPLES:
            if obj_id in objects:
                d = objects[obj_id]
                if not d:
                    self.erase_sample_slot(obj_id - 32)
                else:
                    self.write_sample_slot(obj_id - 32, d[:SMP_HDR], d[SMP_DATA:], moved)

    def put_project(self, data, target, progress=lambda done, total: None):
        """one project (backup object bytes) into "working" or "A".."D" """
        self.require_backup()
        if project_format(data) > device_format(self.info):
            raise SloopError("newer", f"the project is newer than {self.version} reads")
        self.check_stopped()
        done = [0]

        def moved(n):
            done[0] += n
            progress(done[0], len(data))

        self.put_object(PROJECT_IDS[target], data, moved)

    def load_samples(self, slot, name, wavs, progress=lambda done, total: None):
        """WAV files into USR slot+1, one zone each (root note from the file name, as SLOOP's uploader)"""
        header, data = build_slot(name, wavs)
        self.check_stopped()
        done = [0]

        def moved(n):
            done[0] += n
            progress(done[0], len(data))

        self.write_sample_slot(slot, header, data, moved)
        return len(data)


# ---------------------------------------------------------------- files

def objects_of(file, need_complete=True):
    """a sloop-backup -> {id: bytes}, every object checked against its length and CRC"""
    if not isinstance(file, dict) or file.get("format") != FORMAT or not file.get("version", 0) >= 1 \
            or not isinstance(file.get("objects"), list):
        raise SloopError("bad", "not a SLOOP backup")
    objects = {}
    for o in file["objects"]:
        try:
            data = base64.b64decode(o.get("data", ""), validate=False)
        except (ValueError, TypeError):
            raise SloopError("bad", f"object {o.get('id')}: damaged")
        if o.get("id") not in RESTORE + SAMPLES or len(data) != o.get("len") or crc32(data) != (o.get("crc", 0) & 0xFFFFFFFF):
            raise SloopError("bad", f"object {o.get('id')}: damaged")
        objects[o["id"]] = data
    if need_complete and (0 not in objects or 1 not in objects):
        raise SloopError("bad", "incomplete backup")
    return objects


def project_format(data):
    """the project format of an object ("FUN6" stored low byte first: 6), 0 if not a project"""
    return data[0] - 0x30 if data and len(data) >= 4 and data[1:4] == b"NUF" else 0


def device_format(info):
    """the newest project format the device reads: 2.6+ (71 parameters) 6, 2.4-2.5 5, older 4"""
    return 6 if info["pcount"] >= 71 else 5 if info["pcount"] >= 61 else 4


def firmware_at_least(version, want):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    return bool(m) and tuple(int(g or 0) for g in m.groups()) >= tuple(want)


def project_file(data, name, firmware, source):
    return {"format": PROJECT_FORMAT, "version": 1, "firmware": firmware, "name": name, "source": source,
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "len": len(data), "crc": crc32(data), "data": base64.b64encode(data).decode()}


def project_from(file, source=None):
    """project bytes from a sloop-project file, or from a backup's project ("working", "A".."D")"""
    if isinstance(file, dict) and file.get("format") == PROJECT_FORMAT:
        data = base64.b64decode(file.get("data", ""))
        if len(data) != file.get("len") or crc32(data) != file.get("crc"):
            raise SloopError("bad", "the project file is damaged")
    else:
        objects = objects_of(file, need_complete=False)
        data = objects.get(PROJECT_IDS.get(source or "working"))
        if data is None:
            raise SloopError("bad", f"no project {source} in that backup")
    if not data:
        raise SloopError("bad", f"project {source} is empty")
    if not project_format(data):
        raise SloopError("bad", "not a SLOOP project")
    return data


def projects_in(file):
    """{"working": bytes, "A": ...} of the non-empty projects in a backup"""
    objects = objects_of(file, need_complete=False)
    return {name: objects[i] for name, i in PROJECT_IDS.items() if objects.get(i)}


def build_slot(name, wavs):
    """WAV paths -> (header, ADPCM data), as SLOOP's fm1_sample_upload.build"""
    import sampleio as sio
    zones = []
    for path in wavs:
        path = Path(path)
        rate, x = sio.read_any_wav(path)
        x = sio.resample(x, rate, sio.SLOT_RATE)
        if not x:
            raise SloopError("bad", f"{path.name}: no audio")
        zones.append((sio.to_int16(x), sio.note_from_name(path.stem), None, None))
    try:
        return sio.user_slot(name, zones)
    except ValueError as e:
        raise SloopError("bad", str(e))


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise SloopError("bad", f"{Path(path).name}: {e}")


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def describe(file):
    """a short summary of a backup: firmware, date, what's in it"""
    objects = objects_of(file, need_complete=False)
    projects = [n for n, i in PROJECT_IDS.items() if objects.get(i) and n != "working"]
    samples = [f"USR{i - 31}" for i in SAMPLES if objects.get(i)]
    return {"firmware": file.get("firmware"), "date": file.get("date"), "projects": projects, "samples": samples,
            "bytes": sum(len(d) for d in objects.values())}
