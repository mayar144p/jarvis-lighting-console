"""Shared DMX vocabulary: roles, 16-bit maths, curves, geometry.

Split out of engine.py so the hot path (app/merge.py) can use the same
constants and helpers without importing the console itself - a merge
must never drag in the fixture database, the HTTP layer or the output
thread.  engine.py re-exports everything here, so `from app.engine import
HTP_ROLES` keeps working for existing callers and tests.
"""
from __future__ import annotations

import re

__all__ = [
    "SLOTS", "ROLES", "HTP_ROLES", "COLOUR_ROLES", "BEAM_ROLES",
    "FX_ROLES", "LASER_ROLES", "SFX_ROLES",
    "ATTRIBUTE_ALIAS", "ROLE_HEX", "channel_role", "is_fine_role",
    "split_16bit", "join_16bit", "logical16", "curve_pct",
    "pos", "pos_to_ua",
]

# 512 slots per universe, on every transport
SLOTS = 512

# --- channel roles -------------------------------------------------------
# Closed vocabulary; anything the label matcher cannot place becomes "raw"
# and shows a warning badge in the patch view.
ROLES = ("dimmer", "zone_dimmer", "red", "green", "blue", "white", "amber",
         "uv", "cyan", "magenta", "yellow", "pan", "pan_fine", "tilt",
         "tilt_fine", "speed", "shutter", "strobe", "gobo", "gobo_rot",
         "prism", "zoom", "focus", "frost", "iris", "wheel", "macro",
         # special effects and lasers: never driven by a light action
         "fx_fire", "fx_arm", "fx_fan", "fog", "fx_height", "fx_mode",
         "fx_param", "laser_on", "laser_pattern", "laser_size", "laser_rot",
         "laser_x", "laser_y", "laser_speed", "laser_colour",
         # a beam bar's diodes, one channel each; and the further settings
         # channels an effect has (a laser's strobe, a machine's timer...)
         *(f"laser_beam{i}" for i in range(1, 17)),
         *(f"fx_param{i}" for i in range(2, 7)),
         # a light's channels no named role fits (a Wave 360's continuous
         # pan rotation, its built-in tilt programs...): each its own control
         *(f"aux{i}" for i in range(1, 25)),
         "unused", "raw")

# Brightness roles: they combine by HTP (highest wins) and are 0-100.
HTP_ROLES = frozenset({"dimmer", "zone_dimmer"})

# Colour roles: on a fixture with no dimmer these ARE the brightness.
COLOUR_ROLES = frozenset({"red", "green", "blue", "white", "amber", "uv",
                          "cyan", "magenta", "yellow"})

# Special-effect and laser roles.  A flash, a strobe, Full, Locate, an
# effect, the auto show and the copilot never touch these: an SFX machine
# fires only from its own armed buttons, and a laser's output likewise.
SFX_ROLES = frozenset({"fx_fire", "fx_arm", "fx_fan", "fog", "fx_height",
                       "fx_mode", "fx_param", *(f"fx_param{i}" for i in range(2, 7))})
# A beam bar's diodes: programmable (which beams, recorded in cues) but, like
# a laser's power, dark unless the laser output is on from its armed buttons.
LASER_BEAM_ROLES = frozenset(f"laser_beam{i}" for i in range(1, 17))
LASER_ROLES = frozenset({"laser_on", "laser_pattern", "laser_size",
                         "laser_rot", "laser_x", "laser_y", "laser_speed",
                         "laser_colour"}) | LASER_BEAM_ROLES
FX_ROLES = SFX_ROLES | LASER_ROLES
AUX_ROLES = frozenset(f"aux{i}" for i in range(1, 25))
_AUX_RE = re.compile(r"^aux\s*(\d{1,2})\b")

# Beam-shape roles, used for the beam palette and the 3D body shapes.
BEAM_ROLES = frozenset({"shutter", "strobe", "gobo", "gobo_rot", "prism",
                        "zoom", "focus", "frost", "iris"})

