"""What a light's beam is DOING, read from its own fixture file.

The light feed's `beam` gives the 3D view each channel as a number 0..1.
That is enough for zoom or frost, which move smoothly, but not for a
channel whose ranges mean different things: on a Sharpy, prism 200 is
"8-facet prism in", gobo 170 is "Gobo 3 shake", strobe 230 is "random
strobe".  This reads the file's own words for the range each channel is
in and says what the light does:

    prism   facets in the beam (0 = out), plin: a linear prism
    prot    the prism turning: {"spin": turns a second, - = the other way}
            or standing at {"at": degrees}
    grot    the gobo turning (the same)
    gshake  the gobo shaking, times a second
    gscroll the gobo wheel turning through its gobos, slots a second
    split   a colour wheel between two colours: [hex, hex]
    cscroll the colour wheel turning: {"v": slots a second, "cols": [hex...]}
    smode   how it strobes: "pulse", "random", "ramp_up", "ramp_down"
    shz     that strobe's speed, when the strobe channel has no plain rate

Only what the file says: a range with no words, or words this does not
know, adds nothing and the 3D view keeps its plain reading.
"""
from __future__ import annotations

import re


_FACETS = re.compile(r"(\d+)\s*-?\s*(?:facet|fold|face|x\b)", re.I)
_OUT = re.compile(r"\bout\b|\boff\b|no prism|\bopen\b|no function|\bidle\b", re.I)
_DEG = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*(?:-|–|to|\.\.)\s*(-?\d+(?:\.\d+)?)\s*°")
_CCW = re.compile(r"ccw|counter|anti|left|reverse|backward", re.I)
_SLOW_FAST = re.compile(r"slow\s*(?:-|–|>|to|→)+\s*fast", re.I)
_FAST_SLOW = re.compile(r"fast\s*(?:-|–|>|to|→)+\s*slow", re.I)
_TURNING = re.compile(r"rotat|spin|scroll|rainbow|continuous|cycl|flow", re.I)
_SPLIT = re.compile(r"\s+(?:\+|/|&|-|–|and)\s+|\s*[+/&]\s*", re.I)
_PLAIN = re.compile(r"^\s*(?:empty|open|white|no colou?r|empty position)\b", re.I)


def _cap(caps, v: int):
    """(from, to, words) of the range v is in, or None."""
    for c in caps or []:
        if isinstance(c, (list, tuple)) and len(c) >= 3 and int(c[0]) <= v <= int(c[1]):
            return int(c[0]), int(c[1]), str(c[2] or "")
    return None


def _speed(lo: int, hi: int, v: int, words: str, top: float = 1.5) -> float:
    """How fast, from where v is in its range and which way the range runs."""
    f = (v - lo) / max(1, hi - lo)
    if _FAST_SLOW.search(words):
        f = 1 - f
    elif not _SLOW_FAST.search(words) and not re.search(r"slow|fast|speed", words, re.I):
        f = 0.5
    return round(0.05 + (top - 0.05) * f, 3)


def _turn(lo: int, hi: int, v: int, words: str) -> dict | None:
    """A rotation range: spinning (turns a second) or indexed (degrees)."""
    f = (v - lo) / max(1, hi - lo)
    if re.search(r"index|position|angle", words, re.I):
        m = _DEG.search(words)
        a, b = (float(m.group(1)), float(m.group(2))) if m else (0.0, 360.0)
        return {"at": round(a + (b - a) * f, 1)}
    if re.search(r"\bstop\b|no rotation|no function", words, re.I):
        return {"at": 0}
    if _TURNING.search(words) or re.search(r"\bcw\b|ccw|clockwise", words, re.I):
        s = _speed(lo, hi, v, words)
        return {"spin": -s if _CCW.search(words) else s}
    return None


# a light's second prism, colour wheel or gobo rotation is stored as an
# extra channel ("Aux 6 · 8 Facet Prism Rotation", app/fixlib.apply_aux):
# found by its name
_AUX_NAME = {
    "prism": re.compile(r"prism|facet", re.I),
    "gobo_rot": re.compile(r"gobo.*(rotat|index|spin)|(rotat|index|spin).*gobo", re.I),
    "gobo": re.compile(r"^(?!.*(rotat|index|spin)).*gobo", re.I),
    "wheel": re.compile(r"colou?r", re.I),
}


def _copies(ranges: dict, values: dict, role: str):
    """(caps, value) of each channel the light has for this role (a Sharpy
    has two prisms and three colour wheels), its extra channels last."""
    rng = ranges.get(role) or {}
    each = rng.get("caps_each") or [rng.get("caps") or []]
    base = values.get(role)
    for k, caps in enumerate(each, 1):
        v = values.get(f"{role}@{k}", base)
        if v is not None and caps:
            yield caps, max(0, min(255, int(v)))
    name = _AUX_NAME.get(role)
    for key in sorted(ranges, key=lambda r: int(r[3:]) if r[3:].isdigit() else 0) if name else ():
        aux = ranges[key]
        if key.startswith("aux") and aux.get("caps") and key in values and name.search(str(aux.get("name") or "")):
            yield aux["caps"], max(0, min(255, int(values[key])))


def hex_from_name(name: str) -> str | None:
    from app.showdesign import hex_from_name as named    # (showdesign imports the engine)
    return named(name)


def _split(words: str) -> list[str] | None:
    """'Red + Blue' -> two colours; 'Empty + UV filter' -> white and UV."""
    parts = [p for p in _SPLIT.split(words) if p.strip()]
    if len(parts) != 2:
        return None
    out = []
    for p in parts:
        hx = "#ffffff" if _PLAIN.match(p) else hex_from_name(p)
        if not hx:
            return None
        out.append(hx)
    return out if out[0] != out[1] else None


