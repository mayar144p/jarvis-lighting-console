"""The room a rig lives in: walls, floor, ceiling, stage, zones, rigging.

All coordinates are metres.  x runs across the room (0 is the centre line),
y is up (0 is the floor), and z runs from the back wall behind the stage
towards the audience; the stage's front edge sits at a positive z.

Rigging is anything a light can be put on: truss, pipes, towers, ladders,
stands and floor bases.  Each is a segment from `a` to `b`, so one model
covers a 12 m truss span, a vertical tower and a tripod stand.  A head that
is *mounted* stores the rig id and how far along it sits (`t`, 0..1), so
moving a truss carries its lights with it.

Pure data in, pure data out: no engine state here, so it is tested directly.
"""
from __future__ import annotations

import copy
import math

VERSION = 2

RIG_KINDS = ("truss", "pipe", "tower", "ladder", "stand", "base")
OBJECT_KINDS = ("dj_booth", "bar", "speaker", "sub", "pillar", "screen",
                "riser", "balcony", "wall", "table", "door", "mark")
ZONE_KINDS = ("dancefloor", "standing", "seating", "bar", "dj", "foh",
              "vip", "backstage", "stage")
CEILINGS = ("flat", "open", "none")
CROWD_STYLES = ("simple", "varied")

# Default cross-section of each rig kind (m), and whether lights on it hang.
RIG_SIZE = {"truss": 0.3, "pipe": 0.05, "tower": 0.3, "ladder": 0.3,
            "stand": 0.05, "base": 0.4}
_LIMIT = 200.0                               # no coordinate beyond this


def _num(value, fallback=0.0, lo=-_LIMIT, hi=_LIMIT) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if math.isnan(v) or math.isinf(v):
        return float(fallback)
    return max(lo, min(hi, v))


