# Desk box expansion cards

The desk box (a Raspberry Pi 3 B+ with the 3.5" touchscreen and a PiSugar 3 Plus) has a **card slot**: a
board you plug in to add hardware, starting with the MIDI jacks. Cards follow the rules below so any card fits
any desk box, the touchscreen keeps working, and the box knows which card is in without being told.

This is the plan as of the desk box's first build. The box doesn't read the card ID yet; that comes with the
first card.

## The slot

| Option | Parts | Swapping a card |
|---|---|---|
| **GPIO splitter (recommended)** | A "1 to 2" GPIO expansion board: one 40-pin header in, two out side by side. The screen goes on one, the card on the other | Without touching the screen, through a slot in the case |
| Tall stacking header | A 2×20 extra-tall stacking header: the card sits between the Pi and the screen | Lift the screen off first |

Either way every card sees all 40 pins, so the rules below are the same.

**Switch the box off before you swap a card.** The Pi's pins aren't made to be plugged in live.

## Pins

Pin numbers are the header's (1–40, pin 1 nearest the SD card end, odd pins on the inside row); GPIO numbers
are the chip's.

| Pin | GPIO | Used by | Cards may |
|---|---|---|---|
| 1, 17 | | 3.3 V | draw a little (the Pi's 3.3 V supply is shared: keep a card under 50 mA, or make your own 3.3 V from 5 V) |
| 2, 4 | | 5 V (from the PiSugar 3 Plus) | power the card (a Pico runs from 5 V) |
| 6, 9, 14, 20, 25, 30, 34, 39 | | Ground | |
| 3, 5 | 2, 3 | I2C: the PiSugar 3 Plus (addresses 0x57 and 0x68) | share the bus, at other addresses |
| 7 | 4 | free | use |
| 8, 10 | 14, 15 | free: the Pi's serial port (TX, RX) | use: the MIDI card talks to its Pico here |
| 11 | 17 | **touchscreen**: touch interrupt | no |
| 12, 35, 38, 40 | 18, 19, 20, 21 | free (also the Pi's I2S audio pins, and SPI1) | use |
| 13 | 27 | free | use |
| 15, 16 | 22, 23 | free | use |
| 18 | 24 | **touchscreen**: display data/command | no |
| 19, 21, 23, 24, 26 | 10, 9, 11, 8, 7 | **touchscreen**: SPI0 (display and touch) | no |
| 22 | 25 | **touchscreen**: display reset | no |
| 27, 28 | 0, 1 | reserved (the Pi's HAT ID EEPROM pins) | no |
| 29, 31, 33 | 5, 6, 13 | **card ID** (below) | only as the ID |
| 32 | 12 | free | use |
| 36 | 16 | free | use |
| 37 | 26 | free | use |

So a card has GPIO **4, 12, 14, 15, 16, 18–23, 26, 27** to itself, plus I2C (shared), 5 V and a little 3.3 V.

## Card ID

GPIO 5, 6 and 13 tell the box which card is in. The Pi pulls all three up inside; a card ties some of them to
ground. A pin tied to ground counts as 1:

| ID | GPIO 13 | GPIO 6 | GPIO 5 | Card |
|---|---|---|---|---|
| 0 | open | open | open | no card |
| 1 | open | open | ground | **MIDI card** (Pico, 5 TRS jacks: IN A, OUT A, THRU A, IN B, OUT B) |
| 2 | open | ground | open | MIDI card, simple (one IN and one OUT straight on the Pi's serial port, no Pico) |
| 3–6 | | | | free for future cards |
| 7 | ground | ground | ground | prototype card (the box switches nothing on) |

Tie a pin to ground with a plain wire, or a 1 kΩ resistor to be gentle with it. At start-up the box reads the
ID and switches that card's features on: the MIDI card adds its jacks to the MIDI routes and the MIDI jacks
settings page. It joins the "can" list the desk box already uses for features its hardware allows (see
`fm1-bt-bridge/device.py`).

## Size

- At most HAT size: **65 × 56 mm**, a 2×20 female header along one edge (pin 1 in the same corner as the Pi's).
- Parts on top no taller than about 15 mm, so the case's card slot stays one size for every card.
- Jacks and connectors along the edge facing out of the case.

## The MIDI card (ID 1)

The Pico MIDI hub from [midi-jacks-build-plan.md](midi-jacks-build-plan.md) (route C), built on a card:

| Card | Connects to |
|---|---|
| Pico VSYS (pin 39), GND | Pi 5 V (pin 2), ground (pin 6) |
| Pico GP0 TX (pin 1) | Pi GPIO 15 RX (pin 10) |
| Pico GP1 RX (pin 2) | Pi GPIO 14 TX (pin 8) |
| 6N137 A and B pin 8 (and pin 7) | Pi 5 V |
| GPIO 5 (pin 29) | ground: card ID 1 |

The jacks, 6N137s and resistors are exactly as in the build plan. The Pico and the Pi talk at 250,000 baud,
and the Pico tags which jack each message came from or goes to.

On the Pi 3 the full serial port drives Bluetooth, so GPIO 14/15 get the Pi's second ("mini") serial port. The
installer will set `enable_uart=1`, which keeps its speed steady, and switch the serial console off those pins.
The breadboard you're wiring for the Maker Faire is this circuit, so moving it onto a card is a matter of
soldering it to perfboard with a 2×20 header.
