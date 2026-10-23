#!/usr/bin/env python3
"""The FM-1 bridge's web page, http://fm1bridge.local while Wi-Fi is on.

Four tabs: Status (the box's live status, and the settings you change while playing: metronome, tempo,
clock, arp, chord, volume, audio rate, Start/Stop, recording), MIDI (the routes as a grid, each route's
settings, the four presets), Library (backups, projects, samples, firmware) and Recordings (listen in the
browser, download, resample into a slot, delete).

Settings go to the box as commands (library.queue_command; the box applies them like the joystick).
Anything that writes to the FM-1's data (restore, loads, firmware) is handed to the box and waits for a
confirmation there (hold the joystick press), never just a click on the network.

Python's standard library only. Port: $FM1_WEB_PORT (default 80; install.sh lets the user's services use it).
"""
import json
import os
import shutil
import sys
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blemidi  # noqa: E402
import library  # noqa: E402
import sampler  # noqa: E402
import sloop  # noqa: E402

MAX_UPLOAD = 50 * 1024 * 1024
EDITABLE = ("recordings", "samples")    # what the sample editor opens
TARGETS = {"working": "the working project", "A": "project A", "B": "project B", "C": "project C",
           "D": "project D"}


def safe_path(folder, name):
    """a file in the library, or None: one of its folders, a plain name, the right kind of file"""
    if folder not in library.FOLDERS or not isinstance(name, str):
        return None
    base = Path(name).name
    if base != name or base.startswith(".") or Path(base).suffix.lower() not in library.SUFFIXES[folder]:
        return None
    return library.folder(folder) / base


def check_command(c):
    """a command for the box from the page -> the command to queue; ValueError says what's wrong"""
    if not isinstance(c, dict):
        raise ValueError("not a command")
    cmd = c.get("cmd")
    if cmd == "set":
        key, value = c.get("key"), c.get("value")
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        ok = {"metronome": isinstance(value, bool), "arp_on": isinstance(value, bool),
              "chord_on": isinstance(value, bool), "bpm": number and 30 <= value <= 300,
              "clock_mode": value in ("follow", "box"), "click": value in ("low", "mid", "high"),
              "audio_rate": value in (44100, 48000),
              "volume": number and int(value) == value and 0 <= value <= 100}.get(key)
        if not ok:
            raise ValueError(f"bad setting {key!r}")
        return {"cmd": cmd, "key": key, "value": value}
    if cmd in ("transport", "record"):
        if c.get("value") not in ("start", "stop"):
            raise ValueError("start or stop")
        return {"cmd": cmd, "value": c["value"]}
    if cmd == "routes":
        return {"cmd": cmd, "routes": blemidi.clean_routes(c.get("routes"))}
    if cmd == "preset":
        name = c.get("name")
        if c.get("slot") not in ("1", "2", "3", "4") or c.get("action") not in ("load", "save", "clear") \
                or (name is not None and (not isinstance(name, str) or len(name) > 16)):
            raise ValueError("bad preset")
        return {"cmd": cmd, "slot": c["slot"], "action": c["action"], "name": name}
    raise ValueError(f"unknown command {cmd!r}")


def byte_range(header, size):
    """an HTTP Range header ("bytes=START-END", one range) -> (start, end inclusive), or None"""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].partition("-")
    try:
        if first:
            start, end = int(first), int(last) if last else size - 1
        else:                                  # "bytes=-N": the last N bytes
            start, end = max(0, size - int(last)), size - 1
    except ValueError:
        return None
    return (start, min(end, size - 1)) if 0 <= start <= end and start < size else None


