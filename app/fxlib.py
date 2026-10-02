"""Named effects, and which fixture can do them.

WHAT THIS IS FOR
================

The existing FX are an LFO: one wave form on ONE attribute, with speed,
phase, spread, base and depth.  That is the right primitive and it stays -
every effect here is built on `fx.wave`, so the maths still runs in the two
places it always did (the DMX resolve and the visualiser feed) and the
beams and the bytes cannot drift.

What was missing is the thing an operator actually reaches for: a NAME.
"Rainbow", "Circle", "Alternate", "Fan" are effects; "sine on tilt at
1.2 Hz with 90 degrees of spread" is a description of one.  So this is a
library of named effects, each a small pure function over the same wave
engine.

CAPABILITY FILTERING - THE PART THAT MATTERS
============================================

`available()` is the whole point of putting this in a module rather than in
the UI: **an effect is offered only if the fixture can do it.**  A PAR with
no pan channel is never offered "Circle", and a moving head with no colour
is never offered "Rainbow" - not greyed out, not offered-and-failing,
*absent*.

Three things make that honest rather than decorative:

  * `needs` is a tuple of ALTERNATIVE groups, and every group must be
    satisfiable.  A group is a frozenset of roles that do the same job.
    "Brightness" is `{"dimmer", "zone_dimmer"}` or any colour role,
    because on a fixture with no dimmer the colour channels ARE the
    brightness - a Chauvet Intimidator Spot 260 in its 8ch mode has
    pan/tilt/wheel/gobo/prism/focus/shutter and no dimmer at all, and a
    filter that required `dimmer` would offer that fixture nothing.

  * a fine channel satisfies its coarse role.  `pan_fine` is `pan`.  The
    rig has heads with `tilt` + `tilt_fine` and heads with `dimmer` +
    `dimmer_fine`, and demanding the coarse name would exclude half of them.

  * the filter is a PURE function of a role set, so the engine, the UI and
    the tests all get the same answer from the same call.  A UI that
    filtered its own list would be a second source of truth, which is how
    "it offered me Circle and then nothing happened" happens.

EVERYTHING IS PURE
==================

No state, no I/O, no engine import.  An effect is
`(row, roles, params, elapsed, index, count) -> {role: value}`, so the same
call serves the wire and the picture, and a test can call it at t=0 and
t=2.5 without a rig.
"""
from __future__ import annotations

import functools

from .fx import wave

# --- capability groups -----------------------------------------------------
# Alternatives, not requirements: every entry in one of these tuples does
# the same job, so satisfying any one of them is enough.
BRIGHTNESS = ("dimmer", "zone_dimmer")
RGB = ("red", "green", "blue")
PAN = ("pan",)
TILT = ("tilt",)

# Fine channels stand in for their coarse role everywhere.
_FINE = {"pan_fine": "pan", "tilt_fine": "tilt", "dimmer_fine": "dimmer"}

# A small, readable working set.  Values are the programmer's 0..100 unless
# the role is 0..255, which `apply` handles per role.
_COLOURS: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("red", (255, 0, 0)), ("green", (0, 255, 0)), ("blue", (0, 0, 255)),
    ("amber", (255, 140, 0)), ("white", (255, 255, 255)),
    ("magenta", (255, 0, 255)), ("cyan", (0, 255, 255)),
    ("yellow", (255, 255, 0)), ("uv", (180, 0, 255)),
)
_RGB_OF = {**dict(_COLOURS), "lime": (168, 255, 60)}
_COLOUR_ORDER = ("red", "green", "blue", "white", "amber",
                 "magenta", "cyan", "yellow", "uv")
_EMITTERS = ("white", "amber", "uv", "lime")
_CMY = ("cyan", "magenta", "yellow")


def _palette(have) -> list[str]:
    return list(_palette_of(frozenset(have)))


