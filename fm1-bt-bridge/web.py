#!/usr/bin/env python3
"""The FM-1 bridge's web page, http://fm1bridge.local while Wi-Fi is on.

Lists the library (backups, projects, samples, firmware), takes uploads, gives downloads, starts backups and
SLOOP downloads, and asks the box to restore, load or flash: those write to the FM-1, so they wait for a
confirmation on the box itself (hold the joystick press), never just a click on the network.

Python's standard library only. Port: $FM1_WEB_PORT (default 80; install.sh lets the user's services use it).
"""
import json
import os
import shutil
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402
import sloop  # noqa: E402

MAX_UPLOAD = 50 * 1024 * 1024
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
            self.send_file(path, ctype)
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
        if url.path == "/api/delete":
            path = safe_path(request.get("folder"), request.get("name"))
            if not path or not path.is_file():
                return self.fail("no such file", 404)
            path.unlink()
            return self.send(200, {"ok": True, "message": f"deleted {path.name}"})
        self.fail("not found", 404)

    def send_file(self, path, ctype):
        """a download in pieces: a long recording is bigger than the Zero W's memory would like"""
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 256 * 1024)

    def upload(self, folder, name):
        path = safe_path(folder, name) if folder != "recordings" else None
        if not path:
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
        elif command == "load-recording":
            path, slot = safe_path("recordings", r.get("file")), r.get("slot")
            if not path or not path.is_file() or slot not in (1, 2, 3, 4):
                return self.fail("pick a recording and a slot")
            label = "REC" + (path.stem.split("_")[1][:4] if path.stem.count("_") >= 2 else "")
            library.ask_box(["load-recording", str(slot), label, str(path)], "Load recording?",
                            [f"{path.stem[:20]}: first 7 s", f"into USR{slot}"])
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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FM-1 Bridge</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--dim:#626875;--line:#e2e5ea;--accent:#1677c9;--ok:#1d8a4a;--bad:#c23a32}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--ink:#e8eaee;--dim:#9aa1ad;--line:#2a2f38;--accent:#5fb0ff;--ok:#4cc27e;--bad:#ff6b61}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 2px}h2{font-size:16px;margin:0 0 10px}.sub{color:var(--dim);margin:0 0 16px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:0 0 14px}
button,select,input[type=text]{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 10px}
button{cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button:disabled{opacity:.5;cursor:default}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.item{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;justify-content:space-between;padding:9px 0;border-top:1px solid var(--line)}
.item:first-of-type{border-top:0}.name{font-weight:600;word-break:break-all}.meta{color:var(--dim);font-size:13px}
.empty{color:var(--dim)}.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:8px}
.bar div{height:100%;background:var(--accent);width:0;transition:width .3s}
#note{min-height:1.4em;margin-top:8px}.ok{color:var(--ok)}.bad{color:var(--bad)}
label.file{display:inline-block}label.file input{display:none}
</style></head><body><main>
<h1>FM-1 Bridge</h1><p class="sub">Library: backups, projects, samples and firmware for the FM-1.</p>

<section><h2>Now</h2><div id="job" class="meta">Nothing running.</div><div class="bar" id="barwrap" hidden><div id="bar"></div></div>
<div class="row" style="margin-top:10px"><button class="primary" onclick="run('backup')">Back up now</button>
<button onclick="run('fetch-sloop')">Get SLOOP update</button><button onclick="run('fetch-clipper')" title="isod89's synthesizer firmware, experimental">Get Clipper (experimental)</button><button onclick="run('import-sd')">Import SD card</button></div>
<div id="note"></div></section>

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

<section><h2>Firmware</h2><p class="meta" style="margin:0 0 6px">SLOOP (groovebox) and Clipper (synthesizer, experimental) are both isod89's, based on Felucca. Backups, projects and samples belong to the firmware they came from: a SLOOP backup can't be restored onto Clipper, and Clipper has wavetables instead of sample slots.</p><div id="firmware"></div>
<div class="row" style="margin-top:8px"><label class="file"><input type="file" accept=".fwsc" onchange="upload('firmware',this)"><button onclick="this.previousElementSibling.click()">Upload a .fwsc…</button></label></div></section>

<section><h2>Recordings</h2><p class="meta" style="margin:0 0 6px">What the box recorded from the FM-1 (48 kHz WAV): on the box, open the meters (joystick left) and press to start and stop. <i>Load into slot</i> resamples a take into the FM-1: its first 7 seconds of sound, with the note it plays as the root.</p>
<div id="recordings"></div></section>

<p class="meta">Anything that writes to the FM-1 asks for a confirmation on the box: hold the joystick press for 2 seconds. A backup is made first.</p>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const q = (s) => esc(JSON.stringify(s));            // a JS string literal inside an onclick attribute
const when = (t) => new Date(t * 1000).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"});
const fw = (s) => esc((s || "?").split(" ").pop());
let lib = {backups:[], projects:[], samples:[], firmware:[], recordings:[]};
const mins = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function note(msg, ok) { $("note").innerHTML = `<span class="${ok ? "ok" : "bad"}">${esc(msg)}</span>`; }
async function post(url, body) {
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  const j = await r.json().catch(() => ({ok:false, message:r.statusText}));
  note(j.message, j.ok); refresh(); setTimeout(update, 300); return j;
}
const run = (cmd) => post("/api/run", {cmd});
const ask = (body) => post("/api/ask", body);
async function del(folder, name) { if (confirm(`Delete ${name}?`)) post("/api/delete", {folder, name}); }

