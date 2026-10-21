-- FM-1 Bluetooth bridge (WirePlumber 0.4, Raspberry Pi OS Bookworm).
-- Headless: keep Bluetooth audio running without a logged-in desktop seat, and act only as an
-- A2DP source (the Pi sends audio to headphones; no hands-free mic profile).
-- PipeWire's own BLE MIDI server is off: the bridge's blemidi.py provides the MIDI service.
bluez_monitor.properties["with-logind"] = false
bluez_monitor.properties["bluez5.roles"] = "[ a2dp_source ]"
if bluez_midi_monitor then
  bluez_midi_monitor.enabled = false
end
