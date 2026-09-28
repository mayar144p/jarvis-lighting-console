"""What a fixture physically IS: its body type and its brand.

The visualiser draws every head as a 3D model of the real thing - a moving
spot on a yoke, a PAR can, a pixel batten, a Source Four barrel - styled
for the manufacturer.  That decision is made here, once, from what the
patch knows about the head (manufacturer, model name, DMX mode and its
channel roles), so the 3D view, the fixture list and the AI all agree on
what a head is.

When the head came from a GDTF file with its own 3D models, the visualiser
draws those instead; this module is what every other head looks like.

Pure functions, no I/O.  `describe(head)` is cached per fixture type.
"""
from __future__ import annotations

import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# Physical types.  `beam` is the (min, max) field angle in degrees the type
# typically has - a zoom channel sweeps it, a fixed-lens fixture uses min.
# ---------------------------------------------------------------------------
TYPES: dict[str, dict] = {
    "moving_spot":   {"label": "Moving spot",   "moving": True,  "beam": (12, 36)},
    "moving_wash":   {"label": "Moving wash",   "moving": True,  "beam": (8, 55)},
    "moving_beam":   {"label": "Moving beam",   "moving": True,  "beam": (2, 5)},
    "moving_hybrid": {"label": "Hybrid",        "moving": True,  "beam": (3, 40)},
    "moving_bar":    {"label": "Moving bar",    "moving": True,  "beam": (4, 40)},
    "par":           {"label": "LED PAR",       "moving": False, "beam": (25, 40)},
    "par_can":       {"label": "PAR can",       "moving": False, "beam": (12, 30)},
    "profile":       {"label": "Profile",       "moving": False, "beam": (19, 36)},
    "fresnel":       {"label": "Fresnel",       "moving": False, "beam": (15, 60)},
    "wash_panel":    {"label": "Panel wash",    "moving": False, "beam": (40, 90)},
    "cyc":           {"label": "Cyc / flood",   "moving": False, "beam": (70, 110)},
    "bar":           {"label": "LED batten",    "moving": False, "beam": (20, 40)},
    "strobe":        {"label": "Strobe",        "moving": False, "beam": (90, 120)},
    "blinder":       {"label": "Blinder",       "moving": False, "beam": (40, 60)},
    "tube":          {"label": "LED tube",      "moving": False, "beam": (110, 140)},
    "matrix":        {"label": "LED matrix",    "moving": False, "beam": (25, 40)},
    "laser":         {"label": "Laser",         "moving": False, "beam": (1, 1)},
    "followspot":    {"label": "Follow spot",   "moving": False, "beam": (3, 12)},
    "atmos":         {"label": "Haze / fog",    "moving": False, "beam": (0, 0)},
    "generic":       {"label": "Fixture",       "moving": False, "beam": (20, 40)},
}

