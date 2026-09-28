"""Show design studio: interview, alternative concepts, cue lists.

Jarvis collects a short brief (structure, mood, colours, pace, event) and
this module deterministically turns it into 2-3 *different* show concepts -
each with its own palette, cue list and fade times - plus the stage data
the browser needs for the live preview (truss, fixtures, beams).

Nothing here touches the rig: concepts are preview-only until
program_show() records one, after the user explicitly confirmed.

Colour note: a concept's colours are written per role as plain #rrggbb and
the engine maps them onto whatever the head actually has - RGB channels,
CMY slots, or a white channel. Fixtures that only have a colour wheel get
the open slot, so a cue still reads as "white" rather than black.
"""
from __future__ import annotations

from . import engine as engine_mod
from . import fixture_kind

# ---------------------------------------------------------------------------
# interview
# ---------------------------------------------------------------------------

# (key, question) - Jarvis asks the ones whose answer is still missing.
QUESTIONS = [
    ("structure", "What structure will they hang/stand on - goalpost truss, "
                  "full box truss, proscenium theatre, ground-supported stands, "
                  "or open floor?"),
    ("mood", "What's the event and the mood/genre (e.g. wedding ballad, rock "
             "gig, techno club, corporate gala)?"),
    ("colours", "Which colours do you love - and any that are forbidden?"),
    ("pace", "What pace - slow & elegant, medium, fast & punchy, or mixed "
             "across the show?"),
]

STRUCTURES = {
    "goalpost": "goalpost truss (uprights + top bar)",
    "box": "full box truss (rectangular rig)",
    "proscenium": "proscenium theatre arch",
    "ground": "ground-supported stands / towers",
    "open": "open floor - no rig",
}
# a creative hint the console's concept renderer understands
STYLE_HINT = {
    "goalpost": "truss row",
    "box": "truss row symmetric",
    "proscenium": "truss row",
    "ground": "floor",
    "open": "floor",
}

# ---------------------------------------------------------------------------
# colour helpers
# ---------------------------------------------------------------------------

COLOR_NAMES: list[tuple[str, str]] = [
    ("red", "#ff3b30"), ("orange", "#ff8a2a"), ("amber", "#ffb020"),
    ("gold", "#ffd44d"), ("yellow", "#ffe94d"), ("lime", "#a8ff3c"),
    ("green", "#2ee66b"), ("teal", "#25d0b8"), ("cyan", "#3ad6ff"),
    ("sky", "#7fc8ff"), ("blue", "#3f7dff"), ("navy", "#2a3fb8"),
    ("purple", "#8b5cf6"), ("violet", "#a78bfa"), ("magenta", "#ff4ddb"),
    ("pink", "#ff7ac0"), ("rose", "#ff5f8a"), ("cream", "#ffe9c9"),
    ("warm white", "#ffd9a8"), ("white", "#f4f7ff"), ("cold white", "#dce9ff"),
]
_COLOR_HEX = dict(COLOR_NAMES)


def _to_hex(value: str) -> str | None:
    """'#f00' / '#ff0000' / colour name -> '#rrggbb', else None."""
    v = str(value or "").strip().lower()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
            return "#" + h
        return None
    return _COLOR_HEX.get(v)


def _name_of(hexcode: str) -> str:
    """Nearest known colour name for a hex (exact matches win)."""
    for name, h in COLOR_NAMES:
        if h.lower() == hexcode.lower():
            return name
    r, g, b = _hex_rgb(hexcode)

    def dist(h: str) -> int:
        hr, hg, hb = _hex_rgb(h)
        return (r - hr) ** 2 + (g - hg) ** 2 + (b - hb) ** 2

    return min(COLOR_NAMES, key=lambda pair: dist(pair[1]))[0]


def _hex_rgb(hexcode: str) -> tuple[int, int, int]:
    h = str(hexcode).lstrip("#")     # a #rrggbb or #rgb, coerced the same way
                                     # the engine's _parse_hex does it
    if len(h) != 6:
        return (244, 247, 255)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return (244, 247, 255)


# ---------------------------------------------------------------------------
# mood profiles
# ---------------------------------------------------------------------------

