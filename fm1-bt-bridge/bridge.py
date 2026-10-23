#!/usr/bin/env python3
"""FM-1 Bluetooth bridge for a Raspberry Pi Zero W with a Waveshare 1.3" OLED HAT.

Sends the FM-1's USB audio (any firmware that shows up as a USB audio input, such as
Sloop's "Felucca") to Bluetooth headphones through PipeWire, passes MIDI both ways between
the FM-1's USB MIDI and Bluetooth LE MIDI (blemidi.py), and drives the HAT:

  KEY1           scan for headphones; pick with the joystick, press to pair
  KEY2           reconnect the last headphones (hold: disconnect)
  KEY3 (hold 3s) shut down safely
  joystick       up/down: volume on the status screen, move in lists; right: settings
                 (BLE MIDI, pairing window, Wi-Fi); press: change; left: back

  bridge.py --test-screen   draw a test pattern (check the orientation)
  bridge.py --sim DIR       run without the HAT, writing frames as PNG files to DIR
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import battery
import library
import sloop
from libui import LIBRARY_KEYS, LibraryScreens
import meters
from meters import MeterScreens
from midiui import MidiScreens
from sampleui import SampleEditor
from weblink import WebLink

STATE_FILE = Path.home() / ".config" / "fm1-bridge" / "fm1-bridge.json"
DEFAULTS = {
    "fw_label": None,       # what the FM-1 last said it runs ("SLOOP 2.8"): shown until it's asked again
    "rotate": 0,            # 180 if the screen is upside down
    "contrast": 128,
    "last_mac": None,
    "last_name": None,
    "flip_joystick": None,  # None: match the screen; true/false to force
    "midi_enabled": True,
    "auto_backup": "boot",  # "off", "boot" (once at start-up), or minutes between backups: 2, 5, 10, 30
    "audio_rate": 48000,    # PipeWire's rate (Hz), 48000 or 44100: the FM-1 is opened at this rate
    "clock_mode": "follow", # MIDI clock: "follow" (pass on and show incoming clock) or "box" (send our own)
    "bpm": 120,             # the box's tempo when it's the clock master
    "routes": None,         # the MIDI matrix: {"BT>FM-1": {...}, ...}; None = every route on, unchanged
    "route_presets": None,  # {"1": {"routes": ..., "saved": time}, ...}: matrix setups to recall
    "metronome": False,     # a click on the speaker in time with the clock
    "click": "mid",         # its level: "low", "mid", "high"
    "sample_level": "-6 dB",  # how loud samples go into the FM-1: "-6 dB", "-3 dB", "full" (SLOOP's own)
    "library_fm1": "A",     # with two FM-1s on a hub, the one backups, restores and loads go to (library.py reads it)
    "mix": {"a": 100, "b": 100, "usb": 100},   # the mixer's levels (%, to 200): FM-1 A, FM-1 B, USB keyboards' sound
    "source_match": ["felucca", "sloop", "fm-1", "fm1", "m-vave"],
}
GPIO_CHIP = "/dev/gpiochip0"
PIN_DC, PIN_RST = 24, 25
KEYS = {"key1": 21, "key2": 20, "key3": 16, "up": 6, "down": 19, "left": 5, "right": 26, "press": 13}
DIM_AFTER, OFF_AFTER = 60, 300          # seconds without a key press or a status change (OLED burn-in)
SCAN_SECONDS = 10
TAP_GAP = 2.0                           # seconds: a longer pause between taps starts a new tap tempo
BPM_RANGE = (30, 300)
RECONNECT_EVERY = 20
GROUPS = {"g_play": ("play", "Playing"),   # Settings' sections: (row key) -> (settings_menu, title). The first
          "g_audio": ("audio", "Audio"),    # three work whatever you plug in; Sloop is what needs SLOOP's
          "g_midi": ("midi", "MIDI"),       # editor protocol, FM-1 what needs the FM-1 itself (its firmware)
          "g_sloop": ("sloop", "Sloop"),
          "g_fm1": ("fm1", "FM-1")}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x01\x02]")
UNNAMED = re.compile(r"[0-9A-F]{2}(-[0-9A-F]{2}){5}")


# ---------------------------------------------------------------- state

def load_state():
    state = dict(DEFAULTS)
    try:
        state.update(json.loads(STATE_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def run(cmd, timeout=15, merge=True, clean=True):
    """Run a command; return (exit code, output). Never raises. clean strips colour codes."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = r.stdout + (r.stderr if merge else "")
        return r.returncode, ANSI.sub("", out) if clean else out
    except (subprocess.TimeoutExpired, OSError) as e:
        return -1, str(e)


def hardware_fingerprint():
    """USB devices and Bluetooth links as sysfs lists them: cheap enough to check every second,
    and it changes as soon as the FM-1 is plugged in or headphones connect."""
    seen = []
    for folder in ("/sys/bus/usb/devices", "/sys/class/bluetooth"):
        try:
            seen.append(tuple(sorted(os.listdir(folder))))
        except OSError:
            seen.append(())
    return tuple(seen)


# ---------------------------------------------------------------- Bluetooth (bluetoothctl)

def bt(*args, timeout=15):
    return run(["bluetoothctl", *args], timeout)


def parse_info(mac, text):
    info = {"mac": mac, "uuids": []}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "UUID":
            info["uuids"].append(value)
        elif key in ("Name", "Alias", "Icon", "Paired", "Connected", "Trusted"):
            info[key.lower()] = value
    return info


def is_audio(info):
    return info.get("icon", "").startswith("audio") or any(u.startswith("Audio Sink") for u in info["uuids"])


def parse_devices(text):
    """bluetoothctl devices -> [(mac, name)], skipping devices that only have an address."""
    found = []
    for line in text.splitlines():
        m = re.match(r"\s*Device ([0-9A-F:]{17}) (.+)", line)
        if m and not UNNAMED.fullmatch(m.group(2).strip()):
            found.append((m.group(1), m.group(2).strip()))
    return found


def bt_scan(seconds=SCAN_SECONDS):
    bt("--timeout", str(seconds), "scan", "on", timeout=seconds + 10)
    _, out = bt("devices", timeout=5)
    devices = []
    for mac, name in parse_devices(out):
        info = parse_info(mac, bt("info", mac, timeout=5)[1])
        info.setdefault("name", name)
        devices.append(info)
    audio = [d for d in devices if is_audio(d)]
    return sorted(audio or devices, key=lambda d: d["name"].lower())


def bt_error(out):
    """The last line of bluetoothctl's output, in words that fit the screen."""
    friendly = (("profile-unavailable", "Audio service not ready"), ("page-timeout", "Headphones off?"),
                ("Host is down", "Headphones off?"), ("AuthenticationFailed", "Pairing rejected"),
                ("AuthenticationCanceled", "Pairing cancelled"), ("not available", "Not found - rescan"),
                ("NotReady", "Bluetooth is off"), ("timed out", "No answer"))
    for needle, text in friendly:
        if needle in out:
            return text
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[-1][:40] if lines else "Failed"


def bt_connect(mac):
    _, out = bt("connect", mac, timeout=25)
    ok = "Connection successful" in out
    return ok, "Connected" if ok else bt_error(out)


def bt_pair(mac):
    # The adapter is normally not pairable (strangers can't pair), but then BlueZ pairs without
    # bonding and forgets the keys on disconnect: be pairable just while pairing
    bt("pairable", "on", timeout=5)
    try:
        bt("remove", mac, timeout=10)          # a stale, unbonded pairing would make "pair" a no-op
        # BlueZ forgets unpaired devices about 30 s after a scan, and "remove" just did: see it again
        bt("--timeout", "6", "scan", "on", timeout=16)
        _, out = bt("--agent", "NoInputNoOutput", "pair", mac, timeout=40)
    finally:
        bt("pairable", "off", timeout=5)
    if "Pairing successful" not in out:
        return False, bt_error(out)
    bt("trust", mac)
    _, info = bt("info", mac, timeout=5)
    if "Bonded:" in info and "Bonded: yes" not in info:
        return False, "Paired but not saved"
    return bt_connect(mac)