# ---------------------------------------------------------------------------
# Brands.  `body` is the housing colour, `accent` the colour of the logo
# plate and trim, `finish` how shiny the housing reads.  These are styling
# for recognition on a busy stage, not official brand guidelines.
# ---------------------------------------------------------------------------
BRANDS: dict[str, dict] = {
    "robe":        {"name": "Robe",         "aliases": ("robe", "robe lighting"),
                    "body": "#17181b", "accent": "#e2e4e8", "finish": "satin"},
    "martin":      {"name": "Martin",       "aliases": ("martin", "martin professional", "harman"),
                    "body": "#161616", "accent": "#d7262e", "finish": "satin"},
    "claypaky":    {"name": "Claypaky",     "aliases": ("clay paky", "claypaky", "clay-paky"),
                    "body": "#1b1c1f", "accent": "#e6e6e6", "finish": "gloss"},
    "chauvet":     {"name": "Chauvet",      "aliases": ("chauvet", "chauvet professional",
                                                        "chauvet dj", "chauvet pro"),
                    "body": "#141517", "accent": "#e0312b", "finish": "matte"},
    "adj":         {"name": "ADJ",          "aliases": ("adj", "american dj", "adj products"),
                    "body": "#131313", "accent": "#d0202a", "finish": "matte"},
    "elation":     {"name": "Elation",      "aliases": ("elation", "elation professional"),
                    "body": "#151618", "accent": "#f28c1b", "finish": "satin"},
    "etc":         {"name": "ETC",          "aliases": ("etc", "electronic theatre controls",
                                                        "high end", "high end systems"),
                    "body": "#1a1a1c", "accent": "#e8e8e8", "finish": "satin"},
    "glp":         {"name": "GLP",          "aliases": ("glp", "german light products"),
                    "body": "#111214", "accent": "#e3e3e3", "finish": "satin"},
    "ayrton":      {"name": "Ayrton",       "aliases": ("ayrton",),
                    "body": "#16171a", "accent": "#e1062c", "finish": "gloss"},
    "vari-lite":   {"name": "Vari-Lite",    "aliases": ("vari-lite", "varilite", "vari lite"),
                    "body": "#17191c", "accent": "#3b82f6", "finish": "satin"},
    "astera":      {"name": "Astera",       "aliases": ("astera", "astera led"),
                    "body": "#1f2023", "accent": "#f5f5f5", "finish": "matte"},
    "sgm":         {"name": "SGM",          "aliases": ("sgm", "sgm light"),
                    "body": "#2b2d31", "accent": "#e30613", "finish": "matte"},
    "prolights":   {"name": "Prolights",    "aliases": ("prolights", "pro lights", "music & lights"),
                    "body": "#151515", "accent": "#e4002b", "finish": "matte"},
    "cameo":       {"name": "Cameo",        "aliases": ("cameo", "cameo light"),
                    "body": "#141414", "accent": "#e30613", "finish": "matte"},
    "showtec":     {"name": "Showtec",      "aliases": ("showtec", "highlite", "infinity"),
                    "body": "#141414", "accent": "#ffcc00", "finish": "matte"},
    "stairville":  {"name": "Stairville",   "aliases": ("stairville", "thomann"),
                    "body": "#161616", "accent": "#e2231a", "finish": "matte"},
    "eurolite":    {"name": "Eurolite",     "aliases": ("eurolite", "steinigke"),
                    "body": "#181818", "accent": "#2d7ff9", "finish": "matte"},
    "beamz":       {"name": "BeamZ",        "aliases": ("beamz", "beam z"),
                    "body": "#151515", "accent": "#00a3e0", "finish": "matte"},
    "acme":        {"name": "Acme",         "aliases": ("acme", "acme lighting"),
                    "body": "#181a1d", "accent": "#e2e2e2", "finish": "satin"},
    "ghost":       {"name": "Ghost",        "aliases": ("ghost",),
                    "body": "#f2f2f2", "accent": "#111111", "finish": "matte"},
    "portman":     {"name": "Portman",      "aliases": ("portman", "portman lights"),
                    "body": "#c9a266", "accent": "#2b2b2b", "finish": "metal"},
    "arri":        {"name": "ARRI",         "aliases": ("arri",),
                    "body": "#202226", "accent": "#1e88e5", "finish": "satin"},
    "strand":      {"name": "Strand",       "aliases": ("strand", "strand lighting", "selecon"),
                    "body": "#1b1b1b", "accent": "#d8d8d8", "finish": "satin"},
    "altman":      {"name": "Altman",       "aliases": ("altman",),
                    "body": "#1b1b1b", "accent": "#d0d0d0", "finish": "satin"},
    "lightsky":    {"name": "Lightsky",     "aliases": ("lightsky", "light sky"),
                    "body": "#141414", "accent": "#e30613", "finish": "satin"},
    "ultratec":    {"name": "Look / MDG",   "aliases": ("look solutions", "mdg", "antari", "ultratec"),
                    "body": "#2a2a2a", "accent": "#e0e0e0", "finish": "matte"},
    "kvant":       {"name": "Kvant",        "aliases": ("kvant", "kvant lasers"),
                    "body": "#1a1a1a", "accent": "#00c853", "finish": "matte"},
    "generic":     {"name": "Generic",      "aliases": ("generic", ""),
                    "body": "#26282c", "accent": "#9aa4b2", "finish": "matte"},
}

