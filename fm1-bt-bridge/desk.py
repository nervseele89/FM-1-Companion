"""The desk box's screen and touch: a 3.5" 480x320 SPI touchscreen (ILI9486 display, XPT2046/ADS7846
resistive touch) on a Pi 3/4, through the kernel's drivers (dtoverlay: a framebuffer at /dev/fb1 and a touch
input device).

Step one: the app's own 128x64 screens, three times larger and in colour, with big buttons under them for
the joystick and KEY1-3. Taps on the buttons become the same key events the pocket box's keys make (KeyTimer:
holds and repeats included), so every screen works on the desk box from the start.
"""

import select
import struct
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
SCALE = 3                                   # the 128x64 screens at 384x192
SCREEN_AT = ((W - 128 * SCALE) // 2, 6)
BG, FG, DIM = (8, 12, 18), (94, 200, 248), (28, 36, 46)
KEY_BG, KEY_ON, KEY_TEXT = (30, 38, 50), (94, 200, 248), (220, 230, 240)

# the buttons: (key name, label, box), two rows under the screen; sized for a resistive screen's fingertip
ROW1, ROW2 = (206, 256), (262, 316)
BUTTONS = [("key1", "Speaker", (8, ROW1[0], 156, ROW1[1])),
           ("key2", "Audio", (166, ROW1[0], 314, ROW1[1])),
           ("key3", "Power", (324, ROW1[0], 472, ROW1[1])),
           ("left", "◀", (8, ROW2[0], 96, ROW2[1])),
           ("up", "▲", (102, ROW2[0], 190, ROW2[1])),
           ("press", "●", (196, ROW2[0], 284, ROW2[1])),
           ("down", "▼", (290, ROW2[0], 378, ROW2[1])),
           ("right", "▶", (384, ROW2[0], 472, ROW2[1]))]


FONT_DIRS = ["/usr/share/fonts/truetype/dejavu"]    # (tools/render_screens.py adds its own)


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for folder in FONT_DIRS:
        try:
            return ImageFont.truetype(str(Path(folder) / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def button_at(x, y):
    """the key name of the button at screen point (x, y), or None"""
    for name, _, (x0, y0, x1, y1) in BUTTONS:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return name
    return None


def colours(theme=None):
    """(picture, background, rule, key, key lit, key text) from a sloopstyle.Theme, or the defaults"""
    if theme is None:
        return FG, BG, DIM, KEY_BG, KEY_ON, KEY_TEXT
    return theme.c[4], theme.bg, theme.c[0], theme.grey[0], theme.hi, theme.c[4]


def compose(img, pressed=None, theme=None, fonts={}):
    """The app's 128x64 1-bit frame -> the desk screen (RGB 480x320): the frame in colour, scaled up, with the
    buttons under it (pressed one lit); in a theme's colours when there's one (the desk UI's MORE items)"""
    if "label" not in fonts:
        fonts["label"], fonts["symbol"] = font(20, True), font(26, True)
    fg, bg, rule, key_bg, key_on, key_text = colours(theme)
    out = Image.new("RGB", (W, H), bg)
    mono = img.convert("1").resize((128 * SCALE, 64 * SCALE), Image.NEAREST)
    out.paste(Image.composite(Image.new("RGB", mono.size, fg), Image.new("RGB", mono.size, bg), mono), SCREEN_AT)
    d = ImageDraw.Draw(out)
    x0, y0 = SCREEN_AT
    d.rectangle((x0 - 3, y0 - 3, x0 + 128 * SCALE + 2, y0 + 64 * SCALE + 2), outline=rule, width=2)
    for name, label, box in BUTTONS:
        on = name == pressed
        d.rounded_rectangle(box, radius=10, fill=key_on if on else key_bg)
        f = fonts["label"] if len(label) > 1 else fonts["symbol"]
        tw = d.textlength(label, font=f)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        d.text((cx - tw / 2, cy - f.size * 0.62), label, font=f, fill=bg if on else key_text)
    return out


def rgb565(img):
    """an RGB image -> little-endian RGB565 bytes, as fbtft's framebuffer takes them"""
    import numpy as np
    a = np.asarray(img, dtype=np.uint16)
    return (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2").tobytes()


class TftDisplay:
    """The app's display (show, contrast, power) on the touchscreen's framebuffer. Only what changed is
    written: the picture's band of rows on a redraw, the buttons' rows when one is pressed (SPI moves a whole
    480x320 frame, 300 KB, in ~0.1 s; the picture's band is 60% of that, a press 40%)."""

    def __init__(self, device="/dev/fb1"):
        self.fb = open(device, "r+b", buffering=0)
        self.frame, self.pressed, self.lock = Image.new("1", (128, 64)), None, threading.Lock()
        self.blank_path = Path("/sys/class/graphics") / Path(device).name / "blank"
        self.bases = {}                       # (pressed button, theme) -> the whole screen with no picture in it
        self.theme = None                     # the desk UI's colours (sloopstyle.Theme), for the classic screens
        self.last = None                      # the desk UI's last frame, as RGB565 rows (show_rgb)
        with self.lock:
            self.write(0, H)

    def base(self):
        key = (self.pressed, id(self.theme))
        if key not in self.bases:
            self.bases[key] = compose(Image.new("1", (128, 64)), self.pressed, self.theme)
        return self.bases[key]

    def show(self, img):
        """a pocket-box screen (128x64, 1 bit): scaled up, with the on-screen keys"""
        with self.lock:
            self.frame = img.copy()
            full, self.last = self.last is not None, None
            self.write(0, H if full else SCREEN_AT[1] + 64 * SCALE + 4)

    def show_rgb(self, img):
        """a whole desk UI frame (RGB 480x320): only the rows that changed since the last one are sent"""
        import numpy as np
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        px = (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2")
        with self.lock:
            if self.last is None:
                top, bottom = 0, H
            else:
                rows = np.flatnonzero(np.any(px != self.last, axis=1))
                if not len(rows):
                    return
                top, bottom = int(rows[0]), int(rows[-1]) + 1
            self.last = px
            self.fb.seek(top * W * 2)
            self.fb.write(px[top:bottom].tobytes())

    def press(self, name):
        """light a button while it's held (None: none), redrawn at once"""
        with self.lock:
            if name != self.pressed:
                self.pressed = name
                self.write(ROW1[0] - 2, H)

    def write(self, top, bottom):
        """rows top..bottom of the screen as it stands"""
        band = self.base().crop((0, top, W, bottom))
        x0, y0 = SCREEN_AT
        if top < y0 + 64 * SCALE and bottom > y0:
            fg, bg = colours(self.theme)[:2]
            mono = self.frame.convert("1").resize((128 * SCALE, 64 * SCALE), Image.NEAREST)
            picture = Image.composite(Image.new("RGB", mono.size, fg), Image.new("RGB", mono.size, bg), mono)
            band.paste(picture, (x0, y0 - top))
        self.fb.seek(top * W * 2)
        self.fb.write(rgb565(band))

    def contrast(self, value):
        pass                                  # the backlight is on or off (power)

    def power(self, on):
        try:
            self.blank_path.write_text("0" if on else "1")
        except OSError:
            pass


# ---------------------------------------------------------------- touch

EVENT = struct.Struct("llHHi")              # struct input_event: time (2 longs), type, code, value
EV_KEY, EV_ABS, EV_SYN = 1, 3, 0
ABS_X, ABS_Y, BTN_TOUCH = 0, 1, 330


def find_touch(devices=Path("/proc/bus/input/devices")):
    """/dev/input/eventN of the touchscreen (ADS7846 / XPT2046), or None"""
    try:
        text = devices.read_text()
    except OSError:
        return None
    for block in text.split("\n\n"):
        if "ADS7846" in block or "XPT2046" in block or "Touchscreen" in block:
            for line in block.splitlines():
                if line.startswith("H: Handlers="):
                    for handler in line.split("=", 1)[1].split():
                        if handler.startswith("event"):
                            return f"/dev/input/{handler}"
    return None


def to_screen(raw_x, raw_y, cal):
    """the touch chip's raw reading -> a screen point. cal: [x at the left, x at the right, y at the top,
    y at the bottom, swap] (swap: the chip's x runs up and down the screen)"""
    if cal[4]:
        raw_x, raw_y = raw_y, raw_x
    x = (raw_x - cal[0]) * (W - 1) / ((cal[1] - cal[0]) or 1)
    y = (raw_y - cal[2]) * (H - 1) / ((cal[3] - cal[2]) or 1)
    return max(0, min(W - 1, round(x))), max(0, min(H - 1, round(y)))


class Touch:
    """A finger on the screen -> on a classic screen, KeyTimer presses and releases of the on-screen key under
    it; on the desk UI's screens, ui_touch("down" / "move" / "up", x, y) (the app takes it on its own thread)."""

    def __init__(self, timer, display, cal, ui=None, ui_touch=None):
        self.timer, self.display, self.cal, self.ui, self.ui_touch = timer, display, cal, ui, ui_touch
        self.raw, self.down, self.held, self.was_down, self.point = [0, 0], False, None, False, None

    def event(self, kind, code, value, now):
        if kind == EV_ABS and code in (ABS_X, ABS_Y):
            self.raw[code] = value
        elif kind == EV_KEY and code == BTN_TOUCH:
            self.down = bool(value)
        elif kind == EV_SYN:
            if self.ui is not None and not self.ui.classic and self.held is None:
                point = to_screen(*self.raw, self.cal)
                if self.down and not self.was_down:
                    self.ui_touch("down", *point)
                elif self.down and point != self.point:
                    self.ui_touch("move", *point)
                elif not self.down and self.was_down:
                    self.ui_touch("up", *(self.point or point))
                self.was_down, self.point = self.down, point if self.down else None
                return
            self.was_down = self.down
            want = button_at(*to_screen(*self.raw, self.cal)) if self.down else None
            if want != self.held:                 # a finger sliding off a button lets go of it
                if self.held:
                    self.timer.released(self.held, now)
                if want:
                    self.timer.pressed(want, now)
                self.held = want
                self.display.press(want)


def start_touch(timer, display, cal, device=None, ui=None, ui_touch=None):
    """read the touchscreen in a thread of its own; -> the device, or None without one"""
    device = device or find_touch()
    if not device:
        return None
    touch = Touch(timer, display, cal, ui, ui_touch)

    def body():
        with open(device, "rb", buffering=0) as f:
            while True:
                ready, _, _ = select.select([f], [], [], timer.timeout())
                now = time.monotonic()
                if ready:
                    data = f.read(EVENT.size * 16)
                    for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                        _, _, kind, code, value = EVENT.unpack_from(data, i)
                        touch.event(kind, code, value, now)
                timer.tick(now)

    threading.Thread(target=body, daemon=True).start()
    return device
