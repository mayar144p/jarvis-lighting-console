"""Ways to make a room besides drawing it: a shape with typed sizes, and a
room described in words ("a 12 x 8 m club, bar on the left, DJ booth on a
40 cm riser").

Both end in a *spec* - a small dict of what the room is and what is in it
- and `build(spec)` turns a spec into a venue (app/venue.py): the outline,
and, when asked, a starter layout that fits the shape: stage or DJ booth at
the stage end, a dance floor, the bar, trusses spanning the room wall to
wall at their depth.  The AI fills the same spec (SPEC_SCHEMA), so what it
builds is exactly what the offline parser would.

Coordinates as in venue.py: x across (0 = centre line), z from the back
wall (the stage end) towards the audience; the back wall sits at `back`.

Pure: no engine state, tested directly.
"""
from __future__ import annotations

import copy
import math
import re

from app import venue as venue_mod

SHAPES = {
    "rectangle": "Rectangle",
    "l": "L-shape",
    "t": "T-shape",
    "u": "U-shape",
    "octagon": "Octagon (cut corners)",
    "round": "Round / oval",
    "wedge": "Wedge (fan-shaped)",
}
CORNERS = ("front-left", "front-right", "back-left", "back-right")
SIDES = ("back", "front", "left", "right", "centre")
BACK = -1.0

# (width, depth, height) when the words don't say
KIND_SIZE = {
    "club": (16.0, 22.0, 5.5), "small_club": (10.0, 14.0, 3.6), "bar": (10.0, 14.0, 3.4),
    "warehouse": (30.0, 40.0, 9.0), "concert": (24.0, 32.0, 11.0), "theatre": (16.0, 26.0, 9.0),
    "ballroom": (20.0, 24.0, 5.0), "event": (20.0, 24.0, 5.0), "outdoor": (24.0, 30.0, 9.0),
}


def _f(value, default, lo, hi) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(v) or math.isinf(v):
        return float(default)
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# outlines
# ---------------------------------------------------------------------------
def outline(shape: str, width: float, depth: float, back: float = BACK,
            cut_w: float | None = None, cut_d: float | None = None,
            corner: str = "front-right", sides: int = 24) -> list[list[float]]:
    """The room's corners, [x, z], going round (for a polygon floor)."""
    shape = normalise_shape(shape)
    W, D = float(width), float(depth)
    x0, x1, z0, z1 = -W / 2, W / 2, back, back + D
    if shape == "rectangle":
        pts = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
    elif shape == "l":
        cw = _f(cut_w, W / 2, 0.5, W - 0.5)
        cd = _f(cut_d, D / 2, 0.5, D - 0.5)
        corner = corner if corner in CORNERS else "front-right"
        # the rectangle, then take the corner out
        if corner == "front-right":
            pts = [[x0, z0], [x1, z0], [x1, z1 - cd], [x1 - cw, z1 - cd], [x1 - cw, z1], [x0, z1]]
        elif corner == "front-left":
            pts = [[x0, z0], [x1, z0], [x1, z1], [x0 + cw, z1], [x0 + cw, z1 - cd], [x0, z1 - cd]]
        elif corner == "back-right":
            pts = [[x0, z0], [x1 - cw, z0], [x1 - cw, z0 + cd], [x1, z0 + cd], [x1, z1], [x0, z1]]
        else:
            pts = [[x0, z0 + cd], [x0 + cw, z0 + cd], [x0 + cw, z0], [x1, z0], [x1, z1], [x0, z1]]
    elif shape == "t":
        # the wide bar at the stage end, the stem towards the audience
        # (corner "front": the other way round)
        stem = _f(cut_w, W / 2, 1.0, W - 0.5)
        bar = _f(cut_d, D * 0.45, 1.0, D - 1.0)
        s0, s1 = -stem / 2, stem / 2
        if corner == "front":
            pts = [[s0, z0], [s1, z0], [s1, z1 - bar], [x1, z1 - bar], [x1, z1], [x0, z1], [x0, z1 - bar], [s0, z1 - bar]]
        else:
            pts = [[x0, z0], [x1, z0], [x1, z0 + bar], [s1, z0 + bar], [s1, z1], [s0, z1], [s0, z0 + bar], [x0, z0 + bar]]
    elif shape == "u":
        # a notch into one wall (front by default): a room round a courtyard,
        # or two wings either side of a stair
        nw = _f(cut_w, W / 3, 0.5, W - 1.0)
        nd = _f(cut_d, D * 0.4, 0.5, D - 1.0)
        n0, n1 = -nw / 2, nw / 2
        if corner == "back":
            pts = [[x0, z0], [n0, z0], [n0, z0 + nd], [n1, z0 + nd], [n1, z0], [x1, z0], [x1, z1], [x0, z1]]
        else:
            pts = [[x0, z0], [x1, z0], [x1, z1], [n1, z1], [n1, z1 - nd], [n0, z1 - nd], [n0, z1], [x0, z1]]
    elif shape == "octagon":
        c = _f(cut_w, min(W, D) * 0.29, 0.2, min(W, D) / 2 - 0.1)
        pts = [[x0 + c, z0], [x1 - c, z0], [x1, z0 + c], [x1, z1 - c],
               [x1 - c, z1], [x0 + c, z1], [x0, z1 - c], [x0, z0 + c]]
    elif shape == "round":
        n = int(_f(sides, 24, 8, 64))
        cz = (z0 + z1) / 2
        pts = [[W / 2 * math.cos(2 * math.pi * i / n), cz + D / 2 * math.sin(2 * math.pi * i / n)]
               for i in range(n)]
    elif shape == "wedge":
        # narrow at the stage end, wide at the back of the audience (a
        # fan-shaped hall); corner "back" narrows the other end
        narrow = _f(cut_w, W * 0.55, 1.0, W)
        if corner == "back":
            pts = [[x0, z0], [x1, z0], [narrow / 2, z1], [-narrow / 2, z1]]
        else:
            pts = [[-narrow / 2, z0], [narrow / 2, z0], [x1, z1], [x0, z1]]
    else:
        raise ValueError(f"unknown room shape {shape!r}; try: {', '.join(SHAPES)}")
    return [[round(x, 3), round(z, 3)] for x, z in pts]