def check_upload(folder, path):
    """the uploaded file is what it claims to be; -> an error message, or None"""
    try:
        if folder == "firmware":
            import fm1_install
            fm1_install.load_package(str(path), False)
        elif folder == "backups":
            sloop.objects_of(sloop.load_json(path))
        elif folder == "projects":
            sloop.project_from(sloop.load_json(path))
        elif folder == "samples":
            import sampleio
            rate, samples = sampleio.read_any_wav(path)
            if not samples:
                return "no audio in that WAV"
    except SystemExit as e:                     # fm1_install reports a bad package this way
        return str(e) or "not a firmware package"
    except Exception as e:
        return str(e)[:120] or "not a valid file"
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "fm1-bridge"

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit=1 << 20):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("too large")
        return self.rfile.read(length)

    def fail(self, message, code=400):
        self.send(code, {"ok": False, "message": message})

    # ---- GET
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/":
            self.send(200, PAGE, "text/html")
        elif url.path == "/api/library":
            self.send(200, library.listing())
        elif url.path == "/api/status":
            status = library.read_status()
            self.send(200, status if status else {"offline": True})
        elif url.path == "/api/state":
            state = library.read_state() or {}
            if state.get("state") == "running" and not library.running():
                state["state"] = "error"                       # it died without saying
            state["waiting"] = library.REQUEST.exists()
            self.send(200, state)
        elif url.path.startswith("/files/"):
            _, _, folder, name = url.path.split("/", 3)
            path = safe_path(folder, urllib.parse.unquote(name))
            if not path or not path.is_file():
                return self.fail("no such file", 404)
            ctype = {"samples": "audio/wav", "recordings": "audio/wav",
                     "firmware": "application/octet-stream"}.get(folder, "application/json")
            self.send_file(path, ctype, inline=query.get("inline") == "1")
        elif url.path == "/api/edit-info":            # what the sample editor opens a take or sample with
            folder = query.get("folder", "")
            path = safe_path(folder, query.get("file", "")) if folder in EDITABLE else None
            if not path or not path.is_file():
                return self.fail("no such file", 404)
            try:
                self.send(200, dict(sampler.info(path), level=library.sample_peak() / 32768))
            except (OSError, ValueError, EOFError, wave.Error) as e:
                self.fail(str(e))
        elif url.path == "/api/project":
            path = safe_path("backups", query.get("backup", ""))
            source = query.get("source", "")
            if not path or not path.is_file() or source not in TARGETS:
                return self.fail("no such project", 404)
            try:
                backup = sloop.load_json(path)
                data = sloop.project_from(backup, source)
            except sloop.SloopError as e:
                return self.fail(str(e))
            name = f"{path.stem}_{source}"
            body = json.dumps(sloop.project_file(data, name, backup.get("firmware"), f"{path.name}:{source}"))
            self.send(200, body.encode(), "application/json",
                      {"Content-Disposition": f'attachment; filename="{name}.json"'})
        else:
            self.fail("not found", 404)

    # ---- POST
    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path == "/api/upload":
                return self.upload(query.get("folder", ""), query.get("name", ""))
            request = json.loads(self.body() or b"{}")
        except (ValueError, OSError) as e:
            return self.fail(str(e))
        if url.path == "/api/run":
            command = request.get("cmd")
            if command == "import-sd":
                try:
                    copied = library.import_sd()
                except OSError as e:
                    return self.fail(str(e))
                return self.send(200, {"ok": True, "message": f"{len(copied)} new file(s) from the SD card"})
            jobs = {"backup": ["backup"], "fetch-sloop": ["fetch", "sloop"], "fetch-clipper": ["fetch", "clipper"]}
            if command not in jobs:
                return self.fail("unknown command")
            error = library.spawn(jobs[command])
            return self.send(200, {"ok": not error, "message": error or "started"})
        if url.path == "/api/ask":
            return self.ask(request)
        if url.path == "/api/save-sample":
            return self.save_sample(request)
        if url.path == "/api/command":
            try:
                command = check_command(request)
            except (ValueError, TypeError, AttributeError) as e:   # whatever arrives: a 400, never a crash
                return self.fail(str(e)[:120] or "bad command")
            if not library.read_status():
                return self.fail("the box isn't answering", 503)
            library.queue_command(command)
            return self.send(200, {"ok": True, "message": "sent"})
        if url.path == "/api/delete":
            path = safe_path(request.get("folder"), request.get("name"))
            if not path or not path.is_file():
                return self.fail("no such file", 404)
            path.unlink()
            return self.send(200, {"ok": True, "message": f"deleted {path.name}"})
        self.fail("not found", 404)

    def send_file(self, path, ctype, inline=False):
        """a file in pieces (a long recording is bigger than the Zero W's memory would like); a Range request
        gets just that part, so the browser's audio player can start at once and skip around"""
        size = path.stat().st_size
        part = byte_range(self.headers.get("Range"), size)
        start, end = part or (0, size - 1)
        self.send_response(206 if part else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if part:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Disposition", f'{"inline" if inline else "attachment"}; filename="{path.name}"')
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):   # the player stopped or skipped
                    return
                left -= len(chunk)

    def upload(self, folder, name):
        path = safe_path(folder, name) if folder != "recordings" else None
        if not path:
            try:                              # read what was sent first: an unread upload resets the connection,
                self.body(MAX_UPLOAD)          # and the browser shows that instead of the reason
            except (ValueError, OSError):
                pass
            return self.fail("that kind of file doesn't go there")
        data = self.body(MAX_UPLOAD)
        tmp = path.with_name("." + path.name + ".part")
        tmp.write_bytes(data)
        error = check_upload(folder, tmp)
        if error:
            tmp.unlink(missing_ok=True)
            return self.fail(f"{name}: {error}")
        tmp.replace(path)
        self.send(200, {"ok": True, "message": f"uploaded {path.name}"})

    def save_sample(self, r):
        """the sample editor's Save: a recording or a sample, edited, into the samples folder as a new file
        (nothing on the FM-1 changes, so no confirmation on the box; Load sample does that)"""
        folder = r.get("folder", "recordings")
        path = safe_path(folder, r.get("file")) if folder in EDITABLE else None
        if not path or not path.is_file():
            return self.fail("no such file")
        try:
            start, end = float(r["start"]), float(r["end"])
            if not 0 <= start < end:
                raise ValueError("the start must come before the end")
            fades = {k: min(2.0, max(0.0, float(r.get(k) or 0))) for k in ("fade_in", "fade_out")}
            note = sampler.parse_note(r.get("note"))
            name = str(r.get("name") or sampler.default_name(path))
            out = sampler.save(path, name, note, start, end, normalize=bool(r.get("normalize", True)),
                               reverse=bool(r.get("reverse")), **fades)
        except (KeyError, TypeError, ValueError, OSError, EOFError, wave.Error) as e:
            return self.fail(str(e)[:120] or "bad edit")
        self.send(200, {"ok": True, "message": f"Saved {out.name} to the samples", "name": out.name})

    def ask(self, r):
        """a write to the FM-1: handed to the box, which asks for a hold-press there"""
        command = r.get("cmd")
        if library.running():
            return self.fail("a library job is running")
        if command == "restore":
            path = safe_path("backups", r.get("file"))
            if not path or not path.is_file():
                return self.fail("no such backup")
            library.ask_box(["restore", str(path)], "Restore?", [path.name[:16], "replaces the FM-1's data"])
        elif command == "load-project":
            folder, target, source = r.get("folder"), r.get("target"), r.get("source") or "-"
            path = safe_path(folder if folder in ("backups", "projects") else "", r.get("file"))
            if not path or not path.is_file() or target not in TARGETS:
                return self.fail("pick a project and where it goes")
            library.ask_box(["load-project", str(path), source, target], "Load project?",
                            [f"{path.stem[:14]} {source if source != '-' else ''}".strip(), f"into {TARGETS[target]}"])
        elif command == "load-sample":
            paths = [safe_path("samples", n) for n in r.get("files") or []]
            slot, name = r.get("slot"), (r.get("name") or "")[:8]
            if not paths or None in paths or not all(p.is_file() for p in paths) or slot not in (1, 2, 3, 4):
                return self.fail("pick WAV files and a slot")
            name = name or paths[0].stem[:8]
            library.ask_box(["load-sample", str(slot), name, *map(str, paths)], "Load sample?",
                            [f"{name} ({len(paths)} file{'s' if len(paths) > 1 else ''})", f"into USR{slot}"])
        elif command == "flash":
            path = safe_path("firmware", r.get("file"))
            if not path or not path.is_file():
                return self.fail("no such firmware")
            warning = "Experimental: see its README" if sloop.family(path.name) == "clipper" else "Keep the FM-1 plugged in"
            library.ask_box(["flash", str(path)], "Install?", [path.stem[:20], warning])
        else:
            return self.fail("unknown command")
        self.send(200, {"ok": True, "message": "Confirm on the box: hold the joystick press for 2 seconds"})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1115">