async function upload(folder, input) {
  for (const f of input.files) {
    note(`Uploading ${f.name}…`, true);
    const r = await fetch(`/api/upload?folder=${folder}&name=${encodeURIComponent(f.name)}`, {method:"POST", body:f});
    const j = await r.json().catch(() => ({ok:false, message:r.statusText}));
    note(j.message, j.ok);
  }
  input.value = ""; refresh();
}

function targetSelect(id) {
  return `<select id="${id}">${["working","A","B","C","D"].map((t) => `<option value="${t}">${t === "working" ? "working project" : "project " + t}</option>`).join("")}</select>`;
}
function render() {
  $("backups").innerHTML = lib.backups.length ? lib.backups.map((b) => `<div class="item"><div><div class="name">${when(b.date)}</div>
    <div class="meta">${fw(b.firmware)} · projects ${esc((b.projects || []).join(" ") || "none")} · samples ${esc((b.samples || []).join(" ") || "none")}${b.name.includes("_auto") ? " · auto" : ""}</div></div>
    <div class="row"><a href="/files/backups/${encodeURIComponent(b.name)}" download><button>Download</button></a>
    <button onclick="ask({cmd:'restore', file:${q(b.name)}})">Restore…</button><button onclick="del('backups',${q(b.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No backups yet.</p>`;
  const fromBackups = lib.backups.flatMap((b) => (b.projects || []).map((p) => ({folder:"backups", file:b.name, source:p, label:`Project ${p}`, meta:`${when(b.date)} backup · ${fw(b.firmware)}`})));
  const files = lib.projects.map((p) => ({folder:"projects", file:p.name, source:"-", label:p.title || p.name, meta:`project file · ${fw(p.firmware)}`}));
  const all = [...files, ...fromBackups];
  $("projects").innerHTML = all.length ? all.map((p, i) => `<div class="item"><div><div class="name">${esc(p.label)}</div><div class="meta">${p.meta}</div></div>
    <div class="row">${targetSelect("t" + i)}<button onclick="ask({cmd:'load-project', folder:${q(p.folder)}, file:${q(p.file)}, source:${q(p.source)}, target:$('t${i}').value})">Load…</button>
    ${p.folder === "backups" ? `<a href="/api/project?backup=${encodeURIComponent(p.file)}&source=${p.source}" download><button>Download</button></a>` : `<a href="/files/projects/${encodeURIComponent(p.file)}" download><button>Download</button></a><button onclick="del('projects',${q(p.file)})">Delete</button>`}</div></div>`).join("")
    : `<p class="empty">No projects yet: make a backup, or upload a project file.</p>`;
  $("samples").innerHTML = lib.samples.length ? lib.samples.map((s) => `<div class="item"><label class="row"><input type="checkbox" class="pick" value="${esc(s.name)}"><span class="name">${esc(s.name)}</span></label>
    <div class="row"><span class="meta">${(s.bytes / 1024).toFixed(0)} KB</span><button onclick="del('samples',${q(s.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No WAV files yet.</p>`;
  $("firmware").innerHTML = lib.firmware.length ? lib.firmware.map((f) => `<div class="item"><div><div class="name">${esc(f.name)}</div><div class="meta">${when(f.date)}</div></div>
    <div class="row"><button onclick="ask({cmd:'flash', file:${q(f.name)}})">Install…</button><button onclick="del('firmware',${q(f.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No firmware yet: Get SLOOP update or Get Clipper, or upload a .fwsc.</p>`;
}
function renderRecordings() {
  $("recordings").innerHTML = lib.recordings.length ? lib.recordings.map((r, i) => `<div class="item"><div><div class="name">${when(r.date)}</div>
    <div class="meta">${mins(r.seconds)} · ${(r.bytes / 1048576).toFixed(1)} MB · ${esc(r.name)}</div></div>
    <div class="row"><a href="/files/recordings/${encodeURIComponent(r.name)}" download><button>Download</button></a>
    <select id="rs${i}">${[1,2,3,4].map((n) => `<option value="${n}">USR${n}</option>`).join("")}</select>
    <button onclick="ask({cmd:'load-recording', file:${q(r.name)}, slot:+$('rs${i}').value})">Load into slot…</button>
    <button onclick="del('recordings',${q(r.name)})">Delete</button></div></div>`).join("")
    : `<p class="empty">No recordings yet: on the box, open the meters (joystick left) and press to record.</p>`;
}
function loadSamples() {
  const files = [...document.querySelectorAll(".pick:checked")].map((c) => c.value);
  if (!files.length) return note("Tick at least one WAV", false);
  ask({cmd:"load-sample", files, slot:+$("sslot").value, name:$("sname").value});
}

let last = "";
async function update() {
  const s = await fetch("/api/state").then((r) => r.json()).catch(() => ({}));
  const running = s.state === "running";
  $("job").innerHTML = s.waiting ? "Waiting for the confirmation on the box…"
    : running ? `${esc(s.job)}: ${esc(s.step)}${s.percent != null ? " " + Math.round(s.percent) + "%" : ""}`
    : s.state ? `Last: ${esc(s.job)} <span class="${s.state === "done" ? "ok" : "bad"}">${esc(s.state === "done" ? "done" : "failed")}</span>: ${esc(s.message)}` : "Nothing running.";
  $("barwrap").hidden = !running || s.percent == null; $("bar").style.width = (s.percent || 0) + "%";
  const key = `${s.state}${s.finished}`;
  if (key !== last) { last = key; refresh(); }
  return running || s.waiting;
}
async function poll() { setTimeout(poll, (await update()) ? 700 : 3000); }
async function refresh() { lib = await fetch("/api/library").then((r) => r.json()).catch(() => lib); render(); renderRecordings(); }
refresh(); poll();
</script></body></html>
"""


def main():
    port = int(os.environ.get("FM1_WEB_PORT", "80"))
    ThreadingHTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
