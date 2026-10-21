"""Bluetooth LE MIDI for the FM-1 bridge.

The Pi appears as a BLE MIDI device ("FM-1 Bridge") that phones, tablets and computers connect to,
and MIDI passes both ways between it and the FM-1's USB MIDI port:

  phone / iPad / Mac  <--BLE MIDI-->  Pi  <--USB MIDI (ALSA sequencer, via mido)-->  FM-1

The packet coding (encode / strip_timestamps) is plain Python; the BlueZ side needs python3-dbus
and python3-gi, the USB side python3-mido and python3-rtmidi.
"""
import threading
import time

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_IO = "7772e5db-3868-4112-a1a9-f2669d106bf3"
PACKET = 20                 # ATT payload with the minimum MTU: safe with every central
DEVICE_NAME = "FM-1 Bridge"
PAIRING_SECONDS = 120


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

class UsbMidi:
    """The FM-1's USB MIDI ports through mido, reopened when the FM-1 comes and goes."""

    SKIP = ("through", "rtmidi", "pipewire", "midi-bridge")

    def __init__(self, match, on_message):
        import mido
        self.mido = mido
        self.match, self.on_message = match, on_message
        self.name, self.inp, self.out = None, None, None
        self.stream = MidiStream()
        self.last_activity = 0.0
        self.lock = threading.Lock()

    def pick(self, names):
        names = [n for n in names if not any(s in n.lower() for s in self.SKIP)]
        for n in names:
            if any(m in n.lower() for m in self.match):
                return n
        return names[0] if names else None

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
        """Plain MIDI bytes from Bluetooth (running status allowed) to the FM-1."""
        with self.lock:
            messages = self.stream.feed(data)
        if self.out is None:
            return
        for raw in messages:
            try:
                self.out.send(self.mido.Message.from_bytes(list(raw)))
                self.last_activity = time.monotonic()
            except (ValueError, OSError):           # a malformed message, or the FM-1 just left
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
                    "peer_name": peer_name, "connected_peer": connected_peer, "idle_add": GLib.idle_add}
    GLib.MainLoop().run()


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


# ---------------------------------------------------------------- the bridge between the two

class MidiBridge:
    """Joins the FM-1's USB MIDI and the BLE MIDI service. Status for the screen:
    usb_label(), peer, enabled, pairing_until, activity()."""

    def __init__(self, source_match, enabled=True):
        self.enabled = enabled
        self.peer, self.peer_path = None, None
        self.pairing_until = 0.0
        self.last_ble = 0.0
        self.bluez = None                   # filled in by start_bluez's thread
        self.error = None
        self.pending, self.flush_queued = [], False
        self.lock = threading.Lock()
        self.usb = UsbMidi(source_match, self.from_usb)

    def start(self):
        def body():
            try:
                start_bluez(self)
            except Exception as e:          # no BlueZ or no D-Bus: the audio side keeps working
                self.error = str(e)[:40]

        threading.Thread(target=body, daemon=True).start()

    # USB -> BLE: queue, then send from the GLib thread in as few packets as possible
    def from_usb(self, message):
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
            self.bluez["idle_add"](self.lookup_name)          # after replying to bluetoothd
        self.last_ble = time.monotonic()
        if self.enabled:
            self.usb.send_bytes(strip_timestamps(packet))
        log_timing(packet, self.last_ble)

    def lookup_name(self):
        if self.peer_path:
            self.peer = self.bluez["peer_name"](self.peer_path) or self.peer
        return False

    def subscribed(self, on):
        if on:
            path, name = self.bluez["connected_peer"]() if self.bluez else (None, None)
            self.peer_path, self.peer = path, name or "connected"
        else:
            self.peer, self.peer_path = None, None

    # from the app's threads
    def poll(self):
        self.usb.poll()

    def usb_label(self):
        return self.usb.label()

    def activity(self):
        return max(self.last_ble, self.usb.last_activity)

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
    bridge = MidiBridge(source_match, enabled)
    bridge.start()

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

    threading.Thread(target=commands, daemon=True).start()
    last, next_poll = None, 0.0
    while True:
        now = time.monotonic()
        if now >= next_poll:
            bridge.poll()
            next_poll = now + (15 if bridge.usb_label() else 3)
        status = {"peer": bridge.peer, "error": bridge.error, "usb": bridge.usb_label(),
                  "activity": round(bridge.activity(), 1)}  # 0.1 s steps: at most ~5 updates a second
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