@functools.lru_cache(maxsize=256)
def _palette_of(have: frozenset) -> tuple:
    """The colours a light can really MAKE, in chase order.  RGB lights:
    their own emitters (as before).  A CMY light mixes every colour - by
    subtracting.  A light with only white / amber / UV channels has just
    those.  Picking from the channel NAMES alone made a CMY light "chase"
    magenta / cyan / yellow while nothing wrote those channels."""
    if any(r in have for r in RGB):
        return tuple(c for c in _COLOUR_ORDER if c in have)
    if any(r in have for r in _CMY):
        return ("red", "green", "blue", "magenta", "cyan", "yellow")
    return tuple(c for c in _EMITTERS if c in have)



def normalise(roles) -> set[str]:
    """A role set with fine channels folded into their coarse role.

    `unused` and `raw` are dropped: a fixture with only `raw` channels can
    do nothing an effect can drive, and offering it everything is the same
    lie as offering it nothing.
    """
    return set(_normalised(tuple(str(r) for r in roles or ())))


@functools.lru_cache(maxsize=1024)
def _normalised(roles: tuple) -> frozenset:
    out: set[str] = set()
    for r in roles:
        r = str(r).lower()
        if r in ("unused", "raw"):
            continue
        out.add(_FINE.get(r, r))
    return frozenset(out)


def _satisfies(have: set[str], group) -> bool:
    return any(r in have for r in group)


def _bright(have: set[str]) -> bool:
    """Can this fixture be dimmed at all?

    A dimmer channel, OR a colour channel, OR a shutter.

    The colour case is because on a fixture with no dimmer the colour
    channels ARE the brightness.  The shutter case is the one that was
    missed first time: a Chauvet Intimidator Spot 260 in its 8ch mode has
    pan/tilt/wheel/gobo/prism/focus/shutter and NO dimmer, and its shutter
    is the dimmer - inverted, 0 open.  Without this, "only offer what the
    light can do" meant "offer that fixture nothing", which is the filter
    being technically right and practically useless.
    """
    return (_satisfies(have, BRIGHTNESS)
            or _satisfies(have, ("shutter",))
            or bool(have & {"red", "green", "blue", "white", "amber", "uv"}))



# --- the effects -----------------------------------------------------------
# Each is a pure function.  Signature:
#   (base, have, p, elapsed, index, count) -> None, mutating `base`.


def _rainbow(base, have, p, elapsed, index, count):
    """Hue sweep.  Needs full colour, so a white-only fixture is skipped."""
    order = _palette(have)
    if len(order) < 2:
        return
    t = elapsed * p["speed"] + p["phase"] * 0.01 * index
    # spread: the heads that far apart round the colour wheel (360 = one
    # whole rainbow across them) - "rainbow across the rig"
    t += p["spread"] / 360.0 * (index / max(1, count)) * len(order) * max(p["rate"], 0.01)
    pos = (t / max(p["rate"], 0.01)) % len(order)
    a = order[int(pos) % len(order)]
    b = order[(int(pos) + 1) % len(order)]
    f = pos - int(pos)
    ca, cb = _RGB_OF[a], _RGB_OF[b]
    for i, role in enumerate(("red", "green", "blue")):
        if role in have:
            base[role] = int(ca[i] + (cb[i] - ca[i]) * f)


def _colour_chase(base, have, p, elapsed, index, count):
    """Hard steps through the colour wheel, one colour per head."""
    order = _palette(have)
    if len(order) < 2:
        return
    step = int((elapsed * p["speed"] * p["rate"]) + index) % len(order)
    _write_colour(base, have, order[step], 100.0)


def _alternate(base, have, p, elapsed, index, count):
    """Two colours, alternating by head and flipping over time."""
    order = _palette(have)
    if len(order) < 2:
        return
    pair = [order[0], order[1 % len(order)]]
    flip = int(elapsed * p["speed"] * p["rate"]) % 2
    pick = pair[(index % 2 + flip) % 2]
    _write_colour(base, have, pick, 100.0)


