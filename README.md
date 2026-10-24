# FM-1 Companion

**A studio sidekick for the M-VAVE FM-1 pocket synth.** Bluetooth audio and MIDI, a mixer, a recorder, a sample
editor, eleven meters and a MIDI matrix, with no computer needed. By [XOXO Audio Labs](https://xoxoaudiolabs.com).

**[xoxoaudiolabs.com](https://xoxoaudiolabs.com)** · [The Pocket](https://xoxoaudiolabs.com/products/fm1-companion-pocket/)
· [The Studio](https://xoxoaudiolabs.com/products/fm1-companion-studio/) ·
[Try the web app](https://xoxoaudiolabs.com/app/) (sample data, in your browser)

[![The demo video: the box's screens and meters, with the button presses shown](docs/images/demo-preview.gif)](https://cdn.jsdelivr.net/gh/xoxoaudiolabs/FM-1-Companion@main/docs/video/fm1-companion-demo-v2.6.1.mp4)

**[▶ Watch the demo](https://cdn.jsdelivr.net/gh/xoxoaudiolabs/FM-1-Companion@main/docs/video/fm1-companion-demo-v2.6.1.mp4)**
(1:46, with sound): the box's own screens, all eleven meters, the settings, the MIDI matrix and the sample
editor, with an overlay of each button press. The music was played on the FM-1 and recorded by the box.

<img src="docs/images/device-with-fm1.jpg" width="560" alt="The Pocket on its PiSugar battery, cabled to the FM-1 running SLOOP">

It started as a way to add the Bluetooth that custom FM-1 firmware such as [SLOOP](https://github.com/isod89/sloop-fm1)
leaves switched off, and grew from there. Plug it into the FM-1 by USB and run it from its own screen or from
your phone.

## Two builds, one codebase

| | **The Pocket** | **The Studio** |
|---|---|---|
| Computer | Raspberry Pi Zero W | Raspberry Pi 3 B+ |
| Screen and controls | 1.3" OLED, joystick and three keys (Waveshare HAT) | 3.5" 480×320 touchscreen, in SLOOP's style and colours |
| Battery | PiSugar 3 | PiSugar 3 Plus (5000 mAh) |
| Adds | Fits in a pocket | A DAW control surface (Kraken, Ableton Live), network MIDI, any USB audio device on its own channel, four USB ports, Ethernet, a card slot (planned) |
| Software | v2.8.2 | Studio v1.2 (hardware testing from October 2026) |
| More | [Product page](https://xoxoaudiolabs.com/products/fm1-companion-pocket/) | [Product page](https://xoxoaudiolabs.com/products/fm1-companion-studio/) |

The installer works out which build it's on, and each build only shows what its hardware can do.

## What it does

- **Wireless audio and MIDI.** The FM-1 plays on Bluetooth headphones or a speaker, and the box shows up as a
  Bluetooth MIDI device, **"FM-1 Companion"**, so a keyboard, iPad, phone or computer can play the FM-1.
- **More keyboards, a second FM-1.** Through a USB hub (or the Studio's four ports): up to four USB MIDI
  keyboards and a second FM-1, found by themselves. **FM-1 A** and **FM-1 B** keep their names by the hub
  port they're on.
- **A mixer.** Every sound source gets its own level, 0–200%: each FM-1 and each USB audio device (a
  keyboard's own synth, a mic, an audio interface), added up before Bluetooth.
- **A recorder and a sample editor.** Record to WAV, play takes back, and turn a take into a sample: trim,
  fade, normalize, reverse and tune it to its note, ready to load into the FM-1. **Record parts** also keeps
  each source as its own file next to the mix.
- **Playing tools.** MIDI clock (follow, or lead with tap tempo and Start/Stop), metronome, arpeggiator and
  chord memory.
- **A MIDI matrix.** Route Bluetooth, USB keyboards, the FM-1s and the box to each other: transpose,
  re-channel, reshape velocity and filter, with four presets.
- **Eleven meters.** LED bars, VU needles, spectrum, scope, a reel-to-reel tape deck, a tuner, a keyboard view,
  a waterfall, a stereo goniometer, a level history and a beat counter.
- **Looks after the FM-1 (SLOOP 2.3+).** Back up and restore everything on it (by hand or automatically), load
  projects and samples, and install firmware: [SLOOP](https://github.com/isod89/sloop-fm1), or isod89's
  experimental [Clipper](https://github.com/isod89/clipper-fm1). Backups are SLOOP's own files, so they open in
  SLOOP's web editor too.
- **A web app on your phone** at `http://fm1bridge.local` (Wi-Fi, or Ethernet on the Studio): live status, the
  mixer, the MIDI matrix, a sample editor with a waveform you drag, your recordings and the library.
  Installable on your home screen. [Try it with sample data](https://xoxoaudiolabs.com/app/).
- **Plays with your DAW (the Studio).** A network MIDI device (RTP-MIDI) whose DAW tab is a Mackie Control
  surface for [Kraken](https://github.com/isod89/sodwave-site) and Ableton Live: 8 touch faders with track names,
  meters, pan, solo, mute and the transport.
- **Coming:** 5-pin DIN / TRS MIDI in, out and thru (the Pocket's v3, the Studio's first expansion card).

```
                               ┌── Bluetooth audio ──▶ headphones / speaker
                               ├◀── Bluetooth MIDI ──▶ keyboard / iPad / phone / Mac
FM-1 ◀── USB ──▶ Pocket/Studio ┤
 (audio, MIDI, backups,        ├◀── USB ────────────▶ USB MIDI keyboards, USB audio, a second FM-1
  samples, firmware)           ├◀── Wi-Fi/Ethernet ─▶ web app on your phone; a DAW (the Studio)
                               └── its own screen: meters, mixer, recorder, sample editor
```

## Getting started

Everything is in [`fm1-bt-bridge/`](fm1-bt-bridge/). Its [README](fm1-bt-bridge/README.md) covers what to buy,
flashing the SD card, installing, the controls and troubleshooting. In short:

```
scp -r fm1-bt-bridge YOUR_USER@fm1bridge.local:~
ssh YOUR_USER@fm1bridge.local
bash fm1-bt-bridge/install.sh
sudo reboot
```

It's ready about a minute after power-on.

## Screens

The same jobs on both boxes. Every image is drawn by the Companion's own code (`tools/render_screens.py`).

| | The Pocket | The Studio |
|---|---|---|
| **Home:** the FM-1s, the speaker, MIDI, the volume, the clock; LIVE and the battery | ![Pocket home](docs/images/screen-status.png) | ![Studio home](docs/images/desk-ui-home-theme.png) |
| **Mixer:** a level for each FM-1 and USB device, up to 200%, and Record parts | ![Pocket mixer](docs/images/screen-settings-mixer.png) | ![Studio mixer](docs/images/desk-ui-usb-theme.png) |
| **MIDI routes:** sources down the side, destinations across; FM-1 B and the Studio's NET when connected | ![Pocket MIDI routes](docs/images/screen-matrix-two.png) | ![Studio MIDI routes](docs/images/desk-ui-midi-theme.png) |
| **Meters:** eleven of them, from LED bars to a tuner, a tape deck and a waterfall | ![Pocket meter](docs/images/screen-meter-spectrum.png) | ![Studio meter](docs/images/desk-ui-meter-theme.png) |
| **Settings:** Playing, Audio, MIDI, Recordings, Sloop, FM-1; on the Studio, values with - / + by touch | ![Pocket settings](docs/images/screen-settings-play.png) | ![Studio settings](docs/images/desk-page-settings.png) |
| **Sample editor:** a take trimmed, faded and tuned into a sample for SLOOP; on the Studio, drag the markers | ![Pocket sample editor](docs/images/screen-editor.png) | ![Studio sample editor](docs/images/desk-ui-editor-theme.png) |
| **DAW** (the Studio): a Mackie Control surface for Kraken or Ableton Live | | ![Studio DAW](docs/images/desk-ui-daw-theme.png) |

<details><summary>More of the Pocket's screens</summary>

| | | |
|---|---|---|
| ![Recording](docs/images/screen-status-rec.png) | ![Volume](docs/images/screen-status-volume.png) | ![Settings](docs/images/screen-settings.png) |
| Recording: REC and the time | Volume, in 2% steps | Settings: its sections |
| ![Sloop](docs/images/screen-settings-sloop.png) | ![FM-1](docs/images/screen-settings-fm1.png) | ![A route](docs/images/screen-route.png) |
| Sloop: backups, projects, samples | FM-1: firmware | A route: transpose, velocity, channels |
| ![A recording](docs/images/screen-recordings.png) | ![The note](docs/images/screen-editor-note.png) | ![Playing](docs/images/screen-player.png) |
| A recording: play, edit, parts, delete | The note it found by listening | A take playing |
| ![Speakers](docs/images/screen-speakers.png) | ![Firmware install](docs/images/screen-job.png) | ![Screensaver](docs/images/screen-screensaver.png) |
| Speakers found by a scan | A SLOOP update running | The screensaver |
</details>

<details><summary>All eleven meters on each box</summary>

**The Pocket** (joystick left from home; up/down changes meter, press records)

![The Pocket's eleven meters](docs/images/screen-meters.png)

**The Studio** (the METER tab; tap the meter for the next)

![The Studio's eleven meters](docs/images/desk-meters.png)
</details>

## What's new

### Studio v1.2

| The sample editor, by touch | Every screen the Studio's own |
|---|---|
| ![The Studio's sample editor: the take full width, start and end markers to drag, zoom, fades, normalize, reverse, the note, play and save](docs/images/desk-ui-editor-theme.png) | ![A restore on the Studio, held to confirm: the button fills as it's held](docs/images/desk-page-confirm.png) |

- **Every screen drawn for touch.** Settings, lists (recordings, samples, backups, firmware), route details,
  route presets, the speaker scan, job progress and playback each have a page of their own, with a back button.
- **Hold to confirm:** anything that writes to the FM-1 (a restore, a firmware install) asks you to hold a button
  for 2 seconds.
- **The sample editor, by touch:** drag the start and end markers, zoom in around the selection, fade, normalize,
  reverse, set the note, play and save.
- **MORE** gains Wi-Fi and **Shut down**, since the Studio has no power key.

**Connecting a DAW:** on the computer, open network MIDI (Mac: Audio MIDI Setup › MIDI Studio › Network;
Windows: the free rtpMIDI driver) and connect a session to each of the Studio's two ports. In Kraken, set the
port connected to "FM-1 Companion MCU" to Mackie; in Live, choose MackieControl as a control surface on that port.

### Pocket v2.8

| Two FM-1s in the MIDI routes | The mixer |
|---|---|
| ![MIDI routes with FM-1 A and FM-1 B rows and A and B columns](docs/images/screen-matrix-two.png) | ![Settings › Audio: FM-1 A level 100%, FM-1 B level 80%, USB keys level 175%](docs/images/screen-settings-mixer.png) |

- **Two FM-1s at once,** named A and B by their hub port, each with its own row and column in the MIDI routes.
- **An audio mixer:** each sound source at its own level, 0–200%, added up before Bluetooth.
- **Record parts (v2.8.1):** each source as its own file next to the mix, from one recording, so they line up to
  the sample.
- **Faster:** one connection to the audio system kept open, about half the CPU it took before.

<details><summary>Release history</summary>

**Studio**
- **v1.1:** network MIDI (RTP-MIDI) with a music port and a Mackie Control port; the DAW tab for Kraken and
  Ableton Live; every USB audio device on its own named mixer channel, with an input pair for multichannel
  interfaces.
- **v1.0:** the Studio's first release: tabs along the bottom, SLOOP's font, palettes and track colours,
  following FM-1 A's colour theme, all eleven meters redrawn in colour, the card slot's pin map and card ID
  ([docs/studio-expansion-cards.md](docs/studio-expansion-cards.md)).

**Pocket**
- **v2.8.2:** a long USB device name in Settings › Audio no longer runs into its level on the OLED.
- **v2.8:** two FM-1s, the mixer, a much lighter audio connection. **v2.8.1:** Record parts.
- **v2.7:** USB MIDI keyboards through a hub, found by themselves; the FM-1 can be replugged on the hub without a
  reboot (v2.7.1).
- **v2.6:** PiSugar 3 battery support (charge on screen, a warning at 15%, a safe shutdown at 5%); a new demo
  video; a warning when a recording clipped (v2.6.2).
- **v2.5:** the sample editor, on the box and in the web app.
- **v2.4:** settings in sections, a bigger web app, route presets by name, faster sample loads.
- **v2.3:** six more meters, the Recordings menu, metronome, arp and chord memory, faster screens.
- **v2.2:** recording to WAV, the Tape meter, MIDI clock and the MIDI matrix.
</details>

## Tested with

A Pi Zero W on Raspberry Pi OS Lite (Trixie); SLOOP 2.7 and 2.8; a JBL Go 3; an M-VAVE SMK-37 over Bluetooth MIDI
and over USB through an ASMedia ASM1074 hub; two FM-1s together on that hub with the SMK-37 running Baud Girl.
The library was tested on a real FM-1: a backup, a restore (read back byte for byte), a project load, a sample load
and the update from SLOOP 2.7.1 to 2.8. The meters take about a quarter of the Pi Zero's CPU, with no audio
dropouts. The Studio's software is tested on a computer; testing on its hardware starts in October 2026.

## Known limits

- **Bluetooth audio delay:** about 150–250 ms (SBC). Fine for listening and practising; you'll notice it when
  playing tightly in time.
- **Recordings clip if the FM-1 sends USB at full level** with several tracks playing (inside the FM-1, before the
  box hears it). The box warns you; set the FM-1's USB audio to master with the knob around half.
- **Chords over Bluetooth MIDI arrive slightly rolled** (10–50 ms) with keyboards that send each note in its own
  packet.
- **The Pocket only:**
  - **On battery, the FM-1 runs and charges from it too:** the Pi Zero's USB port can't switch its power off, so
    the battery lasts far less with the FM-1 plugged in. A small hardware switch is being tested.
  - **Plugging the FM-1 straight in while the Pi runs can reboot it** (the port has no current limit). Connect
    the FM-1 first, or use a USB hub: replugging the FM-1 on the hub is fine.
  - **USB runs at 12 Mbit/s** so a hub works on the Pi Zero: fine for MIDI and the FM-1's audio, slow for a fast
    USB stick.
  - **Start-up takes about a minute** on the Zero's single core; Wi-Fi comes up about 40 s later (the optional
    `config/fm1-native-wifi` step in the [box README](fm1-bt-bridge/README.md) saves about a minute more).
- **With two FM-1s (or a keyboard's synth), plain recordings need the speaker connected** to hear the whole mix;
  with Record parts on, the mix is made from the parts and needs none. Firmware installs wait until only one FM-1
  is plugged in.

<details><summary>What's in the repository</summary>

| Path | What it is |
|---|---|
| `fm1-bt-bridge/bridge.py` | The app: screens, headphone pairing, audio routing and the mixer (PipeWire), keys |
| `fm1-bt-bridge/blemidi.py` | Bluetooth LE MIDI (BlueZ), the FM-1's USB MIDI and USB keyboards, the clock, the MIDI matrix with its arp and chord memory, the metronome, in a process of its own |
| `fm1-bt-bridge/libui.py` | The Sloop and FM-1 settings, recordings and their screens |
| `fm1-bt-bridge/meters.py` | The eleven meters, recording and playback, pitch detection |
| `fm1-bt-bridge/midiui.py` | The MIDI matrix screens |
| `fm1-bt-bridge/sampleui.py` | The sample editor's screen on the Pocket |
| `fm1-bt-bridge/battery.py` | The PiSugar battery's charge, read over I2C |
| `fm1-bt-bridge/device.py` | Which build a box is and what its hardware can do |
| `fm1-bt-bridge/deskui.py` | The Studio's touch screens and tabs |
| `fm1-bt-bridge/deskmeters.py` | The eleven meters drawn for the Studio |
| `fm1-bt-bridge/deskpages.py` | The Studio's pages: settings, lists, confirmations, jobs, a route's details |
| `fm1-bt-bridge/desk.py` | The Studio's touchscreen and touch input |
| `fm1-bt-bridge/netmidi.py` | Network MIDI (RTP-MIDI) and the Mackie Control surface (the Studio) |
| `fm1-bt-bridge/sloopstyle.py` | SLOOP's palettes, track colours, font and themes (the Studio) |
| `fm1-bt-bridge/sampler.py` | The sample editor's sums, shared by the box and the web page |
| `fm1-bt-bridge/metronome.py` | The metronome's click stream |
| `fm1-bt-bridge/library.py` | The library's jobs (backup, restore, projects, samples, firmware); also a command-line tool |
| `fm1-bt-bridge/sloop.py` | SLOOP's editor protocol over USB MIDI; which FM-1 is A and which is B |
| `fm1-bt-bridge/samplefast.py` | SLOOP's sample converter made faster, byte for byte the same |
| `fm1-bt-bridge/web.py` | The web app at http://fm1bridge.local |
| `fm1-bt-bridge/weblink.py` | How the box and the web app talk |
| `fm1-bt-bridge/vendor/` | SLOOP's firmware installer and sample converter (GPL-3.0) and the Terminus font (SIL OFL), unchanged and credited in `VENDORED.md` |
| `fm1-bt-bridge/install.sh` | Installs packages, configuration and the boot services |
| `fm1-bt-bridge/config/` | PipeWire, WirePlumber, polkit, systemd, swap, Wi-Fi, network MIDI and SD-card configuration |
| `tests/` | A pretend FM-1 speaking SLOOP's protocol, and tests for everything else (`python tests/test_library.py` etc.; needs mido, Pillow and numpy) |
| `tools/render_screens.py` | Draws the screens as PNG files for this README, without a Pi |
| `tools/midi_timing.py` | Analyses the Bluetooth MIDI timing log |
| `docs/` | The Studio's expansion cards, the TRS MIDI jacks' build plan, and the images |
</details>

## License

GPL-3.0, like SLOOP. See [LICENSE](LICENSE).

The Studio's palettes and track colours are SLOOP's (isod89/sloop-fm1, GPL-3.0), and its font is Terminus by
Dimitar Toshkov Zhekov (SIL Open Font License 1.1), as SLOOP ships it.
