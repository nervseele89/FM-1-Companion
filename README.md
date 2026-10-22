# FM-1 Sloop/Felucca Companion Device

![The bridge: a Pi Zero W with the Waveshare OLED HAT, streaming the FM-1 to a JBL Go 3](docs/images/device.jpg)

A small Bluetooth box for the **M-VAVE FM-1** pocket synth running custom firmware such as
[Sloop](https://github.com/isod89/sloop-fm1) or [Felucca](https://github.com/hugelton/Felucca).
Those firmwares never switch the FM-1's radio on, so this adds the Bluetooth from outside: a
Raspberry Pi Zero W with a Waveshare 1.3" OLED HAT, connected to the FM-1 by USB.

- **Audio out:** the FM-1's USB audio plays on Bluetooth headphones or a speaker.
- **MIDI in and out:** the Pi shows up as a Bluetooth MIDI device, **"FM-1 Bridge"**, so a
  keyboard, iPad, phone or computer can play the FM-1 wirelessly and receive what it sends.
- **A library for the FM-1 (v2, SLOOP 2.3+):** back up and restore everything on it, load projects and
  samples, and install firmware (it downloads [SLOOP](https://github.com/isod89/sloop-fm1) and, if you
  want it, isod89's experimental synthesizer firmware [Clipper](https://github.com/isod89/clipper-fm1)),
  from the box or from a web page on your phone. No computer needed. Backups are SLOOP's own backup files,
  so they also open in SLOOP's web editor.
- **Its own screen and controls:** status, pairing, settings and the library on the HAT's OLED, keys and
  joystick.
- **Audio meters (v2.1):** the FM-1's sound as level bars, VU needles, a spectrum or a scope (joystick
  left from the status screen; up/down picks one).
- **Ready in under a minute (v2.1):** Raspberry Pi OS Trixie took almost 3 minutes to start the bridge on
  a Pi Zero W. The installer trims that to about 54 s, and "Starting..." shows on the screen meanwhile.

```
                         ┌──Bluetooth A2DP───▶ headphones / speaker
FM-1 ──USB──▶ Pi Zero W ─┤
 (audio + MIDI)          └◀─Bluetooth LE MIDI─▶ keyboard / iPad / phone / Mac
```

Tested with a Pi Zero W on Raspberry Pi OS Lite (Trixie), Sloop 2.7 and 2.8 on the FM-1, a JBL Go 3
speaker and an M-VAVE SMK-37 keyboard over Bluetooth MIDI. The library was tested on the real FM-1 with
a backup, a restore (read back byte for byte), a project load, a sample load and the update from
SLOOP 2.7.1 to 2.8 (projects, presets and samples kept). The v2.1 meters were checked on the box with
music playing: about a quarter of the CPU while shown, and no audio dropouts.

## Screens

| | |
|---|---|
| ![Status screen](docs/images/screen-status.png) | ![Settings](docs/images/screen-settings.png) |
| **Status:** the FM-1 input, the speaker, the MIDI device (the dot blinks with MIDI traffic), the volume. LIVE means audio is flowing. | **Settings** (joystick right): the library, speaker, Bluetooth audio on/off, BLE MIDI, a pairing window, Wi-Fi. |
| ![Library](docs/images/screen-library.png) | ![Firmware install](docs/images/screen-job.png) |
| **Library:** back up, restore, load a project or a sample, install firmware, import from the SD card. Writes ask you to hold the joystick press for 2 seconds, and back up first. | **A job running:** here a SLOOP update, about 2 minutes. |
| ![Speaker list](docs/images/screen-speakers.png) | ![Pairing](docs/images/screen-pairing.png) |
| **Speakers and headphones** found by a scan (KEY1): pick one with the joystick. | **Pairing**, then it's remembered and reconnects by itself. |
| ![Screensaver](docs/images/screen-screensaver.png) | ![Starting](docs/images/screen-starting.png) |
| **Screensaver** after five minutes: one small word that moves, so the OLED doesn't burn in. | **Starting:** shown as soon as the bridge opens the screen at boot, until the status screen is ready. |

### Meters (v2.1)

Joystick left from the status screen. Up/down switches meter (the box remembers your choice), right goes
back. The screen stays on while sound plays.

| | |
|---|---|
| ![Bars](docs/images/screen-meter-bars.png) | ![VU](docs/images/screen-meter-vu.png) |
| **Bars:** left and right peak level from −48 to 0 dBFS, with a peak hold and CLIP. | **VU:** two needles with real VU ballistics (0 VU = −18 dBFS); the dot lights on peaks. |
| ![Spectrum](docs/images/screen-meter-spectrum.png) | ![Scope](docs/images/screen-meter-scope.png) |
| **Spectrum:** 16 bands from 50 Hz to 7.5 kHz, with falling peak caps. | **Scope:** the waveform, held steady on a rising zero crossing. |

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
| `fm1-bt-bridge/libui.py` | The Library screens on the box |
| `fm1-bt-bridge/meters.py` | The meter screen: level bars, VU needles, spectrum, scope |
| `fm1-bt-bridge/library.py` | The library's jobs (backup, restore, projects, samples, firmware); also a command-line tool |
| `fm1-bt-bridge/sloop.py` | SLOOP's editor protocol over USB MIDI: backups, projects, sample slots |
| `fm1-bt-bridge/web.py` | The library's web page (http://fm1bridge.local while Wi-Fi is on) |
| `fm1-bt-bridge/vendor/sloop/` | SLOOP's firmware installer and sample converter, copied unchanged (GPL-3.0, credited in `VENDORED.md`) |
| `fm1-bt-bridge/install.sh` | Installs packages, configuration and the boot services on the Pi |
| `fm1-bt-bridge/config/` | PipeWire, WirePlumber, polkit, systemd, swap, Wi-Fi and SD-card helper configuration |
| `tests/` | A pretend FM-1 speaking SLOOP's protocol, and tests for the library, its screens, the meters and the web page (`python tests/test_library.py` etc.; needs mido, Pillow and numpy) |
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
- **Start-up still takes about a minute** on a Pi Zero W's single core; Wi-Fi comes up about 40 s
  after the bridge. The optional `config/fm1-native-wifi` step (see the
  [box README](fm1-bt-bridge/README.md)) saves NetworkManager about a minute more.

## License

GPL-3.0, like Sloop and Felucca. See [LICENSE](LICENSE).