def normalise_shape(shape) -> str:
    s = str(shape or "rectangle").strip().lower().replace("-shape", "").replace("_shape", "").replace(" shape", "")
    return {"rect": "rectangle", "box": "rectangle", "square": "rectangle", "oval": "round",
            "circle": "round", "circular": "round", "octagonal": "octagon", "fan": "wedge",
            "l-shaped": "l", "t-shaped": "t", "u-shaped": "u"}.get(s, s)


def inside(x: float, z: float, pts) -> bool:
    return venue_mod.point_in_polygon(x, z, pts)


def span_at(pts, z: float) -> tuple[float, float] | None:
    """The widest stretch of floor across the room at depth z: (x0, x1)."""
    xs = []
    n = len(pts)
    for i in range(n):
        (ax, az), (bx, bz) = pts[i], pts[(i + 1) % n]
        if (az <= z < bz) or (bz <= z < az):
            xs.append(ax + (z - az) * (bx - ax) / (bz - az))
    xs.sort()
    best = None
    for i in range(0, len(xs) - 1, 2):
        if best is None or xs[i + 1] - xs[i] > best[1] - best[0]:
            best = (xs[i], xs[i + 1])
    return best


def depth_at(pts, x: float) -> tuple[float, float] | None:
    """The longest stretch of floor front to back at x: (z0, z1)."""
    flipped = [[p[1], p[0]] for p in pts]
    return span_at(flipped, x)


def clip(subject, x0: float, z0: float, x1: float, z1: float) -> list[list[float]]:
    """The part of a polygon inside a rectangle (Sutherland-Hodgman)."""
    def cut(poly, keep, meet):
        out = []
        for i in range(len(poly)):
            cur, prev = poly[i], poly[i - 1]
            if keep(cur):
                if not keep(prev):
                    out.append(meet(prev, cur))
                out.append(cur)
            elif keep(prev):
                out.append(meet(prev, cur))
        return out

    def at_x(xv):
        return lambda a, b: [xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / ((b[0] - a[0]) or 1e-9)]

    def at_z(zv):
        return lambda a, b: [a[0] + (b[0] - a[0]) * (zv - a[1]) / ((b[1] - a[1]) or 1e-9), zv]

    poly = [list(p) for p in subject]
    for keep, meet in ((lambda p: p[0] >= x0, at_x(x0)), (lambda p: p[0] <= x1, at_x(x1)),
                       (lambda p: p[1] >= z0, at_z(z0)), (lambda p: p[1] <= z1, at_z(z1))):
        if not poly:
            break
        poly = cut(poly, keep, meet)
    out = []
    for p in poly:
        q = [round(p[0], 3), round(p[1], 3)]
        if not out or abs(out[-1][0] - q[0]) > 1e-3 or abs(out[-1][1] - q[1]) > 1e-3:
            out.append(q)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-3 and abs(out[0][1] - out[-1][1]) < 1e-3:
        out.pop()
    return out if len(out) >= 3 else []