# Specific product families, matched against "<manufacturer> <model>".
# First match wins, so the more specific patterns come first.
FAMILIES: list[tuple[str, str, str]] = [
    # (regex, type, family label)
    (r"\bsharpy\s*(plus|x)\b|\bpointe\b|\bmythos\b|\bmega[\s-]?pointe\b|\bmegapointe\b"
     r"|\bforza\b|\bbmfl\b.*blade|\bmac aura xb\b", "moving_hybrid", ""),
    (r"\bsharpy\b|\bbeam\s*\d+|\bb[\s-]?eye\b.*beam", "moving_beam", ""),
    (r"\bb[\s-]?eye\b|\bspiider\b|\bimpression\b|\baura\b|\bmac quantum wash\b"
     r"|\bmagicpanel\b|\bmagic\s*panel\b|\bcoda\b|\brush.*wash|\brogue.*wash"
     r"|\bmaverick.*wash|\bmac.*wash|\bledwash\b|\bled wash\b|\bwash\b.*(zoom|\d+x)",
     "moving_wash", ""),
    (r"\bjdc\s*1\b|\bjdc1\b|\batomic\b|\bxstrobe\b|\bx-strobe\b|\bstrobe\b"
     r"|\bmagnum.*strobe\b|\bshocker\b", "strobe", ""),
    (r"\bmolefay\b|\bmole\b|\bblinder\b|\baudience blinder\b|\bdwe\b", "blinder", ""),
    (r"\bsource\s*four\b|\bsource 4\b|\bs4\b.*(lustr|led|jr)|\bellipsoidal\b"
     r"|\bleko\b|\bprofile\b(?!.*moving)|\bzoomspot\b", "profile", ""),
    (r"\bfresnel\b|\bpebble\b|\bpc\b spot|\bf2\b|\bparnel\b", "fresnel", ""),
    (r"\bax[1-9]\b|\btitan tube\b|\bhelios\b|\bhyperion\b|\bpixel tube\b|\btube\b",
     "tube", ""),
    (r"\bsunstrip\b|\bstrip\b|\bbatten\b|\bbar\b|\bcolorband\b|\bcolour ?band\b"
     r"|\bpixelline\b|\bpixel ?line\b|\bonyx bar\b|\bled bar\b", "bar", ""),
    (r"\bmatrix\b|\bpanel\b.*\bpixel|\bdot\s*\d+|\bsmartbat\b", "matrix", ""),
    (r"\bcyc\b|\bflood\b|\bcyclorama\b|\bhorizon\b", "cyc", ""),
    (r"\bpanel\b|\bskypanel\b|\bs60\b|\bs30\b", "wash_panel", ""),
    (r"\blaser\b|\bclubmax\b|\bpangolin\b", "laser", ""),
    (r"\bhazer\b|\bhaze\b|\bfog\b|\bfogger\b|\bsmoke\b|\bunique\b|\btiny\b",
     "atmos", ""),
    (r"\bfollow\s*spot\b|\bfollowspot\b|\blycian\b|\bsuper trouper\b", "followspot", ""),
    (r"\bpar\s*(64|56|46|36|30|20|16)\b(?!.*led)", "par_can", ""),
    (r"\bpar\b|\bslimpar\b|\bslim par\b|\bcolorado\b|\bparty\s*par\b|\bflat\s*par\b",
     "par", ""),
    (r"\bhybrid\b", "moving_hybrid", ""),
    (r"\bbeam\b", "moving_beam", ""),
    (r"\bwash\b", "moving_wash", ""),
    (r"\bspot\b|\bprofile\b|\bmac\b|\bintimidator\b|\brogue\b|\bmaverick\b"
     r"|\brobin\b|\besprite\b|\bt1\b|\bbmfl\b|\bpointe\b", "moving_spot", ""),
]

