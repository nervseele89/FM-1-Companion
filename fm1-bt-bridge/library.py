#!/usr/bin/env python3
"""The FM-1 library: backups, projects, samples and firmware, one job at a time.

  library.py backup [--auto]          back up the FM-1 (SLOOP 2.3+) into ~/fm1-library/backups
  library.py restore FILE             a whole backup back onto the FM-1 (backs up first)
  library.py load-project FILE SRC TARGET
                                      a project into the FM-1: SRC working/A-D in a backup (ignored for a
                                      project file), TARGET working/A-D (backs up first)
  library.py save-project FILE SRC NAME
                                      a backup's project as its own file in ~/fm1-library/projects
  library.py load-sample SLOT NAME WAV...
                                      WAV files into USR SLOT (1-4), one zone each
  library.py flash FILE.fwsc          install a firmware package (backs up first when it can)
  library.py fetch sloop|clipper      download the newest SLOOP or Clipper release into ~/fm1-library/firmware
                                      (fetch-sloop: the same as fetch sloop)
  library.py import-sd                copy new files from the SD card's fm1-library folder
  library.py list                     the library as JSON
  library.py info                     what the FM-1 runs
  library.py identify                 the same, quietly, for the screens ("SLOOP 2.8", not its USB name)

Progress and the result go to $XDG_RUNTIME_DIR/fm1-library.json (the screen and the web page read it);
one job runs at a time. Exit code 0 when the job worked.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sloop  # noqa: E402

LIBRARY = Path(os.environ.get("FM1_LIBRARY", Path.home() / "fm1-library"))
FOLDERS = ("backups", "projects", "samples", "firmware", "recordings")
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
STATE, LOCK = RUNTIME / "fm1-library.json", RUNTIME / "fm1-library.lock"
FIRMWARE = RUNTIME / "fm1-firmware.json"     # what the connected FM-1 last said it runs
THEME = RUNTIME / "fm1-theme.json"           # FM-1 A's colours (SLOOP MENU > COLOR), for the desk box's screen
                                             # (the pocket box's OLED has one colour: it never asks)
SD = Path(os.environ.get("FM1_SD_LIBRARY", "/boot/firmware/fm1-library"))
SD_SYNC = "/usr/local/sbin/fm1-sd-sync"
KEEP_AUTO = 20
FIRMWARES = {"sloop": ("SLOOP", "isod89/sloop-fm1"),            # isod89's firmwares, both based on Felucca
             "clipper": ("Clipper", "isod89/clipper-fm1")}       # (Clipper: experimental)
RELEASES = "https://api.github.com/repos/{}/releases?per_page=10"
SUFFIXES = {"backups": (".json",), "projects": (".json",), "samples": (".wav",), "firmware": (".fwsc",),
            "recordings": (".wav",)}


def folder(name):
    path = LIBRARY / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def slug(text):
    return re.sub(r"[^A-Za-z0-9.]+", "-", str(text)).strip("-").lower() or "fm1"


# ---------------------------------------------------------------- one job at a time, with its progress

class Busy(Exception):
    pass


class Job:
    """The running job: holds the lock and writes its progress where the screen and web page look."""

    def __init__(self, name):
        self.name, self.lock = name, None
        self.status = {"job": name, "state": "running", "step": "", "percent": None, "message": "",
                       "started": time.time(), "pid": os.getpid()}

    def __enter__(self):
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self.lock = open(LOCK, "a")
        try:
            import fcntl
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:                    # (Windows test runs: no lock)
            pass
        except OSError:
            self.lock.close()
            raise Busy("another library job is running")
        self.write()
        return self

    def __exit__(self, kind, error, trace):
        if kind is not None and self.status["state"] == "running":
            message = str(error) if isinstance(error, (sloop.SloopError, Busy, OSError, ValueError)) else repr(error)
            self.fail(message)
        self.lock.close()
        return False

    def write(self):
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status))
        tmp.replace(STATE)

    def step(self, step, percent=None, message=""):
        self.status.update(step=step, percent=percent, message=message)
        self.write()
        print(f"{step} {'' if percent is None else f'{percent:3.0f}% '}{message}".rstrip(), flush=True)

    def progress(self, step):
        """a progress(done, total) callback that updates at most a few times a second"""
        last = [0.0]

        def report(done, total):
            now = time.monotonic()
            if now - last[0] > 0.3 or done >= total:
                last[0] = now
                self.step(step, min(100.0, 100.0 * done / max(total, 1)))
        return report

    def done(self, message, **result):
        self.status.update(state="done", message=message, percent=100, finished=time.time(), result=result)
        self.write()
        print("done:", message, flush=True)

    def fail(self, message):
        self.status.update(state="error", message=message, finished=time.time())
        self.write()
        print("error:", message, file=sys.stderr, flush=True)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def running():
    """the state of the library job that is running now (its process alive), else None"""
    state = read_state()
    if not state or state.get("state") != "running":
        return None
    try:
        os.kill(state.get("pid", 0), 0)
    except (OSError, TypeError):
        return None                           # it died without saying
    return state


def spawn(args):
    """start `library.py args` in the background, as its own systemd user unit where there is one (so it
    outlives whoever started it); -> an error message, or None"""
    if running():
        return "a library job is running"
    command = [sys.executable, str(Path(__file__).resolve()), *args]
    if shutil.which("systemd-run"):
        command = ["systemd-run", "--user", "--collect", "--quiet", f"--unit=fm1-library-{time.time_ns()}", *command]
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return str(e)
    return None


# A request from the web page to write to the FM-1: the box shows it and acts only when it's confirmed there
REQUEST = RUNTIME / "fm1-web-request.json"


def ask_box(args, title, lines):
    REQUEST.parent.mkdir(parents=True, exist_ok=True)
    # written in place: the box reads it every second and skips a half-written one
    REQUEST.write_text(json.dumps({"args": args, "title": title, "lines": lines, "time": time.time()}))


def take_request():
    """the web page's pending request (taken once), if it's under 2 minutes old"""
    try:
        request = json.loads(REQUEST.read_text())
        REQUEST.unlink()
    except (OSError, ValueError):
        return None
    return request if time.time() - request.get("time", 0) < 120 else None


STATUS = RUNTIME / "fm1-status.json"    # the box's live status, for the web page
COMMANDS = RUNTIME / "fm1-commands.jsonl"  # the web page's settings commands, one JSON object a line


def write_status(status):
    """the box: its status for the web page (RAM: $XDG_RUNTIME_DIR), written in place like ask_box"""
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(dict(status, time=time.time())))


def read_status():
    """the web page: the box's status, or None if there's none or it's over 5 s old (the box isn't running)"""
    try:
        status = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return None
    return status if time.time() - status.get("time", 0) < 5 else None


def queue_command(command):
    """the web page: a command for the box (settings, routes, transport, recording); appended, so several
    clicks in a row all arrive"""
    COMMANDS.parent.mkdir(parents=True, exist_ok=True)
    with open(COMMANDS, "a") as f:
        f.write(json.dumps(dict(command, time=time.time())) + "\n")


def take_commands():
    """the box: the queued commands (taken once), each under 30 s old"""
    taking = COMMANDS.with_suffix(".taking")
    try:
        COMMANDS.rename(taking)
        lines = taking.read_text().splitlines()
        taking.unlink()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if isinstance(command, dict) and time.time() - command.get("time", 0) < 30:
            out.append(command)
    return out


# ---------------------------------------------------------------- the device

def target_card():
    """the sound card of the FM-1 the library works on: with two plugged in, the one chosen in Settings > Sloop
    (FM-1 A or B); None with one (whichever it is)"""
    cards = sloop.fm1_cards()
    if len(cards) < 2:
        return None
    try:
        which = json.loads(SETTINGS.read_text()).get("library_fm1", "A")
    except (OSError, ValueError, AttributeError):
        which = "A"
    return cards[1] if which == "B" else cards[0]


def desk_box():
    """True on the desk box (Pi 3/4 + touchscreen: device.py), the only one with themes"""
    import device
    try:
        setting = json.loads(SETTINGS.read_text()).get("build", "auto")
    except (OSError, ValueError, AttributeError):
        setting = "auto"
    return device.detect(setting)["build"] == "desk"


def read_theme(dev=None):
    """FM-1 A's colours as SLOOP shows them (its palette, or the theme made in SLOOP's web editor when that's
    chosen), saved in THEME for the desk box's screen. dev: an open FM-1 A, else it's opened here"""
    import sloopstyle
    cards = sloop.fm1_cards()
    if dev is None or (len(cards) > 1 and target_card() != cards[0]):
        dev = sloop.Sloop(sloop.Link(card=cards[0] if len(cards) > 1 else None))
    if not dev.can_backup():
        return None                           # not SLOOP 2.3+: no settings to read (the screen keeps its own)
    objects = dev.read_objects({1, 11})
    theme = sloopstyle.theme_from_objects(objects.get(1, b""), objects.get(11, b""))
    try:
        THEME.write_text(json.dumps(sloopstyle.theme_dict(theme)))
    except OSError:
        pass
    return theme


def connect():
    dev = sloop.Sloop(sloop.Link(card=target_card()))
    try:
        FIRMWARE.write_text(json.dumps({"firmware": str(dev.version)}))
    except OSError:
        pass
    return dev


def forget_firmware():
    try:
        FIRMWARE.unlink()
    except OSError:
        pass


def firmware_label():
    """"SLOOP 2.8" from what the FM-1 last said ("FELUCCA SLOOP 2.8"); None if nothing has asked it yet. Its
    USB name says Felucca whatever it runs."""
    try:
        words = str(json.loads(FIRMWARE.read_text())["firmware"]).split()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(words) > 1 and words[0].upper() == "FELUCCA" and not any(c.isdigit() for c in words[1]):
        words = words[1:]
    return " ".join(w if any(c.isdigit() for c in w) or w.upper() == "SLOOP" else w.capitalize()
                    for w in words) or None


def safety_backup(dev, job, why):
    """a backup before anything is written to the FM-1; None if this firmware can't make one"""
    if not dev.can_backup():
        return None
    job.step("Backing up first", 0)
    data = dev.backup(job.progress("Backing up first"))
    return write_backup(data, why)


