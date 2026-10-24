"""Which FM-1 Companion this is, and what it can do.

Two builds share one codebase: the pocket box (a Pi Zero W with the Waveshare 1.3" OLED HAT, joystick and
three keys) and the desk box (a Pi 3/4 with a 3.5" 480x320 SPI touchscreen). The box looks at its board and
screen at start; features that need more than a Zero can carry ask detect()'s "can" set instead of the board.
Settings' "build" ("auto", "pocket" or "desk") overrides the guess.
"""

import os
from pathlib import Path

MODEL = Path("/proc/device-tree/model")
FRAMEBUFFERS = Path("/sys/class/graphics")


def board(path=MODEL):
    """"Raspberry Pi 3 Model B Plus Rev 1.3", or "" off a Pi"""
    try:
        return path.read_bytes().rstrip(b"\0").decode(errors="replace")
    except OSError:
        return ""


def touchscreen(root=FRAMEBUFFERS):
    """(framebuffer device, width, height) of a small SPI screen (480x320), or None: fbtft's (fb_ili9486) or the
    kernel's DRM driver's (ili9486drmfb: the piscreen overlay's, Raspberry Pi OS from 2026), which is /dev/fb0
    when no HDMI monitor was there at boot, else /dev/fb1"""
    for fb in sorted(root.glob("fb[0-9]")):
        try:
            width, height = (int(v) for v in (fb / "virtual_size").read_text().strip().split(","))
        except (OSError, ValueError):
            continue
        try:
            name = (fb / "name").read_text().strip().lower()
        except OSError:
            name = ""
        spi = any(k in name for k in ("fb_", "ili94", "piscreen", "mipi-dbi"))
        if spi or (fb.name != "fb0" and width * height <= 800 * 480):   # (fb0 unnamed: an HDMI monitor)
            return f"/dev/{fb.name}", width, height
    return None


def detect(setting="auto", model=None, screen="look"):
    """-> {"build": "pocket" or "desk", "board", "screen": (device, w, h) or None, "can": set()}

    can: "touch" and "color" (the desk box's screen), "cores" (4 of them: smooth meters, more at once),
    "usb_ports" (built-in USB ports: no hub, no 12 Mbit/s setting)."""
    model = board() if model is None else model
    screen = touchscreen() if screen == "look" else screen
    zero = "Zero" in model
    if setting in ("pocket", "desk"):
        build = setting
    else:
        build = "desk" if screen and not zero else "pocket"
    can = set()
    if build == "desk":
        can |= {"touch", "color"}
    if (os.cpu_count() or 1) >= 4 and not model.startswith("Raspberry Pi Zero W"):
        can.add("cores")
    if model and not zero:
        can.add("usb_ports")
    return {"build": build, "board": model, "screen": screen, "can": can}
