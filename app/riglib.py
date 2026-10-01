"""The rigging library: truss, pipe, poles and stands at real sizes, the
shapes they make (a straight run, a corner, a frame, a circle, a goal post)
and the rigging report - pick-up points and the load on each.

A shape is made of plain straight pieces (venue rigging items) that share a
`group`, so lights attach, aim and draw on a circle truss exactly as on a
straight one.  Weights are typical for the type (a 29 cm box truss is about
5 kg/m); the fixtures' own weights come from their library files.

Pure: the engine hands in the venue, the patch and a weight lookup.
"""
from __future__ import annotations

import math

# id: name, rig kind, cross-section (m), kg per metre (or kg each), what
# lengths it comes in, and the most a stand / pole may carry
PIECES = {
    "box30": {"name": "Box truss 29 cm (F34 type)", "kind": "truss", "size": 0.29, "kg_m": 5.0},
    "box22": {"name": "Box truss 22 cm (F24 type)", "kind": "truss", "size": 0.22, "kg_m": 3.3},
    "box40": {"name": "Box truss 40 cm (F44 type)", "kind": "truss", "size": 0.40, "kg_m": 7.5},
    "tri30": {"name": "Triangle truss 29 cm (F33 type)", "kind": "truss", "size": 0.29, "kg_m": 4.0},
    "ladder30": {"name": "Ladder truss 29 cm (F32 type)", "kind": "truss", "size": 0.29, "kg_m": 2.8},
    "pipe48": {"name": "Pipe / bar 48 mm", "kind": "pipe", "size": 0.048, "kg_m": 3.6},
    "pole30": {"name": "Truss pole 29 cm", "kind": "tower", "size": 0.29, "kg_m": 5.0, "max_kg": 150},
    "stand": {"name": "Wind-up stand", "kind": "stand", "size": 0.05, "kg": 32.0, "max_kg": 80},
    "base": {"name": "Base plate", "kind": "base", "size": 0.4, "kg": 25.0},
}
LENGTHS = (4.0, 3.0, 2.5, 2.0, 1.5, 1.0, 0.5)       # straight sections sold
PRESETS = ("straight", "corner", "frame", "circle", "goalpost", "pole", "stand")

# a light whose file gives no weight: a typical one for its kind
KIND_KG = {"mover": 18.0, "laser": 6.0, "bar": 4.0, "par": 3.0, "blinder": 6.0,
           "strobe": 4.0, "sfx": 20.0, "other": 5.0}
POINT_WARN_KG = 250.0            # per pick-up: check your motors / points


def pieces_public() -> list[dict]:
    return [{"id": k, **v} for k, v in PIECES.items()]


def _r(v: float) -> float:
    return round(float(v), 3)


