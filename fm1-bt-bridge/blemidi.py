"""Bluetooth LE MIDI for the FM-1 Companion.

The Pi appears as a BLE MIDI device ("FM-1 Companion") that phones, tablets and computers connect to,
and MIDI passes both ways between it and the FM-1's USB MIDI port:

  phone / iPad / Mac  <--BLE MIDI-->  Pi  <--USB MIDI (ALSA sequencer, via mido)-->  FM-1

The packet coding (encode / strip_timestamps) is plain Python; the BlueZ side needs python3-dbus
and python3-gi, the USB side python3-mido and python3-rtmidi.
"""
import re
import threading
import time

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_IO = "7772e5db-3868-4112-a1a9-f2669d106bf3"
PACKET = 20                 # ATT payload with the minimum MTU: safe with every central
DEVICE_NAME = "FM-1 Companion"
PAIRING_SECONDS = 120
ADDRESS = re.compile(r"([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}")     # what BlueZ calls a device before its name


# ---------------------------------------------------------------- BLE MIDI packets

def encode(messages, now_ms):
    """Complete MIDI messages (bytes each, SysEx with its F0 and F7) -> BLE MIDI packets."""
    ts = now_ms & 0x1FFF
    header, stamp = 0x80 | (ts >> 7), 0x80 | (ts & 0x7F)
    packets, cur = [], bytearray([header])

    def flush():
        if len(cur) > 1:
            packets.append(bytes(cur))
            cur[:] = bytes([header])

    for m in messages:
        if not m:
            continue
        if m[0] == 0xF0:
            flush()
            cur += bytes([stamp, 0xF0])
            for b in m[1:-1] if m[-1] == 0xF7 else m[1:]:
                if len(cur) == PACKET:
                    flush()                         # continuation packet: header, then data only
                cur.append(b)
            if len(cur) + 2 > PACKET:
                flush()
            cur += bytes([stamp, 0xF7])
            continue
        if len(cur) + 1 + len(m) > PACKET:
            flush()
        cur.append(stamp)
        cur += bytes(m)
    flush()
    return packets


def strip_timestamps(packet):
    """One BLE MIDI packet -> its plain MIDI bytes (running status kept; feed them to a parser)."""
    out = bytearray()
    if len(packet) < 2 or not packet[0] & 0x80:
        return bytes(out)
    i = 1
    while i < len(packet):
        b = packet[i]
        if b & 0x80:
            # a timestamp: before a status byte, or before running-status data
            if i + 1 < len(packet) and packet[i + 1] & 0x80:
                out.append(packet[i + 1])
                i += 2
            else:
                i += 1
        else:
            out.append(b)
            i += 1
    return bytes(out)


class MidiStream:
    """Plain MIDI bytes -> complete messages, with running status (BLE senders use it), real-time
    bytes anywhere, and SysEx split across packets."""

    def __init__(self):
        self.status, self.data, self.sysex = None, bytearray(), None

    @staticmethod
    def length(status):
        if 0xC0 <= status <= 0xDF or status in (0xF1, 0xF3):
            return 1
        return 2

    def feed(self, data):
        out = []
        for b in data:
            if b >= 0xF8:                               # real-time: passes through anything
                out.append(bytes([b]))
                continue
            if self.sysex is not None:
                if b == 0xF7:
                    out.append(bytes(self.sysex) + b"\xf7")
                    self.sysex = None
                    continue
                if b < 0x80:
                    self.sysex.append(b)
                    continue
                self.sysex = None                       # a status byte aborts the SysEx
            if b == 0xF0:
                self.sysex, self.status = bytearray([0xF0]), None
            elif b & 0x80:
                self.status, self.data = b, bytearray()
                if b in (0xF6, 0xF7):                   # tune request; a stray end of SysEx
                    if b == 0xF6:
                        out.append(b"\xf6")
                    self.status = None
            elif self.status is not None:
                self.data.append(b)
                if len(self.data) == self.length(self.status):
                    out.append(bytes([self.status]) + bytes(self.data))
                    self.data = bytearray()
                    if self.status >= 0xF0:             # system common: no running status
                        self.status = None
        return out


# ---------------------------------------------------------------- the FM-1's USB MIDI

FM1_IDS = ("0x1209:0x01", "pid.codes")    # the FM-1's USB ID, its ALSA name when its own name couldn't be read


class UsbMidi:
    """The FM-1's USB MIDI ports through mido, reopened when the FM-1 comes and goes. Found by its name (or USB
    ID) only: anything else on a USB hub is a controller (UsbControllers), never taken for the FM-1."""

    SKIP = ("through", "rtmidi", "pipewire", "midi-bridge", "bluetooth")

    def __init__(self, match, on_message):
        import mido
        self.mido = mido
        self.match, self.on_message = tuple(m.lower() for m in match) + FM1_IDS, on_message
        self.name, self.inp, self.out = None, None, None
        self.stream = MidiStream()
        self.last_activity = 0.0
        self.lock = threading.Lock()

    def pick(self, names):
        names = [n for n in names if not any(s in n.lower() for s in self.SKIP)]
        for n in names:
            if any(m in n.lower() for m in self.match):
                return n
        return None

    def poll(self):
        try:
            want_in = self.pick(self.mido.get_input_names())
            want_out = self.pick(self.mido.get_output_names())
        except Exception:
            want_in = want_out = None
        if want_in == self.name and (want_in is None or self.out is not None):
            return
        self.close()
        if want_in and want_out:
            try:
                self.out = self.mido.open_output(want_out)
                self.inp = self.mido.open_input(want_in, callback=self.received)
                self.name = want_in
            except Exception:
                self.close()

    def received(self, msg):
        self.last_activity = time.monotonic()
        self.on_message(bytes(msg.bytes()))

    def send_bytes(self, data):
        """Plain MIDI bytes (running status allowed) to the FM-1."""
        with self.lock:
            messages = self.stream.feed(data)
        self.send_messages(messages)

    def send_messages(self, messages):
        """Complete MIDI messages (bytes each) to the FM-1."""
        out = self.out
        if out is None:
            return
        with self.lock:
            for raw in messages:
                try:
                    out.send(self.mido.Message.from_bytes(list(raw)))
                    self.last_activity = time.monotonic()
                except (ValueError, OSError):       # a malformed message, or the FM-1 just left
                    continue

    def label(self):
        """The port's name for the screen: "Felucca:Felucca MIDI 1 24:0" -> "Felucca"."""
        return self.name.split(":")[0] if self.name else None

    def close(self):
        for port in (self.inp, self.out):
            try:
                if port:
                    port.close()
            except Exception:
                pass
        self.name, self.inp, self.out = None, None, None