_MOVING_NAMES = (r"\brobin\b|\bmac\b|\bbmfl\b|\bintimidator\b|\brogue\b"
                 r"|\bmaverick\b|\bsharpy\b|\besprite\b|\bforte\b|\bpointe\b"
                 r"|\bmega[- ]?pointe\b|\bscenius\b|\bkhamsin\b|\bdiablo\b"
                 r"|\bmoving\b|\bmover\b|\bvl\d|\bimpression\b|\bb-?eye\b")

_MOVING_FALLBACK = {"moving_bar": "moving_bar", "bar": "moving_bar",
                    "par": "moving_wash", "wash_panel": "moving_wash",
                    "profile": "moving_spot", "fresnel": "moving_wash",
                    "strobe": "moving_hybrid", "matrix": "moving_wash"}


def brand_of(manufacturer: str) -> str:
    """The brand key for a manufacturer name (`generic` when unknown)."""
    text = " " + re.sub(r"[^a-z0-9&\- ]", " ", str(manufacturer or "").lower()) + " "
    best, best_len = "generic", 0
    for key, row in BRANDS.items():
        for alias in row["aliases"]:
            if alias and f" {alias} " in text and len(alias) > best_len:
                best, best_len = key, len(alias)
    return best


def _cells(roles: list[str]) -> int:
    """How many independently coloured cells the mode drives."""
    reds = sum(1 for r in roles if r == "red")
    dims = sum(1 for r in roles if r == "dimmer")
    return max(1, reds, dims if reds == 0 else 1)


@lru_cache(maxsize=512)
def _describe(manufacturer: str, model: str, mode: str,
              roles: tuple[str, ...], channels: int) -> dict:
    name = f"{manufacturer} {model} {mode}".lower()
    has = set(roles)
    moving = "pan" in has and "tilt" in has
    kind = ""
    for pattern, typ, _family in FAMILIES:
        if re.search(pattern, name):
            kind = typ
            break
    if not roles:
        # Nothing known about the channels yet (a GDTF Share listing): the
        # name is all there is, so trust it - and product families that are
        # always moving heads override a word like "Profile" in the name.
        kind = kind or "generic"
        if re.search(_MOVING_NAMES, name) and not TYPES[kind]["moving"]:
            kind = _MOVING_FALLBACK.get(kind, "moving_spot")
    elif moving:
        if kind and not TYPES[kind]["moving"]:
            kind = _MOVING_FALLBACK.get(kind, "")
        if not kind:
            if "gobo" in has or "iris" in has or "prism" in has:
                kind = "moving_spot"
            elif "zoom" in has:
                kind = "moving_wash"
            else:
                kind = "moving_spot"
    elif not kind or TYPES[kind]["moving"]:
        cells = _cells(list(roles))
        if cells >= 4:
            kind = "bar"
        elif has <= {"dimmer", "raw", "unused"} and channels <= 2:
            kind = "par_can"
        elif ("strobe" in has or "shutter" in has) and not (
                has & {"red", "green", "blue", "white"}):
            kind = "strobe"
        elif has & {"red", "green", "blue", "white", "amber", "uv", "cyan"}:
            kind = "par"
        else:
            kind = "generic"
    t = TYPES[kind]
    brand = brand_of(manufacturer)
    b = BRANDS[brand]
    cells = _cells(list(roles))
    zoom = "zoom" in has
    lo, hi = t["beam"]
    return {
        "type": kind,
        "label": t["label"],
        "moving": t["moving"],
        "brand": brand,
        "brand_name": b["name"] if brand != "generic" else (manufacturer or "Generic"),
        "style": {"body": b["body"], "accent": b["accent"],
                  "finish": b["finish"]},
        "cells": cells,
        # A fixed-lens fixture keeps one angle; a zoom channel sweeps it.
        "beam": {"min": lo, "max": hi if zoom else lo},
        "features": sorted(r for r in has & {
            "gobo", "gobo_rot", "prism", "iris", "frost", "zoom", "focus",
            "strobe", "shutter", "wheel"}),
    }