# ---------------------------------------------------------------------------
# spec -> venue
# ---------------------------------------------------------------------------
def clean_spec(raw: dict | None) -> dict:
    raw = dict(raw or {})
    kind = str(raw.get("kind") or "club").lower().replace(" ", "_")
    kind = kind if kind in KIND_SIZE else "club"
    w0, d0, h0 = KIND_SIZE[kind]
    spec = {
        "name": str(raw.get("name") or "")[:60],
        "kind": kind,
        "shape": normalise_shape(raw.get("shape")),
        "width": round(_f(raw.get("width"), w0, 3, 120), 2),
        "depth": round(_f(raw.get("depth"), d0, 3, 160), 2),
        "height": round(_f(raw.get("height"), h0, 2.2, 40), 2),
        "corner": str(raw.get("corner") or ""),
        "layout": raw.get("layout") is not False,
    }
    if spec["shape"] not in SHAPES:
        spec["shape"] = "rectangle"
    for k in ("cut_w", "cut_d"):
        if raw.get(k) is not None:
            spec[k] = round(_f(raw.get(k), 0, 0.2, 120), 2)

    def side(v, default):
        v = str(v or default).lower()
        return v if v in SIDES else default

    def part(key, fields):
        v = raw.get(key)
        if v is None or v is False:
            return None
        v = v if isinstance(v, dict) else {}
        return {f: fn(v.get(f)) for f, fn in fields.items()}

    spec["stage"] = part("stage", {"width": lambda x: _f(x, 0, 0, 40), "depth": lambda x: _f(x, 0, 0, 20),
                                   "height": lambda x: _f(x, 0.8, 0, 3)})
    spec["dj"] = part("dj", {"side": lambda x: side(x, "back"), "riser": lambda x: _f(x, 0, 0, 2.5)})
    spec["bar"] = part("bar", {"side": lambda x: side(x, "front"), "length": lambda x: _f(x, 0, 0, 40)})
    spec["balcony"] = part("balcony", {"side": lambda x: side(x, "front")})
    spec["dancefloor"] = raw.get("dancefloor", kind not in ("theatre",)) is not False
    spec["seating"] = bool(raw.get("seating") or kind == "theatre")
    spec["screen"] = bool(raw.get("screen"))
    spec["pillars"] = int(_f(raw.get("pillars"), 0, 0, 24))
    default_trusses = 0 if kind in ("bar",) else (2 if spec["height"] < 4.2 else 3)
    spec["trusses"] = int(_f(raw.get("trusses"), default_trusses, 0, 12))
    spec["doors"] = [side(d, "front") for d in (raw.get("doors") or [])][:6]
    if spec["dj"] is None and spec["stage"] is None and kind in ("club", "small_club", "bar", "warehouse"):
        spec["dj"] = {"side": "back", "riser": 0.0}
    if spec["stage"] is None and kind in ("concert", "theatre", "outdoor"):
        spec["stage"] = {"width": 0, "depth": 0, "height": 1.2}
    return spec


def build(raw_spec: dict) -> dict:
    """A spec -> a clean venue (app/venue.py)."""
    spec = clean_spec(raw_spec)
    W, D, H = spec["width"], spec["depth"], spec["height"]
    pts = outline(spec["shape"], W, D, BACK, spec.get("cut_w"), spec.get("cut_d"),
                  spec["corner"] or "front-right")
    v = venue_mod.empty()
    v["auto"] = False
    what = spec["kind"].replace("_", " ")
    adj = {"rectangle": "", "l": "L-shaped ", "t": "T-shaped ", "u": "U-shaped ", "octagon": "Octagonal ",
           "round": "Round ", "wedge": "Fan-shaped "}[spec["shape"]]
    v["name"] = spec["name"] or (adj + what if adj else what.capitalize()) + f" {W:g} x {D:g} m"
    v["template"] = ""
    v["room"].update({"outline": pts, "height": H, "ceiling": "open" if spec["kind"] in ("warehouse", "outdoor") else "flat",
                      "floor": "wood" if spec["kind"] in ("bar", "small_club", "ballroom", "theatre") else "concrete"})
    v = venue_mod.normalise(v)
    if spec["layout"]:
        _layout(v, spec, pts)
        venue_mod.fit_inside(v)
    return venue_mod.normalise(copy.deepcopy(v))


def _floor(pts, x0, z0, x1, z1):
    """The floor inside the walls within a box - kept to a piece whose
    middle is on the floor (lights aimed at an area aim at its middle; a
    dance floor wrapped round a U's notch would be aimed into the notch)."""
    for k in range(12):
        zb = z1 - (z1 - z0) * k / 12
        area = clip(pts, x0, z0, x1, zb)
        if area:
            cx = sum(p[0] for p in area) / len(area)
            cz = sum(p[1] for p in area) / len(area)
            if inside(cx, cz, pts):
                return area
    return []


def _add(v, kind, key, raw):
    item = {"rigging": venue_mod._clean_rig, "objects": venue_mod._clean_object,
            "zones": venue_mod._clean_zone}[key]({"kind": kind, **raw}, v)
    if item:
        v[key].append(item)
    return item