<title>FM-1 Bridge</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#16181d;--dim:#626875;--line:#e2e5ea;--soft:#eef0f3;--accent:#1677c9;--on:#1d8a4a;--bad:#c23a32;--rec:#d6332b}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--dim:#9aa1ad;--line:#2a2f38;--soft:#20242c;--accent:#5fb0ff;--on:#4cc27e;--bad:#ff6b61;--rec:#ff5a52}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding-bottom:calc(72px + env(safe-area-inset-bottom))}
header{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;align-items:center;gap:10px;padding:12px 16px 8px;max-width:820px;margin:0 auto}
header h1{font-size:20px;margin:0;flex:1}
.pill{font-size:13px;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--soft);color:var(--dim)}
.pill.live{background:var(--on);color:#fff}.pill.rec{background:var(--rec);color:#fff}.pill.off{background:var(--bad);color:#fff}
main{max-width:820px;margin:0 auto;padding:0 16px 16px}
.tab{display:none}.tab.shown{display:block}
nav{position:fixed;bottom:0;left:0;right:0;z-index:6;display:flex;background:var(--card);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;border:0;background:none;color:var(--dim);padding:10px 4px 9px;font:600 13px system-ui,sans-serif;border-radius:0}
nav button.shown{color:var(--accent)}nav svg{display:block;margin:0 auto 2px;width:24px;height:24px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 12px}
h2{font-size:15px;margin:0 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}.grid section{margin:0}
.line{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:44px;border-top:1px solid var(--line);padding:6px 0}
.line:first-of-type{border-top:0}.line .k{color:var(--dim)}.line .v{font-weight:600;text-align:right}
button,select,input[type=text],input[type=number]{font:inherit;border-radius:10px;border:1px solid var(--line);background:var(--soft);color:var(--ink);padding:9px 14px;min-height:42px}
button{cursor:pointer;font-weight:600}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.danger{color:var(--bad)}button:disabled{opacity:.45;cursor:default}
input[type=number]{width:76px;text-align:center}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.seg button{border:0;border-radius:0;background:var(--soft);min-width:72px}.seg button.on{background:var(--accent);color:#fff}
.sw{position:relative;width:52px;height:30px;flex:none}.sw input{opacity:0;width:0;height:0}
.sw span{position:absolute;inset:0;background:var(--line);border-radius:30px;transition:.2s}
.sw span:before{content:"";position:absolute;width:24px;height:24px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 2px #0004}
.sw input:checked+span{background:var(--on)}.sw input:checked+span:before{transform:translateX(22px)}
.big{font-size:44px;font-weight:700;line-height:1.1;letter-spacing:-.02em}
.dim{color:var(--dim)}.small{font-size:13px}
input[type=range]{width:100%;accent-color:var(--accent);height:30px}
.recbtn{width:100%;font-size:18px;padding:14px;border-radius:12px}.recbtn.on{background:var(--rec);border-color:var(--rec);color:#fff}
.item{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;justify-content:space-between;padding:11px 0;border-top:1px solid var(--line)}
.item:first-of-type{border-top:0}.name{font-weight:600;word-break:break-all}.meta{color:var(--dim);font-size:13px}
.empty{color:var(--dim)}.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:8px}
.bar div{height:100%;background:var(--accent);width:0;transition:width .3s}
audio{width:100%;height:40px;margin-top:6px}
label.file{display:inline-block}label.file input{display:none}
table.mx{border-collapse:separate;border-spacing:8px;margin:-8px}table.mx th{font-size:13px;color:var(--dim);font-weight:600;text-align:left}
table.mx td button{width:100%;min-width:92px}table.mx td button.on{background:var(--on);border-color:var(--on);color:#fff}
table.mx td button.sel{outline:3px solid var(--accent);outline-offset:1px}
.chgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.chgrid label{font-size:12px;color:var(--dim)}
.chgrid select{width:100%;padding:6px;min-height:36px}
#toast{position:fixed;left:50%;bottom:calc(84px + env(safe-area-inset-bottom));transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:9px 16px;border-radius:999px;font-size:14px;opacity:0;transition:opacity .25s;pointer-events:none;max-width:90%;text-align:center}
#toast.shown{opacity:.94}
.banner{border-radius:12px;padding:10px 12px;margin:0 0 12px;background:var(--soft)}
#sed-wave{width:100%;height:150px;display:block;border-radius:10px;background:var(--soft);touch-action:none;cursor:ew-resize}
.sedgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px 10px;margin:10px 0}.sedgrid label{font-size:13px;color:var(--dim);display:flex;flex-direction:column;gap:3px}
.sedgrid input,.sedgrid select{width:100%;box-sizing:border-box}.warn{color:var(--bad)}
</style></head><body>
<header><h1>FM-1 Bridge</h1><span id="recpill" class="pill rec" hidden>REC</span><span id="pill" class="pill">…</span></header>
<main>

<!-- ======================= STATUS ======================= -->
<div class="tab" id="tab-status">
<div id="offline" class="banner" hidden>The box isn't answering: it may be starting, or its bridge stopped. Settings will work again when it's back.</div>
<div class="grid">
<section><h2>Audio</h2>
  <div class="line" id="s-batline" hidden><span class="k">Battery</span><span class="v" id="s-bat">–</span></div>
  <div class="line"><span class="k">FM-1</span><span class="v" id="s-fm1">–</span></div>
  <div class="line"><span class="k">Speaker</span><span class="v" id="s-spk">–</span></div>
  <div class="line" style="display:block"><div class="row" style="justify-content:space-between"><span class="k">Volume</span><b id="s-volv">–</b></div>
    <input type="range" id="s-vol" min="0" max="100" step="2"></div>
  <div class="line"><span class="k">Audio rate</span><div class="seg" id="s-rate"><button data-v="44100">44.1</button><button data-v="48000">48 kHz</button></div></div>
</section>
<section><h2>Clock</h2>
  <div class="row" style="justify-content:space-between;align-items:flex-end"><div><div class="big" id="s-bpm">–</div><div class="dim small" id="s-src"></div></div>
    <div class="seg" id="s-mode"><button data-v="follow">Follow</button><button data-v="box">Box</button></div></div>
  <div class="line"><span class="k">Box tempo</span><div class="row"><button onclick="bump(-1)">−</button><input type="number" id="s-tempo" min="30" max="300"><button onclick="bump(1)">+</button></div></div>
  <div class="line"><span class="k">Transport</span><div class="row"><button class="primary" onclick="cmd({cmd:'transport',value:'start'})">▶ Start</button><button onclick="cmd({cmd:'transport',value:'stop'})">■ Stop</button></div></div>
  <div class="line"><span class="k">Metronome</span><label class="sw"><input type="checkbox" id="s-metro"><span></span></label></div>
  <div class="line"><span class="k">Click level</span><div class="seg" id="s-click"><button data-v="low">Low</button><button data-v="mid">Mid</button><button data-v="high">High</button></div></div>
</section>
<section><h2>Keyboard</h2>
  <div class="line"><span><span class="k">Arp</span> <span class="dim small" id="s-arpd"></span></span><label class="sw"><input type="checkbox" id="s-arp"><span></span></label></div>
  <div class="line"><span><span class="k">Chord</span> <span class="dim small" id="s-chordd"></span></span><label class="sw"><input type="checkbox" id="s-chord"><span></span></label></div>
  <div class="line"><span class="k">MIDI device</span><span class="v" id="s-midi">–</span></div>
  <p class="dim small" style="margin:6px 0 0">Pattern, rate and chord type: MIDI tab, Bluetooth → FM-1.</p>
</section>
<section><h2>Record</h2>
  <button class="recbtn" id="s-rec" onclick="toggleRec()">● Record</button>
  <p class="dim small" style="margin:8px 0 0">Records the FM-1 on the box (a WAV at the FM-1's own rate). Takes are in the Recordings tab.</p>
</section>
</div>
</div>

<!-- ======================= MIDI ======================= -->
<div class="tab" id="tab-midi">
<section><h2>Routes</h2>
  <p class="dim small" style="margin:-4px 0 10px">Green: the route passes MIDI. Tap to switch it, then edit it below.</p>
  <table class="mx" id="mx"></table>
</section>
<section id="editor"><h2 id="ed-title">Route</h2><div id="ed"></div><p class="dim small" id="ed-saved" style="margin:8px 0 0"></p></section>
<section><h2>Presets</h2><div id="presets"></div></section>
</div>

<!-- ======================= LIBRARY ======================= -->
<div class="tab" id="tab-library">
<section><h2>Now</h2><div id="job" class="meta">Nothing running.</div><div class="bar" id="barwrap" hidden><div id="bar"></div></div>
<div class="row" style="margin-top:10px"><button class="primary" onclick="run('backup')">Back up now</button>
<button onclick="run('fetch-sloop')">Get SLOOP update</button><button onclick="run('fetch-clipper')" title="isod89's synthesizer firmware, experimental">Get Clipper (exp.)</button><button onclick="run('import-sd')">Import SD card</button></div></section>

<section><h2>Backups</h2><div id="backups"></div>
<div class="row" style="margin-top:8px"><label class="file"><input type="file" accept=".json" onchange="upload('backups',this)"><button onclick="this.previousElementSibling.click()">Upload a backup…</button></label></div></section>

<section><h2>Projects</h2><p class="meta" style="margin:0 0 6px">Load a project into the FM-1, from a project file or from any backup.</p>
<div id="projects"></div>
<div class="row" style="margin-top:8px"><label class="file"><input type="file" accept=".json" onchange="upload('projects',this)"><button onclick="this.previousElementSibling.click()">Upload a project…</button></label></div></section>

<section><h2>Samples</h2><p class="meta" style="margin:0 0 6px">Tick one or more WAVs (each becomes a key zone, root note from the name, e.g. <i>bass_C2.wav</i>), then load them into a slot. A slot holds about 7 seconds.</p>
<div id="samples"></div>
<div class="row" style="margin-top:8px"><label class="file"><input type="file" accept=".wav" multiple onchange="upload('samples',this)"><button onclick="this.previousElementSibling.click()">Upload WAVs…</button></label>
<input type="text" id="sname" placeholder="name (8 letters)" maxlength="8" size="12"><select id="sslot"><option value="1">USR1</option><option value="2">USR2</option><option value="3">USR3</option><option value="4">USR4</option></select>
<button class="primary" onclick="loadSamples()">Load ticked into slot</button></div></section>

<section><h2>Firmware</h2><p class="meta" style="margin:0 0 6px">SLOOP (groovebox) and Clipper (synthesizer, experimental) are both isod89's. Backups, projects and samples belong to the firmware they came from: a SLOOP backup can't be restored onto Clipper, and Clipper has wavetables instead of sample slots.</p><div id="firmware"></div>
<div class="row" style="margin-top:8px"><label class="file"><input type="file" accept=".fwsc" onchange="upload('firmware',this)"><button onclick="this.previousElementSibling.click()">Upload a .fwsc…</button></label></div></section>
<p class="meta">Anything that writes to the FM-1 asks for a confirmation on the box: hold the joystick press for 2 seconds. Restores, projects and firmware back the FM-1 up first; a sample only replaces its slot.</p>
</div>

<!-- ======================= RECORDINGS ======================= -->
<div class="tab" id="tab-recordings">
<section><button class="recbtn" id="r-rec" onclick="toggleRec()">● Record</button></section>
<section><h2>Takes</h2><p class="meta" style="margin:-4px 0 6px">Listen here, download, or <i>edit as sample</i>: trim it, fade it, set its note, and save it to the samples.</p>
<div id="recordings"></div></section>
</div>

<!-- ======================= SAMPLE EDITOR ======================= -->
<div class="tab" id="tab-editor">
<section><h2>Edit</h2><p class="meta" style="margin:-4px 0 8px">Make a sample from a recording, or edit one that's already in the samples. Saving always makes a new file.</p>
  <div class="row"><select id="sed-pick" style="flex:1;min-width:0"></select><button class="primary" onclick="sedPicked()">Open</button></div></section>
<section id="sed" hidden><h2>Sample editor <span class="dim small" id="sed-take"></span></h2>
  <canvas id="sed-wave"></canvas>
  <div class="row" style="margin-top:6px"><span class="small dim">Zoom</span><input type="range" id="sed-zoom" min="1" max="40" value="1" style="flex:1"></div>
  <div class="sedgrid">
    <label>Start (s)<input type="number" id="sed-start" step="0.01" min="0"></label>
    <label>End (s)<input type="number" id="sed-end" step="0.01" min="0"></label>
    <label>Fade in (ms)<input type="number" id="sed-fi" step="10" min="0" max="2000" value="0"></label>
    <label>Fade out (ms)<input type="number" id="sed-fo" step="10" min="0" max="2000" value="10"></label>
    <label>Root note<select id="sed-note"></select></label>
    <label>Name<input type="text" id="sed-name" maxlength="24"></label>
  </div>
  <div class="row"><label class="row small"><input type="checkbox" id="sed-norm" checked style="width:20px;height:20px"> Normalize to the Sample level</label>
    <label class="row small"><input type="checkbox" id="sed-rev" style="width:20px;height:20px"> Reverse</label></div>
  <p class="small" id="sed-len" style="margin:8px 0"></p>
  <div class="row"><button onclick="sedPlay()">▶ Play selection</button><button class="primary" onclick="sedSave()">Save to samples</button><button onclick="sedClose()">Close</button></div>
  <p class="dim small" style="margin:8px 0 0">Drag on the waveform to move the nearer marker. Saved samples go to Library → Samples, ready to load into a slot (on the box: Sloop → Load sample).</p>
</section>
</div>
</main>

<nav>
<button data-tab="status"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>Status</button>
<button data-tab="midi"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 5v8M12 5v8M16 5v8"/></svg>MIDI</button>
<button data-tab="library"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h6v16H4zM14 4l6 2-4 14-6-2z"/></svg>Library</button>
<button data-tab="editor"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2"/></svg>Editor</button>
<button data-tab="recordings"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>Recordings</button>
</nav>
<div id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const q = (s) => esc(JSON.stringify(s));            // a JS string literal inside an onclick attribute
const when = (t) => new Date(t * 1000).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"});
const fw = (s) => esc((s || "?").split(" ").pop());
const mins = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
let lib = {backups:[], projects:[], samples:[], firmware:[], recordings:[]};
let st = null;                                     // the box's status
let tab = "status";

// ---------------------------------------------------------------- basics
let toastTimer;
function toast(msg, ok = true) { const t = $("toast"); t.textContent = msg; t.style.color = ok ? "" : "var(--bad)"; t.classList.add("shown"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("shown"), 2600); }
async function post(url, body, quiet) {
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)}).catch(() => null);
  const j = r ? await r.json().catch(() => ({ok:false, message:r.statusText})) : {ok:false, message:"no connection"};
  if (!quiet || !j.ok) toast(j.message, j.ok);
  return j;
}
const cmd = (c, quiet = true) => post("/api/command", c, quiet).then((j) => { setTimeout(status, 450); return j; });
const set = (key, value) => cmd({cmd:"set", key, value});
function show(name) {
  tab = name; location.hash = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("shown", t.id === "tab-" + name));
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("shown", b.dataset.tab === name));
  if (name === "library" || name === "recordings" || name === "editor") refresh();
  if (name === "midi") { renderMatrix(); renderEditor(); renderPresets(); }
  status();
}
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => show(b.dataset.tab));
function seg(id, value, onpick) {                  // a segmented control: highlight and click handler
  const el = $(id);
  el.querySelectorAll("button").forEach((b) => { b.classList.toggle("on", String(value) === b.dataset.v); b.onclick = () => onpick(b.dataset.v); });
}

