"""FX wave engine - pure functions, no state, no I/O.

The same maths runs twice per frame: once when the engine resolves DMX
values (app/engine.py) and once for the visualiser look feed, so the
beams you see and the bytes on the wire always agree.

Wave forms take ABSOLUTE cycles (elapsed * speed + phase + spread offset)
and return 0..1.  Fractional cycles select the point inside the current
wave; the integer part is the cycle counter (used by `random` so every
head re-rolls its value once per cycle instead of every frame).

Spread: within a selection of n heads, head i is offset by
`spread_deg * i / n` degrees of a full turn (0-360).  With 360 and four
heads that yields the classic 0/90/180/270 wave.

    value = base + depth * wave(kind, cycles)     (clamped to the range)
"""
from __future__ import annotations

import math

WAVES = ("sine", "saw", "square", "triangle", "random")

# sane operating envelope (Hz = full waves per second)
SPEED_MIN = 0.01
SPEED_MAX = 20.0
SPREAD_MAX = 720.0          # two full turns is plenty


def _hash01(a: int, b: int) -> float:
    """Deterministic 0..1 from two ints (no builtin hash randomisation)."""
    h = ((int(a) * 2654435761) ^ (int(b) * 40503)) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return h / float(0xFFFFFFFF)


def wave(kind: str, cycles: float, seed: int = 0) -> float:
    """Wave form value 0..1 at `cycles` (absolute position on the wave)."""
    c = float(cycles)
    whole = math.floor(c)
    frac = c - whole
    k = str(kind or "sine").lower()
    if k == "sine":
        return 0.5 - 0.5 * math.cos(2.0 * math.pi * frac)
    if k == "saw":
        return frac
    if k == "square":
        return 1.0 if frac < 0.5 else 0.0
    if k == "triangle":
        return 1.0 - abs(2.0 * frac - 1.0)
    if k == "random":
        return _hash01(seed, int(whole))
    raise ValueError(f"unknown wave {kind!r} - use one of {', '.join(WAVES)}")


def spread_cycles(spread_deg: float, index: int, count: int) -> float:
    """Phase offset in cycles for head `index` of `count` under a spread."""
    if count <= 1:
        return 0.0
    deg = float(spread_deg) % 360.0
    return deg * float(index) / float(count) / 360.0


def fx_value(kind: str, cycles: float, base: float, depth: float,
             seed: int = 0, low: int = 0, high: int = 100) -> int:
    """Integer attribute value: base + depth * wave, clamped to [low, high]."""
    v = float(base) + float(depth) * wave(kind, cycles, seed)
    if v < low:
        return int(low)
    if v > high:
        return int(high)
    return int(round(v))


def cycles_for(elapsed: float, speed: float, phase: float = 0.0,
               spread: float = 0.0, index: int = 0, count: int = 1) -> float:
    """Absolute cycle position for one head: time + phase + spread offset."""
    return (float(elapsed) * float(speed) + float(phase)
            + spread_cycles(spread, index, count))
