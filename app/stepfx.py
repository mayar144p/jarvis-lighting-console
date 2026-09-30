"""Step effects: an effect made of your own looks.

A step effect is a list of steps - each one either the programmer as it
was when the step was taken (values for those lights) or a palette (so
changing the palette changes the effect) - with a time for each step and
how much of it is a crossfade from the step before.  Running, every light
goes round the steps; `spread` puts each light that far round the cycle
from the one before (0: all together, 360: spread evenly over one cycle),
by light number or - with a direction - by where the lights are.

    {"id", "name", "steps": [{"values": {head: {role: v}}} |
                             {"palette": {"kind", "n"}},
                             "time": seconds, "fade": 0..1}],
     "curve": "smooth" | "linear" | "snap", "spread": degrees}

Pure: the engine hands in the palettes and the elapsed time.
"""
from __future__ import annotations

import math

CURVES = ("smooth", "linear", "snap")
# channels that are slots, not levels: they change at the middle of the
# fade instead of sliding through every gobo on the wheel
SNAP_ROLES = {"wheel", "gobo", "gobo_rot", "prism", "shutter", "strobe", "macro", "mode"}
MAX_STEPS = 32


def _f(v, default, lo, hi) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(x) or math.isinf(x):
        return float(default)
    return max(lo, min(hi, x))


def clean(raw: dict, ident: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    steps = []
    for st in (raw.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(st, dict):
            continue
        out = {"time": round(_f(st.get("time"), 1.0, 0.05, 600), 3), "fade": round(_f(st.get("fade"), 0.5, 0, 1), 3)}
        if isinstance(st.get("palette"), dict) and st["palette"].get("kind"):
            out["palette"] = {"kind": str(st["palette"]["kind"]), "n": int(_f(st["palette"].get("n"), 1, 1, 9999))}
        elif isinstance(st.get("values"), dict):
            vals = {}
            for head, row in st["values"].items():
                if not isinstance(row, dict):
                    continue
                clean_row = {str(r): v for r, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if clean_row:
                    vals[str(int(head))] = clean_row
            if not vals:
                continue
            out["values"] = vals
        else:
            continue
        if st.get("name"):
            out["name"] = str(st["name"])[:30]
        steps.append(out)
    if len(steps) < 2:
        raise ValueError("a step effect needs at least two steps")
    curve = str(raw.get("curve") or "smooth")
    return {"id": ident, "name": str(raw.get("name") or f"Steps {ident}")[:30], "steps": steps,
            "curve": curve if curve in CURVES else "smooth",
            "spread": round(_f(raw.get("spread"), 0, 0, 720), 1)}


def heads_of(fx: dict) -> list[int]:
    """The lights a step effect was made on (from its programmer steps)."""
    out = set()
    for st in fx["steps"]:
        out |= {int(h) for h in (st.get("values") or {})}
    return sorted(out)


def _ease(curve: str, x: float) -> float:
    if curve == "snap":
        return 1.0 if x >= 0.5 else 0.0
    if curve == "linear":
        return x
    return x * x * (3 - 2 * x)                       # smooth


def step_row(st: dict, head: int, roles: list[str], palettes: dict) -> dict:
    """What one step asks of one light."""
    if "palette" in st:
        pal = next((p for p in palettes.get(st["palette"]["kind"]) or [] if p.get("n") == st["palette"]["n"]), None)
        vals = (pal or {}).get("values") or {}
        return {r: v for r, v in vals.items() if r in roles and isinstance(v, (int, float))}
    return dict((st.get("values") or {}).get(str(head)) or {})


def values(fx: dict, head: int, roles: list[str], palettes: dict, t: float) -> dict:
    """The light's values at `t` seconds into the cycle."""
    steps = fx["steps"]
    total = sum(s["time"] for s in steps)
    if total <= 0:
        return {}
    t %= total
    k, start = 0, 0.0
    for i, s in enumerate(steps):
        if t < start + s["time"]:
            k = i
            break
        start += s["time"]
    cur = steps[k]
    local = (t - start) / cur["time"]
    here = step_row(cur, head, roles, palettes)
    fade = cur["fade"]
    if fade <= 0 or local >= fade:
        return {r: int(round(v)) for r, v in here.items()}
    prev = step_row(steps[k - 1], head, roles, palettes)
    x = _ease(fx["curve"], local / fade)
    out = {}
    for r in set(here) | set(prev):
        a, b = prev.get(r), here.get(r)
        if a is None:
            if r.split("@")[0] not in SNAP_ROLES or x >= 0.5:
                out[r] = int(round(b))
        elif b is None:
            if x < 0.5:
                out[r] = int(round(a))
        elif r.split("@")[0] in SNAP_ROLES:
            out[r] = int(round(b if x >= 0.5 else a))
        else:
            out[r] = int(round(a + (b - a) * x))
    return out


def cycle(fx: dict) -> float:
    return sum(s["time"] for s in fx["steps"])
