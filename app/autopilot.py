"""Autopilot for a set nobody programmed: a cue list is a pool of looks,
and every phrase (4 / 8 / 16 / 32 bars on the beat clock) the desk moves
to another one - a bigger look when the room is loud, a calmer one in a
breakdown, and the biggest on the drop.

How big a look is comes from the cue itself: how bright it is, whether it
runs effects, whether it strobes.  Looks are ranked against each other,
so "calm" and "big" mean the calmest and biggest third of THIS list.

Pure: the engine passes the cues and the room's energy in.
"""
from __future__ import annotations

import random

BARS = (4, 8, 16, 32)
TIERS = ("low", "mid", "high")
_HTP = ("dimmer", "zone_dimmer", "_vdim")


def cue_energy(cue: dict) -> float:
    """0..1: how big a look this cue is."""
    vals = cue.get("values") or {}
    levels = []
    strobing = False
    for row in vals.values():
        if not isinstance(row, dict):
            continue
        lv = [float(v) for r, v in row.items() if r.split("@")[0] in _HTP and isinstance(v, (int, float))]
        if lv:
            levels.append(max(lv) / 100.0)
        s = row.get("strobe", row.get("shutter"))
        if isinstance(s, (int, float)) and 20 < s < 250:
            strobing = True
    bright = sum(levels) / len(levels) if levels else 0.5
    score = 0.55 * min(1.0, bright) + (0.25 if cue.get("fx") else 0.0) + (0.2 if strobing else 0.0)
    return round(min(1.0, score), 3)


def tiers(stack: list[dict]) -> list[str]:
    """Each cue's place in this list: the calmest third, the middle, the biggest."""
    scores = [cue_energy(c) for c in stack]
    order = sorted(range(len(stack)), key=lambda i: (scores[i], i))
    out = ["mid"] * len(stack)
    n = len(stack)
    for rank, i in enumerate(order):
        out[i] = TIERS[min(2, rank * 3 // max(1, n))] if n >= 3 else "mid"
    return out


def room_tier(energy: float | None) -> str | None:
    if energy is None:
        return None
    return "low" if energy < 0.33 else "mid" if energy < 0.62 else "high"


def choose(stack: list[dict], current: int, want: str | None, history: list[int],
           rng: random.Random | None = None, biggest: bool = False) -> int | None:
    """The next cue (index) to go to."""
    n = len(stack)
    if n == 0:
        return None
    if n == 1:
        return 0
    if biggest:
        scores = [cue_energy(c) for c in stack]
        top = max(range(n), key=lambda i: (scores[i], -i))
        return top if top != current or n == 1 else sorted(range(n), key=lambda i: -scores[i])[1]
    rng = rng or random.Random()
    recent = set(history[-2:]) | {current}
    if want is None:
        # no sound to go by: round the list in order
        return (current + 1) % n
    t = tiers(stack)
    pool = [i for i in range(n) if t[i] == want and i not in recent]
    if not pool:
        pool = [i for i in range(n) if t[i] == want and i != current]
    if not pool:
        pool = [i for i in range(n) if i not in recent] or [i for i in range(n) if i != current]
    return rng.choice(pool)