# ---------------------------------------------------------------- audio (PipeWire)

def pick_nodes(objects, source_match, cards=()):
    """From pw-dump's objects: (FM-1 A's USB input, the Bluetooth headphones, FM-1 B's input, [other USB inputs:
    a keyboard's own sound]), each a dict or None. cards: the FM-1s' sound card numbers, A first
    (sloop.fm1_cards); B only with two of them. An FM-1 whose name couldn't be read is known by its USB ID."""
    sources, fallbacks, sink = [], [], None
    for o in objects:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info") or {}).get("props") or {}
        cls, name = props.get("media.class", ""), props.get("node.name", "")
        label = props.get("node.description") or props.get("node.nick") or name
        node = {"id": o.get("id"), "name": name, "label": label}
        if cls == "Audio/Source" and name.startswith("alsa_input."):
            # the USB card name ("Felucca"), not the description ("pid.codes Test PID Analog Stereo")
            node["label"] = props.get("alsa.card_name") or props.get("node.nick") or label
            text = " ".join(str(props.get(k, "")) for k in
                            ("node.name", "node.description", "node.nick", "alsa.card_name", "alsa.long_card_name"))
            path = re.search(r":(\d+)", str(props.get("api.alsa.path", "")))   # "front:3": sound card 3
            node["card"] = int(path.group(1)) if path else None
            if any(m in text.lower() for m in source_match):
                sources.append(node)
            elif ".usb-" in name:
                fallbacks.append((any(i in text.lower() for i in ("1209", "pid.codes")), node))
        elif cls == "Audio/Sink" and name.startswith("bluez_output."):
            sink = sink or node
    # no name matched: the FM-1's USB ID (1209:0001); anything else is a USB keyboard's own sound
    fallback = next((n for fm1, n in fallbacks if fm1), None)
    others = [n for fm1, n in fallbacks if not fm1]
    cards = list(cards)
    if len(cards) > 1:                        # two FM-1s: A and B by the hub port they're on
        by_card = {n["card"]: n for n in sources}
        a, b = by_card.get(cards[0]), by_card.get(cards[1])
        return a or (sources[0] if sources else fallback), sink, b, others
    return (sources[0] if sources else fallback), sink, None, others