MOODS: list[tuple[tuple[str, ...], dict]] = [
    (("romantic", "wedding", "ballad", "love", "church", "ceremony",
      "intimate", "soft"), {
        "key": "romantic",
        "palette": ["#ff5f8a", "#c084fc", "#ffd9a8", "#ffb0c9"],
    }),
    (("rock", "metal", "punk", "gig", "band", "heavy", "grunge", "stadium"), {
        "key": "rock",
        "palette": ["#ff3b30", "#f4f7ff", "#8b5cf6", "#ff8a2a"],
    }),
    (("edm", "techno", "electronic", "club", "dj", "festival", "rave",
      "party", "dance", "disco"), {
        "key": "electronic",
        "palette": ["#ff4ddb", "#3ad6ff", "#a8ff3c", "#8b5cf6"],
    }),
    (("warm", "sunset", "amber", "autumn", "acoustic", "jazz", "cosy",
      "cozy", "wine", "harvest"), {
        "key": "warm",
        "palette": ["#ffb020", "#ff8a2a", "#ff5f8a", "#ffe9c9"],
    }),
    (("cool", "ice", "winter", "clean", "fresh", "tech", "corporate",
      "gala", "conference", "launch", "award", "elegant", "minimal"), {
        "key": "cool",
        "palette": ["#3f7dff", "#3ad6ff", "#f4f7ff", "#8b5cf6"],
    }),
]
_DEFAULT_PROFILE = {"key": "balanced",
                    "palette": ["#3f7dff", "#ffb020", "#ff4ddb", "#f4f7ff"]}

PACE_MULT = {"slow": 1.7, "medium": 1.0, "fast": 0.55, "mixed": 1.0}

# ---------------------------------------------------------------------------
# concept strategies - the three *alternatives*
# ---------------------------------------------------------------------------

STRATEGIES = [
    {"key": "skyline", "suffix": "Skyline",
     "tagline": "slow colour washes - the whole rig breathes as one"},
    {"key": "pulse", "suffix": "Pulse",
     "tagline": "high-contrast stabs and blackouts, hits on the beat"},
    {"key": "gallery", "suffix": "Gallery",
     "tagline": "one colour family with a single accent pop"},
]

WASHY = ("wash", "par", "bar", "generic")
BEAMY = ("beam", "spot")
ROLE_ORDER = ("wash", "par", "bar", "spot", "beam", "generic")

# (name, fade_base s, hold s, spec, energy) - spec: group -> (slot, level)
_TEMPLATES: dict[str, list] = {
    "skyline": [
        ("Open",   1.8, 14, {"all": (0, 35)}, "calm"),
        ("Intro",  2.6, 16, {"all": (0, 65)}, "calm"),
        ("Build",  1.6, 12, {"washy": (1, 85), "beamy": (2, 85)}, "rising"),
        ("Peak",   1.0, 14, {"washy": (0, 100), "beamy": (3, 100)}, "peak"),
        ("Outro",  3.2, 16, {"all": (0, 30)}, "calm"),
    ],
    "pulse": [
        ("Black",      0.35, 3, {"all": (0, 8)}, "dark"),
        ("Stab",       0.15, 2.5, {"beamy": (3, 100), "washy": (0, 12)}, "hit"),
        ("Colour hit", 0.20, 2.5, {"all": (1, 95)}, "hit"),
        ("White-out",  0.12, 2, {"all": (3, 100)}, "hit"),
        ("Groove",     0.45, 4, {"washy": (2, 70), "beamy": (0, 85)}, "groove"),
        ("Tail",       0.90, 5, {"all": (0, 45)}, "settle"),
    ],
    "gallery": [
        ("Open",   2.0, 12, {"all": (3, 40)}, "calm"),
        ("Family", 1.5, 14, {"all": (0, 80)}, "calm"),
        ("Deep",   1.5, 14, {"all": (1, 85)}, "deep"),
        ("Accent", 0.8, 10, {"all": (0, 70), "beamy": (2, 100)}, "accent"),
        ("Lift",   1.2, 12, {"all": (2, 90)}, "rise"),
        ("Close",  3.0, 16, {"all": (0, 35)}, "calm"),
    ],
}


# ---------------------------------------------------------------------------
# stage data
# ---------------------------------------------------------------------------