def _fan(base, have, p, elapsed, index, count):
    """Two colours, split across the selection, flipping slowly.

    The split alone is static in TIME, which reads as a dead effect: a head
    parked on red for the whole cue.  So the split is the shape and the
    flip is the movement, and each side alternates independently - which is
    what "fan" does on a desk.
    """
    order = _palette(have)
    if len(order) < 2:
        return
    mid = (count - 1) / 2.0
    dist = abs(index - mid) / max(1.0, mid)
    flip = int(elapsed * p["speed"] * p["rate"]) % 2
    pair = [order[0], order[1 % len(order)]]
    side = 0 if dist < 0.5 else 1
    _write_colour(base, have, pair[side ^ flip], 100.0)


def _write_colour(base, have, name, level):
    """One named colour on whatever the light mixes with: RGB(W/A/UV),
    CMY (subtractive) or single emitters (white / amber / UV on or off)."""
    vals = dict(_COLOURS)[name]
    k = level / 100.0
    rgb = any(r in have for r in RGB)
    for i, role in enumerate(("red", "green", "blue")):
        if role in have:
            base[role] = int(vals[i] * k)
    for i, role in enumerate(_CMY):
        if role in have and not rgb:
            base[role] = int((255 - vals[i]) * k) if level else 255
    for role in _EMITTERS:
        if role in have:
            # its own colour lights it; on an RGB light the white / amber /
            # UV of another colour stays off so each step reads clearly
            base[role] = int(255 * k) if role == name else 0


def _breathe(base, have, p, elapsed, index, count):
    """Slow sine on brightness, phased across the selection."""
    off = p["spread"] * (index / max(1, count)) / 360.0
    v = wave("sine", elapsed * p["speed"] + off)
    lvl = p["low"] + (p["high"] - p["low"]) * v
    _apply_brightness(base, have, lvl)


def _pulse(base, have, p, elapsed, index, count):
    """Hard on/off on the beat.  Needs a real dimmer or colour."""
    off = p["spread"] * (index / max(1, count)) / 360.0
    v = 1.0 if wave("square", elapsed * p["speed"] + off) > 0.5 else 0.0
    _apply_brightness(base, have, p["low"] + (p["high"] - p["low"]) * v)


def _dimmer_chase(base, have, p, elapsed, index, count):
    """Stepped levels, one per head, marching forward."""
    steps = max(2, int(p["high"] - p["low"]) or 2)
    pos = int(elapsed * p["speed"] * p["rate"]) * count + index
    v = (pos % (steps * count)) // count
    _apply_brightness(base, have, p["low"] + (p["high"] - p["low"]) * v / steps)


def _apply_brightness(base, have, pct):
    """Write brightness, choosing the channel that is actually there."""
    if _satisfies(have, BRIGHTNESS):
        role = "dimmer" if "dimmer" in have else "zone_dimmer"
        base[role] = int(max(0.0, min(100.0, pct)) * 2.55)
        return
    # No dimmer channel: the colour channels ARE the brightness, so all of
    # them scale together.  This is the path the Intimidator's 8ch mode
    # takes, and without it "only offer what the light can do" would mean
    # "offer it nothing".
    lit = [r for r in ("red", "green", "blue", "white", "amber", "uv") if r in have]
    for role in lit:
        base[role] = int(max(0.0, min(100.0, pct)) * 2.55)
    if not lit and "shutter" in have:
        # only a shutter: it is open or shut, and only the engine knows
        # which value opens THIS light - it turns "_level" into that
        base["_level"] = int(max(0.0, min(100.0, pct)) * 2.55)


def _w(base, role, unit):
    """Write one role at 0..255 from a 0..1 wave value.

    EVERY role write goes through here, and that is not tidiness.  The
    first version of the position and beam effects called
    `fx_value(kind, cycles, 0.5, 1.0)`, which returns 0..1, and assigned it
    straight into a row the wire reads as 0..255 - so Pan sweep, Circle,
    Gobo spin, Prism fan and Zoom pulse were all writing 0 or 1 out of 255,
    i.e. doing nothing at all.  Sweeping time at them produced one or two
    distinct values and the effects looked broken rather than wrong.

    An effect that cannot be seen is not a subtle bug; it is a missing
    feature wearing a working one's name.
    """
    base[role] = int(max(0.0, min(1.0, unit)) * 255)


