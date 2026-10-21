"""Analyse the bridge's Bluetooth MIDI timing log (/dev/shm/fm1-midi-timing.log on the Pi).

Each line: arrival time (ms, Pi clock), the sender's 13-bit BLE MIDI timestamp (ms), packet bytes.
Arrival minus sender time is the delay the radio added plus a constant clock offset, so its spread
is the timing jitter you hear, independent of how evenly the notes were played.

  python midi_timing.py LOGFILE
"""
import statistics
import sys


def load(path):
    rows = []
    for line in open(path):
        parts = line.split()
        if len(parts) >= 2:
            rows.append((float(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return rows


def wrap(x):
    """Into [-4096, 4096): the sender's clock wraps every 8192 ms."""
    return (x + 4096) % 8192 - 4096


def analyse(rows):
    if len(rows) < 10:
        return "not enough packets (%d)" % len(rows)
    raw = [arrival - sent for arrival, sent, _ in rows]
    centre = statistics.median([r % 8192 for r in raw])
    offset = [wrap(r - centre) for r in raw]
    base = min(offset)
    late = sorted(o - base for o in offset)          # extra delay over the best packet, ms
    pct = lambda p: late[min(len(late) - 1, int(p / 100 * len(late)))]
    gaps = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] - a[0] > 0.5)
    interval = statistics.median(gaps[: max(1, len(gaps) // 10)]) if gaps else 0
    notes = sum(1 for _, _, data in rows for i in range(0, len(data), 2) if data[i:i + 1] == "9")
    span = (rows[-1][0] - rows[0][0]) / 1000
    return "\n".join([
        f"packets: {len(rows)} over {span:.0f} s (about {notes} note-on/channel bytes)",
        f"extra delay (ms) median {pct(50):.0f}, 90% {pct(90):.0f}, 99% {pct(99):.0f}, worst {late[-1]:.0f}",
        f"shortest gaps between packets: ~{interval:.1f} ms (hints at the BLE connection interval)",
    ])


if __name__ == "__main__":
    print(analyse(load(sys.argv[1])))