# ---------------------------------------------------------------- BlueZ: GATT server, advertisement, agent

def start_bluez(bridge):
    """Build the D-Bus objects and run their GLib main loop (blocks: call from a thread)."""
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    BLUEZ, OM, PROPS = "org.bluez", "org.freedesktop.DBus.ObjectManager", "org.freedesktop.DBus.Properties"
    SERVICE, CHRC = "org.bluez.GattService1", "org.bluez.GattCharacteristic1"
    ADV, AGENT, ADAPTER = "org.bluez.LEAdvertisement1", "org.bluez.Agent1", "org.bluez.Adapter1"
    ROOT = "/org/fm1bridge"

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    class Characteristic(dbus.service.Object):
        def __init__(self):
            self.path = ROOT + "/service0/char0"
            self.notifying = False
            super().__init__(bus, self.path)

        def props(self):
            return {CHRC: {"UUID": MIDI_IO, "Service": dbus.ObjectPath(ROOT + "/service0"),
                           "Flags": dbus.Array(["read", "write", "write-without-response", "notify"],
                                               signature="s")}}

        @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props()[CHRC]

        @dbus.service.method(CHRC, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            return dbus.Array([], signature="y")    # BLE MIDI: reads return no payload

        @dbus.service.method(CHRC, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            bridge.from_ble(bytes(value), options.get("device"))

        @dbus.service.method(CHRC)
        def StartNotify(self):
            self.notifying = True
            GLib.idle_add(lambda: (bridge.subscribed(True), False)[1])    # after replying to bluetoothd

        @dbus.service.method(CHRC)
        def StopNotify(self):
            self.notifying = False
            bridge.subscribed(False)

        @dbus.service.signal(PROPS, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify(self, packet):
            if self.notifying:
                self.PropertiesChanged(CHRC, {"Value": dbus.Array([dbus.Byte(b) for b in packet], signature="y")}, [])

    class Service(dbus.service.Object):
        def __init__(self):
            self.path = ROOT + "/service0"
            super().__init__(bus, self.path)

        def props(self):
            return {SERVICE: {"UUID": MIDI_SERVICE, "Primary": True}}

        @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.props()[SERVICE]

    class Application(dbus.service.Object):
        def __init__(self, service, chrc):
            self.path, self.service, self.chrc = ROOT, service, chrc
            super().__init__(bus, self.path)

        @dbus.service.method(OM, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            return {dbus.ObjectPath(self.service.path): self.service.props(),
                    dbus.ObjectPath(self.chrc.path): self.chrc.props()}

    class Advertisement(dbus.service.Object):
        def __init__(self):
            self.path = ROOT + "/adv0"
            super().__init__(bus, self.path)

        @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return {"Type": "peripheral", "LocalName": DEVICE_NAME,
                    "ServiceUUIDs": dbus.Array([MIDI_SERVICE], signature="s")}

        @dbus.service.method(ADV)
        def Release(self):
            pass

    class Agent(dbus.service.Object):
        """Accepts "just works" pairing. Incoming pairing is only possible while the pairing
        window is open (the adapter's Pairable), so this does not let anyone pair at any time."""

        def __init__(self):
            self.path = ROOT + "/agent"
            super().__init__(bus, self.path)

        @dbus.service.method(AGENT)
        def Release(self):
            pass

        @dbus.service.method(AGENT, in_signature="os")
        def AuthorizeService(self, device, uuid):
            pass

        @dbus.service.method(AGENT, in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            return "0000"

        @dbus.service.method(AGENT, in_signature="o", out_signature="u")
        def RequestPasskey(self, device):
            return dbus.UInt32(0)

        @dbus.service.method(AGENT, in_signature="ouq")
        def DisplayPasskey(self, device, passkey, entered):
            pass

        @dbus.service.method(AGENT, in_signature="os")
        def DisplayPinCode(self, device, pincode):
            pass

        @dbus.service.method(AGENT, in_signature="ou")
        def RequestConfirmation(self, device, passkey):
            pass

        @dbus.service.method(AGENT, in_signature="o")
        def RequestAuthorization(self, device):
            pass

        @dbus.service.method(AGENT)
        def Cancel(self):
            pass

    chrc = Characteristic()
    app = Application(Service(), chrc)
    adv = Advertisement()
    agent = Agent()
    state = {"adapter": None, "advertising": False}

    def adapter_path():
        objects = dbus.Interface(bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()
        for path, ifaces in objects.items():
            if "org.bluez.GattManager1" in ifaces and "org.bluez.LEAdvertisingManager1" in ifaces:
                return path
        return None

    def ignore(*args):
        pass

    def register():
        """(Re)register everything with bluetoothd; called at start and whenever it restarts."""
        state["advertising"] = False
        path = adapter_path()
        state["adapter"] = path
        if not path:
            return
        obj = bus.get_object(BLUEZ, path)
        props = dbus.Interface(obj, PROPS)
        props.Set(ADAPTER, "Powered", dbus.Boolean(True))
        props.Set(ADAPTER, "Alias", DEVICE_NAME)
        props.Set(ADAPTER, "Pairable", dbus.Boolean(False))
        manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), "org.bluez.AgentManager1")
        try:
            manager.RegisterAgent(agent.path, "NoInputNoOutput")
            manager.RequestDefaultAgent(agent.path)
        except dbus.DBusException:
            pass
        dbus.Interface(obj, "org.bluez.GattManager1").RegisterApplication(
            app.path, {}, reply_handler=ignore, error_handler=ignore)
        set_advertising(bridge.enabled)
        GLib.timeout_add_seconds(5, close_pairing)    # bluetoothd turns Pairable back on as it powers up
        GLib.timeout_add_seconds(2, refresh_keyboards)

    def refresh_keyboards():
        """MIDI devices still linked from before this service existed (the Companion restarted, or the keyboard
        connected first at boot) write to a service that's gone: their MIDI goes nowhere until they look it up
        again. Disconnect them so they reconnect and find it; headphones and speakers are left alone."""
        objects = dbus.Interface(bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            uuids = [str(u) for u in dev.get("UUIDs", [])] if dev else []
            if dev and dev.get("Connected") and not any(u.startswith("0000110b") for u in uuids)                     and path != bridge.peer_path:
                try:
                    dbus.Interface(bus.get_object(BLUEZ, path), "org.bluez.Device1").Disconnect()
                except dbus.DBusException:
                    pass
        return False

    def close_pairing():
        if state["adapter"] and time.monotonic() >= bridge.pairing_until:
            try:
                dbus.Interface(bus.get_object(BLUEZ, state["adapter"]), PROPS).Set(
                    ADAPTER, "Pairable", dbus.Boolean(False))
            except dbus.DBusException:
                pass
        return False

    def set_advertising(on):
        path = state["adapter"]
        if not path or on == state["advertising"]:
            return
        manager = dbus.Interface(bus.get_object(BLUEZ, path), "org.bluez.LEAdvertisingManager1")
        if on:
            manager.RegisterAdvertisement(adv.path, {}, reply_handler=ignore, error_handler=ignore)
        else:
            try:
                manager.UnregisterAdvertisement(adv.path)
            except dbus.DBusException:
                pass
        state["advertising"] = on

    def open_pairing():
        if state["adapter"]:
            props = dbus.Interface(bus.get_object(BLUEZ, state["adapter"]), PROPS)
            props.Set(ADAPTER, "PairableTimeout", dbus.UInt32(PAIRING_SECONDS))
            props.Set(ADAPTER, "Pairable", dbus.Boolean(True))

    def peer_name(device_path):
        try:
            props = dbus.Interface(bus.get_object(BLUEZ, device_path), PROPS)
            return str(props.Get("org.bluez.Device1", "Alias"))
        except dbus.DBusException:
            return None

    def connected_peer():
        """The connected device that isn't the headphones: the MIDI central."""
        objects = dbus.Interface(bus.get_object(BLUEZ, "/"), OM).GetManagedObjects()
        for path, ifaces in objects.items():
            dev = ifaces.get("org.bluez.Device1")
            if dev and dev.get("Connected") and not any(u.startswith("0000110b") for u in dev.get("UUIDs", [])):
                return path, str(dev.get("Alias", ""))
        return None, None

    def device_changed(interface, changed, invalidated, path=None):
        if interface == "org.bluez.Device1" and "Connected" in changed and not changed["Connected"]:
            if path == bridge.peer_path:
                chrc.notifying = False
                bridge.subscribed(False)

    def try_register():
        try:
            register()
            bridge.error = None
        except dbus.DBusException as e:     # adapter not ready yet: try again
            bridge.error = str(e)[:40]
            GLib.timeout_add_seconds(3, try_register)
        return False

    def owner_changed(owner):
        if owner:
            GLib.timeout_add(500, try_register)

    bus.add_signal_receiver(device_changed, dbus_interface=PROPS, signal_name="PropertiesChanged",
                            arg0="org.bluez.Device1", path_keyword="path")
    bus.watch_name_owner(BLUEZ, owner_changed)    # also calls back now if bluetoothd is running

    bridge.bluez = {"notify": chrc.notify, "set_advertising": set_advertising, "open_pairing": open_pairing,
                    "peer_name": peer_name, "connected_peer": connected_peer, "idle_add": GLib.idle_add,
                    "timeout_add_seconds": GLib.timeout_add_seconds}
    GLib.MainLoop().run()


CLOCK, START, CONTINUE, STOP = 0xF8, 0xFA, 0xFB, 0xFC
TRANSPORT = (CLOCK, START, CONTINUE, STOP)


def make_realtime(priority=10):
    """Real-time scheduling for the calling thread through RTKit (as PipeWire gets it; the installer's
    polkit rule allows it without a desktop session). Best effort: without it the clock just wobbles more."""
    bus = None
    try:
        import resource
        import dbus
        resource.setrlimit(resource.RLIMIT_RTTIME, (200_000, 200_000))   # RTKit insists on a limit
        # a private connection: the shared one must be made by start_bluez, with its GLib main loop,
        # or the MIDI service can't be exported ("BT error")
        bus = dbus.SystemBus(private=True)
        rtkit = dbus.Interface(bus.get_object("org.freedesktop.RealtimeKit1", "/org/freedesktop/RealtimeKit1"),
                               "org.freedesktop.RealtimeKit1")
        rtkit.MakeThreadRealtime(dbus.UInt64(threading.get_native_id()), dbus.UInt32(priority))
    except Exception:
        pass
    finally:
        if bus is not None:
            bus.close()


class ClockWatch:
    """The tempo and beat grid of a clock (24 ticks a quarter note) from the times its ticks arrived. Over
    Bluetooth ticks arrive bunched (several to a radio packet), so the grid is a straight line fitted through
    the last 48 ticks (two beats), not the arrival time of any one tick."""

    def __init__(self):
        self.ticks, self.playing = [], False   # [(tick number, arrival time)]
        self.count = 0                          # ticks since Start (or since the clock appeared)

    def message(self, status, now):
        if status == CLOCK:
            self.ticks.append((self.count, now))
            del self.ticks[:-48]
            self.count += 1
        elif status in (START, CONTINUE):
            self.playing = True
            if status == START:
                self.count, self.ticks = 0, []      # the next tick is beat 1 of bar 1
        elif status == STOP:
            self.playing = False

    def fit(self, now):
        """(seconds per tick, time of tick 0) by least squares, or None if no clock for half a second"""
        t = self.ticks
        if len(t) < 13 or now - t[-1][1] > 0.5:
            return None
        n = len(t)
        mx, my = sum(k for k, _ in t) / n, sum(v for _, v in t) / n
        sxx = sum((k - mx) ** 2 for k, _ in t)
        if not sxx:
            return None
        per = sum((k - mx) * (v - my) for k, v in t) / sxx
        return (per, my - per * mx) if per > 0 else None

    def bpm(self, now):
        f = self.fit(now)
        return 60.0 / (f[0] * 24) if f else None

    def bar(self, now):
        """when the current bar (96 ticks) began, on the fitted grid"""
        f = self.fit(now)
        if not f:
            return None
        return f[1] + f[0] * 96 * (self.ticks[-1][0] // 96)


class UsbControllers:
    """USB MIDI controllers (on a hub): every MIDI port that isn't the FM-1, a Bluetooth link or the system's own,
    opened as it appears and closed as it goes, up to MAX. They merge into one source and one destination: "USB"
    in the matrix."""

    MAX = 4

    def __init__(self, fm1, on_message):
        import mido
        self.mido, self.fm1, self.on_message = mido, fm1, on_message
        self.ports = {}                     # client name ("SMK-37+VA") -> {"in": port, "out": port or None}
        self.last_activity = 0.0
        self.lock = threading.Lock()

    def wanted(self, names):
        """mido port names -> {client name: port name} of the controllers among them (a client's first port)"""
        out = {}
        for name in names:
            low = name.lower()
            if (any(s in low for s in UsbMidi.SKIP) or any(m in low for m in self.fm1.match)
                    or ADDRESS.search(name)):
                continue
            out.setdefault(name.split(":")[0], name)
        return out

    def poll(self):
        try:
            ins = self.wanted(self.mido.get_input_names())
            outs = self.wanted(self.mido.get_output_names())
        except Exception:
            return
        for name in [n for n in self.ports if n not in ins]:
            self.close(name)
        for name, port in ins.items():
            if name in self.ports or len(self.ports) >= self.MAX:
                continue
            try:
                inp = self.mido.open_input(port, callback=lambda msg, name=name: self.received(msg))
            except Exception:
                continue
            try:
                out = self.mido.open_output(outs[name]) if name in outs else None
            except Exception:
                out = None
            with self.lock:
                self.ports[name] = {"in": inp, "out": out}

    def received(self, msg):
        self.last_activity = time.monotonic()
        self.on_message(bytes(msg.bytes()))

    def send_messages(self, messages):
        """complete MIDI messages to every controller that takes MIDI in"""
        with self.lock:
            for port in self.ports.values():
                if port["out"] is None:
                    continue
                for raw in messages:
                    try:
                        port["out"].send(self.mido.Message.from_bytes(list(raw)))
                    except (ValueError, OSError):
                        break                 # a malformed message, or the controller just left

    def names(self):
        return sorted(self.ports)

    def close(self, name):
        with self.lock:
            port = self.ports.pop(name, None)
        for p in (port or {}).values():
            try:
                if p:
                    p.close()
            except Exception:
                pass


# ---------------------------------------------------------------- the MIDI matrix

SOURCES, DESTINATIONS = ("BT", "USB", "FM-1", "Box"), ("FM-1", "BT", "USB")
ROUTES = tuple(f"{s}>{d}" for s in SOURCES for d in DESTINATIONS if s != d)    # "Box": its clock, Start/Stop
DEFAULT_ROUTE = {"on": True, "channels": "same", "transpose": 0, "velocity": "normal", "fixed": 100,
                 "drop_clock": False, "drop_aftertouch": False, "drop_bend": False,
                 "chord_on": False, "chord": "maj", "arp_on": False, "arp": "up", "arp_rate": "1/8",
                 "arp_octaves": 1}
CURVES = {"normal": 1.0, "soft": 0.6, "hard": 1.6}      # velocity out = 127 * (in / 127) ** exponent
CHORDS = {"oct": (0, 12), "5th": (0, 7), "maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10),
          "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10)}    # chord memory: the notes one key plays
ARPS = ("up", "down", "updown", "played", "random")
ARP_RATES = {"1/4": 24, "1/8": 12, "1/8T": 8, "1/16": 6}  # clock ticks (24 a quarter note) per step


OFF_AT_FIRST = ("USB>BT", "FM-1>USB", "BT>USB", "Box>USB")   # USB controllers play the FM-1; the rest is a choice


def route_default(key):
    return dict(DEFAULT_ROUTE, on=key not in OFF_AT_FIRST)


def default_routes():
    """every route on but the USB ones above, nothing changed: MIDI passes as it always did"""
    return {key: route_default(key) for key in ROUTES}


def clean_routes(routes):
    """routes from outside (the web page) -> the same, checked and complete; ValueError says what's wrong"""
    if not isinstance(routes, dict):
        raise ValueError("routes must be an object")
    out = default_routes()
    for key, route in routes.items():
        if key not in ROUTES or not isinstance(route, dict):
            raise ValueError(f"no route {key!r}")
        for name, value in route.items():
            if name not in DEFAULT_ROUTE:
                raise ValueError(f"{key}: no setting {name!r}")
            if not ROUTE_RULES[name](value):
                raise ValueError(f"{key}: bad {name} {value!r}")
            out[key][name] = value
    return out


def _int(value, lo, hi):
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


ROUTE_RULES = {                         # each route setting: what it may be (checked lazily, one rule a value)
    "on": lambda v: isinstance(v, bool), "drop_clock": lambda v: isinstance(v, bool),
    "drop_aftertouch": lambda v: isinstance(v, bool), "drop_bend": lambda v: isinstance(v, bool),
    "chord_on": lambda v: isinstance(v, bool), "arp_on": lambda v: isinstance(v, bool),
    "channels": lambda v: v == "same" or _int(v, 0, 15) or (
        isinstance(v, list) and len(v) == 16 and all(c is None or _int(c, 0, 15) for c in v)),
    "transpose": lambda v: _int(v, -24, 24),
    "velocity": lambda v: v in ("normal", "soft", "hard", "fixed"),
    "fixed": lambda v: _int(v, 1, 127),
    "chord": lambda v: isinstance(v, str) and v in CHORDS, "arp": lambda v: isinstance(v, str) and v in ARPS,
    "arp_rate": lambda v: isinstance(v, str) and v in ARP_RATES, "arp_octaves": lambda v: _int(v, 1, 3),
}


class Route:
    """One crossing of the matrix: whether it passes MIDI, and what it does to it on the way.
    channels: "same", one channel for everything (0-15), or 16 entries (a channel 0-15, or None: dropped).
    chord_on: one key plays a chord (chord: which, CHORDS). arp_on: the held notes (chords included) are played
    one at a time on the clock (tick(), 24 a quarter note), ARP_RATES apart, each for half a step (arp: the
    pattern, ARPS). Switching either off keeps the choice of chord and pattern for next time.
    A note-off always stops what its note-on started, so changing a route while a key is held can't leave a
    note hanging. apply() and tick() run on different threads: a lock keeps them apart."""

    def __init__(self, settings=None):
        self.active = {}                    # (channel, note) in -> [(channel, note) out], for held notes
        self.held = []                      # arp: [(channel, note, velocity)] in the order played
        self.sounding, self.ticks, self.step = None, 0, 0     # arp: the note playing, ticks counted, steps
        self.lock = threading.Lock()
        self.update(settings)

    def update(self, settings):
        """new settings -> the note-offs that must go out now (an arp switched off mid-note)"""
        with self.lock:
            self.s = dict(DEFAULT_ROUTE, **(settings or {}))
            if self.s["arp"] not in ARPS:     # settings saved before arp_on: "off" was a pattern then
                self.s["arp"] = "up"
            if self.s["chord"] not in CHORDS:
                self.s["chord"] = "maj"
            if not self.arp_active():
                self.held = []
                return self.silence()
            return []

    def silence(self):
        if not self.sounding:
            return []
        channel, note = self.sounding
        self.sounding = None
        return [bytes([0x80 | channel, note, 0])]

    def out_channel(self, channel):
        channels = self.s["channels"]
        if channels == "same":
            return channel
        if isinstance(channels, int):
            return channels
        return channels[channel] if isinstance(channels, list) and len(channels) == 16 else channel

    def note(self, channel, note):
        out, moved = self.out_channel(channel), note + self.s["transpose"]
        return (out, moved) if out is not None and 0 <= moved <= 127 else None

    def chord(self, channel, note):
        """the (channel, note) outs one played note makes: its chord, mapped and transposed"""
        outs = []
        for interval in self.shape():
            out = self.note(channel, note + interval)
            if out and out not in outs:
                outs.append(out)
        return outs

    def shape(self):
        """the intervals one key plays: the chord's, or just the note"""
        return CHORDS[self.s["chord"]] if self.s["chord_on"] else (0,)

    def velocity(self, value):
        if self.s["velocity"] == "fixed":
            return max(1, min(127, int(self.s["fixed"])))
        return max(1, min(127, round(127 * (value / 127) ** CURVES.get(self.s["velocity"], 1.0))))

    def apply(self, message):
        """a complete MIDI message -> the first message out of this route (bytes), or None"""
        out = self.apply_all(message)
        return out[0] if out else None

    def apply_all(self, message):
        """a complete MIDI message -> what comes out of this route: a list of messages (a chord: several;
        with the arp on, notes wait for the clock)"""
        with self.lock:
            return self._apply(message)

    def _apply(self, message):
        s, status = self.s, message[0]
        if status >= 0xF0:                                  # system: SysEx, clock, Start/Stop...
            if not s["on"] or (status in TRANSPORT and s["drop_clock"]):
                return []
            return [message]
        kind, channel = status & 0xF0, status & 0x0F
        if kind in (0x80, 0x90) and len(message) == 3:
            key = (channel, message[1])
            off = kind == 0x80 or message[2] == 0           # note off (or note on at velocity 0)
            if s["arp_on"] and s["on"]:                     # the arp plays the held notes on the clock
                self.held = [h for h in self.held if h[:2] != key]
                if not off:
                    self.held.append((channel, message[1], message[2]))
                return [] if self.held else self.silence()
            if off:
                outs = self.active.pop(key, None) or (self.chord(*key) if s["on"] else [])
                return [bytes([kind | c, n, message[2]]) for c, n in outs]
            outs = self.chord(*key) if s["on"] else []
            if outs:
                self.active[key] = outs
            return [bytes([0x90 | c, n, self.velocity(message[2])]) for c, n in outs]
        if not s["on"] or (kind in (0xA0, 0xD0) and s["drop_aftertouch"]) or (kind == 0xE0 and s["drop_bend"]):
            return []
        if kind == 0xA0 and len(message) == 3:              # poly aftertouch: follows the note
            out = self.note(channel, message[1])
            return [bytes([0xA0 | out[0], out[1], message[2]])] if out else []
        out = self.out_channel(channel)
        return [] if out is None else [bytes([kind | out]) + bytes(message[1:])]

    # ---- the arpeggiator

    def arp_notes(self):
        """the notes the arp steps through, [(channel, note, velocity)], in the pattern's order (one cycle)"""
        notes = []
        for channel, note, velocity in self.held:
            for interval in self.shape():
                notes.append((channel, note + interval, velocity))
        octaves = max(1, min(4, int(self.s["arp_octaves"])))
        notes = [(c, n + 12 * o, v) for o in range(octaves) for c, n, v in notes]
        pattern = self.s["arp"]
        if pattern == "played":
            return notes
        notes = sorted(set(notes), key=lambda x: x[1])
        if pattern == "down":
            return notes[::-1]
        if pattern == "updown":
            return notes + notes[-2:0:-1]
        return notes                                        # up, and the pool for random

    def tick(self):
        """one clock tick (24 a quarter note): the arp's note-offs and note-ons due now"""
        with self.lock:
            if not self.arp_active():
                return []
            rate = ARP_RATES.get(self.s["arp_rate"], 12)
            out = []
            if self.ticks % rate == rate // 2 or not self.held:          # half a step: the note ends
                out += self.silence()
            if self.ticks % rate == 0 and self.held:
                out += self.silence()
                notes = self.arp_notes()
                if self.s["arp"] == "random":
                    import random
                    channel, note, velocity = random.choice(notes)
                else:
                    channel, note, velocity = notes[self.step % len(notes)]
                self.step += 1
                mapped = self.note(channel, note)
                if mapped:
                    self.sounding = mapped
                    out.append(bytes([0x90 | mapped[0], mapped[1], self.velocity(velocity)]))
            self.ticks += 1
            return out

    def restart(self):
        """Start: the pattern from its first note, on the next tick"""
        with self.lock:
            self.ticks, self.step = 0, 0

    def arp_active(self):
        return self.s["on"] and self.s["arp_on"]


LIBRARY_SYSEX = (bytes([0xF0, 0x7D, 0x46, 0x4C]),   # SLOOP's editor protocol (the box's library)
                 bytes([0xF0, 0x00, 0x59]),           # firmware update frames
                 bytes([0xF0, 0x22, 0x24, 0x35]),     # update start
                 bytes([0xF0, 0x00, 0x32, 0x45]))     # update handshake


def is_library_sysex(message):
    """the FM-1's answers to the library and the updater: not for the Bluetooth MIDI device"""
    return message[:1] == b"\xf0" and message.startswith(LIBRARY_SYSEX)


TIMING_LOG = "/dev/shm/fm1-midi-timing.log"     # RAM only; for measuring Bluetooth timing


def log_timing(packet, arrived):
    """One line per received packet: arrival (ms), the sender's 13-bit timestamp, the bytes.
    Arrival minus sender time, unwrapped, shows how unevenly the radio delivers notes."""
    if len(packet) < 2 or not packet[1] & 0x80:
        return
    sent = ((packet[0] & 0x3F) << 7) | (packet[1] & 0x7F)
    try:
        with open(TIMING_LOG, "a") as f:
            if f.tell() > 512_000:
                f.truncate(0)
            f.write(f"{arrived * 1000:.1f} {sent} {packet.hex()}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- the Companion between the two

class MidiBridge:
    """Joins the FM-1's USB MIDI and the BLE MIDI service. Status for the screen:
    usb_label(), peer, enabled, pairing_until, activity()."""

    def __init__(self, source_match, enabled=True):
        self.enabled, self.paused = enabled, False
        self.peer, self.peer_path = None, None
        self.pairing_until = 0.0
        self.last_ble = 0.0
        self.bluez = None                   # filled in by start_bluez's thread
        self.error = None
        self.pending, self.flush_queued = [], False
        self.lock = threading.Lock()
        self.usb = UsbMidi(source_match, self.from_usb)
        self.controllers = UsbControllers(self.usb, self.from_controllers)
        self.ble_stream = MidiStream()
        self.clock_mode, self.bpm, self.playing = "follow", 120.0, False     # "follow" or "box"
        self.box_count, self.box_bar = 0, None      # the box clock's ticks since Start, its bar's start
        self.watch = {"FM-1": ClockWatch(), "BT": ClockWatch(), "USB": ClockWatch()}
        self.routes = {key: Route(route_default(key)) for key in ROUTES}
        self.held, self.chan_seen = {}, [0.0] * 16      # for the MIDI meter: notes down, channel traffic

    def set_routes(self, routes):
        for key, route in self.routes.items():
            self.deliver(key, route.update(dict(route_default(key), **((routes or {}).get(key) or {}))))

    def deliver(self, key, messages):
        """messages out of a route, to its destination"""
        if not messages:
            return
        if key.endswith(">FM-1"):
            if self.enabled and not self.paused:
                self.usb.send_messages(messages)
                for m in messages:
                    self.note_watch(m, time.monotonic())
        elif key.endswith(">USB"):
            self.controllers.send_messages(messages)
        else:
            for m in messages:
                self.to_ble(m)

    def arp_tick(self):
        """a clock tick for the arpeggiators"""
        for key, route in self.routes.items():
            if route.arp_active():
                self.deliver(key, route.tick())

    def arp_restart(self):
        for route in self.routes.values():
            route.restart()

    def external_clock(self):
        now = time.monotonic()
        return any(watch.bpm(now) for watch in self.watch.values())

    def start(self):
        def body():
            try:
                start_bluez(self)
            except Exception as e:          # no BlueZ or no D-Bus: the audio side keeps working
                self.error = str(e)[:40]

        threading.Thread(target=body, daemon=True).start()
        threading.Thread(target=self.clock_loop, daemon=True).start()

    # ---- clock: watch incoming clock; as clock master, send our own instead

    def clock_loop(self):
        """24 ticks a quarter note while the box is the clock master. Each tick is due at a fixed time
        (not "sleep one interval"), so the tempo doesn't drift with how long sending takes. Measured on a
        Zero W streaming to a speaker: the tempo is exact on average, single ticks ~3 ms early or late
        (the Bluetooth UART's interrupts come first, even before a real-time thread)."""
        make_realtime()
        due = time.monotonic()
        while True:
            box = self.clock_mode == "box"
            # no clock to follow but an arp to play: ticks of our own at the box's tempo, not sent anywhere
            own = not box and any(r.arp_active() for r in self.routes.values()) and not self.external_clock()
            if not (box or own):
                time.sleep(0.05)
                due = time.monotonic()
                continue
            now = time.monotonic()
            if due > now:
                time.sleep(due - now)
            if box:
                if self.box_count % 96 == 0:
                    self.box_bar = due
                self.box_count += 1
                self.send_all(bytes([CLOCK]))
            self.arp_tick()
            due += 60.0 / (self.bpm * 24)
            if time.monotonic() - due > 0.1:  # far behind (the CPU was busy): start afresh
                due = time.monotonic()

    def transport(self, command):
        """Start or Stop to the FM-1 and Bluetooth"""
        self.playing = command == "start"
        if self.playing:
            self.box_count = 0
            self.arp_restart()
        self.send_all(bytes([START if self.playing else STOP]))

    def send_all(self, message):
        """the box's own clock and Start/Stop, where the matrix's Box routes are on"""
        if self.routes["Box>FM-1"].s["on"]:
            self.usb.send_messages([message])
        if self.routes["Box>BT"].s["on"]:
            self.to_ble(message)
        if self.routes["Box>USB"].s["on"]:
            self.controllers.send_messages([message])

    def clock_status(self, now):
        """{"bpm", "source", "playing"} for the screen, or None"""
        if self.clock_mode == "box":
            return {"bpm": round(self.bpm), "tempo": self.bpm, "source": "box", "playing": self.playing,
                    "bar": self.box_bar}
        for source, watch in self.watch.items():
            bpm = watch.bpm(now)
            if bpm:
                return {"bpm": round(bpm), "tempo": round(bpm, 2), "source": source, "playing": watch.playing,
                        "bar": round(watch.bar(now), 3)}
        return None

    def note_watch(self, message, now):
        """notes played to and from the FM-1, and when each channel last carried anything"""
        status = message[0] if message else 0xF0
        if status >= 0xF0:
            return
        channel, kind = status & 0x0F, status & 0xF0
        self.chan_seen[channel] = now
        if kind in (0x80, 0x90) and len(message) == 3:
            if kind == 0x90 and message[2]:
                self.held[(channel, message[1])] = now
            else:
                self.held.pop((channel, message[1]), None)

    def notes_status(self, now):
        """(sorted notes held, bit mask of channels busy in the last 0.3 s); a note held over 30 s with no
        note-off seen is forgotten"""
        for key in [k for k, t in self.held.items() if now - t > 30]:
            del self.held[key]
        chans = sum(1 << ch for ch, t in enumerate(self.chan_seen) if now - t < 0.3)
        return sorted({note for _, note in self.held})[:24], chans

    def passes(self, source, message):
        """Looks at clock on the way; as clock master, keeps other clock and transport to itself."""
        if message and message[0] in TRANSPORT:
            self.watch[source].message(message[0], time.monotonic())
            if self.clock_mode != "box":      # following: the arps step with the incoming clock
                if message[0] == CLOCK:
                    self.arp_tick()
                elif message[0] == START:
                    self.arp_restart()
            return self.clock_mode != "box"
        return True

    # USB -> BLE: queue, then send from the GLib thread in as few packets as possible
    def from_usb(self, message):
        if is_library_sysex(message) or not self.passes("FM-1", message):
            return
        self.note_watch(message, time.monotonic())
        for key in ("FM-1>BT", "FM-1>USB"):
            self.deliver(key, self.routes[key].apply_all(message))

    # USB controllers -> the FM-1 (and Bluetooth, if that route is on); called on rtmidi's thread
    def from_controllers(self, message):
        if is_library_sysex(message) or not self.passes("USB", message):
            return
        for key in ("USB>FM-1", "USB>BT"):
            self.deliver(key, self.routes[key].apply_all(message))

    def to_ble(self, message):
        if not (self.bluez and self.enabled and self.peer):
            return
        with self.lock:
            self.pending.append(message)
            if self.flush_queued:
                return
            self.flush_queued = True
        self.bluez["idle_add"](self.flush)

    def flush(self):
        with self.lock:
            messages, self.pending, self.flush_queued = self.pending, [], False
        for packet in encode(messages, int(time.monotonic() * 1000)):
            self.bluez["notify"](packet)
        self.last_ble = time.monotonic()
        return False

    # BLE -> USB (called on the GLib thread)
    def from_ble(self, packet, device_path):
        if device_path and str(device_path) != self.peer_path:
            self.peer_path, self.peer = str(device_path), self.peer or "connected"
            self.bluez["idle_add"](self.find_name)            # after replying to bluetoothd
        self.last_ble = time.monotonic()
        for m in self.ble_stream.feed(strip_timestamps(packet)):
            if self.passes("BT", m):
                for key in ("BT>FM-1", "BT>USB"):
                    self.deliver(key, self.routes[key].apply_all(m))
        log_timing(packet, self.last_ble)

    def lookup_name(self):
        """True (keep the timer) while BlueZ still only has the address: a keyboard's name is read a few
        seconds after it connects, so the first look often gets 28-9E-F6-…"""
        if self.peer_path:
            self.peer = self.bluez["peer_name"](self.peer_path) or self.peer
        self.naming = bool(self.peer_path and self.peer and ADDRESS.fullmatch(self.peer))
        return self.naming

    def find_name(self):
        if getattr(self, "naming", False):                    # already asking every 3 s
            return False
        if self.lookup_name() and "timeout_add_seconds" in self.bluez:
            self.bluez["timeout_add_seconds"](3, self.lookup_name)
        return False

    def subscribed(self, on):
        if on:
            path, name = self.bluez["connected_peer"]() if self.bluez else (None, None)
            self.peer_path, self.peer = path, name or "connected"
            self.find_name()
        else:
            self.peer, self.peer_path = None, None

    # from the app's threads
    def poll(self):
        self.usb.poll()
        self.controllers.poll()

    def usb_label(self):
        return self.usb.label()

    def activity(self):
        return max(self.last_ble, self.usb.last_activity, self.controllers.last_activity)

    def set_enabled(self, on):
        self.enabled = on
        if self.bluez:
            self.bluez["idle_add"](lambda: (self.bluez["set_advertising"](on), False)[1])

    def open_pairing(self):
        self.pairing_until = time.monotonic() + PAIRING_SECONDS
        if self.bluez:
            self.bluez["idle_add"](lambda: (self.bluez["open_pairing"](), False)[1])


# ---------------------------------------------------------------- in a process of its own

def run_child(conn, source_match, enabled):
    """The MIDI process: MidiBridge, sending its status up the pipe when it changes and taking
    ("enabled", bool) and ("pair",) commands. Exits when the app's end of the pipe closes."""
    import os
    import metronome
    bridge = MidiBridge(source_match, enabled)
    bridge.start()
    metro = metronome.Metronome(lambda: bridge.clock_status(time.monotonic()), on_start=make_realtime)
    wanted = [False, None, "mid", 48000]    # the metronome: on, the speaker, the click's level, the rate

    def commands():
        while True:
            try:
                command = conn.recv()
            except (EOFError, OSError):
                os._exit(0)
            if command[0] == "enabled":
                bridge.set_enabled(command[1])
            elif command[0] == "pair":
                bridge.open_pairing()
            elif command[0] == "pause":            # no Bluetooth notes into the FM-1 while it's flashed
                bridge.paused = command[1]
            elif command[0] == "clock":            # ("clock", "follow" or "box", bpm)
                bridge.clock_mode, bridge.bpm = command[1], float(command[2])
            elif command[0] == "transport":        # ("transport", "start" or "stop")
                bridge.transport(command[1])
            elif command[0] == "routes":           # ("routes", {route key: settings})
                bridge.set_routes(command[1])
            elif command[0] == "metronome":        # ("metronome", on, speaker, level, rate)
                wanted[:] = command[1:5]

    threading.Thread(target=commands, daemon=True).start()
    last, next_poll = None, 0.0
    while True:
        now = time.monotonic()
        if now >= next_poll:
            bridge.poll()
            next_poll = now + 3                # a USB controller plugged in shows up within 3 s
        metro.ensure(*wanted)                  # starts, stops, or restarts it if its stream ended
        status = {"peer": bridge.peer, "error": bridge.error, "usb": bridge.usb_label(),
                  "controllers": bridge.controllers.names(),
                  "activity": round(bridge.activity(), 1),  # 0.1 s steps: at most ~5 updates a second
                  "clock": bridge.clock_status(now)}
        status["notes"], status["chans"] = bridge.notes_status(now)
        if status != last:
            try:
                conn.send(status)
            except (BrokenPipeError, OSError):
                os._exit(0)
            last = status
        time.sleep(0.2)


class MidiProcess:
    """MidiBridge in a child process, with the same interface for the app. A Python process runs
    one thread at a time, and drawing a screen frame takes ~75 ms on a Zero W: in the app's process
    that held notes back, unevenly. In its own process the kernel switches to it as a note arrives."""

    def __init__(self, source_match, enabled=True):
        import multiprocessing
        ctx = multiprocessing.get_context("fork")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=run_child, args=(child, source_match, enabled), daemon=True,
                                name="fm1-midi")
        self.enabled, self.peer, self.error = enabled, None, None
        self.pairing_until = 0.0
        self.usb, self.last_activity = None, 0.0
        self.controllers = []               # USB MIDI controllers' names
        self.clock = None                   # {"bpm", "source", "playing"} while there's a clock
        self.notes, self.chans = [], 0      # for the MIDI meter

    def start(self):
        self.proc.start()
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        while True:
            try:
                status = self.conn.recv()
            except (EOFError, OSError):
                self.error = "MIDI process stopped"
                return
            self.peer, self.error = status["peer"], status["error"]
            self.usb, self.last_activity = status["usb"], status["activity"]
            self.controllers = status.get("controllers") or []
            self.clock = status.get("clock")
            self.notes, self.chans = status.get("notes") or [], status.get("chans", 0)

    def poll(self):
        pass                                # the MIDI process watches the USB side itself

    def usb_label(self):
        return self.usb

    def activity(self):
        return self.last_activity           # CLOCK_MONOTONIC is shared by both processes

    def set_enabled(self, on):
        self.enabled = on
        self.conn.send(("enabled", on))

    def open_pairing(self):
        self.pairing_until = time.monotonic() + PAIRING_SECONDS
        self.conn.send(("pair",))

    def pause(self, on):
        self.conn.send(("pause", on))

    def set_clock(self, mode, bpm):
        self.conn.send(("clock", mode, bpm))

    def transport(self, command):
        self.conn.send(("transport", command))

    def set_routes(self, routes):
        self.conn.send(("routes", routes))

    def set_metronome(self, on, target, level, rate):
        self.conn.send(("metronome", on, target, level, rate))