def _pan_sweep(base, have, p, elapsed, index, count):
    """Pan left and right, phased across the selection."""
    if not _satisfies(have, PAN):
        return
    off = p["spread"] * (index / max(1, count)) / 360.0
    _w(base, "pan", wave("sine", elapsed * p["speed"] + off))


def _tilt_bounce(base, have, p, elapsed, index, count):
    if not _satisfies(have, TILT):
        return
    off = p["spread"] * (index / max(1, count)) / 360.0
    _w(base, "tilt", wave("triangle", elapsed * p["speed"] + off))


def _circle(base, have, p, elapsed, index, count):
    """Pan and tilt together, tracing an ellipse.  Needs BOTH."""
    if not (_satisfies(have, PAN) and _satisfies(have, TILT)):
        return
    t = elapsed * p["speed"]
    _w(base, "pan", wave("sine", t + 0.25 * index))
    _w(base, "tilt", wave("sine", t))


def _figure_eight(base, have, p, elapsed, index, count):
    """The lissajous, so a head crosses itself.  Needs BOTH."""
    if not (_satisfies(have, PAN) and _satisfies(have, TILT)):
        return
    t = elapsed * p["speed"]
    _w(base, "pan", wave("sine", t + 0.25 * index))
    _w(base, "tilt", wave("sine", 2.0 * t))


def _fan_pan(base, have, p, elapsed, index, count):
    """Pans fan out from the centre of the selection, in unison.

    Two things were wrong here and both made the effect look broken:

      * the head's distance from the centre SCALED the whole value, so the
        centre head - the one an operator is most likely watching - sat
        still for the whole effect;
      * the centre was computed as `(count - 1) // 2`, which for four heads
        is 1, so index 1 is the centre with a distance of exactly zero and
        the other three all get 1.  A fan needs the MIDPOINT, 1.5.

    So: distance is the amplitude, the midpoint is a float, and a selection
    of one is treated as full amplitude rather than as a head that cannot
    move.
    """
    if not _satisfies(have, PAN):
        return
    if count <= 1:
        dist = 1.0
    else:
        mid = (count - 1) / 2.0
        raw = abs(index - mid) / max(1.0, mid)        # 0 at the exact middle
        # The middle head of an ODD selection has raw == 0 exactly, and
        # with distance as the whole amplitude it sat still for the whole
        # effect - the one head an operator is most likely watching.  A
        # floor of 0.35 keeps it moving at a third of the ends' travel,
        # which is what "fan" looks like: the middle is the stillest point,
        # not a dead one.
        dist = 0.35 + 0.65 * raw
    w = 0.5 + 0.5 * wave("sine", elapsed * p["speed"])        # 0..1
    v = 0.5 + (w - 0.5) * dist
    base["pan"] = int(max(0.0, min(1.0, v)) * 255)


def _gobo_spin(base, have, p, elapsed, index, count):
    if "gobo_rot" not in have:
        return
    off = p["spread"] * (index / max(1, count)) / 360.0
    _w(base, "gobo_rot", wave("saw", elapsed * p["speed"] + off))


def _prism_fan(base, have, p, elapsed, index, count):
    if "prism" not in have:
        return
    off = p["spread"] * (index / max(1, count)) / 360.0
    _w(base, "prism", wave("sine", elapsed * p["speed"] + off))


def _zoom_pulse(base, have, p, elapsed, index, count):
    if "zoom" not in have:
        return
    off = p["spread"] * (index / max(1, count)) / 360.0
    _w(base, "zoom", wave("sine", elapsed * p["speed"] + off))


