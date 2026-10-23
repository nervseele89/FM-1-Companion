"""The sample editor's sums: a recording, or a sample already in the library, -> an edited sample WAV in the
samples folder, where Sloop > Load sample picks it up. An edit is always saved as a new file. The box's editor screen (sampleui.py) and the web page use the same steps,
so a preview sounds the way the saved file will. numpy does the work: a minute of 44.1 kHz audio is 2.6 M
samples, far too many for plain Python on a Zero W.

An edit: start and end (seconds into the take), fade_in and fade_out (seconds), normalize (the peak to the
Sample level) and reverse. The note it plays goes into the file's name (kick_C2.wav), where SLOOP looks for
the root note, so the sample plays in tune across the keyboard.
"""
import re
import wave
from pathlib import Path

import library
import meters

MAX_SECONDS = 120                       # how much of a take is read: the sound starts within two minutes
SILENCE = 32768 * 10 ** (-45 / 20)      # -45 dBFS: quieter than this is the silence around a take
NOTES = (24, 96)                        # the Note setting's range: C1..C7
SHORTEST = 0.02                         # seconds: a selection can't be shorter
DEFAULT_EDIT = {"fade_in": 0.0, "fade_out": 0.01, "normalize": True, "reverse": False}
NOTE_NAME = re.compile(r"(?<![A-Za-z])([A-G])([#b]?)(-?\d)(?!\d)")      # as SLOOP finds one in a file name


def np():
    meters.load_numpy()
    import numpy
    return numpy


def load(path):
    """-> (rate, mono float32 samples) of a 16-bit WAV, up to MAX_SECONDS of it"""
    n = np()
    with wave.open(str(path)) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        if w.getsampwidth() != 2:
            raise ValueError(f"{Path(path).name}: not a 16-bit WAV")
        data = w.readframes(min(w.getnframes(), rate * MAX_SECONDS))
    return rate, n.frombuffer(data, dtype="<i2").reshape(-1, channels).astype(n.float32).mean(axis=1)