_LABEL_ROLE = {
    "dimmer": "dimmer", "intensity": "dimmer", "master dimmer": "dimmer",
    "red": "red", "green": "green", "blue": "blue", "white": "white",
    "amber": "amber", "uv": "uv", "cyan": "cyan", "magenta": "magenta",
    "yellow": "yellow", "pan": "pan", "pan fine": "pan_fine",
    "tilt": "tilt", "tilt fine": "tilt_fine", "speed": "speed",
    "pan/tilt speed": "speed", "shutter": "shutter", "strobe": "strobe",
    "gobo 1": "gobo", "gobo 2": "gobo", "gobo 3": "gobo", "gobo 4": "gobo",
    "gobo 1 rotate": "gobo_rot", "gobo rotate": "gobo_rot",
    "zoom": "zoom", "focus": "focus", "frost": "frost", "iris": "iris",
    "colour wheel": "wheel", "color wheel": "wheel", "wheel": "wheel",
    "unused": "unused", "empty": "unused",
    # the effects vocabulary (labels written by the FX classifier)
    "fx fire": "fx_fire", "fx arm": "fx_arm", "fx fan": "fx_fan",
    "fog output": "fog", "fx height": "fx_height", "fx mode": "fx_mode",
    "fx setting": "fx_param", "laser output": "laser_on",
    "laser pattern": "laser_pattern", "laser size": "laser_size",
    "laser rotation": "laser_rot", "laser x": "laser_x", "laser y": "laser_y",
    "laser speed": "laser_speed", "laser colour": "laser_colour",
    **{f"laser beam {i}": f"laser_beam{i}" for i in range(1, 17)},
    **{f"fx setting {i}": f"fx_param{i}" for i in range(2, 7)},
    # Vendor FUNCTION names, which is what a GDTF import actually stores:
    # parse_gdtf prefers ChannelFunction/@OriginalAttribute over the
    # generic LogicalChannel/@Attribute, because the vendor's own name is
    # what the operator reads on the fixture's display.  The cost is that
    # those names are not the generic vocabulary, so a real Chauvet
    # Intimidator arrived with "Color1", "PositionMSpeed" and "NoFeature"
    # - all three of which mapped to `raw`, i.e. a channel that carries
    # DMX with no control behind it.  Only unambiguous ones are listed;
    # anything genuinely unknown stays `raw` on purpose, because that is
    # the state the channel sheet exists to surface.
    "nofeature": "unused", "not used": "unused", "notused": "unused",
    "positionmspeed": "speed", "pantilspeed": "speed",
    "dimmer1mspeed": "speed", "mspeed": "speed",
    "color1": "wheel", "color wheel1": "wheel",
    "go1wheelrotation": "gobo_rot", "gowheelrotation": "gobo_rot",
}
# "Colour1"/"Color 1" and similar - a trailing index on a wheel name.
_WHEEL_INDEX_RE = re.compile(r"^(?:colou?r)\s*(\d)$")
_ZONE_RE = re.compile(r"zone\s*(\d+)\s*(dimmer|red|green|blue|white|amber|uv)",
                      re.IGNORECASE)

# Attribute aliases: friendly/legacy names -> engine roles.
ATTRIBUTE_ALIAS: dict[str, str] = {r.replace("_", ""): r for r in ROLES}
ATTRIBUTE_ALIAS.update({
    "intensity": "dimmer", "brightness": "dimmer", "master": "dimmer",
    "r": "red", "g": "green", "b": "blue",
    "colour": "wheel", "color": "wheel", "colourwheel": "wheel",
    "colorwheel": "wheel", "colourindex": "wheel",
    "gobo1": "gobo", "gobo2": "gobo", "gobo3": "gobo", "gobo4": "gobo",
    "goborotate": "gobo_rot", "gobospin": "gobo_rot",
    "pantilt": "speed", "pantiltspeed": "speed",
    "strobeRate": "strobe", "rate": "strobe",
    "frost1": "frost", "pan16": "pan", "tilt16": "tilt",
})

# Fallback beam colours when a head has no (touched) colour channels.
ROLE_HEX = {
    "wash": "#8b5cf6", "beam": "#3b82f6", "spot": "#f59e0b", "par": "#22c55e",
    "bar": "#ef4444", "strobe": "#eab308", "hazer": "#94a3b8",
    "follow": "#f8fafc", "generic": "#cbd5e1",
}