def _strobe_random(base, have, p, elapsed, index, count):
    """Random per-head flashes, uncorrelated.

    The threshold is `1 - density`, so density is literally the fraction of
    cycles that fire.  The first version used a hardcoded 0.55, which
    sounds reasonable and is not: `wave("random", ...)` re-rolls once per
    CYCLE, so over a two-second sweep at 8 Hz a 0.55 cut fires on a
    predictable minority of samples and the effect looks intermittent
    rather than random.  Making it a named knob is also what lets an
    operator turn it down for a chase and up for a storm.
    """
    if "strobe" not in have:
        return
    if wave("random", elapsed * p["speed"] * p["rate"], index) > (
            1.0 - max(0.02, min(1.0, p["density"]))):
        base["strobe"] = 255


def _shutter_flicker(base, have, p, elapsed, index, count):
    """Shutters are inverted on many fixtures, so this drives the SHUTTER
    role only where one exists and says nothing about which way is open -
    that is the fixture's business, and guessing it here would strobe a
    fixture the wrong way round."""
    if "shutter" not in have:
        return
    if wave("random", elapsed * p["speed"] * p["rate"], index) > (
            1.0 - max(0.02, min(1.0, p["density"]))):
        base["shutter"] = 255


def _sparks(base, have, p, elapsed, index, count):
    """Random brightness spikes, per head, uncorrelated.

    `depth` is the fraction of cycles that spike.  The first version
    compared against `1.0 - 0.15 * depth`, which with the default depth of
    0.15 is a cut at 0.978 - a random draw essentially never reaches it, so
    Sparks sat dark for the whole cue and looked like a broken effect
    rather than a quiet one.
    """
    v = wave("random", elapsed * p["speed"] * p["rate"], index)
    frac = max(0.02, min(1.0, p["depth"]))
    _apply_brightness(base, have, p["high"] if v > (1.0 - frac) else p["low"])


# --- the table -------------------------------------------------------------
# `needs` is a tuple of ALTERNATIVE groups; every group must be satisfiable.
# `params` are the knobs: (key, label, default, min, max).
# `bright` marks an effect that needs no dimmer because colour can carry it.

_SPARK = (("speed", "rate", 1.0, 0.01, 20.0), ("rate", "hits", 8.0, 1.0, 40.0),
          ("spread", "spread", 90.0, 0.0, 720.0), ("phase", "phase", 0.0, 0.0, 100.0),
          ("depth", "spark", 0.15, 0.0, 1.0),
          ("low", "low", 0.0, 0.0, 100.0), ("high", "high", 100.0, 0.0, 100.0))
_SPREAD = (("speed", "rate", 1.0, 0.01, 20.0), ("spread", "spread", 90.0, 0.0, 720.0),
           ("phase", "phase", 0.0, 0.0, 100.0))
# Movement (app/motion.py): a shape of `size` degrees around where the head
# is aimed, fitted into its limits.  `speed` is turns per second - the
# default 0.125 is one turn in 8 s, which real motors can follow; `arc` <
# 360 makes a circle go out and back over that arc (a 180-degree turn);
# `direction` 1 / -1 = clockwise / counter-clockwise; `lock` 1 keeps the
# tilt where it is (pan only), 2 keeps the pan (tilt only).
_MOVE = (("speed", "speed (turns/s)", 0.125, 0.005, 2.0), ("size", "size (deg)", 20.0, 1.0, 270.0),
         ("arc", "arc (deg)", 360.0, 10.0, 360.0), ("direction", "direction", 1.0, -1.0, 1.0),
         ("lock", "lock", 0.0, 0.0, 2.0), ("spread", "spread", 0.0, 0.0, 720.0),
         ("phase", "phase", 0.0, 0.0, 100.0))
_LEVELS = (("speed", "rate", 1.0, 0.01, 20.0), ("rate", "rate", 4.0, 1.0, 40.0),
           ("spread", "spread", 0.0, 0.0, 720.0),
           ("phase", "phase", 0.0, 0.0, 100.0),
           ("low", "low", 0.0, 0.0, 100.0), ("high", "high", 100.0, 0.0, 100.0))