def suggest(x, rate):
    """(start, end) in seconds for a new edit: the first sound to the last, at most a SLOOP slot long"""
    n = np()
    total = len(x) / rate
    level = n.abs(x)
    quiet = max(SILENCE, 0.03 * float(level.max()) if len(x) else 0.0)   # -30 dB under the peak: a take's hiss
    loud = n.nonzero(level > quiet)[0]                                    # isn't its sound
    if not len(loud):
        return 0.0, round(min(total, library.SLOT_SECONDS), 3)
    start = max(0, int(loud[0]) - rate // 200) / rate
    end = min(int(loud[-1]) / rate + 0.05, start + library.SLOT_SECONDS, total)
    return round(start, 3), round(end, 3)


def detect_note(x, rate, start, end):
    """the MIDI note the selection plays, from a moment past its attack; None without a clear pitch"""
    a, b = int(start * rate), int(end * rate)
    a += (b - a) // 10
    freq, _ = meters.pitch(x[a:min(b, a + 4096)], rate, lo=30.0, hi=2000.0)
    return meters.note_of(freq)[3] if freq else None


def parse_note(text, strict=True):
    """"A3" / "F#2" / "Bb4" -> MIDI note (C4 = 60, as SLOOP), None for "" or None. Outside C1..C7: an error,
    or with strict=False (a note read from a file's name) None."""
    if not text:
        return None
    m = NOTE_NAME.fullmatch(text.strip())
    if not m:
        raise ValueError(f"{text}: not a note (A3, F#2...)")
    note = (int(m.group(3)) + 1) * 12 + "C D EF G A B".index(m.group(1)) + {"#": 1, "b": -1, "": 0}[m.group(2)]
    if not NOTES[0] <= note <= NOTES[1]:
        if not strict:
            return None
        raise ValueError(f"{text}: outside C1..C7")
    return note


def render(x, rate, start, end, fade_in=0.0, fade_out=0.01, normalize=True, reverse=False):
    """the edited selection as int16 samples"""
    n = np()
    a, b = max(0, int(start * rate)), min(len(x), int(end * rate))
    if b - a < SHORTEST * rate:
        raise ValueError("the selection is too short")
    y = x[a:b].copy()
    if reverse:
        y = y[::-1].copy()
    fi, fo = min(len(y), int(fade_in * rate)), min(len(y), int(fade_out * rate))
    if fi:
        y[:fi] *= n.linspace(0.0, 1.0, fi, dtype=n.float32)
    if fo:
        y[len(y) - fo:] *= n.linspace(1.0, 0.0, fo, dtype=n.float32)
    peak = float(n.abs(y).max())
    if normalize and peak > 0:
        y *= library.sample_peak() / peak
    return n.clip(n.round(y), -32768, 32767).astype("<i2")


def write(path, rate, pcm):
    """mono 16-bit WAV"""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def file_name(name, note):
    """"kick" and 36 -> "kick_C2.wav": letters, digits and dashes, and the note last, where SLOOP reads it.
    Anything in the name that looks like a note (SLOOP would take the first one) is taken out."""
    base = NOTE_NAME.sub("", re.sub(r"[^A-Za-z0-9-]+", "-", name or ""))
    base = re.sub(r"-{2,}", "-", base).strip("-")[:24] or "sample"
    return f"{base}_{meters.note_name(note)}.wav" if note is not None else f"{base}.wav"


def save_from(x, rate, name, note, start, end, **edit):
    """the edited selection into the samples folder -> its path (a number added if the name is taken)"""
    pcm = render(x, rate, start, end, **edit)
    folder = library.folder("samples")
    out, k = folder / file_name(name, note), 2
    while out.exists():
        out, k = folder / file_name(f"{name}-{k}", note), k + 1
    tmp = out.with_name("." + out.name + ".part")
    write(tmp, rate, pcm)
    tmp.replace(out)
    return out


def save(recording, name, note, start, end, **edit):
    rate, x = load(recording)
    return save_from(x, rate, name, note, start, end, **edit)


def is_sample(path):
    return Path(path).parent.name == "samples"


def default_name(path):
    """a recording 2026-10-22_101544_fm1.wav -> "rec-101544"; a sample kick_C2.wav -> "kick" """
    stem = Path(path).stem
    if is_sample(path):
        return re.sub(r"[-_]+$", "", NOTE_NAME.sub("", stem)) or "sample"
    return "rec-" + (stem.split("_")[1] if stem.count("_") >= 2 else stem[:8])


def opening(path, x, rate):
    """(start, end, note) an edit starts from. A recording: the sound in it, at most a slot long, and the
    note found there. A sample: all of it, and the note in its name (else the one found)."""
    if is_sample(path):
        start, end = 0.0, round(len(x) / rate, 3)
        m = NOTE_NAME.search(Path(path).stem)
        note = parse_note("".join(m.groups()), strict=False) if m else None
        return start, end, note if note is not None else detect_note(x, rate, start, end)
    start, end = suggest(x, rate)
    return start, end, detect_note(x, rate, start, end)


def info(path):
    """what an editor opens with: the length and rate, the selection to start from, its note and a name"""
    rate, x = load(path)
    start, end, note = opening(path, x, rate)
    return {"seconds": round(len(x) / rate, 3), "rate": rate, "start": start, "end": end,
            "note": meters.note_name(note) if note is not None else None, "name": default_name(path),
            "slot_seconds": library.SLOT_SECONDS}


def waveform(x, columns):
    """(low, high) per column, -1..1, for drawing the take"""
    n = np()
    if len(x) < columns:
        return [(0.0, 0.0)] * columns
    edges = n.linspace(0, len(x), columns + 1).astype(int)[:-1]
    full = max(1.0, float(n.abs(x).max()))
    return list(zip((n.minimum.reduceat(x, edges) / full).tolist(), (n.maximum.reduceat(x, edges) / full).tolist()))