def _stage_from_patch(patch: list[dict], structure: str) -> dict | None:
    """The stage IS the rig, when a rig is patched.

    `design()` used to read a layout session, which only the rig studio
    could produce - so with the studio gone there would have been no
    session, and show-from-a-prompt would have laid its concepts out on a
    SYNTHETIC 10-head rig while a real one sat patched and ignored.

    The console always has the real thing: `patch` is the rig, with the
    positions the operator hung it at, the roles the profiles resolved to
    and the names they gave the heads.  A concept drawn on that is a
    concept they can actually use, and it is strictly more faithful than
    the synthetic fallback - which now only runs for a desk with an empty
    patch, which is the one case where there is nothing better to draw on.

    Returns None for an empty or unusable patch so the caller falls back.
    """
    rows = []
    for h in patch or []:
        try:
            x = float(h.get("x"))
            y = float(h.get("y"))
        except (TypeError, ValueError):
            continue                      # no position: nothing to draw on
        rows.append({
            "head_no": h.get("head_no"),
            "name": h.get("name") or f"Head {h.get('head_no', '?')}",
            # The role the operator gave the head, so a wash is presented
            # as a wash rather than as a generic fixture.
            "role": fixture_kind.design_role(h),
            "kind": h.get("kind") or ("truss" if y >= 2.0 else "floor"),
            "x": round(x, 2), "y": round(y, 2),
            "z": round(float(h.get("z") or 0.0), 2),
        })
    if not rows:
        return None
    xs = [f["x"] for f in rows]
    ys = [f["y"] for f in rows]
    # The venue box, from the extent of what is actually there - with a
    # little margin so a head on the boundary is not on the frame.
    return {
        "width": round(max(4.0, (max(xs) - min(xs)) + 4.0), 2),
        "depth": round(max(3.0, (max(ys) - min(ys)) + 4.0), 2),
        "structure": structure, "fixtures": rows, "from_rig": True,
    }


def _synthetic_stage(structure: str) -> dict:
    """Fallback rig when no layout variant has been chosen yet."""
    width, depth = 12.0, 8.0
    fixtures = []

    def spread(n, kind, z, y):
        for i in range(n):
            # centred x (-w/2 .. +w/2), matching the visualiser
            x = round(width * (-0.38 + 0.76 * (i / max(1, n - 1))), 2)
            fixtures.append({
                "head_no": len(fixtures) + 1,
                "name": f"{kind.title()} {i + 1}",
                "role": kind if kind in ("wash", "beam", "spot") else "generic",
                "kind": "truss" if y > 1 else "floor",
                "x": x, "y": y, "z": z,
            })

    spread(4, "wash", round(depth * 0.20, 2), 4.2)
    spread(3, "beam", round(depth * 0.78, 2), 0.30)
    spread(3, "spot", round(depth * 0.20, 2), 4.2)
    return {"width": width, "depth": depth, "structure": structure,
            "fixtures": fixtures}


# ---------------------------------------------------------------------------
# concepts
# ---------------------------------------------------------------------------
def _expand(spec: dict, roles: list[str], washy: list[str],
            beamy: list[str], slots: list[str]) -> tuple[list, dict, dict]:
    groups = {"all": roles, "washy": washy, "beamy": beamy}
    colours, intensity, active = {}, {}, []
    for role in roles:
        idx, level = 0, 0
        for g, (slot, lvl) in spec.items():
            if role in groups.get(g, []):
                idx, level = slot, lvl
        intensity[role] = int(level)
        colours[role] = slots[idx] if level > 0 else slots[0]
        if level > 0:
            active.append(role)
    return active, colours, intensity


def _cues(strategy: str, roles: list[str], slots: list[str],
          pace: str) -> list[dict]:
    mult = PACE_MULT.get(pace, 1.0)
    washy = [r for r in roles if r in WASHY] or list(roles)
    beamy = [r for r in roles if r in BEAMY] or list(roles)
    out = []
    for n, (name, fade, hold, spec, energy) in enumerate(
            _TEMPLATES[strategy], start=1):
        active, colours, intensity = _expand(spec, roles, washy, beamy, slots)
        out.append({
            "n": n, "name": name, "energy": energy,
            "fade_s": round(max(0.1, fade * mult), 2),
            "hold_s": hold,
            "active": active, "colours": colours, "intensity": intensity,
        })
    return out


