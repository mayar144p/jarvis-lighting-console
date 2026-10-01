"""The rig as pixels: every cell of a pixel bar or panel (a light with its
colour channels repeated - red@1 .. red@12) is a pixel of its own, at its
own place in the room; any other colour light is one pixel where it hangs.

Effects that paint across the room use them:

* **gradient** - two or more colours across the room in a direction (left
  to right, from the stage out, up, centre out, round the room), still or
  scrolling.
* **media** - a picture (or the frames of a video the browser plays) laid
  over the rig: seen from the front (x across, height up) or from above
  (x across, depth down), each pixel takes the colour under it.

Pure: positions in metres in, colours out.
"""
from __future__ import annotations

import base64
import math

from app import merge

COLOUR_ROLES = ("red", "green", "blue", "white", "amber", "uv", "lime", "cyan", "magenta", "yellow")
# metres between the cells of a bar when the fixture file doesn't say
CELL_PITCH = 0.083
SPACES = ("left-right", "right-left", "stage-out", "back-in", "up", "down", "centre-out", "outside-in", "around")
VIEWS = ("front", "top")
MAX_MEDIA = 96 * 96


def cells(head: dict) -> int:
    """How many colour cells a light has (1 for an ordinary light)."""
    reps = merge._repeated(head.get("map") or [])
    return max([reps.get(r, 0) for r in COLOUR_ROLES] + [1])


def units(heads: list[dict]) -> list[dict]:
    """[{n, k, x, y, z}] - k is the cell (None for a one-cell light).  A
    bar's cells run along it (its yaw), centred on where it hangs."""
    out = []
    for h in heads:
        roles = h.get("map") or []
        if not any(r in roles for r in COLOUR_ROLES):
            continue
        x, y, z = float(h.get("x") or 0), float(h.get("y") or 0), float(h.get("z") or 0)
        n = cells(h)
        if n <= 1:
            out.append({"n": h["head_no"], "k": None, "x": x, "y": y, "z": z})
            continue
        rot = h.get("rot") if isinstance(h.get("rot"), (list, tuple)) else [0, 0]
        yaw = math.radians(float(rot[0] or 0))
        ax, az = math.cos(yaw), -math.sin(yaw)
        for k in range(1, n + 1):
            off = (k - (n + 1) / 2.0) * CELL_PITCH
            out.append({"n": h["head_no"], "k": k, "x": x + ax * off, "y": y, "z": z + az * off})
    return out


def along(us: list[dict], space: str) -> list[float]:
    """Each unit's place 0..1 in a direction through the room."""
    if not us:
        return []
    cx = sum(u["x"] for u in us) / len(us)
    cz = sum(u["z"] for u in us) / len(us)
    metric = {
        "left-right": lambda u: u["x"], "right-left": lambda u: -u["x"],
        "stage-out": lambda u: u["z"], "back-in": lambda u: -u["z"],
        "up": lambda u: u["y"], "down": lambda u: -u["y"],
        "centre-out": lambda u: math.hypot(u["x"] - cx, u["z"] - cz),
        "outside-in": lambda u: -math.hypot(u["x"] - cx, u["z"] - cz),
        "around": lambda u: (math.atan2(u["z"] - cz, u["x"] - cx) + math.pi) / (2 * math.pi),
    }.get(space) or (lambda u: u["x"])
    vals = [metric(u) for u in us]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-6:
        return [i / max(1, len(us) - 1) for i in range(len(us))]
    return [(v - lo) / (hi - lo) for v in vals]


def plane(us: list[dict], view: str) -> list[tuple[float, float]]:
    """Each unit's (u, v) 0..1 on a picture laid over the rig: from the
    front (u across, v from the top down) or from above (v from the stage
    out)."""
    if not us:
        return []
    xs = [u["x"] for u in us]
    ys = [u["z"] if view == "top" else u["y"] for u in us]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, hgt = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    out = []
    for x, y in zip(xs, ys):
        u = (x - x0) / w if x1 - x0 > 1e-6 else 0.5
        v = (y - y0) / hgt if y1 - y0 > 1e-6 else 0.5
        out.append((u, v if view == "top" else 1.0 - v))
    return out


def _rgb(hexcol: str) -> tuple[int, int, int]:
    s = hexcol.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def gradient_at(colours: list[str], f: float) -> tuple[int, int, int]:
    """The colour at f (0..1) through the stops."""
    stops = [_rgb(c) for c in colours] or [(255, 255, 255)]
    if len(stops) == 1:
        return stops[0]
    f = max(0.0, min(1.0, f)) * (len(stops) - 1)
    i = min(int(f), len(stops) - 2)
    t = f - i
    a, b = stops[i], stops[i + 1]
    return tuple(int(round(a[j] + (b[j] - a[j]) * t)) for j in range(3))


def scroll(pos: float, offset: float) -> float:
    """A place moved along by `offset` rounds and folded back (there and
    back again), so a scrolling gradient never jumps."""
    u = (pos + offset) % 2.0
    return u if u <= 1.0 else 2.0 - u


def clean_media(w, h, data: str) -> tuple[int, int, bytes]:
    """A picture as the browser sends it: w x h RGB bytes, base64."""
    w, h = int(w), int(h)
    if not (1 <= w <= 96 and 1 <= h <= 96):
        raise ValueError("a picture is 1..96 pixels each way (the browser makes it small)")
    try:
        raw = base64.b64decode(str(data), validate=True)
    except (ValueError, TypeError):
        raise ValueError("the picture's pixels aren't base64") from None
    if len(raw) != w * h * 3:
        raise ValueError(f"a {w}x{h} picture is {w * h * 3} bytes, not {len(raw)}")
    return w, h, raw


def sample(w: int, h: int, raw: bytes, u: float, v: float) -> tuple[int, int, int]:
    """The colour under (u, v), blended between the four nearest pixels."""
    x = max(0.0, min(1.0, u)) * (w - 1)
    y = max(0.0, min(1.0, v)) * (h - 1)
    x0, y0 = int(x), int(y)
    x1, y1 = min(x0 + 1, w - 1), min(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0

    def px(i, j):
        o = (j * w + i) * 3
        return raw[o], raw[o + 1], raw[o + 2]
    a, b, c, d = px(x0, y0), px(x1, y0), px(x0, y1), px(x1, y1)
    return tuple(int(round((a[k] * (1 - fx) + b[k] * fx) * (1 - fy) + (c[k] * (1 - fx) + d[k] * fx) * fy))
                 for k in range(3))