def _wall(pts, side, W, D):
    """(x, z, rot) just inside the middle of a wall, facing into the room."""
    z0, z1 = BACK, BACK + D
    if side == "back":
        return 0.0, z0, 0.0
    if side == "front":
        return 0.0, z1, 180.0
    zm = (z0 + z1) / 2
    sp = span_at(pts, zm) or (-W / 2, W / 2)
    return (sp[0], zm, 90.0) if side == "left" else (sp[1], zm, -90.0)


def _layout(v, spec, pts):
    W, D, H = spec["width"], spec["depth"], spec["height"]
    z0, z1 = BACK, BACK + D
    used_back = z0 + 0.5            # how far the stage end reaches into the room

    # stage deck at the stage end
    st = spec["stage"]
    if st is not None:
        sp = span_at(pts, z0 + 0.3) or (-W / 2, W / 2)
        sw = st["width"] or min(12.0, (sp[1] - sp[0]) * 0.6)
        sd = st["depth"] or min(6.0, D * 0.22)
        v["stage"] = {"x": 0.0, "z": round(z0 + 0.3, 3), "width": round(min(sw, sp[1] - sp[0] - 0.4), 3),
                      "depth": round(sd, 3), "height": round(st["height"], 3), "colour": "#1f2026"}
        used_back = z0 + 0.3 + sd
        _add(v, "stage", "zones", {"name": "Stage", "points": venue_mod._rect(-sw / 2, z0 + 0.3, sw / 2, used_back)})

    # the DJ: a booth (on a riser when asked), speakers either side
    dj = spec["dj"]
    if dj is not None:
        side = dj["side"] if dj["side"] != "centre" else "back"
        riser = dj["riser"]
        on_stage = st is not None and side == "back"
        base_y = (v["stage"]["height"] if on_stage else riser)
        x, z, rot = _wall(pts, side, W, D)
        off = 1.4 if not on_stage else min(1.4, (v["stage"]["depth"]) * 0.45)
        if side == "back":
            z = (v["stage"]["z"] + off) if on_stage else z0 + off
        elif side == "front":
            z = z1 - off
        elif side == "left":
            x += off
        else:
            x -= off
        bw, bd = (3.0, 1.0) if side in ("back", "front") else (1.0, 3.0)
        if riser and not on_stage:
            _add(v, "riser", "objects", {"name": "DJ riser", "x": x, "z": z, "w": bw + 1.4, "d": bd + 1.6,
                                         "h": riser})
        _add(v, "dj_booth", "objects", {"name": "DJ booth", "x": x, "z": z, "w": bw, "d": bd, "h": 1.05, "y": base_y})
        _add(v, "mark", "objects", {"name": "DJ", "x": x + (0 if side in ("back", "front") else (-0.6 if side == "left" else 0.6)),
                                    "z": z + (-0.6 if side == "back" else 0.6 if side == "front" else 0),
                                    "w": 0.5, "d": 0.5, "h": 0.02, "y": base_y})
        for s in (-1, 1):
            if side in ("back", "front"):
                _add(v, "speaker", "objects", {"name": "Speaker " + ("L" if s < 0 else "R"), "x": x + s * (bw / 2 + 1.0),
                                               "z": z, "w": 0.9, "d": 0.8, "h": 1.9})
            else:
                _add(v, "speaker", "objects", {"name": "Speaker " + ("L" if s < 0 else "R"), "x": x,
                                               "z": z + s * (bd / 2 + 1.0), "w": 0.8, "d": 0.9, "h": 1.9})
        _add(v, "dj", "zones", {"name": "DJ", "points": venue_mod._rect(x - bw / 2 - 0.4, z - bd / 2 - 0.6,
                                                                      x + bw / 2 + 0.4, z + bd / 2 + 0.6)})
        if side == "back":
            used_back = max(used_back, z + bd / 2 + 0.6)

    # the bar
    bar = spec["bar"]
    used_front = z1
    if bar is not None:
        side = bar["side"] if bar["side"] != "centre" else "front"
        x, z, rot = _wall(pts, side, W, D)
        if side in ("front", "back"):
            sp = span_at(pts, (z1 - 1.0) if side == "front" else (z0 + 1.0)) or (-W / 2, W / 2)
            length = bar["length"] or min(10.0, (sp[1] - sp[0]) * 0.6)
            zc = z1 - 1.2 if side == "front" else z0 + 1.2
            _add(v, "bar", "objects", {"name": "Bar", "x": (sp[0] + sp[1]) / 2, "z": zc, "w": length, "d": 0.8, "h": 1.1})
            _add(v, "bar", "zones", {"name": "Bar", "points": venue_mod._rect((sp[0] + sp[1]) / 2 - length / 2,
                                                                           zc - 2.4, (sp[0] + sp[1]) / 2 + length / 2, zc - 0.5),
                                     "density": 0.35})
            if side == "front":
                used_front = zc - 2.6
        else:
            length = bar["length"] or min(10.0, D * 0.45)
            zc = z0 + D * 0.62
            sp = span_at(pts, zc) or (-W / 2, W / 2)
            xc = sp[0] + 1.0 if side == "left" else sp[1] - 1.0
            _add(v, "bar", "objects", {"name": "Bar", "x": xc, "z": zc, "w": 0.8, "d": length, "h": 1.1})
            zx0, zx1 = (xc + 0.5, xc + 2.4) if side == "left" else (xc - 2.4, xc - 0.5)
            _add(v, "bar", "zones", {"name": "Bar", "points": venue_mod._rect(zx0, zc - length / 2, zx1, zc + length / 2),
                                     "density": 0.35})

    # dance floor / seating: the open floor between the stage end and the bar
    fz0, fz1 = used_back + 0.8, used_front - 0.6
    if fz1 - fz0 > 1.5:
        if spec["seating"]:
            area = _floor(pts, -W / 2 + 0.8, fz0 + (fz1 - fz0) * (0.35 if spec["dancefloor"] else 0), W / 2 - 0.8, fz1)
            if area:
                _add(v, "seating", "zones", {"name": "Seating", "points": area, "density": 0.5})
                fz1 = fz0 + (fz1 - fz0) * 0.35
        if spec["dancefloor"] and fz1 - fz0 > 1.2:
            area = _floor(pts, -W / 2 + 1.0, fz0, W / 2 - 1.0, fz1)
            if area:
                _add(v, "dancefloor", "zones", {"name": "Dance floor", "points": area, "density": 0.7})

    # pillars: in rows across the room, only where there is floor
    n = spec["pillars"]
    if n:
        rows = 1 if n <= 3 else 2
        per = math.ceil(n / rows)
        placed = 0
        for r in range(rows):
            zr = z0 + D * ((r + 1) / (rows + 1))
            sp = span_at(pts, zr) or (-W / 2, W / 2)
            for i in range(per):
                if placed >= n:
                    break
                xr = sp[0] + (sp[1] - sp[0]) * (i + 1) / (per + 1)
                if inside(xr, zr, pts):
                    _add(v, "pillar", "objects", {"name": f"Pillar {placed + 1}", "x": xr, "z": zr, "w": 0.5, "d": 0.5, "h": H})
                    placed += 1

    # a balcony along a wall, a screen behind the DJ / stage, doors
    if spec["balcony"] is not None and H >= 5.0:
        side = spec["balcony"]["side"]
        x, z, _r = _wall(pts, side, W, D)
        bh = min(3.2, H - 2.4)
        if side in ("front", "back"):
            sp = span_at(pts, z1 - 1.6 if side == "front" else z0 + 1.6) or (-W / 2, W / 2)
            _add(v, "balcony", "objects", {"name": "Balcony", "x": (sp[0] + sp[1]) / 2, "z": z1 - 1.6 if side == "front" else z0 + 1.6,
                                           "w": (sp[1] - sp[0]) - 0.2, "d": 3.0, "h": 0.3, "y": bh})
        else:
            _add(v, "balcony", "objects", {"name": "Balcony", "x": x + (1.4 if side == "left" else -1.4), "z": z,
                                           "w": 2.6, "d": D * 0.6, "h": 0.3, "y": bh})
    if spec["screen"]:
        sp = span_at(pts, z0 + 0.1) or (-W / 2, W / 2)
        _add(v, "screen", "objects", {"name": "Screen", "x": 0.0, "z": z0 + 0.12, "w": min(8.0, (sp[1] - sp[0]) * 0.5),
                                      "d": 0.1, "h": min(3.5, H * 0.5), "y": min(1.6, H * 0.3)})
    for i, side in enumerate(spec["doors"]):
        x, z, rot = _wall(pts, side, W, D)
        _add(v, "door", "objects", {"name": f"Door {i + 1}", "x": x, "z": z, "w": 1.6, "d": 0.12, "h": 2.2, "rot": rot})

    # trusses: across the room over the floor, wall to wall at their depth
    nt = spec["trusses"]
    if nt:
        kind = "pipe" if H < 4.2 else "truss"
        y = H - (0.3 if kind == "pipe" else 0.7)
        a, b = max(z0 + 0.8, used_back - 0.6), min(z1 - 1.0, used_front + 0.5)
        for i in range(nt):
            zt = a + (b - a) * ((i + 0.5) / nt) if nt > 1 else (a + b) / 2
            sp = span_at(pts, zt)
            if not sp or sp[1] - sp[0] < 2.0:
                continue
            m = 0.5 + (sp[1] - sp[0]) * 0.06
            _add(v, kind, "rigging", {"name": f"{'Pipe' if kind == 'pipe' else 'Truss'} {i + 1}",
                                      "a": [sp[0] + m, y, zt], "b": [sp[1] - m, y, zt]})