def describe(head: dict) -> dict:
    """The physical description of one patched head (cached per type)."""
    roles = tuple(str(r) for r in (head.get("map") or []))
    return dict(_describe(str(head.get("manufacturer") or ""),
                          str(head.get("model") or ""),
                          str(head.get("mode") or ""),
                          roles, int(head.get("channels") or len(roles))))


# ---------------------------------------------------------------------------
# Auto-placement: where a newly added head goes when the operator does not
# say.  Everything used to land on the same spot (0, 0.3, 0), so a freshly
# patched rig was a pile of lights in one place.
# ---------------------------------------------------------------------------
# (height m, z as a fraction of stage depth) per type.  z = 0 is upstage.
_ZONES = {
    "moving_spot": (6.0, 0.25), "moving_wash": (6.0, 0.55),
    "moving_beam": (6.0, 0.25), "moving_hybrid": (6.0, 0.25),
    "moving_bar": (6.0, 0.55), "profile": (6.5, 1.0), "fresnel": (6.0, 0.8),
    "followspot": (3.0, 1.6), "par_can": (6.0, 0.8), "par": (0.2, 0.06),
    "bar": (0.15, 0.03), "cyc": (0.2, 0.02), "wash_panel": (6.0, 0.8),
    "strobe": (6.0, 0.55), "blinder": (6.0, 0.9), "tube": (0.8, 0.12),
    "matrix": (6.0, 0.4), "laser": (0.4, 0.1), "atmos": (0.3, 0.02),
    "generic": (6.0, 0.55),
}


def place(kind: str, count: int, existing: list[dict],
          width: float = 10.0, depth: float = 8.0) -> list[dict]:
    """Positions for `count` new heads of `kind`: one row per zone, filled
    outwards from the centre, never on top of a head already there.  A full
    row spills onto a parallel row (a second truss) rather than stacking."""
    height, zf = _ZONES.get(kind, _ZONES["generic"])
    base_z = round(depth * zf, 2)
    spacing = 1.2 if height > 2 else 1.0
    if kind in ("bar", "moving_bar"):
        spacing = 1.4
    half = max(1.0, width / 2.0 - 0.5) + 2 * spacing
    mount = "truss" if height >= 2.0 else "floor"
    out: list[dict] = []
    placed: list[tuple[float, float]] = []
    for row in range(12):
        if len(out) >= count:
            break
        z = round(base_z + (0.9 if height > 2 else 0.6) * ((row + 1) // 2)
                  * (1 if row % 2 else -1), 2) if row else base_z
        taken = [float(h.get("x", 0.0)) for h in existing
                 if abs(float(h.get("y", 0.0)) - height) < 0.6
                 and abs(float(h.get("z", 0.0)) - z) < 0.45]
        taken += [x for x, zz in placed if abs(zz - z) < 0.45]
        k = 0
        while len(out) < count:
            step = (k + 1) // 2
            x = step * spacing * (1 if k % 2 else -1) if k else 0.0
            k += 1
            if abs(x) > half:
                break
            if any(abs(x - t) < spacing * 0.6 for t in taken):
                continue
            taken.append(x)
            placed.append((x, z))
            out.append({"x": round(x, 2), "y": height, "z": z, "kind": mount})
    while len(out) < count:                     # an absurdly full stage
        out.append({"x": 0.0, "y": height, "z": base_z, "kind": mount})
    return out