def channel_role(label) -> str:
    """Fixture channel label -> engine role (see ROLES).

    Understands every label style our sources produce: plain names
    (Dimmer), wheel/zone names (Colour Wheel, Zone 2 Red), GDTF 16-bit
    markers ("Pan (16-bit)" = coarse half of a pair, "Pan fine" = the
    fine half) and vendor spellings.  A fine half becomes "<base>_fine"
    so merge.build_frames can pair it with its base channel and split ONE
    logical value across both DMX bytes (16-bit parameters).
    """
    s = str(label or "").strip().lower()
    if not s:
        return "raw"
    s = s.replace("(16-bit)", "").replace("(16 bit)", "").strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return "unused"                     # label was only a width marker
    if s in _LABEL_ROLE:
        return _LABEL_ROLE[s]
    aux = _AUX_RE.match(s)                  # "Aux 3 · Continuous Pan Rotating"
    if aux and 1 <= int(aux.group(1)) <= 24:
        return f"aux{int(aux.group(1))}"
    zone = _ZONE_RE.search(s)
    if zone:
        kind = zone.group(2).lower()
        return "zone_dimmer" if kind == "dimmer" else kind
    # "Colour 1" / "Colour2" - a colour WHEEL with an index, not an RGB
    # triplet.  Matched before the dictionary only for the spaced form;
    # the unspaced "Color1" is in the table, because "Color1" is
    # ambiguous in a way "Color 1" is not.
    wheel = _WHEEL_INDEX_RE.match(s)
    if wheel:
        return "wheel"
    if s.startswith("unused") or s.startswith("(") or s == "-":
        return "unused"
    # Fine half of a 16-bit pair: map to "<base>_fine".  Recursion is
    # bounded because the suffix is stripped first.
    if s.endswith(" fine") or s.endswith("_fine"):
        base = s[:-5].strip().strip("_")
        if base:
            role = channel_role(base)
            if role != "raw" and not role.endswith("_fine"):
                return f"{role}_fine"
        return "raw"
    # Maintenance channels carry no look: leave them alone on the wire
    # so they stay at their NoFunction default (0).  "Dimmer Speed" and
    # "Dimmer Curve" are maintenance too - they must NOT read as dimmer
    # or intensity would write the curve/speed channel as well.
    if ("function" in s or "maintenance" in s or "reset" in s
            or (s.startswith("dimmer") and ("speed" in s or "curve" in s))):
        return "unused"
    if "dimmer" in s or "intensity" in s:
        return "dimmer"
    if "shutter" in s:
        return "shutter"
    if "strobe" in s:
        return "strobe"
    if "gobo" in s:
        # GDTF "Gobo1Pos" / "Gobo1PosRotate" is the gobo's index/spin, not
        # a second gobo wheel (it took the wheel's role and hid it)
        return "gobo_rot" if re.search(r"rotat|spin|pos|index|indx", s) \
            else "gobo"
    if "prism" in s:
        return "prism"
    if "speed" in s and ("pan" in s or "tilt" in s):
        return "speed"
    if "zoom" in s:
        return "zoom"
    if "focus" in s:
        return "focus"
    if "frost" in s:
        return "frost"
    if "iris" in s:
        return "iris"
    if "macro" in s:
        return "macro"
    if ("colour" in s or "color" in s) and ("wheel" in s or "index" in s):
        return "wheel"
    for word in ("red", "green", "blue", "white", "amber", "uv",
                 "cyan", "magenta", "yellow"):
        if word in s:
            return word
    return "raw"


def is_fine_role(role) -> bool:
    """True for the fine half of a 16-bit pair ("pan_fine", ...)."""
    return (isinstance(role, str) and role.endswith("_fine")
            and role[:-5] in ROLES)


def split_16bit(value, fine_first: bool = False) -> tuple[int, int]:
    """Logical 0..65535 -> (coarse, fine) DMX bytes.

    Integer-only, so there is no rounding error to cause visible
    stepping: the value is simply cut in half.  `fine_first` swaps the
    pair for fixtures whose definition declares LSB-before-MSB ordering.
    Values outside 0..65535 clamp to the ends (0 and 65535 round trip
    exactly as (0,0) and (255,255)).
    """
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {value!r}") from None
    v = 0 if v < 0 else (65535 if v > 65535 else v)
    coarse, fine = (v >> 8) & 0xFF, v & 0xFF
    return (fine, coarse) if fine_first else (coarse, fine)


def join_16bit(coarse: int, fine: int, fine_first: bool = False) -> int:
    """Inverse of split_16bit: two DMX bytes -> logical 0..65535."""
    c, f = int(coarse) & 0xFF, int(fine) & 0xFF
    if fine_first:
        c, f = f, c
    return (c << 8) | f


def logical16(value) -> int:
    """Programmer value -> 16-bit logical: 0-255 keeps its 8-bit meaning
    (scaled to full scale so coarse stays byte-identical), 256..65535 is
    already a 16-bit parameter value."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if v <= 255:
        return 0 if v < 0 else v * 257          # 255 -> 65535 exactly
    return 65535 if v > 65535 else v


def pos(universe: int, address: int) -> int:
    """1-based (universe, address) -> flat channel index (0-based)."""
    return (int(universe) - 1) * SLOTS + (int(address) - 1)


def pos_to_ua(pos: int) -> tuple[int, int]:
    return pos // SLOTS + 1, pos % SLOTS + 1


def curve_pct(value: int, curve: str) -> int:
    """Dimmer curve over a 0-100 value, returns 0-100."""
    v = max(0, min(100, int(value))) / 100.0
    if curve == "scurve":
        v = v * v * (3 - 2 * v)
    elif curve == "square":
        v = v * v
    return int(round(v * 100))