# The strobe effects read a `rate` the wave helpers do not, and putting it
# in _SPREAD would show a dead knob on Pan sweep and Gobo spin.  A knob an
# effect ignores is worse than no knob: it invites an operator to change
# something that cannot change.
_STROBE = (("speed", "rate", 1.0, 0.01, 20.0), ("rate", "hits", 8.0, 1.0, 40.0),
           ("spread", "spread", 0.0, 0.0, 720.0),
           ("phase", "phase", 0.0, 0.0, 100.0),
           ("density", "density", 0.25, 0.02, 1.0))
_COLOURP = (("speed", "rate", 1.0, 0.01, 20.0), ("rate", "rate", 2.0, 1.0, 40.0),
            ("spread", "spread", 0.0, 0.0, 720.0),
            ("phase", "phase", 0.0, 0.0, 100.0))

FX: dict[str, dict] = {
    # --- colour -----------------------------------------------------------
    "rainbow": {"label": "Rainbow", "group": "colour", "needs": (RGB,),
                "params": _COLOURP, "fn": _rainbow},
    "colour_chase": {"label": "Colour chase", "group": "colour",
                     "needs": (("red", "green", "blue", "white", "amber", "uv",
                                "cyan", "magenta", "yellow"),),
                     "params": _COLOURP, "fn": _colour_chase},
    "alternate": {"label": "Alternate", "group": "colour",
                  "needs": (("red", "green", "blue", "white", "amber", "uv",
                             "cyan", "magenta", "yellow"),),
                  "params": _COLOURP, "fn": _alternate},
    "fan": {"label": "Fan", "group": "colour",
            "needs": (("red", "green", "blue", "white", "amber", "uv",
                       "cyan", "magenta", "yellow"),),
            "params": _COLOURP, "fn": _fan},
    # --- brightness -------------------------------------------------------
    "breathe": {"label": "Breathe", "group": "dimmer", "bright": True,
                "needs": (), "params": _LEVELS, "fn": _breathe},
    "pulse": {"label": "Pulse", "group": "dimmer", "bright": True,
              "needs": (), "params": _LEVELS, "fn": _pulse},
    "dimmer_chase": {"label": "Dimmer chase", "group": "dimmer", "bright": True,
                     "needs": (), "params": _LEVELS, "fn": _dimmer_chase},
    "sparks": {"label": "Sparks", "group": "dimmer", "bright": True,
               "needs": (), "params": _SPARK, "fn": _sparks},
    # --- position ---------------------------------------------------------
    "pan_sweep": {"label": "Pan sweep", "group": "position", "needs": (PAN,),
                  "params": _MOVE, "fn": _pan_sweep},
    "tilt_bounce": {"label": "Tilt bounce", "group": "position",
                    "needs": (TILT,), "params": _MOVE, "fn": _tilt_bounce},
    "fan_pan": {"label": "Fan pan", "group": "position", "needs": (PAN,),
                "params": _MOVE, "fn": _fan_pan},
    "circle": {"label": "Circle", "group": "position", "needs": (PAN, TILT),
               "params": _MOVE, "fn": _circle},
    "figure_eight": {"label": "Figure eight", "group": "position",
                     "needs": (PAN, TILT), "params": _MOVE,
                     "fn": _figure_eight},
    # --- beam -------------------------------------------------------------
    "gobo_spin": {"label": "Gobo spin", "group": "beam",
                  "needs": (("gobo_rot",),), "params": _SPREAD,
                  "fn": _gobo_spin},
    "prism_fan": {"label": "Prism fan", "group": "beam",
                  "needs": (("prism",),), "params": _SPREAD,
                  "fn": _prism_fan},
    "zoom_pulse": {"label": "Zoom pulse", "group": "beam",
                   "needs": (("zoom",),), "params": _SPREAD,
                   "fn": _zoom_pulse},
    "strobe_random": {"label": "Random strobe", "group": "beam",
                      "needs": (("strobe",),), "params": _STROBE,
                      "fn": _strobe_random},
    "shutter_flicker": {"label": "Shutter flicker", "group": "beam",
                        "needs": (("shutter",),), "params": _STROBE,
                        "fn": _shutter_flicker},
}