def _concept(i: int, roles: list[str], palette: list[dict],
             pace: str, structure: str, stage: dict) -> dict:
    strat = STRATEGIES[i % len(STRATEGIES)]
    # rotate the palette so each concept starts from a different base colour
    slots = [palette[j % len(palette)]["hex"] for j in range(i, i + 3)]
    slots.append("#f4f7ff")                      # slot 3 = white (stabs)
    return {
        "id": i,
        "name": f"{_name_of(slots[0]).title()} {strat['suffix']}",
        "tagline": strat["tagline"],
        "strategy": strat["key"],
        "structure": structure,
        "palette": [{"name": p["name"], "hex": p["hex"]} for p in palette],
        "cues": _cues(strat["key"], roles, slots, pace),
        "stage": stage,
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

_last: dict | None = None


def clear() -> None:
    global _last
    _last = None


def get_concept(index: int) -> dict | None:
    if not _last:
        return None
    concepts = _last.get("concepts") or []
    if 0 <= int(index) < len(concepts):
        return concepts[int(index)]
    return None


def design(brief: dict | None = None, variant_index: int = 0) -> dict:
    """Turn interview answers into 2-3 alternative show concepts."""
    global _last
    brief = brief if isinstance(brief, dict) else {}
    mood = str(brief.get("mood") or "").strip()
    event = str(brief.get("event") or "").strip()
    pace = str(brief.get("pace") or "").strip().lower()
    structure = str(brief.get("structure") or "").strip().lower()
    avoid = str(brief.get("avoid") or "").strip()

    if pace not in PACE_MULT:
        pace = "medium"
    if structure not in STRUCTURES:
        structure = "goalpost"

    # colours: names or hexes, recognised ones only (in order)
    given_colors = [str(c) for c in (brief.get("colours") or [])
                    if isinstance(brief.get("colours"), list)]
    hexes, unknown = [], []
    for raw in given_colors:
        h = _to_hex(raw)
        if h and h not in hexes:
            hexes.append(h)
        elif not h:
            unknown.append(raw)

    # mood profile
    haystack = f"{mood} {event} {avoid}".lower()
    profile = _DEFAULT_PROFILE
    for keys, prof in MOODS:
        if any(k in haystack for k in keys):
            profile = prof
            break

    palette_hexes = list(hexes)
    for h in profile["palette"]:
        if len(palette_hexes) >= 4:
            break
        if h not in palette_hexes:
            palette_hexes.append(h)
    palette = [{"name": _name_of(h), "hex": h} for h in palette_hexes]

    # stage: the rig that is actually patched, or a synthetic one only when
    # there is no rig to draw on.  See `_stage_from_patch`.
    stage = _stage_from_patch(engine_mod.ENGINE.patch if engine_mod.ENGINE
                              else [], structure)
    if stage is None:
        stage = _synthetic_stage(structure)
    roles = []
    for f in stage["fixtures"]:
        if f["role"] not in roles:
            roles.append(f["role"])
    roles = sorted(roles, key=lambda r: ROLE_ORDER.index(r)
                   if r in ROLE_ORDER else 99)

    concepts = [_concept(i, roles, palette, pace, structure, stage)
                for i in range(len(STRATEGIES))]

    answered = {
        "structure": bool(brief.get("structure")),
        "mood": bool(mood or event),
        "colours": bool(hexes),
        "pace": bool(brief.get("pace")),
    }
    questions = [q for key, q in QUESTIONS if not answered[key]]

    assumptions = []
    if not mood and not event:
        assumptions.append("mood defaulted to a balanced multicolour look")
    if not hexes:
        assumptions.append(f"palette taken from the '{profile['key']}' mood profile")
    if unknown:
        assumptions.append("colour(s) not recognised and ignored: "
                           + ", ".join(unknown))
    if not brief.get("pace"):
        assumptions.append("pace medium")
    if not brief.get("structure"):
        assumptions.append("structure goalpost")
    if stage.get("fixtures") and not stage.get("from_rig"):
        assumptions.append("no rig is patched - the preview stage is a "
                           "synthetic one, not your fixture positions")
    elif stage.get("from_rig"):
        assumptions.append("concepts are drawn on the %d head(s) you have "
                           "patched, at their real positions"
                           % len(stage["fixtures"]))

    result = {
        "ok": True,
        "brief": {"mood": mood, "event": event, "colours": palette_hexes,
                  "pace": pace, "structure": structure, "avoid": avoid},
        "style_hint": STYLE_HINT.get(structure, "truss row"),
        "questions": questions,
        "assumptions": assumptions,
        "concepts": concepts,
        # No layout variants any more - the rig studio went with the
        # assistant page - so there is never a variant to index into.  The
        # key stays, at -1, because the console's own panel reads it.
        "variant_index": -1,
    }
    _last = {"concepts": concepts, "variant_index": -1}
    return result