# ---------------------------------------------------------------------------
# words -> spec (offline; the AI fills the same spec, see SPEC_SCHEMA)
# ---------------------------------------------------------------------------
_NUM_WORDS = {"one": 1, "a": 1, "an": 1, "single": 1, "two": 2, "pair": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
_FT = 0.3048


def _count(word: str) -> int | None:
    w = word.lower()
    if w.isdigit():
        return int(w)
    return _NUM_WORDS.get(w)


def _metres(num: str, unit: str | None) -> float:
    v = float(num)
    u = (unit or "m").lower()
    if u.startswith(("ft", "feet", "foot", "'")):
        return v * _FT
    if u.startswith(("cm", "centi")):
        return v / 100
    if u.startswith(("mm",)):
        return v / 1000
    return v


_SIDE_RE = r"\b(left|right|back|rear|front|entrance|middle|centre|center|behind|far end|stage end)\b"


def _clause(text: str, start: int, end: int) -> tuple[str, str]:
    """The words after and before a thing, within its own clause (so "bar
    on the left, DJ booth on a riser" doesn't put the DJ on the left)."""
    stop = re.compile(r"[,.;]| and (?:a|an|the)\b| with ")
    m = stop.search(text, end)
    after = text[end:m.start() if m else len(text)]
    b0 = max((x.end() for x in stop.finditer(text, 0, start)), default=0)
    return after, text[b0:start]


def _sides(text: str, start: int, end: int) -> list[str]:
    after, before = _clause(text, start, end)
    out = []
    for chunk in (after, before):
        for m in re.finditer(_SIDE_RE, chunk):
            w = m.group(1)
            w = {"rear": "back", "far end": "front", "entrance": "front", "behind": "back", "stage end": "back",
                 "middle": "centre", "center": "centre"}.get(w, w)
            if w not in out:
                out.append(w)
        if out:
            break
    return out


def _side_near(text: str, start: int, end: int) -> str | None:
    """left / right / back / front / centre said about a thing, in its clause."""
    after, before = _clause(text, start, end)
    for chunk in (after, before):
        m = re.search(r"\b(left|right|back|rear|front|entrance|middle|centre|center|behind|far end|stage end)\b", chunk)
        if m:
            w = m.group(1)
            return {"rear": "back", "far end": "front", "entrance": "front", "behind": "back", "stage end": "back",
                    "middle": "centre", "center": "centre"}.get(w, w)
    return None


def parse(text: str) -> dict:
    """{"spec": ..., "understood": [what it read], "unsure": [what it guessed]}"""
    t = " " + " ".join(str(text or "").lower().replace("×", " x ").replace(",", " , ").split()) + " "
    raw: dict = {}
    got: list[str] = []
    unsure: list[str] = []
    unit = r"(m|metres|meters|metre|meter|ft|feet|foot|')"

    # what kind of place
    for words, kind in ((r"warehouse|rave", "warehouse"), (r"theatre|theater|auditorium", "theatre"),
                        (r"ballroom|wedding|banquet|conference|event hall|hall", "ballroom"),
                        (r"concert|arena|gig venue|live stage", "concert"), (r"outdoor|festival|open air|field", "outdoor"),
                        (r"small club|lounge|pub|\bbar\b(?! on| at| along| in)", "small_club"), (r"club|disco|nightclub", "club")):
        if re.search(rf"\b({words})\b", t):
            raw["kind"] = kind
            got.append(f"a {kind.replace('_', ' ')}")
            break

    # size: 12 x 8 m, 12 by 8, 40ft x 30ft
    m = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}?\s*(?:x|by)\s*(\d+(?:\.\d+)?)\s*{unit}?", t)
    if m:
        u = m.group(4) or m.group(2)
        a, b = _metres(m.group(1), u), _metres(m.group(3), u)
        raw["width"], raw["depth"] = round(a, 1), round(b, 1)
        got.append(f"{a:g} m wide, {b:g} m deep")
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m2|sqm|square met(?:re|er)s?|m²)", t)
        if m:
            area = float(m.group(1))
            raw["width"] = round(math.sqrt(area / 1.3), 1)
            raw["depth"] = round(area / raw["width"], 1)
            got.append(f"{area:g} m² (about {raw['width']:g} x {raw['depth']:g} m)")
            unsure.append("the room's proportions (only the area was given)")
    # ceiling height
    m = (re.search(rf"(\d+(?:\.\d+)?)\s*{unit}?\s*(?:high|tall)?\s*ceilings?", t)
         or re.search(rf"ceilings?\s*(?:height\s*)?(?:is\s*|of\s*|at\s*)?(\d+(?:\.\d+)?)\s*{unit}?", t)
         or re.search(rf"(\d+(?:\.\d+)?)\s*{unit}\s*(?:high|tall)\b", t))
    if m:
        raw["height"] = _metres(m.group(1), m.group(2))
        got.append(f"{raw['height']:g} m ceiling")

    # shape
    for words, shape in ((r"l[- ]?shaped?|\bl shape\b", "l"), (r"t[- ]?shaped?", "t"), (r"u[- ]?shaped?", "u"),
                         (r"octagon(?:al)?|cut corners", "octagon"), (r"round|circular|oval|circle", "round"),
                         (r"wedge|fan[- ]shaped|fan shape", "wedge")):
        if re.search(rf"\b(?:{words})\b", t):
            raw["shape"] = shape
            got.append(SHAPES[shape].split(" (")[0].lower())
            break
    if raw.get("shape") == "l":
        for c in CORNERS:
            if c.replace("-", " ") in t or c in t:
                raw["corner"] = c
                break

    # the stage
    m = re.search(r"\bstage\b", t)
    if m and not re.search(r"\bno stage\b", t):
        st: dict = {}
        ms = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}?\s*(?:x|by)\s*(\d+(?:\.\d+)?)\s*{unit}?\s*stage", t) \
            or re.search(rf"stage[^,\d]{{0,24}}(\d+(?:\.\d+)?)\s*{unit}?\s*(?:x|by)\s*(\d+(?:\.\d+)?)\s*{unit}?", t)
        if ms:
            u = ms.group(4) or ms.group(2)
            st["width"], st["depth"] = _metres(ms.group(1), u), _metres(ms.group(3), u)
        mh = re.search(r"stage[^,.]*?(\d+(?:\.\d+)?)\s*(cm|m|ft|feet)\s*(?:high|tall|up)", t) \
            or re.search(r"(\d+(?:\.\d+)?)\s*(cm|m|ft|feet)\s*(?:high|tall)?\s*stage", t)
        if mh:
            st["height"] = _metres(mh.group(1), mh.group(2))
        raw["stage"] = st
        got.append("a stage" + (f" {st['width']:g} x {st['depth']:g} m" if "width" in st else "")
                   + (f", {st['height']:g} m high" if "height" in st else ""))

    # the DJ
    m = re.search(r"\b(dj booth|dj box|dj table|decks|dj)\b", t)
    if m and not re.search(r"\bno dj\b", t):
        dj: dict = {"side": _side_near(t, m.start(), m.end()) or "back"}
        mr = re.search(r"(\d+(?:\.\d+)?)\s*(cm|m|ft|feet|mm)\s*(?:high\s*)?(?:riser|platform|podium)", t) \
            or re.search(r"(?:riser|platform|podium)[^,.]*?(\d+(?:\.\d+)?)\s*(cm|m|ft|feet|mm)", t)
        if mr:
            dj["riser"] = _metres(mr.group(1), mr.group(2))
        elif re.search(r"\b(riser|platform|podium|raised)\b", t):
            dj["riser"] = 0.4
            unsure.append("the riser height (0.4 m)")
        raw["dj"] = dj
        got.append(f"DJ booth at the {dj['side']}" + (f" on a {dj['riser'] * 100:g} cm riser" if dj.get("riser") else ""))

    # the bar (the counter, not "a small bar" as the kind of place)
    for m in re.finditer(r"\bbars?\b", t):
        ctx = t[max(0, m.start() - 12):m.start()]
        if re.search(r"(small|cocktail|a|little)\s*$", ctx) and raw.get("kind") == "small_club" and "bar" not in raw:
            # "a small bar": the place itself is a bar - it still has one
            raw["bar"] = {"side": _side_near(t, m.start(), m.end()) or "front"}
            continue
        if re.search(r"\bno bar\b", t):
            break
        raw["bar"] = {"side": _side_near(t, m.start(), m.end()) or "front"}
        ml = re.search(rf"(\d+(?:\.\d+)?)\s*{unit}?\s*(?:long\s*)?bar", t)
        if ml:
            raw["bar"]["length"] = _metres(ml.group(1), ml.group(2))
        break
    if "bar" in raw:
        got.append(f"bar at the {raw['bar']['side']}")

    # the rest
    m = re.search(r"\b(\w+)\s*(?:pillars|columns|posts)\b", t) or re.search(r"\b(\w+)\s*(?:pillar|column)\b", t)
    if m:
        n = _count(m.group(1))
        if n:
            raw["pillars"] = n
            got.append(f"{n} pillar(s)")
    elif re.search(r"\b(pillars|columns)\b", t):
        raw["pillars"] = 2
        unsure.append("how many pillars (2)")
    m = re.search(r"\bbalcon(?:y|ies)\b", t)
    if m:
        raw["balcony"] = {"side": _side_near(t, m.start(), m.end()) or "front"}
        got.append(f"balcony at the {raw['balcony']['side']}")
    if re.search(r"\b(screen|led wall|video wall|projection)\b", t):
        raw["screen"] = True
        got.append("a screen behind the stage end")
    if re.search(r"\b(seating|seats|tables|chairs|seated)\b", t):
        raw["seating"] = True
        got.append("seating")
    if re.search(r"\bno dance ?floor\b", t):
        raw["dancefloor"] = False
    elif re.search(r"\bdance ?floor\b", t):
        raw["dancefloor"] = True
        got.append("a dance floor")
    m = re.search(r"\b(\w+)\s*(?:truss(?:es)?|pipes|bars of truss)\b", t)
    if re.search(r"\bno (?:truss|rigging|pipes)\b", t):
        raw["trusses"] = 0
        got.append("no rigging")
    elif m and _count(m.group(1)) is not None:
        raw["trusses"] = _count(m.group(1))
        got.append(f"{raw['trusses']} truss(es)")
    doors = []
    for m in re.finditer(r"\b(door|entrance|exit|doors)\b", t):
        doors += [s for s in _sides(t, m.start(), m.end()) if s != "centre"]
    if doors:
        raw["doors"] = sorted(set(doors), key=doors.index)
        got.append("door(s) at the " + ", ".join(raw["doors"]))
    m = re.search(r"\b(?:called|named)\s+([a-z0-9' ]{2,30}?)(?:\s*[,.]|$)", t)
    if m:
        raw["name"] = m.group(1).strip().title()
    if "width" not in raw:
        unsure.append("the size (a typical one for the kind of room)")
    if "height" not in raw:
        unsure.append("the ceiling height")
    return {"spec": clean_spec(raw), "understood": got, "unsure": unsure}


SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": list(KIND_SIZE)},
        "shape": {"type": "string", "enum": list(SHAPES)},
        "width": {"type": "number", "description": "metres, across the room (left-right as seen from the stage end looking at the audience... i.e. the stage wall's length)"},
        "depth": {"type": "number", "description": "metres, from the stage end to the far wall"},
        "height": {"type": "number", "description": "ceiling height, metres"},
        "corner": {"type": "string", "description": "L: the missing corner (front-left, front-right, back-left, back-right); T / U / wedge: 'front' or 'back'"},
        "stage": {"type": "object", "properties": {"width": {"type": "number"}, "depth": {"type": "number"}, "height": {"type": "number"}}},
        "dj": {"type": "object", "properties": {"side": {"type": "string", "enum": list(SIDES)}, "riser": {"type": "number", "description": "metres"}}},
        "bar": {"type": "object", "properties": {"side": {"type": "string", "enum": list(SIDES)}, "length": {"type": "number"}}},
        "balcony": {"type": "object", "properties": {"side": {"type": "string", "enum": list(SIDES)}}},
        "dancefloor": {"type": "boolean"},
        "seating": {"type": "boolean"},
        "screen": {"type": "boolean"},
        "pillars": {"type": "integer"},
        "trusses": {"type": "integer"},
        "doors": {"type": "array", "items": {"type": "string", "enum": list(SIDES)}},
    },
    "required": ["kind", "shape", "width", "depth", "height"],
}