GROUPS = ("dimmer", "colour", "position", "beam")


def _groups(spec) -> tuple:
    """`spec["needs"]` as groups, whatever shape it was written in.

    A single-role need written as a bare string - `needs=("gobo_rot",)` -
    is the shape a Python programmer naturally reaches for, and it is a
    silent trap: `_satisfies` then iterates the STRING character by
    character, so "gobo_rot" is tested as g/o/b/o/_/r/o/t, none of which is
    a role, and the effect is offered to nobody.  Every beam effect in this
    table was unavailable to every fixture on this rig because of it, and
    the filter reported a clean "no" rather than an error.

    Normalising here makes the mistake impossible instead of fixing one
    instance of it, and a test asserts both spellings agree.
    """
    needs = spec.get("needs") or ()
    return tuple((g,) if isinstance(g, str) else tuple(g) for g in needs)


def available(roles) -> list[str]:
    """The effect names a fixture with these roles can actually do.

    A PURE function of a role set, and the single answer used by the engine,
    the UI and the tests.  A UI that filtered its own list would be a second
    source of truth, which is how "it offered me Circle and then nothing
    happened" happens.
    """
    return list(_available_for(frozenset(normalise(roles))))


@functools.lru_cache(maxsize=512)
def _available_for(have: frozenset) -> tuple[str, ...]:
    # every effect on every head asks this 40 times a second, and a rig has
    # a handful of distinct role sets: answer each set once
    out: list[str] = []
    for name in sorted(FX):
        spec = FX[name]
        if spec.get("bright"):
            if not _bright(have):
                continue
        elif spec.get("group") == "colour":
            # two colours it can really make (a light with only a red
            # channel can't run a rainbow)
            if len(_palette(have)) < 2 or name == "rainbow" and sum(r in have for r in RGB) < 2:
                continue
        else:
            if not all(_satisfies(have, g) for g in _groups(spec)):
                continue
        out.append(name)
    return tuple(out)


def describe(roles) -> list[dict]:
    """`available` with the labels and knobs, for a picker."""
    out = []
    for name in available(roles):
        spec = FX[name]
        out.append({
            "name": name, "label": spec["label"], "group": spec["group"],
            "params": [{"key": k, "label": lbl, "default": d,
                        "min": lo, "max": hi}
                       for (k, lbl, d, lo, hi) in spec["params"]],
        })
    return out


def why_not(roles, name: str) -> str:
    """Why an effect is not available - for a diagnostic, never for the UI.

    The picker simply omits an unavailable effect; this exists so that
    "why is there no Circle here" is answerable without guessing.
    """
    if name not in FX:
        return "no such effect"
    if name in available(roles):
        return ""
    have = normalise(roles)
    spec = FX[name]
    if spec.get("bright") and not _bright(have):
        return "this fixture has no dimmer and no colour to dim with"
    missing = [sorted(g)[0] for g in spec["needs"]
               if not _satisfies(have, g)]
    return "needs " + ", ".join(missing) if missing else "not available"


def defaults(name: str) -> dict:
    return dict(_defaults(name))


@functools.lru_cache(maxsize=None)
def _defaults(name: str) -> tuple:
    return tuple((k, d) for (k, _l, d, _lo, _hi) in FX[name]["params"])


def apply(name: str, base: dict, roles, params: dict | None = None,
          elapsed: float = 0.0, index: int = 0, count: int = 1) -> dict:
    """Run one effect over a base row.  Pure; mutates and returns `base`."""
    spec = FX.get(name)
    if not spec:
        raise ValueError("unknown effect %r" % (name,))
    have = normalise(roles)
    if name not in _available_for(frozenset(have)):
        raise ValueError("%s: %s" % (name, why_not(roles, name)))
    p = defaults(name)
    p.update({k: float(v) for k, v in (params or {}).items()
              if k in p and v is not None})
    spec["fn"](base, have, p, float(elapsed), int(index), max(1, int(count)))
    return base
