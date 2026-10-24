# FM-1 Companion

[![The demo video: the box's screens and meters, with the button presses shown](docs/images/demo-preview.gif)](https://cdn.jsdelivr.net/gh/nervseele89/FM-1-Companion@main/docs/video/fm1-companion-demo-v2.6.1.mp4)

**[▶ Watch the demo](https://cdn.jsdelivr.net/gh/nervseele89/FM-1-Companion@main/docs/video/fm1-companion-demo-v2.6.1.mp4)**
(1:46, with sound): the box's own screens, all eleven meters, the settings, the MIDI matrix and the sample
editor, with an overlay of each button press. The music was played on the FM-1 and recorded by the box.

<img src="docs/images/device-with-fm1.jpg" width="560" alt="The box on its PiSugar battery, cabled to the FM-1 running SLOOP">

A pocket-sized companion box for the **M-VAVE FM-1** pocket synth running custom firmware such as
[Sloop](https://github.com/isod89/sloop-fm1). It started as a way to add the Bluetooth those firmwares leave switched off, and grew into a small studio
sidekick: it plays the FM-1 through wireless speakers and keyboards, records it, shapes its MIDI, shows what
it's doing on eleven meters, and looks after everything stored on it, with no computer needed. It comes in two builds that share one codebase: the **pocket box**, a Raspberry Pi Zero W with a Waveshare
1.3" OLED HAT, and the **desk box** (new, see below), a Raspberry Pi 3 B+ with a 3.5" touchscreen. Either plugs
into the FM-1 by USB, and you run it from its own screen or from your phone.

- **Wireless audio and MIDI:** the FM-1 plays on Bluetooth headphones or a speaker, and the box shows up
  as a Bluetooth MIDI device, **"FM-1 Companion"**, so a keyboard, iPad, phone or computer can play the FM-1
  and hear back what it sends.
- **USB keyboards and a second FM-1:** put a USB hub between the box and the FM-1 and plug in up to four
  USB MIDI keyboards or controllers, and a second FM-1 if you have one. They're found by themselves and play
  straight away; a mixer sets the level of each FM-1 and of a keyboard's own synth.
- **A recorder and a sample editor:** record the FM-1 to WAV from the box, play takes back on the speaker,
  and edit a take (or any sample) into a new sample: trim it, fade it, normalize or reverse it, and tune it
  to the note it plays, ready to load into the FM-1.
- **Playing tools:** a MIDI clock (follow your keyboard's clock, or be the master with tap tempo and
  Start/Stop), a metronome, an arpeggiator and chord memory.
- **A MIDI matrix:** route Bluetooth, USB keyboards, the FM-1s and the box to each other; transpose,
  re-channel, reshape velocity and filter, with four presets.
- **Eleven meters:** level bars, VU needles, spectrum, scope, a reel-to-reel tape deck, a tuner, a MIDI
  keyboard view, a waterfall, a stereo goniometer, a level history and a beat counter.
- **Looks after the FM-1 (SLOOP 2.3+):** back up and restore everything on it (by hand or automatically),
  load projects and samples, and install firmware. It downloads [SLOOP](https://github.com/isod89/sloop-fm1)
  and, if you want it, isod89's experimental synthesizer firmware [Clipper](https://github.com/isod89/clipper-fm1).
  Backups are SLOOP's own backup files, so they also open in SLOOP's web editor.
- **A web app on your phone** (`http://fm1bridge.local`, Wi-Fi on): live status, volume and the playing
  settings, the MIDI matrix, a sample editor with a waveform you drag, recordings you can play in the browser,
  and uploads and downloads for the library.
- **Its own screen and controls:** status, meters and a short settings menu in sections, on the HAT's OLED,
  keys and joystick. Ready about a minute after power-on.
- **Coming in v3:** 5-pin DIN MIDI In, Out and Thru.

```
                            ┌── Bluetooth audio ──▶ headphones / speaker
                            ├◀── Bluetooth MIDI ──▶ keyboard / iPad / phone / Mac
FM-1 ◀── USB ──▶ Pi Zero W ─┤
 (audio, MIDI, backups,     ├◀── USB hub ─────────▶ USB MIDI keyboards, a second FM-1
  samples, firmware)        ├◀── Wi-Fi ───────────▶ web app on your phone
                            └── OLED + joystick: meters, recorder, sample editor
```

Tested with a Pi Zero W on Raspberry Pi OS Lite (Trixie), Sloop 2.7 and 2.8 on the FM-1, a JBL Go 3
speaker and an M-VAVE SMK-37 keyboard over Bluetooth MIDI (including its clock) and over USB through an
ASMedia ASM1074 USB hub, two FM-1s together on that hub with the SMK-37 running Baud Girl. The library was
tested on the real FM-1 with a backup, a restore (read back byte for byte), a project load, a sample load and the
update from SLOOP 2.7.1 to 2.8 (projects, presets and samples kept). The meters were checked on the box with
music playing: about a quarter of the CPU while shown, and no audio dropouts.

## The desk box: Desk v1.0

| Home, in FM-1 A's own colours | The mixer |
|---|---|
| ![Desk home: the sources as SLOOP-style tiles, tempo, REC, START and TAP](docs/images/desk-ui-home-theme.png) | ![Desk mixer: a slider for FM-1 A, FM-1 B, USB keys and the speaker](docs/images/desk-ui-mix-theme.png) |
| ![Desk MIDI routes: tap a route to switch it](docs/images/desk-ui-midi-theme.png) | ![Desk More: the screens not redrawn for touch yet](docs/images/desk-ui-more-theme.png) |

A second build of the Companion for the desk: a **Raspberry Pi 3 B+** with a **3.5" 480×320 touchscreen**, a
**PiSugar 3 Plus** battery (5000 mAh), and a **card slot** for expansion cards. It runs the same software as the
pocket box (audio, the mixer, recording, MIDI, the library, the web app) with screens of its own, made for
touch and drawn in the style of [SLOOP](https://github.com/isod89/sloop-fm1).

> **Hardware testing starts 10/24/2026.** Desk v1.0 is the software, built and tested on a computer against
> the real drawing code; the Pi 3, the screen and the PiSugar 3 Plus arrive that day. Expect the touch
> calibration and the screen driver setting to change once it's on the real hardware.

**What's in Desk v1.0**
- **Five tabs along the bottom:** HOME (the sources as SLOOP-style tiles in the track colours, the tempo, and
  big REC, START and TAP buttons), MIX (a slider for each source and the speaker), MIDI (the routes: tap one to
  switch it, hold it to open its settings), METER and MORE.
- **SLOOP's look:** SLOOP's own pixel font (Terminus), its five palettes (Green, Amber, Cyan, Red, Mono) and its
  four track colours, from SLOOP's source.
- **Follows FM-1 A's colours:** the box asks FM-1 A over USB what SLOOP's MENU › COLOR is set to (a palette, or
  the custom theme made in SLOOP's web editor) and uses the same. More › Theme picks a palette instead. The
  pocket box keeps its blue OLED and never asks.
- **All eleven meters redrawn** full-width and in colour: LED bars, real VU dials, a 32-band spectrum, a
  reel-to-reel tape deck, a big tuner, a 3-octave keyboard, a coloured waterfall and more.
- **More** opens what isn't redrawn for touch yet (Recordings, the sample editor, Sloop, FM-1, settings) as the
  pocket box's screens, larger, in the theme's colours, with on-screen keys. Backing out returns to the tabs.
- **Expansion cards:** a card slot with a pin map, a card ID on three pins so the box knows which card is in,
  and the first card planned: the Pico MIDI card with five TRS jacks. See
  [docs/desk-expansion-cards.md](docs/desk-expansion-cards.md).
- **One codebase:** the box detects its build at start (`device.py`). The desk box's extras check what the
  hardware can do, so they never appear on the pocket box, and `install.sh` sets up whichever build it's on.

<details><summary>All eleven desk meters</summary>

![The desk box's eleven meters in a custom theme](docs/images/desk-meters.png)
</details>

| In SLOOP's Green palette | |
|---|---|
| ![Desk home in Green](docs/images/desk-ui-home.png) | ![Desk mixer in Green](docs/images/desk-ui-mix.png) |

**Parts:** Raspberry Pi 3 B+, a 3.5" 480×320 SPI touchscreen (ILI9486 display, XPT2046 touch: the common
"3.5 inch RPi LCD"), a PiSugar 3 Plus, a 1-to-2 GPIO splitter for the card slot, and a microSD card.

## What's new in v2.8

| Two FM-1s in the MIDI routes | The mixer |
|---|---|
| ![MIDI routes with FM-1 A and FM-1 B rows and A and B columns](docs/images/screen-matrix-two.png) | ![Settings › Audio: FM-1 A level 100%, FM-1 B level 80%, USB keys level 175%](docs/images/screen-settings-mixer.png) |

**New**
- **Two FM-1s at once:** plug a second FM-1 into the USB hub and both play. They're **FM-1 A** and
  **FM-1 B** by the hub port they're on (the lower port is A), so they keep their names across reboots and
  replugs. The home screen shows "SLOOP 2.8 + B".
- **FM-1 B in the MIDI routes:** while it's plugged in, the routes get an *FM-1 B* row and a *B* column with
  all the usual options. At first Bluetooth, USB keyboards and the box's clock play both FM-1s together;
  split them by switching routes off, for example the SMK-37 on A and Bluetooth on B.
- **An audio mixer:** with more than one sound source, each gets its own level, 0–200%: FM-1 A, FM-1 B and
  a USB keyboard's own synth (an SMK-37 running Baud Girl, say). They're added together before Bluetooth,
  and recordings and the meters hear the whole mix. Settings › Audio on the box, the Mixer sliders in the
  web app.
- **Sloop jobs pick their FM-1:** with two plugged in, Settings › Sloop › **FM-1** chooses which one
  backups, restores and loads go to. Firmware installs ask you to unplug the other one first.
- **Faster:** the box now keeps one connection to the audio system open instead of opening a new one
  every few seconds. Each of those cost the Pi Zero 1–2 seconds of CPU, which made playing lag and the
  controls slow with three sound sources. Audio now takes about half the CPU it did.

**New in v2.8.1: Record parts**
- **Each source on its own, plus the mix:** turn on Settings › Audio › **Record parts** and every take also
  keeps FM-1 A, FM-1 B and a USB keyboard's synth as files of their own (`…_fm1-a.wav`, `…_fm1-b.wav`,
  `…_fm1-usb0.wav`), at full level, next to the mix. Ready for samples, or for mixing again on a computer.
- **One recorder for everything:** all the sources go into one many-channel recording, and the box splits
  it into the parts and adds up the mix at your mixer levels after Stop. So the parts and the mix line up to
  the sample, the mix no longer needs the speaker connected, and the Pi stays smooth while recording (a
  recorder for each source maxed its CPU out).
- **In Recordings, a take shows once:** **Parts ›** plays a part or opens it in the sample editor, and
  Delete removes the take with its parts. The web app lists the parts under each take, with a player,
  Download and Edit. Clipping is checked on each part, so a warning names the source that clipped.
- Off at first: a take with parts uses about four times the SD card space.

**Fixed**
- Turning BLE MIDI off no longer silences USB keyboards (it was meant for Bluetooth only).
- With no FM-1 plugged in, a USB keyboard's own sound is never taken for the FM-1's.
- A second FM-1's sound warbled and crackled: re-timing it to the first FM-1's clock now has the slack it
  needs.

**Good to know:** two USB devices never tick at exactly the same speed, so FM-1 A keeps the clock and the
others are re-timed to it, out of earshot. The Audio rate is best left at 48 kHz (the default): it's what
Bluetooth speakers run at, and 44.1 kHz costs the Pi an extra conversion.

<details><summary>Earlier: v2.7</summary>

USB MIDI keyboards and controllers through a USB hub (up to four, found by themselves), a *USB keys* row
and *USB* column in the MIDI routes, the keyboard named on the home screen, Settings › MIDI › USB keys and the
web app; the installer sets the Pi's USB to full speed so a hub works. With the FM-1 on the hub, it can be
unplugged and plugged back in without a reboot (v2.7.1).

| A USB keyboard playing the FM-1 | Settings › MIDI |
|---|---|
| ![The home screen: MIDI SMK-37+VA](docs/images/screen-status.png) | ![Settings › MIDI: USB keys 1](docs/images/screen-settings-midi.png) |
</details>

<details><summary>Earlier: v2.6</summary>

Runs on a [PiSugar 3](https://docs.pisugar.com/docs/product-wiki/battery/pisugar3/pisugar-3-series)
battery, with its charge in the title bar and the web app, a warning at 15% and a safe shutdown at 5%; a new
demo video; recordings warn when the FM-1 clipped (v2.6.2).

| On battery | Plugged in |
|---|---|
| ![The box on battery: the gauge in the title bar reads 70](docs/images/device-battery.jpg) | ![Charging: a bolt in the battery](docs/images/screen-status-charging.png) |
</details>

<details><summary>Earlier: v2.5</summary>

A sample editor on the box and in the web app: start from a recording or a sample, set the start and end,
fades, normalize, reverse and the root note (found by listening), play it, and save it as a new sample with
its note in the name, ready for Sloop › Load sample. Recordings no longer go straight into a slot.
</details>

<details><summary>Earlier: v2.4</summary>

Settings in sections (Playing, Audio, MIDI, Recordings, Wi-Fi, Sloop, FM-1), a bigger web app (live status
and controls, the MIDI matrix with named presets, recordings in the browser), the Sample level setting, a
big volume bar with 2% steps, faster hold-repeat, the home screen naming the firmware (SLOOP 2.8) and the
keyboard, recordings that keep all their audio, and sample loads that convert in about half the time.
</details>

<details><summary>Earlier: v2.3</summary>

Six more meters (Tuner, MIDI, Waterfall, Stereo, History, Beat), the Recordings menu (play a take, resample
it into a slot, delete), a metronome, arp and chord memory with one-press switches, route presets, controls
that react at once (screens redraw in about 8 ms), a Bluetooth MIDI keyboard that reconnects by itself after
the Companion restarts, and a steadier external clock.
</details>

<details><summary>Earlier: v2.2</summary>

Recording to WAV from any meter screen (REC on the screen; stops safely; recordings on the web page), the
Tape meter, MIDI clock (follow or box clock, tap tempo, hold for Start/Stop), the MIDI matrix (per route:
channels, transpose, velocity, drop clock/aftertouch/bend; note-offs always follow their note-ons), and the
Audio rate setting (v2.1.1) that also restarts a silent FM-1 audio stream.
</details>

## Screens

| | |
|---|---|
| ![Status screen](docs/images/screen-status.png) | ![Recording](docs/images/screen-status-rec.png) |
| **Status:** the FM-1 input, the speaker, the MIDI device (the dot blinks with MIDI traffic), the volume and the clock. LIVE means audio is flowing; the battery's charge is next to it. | **Recording:** REC and the time in place of LIVE. |
| ![Settings](docs/images/screen-settings.png) | ![Playing](docs/images/screen-settings-play.png) |
| **Settings** (joystick right): Playing, Audio, MIDI, Recordings, Sample editor, Wi-Fi, Sloop and FM-1. Press opens a section, left goes back. | **Playing ›:** Metronome, Tempo, Clock, Arp, Chord and the click level. **Audio ›** has the speaker and the audio rate; **MIDI ›** is below. |
| ![Sloop](docs/images/screen-settings-sloop.png) | ![FM-1](docs/images/screen-settings-fm1.png) |
| **Sloop ›:** back up, restore, load a project or a sample, the sample level, auto backup, the SD card. Writes ask you to hold the joystick press for 2 seconds; restores and project loads back up first. | **FM-1 ›:** what the FM-1 runs, and installing firmware (SLOOP and Clipper download from GitHub with Wi-Fi on). |
| ![MIDI](docs/images/screen-settings-midi.png) | ![MIDI routes](docs/images/screen-matrix.png) |
| **MIDI ›:** Bluetooth MIDI, pairing, the USB keyboards plugged in (press lists them), the routes and their presets. | **MIDI routes:** rows are sources (Bluetooth, USB keyboards, the FM-1, the box's clock), columns destinations; a filled square is a route that's on. Press opens it. |
| ![Two FM-1s](docs/images/screen-matrix-two.png) | ![The mixer](docs/images/screen-settings-mixer.png) |
| **Two FM-1s:** FM-1 B's row and column (A and B) appear while it's plugged in. Here the SMK-37 plays A and Bluetooth plays B. | **The mixer** (Settings › Audio, with more than one sound source): a level for each FM-1 and a USB keyboard's own synth, up to 200%. |
| ![A route](docs/images/screen-route.png) | ![Volume](docs/images/screen-status-volume.png) |
| **A route:** here the keyboard is played an octave up with a soft velocity curve. | **Volume:** up/down on the home screen, in 2% steps, shown big for a moment. |
| ![Firmware install](docs/images/screen-job.png) | ![A recording](docs/images/screen-recordings.png) |
| **A job running:** here a SLOOP update, about 2 minutes. | **A recording:** play it, edit it as a sample, or delete it. |
| ![Playing](docs/images/screen-player.png) | ![Sample editor](docs/images/screen-editor.png) |
| **Playing** a take on the speaker. | **Sample editor:** the selection between two markers (outside it, dotted); left/right picks the field, up/down changes it, press plays it. The length is top right. |
| ![The note](docs/images/screen-editor-note.png) | ![Speaker list](docs/images/screen-speakers.png) |
| **Note:** the root note it found by listening (up/down changes it). It goes into the saved file's name so SLOOP plays the sample in tune. | **Speakers and headphones** found by a scan (KEY1): pick one with the joystick. |
| ![Pairing](docs/images/screen-pairing.png) | ![Screensaver](docs/images/screen-screensaver.png) |
| **Pairing**, then it's remembered and reconnects by itself. | **Screensaver** after five minutes: one small word that moves, so the OLED doesn't burn in. |
| ![Starting](docs/images/screen-starting.png) |  |
| **Starting:** shown as soon as the Companion opens the screen at boot, until the status screen is ready. |  |

### Meters

Joystick left from the status screen. Up/down switches meter (the box remembers your choice), press
records, right goes back. The screen stays on while sound (or MIDI) plays.

| | |
|---|---|
| ![Bars](docs/images/screen-meter-bars.png) | ![VU](docs/images/screen-meter-vu.png) |
| **Bars:** left and right peak level from −48 to 0 dBFS, with a peak hold and CLIP. | **VU:** two needles with real VU ballistics (0 VU = −18 dBFS); the dot lights on peaks. |
| ![Spectrum](docs/images/screen-meter-spectrum.png) | ![Scope](docs/images/screen-meter-scope.png) |
| **Spectrum:** 16 bands from 50 Hz to 7.5 kHz, with falling peak caps. | **Scope:** the waveform, held steady on a rising zero crossing. |
| ![Tape](docs/images/screen-meter-tape.png) | ![Tuner](docs/images/screen-meter-tuner.png) |
| **Tape:** a reel-to-reel deck, here 1 min 23 s into a recording. | **Tuner:** the note, how far off in cents, its frequency; boxed when within 5 cents. |
| ![MIDI](docs/images/screen-meter-midi.png) | ![Waterfall](docs/images/screen-meter-waterfall.png) |
| **MIDI:** the notes played and the chord's name; below, the busy channels. | **Waterfall:** the spectrum over the last ~5 s, newest at the top. |
| ![Stereo](docs/images/screen-meter-stereo.png) | ![History](docs/images/screen-meter-history.png) |
| **Stereo:** mono is a vertical line, wide stereo a cloud; correlation below. | **History:** the peak level over the last ~50 s. |
| ![Beat](docs/images/screen-meter-beat.png) | |
| **Beat:** the tempo and the beat in the bar, flashing in time. | |

The screen images are drawn by the Companion's own code (`tools/render_screens.py`).

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
| `fm1-bt-bridge/bridge.py` | The OLED app: status and settings screens, headphone pairing, audio routing and the mixer (PipeWire), keys |
| `fm1-bt-bridge/blemidi.py` | The Bluetooth LE MIDI service (BlueZ), its link to the FM-1's USB MIDI and to USB keyboards, the clock, the MIDI matrix with its arp and chord memory, and the metronome, in a process of its own |
| `fm1-bt-bridge/libui.py` | The Sloop and FM-1 settings, recordings and their screens on the box |
| `fm1-bt-bridge/meters.py` | The eleven meters, recording and playback, pitch detection |
| `fm1-bt-bridge/midiui.py` | The MIDI matrix screens on the box |
| `fm1-bt-bridge/sampleui.py` | The sample editor's screen on the box |
| `fm1-bt-bridge/battery.py` | The PiSugar 3 battery's charge, read over I2C |
| `fm1-bt-bridge/device.py` | Which build a box is (pocket or desk) and what its hardware can do |
| `fm1-bt-bridge/deskui.py` | The desk box's touch screens: the tabs, HOME, MIX, MIDI, METER, MORE |
| `fm1-bt-bridge/deskmeters.py` | The eleven meters drawn for the desk box's screen |
| `fm1-bt-bridge/desk.py` | The desk box's touchscreen and touch input (the kernel's framebuffer and touch device) |
| `fm1-bt-bridge/sloopstyle.py` | SLOOP's palettes, track colours, font and themes, for the desk box |
| `fm1-bt-bridge/vendor/fonts/` | Terminus, SLOOP's pixel font (SIL Open Font License, credited in `VENDORED.md`) |
| `fm1-bt-bridge/sampler.py` | The sample editor's sums (trim, fades, normalize, reverse, the note), shared by the box and the web page |
| `fm1-bt-bridge/metronome.py` | The metronome's click stream (run by the MIDI process) |
| `fm1-bt-bridge/library.py` | The library's jobs (backup, restore, projects, samples, firmware); also a command-line tool |
| `fm1-bt-bridge/sloop.py` | SLOOP's editor protocol over USB MIDI: backups, projects, sample slots; which FM-1 is A and which is B |
| `fm1-bt-bridge/samplefast.py` | SLOOP's sample converter made faster, with the same output byte for byte |
| `fm1-bt-bridge/web.py` | The web app (http://fm1bridge.local while Wi-Fi is on) |
| `fm1-bt-bridge/weblink.py` | How the box and the web app talk: the box's status out, the page's commands in |
| `fm1-bt-bridge/vendor/sloop/` | SLOOP's firmware installer and sample converter, copied unchanged (GPL-3.0, credited in `VENDORED.md`) |
| `fm1-bt-bridge/install.sh` | Installs packages, configuration and the boot services on the Pi |
| `fm1-bt-bridge/config/` | PipeWire, WirePlumber, polkit, systemd, swap, Wi-Fi and SD-card helper configuration |
| `tests/` | A pretend FM-1 speaking SLOOP's protocol, and tests for the library, the screens, the meters, recording and playback, MIDI, the clock, the metronome and the web page (`python tests/test_library.py` etc.; needs mido, Pillow and numpy) |
| `tools/midi_timing.py` | Analyses the Companion's Bluetooth MIDI timing log (`/dev/shm/fm1-midi-timing.log` on the Pi) |
| `tools/render_screens.py` | Draws the OLED screens as PNG files for this README, without a Pi |
| `docs/desk-expansion-cards.md` | The desk box's card slot: pins, card ID, the MIDI card |
| `docs/midi-jacks-build-plan.md` | Building the TRS MIDI jacks (the Pico MIDI card's circuit) |
| `docs/images/` | The photo and screen images |

## Known limits

- **Bluetooth audio delay:** about 150–250 ms (SBC). Fine for listening and practising; you'll
  notice it when playing tightly in time.
- **On battery, the FM-1 runs (and charges) from it too.** The Pi Zero's USB port gives the FM-1 power
  that software can't switch off, so a PiSugar 3 lasts far less with the FM-1 plugged in. Charge the FM-1
  first; cutting its power on battery needs a small hardware switch, still being tested.
- **Recordings clip if the FM-1 sends USB at full level** with several tracks playing: it clips inside the
  FM-1, before the box gets the sound. The box warns you after the take; set the FM-1's USB audio to
  master with the knob around half (or lower its track levels) for clean takes.
- **Plugging the FM-1 straight into the box while the Pi is running can reboot the Pi.** The Zero's USB
  port has no current limit. Connect the FM-1 first, or put a USB hub in between: unplugging the FM-1 from
  the hub and plugging it back in doesn't reboot the box (plugging the hub itself into the box may still).
- **With two FM-1s (or a keyboard's synth), recordings need the speaker connected** to hear the whole mix;
  without one they record FM-1 A. With Record parts on (v2.8.1) the mix is made from the parts and needs no
  speaker. Firmware installs wait until only one FM-1 is plugged in.
- **USB runs at 12 Mbit/s** so a hub works on the Pi Zero. Fine for MIDI and the FM-1's audio, but a
  USB 2 device that needs high speed (a fast USB stick, say) is slower behind it.
- **Chords over Bluetooth MIDI arrive slightly rolled** (10–50 ms) with keyboards that send
  each note in its own packet.
- **Start-up still takes about a minute** on a Pi Zero W's single core; Wi-Fi comes up about 40 s
  after the Companion. The optional `config/fm1-native-wifi` step (see the
  [box README](fm1-bt-bridge/README.md)) saves NetworkManager about a minute more.

## License

GPL-3.0, like Sloop. See [LICENSE](LICENSE).

The desk box's palettes and track colours are SLOOP's (isod89/sloop-fm1, GPL-3.0), and its font is Terminus by Dimitar Toshkov Zhekov (SIL Open Font License 1.1), as SLOOP ships it.
