# FM-1 Sloop/Felucca Companion Device

![The bridge: a Pi Zero W with the Waveshare OLED HAT, streaming the FM-1 to a JBL Go 3](docs/images/device.jpg)

A small Bluetooth box for the **M-VAVE FM-1** pocket synth running custom firmware such as
[Sloop](https://github.com/isod89/sloop-fm1) or [Felucca](https://github.com/hugelton/Felucca).
Those firmwares never switch the FM-1's radio on, so this adds the Bluetooth from outside: a
Raspberry Pi Zero W with a Waveshare 1.3" OLED HAT, connected to the FM-1 by USB.

- **Audio out:** the FM-1's USB audio plays on Bluetooth headphones or a speaker.
- **MIDI in and out:** the Pi shows up as a Bluetooth MIDI device, **"FM-1 Bridge"**, so a
  keyboard, iPad, phone or computer can play the FM-1 wirelessly and receive what it sends.
- **Its own screen and controls:** status, pairing and settings on the HAT's OLED, keys and joystick.

```
                         ┌──Bluetooth A2DP───▶ headphones / speaker
FM-1 ──USB──▶ Pi Zero W ─┤
 (audio + MIDI)          └◀─Bluetooth LE MIDI─▶ keyboard / iPad / phone / Mac
```

Tested with a Pi Zero W on Raspberry Pi OS Lite (Trixie), Sloop 2.7 on the FM-1, a JBL Go 3
speaker and an M-VAVE SMK-37 keyboard over Bluetooth MIDI.

## Screens

| | |
|---|---|
| ![Status screen](docs/images/screen-status.png) | ![Settings](docs/images/screen-settings.png) |
| **Status:** the FM-1 input, the speaker, the MIDI device (the dot blinks with MIDI traffic), the volume. LIVE means audio is flowing. | **Settings** (joystick right): speaker, Bluetooth audio on/off, BLE MIDI, a pairing window, Wi-Fi. |
| ![Speaker list](docs/images/screen-speakers.png) | ![Pairing](docs/images/screen-pairing.png) |
| **Speakers and headphones** found by a scan (KEY1): pick one with the joystick. | **Pairing**, then it's remembered and reconnects by itself. |
| ![Screensaver](docs/images/screen-screensaver.png) | |
| **Screensaver** after five minutes: one small word that moves, so the OLED doesn't burn in. | |

The screen images are drawn by the bridge's own code (`tools/render_screens.py`).

## Getting started

Everything is in [`fm1-bt-bridge/`](fm1-bt-bridge/). Its [README](fm1-bt-bridge/README.md)
covers what to buy, flashing the SD card, installing, the controls and troubleshooting. In short:

```
scp -r fm1-bt-bridge YOUR_USER@fm1bridge.local:~
ssh YOUR_USER@fm1bridge.local
bash fm1-bt-bridge/install.sh
sudo reboot
```

## Repository

| Path | What it is |
|---|---|
| `fm1-bt-bridge/bridge.py` | The OLED app: status and settings screens, headphone pairing, audio routing (PipeWire), keys |
| `fm1-bt-bridge/blemidi.py` | The Bluetooth LE MIDI service (BlueZ) and its link to the FM-1's USB MIDI, in a process of its own |
| `fm1-bt-bridge/install.sh` | Installs packages, configuration and the boot service on the Pi |
| `fm1-bt-bridge/config/` | PipeWire, WirePlumber, polkit and systemd configuration |
| `tools/midi_timing.py` | Analyses the bridge's Bluetooth MIDI timing log (`/dev/shm/fm1-midi-timing.log` on the Pi) |
| `tools/render_screens.py` | Draws the OLED screens as PNG files for this README, without a Pi |
| `docs/images/` | The photo and screen images |

## Known limits

- **Bluetooth audio delay:** about 150–250 ms (SBC). Fine for listening and practising; you'll
  notice it when playing tightly in time.
- **Plugging the FM-1 in while the Pi is running can reboot the Pi.** The Zero's USB port has no
  current limit. Connect the FM-1 first, or use a powered USB hub.
- **Chords over Bluetooth MIDI arrive slightly rolled** (10–50 ms) with keyboards that send
  each note in its own packet.

## License

GPL-3.0, like Sloop and Felucca. See [LICENSE](LICENSE).