class PwWatch:
    """PipeWire's objects, kept up to date by one `pw-dump --monitor` left running. Every new connection to
    PipeWire (a pw-dump, a pw-cli, a wpctl) costs a Zero W 1-2 s of CPU in PipeWire and WirePlumber, and a
    whole pw-dump with three sound sources ~5 s: done every scan, that took a third of the CPU, made playing
    lag and the controls slow. This connects once: the whole picture, then only what changes."""

    def __init__(self):
        self.objects, self.ready = {}, False
        self.lock, self.changed = threading.Lock(), threading.Event()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                proc = subprocess.Popen(["pw-dump", "-N", "-m"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return
            lines = []
            for line in proc.stdout:          # batches: "[", objects, "]"
                lines.append(line)
                if line.rstrip() == "]":
                    try:
                        self.apply(json.loads("".join(lines)))
                    except ValueError:
                        pass
                    lines = []
            with self.lock:                   # PipeWire went away (restarted): start afresh
                self.objects, self.ready = {}, False
            time.sleep(2)

    def apply(self, batch):
        with self.lock:
            for o in batch:
                key = o.get("id")
                if o.get("info") is None and "type" not in o:
                    self.objects.pop(key, None)                 # removed
                    continue
                old = self.objects.get(key)
                if old and old.get("info") and o.get("info"):  # an update: only what changed is in it
                    info = dict(old["info"], **o["info"])
                    info["params"] = dict(old["info"].get("params") or {}, **(o["info"].get("params") or {}))
                    o = dict(old, **dict(o, info=info))
                self.objects[key] = o
            self.ready = True
        self.changed.set()

    def snapshot(self):
        """the objects as pw-dump gives them, or None before the first picture"""
        with self.lock:
            return list(self.objects.values()) if self.ready else None


def node_volume(objects, node_id):
    """a node's volume in % as wpctl shows it (the cube root of its channel volume), or None"""
    for o in objects:
        if o.get("id") == node_id:
            for props in ((o.get("info") or {}).get("params") or {}).get("Props") or []:
                volumes = props.get("channelVolumes") if isinstance(props, dict) else None
                if volumes:
                    return round(max(0.0, volumes[0]) ** (1 / 3) * 100)
    return None


def pw_nodes(source_match, watch):
    """-> (pick_nodes' answer, the objects), or None before PipeWire's picture is in (not "all unplugged")"""
    objects = watch.snapshot()
    if not objects:
        return None
    return pick_nodes(objects, source_match, sloop.fm1_cards()), objects


def get_volume(node_id):
    _, out = run(["wpctl", "get-volume", str(node_id)], timeout=5)
    m = re.search(r"Volume:\s*([\d.]+)", out)
    return round(float(m.group(1)) * 100) if m else None


def change_volume(node_id, step):
    run(["wpctl", "set-volume", "-l", "1.0", str(node_id), f"{abs(step)}%{'+' if step > 0 else '-'}"], timeout=5)


def set_volume(node_id, percent):
    run(["wpctl", "set-volume", str(node_id), f"{percent}%"], timeout=5)


def volume_snap(volume, step):
    """the next multiple of the step up or down from volume: 37 -> 38 or 36, 38 -> 40 or 36. Relative steps
    drift (a Bluetooth speaker rounds its volume to 1/127ths); whole percents come back from it unchanged."""
    size = abs(step)
    target = (volume // size + 1) * size if step > 0 else (volume - 1) // size * size
    return max(0, min(100, target))


def alsa_has_card(source_match):
    """the FM-1's USB sound card is in /proc/asound/cards (whatever PipeWire thinks)"""
    try:
        text = Path("/proc/asound/cards").read_text().lower()
    except OSError:
        return False
    return any("usb-audio" in line and any(m in line for m in source_match) for line in text.splitlines())


def wifi_enabled():
    """Wi-Fi radio state from NetworkManager: True, False, or None if unknown."""
    code, out = run(["nmcli", "radio", "wifi"], timeout=5)
    return {"enabled": True, "disabled": False}.get(out.strip()) if code == 0 else None


def set_rate(rate):
    """Lock PipeWire to rate (until PipeWire restarts; Loopback sets it again before each start), in blocks of
    1024 samples: left alone, at 48 kHz it picks 2048, ~20 ms more delay at every stage (keys feel laggy)."""
    run(["pw-metadata", "-n", "settings", "0", "clock.force-rate", str(rate)], timeout=5)
    run(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "1024"], timeout=5)


def set_wifi(on):
    """NetworkManager keeps the setting across reboots."""
    run(["sudo", "-n", "nmcli", "radio", "wifi", "on" if on else "off"], timeout=10)


class Loopback:
    """One pw-loopback process from the FM-1 to the headphones, restarted when either changes."""

    def __init__(self, rate=48000):
        self.proc, self.key = None, None
        self.rate, self.paused_until = rate, 0.0

    def ensure(self, source, sink, name=None):
        """name: the playback side's node name (a mixer channel's, to set its level there)"""
        key = (source, sink, name) if source and sink else None
        if key == self.key and (key is None or self.proc.poll() is None):
            return
        if time.monotonic() < self.paused_until:
            return                            # reopening: the FM-1's input is still closing
        self.stop()
        if key:
            set_rate(self.rate)
            self.proc = subprocess.Popen(
                ["pw-loopback",
                 "--capture-props", f"target.object={source} node.dont-reconnect=true",
                 "--playback-props", f"target.object={sink} node.dont-reconnect=true"
                 + (f" node.name={name}" if name else "")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.key = key

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def reopen(self, rate):
        """Close the FM-1's input and open it again at rate. PipeWire closes an unused input after 5 s;
        the FM-1 sometimes sends only silence until its input is opened afresh."""
        self.rate = rate
        self.stop()
        self.paused_until = time.monotonic() + 7

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.key = None, None


MIX_MAX = 200                           # the levels go to 200%: some keyboards' synths are much quieter


class Mixer:
    """With more than one sound source (FM-1 A and B, a USB keyboard's own sound): one pw-loopback a source,
    straight to the speaker, its playback node named fm1-mix-<key> so its level can be set there; PipeWire adds
    them up at the speaker, before Bluetooth. (A mixing stage of its own between them and the speaker cost a
    Zero W another hop of delay and CPU: playing felt laggy, the controls slow.) An FM-1 keeps the clock, the
    others are re-timed to it (WirePlumber gives USB inputs the slack for that: wireplumber-51-fm1-bridge.conf)."""

    def __init__(self, rate=48000):
        self.sink, self.rate = None, rate
        self.channels, self.levels = {}, {}   # key -> Loopback to the speaker; node id -> level last set
        self.paused_until = 0.0

    def running(self):
        return any(loop.running() for loop in self.channels.values())

    def ensure(self, sink, sources):
        """sink: the speaker's node name or None; sources: [(key, source node name)]"""
        if time.monotonic() < self.paused_until:
            return
        if sink != self.sink:
            self.stop()
            self.sink = sink
        wanted = dict(sources) if sink else {}
        for key in [k for k in self.channels if k not in wanted]:
            self.channels.pop(key).stop()
        for key, source in wanted.items():
            loop = self.channels.setdefault(key, Loopback(self.rate))
            loop.rate = self.rate
            loop.ensure(source, sink, f"fm1-mix-{key}")

    def set_levels(self, objects, levels):
        """levels: {key: percent}, set on the channels' nodes once pw-dump shows them"""
        for o in objects:
            props = (o.get("info") or {}).get("props") or {}
            name = str(props.get("node.name", ""))
            key = name[len("fm1-mix-"):] if name.startswith("fm1-mix-") else None
            if key in levels and self.levels.get(o.get("id")) != levels[key]:
                run(["wpctl", "set-volume", str(o["id"]), f"{levels[key] / 100:.2f}"], timeout=5)
                self.levels[o.get("id")] = levels[key]

    def reopen(self, rate):
        self.rate = rate
        self.stop()
        self.paused_until = time.monotonic() + 7

    def stop(self):
        for loop in self.channels.values():
            loop.stop()
        self.channels, self.levels, self.sink = {}, {}, None


# ---------------------------------------------------------------- hardware

def pack_pages(img):
    """A 128x64 1-bit image as the SH1106's 8 pages: per column one byte, bit 0 at the top."""
    data = img.transpose(Image.Transpose.ROTATE_270).tobytes()   # per column 8 bytes, the bottom page first
    return [data[7 - p::8] for p in range(8)]


class SH1106:
    """The HAT's 128x64 OLED on SPI0 CE0, DC on GPIO24, reset on GPIO25."""

    def __init__(self, rotate=0, contrast=128):
        import gpiod
        import spidev
        from gpiod.line import Direction, Value
        self.on, self.off = Value.ACTIVE, Value.INACTIVE
        self.pins = gpiod.request_lines(GPIO_CHIP, consumer="fm1-bridge oled", config={
            (PIN_DC, PIN_RST): gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)})
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 8_000_000
        self.spi.mode = 0
        self.rotate = rotate
        self.pins.set_value(PIN_RST, self.off)
        time.sleep(0.01)
        self.pins.set_value(PIN_RST, self.on)
        time.sleep(0.01)
        # SH1106 128x64; 0xAD 0x8B switches on the panel's DC-DC supply (without it the HAT stays dark)
        self.cmd(0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0xAD, 0x8B, 0xA1, 0xC8, 0xDA, 0x12,
                 0x81, contrast, 0xD9, 0x22, 0xDB, 0x35, 0xA4, 0xA6)
        self.show(Image.new("1", (128, 64)))
        self.cmd(0xAF)

    def cmd(self, *values):
        self.pins.set_value(PIN_DC, self.off)
        self.spi.writebytes(list(values))

    def show(self, img):
        if self.rotate:
            img = img.rotate(self.rotate)
        for page, data in enumerate(pack_pages(img)):
            self.cmd(0xB0 + page, 0x02, 0x10)       # the panel's 128 columns start at RAM column 2
            self.pins.set_value(PIN_DC, self.on)
            self.spi.writebytes(list(data))

    def contrast(self, value):
        self.cmd(0x81, value)

    def power(self, on):
        self.cmd(0xAF if on else 0xAE)


class SimDisplay:
    """Stand-in for the OLED: writes each new frame to DIR as a PNG."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def show(self, img):
        self.count += 1
        img.convert("L").resize((512, 256), Image.NEAREST).save(self.folder / f"frame{self.count:03}.png")

    def contrast(self, value):
        pass

    def power(self, on):
        pass


class KeyTimer:
    """Turns presses and releases into the app's key events: post(name) on press, or for KEY2/KEY3
    on release, post(name, True) once held long enough, and up/down repeating while held."""

    HOLD = {"key2": 1.2, "key3": 3.0, "up": 0.4, "down": 0.4, "press": 2.0}    # press held: confirm
    REPEAT, FAST, FAST_AFTER = 0.2, 0.08, 1.5   # up/down held: 5 a second, faster after 1.5 s

    def __init__(self, post):
        self.post, self.down = post, {}

    def pressed(self, name, now):
        self.down[name] = {"at": now, "held": False, "next": 0.0}
        if name not in ("key2", "key3"):
            self.post(name)

    def released(self, name, now):
        info = self.down.pop(name, None)
        if info and name in ("key2", "key3") and not info["held"]:
            self.post(name)

    def tick(self, now):
        for name, info in self.down.items():
            hold = self.HOLD.get(name)
            if hold and not info["held"] and now - info["at"] >= hold:
                info["held"], info["next"] = True, now + self.REPEAT
                self.post(name, True) if name in ("key2", "key3", "press") else self.post(name)
            elif name in ("up", "down") and info["held"] and now >= info["next"]:
                info["next"] = now + (self.FAST if now - info["at"] >= self.FAST_AFTER else self.REPEAT)
                self.post(name)

    def timeout(self):
        """How long the key thread may sleep: forever when nothing is pressed."""
        return 0.05 if self.down else None


def key_names(flip):
    """GPIO pin -> key name. flip turns the joystick's directions round, for a HAT held the other
    way up from Waveshare's labels (KEY1-3 keep their names: they are pressed by label)."""
    turn = {"up": "down", "down": "up", "left": "right", "right": "left"} if flip else {}
    return {pin: turn.get(name, name) for name, pin in KEYS.items()}


def start_keys(post, flip=False):
    """The HAT's keys and joystick (active low) through libgpiod edge events: the thread sleeps in
    the kernel until a key changes (lgpio's alert thread kept a Zero W core ~14% busy)."""
    import datetime
    import gpiod
    from gpiod import EdgeEvent
    from gpiod.line import Bias, Direction, Edge
    names = key_names(flip)
    request = gpiod.request_lines(GPIO_CHIP, consumer="fm1-bridge keys", config={
        tuple(names): gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH, bias=Bias.PULL_UP,
                                         debounce_period=datetime.timedelta(milliseconds=20))})
    timer = KeyTimer(post)

    def body():
        while True:
            if request.wait_edge_events(timer.timeout()):
                for event in request.read_edge_events():
                    name, now = names[event.line_offset], time.monotonic()
                    if event.event_type == EdgeEvent.Type.FALLING_EDGE:
                        timer.pressed(name, now)
                    else:
                        timer.released(name, now)
            timer.tick(time.monotonic())

    threading.Thread(target=body, daemon=True).start()
    return request


# ---------------------------------------------------------------- the app

class CachedFont(ImageFont.FreeTypeFont):
    """A TrueType font that remembers the text it has rendered and measured. FreeType takes ~4 ms per string on
    a Zero W, and a screen is ~10 strings that rarely change: without this a frame took ~55 ms to draw, and a key
    press waited for that. Kept to the last 512 strings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.masks, self.lengths = {}, {}

    def getmask2(self, text, mode="", *args, **kwargs):
        key = (text, mode, args, tuple(sorted(kwargs.items())))
        hit = self.masks.get(key)
        if hit is None:
            hit = super().getmask2(text, mode, *args, **kwargs)
            if len(self.masks) >= 512:
                self.masks.clear()
            self.masks[key] = hit
        return hit

    def getlength(self, text, mode="", *args, **kwargs):
        key = (text, mode, args, tuple(sorted(kwargs.items())))
        hit = self.lengths.get(key)
        if hit is None:
            hit = super().getlength(text, mode, *args, **kwargs)
            if len(self.lengths) >= 512:
                self.lengths.clear()
            self.lengths[key] = hit
        return hit


def font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")
    try:
        return CachedFont(path, size)
    except OSError:
        return ImageFont.load_default()


def fit(draw, text, fnt, width):
    """Shorten text with an ellipsis until it fits width pixels."""
    if draw.textlength(text, font=fnt) <= width:
        return text
    while text and draw.textlength(text + "…", font=fnt) > width:
        text = text[:-1]
    return text + "…"


def short_label(label):
    for suffix in (" Analog Stereo", " Stereo", " Mono"):
        if label.endswith(suffix):
            return label[:-len(suffix)]
    return label


class App(LibraryScreens, MeterScreens, MidiScreens, SampleEditor, WebLink):
    fit = staticmethod(fit)

    def __init__(self, display, state):
        self.display, self.state = display, state
        self.library_init()
        self.editor_init()
        self.meter_init()
        self.midi_init()
        self.web_init()
        self.events = queue.Queue()
        self.bt_lock = threading.Lock()       # one bluetoothctl operation at a time
        self.loop = Loopback(state["audio_rate"])
        self.mixer = Mixer(state["audio_rate"])        # a second FM-1, or a USB keyboard's sound: mixed
        self.metro_sent = None                # what the MIDI process was last told about the metronome
        self.source = self.sink = self.source_b = None
        self.others = []                      # other USB sound: a keyboard's own synth
        self.scan_now = False                 # a mixer level changed: apply it within a second
        self.pw = PwWatch()                   # PipeWire's objects, without a new connection each scan
        self.mix_lost = 0                     # scans in a row that found one source while mixing
        self.volume = None
        self.volume_lock = threading.Lock()   # one wpctl at a time, the newest press wins
        self.volume_job, self.volume_busy, self.volume_at = None, False, 0.0
        self.volume_shown = 0.0               # until when the big volume bar shows
        self.auto = True                      # reconnect the last headphones by itself
        self.connecting = False
        self.screen = "status"                # or "scan", "settings"
        self.back_to = "status"               # where left leaves the scan list for
        self.devices, self.cursor, self.scanning = [], 0, False
        self.midi = None                      # blemidi.MidiBridge, when BlueZ and mido are there
        self.wifi, self.next_wifi = None, 0.0
        self.battery, self.next_battery = None, 0.0   # the PiSugar's {"percent", "mv", "plugged"}, or None
        self.battery_warned, self.battery_empty = False, 0
        self.setting = 0                      # cursor on the settings page
        self.settings_menu = None             # None: the top list of sections; else a section (GROUPS)
        self.flash_paused = False
        self.no_source_since, self.healed_at = None, -1e9
        self.busy = None                      # text shown while a user action runs
        self.editing = None                   # "tempo" while up/down change the tempo in Settings
        self.taps, self.playing = [], False   # tap tempo times; Start/Stop sent by the box
        self.message, self.message_until = None, 0.0
        self.last_activity = time.monotonic()
        self.mode = "on"                      # "on", "dim", "saver"
        self.frame = self.shown = None
        self.f_title, self.f_body, self.f_small = font(12, True), font(11), font(9)
        self.f_big = font(26, True)          # the tuner's note

    def post(self, *event):
        self.events.put(event)

    def save_settings(self):
        save_state(self.state)

    # ---- background work

    def monitor(self):
        """Scanning PipeWire and the MIDI ports costs the Zero W ~0.25 s of CPU, so scan when sysfs
        shows a USB or Bluetooth change (then a few times quickly), otherwise only now and then."""
        next_scan = next_reconnect = 0.0
        fast_until, seen = 0.0, None
        self.pw.start()
        while True:
            now = time.monotonic()
            fingerprint = hardware_fingerprint()
            if fingerprint != seen:
                seen, fast_until = fingerprint, now + 12
                next_scan = min(next_scan, now + 1.5)       # PipeWire needs a moment to add its nodes
            if now >= self.next_wifi:
                self.wifi, self.next_wifi = wifi_enabled(), now + 60
            if now >= next_scan or self.scan_now or self.pw.changed.is_set():     # cheap now: no new connection
                self.scan_now = False
                self.pw.changed.clear()
                self.scan_devices()
                settled = self.source and self.sink and (not self.midi or self.midi.usb_label())
                next_scan = now + (2 if now < fast_until else 15 if settled else 6)
            if (not self.sink and self.auto and self.state["last_mac"] and self.screen == "status"
                    and now >= next_reconnect and self.bt_lock.acquire(blocking=False)):
                next_reconnect = now + RECONNECT_EVERY
                threading.Thread(target=self.reconnect_quietly, daemon=True).start()
            if now >= self.next_battery:
                self.battery, self.next_battery = battery.read(), now + 10
                self.battery_check()
            self.auto_backup()
            self.identify_fm1()
            self.web_publish()
            metro = (self.state["metronome"], self.sink and self.sink["name"], self.state["click"],
                     self.state["audio_rate"])
            if self.midi and metro != self.metro_sent:      # the metronome runs in the MIDI process
                self.midi.set_metronome(*metro)
                self.metro_sent = metro
            request = library.take_request()
            if request:
                self.post("request", request)                 # the web page wants a confirm here
            flashing = self.flash_running()
            if self.midi and flashing != self.flash_paused:     # no Bluetooth notes into the FM-1 mid-flash
                self.midi.pause(flashing)
                self.flash_paused = flashing
            time.sleep(1)

    def scan_devices(self):
        if self.midi:
            self.midi.poll()
        found = pw_nodes(self.state["source_match"], self.pw)
        if found is None:
            return                            # couldn't look: keep everything as it is
        (source, sink, source_b, others), objects = found
        volume = node_volume(objects, sink["id"]) if sink else None
        if sink and volume is None:
            volume = get_volume(sink["id"])
        if sink == self.sink and time.monotonic() - self.volume_at < 3:
            volume = self.volume              # a reading from before the last press landed
        changed = (source, sink, source_b, others) != (self.source, self.sink, self.source_b, self.others)
        if changed or volume != self.volume:
            self.source, self.sink, self.source_b, self.others, self.volume = source, sink, source_b, others, volume
            self.post("status", changed)
        channels = self.mix_channels()
        if len(channels) > 1:                 # more than one source: through the mixer, each at its level
            self.loop.ensure(None, None)
            self.mixer.ensure(sink and sink["name"],
                              [(key, node["name"]) for key, node in channels if self.mix_level(key) > 0])
            self.mixer.set_levels(objects, {key: self.mix_level(key) for key, _ in channels})
            self.mix_lost = 0
        elif self.mixer.running() and self.mix_lost < 1:
            self.mix_lost += 1                # a source gone: sure only on the next look (no dropout for nothing)
        else:                                 # the FM-1 alone: straight to the speaker, as ever
            self.mixer.stop()
            self.mix_lost = 0
            self.loop.ensure(source and source["name"], sink and sink["name"])
        self.heal_pipewire(source)

    def mix_channels(self):
        """[(key, node)] of the sound sources there are: "a", "b" (the FM-1s), "usb0"... (keyboards' own sound)"""
        found = [("a", self.source), ("b", self.source_b)] + [(f"usb{i}", n) for i, n in enumerate(self.others)]
        return [(key, node) for key, node in found if node]

    def mix_level(self, key):
        return int((self.state.get("mix") or {}).get("usb" if key.startswith("usb") else key, 100))

    def set_mix_level(self, key, level):
        level = max(0, min(MIX_MAX, int(level)))       # over 100: a quiet source (a keyboard's synth) boosted
        self.state["mix"] = dict(self.state.get("mix") or {}, **{key: level})
        self.save_settings()
        self.scan_now = True

    def live(self):
        return self.loop.running() or self.mixer.running()

    def capture_target(self):
        """what recordings and the meters listen to: the FM-1, or while mixing, what plays on the speaker"""
        if self.mixer.running() and self.mixer.sink:
            return "monitor:" + self.mixer.sink
        return self.source and self.source["name"]

    def heal_pipewire(self, source):
        """WirePlumber can lose the FM-1 when it reconnects quickly under another identity (as after a
        firmware update: app, update loader, app again). If Linux has the FM-1's sound card but PipeWire has
        had no FM-1 input for 20 s, restart WirePlumber (at most every 3 minutes, never mid-job)."""
        now = time.monotonic()
        if source or not alsa_has_card(self.state["source_match"]) or self.job_busy():
            self.no_source_since = None
            return
        self.no_source_since = self.no_source_since or now
        if now - self.no_source_since > 20 and now - self.healed_at > 180:
            self.healed_at, self.no_source_since = now, None
            run(["systemctl", "--user", "restart", "wireplumber"], timeout=30)

    def reconnect_quietly(self):
        try:
            self.connecting = True
            self.post("status", False)
            bt("connect", self.state["last_mac"], timeout=12)
        finally:
            self.connecting = False
            self.bt_lock.release()
            self.post("status", False)

    def job(self, label, work):
        """Run a Bluetooth action in the background with label on screen; work() returns (ok, text)."""
        if self.busy:
            return
        self.busy = label

        def body():
            try:
                with self.bt_lock:
                    ok, text = work()
            except Exception as e:            # keep the UI alive whatever bluetoothctl does
                ok, text = False, str(e)[:40]
            self.busy = None
            if text:
                self.say(text)
            self.post("status", True)

        threading.Thread(target=body, daemon=True).start()

    def say(self, text, seconds=3):
        self.message, self.message_until = text, time.monotonic() + seconds

    # ---- actions

    def start_scan(self):
        if self.busy or self.scanning:
            return
        if self.screen != "scan":
            self.back_to = self.screen        # left goes back where the scan was started from
        self.screen, self.devices, self.cursor, self.scanning = "scan", [], 0, True

        def body():
            devices = []
            try:
                with self.bt_lock:
                    devices = bt_scan()
            finally:
                self.devices, self.scanning = devices, False
            self.post("status", True)

        threading.Thread(target=body, daemon=True).start()

    def pair_selected(self):
        device = self.devices[self.cursor]

        def work():
            ok, text = bt_pair(device["mac"])
            if ok:
                self.state.update(last_mac=device["mac"], last_name=device["name"])
                save_state(self.state)
                self.auto, self.screen = True, "status"
            return ok, text

        self.job("Pairing " + device["name"], work)

    def reconnect(self):
        if not self.state["last_mac"]:
            self.say("No headphones yet: KEY1")
            return
        self.auto = True
        self.job("Connecting", lambda: bt_connect(self.state["last_mac"]))

    def disconnect(self):
        if not self.state["last_mac"]:
            return
        self.auto = False

        def work():
            bt("disconnect", self.state["last_mac"], timeout=10)
            return True, "Disconnected (KEY2 = reconnect)"

        self.job("Disconnecting", work)

    def volume_step(self, step):
        if not self.sink:
            return
        if self.volume is None:               # not read yet: a relative step
            threading.Thread(target=change_volume, args=(self.sink["id"], step), daemon=True).start()
            return
        self.volume_to(volume_snap(self.volume, step))
        self.volume_shown = time.monotonic() + 1.2      # the big volume bar

    def volume_to(self, percent):
        """the speaker's volume, from the keys or the web page: shown at once, set in the background"""
        self.volume, self.volume_at = percent, time.monotonic()
        with self.volume_lock:
            self.volume_job = (self.sink["id"], percent)
            start, self.volume_busy = not self.volume_busy, True
        if start:
            threading.Thread(target=self.volume_worker, daemon=True).start()

    def volume_worker(self):
        while True:
            with self.volume_lock:
                job, self.volume_job = self.volume_job, None
                if job is None:
                    self.volume_busy = False
                    return
            set_volume(*job)

    def settings_rows(self):
        """(key, label, value) for the settings page, in cursor order."""
        speaker = (short_label(self.sink["label"]) if self.sink else self.state["last_name"]) or "none"
        audio = "on" if self.sink else "..." if self.connecting else "off"
        midi = "n/a" if not self.midi else "on" if self.midi.enabled else "off"
        left = self.midi.pairing_until - time.monotonic() if self.midi else 0
        wifi = {True: "on", False: "off", None: "?"}[self.wifi]
        rate = f"{self.state['audio_rate'] / 1000:g} kHz"
        tempo = f"{self.state['bpm']} BPM"
        onoff = lambda on: "on" if on else "off"      # noqa: E731
        if self.settings_menu == "sloop":     # SLOOP 2.3+'s editor protocol
            which = ([("library_fm1", "FM-1", self.state["library_fm1"])]       # only with two FM-1s plugged in
                     if self.midi and getattr(self.midi, "usb_b", None) else [])
            return which + [("backup", "Back up now", ""), ("restore", "Restore", "›"),
                            ("project", "Load project", "›"), ("sample", "Load sample", "›"),
                            ("sample_level", "Sample level", self.state["sample_level"]),
                            ("auto", "Auto backup", self.auto_label(self.auto_mode())), ("sd", "Import SD card", "")]
        if self.settings_menu == "fm1":       # the FM-1 itself, whatever it runs
            return [("fw_info", "Runs", self.fw_label or "?"), ("firmware", "Install firmware", "›")]
        if self.settings_menu == "play":      # what changes while playing
            keys = self.keys_route()
            return [("metronome", "Metronome", onoff(self.state["metronome"])),
                    ("tempo", "Tempo", f"± {tempo}" if self.editing == "tempo" else tempo),
                    ("clock", "Clock", self.state["clock_mode"]), ("arp_on", "Arp", onoff(keys["arp_on"])),
                    ("chord_on", "Chord", onoff(keys["chord_on"])), ("click", "Click level", self.state["click"])]
        if self.settings_menu == "audio":
            names = {"a": "FM-1 A level" if self.source_b else "FM-1 level", "b": "FM-1 B level",
                     "usb": "USB keys level"}
            keys = list(dict.fromkeys("usb" if k.startswith("usb") else k for k, _ in self.mix_channels()))
            mix = [(f"mix_{k}", names[k], ("± " if self.editing == f"mix_{k}" else "") + f"{self.mix_level(k)}%")
                   for k in keys] if len(self.mix_channels()) > 1 else []
            return [("speaker", "Speaker", speaker), ("audio", "BT audio", audio), ("rate", "Audio rate", rate)] + mix
        if self.settings_menu == "midi":
            keys = getattr(self.midi, "controllers", None) or []
            return [("midi", "BLE MIDI", midi), ("pairing", "Pairing", f"open {int(left)}s" if left > 0 else "closed"),
                    ("usbkeys", "USB keys", str(len(keys)) if keys else "none"),
                    ("routes", "MIDI routes", "›"), ("presets", "Route presets", "›")]
        return [("g_play", "Playing", "›"), ("g_audio", "Audio", "›"), ("g_midi", "MIDI", "›"),
                ("recording", "Recordings", "›"), ("editor", "Sample editor", "›"), ("wifi", "Wi-Fi", wifi),
                ("g_sloop", "Sloop", "›"),
                ("g_fm1", "FM-1", "›")]

    def change_setting(self):
        key = self.settings_rows()[self.setting][0]
        if key in GROUPS:
            self.settings_menu, self.setting = GROUPS[key][0], 0
        elif key in LIBRARY_KEYS:
            self.library_choose(key)
        elif key == "speaker" or (key == "audio" and not self.state["last_mac"]):
            self.start_scan()                 # pick and pair a speaker or headphones
        elif key == "audio":
            self.disconnect() if self.sink else self.reconnect()
        elif key == "rate":
            rate = 44100 if self.state["audio_rate"] == 48000 else 48000
            self.state["audio_rate"] = rate
            self.save_settings()
            self.loop.reopen(rate)
            self.mixer.reopen(rate)
            self.say(f"{rate / 1000:g} kHz: audio restarts")
        elif key == "clock":
            self.set_clock("box" if self.state["clock_mode"] == "follow" else "follow")
        elif key == "tempo" or key.startswith("mix_"):
            self.editing = key                 # up/down change it
        elif key == "metronome":
            self.state["metronome"] = not self.state["metronome"]
            self.save_settings()
            if self.state["metronome"] and not self.sink:
                self.say("Metronome: connect a speaker")
        elif key == "library_fm1":
            self.state["library_fm1"] = "B" if self.state["library_fm1"] == "A" else "A"
            self.save_settings()
            library.forget_firmware()          # IN shows what the chosen FM-1 runs: ask it
            self.fw_asked, self.fw_seen = False, time.monotonic() - 5
            self.say(f"Sloop jobs go to FM-1 {self.state['library_fm1']}", 2)
        elif key == "sample_level":
            levels = ("-6 dB", "-3 dB", "full")
            now = self.state["sample_level"] if self.state["sample_level"] in levels else "-6 dB"
            self.state["sample_level"] = levels[(levels.index(now) + 1) % 3]
            self.save_settings()
        elif key == "click":
            levels = ("low", "mid", "high")
            self.state["click"] = levels[(levels.index(self.state["click"]) + 1) % 3]
            self.save_settings()
        elif key in ("arp_on", "chord_on"):    # the keyboard's arp and chord: on/off in one press
            self.toggle_keys(key)
        elif key == "routes":
            self.open_matrix()
        elif key == "presets":
            self.open_presets()
        elif key == "midi" and self.midi:
            on = not self.midi.enabled
            self.midi.set_enabled(on)
            self.state["midi_enabled"] = on
            save_state(self.state)
        elif key == "usbkeys":                 # USB MIDI controllers on a hub, found by themselves
            keys = getattr(self.midi, "controllers", None) or []
            self.say(", ".join(keys) if keys else "Plug a USB keyboard into the hub", 3)
        elif key == "pairing" and self.midi:
            self.midi.open_pairing()
            self.say("Pair from your device now (2 min)")
        elif key == "wifi" and self.wifi is not None:
            on = not self.wifi
            self.wifi = on
            threading.Thread(target=set_wifi, args=(on,), daemon=True).start()
            self.say("Wi-Fi on: SSH works again" if on else "Wi-Fi off (stays off)")

    # ---- MIDI clock

    def set_clock(self, mode=None, bpm=None):
        self.state["clock_mode"] = mode or self.state["clock_mode"]
        self.state["bpm"] = int(min(BPM_RANGE[1], max(BPM_RANGE[0], round(bpm or self.state["bpm"]))))
        self.save_settings()
        if self.midi:
            self.midi.set_clock(self.state["clock_mode"], self.state["bpm"])

    def tap(self, now):
        """Tap tempo: from the second tap on, the average of the last taps; makes the box the master."""
        if self.taps and now - self.taps[-1] > TAP_GAP:
            self.taps = []
        self.taps = (self.taps + [now])[-5:]
        if len(self.taps) < 2:
            return
        beat = (self.taps[-1] - self.taps[0]) / (len(self.taps) - 1)
        self.set_clock("box", 60 / beat)
        self.say(f"Box clock {self.state['bpm']} BPM", 1.5)

    def start_stop(self):
        if not self.midi:
            return
        self.playing = not self.playing
        self.midi.transport("start" if self.playing else "stop")
        self.say("Start" if self.playing else "Stop", 1)

    def clock_text(self):
        """Box clock: "120 BPM"; following: "follow", or "follow 120" while clock arrives; ▶ in front while
        playing. None without MIDI."""
        if not self.midi:
            return None
        clock = getattr(self.midi, "clock", None)
        if self.state["clock_mode"] != "box":
            text = f"follow {clock['bpm']}" if clock else "follow"
        else:
            text = f"{clock['bpm']} BPM" if clock else f"{self.state['bpm']} BPM"
        return ("▶ " if clock and clock.get("playing") else "") + text

    def battery_check(self):
        """On battery: a warning at battery.LOW, and at battery.EMPTY (twice in a row, so one odd reading
        doesn't) a safe shutdown before the PiSugar cuts the power and the SD card is caught mid-write."""
        b = self.battery
        if not b or b["plugged"]:
            self.battery_warned, self.battery_empty = False, 0
            return
        if b["percent"] <= battery.EMPTY:
            self.battery_empty += 1
            if self.battery_empty == 2:
                self.post("battery", "empty")
            return
        self.battery_empty = 0
        if b["percent"] <= battery.LOW and not self.battery_warned:
            self.battery_warned = True
            self.post("battery", "low")
        elif b["percent"] > battery.LOW + 5:
            self.battery_warned = False

    def shutdown(self):
        self.busy = "Shutting down"
        self.render(force=True)
        self.recorder.stop()                  # finishes the WAV file
        self.player.stop()
        self.loop.stop()
        self.mixer.stop()
        time.sleep(1.5)
        self.display.power(False)
        run(["sudo", "-n", "systemctl", "poweroff"], timeout=10)

    # ---- input

    def on_key(self, key, held=False):
        self.last_activity = time.monotonic()
        if self.mode != "on":                 # the first press only wakes the screen
            self.wake()
            return
        if key == "press" and held and self.screen not in ("confirm", "status"):
            return                            # holding press only confirms (the press itself already acted)
        if key == "key3":
            if self.flash_running():
                self.say("Wait: the FM-1 is being flashed")
            elif held:
                self.shutdown()
            else:
                self.say("Hold KEY3 to power off")
            return
        if (self.meter_key(key, held) or self.library_key(key, held) or self.midi_key(key, held)
                or self.editor_key(key, held)):
            return
        if self.screen == "status":
            if key == "key1":
                self.start_scan()
            elif key == "key2":
                self.disconnect() if held else self.reconnect()
            elif key in ("up", "down"):
                self.volume_step(2 if key == "up" else -2)
            elif key == "press":
                self.start_stop() if held else self.tap(time.monotonic())
            elif key == "right":
                self.screen, self.setting, self.next_wifi = "settings", 0, 0.0    # fresh Wi-Fi state
                self.settings_menu = None
            elif key == "left":
                self.open_meters()
        elif self.screen == "settings" and self.editing and self.editing.startswith("mix_"):
            if key in ("up", "down"):
                which = self.editing[4:]
                self.set_mix_level(which, self.mix_level(which) + (5 if key == "up" else -5))
            elif key in ("press", "left", "right"):
                self.editing = None
        elif self.screen == "settings" and self.editing == "tempo":
            if key in ("up", "down"):
                self.set_clock(bpm=self.state["bpm"] + (1 if key == "up" else -1))
            elif key in ("press", "left", "right"):
                self.editing = None
        elif self.screen == "settings":
            if key in ("up", "down"):
                self.setting = (self.setting + (1 if key == "down" else -1)) % len(self.settings_rows())
            elif key in ("press", "right"):
                self.change_setting()
            elif key == "left" and self.settings_menu:      # back up to the top list, on the group
                group = next(k for k, (name, _) in GROUPS.items() if name == self.settings_menu)
                self.settings_menu = None
                self.setting = [row[0] for row in self.settings_rows()].index(group)
            elif key == "left":
                self.screen = "status"
        else:
            if key in ("up", "down") and self.devices:
                self.cursor = (self.cursor + (1 if key == "down" else -1)) % len(self.devices)
            elif key in ("press", "right") and self.devices and not self.scanning:
                self.pair_selected()
            elif key == "key1":
                self.start_scan()
            elif key == "left" and not self.busy:
                self.screen = self.back_to

    def wake(self):
        if self.mode != "on":
            self.display.power(True)
            self.display.contrast(self.state["contrast"])
            self.mode = "on"

    def screensaver(self):
        idle = time.monotonic() - self.last_activity
        if self.busy or self.scanning:
            idle = 0
        if idle > OFF_AFTER and self.mode != "saver":
            self.display.contrast(1)
            self.mode = "saver"               # one dim word that moves: alive, and no burn-in
        elif DIM_AFTER < idle <= OFF_AFTER and self.mode == "on":
            self.display.contrast(1)
            self.mode = "dim"

    # ---- drawing

    def view(self, now):
        """Everything the screen shows right now. Drawing a frame takes ~75 ms on a Zero W, so
        render() only draws when this changes."""
        if self.mode == "saver":
            return ("saver", int(now // 20), self.live(), bool(self.midi and self.midi.peer),
                    self.recorder.recording())
        return (self.screen, self.busy and (self.busy, int(now * 2) % 4),
                self.message if now < self.message_until else None, self.live(),
                self.source and self.source["label"], self.fw_label, self.sink and self.sink["label"], self.connecting,
                self.state["last_name"], self.auto, self.volume, now < self.volume_shown,
                self.battery and (self.battery["percent"], self.battery["plugged"]),
                self.midi_text(), bool(self.midi and now - self.midi.activity() < 0.4), self.clock_text(),
                tuple(d["name"] for d in self.devices), self.cursor, self.scanning,
                tuple(self.settings_rows()) if self.screen == "settings" else None, self.setting, self.settings_menu,
                self.library_view(now), self.meter_view(now), self.midi_view(),
                self.editor_view() if self.screen == "editor" else None)

    def render(self, force=False):
        now = time.monotonic()
        view = self.view(now)
        if view == self.shown and not force:
            return
        self.shown = view
        img = self.meter_image(now) if self.screen == "meter" and self.mode != "saver" else Image.new("1", (128, 64))
        d = ImageDraw.Draw(img)
        if self.mode == "saver" and not force:
            self.draw_saver(d, now)
        else:
            {"scan": self.draw_scan, "settings": self.draw_settings,
             "pick": self.draw_pick, "confirm": self.draw_confirm, "job": self.draw_job,
             "meter": lambda d: None, "matrix": self.draw_matrix, "route": self.draw_route,
             "player": self.draw_player, "editor": self.draw_editor,
             "chanmap": self.draw_chanmap, "presets": self.draw_presets}.get(
                self.screen, self.draw_status)(d)
            if self.screen == "status" and now < self.volume_shown and self.volume is not None:
                self.draw_volume(d)
            if self.busy:
                dots = "." * (int(now * 2) % 4)
                self.draw_box(d, self.busy + dots)
            elif self.message and now < self.message_until:
                self.draw_box(d, self.message)
        data = img.tobytes()
        if force or data != self.frame:
            self.frame = data
            self.display.show(img)

    def draw_status(self, d):
        live = self.live()
        d.text((0, 0), "FM-1 > BT", font=self.f_title, fill=1)
        tag = "LIVE" if live else "idle"
        if self.recorder.recording():
            tag, live = "REC " + meters.clock(self.recorder.seconds()), True
        w = d.textlength(tag, font=self.f_small)
        if live:
            d.rectangle((124 - w, 0, 127, 11), fill=1)
        d.text((126 - w, 1), tag, font=self.f_small, fill=0 if live else 1)
        if self.battery:
            self.draw_battery(d, 122 - w)
        d.line((0, 14, 127, 14), fill=1)
        if self.sink:
            out = short_label(self.sink["label"])
        elif self.connecting:
            out = "connecting…"
        elif self.state["last_name"]:
            out = self.state["last_name"] + (" (off)" if self.auto else " (paused)")
        else:
            out = "none: press KEY1"
        fm1 = (self.fw_label or short_label(self.source["label"])) if self.source else "FM-1 not found"
        rows = (("IN", fm1 + (" + B" if self.source and self.source_b else "")),
                ("OUT", out),
                ("MIDI", self.midi_text()))
        for i, (name, value) in enumerate(rows):
            y = 17 + i * 12
            d.text((0, y + 1), name, font=self.f_small, fill=1)
            d.text((26, y), fit(d, value, self.f_body, 94), font=self.f_body, fill=1)
        if self.midi and time.monotonic() - self.midi.activity() < 0.4:
            d.ellipse((123, 44, 127, 48), fill=1)                     # MIDI traffic
        vol = f"VOL {self.volume}%" if self.volume is not None else "VOL --"
        d.text((0, 53), vol, font=self.f_small, fill=1)
        running = self.job_busy()
        if running:                           # a library job (the boot backup, say) and how far it is
            percent = running.get("percent")
            hint = running.get("job", "job") + (f" {int(percent)}%" if percent is not None else "…")
        else:
            hint = self.clock_text() or "settings >"
        d.text((128 - d.textlength(hint, font=self.f_small), 53), hint, font=self.f_small, fill=1)

    def draw_battery(self, d, right):
        """the PiSugar's charge between the title and the LIVE tag: a battery (a bolt in it while plugged in)
        and its percent (the number alone: there's room for 32 pixels); left out where there isn't room (the
        REC timer is wider)"""
        b = self.battery
        text = str(b["percent"])
        left = right - d.textlength(text, font=self.f_small) - 12
        if left < 70:                          # "FM-1 > BT" ends at 68
            return
        x, y = int(left), 3
        d.rectangle((x, y, x + 9, y + 6), outline=1)
        d.rectangle((x + 10, y + 2, x + 10, y + 4), fill=1)                      # the nub
        if b["plugged"]:
            d.polygon([(x + 5, y + 1), (x + 2, y + 4), (x + 5, y + 4), (x + 4, y + 6), (x + 8, y + 2),
                       (x + 5, y + 2)], fill=1)
        elif b["percent"]:
            d.rectangle((x + 2, y + 2, x + 2 + round(5 * b["percent"] / 100), y + 4), fill=1)
        d.text((x + 12, 1), text, font=self.f_small, fill=1)

    def draw_saver(self, d, now):
        """After OFF_AFTER idle: "FM-1 LIVE" (or idle) in small type, moving every 20 s."""
        text = "FM-1 " + ("LIVE" if self.live() else "idle")
        if self.midi and self.midi.peer:
            text += " +MIDI"
        if self.recorder.recording():
            text += " REC"
        w = int(d.textlength(text, font=self.f_small))
        step = int(now // 20)
        x, y = (step * 37) % (128 - w), (step * 23) % 54
        d.text((x, y), text, font=self.f_small, fill=1)

    def midi_text(self):
        if not self.midi:
            return "not available"
        if self.midi.error:
            return "BT error"
        if not self.midi.enabled:
            return "off"
        usb = "" if self.midi.usb_label() else " (no FM-1)"
        keys = getattr(self.midi, "controllers", None) or []
        if keys and not self.midi.peer:                   # USB keyboards play the FM-1 without Bluetooth
            return (keys[0] if len(keys) == 1 else f"{len(keys)} USB keys") + usb
        both = f"{len(keys)} USB + " if keys else ""      # first: a long Bluetooth name gets cut short on the right
        return (both + self.midi.peer + usb) if self.midi.peer else "waiting" + usb

    def draw_volume(self, d):
        """the volume, big, over the home screen for a moment after up/down"""
        d.rectangle((6, 15, 121, 51), fill=0, outline=1)
        text = f"Volume {self.volume}%"
        d.text(((128 - d.textlength(text, font=self.f_body)) / 2, 18), text, font=self.f_body, fill=1)
        d.rectangle((12, 34, 115, 46), outline=1)
        if self.volume:
            d.rectangle((14, 36, 14 + round(99 * self.volume / 100), 44), fill=1)

    def draw_settings(self, d):
        rows = self.settings_rows()
        title = next((t for name, t in GROUPS.values() if name == self.settings_menu), "Settings")
        d.text((0, 0), title, font=self.f_title, fill=1)
        where = f"{self.setting + 1}/{len(rows)}"
        d.text((127 - d.textlength(where, font=self.f_small), 2), where, font=self.f_small, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        top = min(max(0, self.setting - 1), max(0, len(rows) - 3))      # three rows, scrolling
        for i, (_, name, value) in enumerate(rows[top:top + 3]):
            y, selected = 16 + i * 12, top + i == self.setting
            if selected:
                d.rectangle((0, y, 127, y + 11), fill=1)
            d.text((2, y), name, font=self.f_body, fill=0 if selected else 1)
            value = fit(d, value, self.f_body, 70)
            w = d.textlength(value, font=self.f_body)
            d.text((125 - w, y), value, font=self.f_body, fill=0 if selected else 1)
        hint = ("up/down = tempo  < done" if self.editing == "tempo" else
                "up/down = level  < done" if self.editing else "press = change   < back")
        d.text((0, 53), hint, font=self.f_small, fill=1)

    def draw_scan(self, d):
        d.text((0, 0), "BT audio out", font=self.f_title, fill=1)
        d.line((0, 14, 127, 14), fill=1)
        if self.scanning:
            d.text((0, 18), "Searching…", font=self.f_body, fill=1)
            d.text((0, 32), "Put headphones in", font=self.f_small, fill=1)
            d.text((0, 42), "pairing mode now", font=self.f_small, fill=1)
        elif not self.devices:
            d.text((0, 18), "None found", font=self.f_body, fill=1)
            d.text((0, 34), "K1 scan again", font=self.f_small, fill=1)
            d.text((0, 44), "< back", font=self.f_small, fill=1)
        else:
            top = min(max(0, self.cursor - 1), max(0, len(self.devices) - 3))
            for row, device in enumerate(self.devices[top:top + 3]):
                y, selected = 16 + row * 12, top + row == self.cursor
                if selected:
                    d.rectangle((0, y, 127, y + 11), fill=1)
                d.text((2, y), fit(d, device["name"], self.f_body, 122), font=self.f_body, fill=0 if selected else 1)
            d.text((0, 53), "press = pair   K1 = rescan", font=self.f_small, fill=1)

    def draw_box(self, d, text):
        words, lines, line = text.split(), [], ""
        for word in words:
            trial = (line + " " + word).strip()
            if d.textlength(trial, font=self.f_body) <= 116 or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines = (lines + [line])[:3]
        h = 6 + 12 * len(lines)
        top = (64 - h) // 2
        d.rectangle((2, top, 125, top + h), fill=0, outline=1)
        for i, l in enumerate(lines):
            l = fit(d, l, self.f_body, 116)
            d.text(((128 - d.textlength(l, font=self.f_body)) / 2, top + 3 + 12 * i), l, font=self.f_body, fill=1)

    # ---- main loop

    def run(self):
        threading.Thread(target=self.monitor, daemon=True).start()   # starts pw-loopback: keeps priority
        if hasattr(os, "nice"):
            os.nice(5)      # Linux: this thread only (drawing, keys), and threads it starts later; MIDI and
                            # audio run in processes of their own at real-time priority
        while True:
            try:
                # 4 Hz: the MIDI dot and countdowns; 10 Hz for the meters
                event = self.events.get(timeout=0.1 if self.screen == "meter" and self.mode != "saver" else 0.25)
            except queue.Empty:
                event = None
            self.web_commands()                       # the web page's settings (a file check: cheap)
            if event and event[0] in KEYS:
                self.on_key(event[0], len(event) > 1 and event[1])
            elif event and event[0] == "status" and event[1]:
                self.last_activity = time.monotonic()     # connects and disconnects wake the screen
                self.wake()
            elif event and event[0] == "request":
                self.web_request(event[1])
            elif event and event[0] == "battery":
                self.last_activity = time.monotonic()
                self.wake()
                if event[1] == "empty":
                    self.say("Battery empty: shutting down", 5)
                    self.shutdown()
                else:
                    self.say(f"Battery low: {self.battery['percent']}%", 4)
            self.screensaver()
            self.render()


def test_screen(display):
    img = Image.new("1", (128, 64))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 127, 63), outline=1)
    d.polygon(((64, 4), (54, 18), (74, 18)), fill=1)
    d.text((40, 22), "TOP", font=font(12, True), fill=1)
    d.text((6, 40), "KEY1-3 on the left", font=font(9), fill=1)
    d.text((6, 50), "joystick on the right", font=font(9), fill=1)
    display.show(img)


def splash(display):
    """Drawn as soon as the OLED is open: at boot the rest of start-up takes a while on a Zero W."""
    img = Image.new("1", (128, 64))
    d = ImageDraw.Draw(img)
    for y, text, f in ((16, "FM-1 Companion", font(12, True)), (38, "Starting...", font(11))):
        d.text(((128 - d.textlength(text, font=f)) / 2, y), text, font=f, fill=1)
    display.show(img)


def main():
    state = load_state()
    sim, test = "--sim" in sys.argv, "--test-screen" in sys.argv
    if sim:
        display = SimDisplay(sys.argv[sys.argv.index("--sim") + 1])
    else:
        display = SH1106(state["rotate"], state["contrast"])
    if test:
        test_screen(display)
        time.sleep(15)
        return
    splash(display)
    midi = None
    if not sim:
        # MIDI gets its own process, forked before any thread exists (App.run lowers the drawing
        # thread's priority so the kernel runs MIDI and audio first)
        import blemidi
        midi = blemidi.MidiProcess(state["source_match"], state["midi_enabled"])
        midi.start()
        midi.set_clock(state["clock_mode"], state["bpm"])
        midi.set_routes(state["routes"])
    app = App(display, state)
    app.midi = midi
    if not sim:
        flip = state["flip_joystick"]
        if flip is None:                      # follow the screen: rotate 0 is upside down to Waveshare's labels
            flip = state["rotate"] == 0
        app.keys = start_keys(app.post, flip)
    try:
        app.run()
    finally:
        app.recorder.stop()
        app.loop.stop()


if __name__ == "__main__":
    main()
