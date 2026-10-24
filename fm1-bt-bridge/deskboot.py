"""The Studio's boot screen (480x320), in SLOOP's style and the last theme it showed: the FM-1 Companion's name,
STUDIO, XOXO//AUDIO//LABS, and a bar along the start-up's steps until HOME takes over.

Two moments use it. Early in boot, before the app runs, a udev rule copies splash.raw onto the screen as soon as
the touchscreen's framebuffer appears (install.sh; the kernel's console is kept off the screen), so the boot text
never shows. Then the app draws the steps over it (main(): show() after each one). save() writes splash.raw from
the theme in use, so the next boot's first picture matches."""

from pathlib import Path

from PIL import Image, ImageDraw

import sloopstyle as st

W, H = 480, 320
STEPS = ("STARTING THE SAMPLER", "STARTING MIDI", "STARTING AUDIO", "FINDING YOUR DEVICES")
VERSION = "STUDIO 1.3"
SPLASH = Path.home() / ".local" / "share" / "fm1-bridge" / "splash.raw"   # RGB565, what udev copies at boot


def theme(state):
    """the theme the Studio last showed (state "boot_theme", saved by the desk UI), else the picked palette"""
    saved = state.get("boot_theme")
    if saved:
        return st.theme_load(saved)
    pick = state.get("desk_theme", "follow")
    return st.Theme(pick) if pick in st.NAMES else st.Theme()


def frame(t, step=None, version=VERSION):
    """the boot screen; step: the index into STEPS under way (None: the picture before the app runs)"""
    img = Image.new("RGB", (W, H), t.bg)
    d = ImageDraw.Draw(img)
    for k in range(4):                             # SLOOP's four track colours, a band along the top
        d.rectangle((k * W // 4, 0, (k + 1) * W // 4 - 1, 5), fill=t.track[k])
    name = "FM-1 COMPANION"
    w, _ = st.text_size(name, 3)
    st.text(img, ((W - w) // 2, 62), name, t.hi, 3)
    studio = "S T U D I O"
    w, _ = st.text_size(studio, 2)
    st.text(img, ((W - w) // 2, 122), studio, t.track[2], 2)
    left, right, y = 80, W - 80, 196                # the start-up's bar, its steps in the track colours
    d.rectangle((left, y, right, y + 10), fill=t.grey[0])
    done = 0 if step is None else step + 1
    seg = (right - left) / len(STEPS)
    for k in range(done):
        d.rectangle((int(left + k * seg), y, int(left + (k + 1) * seg) - 2, y + 10), fill=t.track[k % 4])
    label = "STARTING..." if step is None else STEPS[step]
    w, _ = st.text_size(label)
    st.text(img, ((W - w) // 2, y + 20), label, t.c[3])
    x = (W - st.text_size("XOXO//AUDIO//LABS")[0]) // 2   # the XOXO Audio Labs mark, its slashes grey
    for part in ("XOXO", "//", "AUDIO", "//", "LABS"):
        st.text(img, (x, H - 34), part, t.grey[2] if part == "//" else t.c[4])
        x += st.text_size(part)[0]
    if version:
        st.text(img, (W - 8 - st.text_size(version)[0], H - 20), version, t.grey[1])
    return img


def show(display, t, step=None):
    display.show_rgb(frame(t, step))


def rgb565(img):
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    return (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2").tobytes()


def save(t, path=SPLASH):
    """splash.raw for the next boot's first picture, in this theme (only written when it changes)"""
    data = rgb565(frame(t))
    try:
        if path.exists() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except OSError:
        return False