PROMPT = ("You turn a description of a venue into a room spec for a lighting console. Coordinates: the "
          "stage end (where the DJ or the stage is, unless the words say otherwise) is the BACK wall; "
          "width is along the back wall, depth from the back wall to the far (front) wall; metres. "
          "Convert feet. Only include what the words say or clearly imply; leave the rest out and the "
          "console picks sensible defaults. 'kind' is the sort of place (club, small_club, bar, warehouse, "
          "concert, theatre, ballroom, event, outdoor).")


def explain(spec: dict) -> list[str]:
    """A spec in words: what will be built (for the AI path's answer)."""
    s = clean_spec(spec)
    out = [f"a {s['kind'].replace('_', ' ')}", f"{s['width']:g} m wide, {s['depth']:g} m deep",
           f"{s['height']:g} m ceiling"]
    if s["shape"] != "rectangle":
        out.append(SHAPES[s["shape"]].split(" (")[0].lower())
    if s["stage"] is not None:
        out.append("a stage")
    if s["dj"] is not None:
        out.append(f"DJ booth at the {s['dj']['side']}" + (f" on a {s['dj']['riser'] * 100:g} cm riser" if s["dj"]["riser"] else ""))
    if s["bar"] is not None:
        out.append(f"bar at the {s['bar']['side']}")
    if s["pillars"]:
        out.append(f"{s['pillars']} pillar(s)")
    if s["balcony"] is not None:
        out.append(f"balcony at the {s['balcony']['side']}")
    if s["screen"]:
        out.append("a screen")
    if s["seating"]:
        out.append("seating")
    out.append(f"{s['trusses']} truss(es)" if s["trusses"] else "no rigging")
    if s["doors"]:
        out.append("door(s) at the " + ", ".join(s["doors"]))
    return out