def build(preset: str, piece: str = "box30", x: float = 0.0, y: float = 4.0, z: float = 2.0,
          length: float = 4.0, width: float = 4.0, depth: float = 3.0, diameter: float = 4.0,
          segments: int | None = None, height: float = 3.0, rot: float = 0.0, name: str = "") -> list[dict]:
    """The rigging items a shape is made of (centred on x, z; at height y
    for hung pieces; `rot` degrees about the vertical)."""
    preset = str(preset or "straight").lower()
    if preset not in PRESETS:
        raise ValueError("a shape is one of " + ", ".join(PRESETS))
    if piece not in PIECES:
        raise ValueError("a piece is one of " + ", ".join(PIECES))
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def at(dx, dy, dz):
        return [_r(x + dx * ca - dz * sa), _r(dy), _r(z + dx * sa + dz * ca)]

    def seg(a, b, pc=piece, label=""):
        k = PIECES[pc]
        return {"kind": k["kind"], "model": pc, "size": k["size"], "a": a, "b": b,
                "name": (label or name or k["name"])[:40]}

    length, width, depth = (max(0.5, min(60.0, float(v))) for v in (length, width, depth))
    height = max(0.5, min(20.0, float(height)))
    if preset == "straight":
        return [seg(at(-length / 2, y, 0), at(length / 2, y, 0))]
    if preset == "corner":
        return [seg(at(-width / 2, y, 0), at(width / 2, y, 0), label=name or "Corner"),
                seg(at(width / 2, y, 0), at(width / 2, y, depth), label=name or "Corner")]
    if preset == "frame":
        c = [(-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)]
        return [seg(at(c[i][0], y, c[i][1]), at(c[(i + 1) % 4][0], y, c[(i + 1) % 4][1]), label=name or "Frame")
                for i in range(4)]
    if preset == "circle":
        d = max(1.0, min(30.0, float(diameter)))
        n = int(segments) if segments else (8 if d <= 4 else 12 if d <= 8 else 16)
        n = max(4, min(32, n))
        pts = [(d / 2 * math.cos(2 * math.pi * i / n), d / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return [seg(at(pts[i][0], y, pts[i][1]), at(pts[(i + 1) % n][0], y, pts[(i + 1) % n][1]),
                    label=name or f"Circle {d:g} m") for i in range(n)]
    if preset == "goalpost":
        top = height
        return [seg(at(-width / 2, 0, 0), at(-width / 2, top, 0), "pole30", name or "Goal post"),
                seg(at(width / 2, 0, 0), at(width / 2, top, 0), "pole30", name or "Goal post"),
                seg(at(-width / 2, top, 0), at(width / 2, top, 0), label=name or "Goal post")]
    if preset == "pole":
        return [seg(at(0, 0, 0), at(0, height, 0), "pole30" if piece not in ("pole30",) else piece)]
    return [seg(at(0, 0, 0), at(0, height, 0), "stand")]           # stand


def sections(length: float) -> list[float]:
    """Standard straight sections that make up `length` (to 0.5 m)."""
    left = math.ceil(max(0.0, length) * 2 - 1e-6) / 2
    out = []
    for L in LENGTHS:
        while left >= L - 1e-6:
            out.append(L)
            left -= L
    return out


def _vertical(r: dict) -> bool:
    a, b = r["a"], r["b"]
    return abs(a[1] - b[1]) > 0.5 and math.hypot(a[0] - b[0], a[2] - b[2]) < 0.3


def light_kind(head: dict) -> str:
    m = set(head.get("map") or [])
    name = f"{head.get('model', '')} {head.get('name', '')}".lower()
    if {"pan", "tilt"} & m:
        return "mover"
    if any(r.startswith("laser") for r in m):
        return "laser"
    if any(k in name for k in ("blinder", "molefay")):
        return "blinder"
    if "strobe" in name:
        return "strobe"
    if any(r.startswith(("fire", "co2", "confetti", "spark", "fog", "haze")) for r in m):
        return "sfx"
    if "bar" in name or sum(1 for r in m if r.startswith("red")) > 1:
        return "bar"
    if m & {"red", "green", "blue", "dimmer"}:
        return "par"
    return "other"


def report(venue: dict, patch: list[dict], phys_of) -> dict:
    """Every piece (a shape counts as one), what hangs on it, its pick-up
    points and the load on each.  phys_of(head) -> {kg?, watts?}."""
    rigging = list(venue.get("rigging") or [])
    ceiling = float(((venue.get("room") or {}).get("height")) or 0)
    on: dict[str, list[dict]] = {}
    for h in patch:
        rid = (h.get("mount") or {}).get("rig")
        if rid:
            on.setdefault(rid, []).append(h)
    groups: dict[str, list[dict]] = {}
    for r in rigging:
        groups.setdefault(r.get("group") or r["id"], []).append(r)
    rows, total_kg, total_w, parts = [], 0.0, 0.0, {}
    for key, items in groups.items():
        first = items[0]
        model = first.get("model") or ""
        spec = PIECES.get(model) or {}
        length = sum(math.dist(r["a"], r["b"]) for r in items)
        if "kg" in spec:
            self_kg = spec["kg"] * len(items)
        else:
            self_kg = length * spec.get("kg_m", {"truss": 5.0, "pipe": 3.6, "tower": 5.0}.get(first["kind"], 4.0))
        lights, light_kg, light_w, guessed = [], 0.0, 0.0, 0
        for r in items:
            for h in on.get(r["id"], []):
                ph = phys_of(h) or {}
                kg = ph.get("kg")
                if kg is None:
                    kg = KIND_KG[light_kind(h)]
                    guessed += 1
                light_kg += kg
                light_w += ph.get("watts") or 0
                lights.append({"head": h["head_no"], "name": h.get("name", ""), "kg": round(kg, 1),
                               "guessed": "kg" not in ph})
        total = self_kg + light_kg
        ground = first["kind"] in ("tower", "stand", "base") or all(_vertical(r) for r in items) \
            or any(min(r["a"][1], r["b"][1]) < 0.3 for r in items)
        horiz = [r for r in items if not _vertical(r)]
        # the trim is the height of its underside (the line runs through its middle)
        trim = round(min(min(r["a"][1], r["b"][1]) - float(r.get("size") or 0.3) / 2 for r in horiz), 2) if horiz else None
        if ground:
            points = 0
        elif len(items) > 1:
            points = len(items) if len(items) <= 6 else math.ceil(len(items) / 2)   # a corner each / every other
        else:
            points = max(2, math.ceil(length / 4) + 1)          # every ~4 m, both ends
        per = total / points if points else None
        warn = []
        if per and per > POINT_WARN_KG:
            warn.append(f"{per:.0f} kg on each point - check the motors / points")
        if spec.get("max_kg") and light_kg > spec["max_kg"] * len(items):
            warn.append(f"{light_kg:.0f} kg on a {spec['name'].lower()} made for {spec['max_kg']} kg")
        if ceiling and trim is not None and trim > ceiling - 0.2:
            warn.append("hangs at the ceiling - no room for motors / chains")
        if guessed:
            warn.append(f"{guessed} light weight(s) guessed from the kind of light")
        arc = len(items) >= 4 and first.get("name", "").startswith("Circle")
        if arc and model:
            dia = max(math.hypot(r["a"][0] - items[0]["a"][0], r["a"][2] - items[0]["a"][2]) for r in items)
            k = (model, f"arc 1/{len(items)} of Ø{dia:.1f} m")
            parts[k] = parts.get(k, 0) + len(items)
        elif first["kind"] in ("truss", "pipe") and model:
            for r in items:
                for L in sections(math.dist(r["a"], r["b"])):
                    k = (model, L)
                    parts[k] = parts.get(k, 0) + 1
        elif model:
            parts[(model, None)] = parts.get((model, None), 0) + len(items)
        rows.append({"id": key, "ids": [r["id"] for r in items],
                     "name": first.get("name") or spec.get("name") or first["kind"],
                     "kind": first["kind"], "model": model, "pieces": len(items),
                     "length": round(length, 2), "trim": trim, "self_kg": round(self_kg, 1),
                     "lights": lights, "light_kg": round(light_kg, 1), "total_kg": round(total, 1),
                     "watts": round(light_w), "points": points,
                     "per_point_kg": round(per, 1) if per else None,
                     "ground": ground, "warnings": warn})
        total_kg += total
        total_w += light_w
    parts_list = [{"model": m, "name": PIECES.get(m, {}).get("name", m), "length": L, "count": n}
                  for (m, L), n in sorted(parts.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))]
    return {"rigs": rows, "total_kg": round(total_kg, 1), "total_watts": round(total_w),
            "points": sum(r["points"] for r in rows), "parts": parts_list}


def report_csv(rep: dict) -> str:
    lines = ["piece,kind,model,length m,trim m,self kg,lights,lights kg,total kg,points,kg per point,warnings"]
    for r in rep["rigs"]:
        lines.append(",".join(str(v).replace(",", ";") for v in (
            r["name"], r["kind"], r["model"], r["length"], "" if r["trim"] is None else r["trim"],
            r["self_kg"], len(r["lights"]), r["light_kg"], r["total_kg"],
            "ground" if r["ground"] else r["points"], r["per_point_kg"] or "", " | ".join(r["warnings"]))))
    lines.append("")
    lines.append("parts,,length m,count")
    for p in rep["parts"]:
        lines.append(f"{p['name']},,{p['length'] or ''},{p['count']}")
    return "\n".join(lines) + "\n"