// ---------------------------------------------------------------- status tab
let volTimer, tempoTimer, editingUntil = 0;
function renderStatus() {
  const off = !st || st.offline;
  $("offline").hidden = !off;
  $("pill").textContent = off ? "offline" : st.live ? "LIVE" : "idle";
  $("pill").className = "pill " + (off ? "off" : st.live ? "live" : "");
  const rec = !off && st.recording != null;
  $("recpill").hidden = !rec; if (rec) $("recpill").textContent = "REC " + mins(st.recording);
  for (const id of ["s-rec", "r-rec"]) { $(id).classList.toggle("on", rec); $(id).textContent = rec ? `■ Stop recording  ${mins(st.recording)}` : "● Record"; $(id).disabled = off; }
  if (off) return;
  const s = st.settings;
  $("s-fm1").textContent = st.fm1 || "not found";
  const bat = !st.offline && st.battery;
  $("s-batline").hidden = !bat;
  if (bat) $("s-bat").textContent = `${bat.percent}%` + (bat.plugged ? " · charging" : "");
  $("s-spk").textContent = st.speaker || (st.connecting ? "connecting…" : (st.speaker_name ? st.speaker_name + " (off)" : "none"));
  if (Date.now() > editingUntil) { if (st.volume != null) $("s-vol").value = st.volume; $("s-tempo").value = s.bpm; }
  $("s-volv").textContent = st.volume != null ? st.volume + "%" : "–"; $("s-vol").disabled = st.volume == null;
  seg("s-rate", s.audio_rate, (v) => set("audio_rate", +v));
  const c = st.clock;
  $("s-bpm").textContent = c ? c.bpm : "–";
  $("s-src").textContent = c ? `${c.playing ? "▶ playing" : "stopped"} · ${c.source === "box" ? "box clock" : "from " + c.source}` : (s.clock_mode === "follow" ? "following: no clock coming in" : "");
  seg("s-mode", s.clock_mode, (v) => set("clock_mode", v));
  $("s-metro").checked = s.metronome; $("s-metro").onchange = (e) => set("metronome", e.target.checked);
  seg("s-click", s.click, (v) => set("click", v));
  $("s-arp").checked = s.arp_on; $("s-arp").onchange = (e) => set("arp_on", e.target.checked);
  $("s-chord").checked = s.chord_on; $("s-chord").onchange = (e) => set("chord_on", e.target.checked);
  $("s-arpd").textContent = `${s.arp} ${s.arp_rate}`; $("s-chordd").textContent = s.chord;
  $("s-midi").textContent = st.midi.error ? "Bluetooth error" : !st.midi.enabled ? "off" : (st.midi.peer || "waiting");
}
$("s-vol").oninput = (e) => { editingUntil = Date.now() + 2500; $("s-volv").textContent = e.target.value + "%"; clearTimeout(volTimer); volTimer = setTimeout(() => set("volume", +e.target.value), 250); };
$("s-tempo").onchange = (e) => { const v = Math.max(30, Math.min(300, Math.round(+e.target.value || 120))); e.target.value = v; set("bpm", v); };
function bump(d) { editingUntil = Date.now() + 2500; const el = $("s-tempo"); el.value = Math.max(30, Math.min(300, (+el.value || 120) + d)); clearTimeout(tempoTimer); tempoTimer = setTimeout(() => set("bpm", +el.value), 350); }
function toggleRec() { cmd({cmd:"record", value: st && st.recording != null ? "stop" : "start"}, false).then(() => setTimeout(refresh, 900)); }