# The light feed asks for every light, several times a second on each
# screen: per model, which of its channels can say anything (most lights:
# none), and per model + those channels' values, the answer.
_PLAN: dict = {}            # id(ranges) -> (ranges, shutter role, the keys that matter)
_MEMO: dict = {}


def _keys(ranges: dict, shutter_role: str | None) -> frozenset:
    roles = {r for r in ("prism", "gobo_rot", "gobo", "wheel", shutter_role)
             if r and any((ranges.get(r) or {}).get("caps_each") or [(ranges.get(r) or {}).get("caps")])}
    roles |= {k for k in ranges if k.startswith("aux") and (ranges[k] or {}).get("caps")
              and any(rx.search(str(ranges[k].get("name") or "")) for rx in _AUX_NAME.values())}
    return frozenset(roles)


def describe(ranges: dict, values: dict, shutter_role: str | None = None) -> dict:
    """The beam's look, from the file's words for each channel's range."""
    plan = _PLAN.get(id(ranges))
    if plan is None or plan[0] is not ranges or plan[1] != shutter_role:
        if len(_PLAN) > 512:
            _PLAN.clear()
        plan = _PLAN[id(ranges)] = (ranges, shutter_role, _keys(ranges, shutter_role))
    if not plan[2]:
        return {}                         # a light with nothing to say (a PAR, a wash)
    memo = (id(ranges), shutter_role, tuple(sorted((k, v) for k, v in values.items() if k.split("@")[0] in plan[2])))
    hit = _MEMO.get(memo)
    if hit is None or hit[0] is not ranges:
        if len(_MEMO) > 4096:
            _MEMO.clear()
        hit = _MEMO[memo] = (ranges, _describe(ranges, values, shutter_role))
    return dict(hit[1])


def _describe(ranges: dict, values: dict, shutter_role: str | None) -> dict:
    out: dict = {}
    for caps, v in _copies(ranges, values, "prism"):
        c = _cap(caps, v)
        if not c:
            continue
        lo, hi, words = c
        has_facets = _FACETS.search(words)
        if re.search(r"prism", words, re.I) and _OUT.search(words) and not has_facets:
            continue                          # prism out
        turning = _TURNING.search(words) or re.search(r"index|\bcc?w\b|clockwise", words, re.I)
        if (turning and not has_facets and not re.search(r"insert|into|\bin\b", words, re.I)) \
                or not re.search(r"prism|facet|beam split", words, re.I):
            t = _turn(lo, hi, v, words)       # a prism ROTATION channel
            if t and "prot" not in out:
                out["prot"] = t
            continue
        m = _FACETS.search(words)
        facets = int(m.group(1)) if m else 3
        if facets > out.get("prism", 0):
            out["prism"] = max(2, min(16, facets))
            out["plin"] = bool(re.search(r"linear|line|row", words, re.I))
        t = _turn(lo, hi, v, words)           # "3-facet prism, rotating CW"
        if t and "spin" in t:
            out["prot"] = t
    for caps, v in _copies(ranges, values, "gobo_rot"):
        c = _cap(caps, v)
        if c and re.search(r"shak|wobbl|vibrat", c[2], re.I):
            out["gshake"] = round(_speed(c[0], c[1], v, c[2], 1.0) * 8, 2)   # a second wheel's gobo shaking
            continue
        t = c and _turn(*c[:2], v, c[2])
        if t and "grot" not in out:
            out["grot"] = t
    for caps, v in _copies(ranges, values, "gobo"):
        c = _cap(caps, v)
        if not c:
            continue
        lo, hi, words = c
        if re.search(r"shak|wobbl|vibrat|bounce", words, re.I):
            out["gshake"] = round(_speed(lo, hi, v, words, 1.0) * 8, 2)
        elif _TURNING.search(words) and not re.search(r"\bgobo\s*\d", words, re.I):
            s = _speed(lo, hi, v, words)
            out["gscroll"] = round((-s if _CCW.search(words) else s) * 3, 3)
        break
    wheel = ranges.get("wheel") or {}
    for caps, v in _copies(ranges, values, "wheel"):
        c = _cap(caps, v)
        if not c:
            continue
        lo, hi, words = c
        if _TURNING.search(words):
            s = _speed(lo, hi, v, words)
            cols = [x["hex"] for x in wheel.get("slots") or [] if x.get("hex")][:16]
            if not cols:
                cols = [hx for hx in (hex_from_name(str(k[2] or "")) for k in caps) if hx][:16]
            if len(cols) < 2:                  # a file that names no colours: a rainbow
                cols = ["#ff2020", "#ffee22", "#22ff44", "#22ffee", "#2244ff", "#ff22dd"]
            if len(cols) >= 2:
                out["cscroll"] = {"v": round((-s if _CCW.search(words) else s) * 3, 3), "cols": cols}
                break
        sp = _split(words)
        if sp:
            out["split"] = sp
            break
    if shutter_role:
        for caps, v in _copies(ranges, values, shutter_role):
            c = _cap(caps, v)
            if not c:
                break
            lo, hi, words = c
            mode = ("random" if re.search(r"random", words, re.I)
                    else "ramp_up" if re.search(r"ramp\s*(up|on)|slow on|open slow|fade in", words, re.I)
                    else "ramp_down" if re.search(r"ramp\s*(down|off)|slow off|close slow|fade out", words, re.I)
                    else "pulse" if re.search(r"puls|even on|gradual|fade on|breath", words, re.I) else None)
            if mode:
                out["smode"] = mode
                out["shz"] = round(_speed(lo, hi, v, words, 1.0) * 12, 2)
            break
    return out
