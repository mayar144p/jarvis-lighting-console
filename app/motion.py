"""Movement effects that stay where they are aimed.

The first movement effects drove pan and tilt across a head's WHOLE travel
(540 x 190 degrees on a typical mover) at one cycle a second: the real
heads thrashed, pointed at walls and ceilings, and never looked like the
neat circle the 3D view drew.  Here a movement is a shape of a given size
in DEGREES around where the head is aimed right now (the programmer or the
cue underneath), fitted inside the head's own limits - shrunk and shifted
to fit, never clipped flat against an edge - and never asked to move faster
than the motor can.

Pure functions: no engine, no I/O.  Fractions are 0..1 of a head's travel.
"""
from __future__ import annotations

import math

# the movement effects this module drives (fxlib names)
KINDS = ("circle", "figure_eight", "pan_sweep", "tilt_bounce", "fan_pan")

# seconds for a FULL pan / tilt at top speed until a model is calibrated
# (the same numbers the visualiser's motor model uses)
TRAVEL_S = {"moving_beam": (2.2, 1.3), "moving_spot": (3.0, 1.8), "moving_wash": (3.2, 1.9),
            "moving_hybrid": (2.8, 1.7), "moving_bar": (3.0, 1.5)}
DEFAULT_TRAVEL_S = (3.0, 1.8)
DEFAULT_SPAN_DEG = (540.0, 270.0)

# a motor asked for more than this share of its top speed stutters or skips
_MOTOR_HEADROOM = 0.6


def fit(centre: float, amp: float, lo: float = 0.0, hi: float = 1.0) -> tuple[float, float]:
    """(centre, amplitude) of a swing that fits inside lo..hi: the swing
    shrinks to the room there is and the centre moves off the edge - the
    shape stays whole instead of flattening against a limit."""
    lo, hi = (lo, hi) if lo <= hi else (hi, lo)
    amp = max(0.0, min(float(amp), (hi - lo) / 2.0))
    return min(max(float(centre), lo + amp), hi - amp), amp


def _pingpong(u: float) -> float:
    """0..1..0 over one cycle - an arc goes out and comes back smoothly."""
    u %= 1.0
    return 2.0 * u if u < 0.5 else 2.0 * (1.0 - u)


def _ease(u: float) -> float:
    """Slow down at the ends of an arc, like an operator's hand would."""
    return 0.5 - 0.5 * math.cos(math.pi * u)


def shape(kind: str, cycles: float, p: dict, index: int = 0, count: int = 1) -> tuple[float | None, float | None]:
    """Unit offsets (-1..1, -1..1) of the shape at `cycles` (turns done),
    for pan and tilt; None for an axis the shape leaves alone."""
    count = max(1, int(count))
    spread = float(p.get("spread", 0.0)) / 360.0 * (index / count)
    phase = float(p.get("phase", 0.0)) / 100.0
    t = cycles + spread + phase
    direction = -1.0 if float(p.get("direction", 1.0)) < 0 else 1.0
    arc = max(10.0, min(360.0, float(p.get("arc", 360.0))))
    if kind == "circle":
        if arc >= 359.0:
            a = direction * 2.0 * math.pi * t
        else:                               # part of a circle: out and back
            half = math.radians(arc) / 2.0
            a = direction * (-half + 2.0 * half * _ease(_pingpong(t)))
        return math.cos(a), math.sin(a)
    if kind == "figure_eight":
        a = direction * 2.0 * math.pi * t
        return math.sin(a), math.sin(2.0 * a)
    if kind == "pan_sweep":
        return direction * math.sin(2.0 * math.pi * t), None
    if kind == "tilt_bounce":
        return None, direction * math.sin(2.0 * math.pi * t)
    if kind == "fan_pan":
        # heads spread evenly across the size, swaying gently together
        pos = (index / (count - 1)) * 2.0 - 1.0 if count > 1 else 0.0
        return pos * 0.8 + 0.2 * math.sin(2.0 * math.pi * t), None
    raise ValueError(f"not a movement effect: {kind}")


def amplitude(kind: str, p: dict, span_deg: tuple[float, float]) -> tuple[float, float]:
    """Swing per axis as a fraction of travel, from the size in degrees."""
    size = max(1.0, min(270.0, float(p.get("size", 20.0))))
    pan_span = float(span_deg[0] or DEFAULT_SPAN_DEG[0])
    tilt_span = float(span_deg[1] or DEFAULT_SPAN_DEG[1])
    if kind == "pan_sweep" and float(p.get("arc", 360.0)) < 359.0:
        size = float(p["arc"]) / 2.0        # a 180-degree sweep: +/-90 degrees
    return size / pan_span, size / tilt_span


def max_rate(kinds_amp: list[tuple[str, float, float]], travel_s: list[tuple[float, float]]) -> float:
    """The fastest turns-per-second every head can follow: a sine swing of
    amplitude A (fraction of travel) at f turns/s peaks at 2*pi*f*A, and a
    motor covers its whole travel in `travel_s` at best."""
    best = math.inf
    for (_kind, ap, at), (ps, ts) in zip(kinds_amp, travel_s):
        for amp, secs in ((ap, ps), (at, ts)):
            if amp > 1e-6 and secs > 0:
                best = min(best, _MOTOR_HEADROOM / (secs * 2.0 * math.pi * amp))
    return best


def position(kind: str, cycles: float, p: dict, centre: tuple[float, float],
             limits: tuple[tuple[float, float], tuple[float, float]],
             span_deg: tuple[float, float], index: int = 0, count: int = 1) -> dict:
    """{"pan": frac, "tilt": frac} for one head at one moment - only the axes
    the shape moves and the lock leaves free."""
    lock = int(round(float(p.get("lock", 0.0))))        # 1: tilt stays, 2: pan stays
    dp, dt = shape(kind, cycles, p, index, count)
    ap, at = amplitude(kind, p, span_deg)
    out = {}
    if dp is not None and lock != 2:
        c, a = fit(centre[0], ap, *limits[0])
        out["pan"] = c + a * dp
    if dt is not None and lock != 1:
        c, a = fit(centre[1], at, *limits[1])
        out["tilt"] = c + a * dt
    return {k: min(1.0, max(0.0, v)) for k, v in out.items()}