def write_backup(data, kind):
    when = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = folder("backups") / f"{when}_{slug(data.get('firmware'))}_{kind}.json"
    sloop.save_json(path, data)
    if kind == "auto":
        prune_auto()
    sd_export()
    return path


def prune_auto():
    autos = sorted(folder("backups").glob("*_auto.json"))
    for old in autos[:-KEEP_AUTO]:
        old.unlink(missing_ok=True)


# ---------------------------------------------------------------- the SD card's folder

def sd_export():
    """copy backups to the SD card (FAT, root only: through the installed helper); best effort"""
    if Path(SD_SYNC).exists():
        subprocess.run(["sudo", "-n", SD_SYNC], capture_output=True, timeout=60)


def import_sd():
    """new files from the SD card's fm1-library folders into the library; -> names copied"""
    copied = []
    for name in FOLDERS:
        if name == "recordings":              # made on the box, not brought in
            continue
        source = SD / name
        if not source.is_dir():
            continue
        for path in sorted(source.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES[name] or path.name.startswith("."):
                continue
            target = folder(name) / path.name
            if target.exists() and target.stat().st_size == path.stat().st_size:
                continue
            shutil.copyfile(path, target)
            copied.append(f"{name}/{path.name}")
    return copied


# ---------------------------------------------------------------- the jobs

def job_backup(auto=False):
    with Job("backup") as job:
        job.step("Connecting")
        dev = connect()
        dev.require_backup()
        data = dev.backup(job.progress("Backing up"))
        if auto:                               # timed backups: no new file when nothing changed
            latest = newest_auto()
            if latest and same_content(latest, data):
                job.done(f"No changes since {latest.name[11:13]}:{latest.name[13:15]}", file=latest.name,
                         unchanged=True)
                return
        path = write_backup(data, "auto" if auto else "manual")
        job.done(f"Backed up {dev.version}", file=path.name)


def newest_auto():
    autos = sorted(folder("backups").glob("*_auto.json"))
    return autos[-1] if autos else None


def same_content(path, data):
    """the backup file holds exactly the objects in data (dates and firmware strings aside)"""
    try:
        return sloop.objects_of(sloop.load_json(path)) == sloop.objects_of(data)
    except sloop.SloopError:
        return False


def job_restore(file):
    with Job("restore") as job:
        backup = sloop.load_json(file)
        sloop.objects_of(backup)               # damaged files stop here, before anything is written
        job.step("Connecting")
        dev = connect()
        sloop.same_family(dev.version, backup.get("firmware"))      # before anything, even the backup
        dev.check_stopped()
        before = safety_backup(dev, job, "before-restore")
        dev.restore(backup, job.progress("Restoring"))
        job.done(f"Restored {Path(file).name}", before=before and before.name)


def job_load_project(file, source, target):
    if target not in sloop.PROJECT_IDS:
        raise ValueError("TARGET is working, A, B, C or D")
    with Job("load-project") as job:
        project = sloop.load_json(file)
        data = sloop.project_from(project, source)
        job.step("Connecting")
        dev = connect()
        sloop.same_family(dev.version, project.get("firmware"))     # before anything, even the backup
        dev.check_stopped()
        before = safety_backup(dev, job, "before-project")
        dev.put_project(data, target, job.progress("Loading project"), firmware=project.get("firmware"))
        where = "the working project" if target == "working" else f"project {target}"
        job.done(f"Loaded into {where}", before=before and before.name)


def job_save_project(file, source, name):
    backup = sloop.load_json(file)
    data = sloop.project_from(backup, source)
    path = folder("projects") / f"{slug(name)}.json"
    sloop.save_json(path, sloop.project_file(data, name, backup.get("firmware"), f"{Path(file).name}:{source}"))
    print("saved", path)
    return path


SLOT_SECONDS = 7.3                      # a sample slot holds ~7.4 s at 22,050 Hz
# a sample's loudest point in the slot (16-bit): SLOOP's own uploader goes to 30000, ~0.8 dB below full scale,
# which leaves the FM-1 no room when it mixes a sample with other notes and sounds: it clips
SAMPLE_LEVELS = {"full": 30000, "-3 dB": 23197, "-6 dB": 16422}
SETTINGS = Path(os.environ.get("FM1_SETTINGS") or Path.home() / ".config" / "fm1-bridge" / "fm1-bridge.json")


def sample_peak():
    """the box's Sample level setting (Settings > Sloop), as a peak value; -6 dB if it isn't set"""
    try:
        level = json.loads(SETTINGS.read_text()).get("sample_level", "-6 dB")
    except (OSError, ValueError):
        level = "-6 dB"
    return SAMPLE_LEVELS.get(level, SAMPLE_LEVELS["-6 dB"])


def job_load_sample(slot, name, wavs):
    if slot not in (1, 2, 3, 4):
        raise ValueError("SLOT is 1, 2, 3 or 4")
    with Job("load-sample") as job:
        # no safety backup: a sample load only replaces that one slot, and the WAV it came from stays in the
        # library. Converting first means a bad WAV stops the job before the FM-1 is touched.
        job.step("Converting")
        header, data = sloop.build_slot(name, wavs, sample_peak())
        job.step("Connecting")
        dev = connect()
        dev.require_sample_slots()
        dev.check_stopped()
        done, report = [0], job.progress("Loading sample")

        def moved(n):
            done[0] += n
            report(done[0], len(data))

        dev.write_sample_slot(slot - 1, header, data, moved)
        job.done(f"USR{slot}: {name.upper()[:8]}")


def job_flash(package):
    import fm1_install
    with Job("flash") as job:
        if len(sloop.fm1_cards()) > 1:         # mid-update the FM-1 restarts as another device: the installer
            raise sloop.SloopError("two", "Unplug the other FM-1 first")    # could pick up the wrong one
        job.step("Checking the package")       # (~30 s on a Zero W)
        product, image = fm1_install.load_package(package, False)   # a bad package stops here
        job.step("Connecting")
        before = None
        try:
            dev = connect()
            dev.check_stopped()
            before = safety_backup(dev, job, "before-flash")
            dev.link.close()
        except sloop.SloopError as e:
            if e.code == "stop":
                raise
            # not SLOOP's editor (Felucca, stock, the update loader): flash without a backup
        updater = fm1_install.Updater(fm1_install.MidoBackend())
        device = updater.find()
        if not device:
            raise sloop.SloopError("notfound", "FM-1 not found for the update")
        steps = {"start": "Starting update", "loader": "Update mode", "reboot": "Restarting", "done": "Done"}

        def progress(kind, arg=None):
            if kind == "write":
                job.step("Writing", arg)
            elif kind == "check":
                job.step("Checking", None)
            else:
                job.step(steps.get(kind, kind))

        try:
            updater.install(device, image, product, progress)
        except fm1_install.InstallError as e:
            raise sloop.SloopError("rc", str(e))
        job.done(f"Installed {Path(package).stem}", product=product, before=before and before.name)


def newest_firmware(releases):
    """(release, its .fwsc asset) of the newest published release that has one (Clipper's are marked
    experimental, so GitHub's "latest" can't be relied on)"""
    for release in releases:
        if release.get("draft"):
            continue
        asset = next((a for a in release.get("assets", []) if a["name"].endswith(".fwsc")), None)
        if asset:
            return release, asset
    return None, None


def job_fetch(name):
    import fm1_install
    if name not in FIRMWARES:
        raise ValueError(f"fetch {' or '.join(FIRMWARES)}")
    title, repo = FIRMWARES[name]
    with Job(f"fetch-{name}") as job:
        job.step("Checking GitHub")
        request = urllib.request.Request(RELEASES.format(repo), headers={"Accept": "application/vnd.github+json",
                                                                         "User-Agent": "fm1-bridge"})
        with urllib.request.urlopen(request, timeout=20) as reply:
            release, asset = newest_firmware(json.load(reply))
        if not asset:
            raise ValueError(f"no {title} firmware file on GitHub")
        target = folder("firmware") / asset["name"]
        if target.exists() and target.stat().st_size == asset["size"]:
            job.done(f"Already have {asset['name']}", file=target.name)
            return
        tmp = target.with_suffix(".part")
        with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as reply, open(tmp, "wb") as out:
            total, got = asset["size"], 0
            while chunk := reply.read(65536):
                out.write(chunk)
                got += len(chunk)
                job.step("Downloading", 100.0 * got / max(total, 1))
        fm1_install.load_package(str(tmp), False)      # a damaged download never gets listed
        tmp.replace(target)
        job.done(f"Downloaded {asset['name']}", file=target.name, tag=release.get("tag_name"))


PART = re.compile(r"^(.+_fm1)-(a|b|usb\d+|u-[a-z0-9]+)\.wav$")   # a take's part: "..._fm1-b.wav", "..._fm1-u-ep136.wav"


def part_label(key):
    """"a" -> "FM-1 A", "u-ep136" -> "EP136" (a USB device, by its name), "usb0" -> "USB keys" (before v2.9)"""
    if key.startswith("u-"):
        return key[2:].upper()
    if key.startswith("usb"):
        n = int(key[3:] or 0)
        return "USB keys" + (f" {n + 1}" if n else "")
    return f"FM-1 {key.upper()}"


def recording_parts(name):
    """a take's parts (recorded with Record parts on, next to it), as file names in recording order"""
    stem = Path(name).stem
    found = [p.name for p in folder("recordings").glob(f"{stem}-*.wav") if PART.match(p.name)]
    order = {"a": 0, "b": 1}
    return sorted(found, key=lambda n: (order.get(PART.match(n).group(2), 2), n))


def delete_recording(name):
    """a take, its parts and their clip counts"""
    for n in [name] + recording_parts(name):
        path = folder("recordings") / n
        path.unlink(missing_ok=True)
        Path(str(path) + ".clips").unlink(missing_ok=True)


def recording_entry(path):
    """{name, bytes, date, seconds, clips?} for a take or a part"""
    entry = {"name": path.name, "bytes": path.stat().st_size, "date": path.stat().st_mtime}
    with open(path, "rb") as f:               # 16-bit stereo, 44.1 or 48 kHz: the byte rate is in the header
        head = f.read(44)
    rate = int.from_bytes(head[28:32], "little") if head[:4] == b"RIFF" else 0
    entry["seconds"] = max(0, entry["bytes"] - 44) // (rate or 192000)
    try:                                      # flat tops counted when it was saved (meters.clip_count)
        entry["clips"] = int(Path(str(path) + ".clips").read_text())
    except (OSError, ValueError):
        pass
    return entry


def listing():
    """{folder: [{name, bytes, date, ...}]} newest first, backups with what's in them, recordings with their
    parts (each {name, part, label, seconds, ...}) instead of beside them"""
    out = {}
    for name in FOLDERS:
        entries = []
        for path in sorted(folder(name).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES[name]:
                continue
            if name == "recordings" and PART.match(path.name):
                continue                      # under its take, below
            entry = {"name": path.name, "bytes": path.stat().st_size, "date": path.stat().st_mtime}
            if name == "backups":
                try:
                    entry.update(sloop.describe(sloop.load_json(path)))
                except sloop.SloopError:
                    entry["damaged"] = True
            elif name == "recordings":
                entry = recording_entry(path)
                parts = [dict(recording_entry(folder(name) / n), part=PART.match(n).group(2),
                              label=part_label(PART.match(n).group(2))) for n in recording_parts(path.name)]
                if parts:
                    entry["parts"] = parts
            elif name == "projects":
                try:
                    project = sloop.load_json(path)
                    entry.update(firmware=project.get("firmware"), title=project.get("name"))
                except sloop.SloopError:
                    entry["damaged"] = True
            entries.append(entry)
        out[name] = entries
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "backup":
            job_backup(auto="--auto" in args)
        elif command == "restore" and len(args) == 1:
            job_restore(args[0])
        elif command == "load-project" and len(args) == 3:
            job_load_project(args[0], args[1], args[2])
        elif command == "save-project" and len(args) == 3:
            job_save_project(*args)
        elif command == "load-sample" and len(args) >= 3:
            job_load_sample(int(args[0]), args[1], args[2:])
        elif command == "flash" and len(args) == 1:
            job_flash(args[0])
        elif command == "fetch-sloop":
            job_fetch("sloop")
        elif command == "fetch" and len(args) == 1:
            job_fetch(args[0])
        elif command == "import-sd":
            print(json.dumps(import_sd()))
        elif command == "list":
            print(json.dumps(listing(), indent=1))
        elif command == "identify":
            with Job("identify") as job:
                dev = connect()
                if desk_box():                # and FM-1 A's colours, for the desk box's screen
                    try:
                        read_theme(dev)
                    except sloop.SloopError:
                        pass
                job.done(firmware_label() or str(dev.version))
        elif command == "info":
            dev = connect()
            print(json.dumps({**dev.info, "can_backup": dev.can_backup()}))
        else:
            print(__doc__)
            return 2
        return 0
    except (sloop.SloopError, Busy, OSError, ValueError) as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