// ---------------------------------------------------------------- MIDI tab
const SOURCES = {"BT":"Bluetooth", "FM-1":"FM-1", "Box":"Box clock"}, DESTS = ["FM-1", "BT"];
const CHORDS = ["oct","5th","maj","min","7","maj7","m7"], ARPS = ["up","down","updown","played","random"], RATES = ["1/4","1/8","1/8T","1/16"];
let routes = null, sel = "BT>FM-1", routesDirtyUntil = 0, saveTimer;
function renderMatrix() {
  if (!routes) { $("mx").innerHTML = `<tr><td class="dim">Waiting for the box…</td></tr>`; return; }
  $("mx").innerHTML = `<tr><th></th>${DESTS.map((d) => `<th>to ${d === "BT" ? "Bluetooth" : d}</th>`).join("")}</tr>` +
    Object.keys(SOURCES).map((s) => `<tr><th>${SOURCES[s]}</th>${DESTS.map((d) => {
      const key = `${s}>${d}`;
      if (s === d) return `<td class="dim" style="text-align:center">–</td>`;
      const r = routes[key];
      return `<td><button class="${r.on ? "on" : ""} ${key === sel ? "sel" : ""}" onclick="pick(${q(key)})">${r.on ? "on" : "off"}</button></td>`;
    }).join("")}</tr>`).join("");
}
function pick(key) {
  if (key === sel) { routes[key].on = !routes[key].on; saveRoutes(); }   // tapping the selected route switches it
  sel = key; renderMatrix(); renderEditor();
}
const opt = (list, v, label = (x) => x) => list.map((x) => `<option value="${esc(x)}" ${String(x) === String(v) ? "selected" : ""}>${esc(label(x))}</option>`).join("");
const sw = (field, on) => `<label class="sw"><input type="checkbox" ${on ? "checked" : ""} onchange="edit(${q(field)}, this.checked)"><span></span></label>`;
function renderEditor() {
  if (!routes) return;
  const r = routes[sel], [s, d] = sel.split(">");
  $("ed-title").textContent = `${SOURCES[s]} → ${d === "BT" ? "Bluetooth" : d}`;
  let h = `<div class="line"><span class="k">Route</span>${sw("on", r.on)}</div>`;
  if (s !== "Box") {
    const ch = r.channels, mode = ch === "same" ? "same" : Array.isArray(ch) ? "custom" : "all";
    h += `<div class="line"><span class="k">Channels</span><div class="row"><select onchange="chMode(this.value)">${opt(["same","all","custom"], mode, (x) => ({same:"same", all:"all to one", custom:"custom table"})[x])}</select>
      ${mode === "all" ? `<select onchange="edit('channels', +this.value)">${opt([...Array(16).keys()], ch, (x) => "ch " + (x + 1))}</select>` : ""}</div></div>`;
    if (mode === "custom") h += `<div class="chgrid" style="margin:4px 0 10px">${ch.map((o, i) => `<div><label>in ${i + 1}</label><select onchange="chSet(${i}, this.value)">${opt(["off", ...Array(16).keys()], o == null ? "off" : o, (x) => x === "off" ? "off" : "ch " + (x + 1))}</select></div>`).join("")}</div>`;
    h += `<div class="line"><span class="k">Transpose</span><div class="row"><button onclick="edit('transpose', Math.max(-24, routes[sel].transpose - 1))">−</button><b style="min-width:42px;text-align:center">${r.transpose > 0 ? "+" : ""}${r.transpose}</b><button onclick="edit('transpose', Math.min(24, routes[sel].transpose + 1))">+</button></div></div>
      <div class="line"><span class="k">Velocity</span><div class="row"><select onchange="edit('velocity', this.value)">${opt(["normal","soft","hard","fixed"], r.velocity)}</select>
      ${r.velocity === "fixed" ? `<input type="number" min="1" max="127" value="${r.fixed}" onchange="edit('fixed', Math.max(1, Math.min(127, Math.round(+this.value || 100))))">` : ""}</div></div>
      <div class="line"><span class="k">Drop clock</span>${sw("drop_clock", r.drop_clock)}</div>
      <div class="line"><span class="k">Drop aftertouch</span>${sw("drop_aftertouch", r.drop_aftertouch)}</div>
      <div class="line"><span class="k">Drop pitch-bend</span>${sw("drop_bend", r.drop_bend)}</div>
      <div class="line"><span class="k">Chord</span><div class="row"><select onchange="edit('chord', this.value)">${opt(CHORDS, r.chord)}</select>${sw("chord_on", r.chord_on)}</div></div>
      <div class="line"><span class="k">Arp</span><div class="row"><select onchange="edit('arp', this.value)">${opt(ARPS, r.arp)}</select>
        <select onchange="edit('arp_rate', this.value)">${opt(RATES, r.arp_rate)}</select>
        <select onchange="edit('arp_octaves', +this.value)">${opt([1,2,3], r.arp_octaves, (x) => x + " oct")}</select>${sw("arp_on", r.arp_on)}</div></div>`;
  } else h += `<p class="dim small">The box's clock and Start/Stop go here when it's on (Clock: Box).</p>`;
  $("ed").innerHTML = h;
}
function edit(field, value) { routes[sel][field] = value; saveRoutes(); renderMatrix(); renderEditor(); }
function chMode(m) { edit("channels", m === "same" ? "same" : m === "all" ? 0 : [...Array(16).keys()]); }
function chSet(i, v) { routes[sel].channels[i] = v === "off" ? null : +v; saveRoutes(); }
function saveRoutes() {
  routesDirtyUntil = Date.now() + 3000; shownRoutes = JSON.stringify(routes); $("ed-saved").textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => { const j = await cmd({cmd:"routes", routes}); $("ed-saved").textContent = j.ok ? "Saved on the box." : j.message; }, 300);
}
function renderPresets() {
  const p = (st && st.presets) || {};
  $("presets").innerHTML = ["1","2","3","4"].map((slot) => { const x = p[slot];
    return `<div class="item"><div style="flex:1;min-width:180px"><input type="text" id="pn${slot}" maxlength="16" placeholder="Preset ${slot}" value="${esc(x && x.name || "")}" style="width:100%">
      <div class="meta">${x ? "saved " + when(x.saved) : "empty"}</div></div>
      <div class="row">${x ? `<button class="primary" onclick="preset(${q(slot)}, 'load')">Load</button>` : ""}<button onclick="preset(${q(slot)}, 'save')">Save here</button>${x ? `<button class="danger" onclick="preset(${q(slot)}, 'clear')">Clear</button>` : ""}</div></div>`; }).join("");
}
function preset(slot, action) {
  if (action === "clear" && !confirm(`Clear preset ${slot}?`)) return;
  const name = $("pn" + slot).value.trim() || `preset ${slot}`;
  cmd({cmd:"preset", slot, action, name: action === "save" ? $("pn" + slot).value.trim() : null}).then((j) => {
    routesDirtyUntil = 0; shownRoutes = ""; shownPresets = "";
    if (j.ok) toast({load: `Loaded ${name}`, save: `Saved as ${name}`, clear: `Preset ${slot} cleared`}[action]);
  });
}

