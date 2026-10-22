"""Tests for the web page's server (web.py) against a temporary library. No hardware needed.

  python tests/test_web.py [--serve]      (--serve keeps it running on http://localhost:8765 to look at)
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fm1-bt-bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
TMP = Path(tempfile.mkdtemp(prefix="fm1-web-"))
os.environ.update(FM1_LIBRARY=str(TMP / "library"), XDG_RUNTIME_DIR=str(TMP / "run"), FM1_SD_LIBRARY=str(TMP / "sd"))

import fake_fm1  # noqa: E402
import library  # noqa: E402
import sloop  # noqa: E402
import web  # noqa: E402
from test_library import write_wav  # noqa: E402

PORT = 8765
BASE = f"http://localhost:{PORT}"


def call(path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(BASE + path, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, reply.read(), reply.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def jcall(path, body=None, raw=None):
    status, data, _ = call(path, body, raw)
    return status, json.loads(data)


def setup():
    fake = fake_fm1.FakeFM1()
    library.write_backup(sloop.Sloop(sloop.Link(mido=fake, time_scale=0.02)).backup(), "auto")
    server = web.ThreadingHTTPServer(("localhost", PORT), web.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_page_and_listing():
    status, page, headers = call("/")
    assert status == 200 and b"FM-1 Bridge" in page and headers["Content-Type"].startswith("text/html")
    status, lib = jcall("/api/library")
    assert status == 200 and len(lib["backups"]) == 1 and lib["backups"][0]["projects"] == ["A", "D"]


def test_uploads():
    wav = write_wav(TMP / "bass_C2.wav", 0.1).read_bytes()
    assert jcall("/api/upload?folder=samples&name=bass_C2.wav", raw=wav)[1]["ok"]
    status, reply = jcall("/api/upload?folder=samples&name=noise.wav", raw=b"RIFF....not audio")
    assert status == 400 and not reply["ok"]
    assert not (library.folder("samples") / "noise.wav").exists()
    assert not list(library.folder("samples").glob(".*.part"))        # no leftovers
    # names and folders that would escape the library or mix kinds up
    for folder, name in (("samples", "../evil.wav"), ("samples", "x.sh"), ("etc", "passwd.json"),
                         ("firmware", "a.wav"), ("samples", ".hidden.wav")):
        status, reply = jcall(f"/api/upload?folder={folder}&name={name}", raw=wav)
        assert status == 400, (folder, name)
    status, reply = jcall("/api/upload?folder=firmware&name=bad.fwsc", raw=b"not a package")
    assert status == 400 and "bad.fwsc" in reply["message"]


def test_downloads():
    name = library.listing()["backups"][0]["name"]
    status, data, headers = call(f"/files/backups/{name}")
    assert status == 200 and json.loads(data)["format"] == "sloop-backup" and "attachment" in headers["Content-Disposition"]
    assert call("/files/backups/..%2F..%2Fx.json")[0] == 404
    status, data, _ = call(f"/api/project?backup={name}&source=D")
    project = json.loads(data)
    assert status == 200 and project["format"] == "sloop-project" and sloop.project_from(project)
    assert call(f"/api/project?backup={name}&source=C")[0] == 400       # empty


def test_recordings():
    data = b"RIFF" + bytes(40) + bytes(192000 * 3)                 # a 3-second "recording"
    (library.folder("recordings") / "2026-10-22_101500_fm1.wav").write_bytes(data)
    status, lib = jcall("/api/library")
    rec = lib["recordings"][0]
    assert rec["name"] == "2026-10-22_101500_fm1.wav" and rec["seconds"] == 3
    status, body, headers = call("/files/recordings/2026-10-22_101500_fm1.wav")
    assert status == 200 and body == data and headers["Content-Type"] == "audio/wav"
    assert int(headers["Content-Length"]) == len(data)
    status, reply = jcall("/api/upload?folder=recordings&name=x.wav", raw=data)
    assert status == 400                                          # made on the box only
    assert jcall("/api/ask", {"cmd": "load-recording", "file": rec["name"], "slot": 3})[1]["ok"]
    request = library.take_request()
    assert request["args"][:3] == ["load-recording", "3", "REC1015"] and request["title"] == "Load recording?"
    assert jcall("/api/ask", {"cmd": "load-recording", "file": "../x.wav", "slot": 3})[0] == 400
    assert jcall("/api/delete", {"folder": "recordings", "name": rec["name"]})[1]["ok"]


def test_writes_wait_for_the_box():
    name = library.listing()["backups"][0]["name"]
    status, reply = jcall("/api/ask", {"cmd": "restore", "file": name})
    assert status == 200 and "box" in reply["message"]
    request = library.take_request()
    assert request["args"] == ["restore", str(library.folder("backups") / name)] and request["title"] == "Restore?"
    assert library.take_request() is None                               # taken once
    jcall("/api/ask", {"cmd": "load-project", "folder": "backups", "file": name, "source": "A", "target": "C"})
    assert library.take_request()["args"][2:] == ["A", "C"]
    jcall("/api/ask", {"cmd": "load-sample", "files": ["bass_C2.wav"], "slot": 2, "name": "bass"})
    assert library.take_request()["args"][:3] == ["load-sample", "2", "bass"]
    assert jcall("/api/ask", {"cmd": "load-sample", "files": ["../x.wav"], "slot": 2})[0] == 400
    assert jcall("/api/ask", {"cmd": "flash", "file": "nope.fwsc"})[0] == 400
    assert jcall("/api/ask", {"cmd": "rm -rf"})[0] == 400


def test_run_and_delete():
    (TMP / "sd" / "samples").mkdir(parents=True, exist_ok=True)
    write_wav(TMP / "sd" / "samples" / "pad_C3.wav", 0.1)
    status, reply = jcall("/api/run", {"cmd": "import-sd"})
    assert reply["ok"] and "1 new" in reply["message"]
    assert jcall("/api/run", {"cmd": "reboot"})[0] == 400
    assert jcall("/api/delete", {"folder": "samples", "name": "pad_C3.wav"})[1]["ok"]
    assert not (library.folder("samples") / "pad_C3.wav").exists()
    assert jcall("/api/delete", {"folder": "samples", "name": "../../x.wav"})[0] == 404
    status, state = jcall("/api/state")
    assert status == 200 and "waiting" in state


if __name__ == "__main__":
    server = setup()
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")
    if "--serve" in sys.argv:
        write_wav(library.folder("samples") / "lead_A4.wav", 0.2)
        print(f"serving {BASE} (Ctrl-C to stop)")
        threading.Event().wait()
