"""Tests for the library (sloop.py, library.py) against a pretend FM-1. No hardware needed.

  python tests/test_library.py        (needs mido: pip install mido)
"""
import json
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fm1-bt-bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
TMP = Path(tempfile.mkdtemp(prefix="fm1-test-"))
os.environ.update(FM1_LIBRARY=str(TMP / "library"), XDG_RUNTIME_DIR=str(TMP / "run"), FM1_SD_LIBRARY=str(TMP / "sd"))

import fake_fm1  # noqa: E402
import library  # noqa: E402
import sloop  # noqa: E402


def device(**kw):
    fake = fake_fm1.FakeFM1(**kw)
    return fake, sloop.Sloop(sloop.Link(mido=fake, time_scale=0.02))


def use(fake):
    """library jobs connect through the fake"""
    library.connect = lambda: sloop.Sloop(sloop.Link(mido=fake, time_scale=0.02))


def write_wav(path, seconds=0.3, hz=220):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / 44100)))
                               for i in range(int(44100 * seconds))))
    return path


def test_encodings():
    for data in (b"", b"\x00", bytes(range(256)), bytes([0xFF] * 7), bytes([0x80] * 15)):
        assert sloop.unpack7(sloop.pack7(data)) == data
        assert all(b < 0x80 for b in sloop.pack7(data))
    assert sloop.u35(0x12345678) == [0x78, 0x2C, 0x51, 0x11, 0x01]
    assert sloop.project_format(fake_fm1.project(6)) == 6 and sloop.project_format(b"PER3") == 0
    assert sloop.firmware_at_least("SLOOP 2.6", (2, 5, 1)) and not sloop.firmware_at_least("SLOOP 2.5", (2, 5, 1))


def test_info():
    fake, dev = device()
    assert dev.version == "SLOOP 2.7.1" and dev.info["proto"] == 12 and dev.can_backup()
    assert dev.info["pcount"] == 71 and sloop.device_format(dev.info) == 6
    assert device(proto=5)[1].can_backup() is False


def test_backup_round_trip():
    fake, dev = device()
    original = dict(fake.objects)
    backup = dev.backup()
    assert backup["format"] == "sloop-backup" and backup["version"] == 1 and backup["firmware"] == "SLOOP 2.7.1"
    objects = sloop.objects_of(backup)
    assert objects == {i: original[i] for i in fake.listed()}
    json.dumps(backup)                                     # it serialises as SLOOP's editor expects
    # wipe the device, restore, and everything is back byte for byte
    for i in fake.objects:
        fake.objects[i] = b"" if i >= 2 else fake.objects[i]
    dev.restore(backup)
    assert all(fake.objects[i] == original[i] for i in original), "restore is not byte-identical"


def test_lost_frames_are_retried():
    fake, dev = device()
    fake.drop_every = 7                                    # every 7th request gets no answer
    backup = dev.backup()
    assert sloop.objects_of(backup)[0] == fake.objects[0]


def test_lost_replies_after_a_write():
    fake, dev = device()
    original = dict(fake.objects)
    backup = dev.backup()
    for i in (2, 5, 32):
        fake.objects[i] = b""
    fake.lose_reply_every = 3  # writes happen, but some replies never come back
    dev.restore(backup)
    fake.lose_reply_every = 0
    assert all(fake.objects[i] == original[i] for i in original), "restore with lost replies differs"


def test_refusals():
    fake, dev = device()
    backup = dev.backup()
    fake.playing = True
    try:
        dev.restore(backup)
        raise AssertionError("restore while playing")
    except sloop.SloopError as e:
        assert e.code == "stop"
    fake.playing = False
    newer = dict(backup, objects=[dict(o) for o in backup["objects"]])
    old_fake, old_dev = device(version="SLOOP 2.5", pcount=61, proto=10)
    try:
        old_dev.restore(newer)
        raise AssertionError("a 2.7 backup into 2.5")
    except sloop.SloopError as e:
        assert e.code == "newer"
    broken = json.loads(json.dumps(backup))
    broken["objects"][0]["crc"] ^= 1
    try:
        sloop.objects_of(broken)
        raise AssertionError("damaged backup accepted")
    except sloop.SloopError as e:
        assert e.code == "bad"