def _point(raw, fallback=(0.0, 0.0, 0.0)) -> list[float]:
    if isinstance(raw, dict):
        raw = [raw.get("x"), raw.get("y"), raw.get("z")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raw = fallback
    return [round(_num(raw[0], fallback[0]), 3),
            round(_num(raw[1], fallback[1], 0.0), 3),
            round(_num(raw[2], fallback[2]), 3)]


def _text(value, default="", limit=60) -> str:
    return (str(value).strip() if value is not None else "")[:limit] or default


def _colour(value, default) -> str:
    v = str(value or "").strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.lower()
        except ValueError:
            pass
    return default


def _polygon(raw) -> list[list[float]]:
    pts = []
    for p in raw or []:
        if isinstance(p, dict):
            p = [p.get("x"), p.get("z")]
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            pts.append([round(_num(p[0]), 3), round(_num(p[1]), 3)])
    return pts if len(pts) >= 3 else []


# ---------------------------------------------------------------------------
# normalising
# ---------------------------------------------------------------------------
def empty() -> dict:
    return {"version": VERSION, "name": "", "auto": True,
            "room": {"width": 0.0, "depth": 0.0, "height": 0.0, "back": -1.0,
                     "cx": 0.0,
                     "outline": [], "ceiling": "flat", "floor": "concrete",
                     "wall_colour": "#3a3d45"},
            "stage": None, "zones": [], "rigging": [], "objects": [],
            "cameras": [], "crowd": {"style": "varied", "density": 0.45,
                                     "show": True},
            "underlay": None, "seq": 0}


def _next_id(v: dict, prefix: str) -> str:
    v["seq"] = int(v.get("seq") or 0) + 1
    return f"{prefix}{v['seq']}"


def _clean_room(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    ceiling = str(raw.get("ceiling") or "flat")
    room = {
        "width": round(_num(raw.get("width"), 0, 0, _LIMIT), 3),
        "depth": round(_num(raw.get("depth"), 0, 0, _LIMIT), 3),
        "height": round(_num(raw.get("height"), 0, 0, 60), 3),
        "back": round(_num(raw.get("back"), -1.0), 3),
        "cx": round(_num(raw.get("cx"), 0.0), 3),
        "outline": _polygon(raw.get("outline")),
        "ceiling": ceiling if ceiling in CEILINGS else "flat",
        "floor": _text(raw.get("floor"), "concrete", 20),
        "wall_colour": _colour(raw.get("wall_colour"), "#3a3d45"),
    }
    if room["outline"]:
        # A traced room: its box is the outline's extent.
        xs = [p[0] for p in room["outline"]]
        zs = [p[1] for p in room["outline"]]
        room.update({"width": round(max(xs) - min(xs), 3),
                     "depth": round(max(zs) - min(zs), 3),
                     "back": round(min(zs), 3),
                     "cx": round((max(xs) + min(xs)) / 2, 3)})
    return room


def _clean_stage(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    w = _num(raw.get("width"), 0, 0, _LIMIT)
    d = _num(raw.get("depth"), 0, 0, _LIMIT)
    if w <= 0 or d <= 0:
        return None
    return {"x": round(_num(raw.get("x")), 3), "z": round(_num(raw.get("z")), 3),
            "width": round(w, 3), "depth": round(d, 3),
            "height": round(_num(raw.get("height"), 0.6, 0, 10), 3),
            "colour": _colour(raw.get("colour"), "#26272c")}


def _clean_item(raw: dict, v: dict, kinds: tuple, prefix: str,
                default_kind: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or default_kind).strip().lower()
    if kind not in kinds:
        return None
    item = {"id": _text(raw.get("id"), "", 24) or _next_id(v, prefix),
            "kind": kind, "name": _text(raw.get("name"), "", 40)}
    return item


def _clean_rig(raw: dict, v: dict) -> dict | None:
    item = _clean_item(raw, v, RIG_KINDS, "r", "truss")
    if not item:
        return None
    kind = item["kind"]
    a = _point(raw.get("a"))
    b = _point(raw.get("b"), tuple(a))
    if kind in ("tower", "ladder", "stand") and a == b:
        b = [a[0], a[1] + 3.0, a[2]]
    item.update({"a": a, "b": b,
                 "size": round(_num(raw.get("size"), RIG_SIZE[kind], 0.02, 2), 3)})
    return item


def _clean_object(raw: dict, v: dict) -> dict | None:
    item = _clean_item(raw, v, OBJECT_KINDS, "o", "riser")
    if not item:
        return None
    item.update({
        "x": round(_num(raw.get("x")), 3), "z": round(_num(raw.get("z")), 3),
        "y": round(_num(raw.get("y"), 0, 0, 60), 3),
        "w": round(_num(raw.get("w"), 1, 0.05, _LIMIT), 3),
        "d": round(_num(raw.get("d"), 1, 0.05, _LIMIT), 3),
        "h": round(_num(raw.get("h"), 1, 0.01, 60), 3),
        "rot": round(_num(raw.get("rot"), 0, -360, 360), 2),
        "colour": _colour(raw.get("colour"), ""),
    })
    if not item["colour"]:
        del item["colour"]
    return item


def _clean_zone(raw: dict, v: dict) -> dict | None:
    item = _clean_item(raw, v, ZONE_KINDS, "z", "standing")
    if not item:
        return None
    pts = _polygon(raw.get("points"))
    if not pts:
        return None
    item.update({"points": pts,
                 "y": round(_num(raw.get("y"), 0, 0, 60), 3),
                 "density": round(_num(raw.get("density"), -1, -1, 1), 3)})
    return item


def _clean_camera(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), "", 30)
    if not name:
        return None
    return {"name": name, "pos": _point(raw.get("pos"), (0, 4, 18)),
            "target": _point(raw.get("target"), (0, 2, 4))}


def _from_v1(raw: dict) -> dict:
    """The old {width_m, depth_m, height_m, surfaces} room."""
    v = empty()
    w = _num(raw.get("width_m"), 0, 0)
    d = _num(raw.get("depth_m"), 0, 0)
    h = _num(raw.get("height_m"), 0, 0)
    v["name"] = _text(raw.get("name"), "")
    if w > 0 or d > 0 or h > 0 or raw.get("surfaces"):
        v["auto"] = False
        w, d = w or 10.0, d or 8.0
        v["room"].update({"width": round(max(w + 4, 10), 3),
                          "depth": round(d + 12, 3),
                          "height": round(h or 7.0, 3), "back": -1.0})
        v["stage"] = {"x": 0.0, "z": 0.0, "width": w, "depth": d,
                      "height": 0.0, "colour": "#26272c"}
    for s in raw.get("surfaces") or []:
        if not isinstance(s, dict):
            continue
        try:
            a = [float(s["x1"]), float(s["y1"]), float(s["z1"])]
            b = [float(s["x2"]), float(s["y2"]), float(s["z2"])]
        except (KeyError, TypeError, ValueError):
            continue
        if str(s.get("kind") or "").lower() == "truss":
            rig = _clean_rig({"kind": "truss", "a": a, "b": b}, v)
            if rig:
                v["rigging"].append(rig)
        else:
            length = math.hypot(b[0] - a[0], b[2] - a[2])
            obj = _clean_object({
                "kind": "wall", "x": (a[0] + b[0]) / 2, "z": (a[2] + b[2]) / 2,
                "w": max(0.1, length), "d": 0.1,
                "h": max(0.5, (v["room"]["height"] or 4) * 0.6),
                "rot": -math.degrees(math.atan2(b[2] - a[2], b[0] - a[0])),
                "colour": s.get("color")}, v)
            if obj:
                v["objects"].append(obj)
    return v


def normalise(raw) -> dict:
    """Any stored venue (v1, v2, partial, junk) -> a clean v2 venue."""
    if not isinstance(raw, dict) or not raw:
        return empty()
    if int(raw.get("version") or 1) < 2:
        return _from_v1(raw)
    v = empty()
    v["seq"] = int(_num(raw.get("seq"), 0, 0, 1e6))
    v["name"] = _text(raw.get("name"), "")
    v["template"] = _text(raw.get("template"), "", 30)
    v["room"] = _clean_room(raw.get("room"))
    v["auto"] = bool(raw.get("auto")) or not (v["room"]["width"] and v["room"]["depth"])
    v["stage"] = _clean_stage(raw.get("stage"))
    seen: set[str] = set()

    def unique(item):
        if not item or item["id"] in seen:
            if item:
                item["id"] = _next_id(v, item["id"][0])
            else:
                return None
        seen.add(item["id"])
        return item

    v["rigging"] = [r for r in (unique(_clean_rig(x, v)) for x in raw.get("rigging") or []) if r]
    v["objects"] = [o for o in (unique(_clean_object(x, v)) for x in raw.get("objects") or []) if o]
    v["zones"] = [z for z in (unique(_clean_zone(x, v)) for x in raw.get("zones") or []) if z]
    v["cameras"] = [c for c in (_clean_camera(x) for x in raw.get("cameras") or []) if c][:12]
    crowd = raw.get("crowd") if isinstance(raw.get("crowd"), dict) else {}
    style = str(crowd.get("style") or "varied")
    v["crowd"] = {"style": style if style in CROWD_STYLES else "varied",
                  "density": round(_num(crowd.get("density"), 0.45, 0, 1), 3),
                  "show": crowd.get("show") is not False}
    v["underlay"] = clean_underlay(raw.get("underlay"))
    return v


def clean_underlay(raw) -> dict | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    ident = "".join(ch for ch in str(raw["id"]) if ch.isalnum())[:64]
    if not ident:
        return None
    return {"id": ident,
            "x": round(_num(raw.get("x")), 3), "z": round(_num(raw.get("z")), 3),
            "width": round(_num(raw.get("width"), 10, 0.1, _LIMIT), 3),
            "aspect": round(_num(raw.get("aspect"), 1, 0.01, 100), 5),
            "rot": round(_num(raw.get("rot"), 0, -360, 360), 2),
            "opacity": round(_num(raw.get("opacity"), 0.6, 0, 1), 3),
            "show": raw.get("show") is not False}


# ---------------------------------------------------------------------------
# questions about a venue
# ---------------------------------------------------------------------------
def dims(v: dict) -> tuple[float, float, float]:
    """(width, depth, height) of the room; zeros when there is none."""
    r = (v or {}).get("room") or {}
    return (float(r.get("width") or 0), float(r.get("depth") or 0),
            float(r.get("height") or 0))


def bounds(v: dict) -> dict:
    """The room's box: x0..x1, z0..z1, height.  Generous when undrawn."""
    w, d, h = dims(v)
    if not w or not d:
        return {"x0": -36.0, "x1": 36.0, "z0": -4.0, "z1": 56.0, "h": 16.0}
    room = (v or {}).get("room") or {}
    back = float(room.get("back") or 0)
    cx = float(room.get("cx") or 0)
    return {"x0": cx - w / 2, "x1": cx + w / 2, "z0": back, "z1": back + d,
            "h": h or 20.0}


def stage_height_at(v: dict, x: float, z: float) -> float:
    """Floor height at (x, z): the stage deck, a riser, or the floor."""
    best = 0.0
    s = (v or {}).get("stage")
    if s and abs(x - s["x"]) <= s["width"] / 2 and s["z"] <= z <= s["z"] + s["depth"]:
        best = max(best, s["height"])
    for o in (v or {}).get("objects") or []:
        if o["kind"] not in ("riser", "balcony"):
            continue
        if abs(x - o["x"]) <= o["w"] / 2 and abs(z - o["z"]) <= o["d"] / 2:
            best = max(best, o["y"] + o["h"])
    return best


def rig(v: dict, ident: str) -> dict | None:
    for r in (v or {}).get("rigging") or []:
        if r["id"] == ident:
            return r
    return None


def is_vertical(r: dict) -> bool:
    a, b = r["a"], r["b"]
    return abs(b[1] - a[1]) > max(abs(b[0] - a[0]), abs(b[2] - a[2]))


def length(r: dict) -> float:
    a, b = r["a"], r["b"]
    return math.dist(a, b)


def default_orient(r: dict) -> str:
    """Hung under a horizontal bar; upright on a stand, base or tower."""
    if r["kind"] in ("base", "stand") or is_vertical(r):
        return "stand"
    return "hang"


def mount_position(r: dict, t: float, orient: str | None = None) -> dict:
    """Where a head on rig `r` at `t` sits, and which way up."""
    t = max(0.0, min(1.0, float(t)))
    a, b = r["a"], r["b"]
    p = [a[i] + (b[i] - a[i]) * t for i in range(3)]
    if not orient and is_vertical(r):
        # clamped to a tower or pole: hung (yoke up) once it is up high -
        # standing upright at 4 m it couldn't tilt down to the floor
        orient = "hang" if p[1] >= 2.0 else "stand"
    orient = orient or default_orient(r)
    half = r["size"] / 2
    if is_vertical(r):
        # clamped to the face of a tower, towards the audience
        p[2] += half + 0.12
        if r["kind"] == "stand" and t >= 0.999:
            p[2] -= half + 0.12
    elif r["kind"] == "base":
        p[1] = a[1]
    elif orient == "hang":
        p[1] -= half + 0.05
    else:
        p[1] += half
    return {"x": round(p[0], 3), "y": round(max(0.0, p[1]), 3),
            "z": round(p[2], 3), "orient": orient}


def nearest_rig(v: dict, x: float, y: float, z: float,
                reach: float = 0.8) -> tuple[dict, float, float] | None:
    """The rig a point is closest to, within `reach` m: (rig, t, distance)."""
    best = None
    p = (x, y, z)
    for r in (v or {}).get("rigging") or []:
        a, b = r["a"], r["b"]
        ab = [b[i] - a[i] for i in range(3)]
        denom = sum(c * c for c in ab)
        t = 0.0 if denom < 1e-9 else max(0.0, min(1.0, sum(
            (p[i] - a[i]) * ab[i] for i in range(3)) / denom))
        q = [a[i] + ab[i] * t for i in range(3)]
        dist = math.dist(p, q)
        if dist <= reach + r["size"] / 2 and (best is None or dist < best[2]):
            best = (r, t, dist)
    return best


def free_slots(r: dict, taken: list[float], count: int,
               spacing: float) -> list[float]:
    """`count` positions along a rig (t values), outward from its middle,
    at least `spacing` metres from every taken one."""
    span = length(r)
    if span < 1e-6:
        return [0.5] * count if not taken else []
    if is_vertical(r):
        # towers and stands: from the top down
        order = [1.0 - i * spacing / span for i in range(int(span / spacing) + 1)]
    else:
        n = int(span / spacing)
        order = []
        for k in range(n * 2 + 1):
            step = (k + 1) // 2
            off = step * spacing * (1 if k % 2 else -1) if k else 0.0
            t = 0.5 + off / span
            if 0.03 <= t <= 0.97:
                order.append(t)
    out: list[float] = []
    gap = spacing * 0.8 / span
    for t in order:
        if len(out) >= count:
            break
        if any(abs(t - u) < gap for u in taken + out):
            continue
        out.append(round(t, 4))
    return out


def zone_centroid(z: dict) -> tuple[float, float]:
    pts = z["points"]
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _nearest_inside(x: float, z: float, pts: list, inset: float) -> tuple[float, float]:
    """The point on a room outline nearest (x, z), pulled `inset` inwards."""
    best, bx, bz = None, x, z
    n = len(pts)
    for i in range(n):
        (x0, z0), (x1, z1) = pts[i], pts[(i + 1) % n]
        dx, dz = x1 - x0, z1 - z0
        t = max(0.0, min(1.0, ((x - x0) * dx + (z - z0) * dz) / ((dx * dx + dz * dz) or 1e-9)))
        px, pz = x0 + dx * t, z0 + dz * t
        d = (px - x) ** 2 + (pz - z) ** 2
        if best is None or d < best:
            best, bx, bz = d, px, pz
    cx = sum(p[0] for p in pts) / n
    cz = sum(p[1] for p in pts) / n
    ln = ((cx - bx) ** 2 + (cz - bz) ** 2) ** 0.5 or 1.0
    return bx + (cx - bx) / ln * inset, bz + (cz - bz) / ln * inset


def fit_inside(v: dict, margin: float = 0.2) -> int:
    """After the room is reshaped, bring rigging and objects that ended up
    outside it (or above the ceiling) back in; a truss keeps its length
    and slides in whole when it fits.  Returns how many were moved."""
    w, d, _h = dims(v)
    if not w or not d:
        return 0
    b = bounds(v)
    top = b["h"] - 0.1
    outline = [(float(p[0]), float(p[1])) for p in ((v.get("room") or {}).get("outline") or [])
               if isinstance(p, (list, tuple)) and len(p) >= 2]
    lo_x, hi_x, lo_z, hi_z = b["x0"] + margin, b["x1"] - margin, b["z0"] + margin, b["z1"] - margin

    def inside(x, z):
        if not (lo_x <= x <= hi_x and lo_z <= z <= hi_z):
            return False
        return point_in_polygon(x, z, outline) if len(outline) >= 3 else True

    def pull(x, z):
        x, z = min(hi_x, max(lo_x, x)), min(hi_z, max(lo_z, z))
        if len(outline) >= 3 and not point_in_polygon(x, z, outline):
            x, z = _nearest_inside(x, z, outline, margin)
        return round(x, 3), round(z, 3)

    moved = 0
    for r in v.get("rigging") or []:
        moved += _fit_rig(r, inside, pull, (lo_x, hi_x, lo_z, hi_z), top)
    for o in v.get("objects") or []:
        if inside(o["x"], o["z"]):
            continue
        o["x"], o["z"] = pull(o["x"], o["z"])
        moved += 1
    for zn in v.get("zones") or []:
        # a dance floor / stage zone left outside the new walls comes in
        # too (the crowd stands on it), squeezed to fit when it's bigger
        pts = [list(q) for q in zn.get("points") or []]
        if not pts or all(inside(q[0], q[1]) for q in pts):
            continue
        zn["points"] = [list(pull(q[0], q[1])) for q in pts]
        moved += 1
    return moved


def scale_zones(old: dict, new: dict) -> int:
    """When the room is resized, its zones (dance floor, stage, bar...)
    scale with it, keeping their place in the room - clamping squashed a
    dance floor outside a smaller room into a line."""
    ow, od, _ = dims(old)
    nw, nd, _ = dims(new)
    if not (ow and od and nw and nd) or (ow, od) == (nw, nd):
        return 0
    ob, nb = bounds(old), bounds(new)
    sx = (nb["x1"] - nb["x0"]) / ((ob["x1"] - ob["x0"]) or 1)
    sz = (nb["z1"] - nb["z0"]) / ((ob["z1"] - ob["z0"]) or 1)
    n = 0
    for zn in new.get("zones") or []:
        pts = zn.get("points") or []
        if not pts:
            continue
        zn["points"] = [[round(nb["x0"] + (q[0] - ob["x0"]) * sx, 3), round(nb["z0"] + (q[1] - ob["z0"]) * sz, 3)]
                        for q in pts]
        n += 1
    return n


def _room_fns(v: dict, margin: float = 0.2):
    """(inside, pull, box, top) for the room, or None when it has no size."""
    w, d, _h = dims(v)
    if not w or not d:
        return None
    b = bounds(v)
    top = b["h"] - 0.1
    outline = [(float(p[0]), float(p[1])) for p in ((v.get("room") or {}).get("outline") or [])
               if isinstance(p, (list, tuple)) and len(p) >= 2]
    lo_x, hi_x, lo_z, hi_z = b["x0"] + margin, b["x1"] - margin, b["z0"] + margin, b["z1"] - margin

    def inside(x, z):
        if not (lo_x <= x <= hi_x and lo_z <= z <= hi_z):
            return False
        return point_in_polygon(x, z, outline) if len(outline) >= 3 else True

    def pull(x, z):
        x, z = min(hi_x, max(lo_x, x)), min(hi_z, max(lo_z, z))
        if len(outline) >= 3 and not point_in_polygon(x, z, outline):
            x, z = _nearest_inside(x, z, outline, margin)
        return round(x, 3), round(z, 3)
    return inside, pull, (lo_x, hi_x, lo_z, hi_z), top


def keep_rig_inside(v: dict, r: dict) -> bool:
    """Bring one rig inside the room (and under its ceiling); True if moved."""
    fns = _room_fns(v)
    if not fns:
        return False
    inside, pull, box, top = fns
    return bool(_fit_rig(r, inside, pull, box, top))


def _fit_rig(r: dict, inside, pull, box, top) -> int:
    lo_x, hi_x, lo_z, hi_z = box
    if True:
        a, bb = list(r["a"]), list(r["b"])
        if inside(a[0], a[2]) and inside(bb[0], bb[2]) and max(a[1], bb[1]) <= top:
            return 0
        # slide the whole piece in first, so it keeps its length
        sx = (lo_x - min(a[0], bb[0]) if min(a[0], bb[0]) < lo_x else 0) or \
             (hi_x - max(a[0], bb[0]) if max(a[0], bb[0]) > hi_x else 0)
        sz = (lo_z - min(a[2], bb[2]) if min(a[2], bb[2]) < lo_z else 0) or \
             (hi_z - max(a[2], bb[2]) if max(a[2], bb[2]) > hi_z else 0)
        for p in (a, bb):
            p[0], p[2] = p[0] + sx, p[2] + sz
            p[0], p[2] = pull(p[0], p[2])              # still out (too long, or an L-room corner)
            p[1] = round(min(p[1], top), 3)
        r["a"], r["b"] = a, bb
        return 1


def rig_transform(v: dict, r: dict, turn: float | None = None, length: float | None = None,
                  orient: str | None = None, ceiling: bool = False) -> dict:
    """A rig turned about its middle (degrees, seen from above), resized,
    stood up as a pole / laid flat, or hung just under the ceiling.
    Returns the new {a, b}."""
    a, b = list(r["a"]), list(r["b"])
    mid = [(a[i] + b[i]) / 2 for i in range(3)]
    half = [(b[i] - a[i]) / 2 for i in range(3)]
    ln = math.sqrt(sum(c * c for c in half)) * 2 or 1.0
    if orient == "vertical":
        half = [0.0, ln / 2, 0.0]               # a pole: stands on the floor
        mid[1] = ln / 2
    elif orient == "horizontal":
        flat = math.hypot(half[0], half[2])
        half = [half[0] / flat * ln / 2, 0.0, half[2] / flat * ln / 2] if flat > 1e-6 else [ln / 2, 0.0, 0.0]
    if turn:
        c, sn = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        half = [half[0] * c - half[2] * sn, half[1], half[0] * sn + half[2] * c]
    if length:
        k = float(length) / ln
        half = [x * k for x in half]
        if orient == "vertical" or abs(half[1]) > max(abs(half[0]), abs(half[2])):
            mid[1] = max(mid[1], abs(half[1]))   # a pole keeps its foot on the floor
    if ceiling:
        h = dims(v)[2] or 6.0
        mid[1] = h - float(r.get("size") or 0.3) / 2 - 0.05 - abs(half[1])
    a = [round(mid[i] - half[i], 3) for i in range(3)]
    b = [round(mid[i] + half[i], 3) for i in range(3)]
    a[1], b[1] = max(0.0, a[1]), max(0.0, b[1])
    return {"a": a, "b": b}


def point_in_polygon(x: float, z: float, pts: list) -> bool:
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, zi = pts[i]
        xj, zj = pts[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / ((zj - zi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def describe(v: dict) -> dict:
    """A compact summary for the AI and the doctor."""
    w, d, h = dims(v)
    rigs = []
    for r in v.get("rigging") or []:
        a, b = r["a"], r["b"]
        rigs.append({"id": r["id"], "kind": r["kind"], "name": r["name"],
                     "height": round((a[1] + b[1]) / 2, 2),
                     "z": round((a[2] + b[2]) / 2, 2),
                     "x": [round(min(a[0], b[0]), 2), round(max(a[0], b[0]), 2)],
                     "length": round(length(r), 2), "vertical": is_vertical(r)})
    zones = []
    for z in v.get("zones") or []:
        cx, cz = zone_centroid(z)
        xs = [p[0] for p in z["points"]]
        zs = [p[1] for p in z["points"]]
        zones.append({"id": z["id"], "kind": z["kind"], "name": z["name"],
                      "centre": [round(cx, 2), round(cz, 2)],
                      "size": [round(max(xs) - min(xs), 2), round(max(zs) - min(zs), 2)]})
    s = v.get("stage")
    return {"name": v.get("name") or v.get("template") or "", "room": [w, d, h],
            "ceiling": (v.get("room") or {}).get("ceiling"),
            "stage": ({"front_z": round(s["z"] + s["depth"], 2), "width": s["width"],
                       "depth": s["depth"], "height": s["height"]} if s else None),
            "rigging": rigs, "zones": zones,
            "objects": [{"kind": o["kind"], "x": o["x"], "z": o["z"]}
                        for o in v.get("objects") or [] if o["kind"] != "mark"],
            "marks": [{"name": o["name"], "x": o["x"], "z": o["z"]}
                      for o in v.get("objects") or [] if o["kind"] == "mark"]}


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def _rect(x0, z0, x1, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def _truss(v, name, x0, x1, y, z, kind="truss"):
    return _clean_rig({"kind": kind, "name": name, "a": [x0, y, z], "b": [x1, y, z]}, v)


def _tower(v, name, x, z, top, kind="tower"):
    return _clean_rig({"kind": kind, "name": name, "a": [x, 0, z], "b": [x, top, z]}, v)


def _obj(v, kind, name, x, z, w, d, h, y=0.0, rot=0.0):
    return _clean_object({"kind": kind, "name": name, "x": x, "z": z, "w": w,
                          "d": d, "h": h, "y": y, "rot": rot}, v)


def _zone(v, kind, name, x0, z0, x1, z1, density=-1):
    return _clean_zone({"kind": kind, "name": name, "points": _rect(x0, z0, x1, z1),
                        "density": density}, v)


def _club(v, W, D, H):
    """A club: DJ riser at the back, dance floor, bar at the far end."""
    v["room"].update({"width": W, "depth": D, "height": H, "back": -1.5,
                      "ceiling": "flat", "floor": "concrete"})
    sw, sd = min(8.0, W * 0.5), min(4.0, D * 0.2)
    v["stage"] = {"x": 0.0, "z": 0.0, "width": sw, "depth": sd, "height": 0.8,
                  "colour": "#1f2026"}
    top = H - 0.7
    rigs = [
        _truss(v, "Upstage truss", -sw / 2, sw / 2, top - 0.3, 0.4),
        _truss(v, "Front truss", -W * 0.4, W * 0.4, top, sd + 0.6),
        _truss(v, "Mid truss", -W * 0.42, W * 0.42, top, sd + D * 0.25),
        _truss(v, "Rear truss", -W * 0.42, W * 0.42, top, sd + D * 0.48),
        _tower(v, "Tower SL", -sw / 2 - 0.6, sd + 0.2, top - 0.6),
        _tower(v, "Tower SR", sw / 2 + 0.6, sd + 0.2, top - 0.6),
    ]
    objs = [
        _obj(v, "dj_booth", "DJ booth", 0, sd * 0.5, 3.0, 1.0, 1.05, y=0.8),
        _obj(v, "speaker", "Stack SL", -sw / 2 - 0.9, sd - 0.4, 1.1, 1.0, 2.3),
        _obj(v, "speaker", "Stack SR", sw / 2 + 0.9, sd - 0.4, 1.1, 1.0, 2.3),
        _obj(v, "bar", "Bar", 0, sd + D * 0.78, min(10.0, W * 0.6), 0.8, 1.1),
        _obj(v, "mark", "DJ", 0, sd * 0.25, 0.5, 0.5, 0.02, y=0.8),
    ]
    zones = [
        _zone(v, "dj", "DJ", -sw / 2, 0, sw / 2, sd),
        _zone(v, "dancefloor", "Dance floor", -W * 0.44, sd + 0.8, W * 0.44, sd + D * 0.55, 0.7),
        _zone(v, "bar", "Bar", -W * 0.44, sd + D * 0.62, W * 0.44, sd + D * 0.74, 0.35),
    ]
    return rigs, objs, zones


def _small_club(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -1.0,
                      "ceiling": "flat", "floor": "wood"})
    v["stage"] = {"x": 0.0, "z": 0.0, "width": min(4.0, W * 0.4), "depth": 2.2,
                  "height": 0.3, "colour": "#1f2026"}
    top = H - 0.25
    rigs = [_truss(v, "Pipe 1", -W * 0.4, W * 0.4, top, 1.6, "pipe"),
            _truss(v, "Pipe 2", -W * 0.4, W * 0.4, top, D * 0.4, "pipe"),
            _truss(v, "Pipe 3", -W * 0.4, W * 0.4, top, D * 0.62, "pipe")]
    objs = [_obj(v, "dj_booth", "DJ booth", 0, 1.1, 2.2, 0.8, 1.0, y=0.3),
            _obj(v, "speaker", "Speaker L", -W * 0.4, 0.8, 0.6, 0.6, 1.6),
            _obj(v, "speaker", "Speaker R", W * 0.4, 0.8, 0.6, 0.6, 1.6),
            _obj(v, "bar", "Bar", W / 2 - 0.6, D * 0.65, 0.8, D * 0.45, 1.1, rot=0)]
    zones = [_zone(v, "dancefloor", "Dance floor", -W * 0.42, 2.6, W * 0.3, D * 0.7, 0.75)]
    return rigs, objs, zones


def _warehouse(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -2.0,
                      "ceiling": "open", "floor": "concrete"})
    sw, sd = min(14.0, W * 0.5), 7.0
    v["stage"] = {"x": 0.0, "z": 0.0, "width": sw, "depth": sd, "height": 1.4,
                  "colour": "#1b1c21"}
    top = H - 1.5
    g0, g1 = sd + 3.0, sd + 3.0 + min(14.0, D * 0.35)
    gx = min(8.0, W * 0.3)
    rigs = [
        _truss(v, "Stage front truss", -sw / 2 - 1, sw / 2 + 1, top, sd - 0.5),
        _truss(v, "Stage back truss", -sw / 2, sw / 2, top, 1.0),
        _tower(v, "Tower SL", -sw / 2 - 1.2, sd - 0.5, top),
        _tower(v, "Tower SR", sw / 2 + 1.2, sd - 0.5, top),
        _truss(v, "Grid front", -gx, gx, top, g0),
        _truss(v, "Grid back", -gx, gx, top, g1),
        _clean_rig({"kind": "truss", "name": "Grid left", "a": [-gx, top, g0], "b": [-gx, top, g1]}, v),
        _clean_rig({"kind": "truss", "name": "Grid right", "a": [gx, top, g0], "b": [gx, top, g1]}, v),
        _truss(v, "Grid centre", -gx, gx, top, (g0 + g1) / 2),
    ]
    objs = [_obj(v, "dj_booth", "DJ booth", 0, sd * 0.45, 4.0, 1.2, 1.1, y=1.4),
            _obj(v, "speaker", "Hang L", -sw / 2 - 2.2, sd, 1.2, 1.2, 3.4, y=2.5),
            _obj(v, "speaker", "Hang R", sw / 2 + 2.2, sd, 1.2, 1.2, 3.4, y=2.5),
            _obj(v, "sub", "Subs", 0, sd + 0.6, sw * 0.8, 1.0, 1.0),
            _obj(v, "riser", "FOH", 0, D * 0.72, 5.0, 3.0, 0.4),
            _obj(v, "mark", "DJ", 0, sd * 0.3, 0.5, 0.5, 0.02, y=1.4)]
    zones = [_zone(v, "dj", "Stage", -sw / 2, 0, sw / 2, sd),
             _zone(v, "dancefloor", "Floor", -W * 0.45, sd + 1.5, W * 0.45, D * 0.68, 0.8),
             _zone(v, "foh", "FOH", -2.5, D * 0.72 - 1.5, 2.5, D * 0.72 + 1.5),
             _zone(v, "bar", "Bar", -W * 0.45, D * 0.8, W * 0.45, D * 0.92, 0.3)]
    return rigs, objs, zones


def _concert(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -2.0,
                      "ceiling": "open", "floor": "concrete"})
    sw, sd = min(14.0, W * 0.6), 10.0
    v["stage"] = {"x": 0.0, "z": 0.0, "width": sw, "depth": sd, "height": 1.5,
                  "colour": "#1b1c21"}
    top = H - 1.2
    rigs = [_truss(v, "Front truss", -sw / 2, sw / 2, top, sd - 0.8),
            _truss(v, "Mid truss", -sw / 2, sw / 2, top, sd * 0.55),
            _truss(v, "Back truss", -sw / 2, sw / 2, top, 1.2),
            _tower(v, "Tower SL", -sw / 2 - 1.0, sd - 0.8, top),
            _tower(v, "Tower SR", sw / 2 + 1.0, sd - 0.8, top),
            _truss(v, "FOH truss", -sw * 0.35, sw * 0.35, top - 1, sd + 8)]
    objs = [_obj(v, "riser", "Drum riser", 0, 3.5, 3.0, 2.5, 0.6, y=1.5),
            _obj(v, "riser", "FOH", 0, D * 0.65, 5.0, 3.0, 0.4),
            _obj(v, "mark", "Lead", 0, sd - 2.2, 0.5, 0.5, 0.02, y=1.5)]
    zones = [_zone(v, "stage", "Stage", -sw / 2, 0, sw / 2, sd),
             _zone(v, "standing", "Standing", -W * 0.45, sd + 1.5, W * 0.45, D * 0.6, 0.75),
             _zone(v, "foh", "FOH", -2.5, D * 0.65 - 1.5, 2.5, D * 0.65 + 1.5)]
    return rigs, objs, zones


def _theatre(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -1.0,
                      "ceiling": "flat", "floor": "wood"})
    sw, sd = min(12.0, W * 0.75), 8.0
    v["stage"] = {"x": 0.0, "z": 0.0, "width": sw, "depth": sd, "height": 1.0,
                  "colour": "#2b2320"}
    top = H - 1.5
    rigs = [_truss(v, f"LX {i + 1}", -sw / 2, sw / 2, top, z, "pipe")
            for i, z in enumerate((1.5, 3.5, 5.5, 7.2))]
    rigs.append(_truss(v, "FOH bar", -sw * 0.45, sw * 0.45, top - 1.0, sd + 5.0))
    for s, x in (("SL", -sw / 2 - 0.5), ("SR", sw / 2 + 0.5)):
        rigs.append(_tower(v, f"Boom {s}", x, sd * 0.5, 3.0, "stand"))
    zones = [_zone(v, "stage", "Stage", -sw / 2, 0, sw / 2, sd),
             _zone(v, "seating", "Stalls", -W * 0.42, sd + 2.0, W * 0.42, D - 2.0, 0.6)]
    return rigs, [_obj(v, "mark", "Centre", 0, sd * 0.6, 0.5, 0.5, 0.02, y=1.0)], zones


def _ballroom(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -1.0,
                      "ceiling": "flat", "floor": "wood"})
    v["stage"] = {"x": 0.0, "z": 0.0, "width": 6.0, "depth": 3.0, "height": 0.6,
                  "colour": "#24252b"}
    rigs = [_tower(v, "Stand SL", -4.0, 3.4, 3.2, "stand"),
            _tower(v, "Stand SR", 4.0, 3.4, 3.2, "stand"),
            _truss(v, "Stage truss", -3.5, 3.5, H - 1.0, 3.2)]
    objs = [_obj(v, "table", f"Table {i + 1}", x, z, 1.8, 1.8, 0.75)
            for i, (x, z) in enumerate([(-6, 12), (-2, 13), (2, 13), (6, 12),
                                        (-6, 16), (-2, 17), (2, 17), (6, 16)])]
    zones = [_zone(v, "dancefloor", "Dance floor", -4, 4.5, 4, 9.5, 0.5),
             _zone(v, "seating", "Tables", -W * 0.42, 10.5, W * 0.42, D - 3, 0.35)]
    return rigs, objs, zones


def _outdoor(v, W, D, H):
    v["room"].update({"width": W, "depth": D, "height": H, "back": -1.0,
                      "ceiling": "none", "floor": "grass"})
    sw, sd = 12.0, 8.0
    v["stage"] = {"x": 0.0, "z": 0.0, "width": sw, "depth": sd, "height": 1.5,
                  "colour": "#1b1c21"}
    top = H - 0.5
    rigs = [_truss(v, "Roof front", -sw / 2, sw / 2, top, sd),
            _truss(v, "Roof back", -sw / 2, sw / 2, top, 0.5),
            _clean_rig({"kind": "truss", "name": "Roof left", "a": [-sw / 2, top, 0.5], "b": [-sw / 2, top, sd]}, v),
            _clean_rig({"kind": "truss", "name": "Roof right", "a": [sw / 2, top, 0.5], "b": [sw / 2, top, sd]}, v),
            _tower(v, "Leg DSL", -sw / 2, sd, top), _tower(v, "Leg DSR", sw / 2, sd, top),
            _tower(v, "Leg USL", -sw / 2, 0.5, top), _tower(v, "Leg USR", sw / 2, 0.5, top)]
    zones = [_zone(v, "standing", "Field", -W * 0.45, sd + 2, W * 0.45, D - 1, 0.6)]
    return rigs, [], zones


TEMPLATES = {
    "club": ("Club", (16.0, 22.0, 5.5), _club),
    "small_club": ("Small club / bar", (10.0, 14.0, 3.6), _small_club),
    "warehouse": ("Warehouse rave", (30.0, 40.0, 9.0), _warehouse),
    "concert": ("Concert stage", (24.0, 32.0, 11.0), _concert),
    "theatre": ("Theatre", (16.0, 26.0, 9.0), _theatre),
    "ballroom": ("Ballroom / event", (20.0, 24.0, 5.0), _ballroom),
    "outdoor": ("Outdoor stage", (24.0, 30.0, 9.0), _outdoor),
}


def template(name: str, width=None, depth=None, height=None) -> dict:
    key = str(name or "club").strip().lower().replace(" ", "_")
    if key not in TEMPLATES:
        raise ValueError(f"unknown venue template {name!r}; "
                         f"try: {', '.join(TEMPLATES)}")
    label, (w0, d0, h0), build = TEMPLATES[key]
    W = _num(width, w0, 4, 120) if width else w0
    D = _num(depth, d0, 4, 160) if depth else d0
    H = _num(height, h0, 2.2, 40) if height else h0
    v = empty()
    v["auto"] = False
    v["name"] = label
    v["template"] = key
    rigs, objs, zones = build(v, W, D, H)
    v["rigging"] = [r for r in rigs if r]
    v["objects"] = [o for o in objs if o]
    v["zones"] = [z for z in zones if z]
    return normalise(copy.deepcopy(v))


def template_list() -> list[dict]:
    return [{"key": k, "name": label, "size": list(size)}
            for k, (label, size, _) in TEMPLATES.items()]


# ---------------------------------------------------------------------------
# edits (pure: take a venue, return the changed venue)
# ---------------------------------------------------------------------------
def add_item(v: dict, raw: dict) -> tuple[dict, dict]:
    v = normalise(copy.deepcopy(v))
    kind = str((raw or {}).get("kind") or "").lower()
    raw = dict(raw or {})
    raw.pop("id", None)
    if kind in RIG_KINDS:
        item = _clean_rig(raw, v)
        keep_rig_inside(v, item)                # never outside the walls or through the ceiling
        v["rigging"].append(item)
    elif kind in OBJECT_KINDS:
        item = _clean_object(raw, v)
        v["objects"].append(item)
    elif kind in ZONE_KINDS:
        item = _clean_zone(raw, v)
        if not item:
            raise ValueError("a zone needs at least three points")
        v["zones"].append(item)
    else:
        raise ValueError(f"unknown venue item kind {kind!r}")
    v["auto"] = False if dims(v)[0] else v["auto"]
    return v, item


def find(v: dict, ident: str) -> tuple[str, int] | None:
    for key in ("rigging", "objects", "zones"):
        for i, item in enumerate(v.get(key) or []):
            if item["id"] == ident:
                return key, i
    return None


def update_item(v: dict, ident: str, changes: dict) -> tuple[dict, dict]:
    v = normalise(copy.deepcopy(v))
    where = find(v, ident)
    if not where:
        raise ValueError(f"no venue item {ident!r}")
    key, i = where
    merged = {**v[key][i], **{k: val for k, val in (changes or {}).items()
                              if k not in ("id", "kind")}, "id": ident}
    clean = {"rigging": _clean_rig, "objects": _clean_object,
             "zones": _clean_zone}[key](merged, v)
    if not clean:
        raise ValueError("that change would leave the item invalid")
    clean["id"] = ident
    if key == "rigging":
        keep_rig_inside(v, clean)
    v[key][i] = clean
    return v, clean


def remove_item(v: dict, ident: str) -> dict:
    v = normalise(copy.deepcopy(v))
    where = find(v, ident)
    if not where:
        raise ValueError(f"no venue item {ident!r}")
    key, i = where
    del v[key][i]
    return v
