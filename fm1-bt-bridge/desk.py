"""The desk box's screen and touch: a 3.5" 480x320 SPI touchscreen (ILI9486 display, XPT2046/ADS7846
resistive touch) on a Pi 3/4, through the kernel's drivers (dtoverlay: a framebuffer at /dev/fb1 and a touch
input device).

Step one: the app's own 128x64 screens, three times larger and in colour, with big buttons under them for
the joystick and KEY1-3. Taps on the buttons become the same key events the pocket box's keys make (KeyTimer:
holds and repeats included), so every screen works on the desk box from the start.

HdmiMirror: the Studio's screen on a monitor or projector on the Pi's HDMI port too (its framebuffer, /dev/fb0
under KMS), scaled up by a whole number, only while one is plugged in, at most MIRROR_FPS.
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


MIRROR_FPS = 10


class HdmiMirror:
    """the touchscreen's frames, copied to the HDMI framebuffer: the largest whole-number scale that fits,
    centred on black. Its own thread, the newest frame only: the touchscreen never waits for it."""

    def __init__(self, device, size, bpp, stride, status=None):
        self.device, self.size, self.bpp, self.stride, self.status = device, size, bpp, stride, status
        self.frame, self.event = None, threading.Event()
        self.scale = max(1, min(size[0] // W, size[1] // H))
        self.at = ((size[0] - W * self.scale) // 2, (size[1] - H * self.scale) // 2)
        self.connected, self.checked, self.cleared = False, 0.0, False

    @classmethod
    def find(cls, skip="/dev/fb1", graphics=Path("/sys/class/graphics"), drm=Path("/sys/class/drm")):
        """the HDMI framebuffer (a KMS one, not the touchscreen's fbtft), or None"""
        for fb in sorted(graphics.glob("fb[0-9]*")):
            device = "/dev/" + fb.name
            try:
                name = (fb / "name").read_text().strip().lower()
                if device == skip or "fb_" in name or "ili" in name:       # (fbtft's: "fb_ili9486")
                    continue
                size = tuple(int(v) for v in (fb / "virtual_size").read_text().strip().split(","))
                bpp = int((fb / "bits_per_pixel").read_text())
                stride = int((fb / "stride").read_text()) if (fb / "stride").exists() else size[0] * bpp // 8
            except (OSError, ValueError):
                continue
            if bpp in (16, 32) and size[0] >= W and size[1] >= H:
                status = next(iter(sorted(drm.glob("card*-HDMI-A-*/status"))), None)
                return cls(device, size, bpp, stride, status)
        return None

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def show(self, rgb):
        """an (H, W, 3) uint8 array: the newest frame"""
        self.frame = rgb
        self.event.set()

    def plugged(self, now):
        if now - self.checked >= 5:             # (a monitor plugged in later: within 5 s)
            self.checked = now
            try:
                was, self.connected = self.connected, self.status is None or \
                    self.status.read_text().strip() == "connected"
            except OSError:
                was, self.connected = self.connected, False
            if self.connected and not was:
                self.cleared = False
        return self.connected

    def pixels(self, rgb):
        import numpy as np
        big = rgb.repeat(self.scale, axis=0).repeat(self.scale, axis=1)
        if self.bpp == 16:
            a = big.astype(np.uint16)
            return (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2")
        out = np.zeros(big.shape[:2] + (4,), dtype=np.uint8)            # XRGB8888: B, G, R, X in memory
        out[..., 0], out[..., 1], out[..., 2] = big[..., 2], big[..., 1], big[..., 0]
        return out

    def write(self, f, rgb):
        if not self.cleared:                    # the first frame on this monitor: black around it
            f.seek(0)
            f.write(bytes(self.stride * self.size[1]))
            self.cleared = True
        px = self.pixels(rgb)
        row = px.shape[1] * self.bpp // 8
        x = self.at[0] * self.bpp // 8
        for y in range(px.shape[0]):
            f.seek((self.at[1] + y) * self.stride + x)
            f.write(px[y].tobytes()[:row])

    def run(self):
        try:
            f = open(self.device, "r+b", buffering=0)
        except OSError:
            return
        with f:
            while True:
                self.event.wait()
                self.event.clear()
                if self.frame is not None and self.plugged(time.monotonic()):
                    try:
                        self.write(f, self.frame)
                    except OSError:
                        return
                time.sleep(1 / MIRROR_FPS)


def spi_hz(config=Path("/boot/firmware/config.txt")):
    """the touchscreen's SPI clock, from its dtoverlay's speed= (16 MHz without one)"""
    try:
        for line in config.read_text().splitlines():
            if line.startswith("dtoverlay=") and ("piscreen" in line or "waveshare35" in line):
                for part in line.split(","):
                    if part.startswith("speed="):
                        return int(part[6:])
    except (OSError, ValueError):
        pass
    return 16_000_000


def changed_strips(changed, gap=8):
    """a row a bool (changed) -> [(top, bottom)] of the strips of changed rows; strips under gap rows apart are
    one (a write each costs more than a few rows)"""
    strips = []
    for row, hit in enumerate(changed):
        if not hit:
            continue
        if strips and row - strips[-1][1] < gap:
            strips[-1][1] = row + 1
        else:
            strips.append([row, row + 1])
    return [tuple(s) for s in strips]


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
        self.hz, self.busy_until = spi_hz(), 0.0   # the SPI link; when the last frame is all on the screen
        self.mirror = HdmiMirror.find(skip=device)        # a monitor on HDMI shows it too
        if self.mirror:
            self.mirror.start()
        # (nothing drawn yet: the boot screen, deskboot.py, is already there, and the app's first frame replaces
        # it; the classic screens draw their whole screen with the first show())

    def base(self):
        key = (self.pressed, id(self.theme))
        if key not in self.bases:
            self.bases[key] = compose(Image.new("1", (128, 64)), self.pressed, self.theme)
        return self.bases[key]

    def show(self, img):
        """a pocket-box screen (128x64, 1 bit): scaled up, with the on-screen keys"""
        with self.lock:
            full = self.last is not None or not getattr(self, "shown", False)
            self.frame, self.last, self.shown = img.copy(), None, True
            self.write(0, H if full else SCREEN_AT[1] + 64 * SCALE + 4)

    def show_rgb(self, img):
        """a whole desk UI frame (RGB 480x320): only the rows that changed since the last one are sent, each
        changed strip on its own (a meter's two bars: not the scale between them). The driver sends the screen
        what was written, so less written is more frames a second over its SPI link."""
        import numpy as np
        rgb = np.asarray(img.convert("RGB"))
        a = rgb.astype(np.uint16)
        px = (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2")
        with self.lock:
            strips = [(0, H)] if self.last is None else changed_strips(np.any(px != self.last, axis=1))
            if not strips:
                return
            # no new frame while the last is still going over the SPI link: the panel shows rows as they come,
            # so writing over a frame mid-transfer tears it (a meter's bar half old, half new). The driver
            # sends what was written up to 1/30 s later (fbtft's deferred I/O), then at the link's speed.
            wait = self.busy_until - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, 0.25))
            self.last = px
            if self.mirror:
                self.mirror.show(rgb)
            rows = 0
            for top, bottom in strips:
                self.fb.seek(top * W * 2)
                self.fb.write(px[top:bottom].tobytes())
                rows += bottom - top
            self.busy_until = time.monotonic() + 1 / 30 + rows * W * 16 * 1.15 / self.hz

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


CAL_INSET = 30                          # the calibration targets: this far in from each corner


def calibration(points, inset=CAL_INSET):
    """the raw readings of a finger on the four targets (top left, top right, bottom left, bottom right) ->
    touch_cal for to_screen, or None if they make no sense (the same spot tapped twice, say)"""
    tl, tr, bl, br = points
    across_x = abs(tr[0] + br[0] - tl[0] - bl[0])  # how much each raw axis changes left to right
    across_y = abs(tr[1] + br[1] - tl[1] - bl[1])
    swap = across_y > across_x                       # the chip's x runs up and down the screen
    if swap:
        tl, tr, bl, br = [(p[1], p[0]) for p in (tl, tr, bl, br)]
    left, right = (tl[0] + bl[0]) / 2, (tr[0] + br[0]) / 2
    top, bottom = (tl[1] + tr[1]) / 2, (bl[1] + br[1]) / 2
    if abs(right - left) < 100 or abs(bottom - top) < 100:
        return None
    kx, ky = (right - left) / (W - 1 - 2 * inset), (bottom - top) / (H - 1 - 2 * inset)   # raw per pixel
    x0, y0 = left - inset * kx, top - inset * ky
    return [round(x0), round(x0 + kx * (W - 1)), round(y0), round(y0 + ky * (H - 1)), swap]


class Touch:
    """A finger on the screen -> on a classic screen, KeyTimer presses and releases of the on-screen key under
    it; on the desk UI's screens, ui_touch("down" / "move" / "up", x, y) (the app takes it on its own thread).
    While the desk UI calibrates (ui.cal is a list), a finger lifted is ui_touch("raw", raw x, raw y) instead."""

    def __init__(self, timer, display, cal, ui=None, ui_touch=None):
        self.timer, self.display, self.cal, self.ui, self.ui_touch = timer, display, cal, ui, ui_touch
        self.raw, self.down, self.held, self.was_down, self.point = [0, 0], False, None, False, None
        self.raw_down = None                  # calibrating: the reading while the finger was down

    def event(self, kind, code, value, now):
        if kind == EV_ABS and code in (ABS_X, ABS_Y):
            self.raw[code] = value
        elif kind == EV_KEY and code == BTN_TOUCH:
            self.down = bool(value)
        elif kind == EV_SYN:
            if self.ui is not None and getattr(self.ui, "cal", None) is not None:
                if self.down:
                    self.raw_down = tuple(self.raw)
                elif self.was_down and self.raw_down:
                    self.ui_touch("raw", *self.raw_down)
                self.was_down, self.point = self.down, None
                return
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