def test_projects():
    fake, dev = device()
    backup = dev.backup()
    assert set(sloop.projects_in(backup)) == {"working", "A", "D"}
    data = sloop.project_from(backup, "D")
    dev.put_project(data, "B")
    assert fake.objects[3] == fake.objects[5]
    shared = sloop.project_file(data, "My song", "SLOOP 2.7.1", "x:D")
    assert sloop.project_from(json.loads(json.dumps(shared))) == data
    try:
        sloop.project_from(backup, "C")
        raise AssertionError("empty project accepted")
    except sloop.SloopError:
        pass


def test_samples():
    fake, dev = device()
    wavs = [write_wav(TMP / "bass_C2.wav", 0.4, 65.4), write_wav(TMP / "lead_A4.wav", 0.2, 440)]
    n = dev.load_samples(2, "synths", wavs)
    obj = fake.objects[34]
    assert obj[:4] == b"FSMP" and obj[6] == 2 and obj[8:14] == b"SYNTHS" and len(obj) == 512 + n
    roots = [struct.unpack_from("<h", obj, 32 + z * 28 + 20)[0] // 16 for z in range(2)]
    assert roots == [36, 69], roots                         # C2 and A4 from the file names, sorted
    assert dev.sample_slots()[2]["name"] == "SYNTHS"


def test_library_jobs():
    fake = fake_fm1.FakeFM1()
    use(fake)
    library.job_backup(auto=True)
    state = library.read_state()
    assert state["state"] == "done" and state["job"] == "backup", state
    backups = list((TMP / "library" / "backups").glob("*_auto.json"))
    assert len(backups) == 1 and "sloop-2.7.1" in backups[0].name
    # restore makes its own backup first
    library.job_restore(str(backups[0]))
    assert library.read_state()["state"] == "done"
    assert list((TMP / "library" / "backups").glob("*_before-restore.json"))
    # a project from the backup, into C
    library.job_load_project(str(backups[0]), "A", "C")
    assert fake.objects[4] == fake.objects[2]
    saved = library.job_save_project(str(backups[0]), "D", "Night Drive")
    assert saved.name == "night-drive.json"
    library.job_load_project(str(saved), "-", "working")
    assert fake.objects[0] == fake.objects[5]
    # a sample
    library.job_load_sample(4, "drum", [write_wav(TMP / "hit.wav", 0.1)])
    assert fake.objects[35][8:12] == b"DRUM"
    # pruning keeps the newest 20 auto backups
    for k in range(25):
        (TMP / "library" / "backups" / f"2020-01-01_0000{k:02d}_x_auto.json").write_text("{}")
    library.prune_auto()
    assert len(list((TMP / "library" / "backups").glob("*_auto.json"))) == 20
    listing = library.listing()
    assert listing["projects"][0]["title"] == "Night Drive"


def write_recording(path, before=1.0, sound=10.0, hz=110.0):
    """a 48 kHz stereo recording: silence, a sawtooth-like synth note, silence"""
    import numpy as np
    t = np.arange(int(48000 * sound)) / 48000
    note = sum(np.sin(2 * math.pi * hz * k * t) / k for k in range(1, 6)) * 8000
    x = np.concatenate([np.zeros(int(48000 * before)), note, np.zeros(48000)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.repeat(x.astype("<i2"), 2).tobytes())
    return path


def test_resample_a_recording():
    rec = write_recording(library.folder("recordings") / "2026-10-22_001217_fm1.wav")
    out = library.sample_from_recording(rec)
    assert out.parent.name == "samples" and out.name == "rec-001217_A2.wav", out.name   # 110 Hz: A2
    with wave.open(str(out)) as w:
        assert w.getframerate() == 22050 and w.getnchannels() == 1
        seconds = w.getnframes() / 22050
        first = struct.unpack("<200h", w.readframes(200))
    assert 7.2 < seconds <= library.SLOT_SECONDS + 0.01, seconds                  # cut to fit a slot
    assert max(abs(v) for v in first) > 1000                                    # starts at the sound
    silent = library.folder("recordings") / "2026-10-22_000000_fm1.wav"
    write_recording(silent, sound=0.0)
    try:
        library.sample_from_recording(silent)
        raise AssertionError("a silent recording was accepted")
    except sloop.SloopError as e:
        assert "silence" in str(e)
    # the whole job, into USR2 of the pretend FM-1
    fake = fake_fm1.FakeFM1()
    use(fake)
    library.job_load_sample(2, "REC0012", [], recording=str(rec))
    assert library.read_state()["state"] == "done", library.read_state()
    assert fake.objects[33][8:15] == b"REC0012"


def test_timed_backups_skip_unchanged():
    fake = fake_fm1.FakeFM1()
    use(fake)
    backups = TMP / "library" / "backups"
    library.job_backup(auto=True)
    count = len(list(backups.glob("*_auto.json")))
    library.job_backup(auto=True)                          # nothing changed: no new file
    state = library.read_state()
    assert state["result"].get("unchanged") and "No changes" in state["message"], state
    assert len(list(backups.glob("*_auto.json"))) == count
    newest = library.newest_auto()
    fake.objects[2] = fake_fm1.project(6, 99)             # project A edited on the FM-1
    import time
    time.sleep(1.1)                                        # (file names carry the second)
    library.job_backup(auto=True)
    assert library.newest_auto() != newest and library.same_content(library.newest_auto(), {
        "format": "sloop-backup", "version": 1,
        "objects": sloop.load_json(library.newest_auto())["objects"]})
    library.job_backup()                                   # a manual backup is always saved
    assert library.read_state()["result"].get("unchanged") is None


def test_sloop_and_clipper_dont_mix():
    assert sloop.family("FELUCCA SLOOP 2.8") == "sloop" and sloop.family("clipper-1.1-experimental.fwsc") == "clipper"
    assert sloop.family("FELUCCA 1.6") is None
    sloop_backup = device()[1].backup()
    clipper, dev = device(version="FELUCCA CLIPPER 1.1", pcount=126, proto=14)
    before = dict(clipper.objects)
    for attempt in (lambda: dev.restore(sloop_backup),
                    lambda: dev.put_project(sloop.project_from(sloop_backup, "A"), "working",
                                            firmware=sloop_backup["firmware"]),
                    lambda: dev.load_samples(0, "x", [write_wav(TMP / "c.wav", 0.1)])):
        try:
            attempt()
            raise AssertionError("SLOOP data went onto Clipper")
        except sloop.SloopError as e:
            assert e.code == "family", e
    assert clipper.objects == before                       # nothing was written
    dev.restore(dev.backup())                              # Clipper's own backup: fine
    # the library jobs refuse before they make a safety backup
    use(clipper)
    path = library.write_backup(sloop_backup, "manual")
    count = len(list((TMP / "library" / "backups").glob("*.json")))
    assert library.main(["restore", str(path)]) == 1 and "Clipper" in library.read_state()["message"]
    assert len(list((TMP / "library" / "backups").glob("*.json"))) == count


def test_newest_firmware_release():
    releases = [{"tag_name": "v1.2", "draft": True, "assets": [{"name": "c-1.2.fwsc"}]},
                {"tag_name": "v1.1-experimental", "prerelease": True,
                 "assets": [{"name": "demo.wav"}, {"name": "clipper-1.1-experimental.fwsc"}]},
                {"tag_name": "v1.0", "assets": [{"name": "clipper-1.0.fwsc"}]}]
    release, asset = library.newest_firmware(releases)
    assert release["tag_name"] == "v1.1-experimental" and asset["name"] == "clipper-1.1-experimental.fwsc"
    assert library.newest_firmware([{"assets": [{"name": "x.wav"}]}]) == (None, None)
    assert set(library.FIRMWARES) == {"sloop", "clipper"}


def test_job_errors_are_reported():
    fake = fake_fm1.FakeFM1()
    use(fake)
    fake.playing = True
    backup = next((TMP / "library" / "backups").glob("*_manual.json"), None) or \
        library.write_backup(sloop.Sloop(sloop.Link(mido=fake, time_scale=0.02)).backup(), "manual")
    assert library.main(["restore", str(backup)]) == 1
    state = library.read_state()
    assert state["state"] == "error" and "stop the song" in state["message"], state


def test_sd_import():
    sd = TMP / "sd" / "samples"
    sd.mkdir(parents=True)
    write_wav(sd / "pad_C3.wav", 0.1)
    (sd / "notes.txt").write_text("not a sample")
    assert library.import_sd() == ["samples/pad_C3.wav"]
    assert library.import_sd() == []                       # already there


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    tests.sort(key=lambda t: t[1].__code__.co_firstlineno)
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