// ---------------------------------------------------------------- library and recordings
const run = (c) => post("/api/run", {cmd:c}).then(() => { refresh(); setTimeout(update, 300); });
const ask = (body) => post("/api/ask", body).then(() => setTimeout(update, 300));
async function del(folder, name) { if (confirm(`Delete ${name}?`)) { await post("/api/delete", {folder, name}); refresh(); } }
async function upload(folder, input) {
  for (const f of input.files) {
    toast(`Uploading ${f.name}…`);
    const r = await fetch(`/api/upload?folder=${folder}&name=${encodeURIComponent(f.name)}`, {method:"POST", body:f});
    const j = await r.json().catch(() => ({ok:false, message:r.statusText}));
    toast(j.message, j.ok);
  }
  input.value = ""; refresh();
}
function targetSelect(id) {
  return `<select id="${id}">${["working","A","B","C","D"].map((t) => `<option value="${t}">${t === "working" ? "working project" : "project " + t}</option>`).join("")}</select>`;
}
function renderLibrary() {
  $("backups").innerHTML = lib.backups.length ? lib.backups.map((b) => `<div class="item"><div><div class="name">${when(b.date)}</div>
    <div class="meta">${fw(b.firmware)} · projects ${esc((b.projects || []).join(" ") || "none")} · samples ${esc((b.samples || []).join(" ") || "none")}${b.name.includes("_auto") ? " · auto" : ""}</div></div>
    <div class="row"><a href="/files/backups/${encodeURIComponent(b.name)}" download><button>Download</button></a>
    <button onclick="ask({cmd:'restore', file:${q(b.name)}})">Restore…</button><button class="danger" onclick="del('backups',${q(b.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No backups yet.</p>`;
  const fromBackups = lib.backups.flatMap((b) => (b.projects || []).map((p) => ({folder:"backups", file:b.name, source:p, label:`Project ${p}`, meta:`${when(b.date)} backup · ${fw(b.firmware)}`})));
  const files = lib.projects.map((p) => ({folder:"projects", file:p.name, source:"-", label:p.title || p.name, meta:`project file · ${fw(p.firmware)}`}));
  const all = [...files, ...fromBackups];
  $("projects").innerHTML = all.length ? all.map((p, i) => `<div class="item"><div><div class="name">${esc(p.label)}</div><div class="meta">${p.meta}</div></div>
    <div class="row">${targetSelect("t" + i)}<button onclick="ask({cmd:'load-project', folder:${q(p.folder)}, file:${q(p.file)}, source:${q(p.source)}, target:$('t${i}').value})">Load…</button>
    ${p.folder === "backups" ? `<a href="/api/project?backup=${encodeURIComponent(p.file)}&source=${p.source}" download><button>Download</button></a>` : `<a href="/files/projects/${encodeURIComponent(p.file)}" download><button>Download</button></a><button class="danger" onclick="del('projects',${q(p.file)})">Delete</button>`}</div></div>`).join("")
    : `<p class="empty">No projects yet: make a backup, or upload a project file.</p>`;
  $("samples").innerHTML = lib.samples.length ? lib.samples.map((s) => `<div class="item"><label class="row"><input type="checkbox" class="pick" value="${esc(s.name)}" style="width:22px;height:22px"><span class="name">${esc(s.name)}</span></label>
    <div class="row"><span class="meta">${(s.bytes / 1024).toFixed(0)} KB</span><button onclick="sedOpen('samples', ${q(s.name)})">Edit…</button><button class="danger" onclick="del('samples',${q(s.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No WAV files yet.</p>`;
  $("firmware").innerHTML = lib.firmware.length ? lib.firmware.map((f) => `<div class="item"><div><div class="name">${esc(f.name)}</div><div class="meta">${when(f.date)}</div></div>
    <div class="row"><button onclick="ask({cmd:'flash', file:${q(f.name)}})">Install…</button><button class="danger" onclick="del('firmware',${q(f.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No firmware yet: Get SLOOP update or Get Clipper, or upload a .fwsc.</p>`;
  $("recordings").innerHTML = lib.recordings.length ? lib.recordings.map((r, i) => `<div class="item" style="display:block"><div class="row" style="justify-content:space-between"><div><div class="name">${when(r.date)}</div>
    <div class="meta">${mins(r.seconds)} · ${(r.bytes / 1048576).toFixed(1)} MB</div></div>
    <a href="/files/recordings/${encodeURIComponent(r.name)}" download><button>Download</button></a></div>
    <audio controls preload="none" src="/files/recordings/${encodeURIComponent(r.name)}?inline=1"></audio>
    <div class="row" style="margin-top:6px"><button onclick="sedOpen('recordings', ${q(r.name)})">Edit as sample…</button>
    <button class="danger" onclick="del('recordings',${q(r.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No recordings yet: press Record above, or on the box press on any meter screen.</p>`;
  const pick = $("sed-pick"), was = pick.value;
  pick.innerHTML = (lib.recordings.length ? `<optgroup label="Recordings">${lib.recordings.map((r) => `<option value="recordings/${esc(r.name)}">${when(r.date)} · ${mins(r.seconds)}</option>`).join("")}</optgroup>` : "")
    + (lib.samples.length ? `<optgroup label="Samples">${lib.samples.map((x) => `<option value="samples/${esc(x.name)}">${esc(x.name)}</option>`).join("")}</optgroup>` : "")
    || `<option value="">No recordings or samples yet</option>`;
  if ([...pick.options].some((o) => o.value === was)) pick.value = was;
}
function loadSamples() {
  const files = [...document.querySelectorAll(".pick:checked")].map((c) => c.value);
  if (!files.length) return toast("Tick at least one WAV", false);
  ask({cmd:"load-sample", files, slot:+$("sslot").value, name:$("sname").value});
}

// ---------------------------------------------------------------- sample editor
// The take is decoded here, so the waveform, zoom and preview run on the phone; Save sends the settings and
// the box makes the file with the same sums the preview uses (sampler.py).
let sed = null, actx = null, sedSrc = null;
const NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const noteName = (n) => NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1);
const num = (id) => parseFloat($(id).value) || 0;
function sedPicked() { const v = $("sed-pick").value; if (v) sedOpen(v.slice(0, v.indexOf("/")), v.slice(v.indexOf("/") + 1)); }
async function sedOpen(folder, file) {
  if (tab !== "editor") show("editor");
  $("sed").hidden = false; $("sed-take").textContent = "loading…"; $("sed").scrollIntoView({behavior:"smooth", block:"start"});
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const [info, data] = await Promise.all([fetch(`/api/edit-info?folder=${folder}&file=${encodeURIComponent(file)}`).then((r) => r.json()),
                                            fetch(`/files/${folder}/${encodeURIComponent(file)}?inline=1`).then((r) => r.arrayBuffer())]);
    if (info.ok === false) throw new Error(info.message);
    const buf = await actx.decodeAudioData(data);
    const mono = new Float32Array(buf.length);
    for (let c = 0; c < buf.numberOfChannels; c++) { const d = buf.getChannelData(c); for (let i = 0; i < d.length; i++) mono[i] += d[i] / buf.numberOfChannels; }
    sed = {folder, file, info, rate: buf.sampleRate, mono, seconds: buf.length / buf.sampleRate, drag: null};
  } catch (e) { toast("Can't open it: " + e.message, false); return sedClose(); }
  const i = sed.info;
  $("sed-take").textContent = i.name; $("sed-name").value = i.name;
  $("sed-start").value = i.start; $("sed-end").value = i.end; $("sed-start").max = $("sed-end").max = sed.seconds;
  $("sed-fi").value = 0; $("sed-fo").value = 10; $("sed-norm").checked = true; $("sed-rev").checked = false; $("sed-zoom").value = 1;
  let opts = `<option value="">none (plays as C4)</option>`;
  for (let n = 24; n <= 96; n++) opts += `<option value="${noteName(n)}">${noteName(n)}${noteName(n) === i.note ? " (found)" : ""}</option>`;
  $("sed-note").innerHTML = opts; $("sed-note").value = i.note || "";
  sedDraw();
}
function sedClose() { sedStop(); sed = null; $("sed").hidden = true; }
function sedStop() { if (sedSrc) { try { sedSrc.stop(); } catch (e) {} sedSrc = null; } }
function sedView() {                                // [from, to] seconds shown: the selection's middle, zoomed
  if (sed.drag && sed.view) return sed.view;        // held still while a marker is dragged
  const z = +$("sed-zoom").value, span = sed.seconds / z, mid = (num("sed-start") + num("sed-end")) / 2;
  const from = Math.min(Math.max(0, mid - span / 2), sed.seconds - span);
  return [from, from + span];
}
function sedDraw() {
  if (!sed) return;
  const cv = $("sed-wave"), dpr = window.devicePixelRatio || 1, w = cv.width = cv.clientWidth * dpr, h = cv.height = cv.clientHeight * dpr;
  const g = cv.getContext("2d"), css = getComputedStyle(document.body);
  const [from, to] = sedView(), x = (t) => (t - from) / (to - from) * w;
  const s = num("sed-start"), e = num("sed-end"), fi = num("sed-fi") / 1000, fo = num("sed-fo") / 1000;
  g.fillStyle = css.getPropertyValue("--accent"); g.globalAlpha = .16; g.fillRect(x(s), 0, x(e) - x(s), h); g.globalAlpha = 1;
  const a0 = Math.floor(from * sed.rate), per = (to - from) * sed.rate / w;
  let peak = 1e-9; for (let i = 0; i < sed.mono.length; i += 64) peak = Math.max(peak, Math.abs(sed.mono[i]));
  g.strokeStyle = css.getPropertyValue("--ink"); g.lineWidth = Math.max(1, dpr * .8); g.beginPath();
  for (let px = 0; px < w; px++) {
    let lo = 0, hi = 0; const a = a0 + Math.floor(px * per), b = Math.min(sed.mono.length, a0 + Math.floor((px + 1) * per) + 1);
    for (let i = a; i < b; i++) { const v = sed.mono[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
    g.moveTo(px + .5, h / 2 - hi / peak * h * .46); g.lineTo(px + .5, h / 2 - lo / peak * h * .46 + 1);
  }
  g.stroke();
  g.strokeStyle = css.getPropertyValue("--accent"); g.lineWidth = 2 * dpr; g.beginPath();   // the fades
  g.moveTo(x(s), h); g.lineTo(x(s + fi), 4 * dpr); g.lineTo(x(e - fo), 4 * dpr); g.lineTo(x(e), h); g.stroke();
  for (const t of [s, e]) { g.fillRect(x(t) - dpr, 0, 2 * dpr, h); }
  const len = e - s, slot = sed.info.slot_seconds;
  $("sed-len").innerHTML = `${len.toFixed(2)} s selected` + (len > slot ? ` <span class="warn">· longer than a SLOOP slot holds (${slot} s): it won't fit until it's trimmed</span>` : "");
}
function sedTime(ev) { const r = $("sed-wave").getBoundingClientRect(), [from, to] = sedView(); return from + (ev.clientX - r.left) / r.width * (to - from); }
function sedSet(which, t) {
  const s = num("sed-start"), e = num("sed-end");
  t = Math.round(Math.min(sed.seconds, Math.max(0, t)) * 1000) / 1000;
  if (which === "start") $("sed-start").value = Math.min(t, e - .02); else $("sed-end").value = Math.max(t, s + .02);
  sedDraw();
}
$("sed-wave").addEventListener("pointerdown", (ev) => {
  if (!sed) return;
  const t = sedTime(ev); sed.drag = Math.abs(t - num("sed-start")) < Math.abs(t - num("sed-end")) ? "start" : "end";
  sed.view = null; sed.view = sedView(); $("sed-wave").setPointerCapture(ev.pointerId); sedSet(sed.drag, t);
});
$("sed-wave").addEventListener("pointermove", (ev) => { if (sed && sed.drag) sedSet(sed.drag, sedTime(ev)); });
$("sed-wave").addEventListener("pointerup", () => { if (sed) { sed.drag = sed.view = null; sedDraw(); } });
for (const id of ["sed-start", "sed-end", "sed-fi", "sed-fo", "sed-zoom"]) $(id).addEventListener("input", sedDraw);
window.addEventListener("resize", sedDraw);
function sedPlay() {                                // the edit as it will be saved, played on this phone
  if (!sed) return;
  sedStop(); actx.resume();
  const a = Math.floor(num("sed-start") * sed.rate), b = Math.min(sed.mono.length, Math.floor(num("sed-end") * sed.rate));
  if (b - a < sed.rate * .02) return toast("The selection is too short", false);
  const y = sed.mono.slice(a, b); if ($("sed-rev").checked) y.reverse();
  const fi = Math.min(y.length, Math.floor(num("sed-fi") / 1000 * sed.rate)), fo = Math.min(y.length, Math.floor(num("sed-fo") / 1000 * sed.rate));
  for (let i = 0; i < fi; i++) y[i] *= i / fi;
  for (let i = 0; i < fo; i++) y[y.length - 1 - i] *= i / fo;
  if ($("sed-norm").checked) { let p = 0; for (const v of y) p = Math.max(p, Math.abs(v)); if (p) { const k = sed.info.level / p; for (let i = 0; i < y.length; i++) y[i] *= k; } }
  const buf = actx.createBuffer(1, y.length, sed.rate); buf.copyToChannel(y, 0);
  sedSrc = actx.createBufferSource(); sedSrc.buffer = buf; sedSrc.connect(actx.destination); sedSrc.start();
}
async function sedSave() {
  if (!sed) return;
  const j = await post("/api/save-sample", {folder: sed.folder, file: sed.file, start: num("sed-start"), end: num("sed-end"),
    fade_in: num("sed-fi") / 1000, fade_out: num("sed-fo") / 1000, normalize: $("sed-norm").checked,
    reverse: $("sed-rev").checked, note: $("sed-note").value, name: $("sed-name").value});
  if (j.ok) refresh();
}

// ---------------------------------------------------------------- polling
let last = "";
async function update() {                          // the library job, as before
  const s = await fetch("/api/state").then((r) => r.json()).catch(() => ({}));
  const running = s.state === "running";
  $("job").innerHTML = s.waiting ? "Waiting for the confirmation on the box…"
    : running ? `${esc(s.job)}: ${esc(s.step)}${s.percent != null ? " " + Math.round(s.percent) + "%" : ""}`
    : s.state ? `Last: ${esc(s.job)} <span style="color:var(${s.state === "done" ? "--on" : "--bad"})">${esc(s.state === "done" ? "done" : "failed")}</span>: ${esc(s.message)}` : "Nothing running.";
  $("barwrap").hidden = !running || s.percent == null; $("bar").style.width = (s.percent || 0) + "%";
  const key = `${s.state}${s.finished}`;
  if (key !== last) { last = key; refresh(); }
  return running || s.waiting;
}
let shownRoutes = "", shownPresets = "";
async function status() {
  st = await fetch("/api/status").then((r) => r.json()).catch(() => ({offline:true}));
  renderStatus();
  if (st.offline) return;
  // redraw the MIDI tab only when the box's routes or presets changed: a redraw closes an open dropdown,
  // and the presets' name fields mustn't change under your fingers
  const r = JSON.stringify(st.routes), p = JSON.stringify(st.presets || {});
  if (r !== shownRoutes && Date.now() > routesDirtyUntil) { routes = JSON.parse(r); shownRoutes = r; renderMatrix(); renderEditor(); }
  if (p !== shownPresets && !String(document.activeElement && document.activeElement.id).startsWith("pn")) { shownPresets = p; renderPresets(); }
}
async function refresh() { lib = await fetch("/api/library").then((r) => r.json()).catch(() => lib); renderLibrary(); }
async function pollJobs() { setTimeout(pollJobs, (await update()) ? 700 : 3000); }
async function pollStatus() { if (!document.hidden && (tab === "status" || tab === "midi" || tab === "recordings")) await status(); setTimeout(pollStatus, 1000); }
show((location.hash || "#status").slice(1).replace(/[^a-z]/g, "") || "status");
pollJobs(); pollStatus();
</script></body></html>
"""


def main():
    port = int(os.environ.get("FM1_WEB_PORT", "80"))
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
