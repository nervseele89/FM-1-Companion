"""Faster WAV -> sample slot conversion: the same results as SLOOP's sampleio (vendored, unchanged), byte for byte,
in fewer Python steps. On a Zero W sampleio took ~14 s for a 7-second WAV (resampling and ADPCM encoding a
sample at a time in Python loops); here the same arithmetic runs in the same order through comprehensions,
map() and itertools, and the encoder's loop does less per sample.
"""
from itertools import accumulate, repeat
from operator import add, mul, sub, truediv

import sampleio as sio


def read_any_wav(path):
    """sampleio.read_any_wav, the same floats: 16-bit PCM (nearly every WAV) through array and map() (scaling by
    1/32768 and halving are exact in binary floating point); other formats by sampleio itself"""
    from array import array
    import sys
    d = path.read_bytes()
    ch = sio.wav_chunks(d)
    tag, nch, sr, bits = sio.wav_format(d, ch)
    if tag != 1 or bits != 16 or nch not in (1, 2):
        return sio.read_any_wav(path)
    o, n = ch.get(b"data", (0, 0))
    frames = min(n, len(d) - o) // (2 * nch)
    raw = array("h")
    raw.frombytes(d[o:o + frames * 2 * nch])
    if sys.byteorder == "big":
        raw.byteswap()
    v = list(map((1 / 32768).__mul__, raw))
    if nch == 1:
        return sr, v
    return sr, list(map((0.5).__mul__, map(add, v[0::2], v[1::2])))


def resample(x, sr, to):
    """sampleio.resample, the same values: moving average over the ratio, then linear interpolation. Done in
    C-level passes (map, slicing) instead of a Python loop a sample: on a Zero W that loop took ~5 s for 7 s."""
    if sr == to or not x:
        return x if sr == to else []
    if sr == 2 * to:                          # 44.1 kHz: averaged pairs, and only the ones kept (every other one)
        return list(map((0.5).__mul__, map(add, x[0::2], x[1::2]))) if len(x) > 1 else []
    if sr > to:
        k = max(1, int(round(sr / to)))
        if k == 2:                            # sampleio: [(a + b) / 2 for a, b in zip(x, x[1:])] + [x[-1] / 2]
            x = list(map((0.5).__mul__, map(add, x, x[1:]))) + [x[-1] / 2]
        elif k > 1:                           # sampleio: reduce(add, x[i:i + k]) / k, left to right
            n = len(x)
            sums = x[:]
            for j in range(1, min(k, n)):     # adds the next value to each window, in the same order
                sums[:n - j] = map(add, sums[:n - j], x[j:])
            x = list(map(truediv, sums, repeat(k)))
    step, last = sr / to, len(x) - 1
    if last <= 0:
        return []
    if step == int(step):                     # a whole ratio (44.1 kHz -> 22.05 kHz): positions 0, 2, 4...
        return x[0:last:int(step)]            # x[i] * 1.0 + x[i + 1] * 0.0 is x[i]
    count = int(last / step) + 2              # positions summed one step at a time, as sampleio does
    positions = list(accumulate(repeat(step, count), initial=0.0))
    while positions and positions[-1] >= last:
        positions.pop()
    idx = list(map(int, positions))
    frac = list(map(sub, positions, idx))
    get = x.__getitem__
    return list(map(add, map(mul, map(get, idx), map(sub, repeat(1), frac)),   # x[i] * (1 - f) + x[i + 1] * f
                    map(mul, map(get, map((1).__add__, idx)), frac)))


def to_int16(x, peak=30000):
    """sampleio.to_int16: peak normalised to `peak` (30000 there, ~0.8 dB below full scale); sampleio clamps to
    16 bits, which a value scaled by its own peak never needs (peak is at most 32767)"""
    pk = sio.peak(x)
    return [int(v / pk * peak) for v in x]


def _tables():
    """Per step index: the 7 thresholds that decide a sample's code (from sampleio's comparisons: the code's
    3 bits are diff >= step, then >= step>>1, then >= step>>2 of what's left, which is monotonic in diff),
    the predictor change for each code, and the next step index for each code (clamped to 0..88)."""
    thresholds, deltas, nexts = [], [], []
    for idx, step in enumerate(sio.IMA_STEP):
        half, quarter, eighth = step >> 1, step >> 2, step >> 3
        thresholds.append((quarter, half, half + quarter, step, step + quarter, step + half, step + half + quarter))
        deltas.append(tuple(eighth + (step if m & 4 else 0) + (half if m & 2 else 0) + (quarter if m & 1 else 0)
                            for m in range(8)))
        nexts.append(tuple(min(88, max(0, idx + sio.IMA_IDX[m])) for m in range(8)))
    return thresholds, deltas, nexts


_THR, _DELTA, _NEXT = _tables()


def ima_encode(samples, loop_start):
    """sampleio.ima_encode, the same bytes: each sample's code found by one bisect over its step's thresholds
    (in C), the predictor change and next step looked up, instead of a chain of Python comparisons"""
    from bisect import bisect_right
    pred, idx, state_at_loop = 0, 0, (0, 0)
    thr, delta, nxt = _THR[0], _DELTA[0], _NEXT[0]
    codes = bytearray(len(samples))
    if loop_start:                            # a loop later in the sample: note the state there on the way
        return _encode_from(samples, loop_start)
    for n, x in enumerate(samples):
        diff = x - pred
        if diff < 0:
            m = bisect_right(thr, -diff)
            pred -= delta[m]
            if pred < -32768:
                pred = -32768
            codes[n] = m | 8
        else:
            m = bisect_right(thr, diff)
            pred += delta[m]
            if pred > 32767:
                pred = 32767
            codes[n] = m
        idx = nxt[m]
        thr, delta, nxt = _THR[idx], _DELTA[idx], _NEXT[idx]
    if len(codes) & 1:
        codes.append(0)
    return bytes(map(add, codes[0::2], [b << 4 for b in codes[1::2]])), state_at_loop


def _encode_from(samples, loop_start):
    """ima_encode with a loop point: the plain loop, checking for the loop point (rare: sampleio uses 0)"""
    from bisect import bisect_right
    pred, idx, state_at_loop = 0, 0, (0, 0)
    thr, delta, nxt = _THR[0], _DELTA[0], _NEXT[0]
    codes = bytearray(len(samples))
    for n, x in enumerate(samples):
        if n == loop_start:
            state_at_loop = (pred, idx)
        diff = x - pred
        if diff < 0:
            m = bisect_right(thr, -diff)
            pred -= delta[m]
            if pred < -32768:
                pred = -32768
            codes[n] = m | 8
        else:
            m = bisect_right(thr, diff)
            pred += delta[m]
            if pred > 32767:
                pred = 32767
            codes[n] = m
        idx = nxt[m]
        thr, delta, nxt = _THR[idx], _DELTA[idx], _NEXT[idx]
    if len(codes) & 1:
        codes.append(0)
    return bytes(map(add, codes[0::2], [b << 4 for b in codes[1::2]])), state_at_loop


def user_slot(name, zones):
    """sampleio.user_slot with the faster encoder"""
    real = sio.ima_encode
    sio.ima_encode = ima_encode
    try:
        return sio.user_slot(name, zones)
    finally:
        sio.ima_encode = real
