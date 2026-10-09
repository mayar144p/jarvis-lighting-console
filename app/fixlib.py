"""Open fixture libraries: the Open Fixture Library and QLC+.

Two free, community-maintained collections cover most of the lights a
club actually hangs - including the budget DJ fixtures GDTF Share does
not carry (a Chauvet Intimidator Wave 360, say).  This module reads both
formats into exactly the shape `fixtures.parse_gdtf` returns, so they are
stored, patched, pan/tilt-ranged, shutter-opened and wheel-buttoned by
the same code as a GDTF file:

    [{"manufacturer", "model", "type",
      "modes": [{"name", "channel_count", "channels": [label, ...],
                 "detail": [{role, label, bits, coarse, phys_from,
                             phys_to, open_from, slots, ...}, ...]}]}]

Both libraries also ship with Jarvis, compressed, under app/fixlib/
(built by tools/build_fixture_libraries.py), so they can be searched and
installed offline at a venue.  Licences: OFL is MIT, QLC+ is Apache 2.0;
see app/fixlib/NOTICE.md.

    Open Fixture Library  https://open-fixture-library.org  (.json)
    QLC+ fixtures         https://www.qlcplus.org           (.qxf)
"""
from __future__ import annotations

import json
import re
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .engine_support import channel_role

BUNDLE_DIR = Path(__file__).resolve().parent / "fixlib"
SOURCES = {
    # Jarvis's own profiles, written from the manufacturers' manuals, for
    # what no open library carries yet (confetti launchers first).
    "jarvis": {"name": "Built-in library", "file": "jarvis.json",
               "licence": "part of this desk", "url": "app/fixlib/jarvis.json"},
    "ofl": {"name": "Open Fixture Library", "file": "ofl.zip",
            "licence": "MIT", "url": "https://open-fixture-library.org"},
    "qlc": {"name": "QLC+ fixture library", "file": "qlcplus.zip",
            "licence": "Apache-2.0", "url": "https://www.qlcplus.org"},
}

_COLOURS = {"red": "Red", "green": "Green", "blue": "Blue", "white": "White",
            "amber": "Amber", "uv": "UV", "cyan": "Cyan", "magenta": "Magenta",
            "yellow": "Yellow", "warm white": "White", "cold white": "White",
            "lime": "Lime", "indigo": "Indigo"}

# Words that mark a wheel capability as an effect, not a slot to stop on.
_NOT_A_SLOT = re.compile(
    r"no function|stop|rotat|rainbow|fade|snap|sound|speed|shake|random|"
    r"auto|scroll|spin|\bcw\b|\bccw\b|clockwise|reset|music", re.I)
_OPEN_TEXT = re.compile(r"^\s*(shutter\s+)?(open|on|light on|lamp on)\b", re.I)


def _slot_rows(found: list[tuple[int, int, dict]]) -> list[dict] | None:
    out, seen = [], set()
    for lo, hi, row in sorted(found, key=lambda t: t[0]):
        key = (row.get("slot"), row["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({**row, "from": lo, "to": hi, "value": (lo + hi) // 2})
    return out or None


def _detail(label: str, **extra) -> dict:
    row = {"role": channel_role(label), "attribute": extra.pop("attribute", ""),
           "label": label, "bits": 8, "dmx_from": None, "dmx_to": None,
           "phys_from": None, "phys_to": None, "wheel": None,
           "open_from": None, "slots": None, "coarse": True,
           "strobe_ranges": None, "fast_first": None, "name": label,
           "caps": None}
    row.update(extra)
    return row


def _centred(span: float | None) -> tuple[float | None, float | None]:
    if not span:
        return None, None
    return -abs(span) / 2.0, abs(span) / 2.0


def _orphan_fine(rows: list[dict]) -> list[dict]:
    """A "fine" channel with no coarse one in the mode is the coarse one
    (files tag a lone gobo / dimmer / shutter as the 2nd byte): a gobo wheel
    read as "Gobo fine" never moved.  When several would take the same
    place, the one whose name says it wins; the others stay the light's own."""
    present = {r.get("role") for r in rows}
    want: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        role = str(r.get("role") or "")
        if role.endswith("_fine") and role[:-5] not in present:
            want.setdefault(role[:-5], []).append(i)
    for base, idx in want.items():
        word = base.split("_")[0]
        named = [i for i in idx if word in str(rows[i].get("name") or "").lower()]
        pick = named[0] if named else (idx[0] if len(idx) == 1 else None)
        for i in idx:
            r = rows[i]
            own = str(r.get("name") or r.get("label") or "")
            if i == pick:
                label = str(r.get("label") or "")
                label = label[:-5] if label.lower().endswith(" fine") else label
                r.update({"label": label, "role": channel_role(label), "bits": 8})
            else:
                r.update({"label": own, "role": "raw"})
    return rows


def _subtractive_names(rows: list[dict]) -> list[dict]:
    """Vari-Lite names a lamp's colour-mixing flags Blue / Amber / Magenta
    (VL2402, VL3000): with magenta there and no red, green or cyan, those are
    the subtractive cyan and yellow, not LEDs - read as LEDs, red couldn't be
    made and Locate came out magenta instead of white."""
    roles = {r.get("role") for r in rows}
    if "magenta" in roles and "blue" in roles and not roles & {"red", "green", "cyan"}:
        swap = {"blue": "cyan", "amber": "yellow"}
        for r in rows:
            if r.get("role") in swap:
                r["role"] = swap[r["role"]]
                r["label"] = r["role"].capitalize()     # the desk reads the role back from the label
    return rows


def _mode(name: str, rows: list[dict]) -> dict:
    rows = _subtractive_names(_orphan_fine(rows))
    for n, row in enumerate(rows, start=1):
        row["n"] = n
    return {"name": name or "Default", "channel_count": len(rows),
            "channels": [r["label"] for r in rows], "detail": rows}


# ---------------------------------------------------------------------------
# Open Fixture Library (.json)
# ---------------------------------------------------------------------------
def _ofl_caps(cdef: dict) -> list[dict]:
    caps = cdef.get("capabilities")
    if caps:
        return [c for c in caps if isinstance(c, dict)]
    cap = cdef.get("capability")
    return [dict(cap, dmxRange=[0, 255])] if isinstance(cap, dict) else []


def _ofl_angle(text) -> float | None:
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*deg", str(text or ""))
    return float(m.group(1)) if m else None


def _ofl_main_type(caps: list[dict]) -> str:
    """The capability type that covers most of the channel's range."""
    cover: dict[str, int] = {}
    for c in caps:
        if c.get("type") == "NoFunction":
            continue
        lo, hi = (c.get("dmxRange") or [0, 255])[:2]
        cover[c["type"]] = cover.get(c["type"], 0) + int(hi) - int(lo) + 1
    return max(cover, key=cover.get) if cover else "NoFunction"


def _ofl_wheel_kind(wheels: dict, wheel: str) -> str:
    slots = (wheels.get(wheel) or {}).get("slots") or []
    types = {s.get("type") for s in slots}
    if types & {"Gobo", "AnimationGoboStart"}:
        return "gobo"
    if "Color" in types:
        return "colour"
    return "other"


def _ofl_label(name: str, cdef: dict, wheels: dict) -> str:
    caps = _ofl_caps(cdef)
    kind = _ofl_main_type(caps)
    if any(c.get("type") == "WheelSlot" for c in caps):
        kind = "WheelSlot"                  # slots win over spin/shake ranges
    low = name.lower()
    if kind in ("Pan", "Tilt"):
        return kind
    if kind == "Intensity":
        return "Dimmer"
    if kind == "ColorIntensity":
        colour = str(next((c.get("color") for c in caps if c.get("color")), "")).lower()
        return _COLOURS.get(colour, name)
    if kind == "ShutterStrobe":
        return "Strobe" if "strobe" in low else "Shutter"
    if kind == "PanTiltSpeed":
        return "Pan/Tilt Speed"
    if kind in ("Zoom", "Focus", "Iris", "Frost", "Prism"):
        return kind
    if kind == "ColorPreset":
        return "Color Macro"
    if kind in ("WheelSlot", "WheelShake"):
        wheel = next((c.get("wheel") for c in caps if c.get("wheel")), name)
        what = _ofl_wheel_kind(wheels, wheel)
        if what == "colour":
            return "Color Wheel"
        if what == "gobo":
            return "Gobo 2" if re.search(r"\b2\b", wheel) else "Gobo Wheel"
        return name
    if kind in ("WheelSlotRotation", "WheelRotation"):
        wheel = next((c.get("wheel") for c in caps if c.get("wheel")), name)
        return "Gobo Rotate" if _ofl_wheel_kind(wheels, wheel) == "gobo" else "Wheel Spin"
    if kind == "PrismRotation":
        return "Facet Rotation"
    # Maintenance, effects, programmes: never let a name like "Dimmer
    # Mode" turn a settings channel into the dimmer.
    if channel_role(name) in ("raw", "unused", "macro"):
        return name
    return "Function " + name


def _ofl_detail(name: str, cdef: dict, wheels: dict, bits: int) -> dict:
    label = _ofl_label(name, cdef, wheels)
    caps = _ofl_caps(cdef)
    fine = cdef.get("fineChannelAliases") or []
    res = str(cdef.get("dmxValueResolution") or "")
    shift = (8 * len(fine)) if not res and fine else (8 if res == "16bit" else 16 if res == "24bit" else 0)

    def byte(v):
        return max(0, min(255, int(v) >> shift))
    row = _detail(label, attribute=_ofl_main_type(caps), bits=bits, name=name)
    row["caps"] = [[byte(c["dmxRange"][0]), byte(c["dmxRange"][1]),
                    str(c.get("comment") or c.get("effectName") or c.get("shutterEffect")
                        or c.get("color") or c.get("type") or "")[:48]]
                   for c in caps if c.get("dmxRange")] or None
    if label in ("Pan", "Tilt"):
        c = caps[0] if caps else {}
        a, b = _ofl_angle(c.get("angleStart")), _ofl_angle(c.get("angleEnd"))
        if a is not None and b is not None:
            row["phys_from"], row["phys_to"] = _centred(b - a)
            if b < a:
                row["phys_from"], row["phys_to"] = row["phys_to"], row["phys_from"]
    opens = [byte(c["dmxRange"][0]) for c in caps
             if c.get("type") == "ShutterStrobe" and c.get("shutterEffect") == "Open"
             and c.get("dmxRange")]
    if opens:
        row["open_from"] = min(opens)
    strobes = [[byte(c["dmxRange"][0]), byte(c["dmxRange"][1])] for c in caps
               if c.get("type") == "ShutterStrobe" and c.get("dmxRange")
               and c.get("shutterEffect") not in ("Open", "Closed", None)]
    row["strobe_ranges"] = strobes or None
    for c in caps:
        if c.get("type") == "PanTiltSpeed":
            first = str(c.get("speedStart") or c.get("duration") or "").lower()
            if "fast" in first:
                row["fast_first"] = True
            elif "slow" in first:
                row["fast_first"] = False
            break
    found = []
    for c in caps:
        if c.get("type") != "WheelSlot" or not c.get("dmxRange"):
            continue
        num = c.get("slotNumber")
        if not isinstance(num, int):
            continue                        # a split between two slots
        wheel = c.get("wheel") or name
        slots = (wheels.get(wheel) or {}).get("slots") or []
        sdef = slots[num - 1] if 1 <= num <= len(slots) else {}
        colours = sdef.get("colors") or []
        stype = sdef.get("type") or ""
        sname = sdef.get("name") or (stype if stype in ("Open", "Closed") else "") \
            or (colours[0] if colours else f"Slot {num}")
        hexcol = colours[0] if colours else ("#ffffff" if stype == "Open" else None)
        lo, hi = c["dmxRange"][:2]
        slot = {"name": sname, "hex": hexcol, "slot": num}
        res = sdef.get("resource")
        if stype == "Gobo" and isinstance(res, str) and res.startswith("gobos/"):
            slot["img"] = "ofl:" + res[6:]          # the gobo's picture (OFL resources)
        found.append((byte(lo), byte(hi), slot))
    row["slots"] = _slot_rows(found)
    return row


def _ofl_pixel_keys(matrix: dict) -> list[str]:
    if not isinstance(matrix, dict):
        return []
    if matrix.get("pixelKeys"):
        keys = []
        for plane in matrix["pixelKeys"]:
            for row in plane:
                keys.extend(k for k in row if k)
        return keys
    x, y, z = (list(matrix.get("pixelCount") or [1, 1, 1]) + [1, 1, 1])[:3]
    if y == 1 and z == 1:
        return [str(i) for i in range(1, x + 1)]
    return [f"({i}, {j}, {k})" for k in range(1, z + 1)
            for j in range(1, y + 1) for i in range(1, x + 1)]


def parse_ofl(data: dict, manufacturer: str = "", key: str = "") -> list[dict]:
    """One OFL fixture (the parsed JSON) -> the parse_gdtf shape."""
    if not isinstance(data, dict) or data.get("redirectTo") or not data.get("modes"):
        raise ValueError("not an Open Fixture Library fixture")
    wheels = data.get("wheels") or {}
    chans: dict[str, dict] = dict(data.get("availableChannels") or {})
    matrix = data.get("matrix") or {}
    pixel_keys = _ofl_pixel_keys(matrix)
    group_keys = list((matrix.get("pixelGroups") or {}).keys())
    for tname, tdef in (data.get("templateChannels") or {}).items():
        for pk in pixel_keys + group_keys:
            text = json.dumps(tdef).replace("$pixelKey", pk)
            chans[tname.replace("$pixelKey", pk)] = json.loads(text)
    fine: dict[str, tuple[str, int]] = {}
    switched: dict[str, str] = {}
    for name, cdef in chans.items():
        for i, alias in enumerate(cdef.get("fineChannelAliases") or [], start=1):
            fine[alias] = (name, i)
        for alias, targets in (cdef.get("switchChannels") or {}).items():
            if isinstance(targets, dict) and targets:
                switched[alias] = next(iter(targets.values()))

    modes = []
    for mode in data["modes"]:
        names: list[str | None] = []
        for c in mode.get("channels") or []:
            if isinstance(c, dict) and c.get("insert") == "matrixChannels":
                rep = c.get("repeatFor")
                keys = rep if isinstance(rep, list) else (
                    group_keys if rep == "eachPixelGroup" else pixel_keys)
                temps = c.get("templateChannels") or []
                if c.get("channelOrder") == "perChannel":
                    names += [t.replace("$pixelKey", k) for t in temps for k in keys]
                else:
                    names += [t.replace("$pixelKey", k) for k in keys for t in temps]
            else:
                names.append(c if isinstance(c, str) else None)
        present = set(n for n in names if n)
        rows = []
        for name in names:
            if name is None:
                rows.append(_detail("Unused"))
                continue
            name = switched.get(name, name)
            if name in fine:
                base, level = fine[name]
                bdef = chans.get(base) or {}
                blabel = _ofl_label(base, bdef, wheels)
                label = f"{blabel} fine" if level == 1 and channel_role(blabel) not in ("raw", "unused") else "Unused"
                rows.append(_detail(label, attribute="fine", bits=16, coarse=False))
                continue
            cdef = chans.get(name)
            if cdef is None:
                rows.append(_detail(name))
                continue
            aliases = cdef.get("fineChannelAliases") or []
            bits = 16 if aliases and aliases[0] in present else 8
            rows.append(_ofl_detail(name, cdef, wheels, bits))
        modes.append(_mode(mode.get("name") or mode.get("shortName"), rows))
    return [{"manufacturer": manufacturer or key.split("/")[0].replace("-", " ").title(),
             "model": data.get("name") or key, "type": ", ".join(data.get("categories") or []),
             "modes": modes}]


# ---------------------------------------------------------------------------
# QLC+ (.qxf)
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(node, name: str) -> list:
    return [c for c in list(node) if _local(c.tag) == name]


def _kid(node, name: str):
    for c in list(node):
        if _local(c.tag) == name:
            return c
    return None


_PRESET_LABEL = {
    "IntensityMasterDimmer": "Dimmer", "IntensityDimmer": "Dimmer",
    "PositionPan": "Pan", "PositionTilt": "Tilt",
    "ColorMacro": "Color Macro", "ColorWheel": "Color Wheel",
    "GoboWheel": "Gobo Wheel", "GoboIndex": "Gobo Rotate",
    "ShutterStrobeSlowFast": "Strobe", "ShutterStrobeFastSlow": "Strobe",
    "ShutterIrisMinToMax": "Iris", "ShutterIrisMaxToMin": "Iris",
    "BeamFocusNearFar": "Focus", "BeamFocusFarNear": "Focus",
    "BeamZoomSmallBig": "Zoom", "BeamZoomBigSmall": "Zoom",
    "PrismRotationSlowFast": "Facet Rotation", "PrismRotationFastSlow": "Facet Rotation",
    "NoFunction": "Unused",
    # the bases of the *Fine presets
    "BeamZoom": "Zoom", "BeamFocus": "Focus", "ShutterIris": "Iris",
}


def _qxf_label(ch) -> str:
    name = (ch.get("Name") or "").strip()
    low = name.lower()
    preset = ch.get("Preset") or ""
    if preset:
        base = preset[:-4] if preset.endswith("Fine") else preset
        if base in _PRESET_LABEL:
            label = _PRESET_LABEL[base]
        elif base.startswith("Intensity") and base[9:].lower() in _COLOURS:
            label = _COLOURS[base[9:].lower()]
        elif base.startswith("Speed") and ("Pan" in base or "Tilt" in base):
            label = "Pan/Tilt Speed"
        else:
            label = name
        if base in ("PositionPan", "PositionTilt"):
            # some files tag Tilt Fine with the Pan preset: trust the name
            if "tilt" in name.lower() and "pan" not in name.lower():
                label = "Tilt"
            elif "pan" in name.lower() and "tilt" not in name.lower():
                label = "Pan"
        if preset.endswith("Fine"):
            if label.lower().endswith(" fine"):
                return label
            return f"{label} fine" if channel_role(label) not in ("raw", "unused") else "Unused"
        return label
    group = _kid(ch, "Group")
    gname = (group.text or "").strip() if group is not None else ""
    byte1 = group is not None and group.get("Byte") == "1"
    colour = _kid(ch, "Colour")
    label = name
    if gname == "Intensity":
        if colour is not None and (colour.text or "").strip().lower() in _COLOURS:
            label = _COLOURS[(colour.text or "").strip().lower()]
        elif channel_role(name) in ("raw", "dimmer", "zone_dimmer"):
            label = "Dimmer" if channel_role(name) != "zone_dimmer" else name
    elif gname in ("Pan", "Tilt"):
        label = name if re.search(r"rotat|continuous|speed|endless", low) else gname
    elif gname == "Colour":
        label = name if re.search(r"rotat|scroll|speed", low) else "Color Wheel"
    elif gname == "Gobo":
        label = "Gobo Rotate" if re.search(r"rot|index|spin", low) else (
            name if "shake" in low else ("Gobo 2" if re.search(r"\b2\b", low) else "Gobo Wheel"))
    elif gname == "Shutter":
        label = "Iris" if "iris" in low else ("Strobe" if "strobe" in low else "Shutter")
    elif gname == "Speed":
        label = "Pan/Tilt Speed" if re.search(r"pan|tilt|p/t", low) else name
    elif gname == "Prism":
        label = "Facet Rotation" if "rot" in low else "Prism"
    elif gname == "Beam":
        for word in ("Zoom", "Focus", "Frost", "Iris"):
            if word.lower() in low:
                label = word
                break
    elif gname == "Maintenance":
        label = "Function " + name
    elif gname == "Nothing":
        label = "Unused"
    elif gname == "Effect" and channel_role(name) in (
            "dimmer", "pan", "tilt", "red", "green", "blue", "white", "amber",
            "uv", "cyan", "magenta", "yellow", "wheel", "gobo"):
        label = "Function " + name          # a programme, not the thing
    if byte1:
        if label.lower().endswith(" fine"):
            return label if channel_role(label).endswith("_fine") else "Unused"
        return f"{label} fine" if channel_role(label) not in ("raw", "unused") else "Unused"
    return label


def _qxf_detail(ch, pan_max: float | None, tilt_max: float | None) -> dict:
    label = _qxf_label(ch)
    row = _detail(label, attribute=ch.get("Preset") or "", name=(ch.get("Name") or "").strip())
    caps_rows = []
    for c in _kids(ch, "Capability"):
        try:
            caps_rows.append([int(c.get("Min") or 0), int(c.get("Max") or 0), (c.text or "").strip()[:48]])
        except ValueError:
            continue
    row["caps"] = caps_rows or None
    role = row["role"]
    if role.endswith("_fine"):
        row.update(coarse=False, bits=16)
        return row
    if role == "pan":
        row["phys_from"], row["phys_to"] = _centred(pan_max)
    elif role == "tilt":
        row["phys_from"], row["phys_to"] = _centred(tilt_max)
    caps = _kids(ch, "Capability")
    preset = ch.get("Preset") or ""
    low_name = (ch.get("Name") or "").lower()
    if role == "speed":
        if "FastSlow" in preset or "fast to slow" in low_name or "fast-slow" in low_name:
            row["fast_first"] = True
        elif "SlowFast" in preset or "slow to fast" in low_name or "slow-fast" in low_name:
            row["fast_first"] = False
    if role in ("shutter", "strobe"):
        ranges = []
        for c in caps:
            text = (c.text or "").strip().lower()
            # the file's own tag first ("StrobeFreqRange", "PulseFreqRange"), then
            # its words - also as misspelled ("Stobe") or longer ("Pulsation")
            tagged = re.match(r"(Strobe|Pulse)(?!.*Off)", c.get("Preset") or "")
            if (tagged or re.search(r"strobe|stobe|puls|random|lightning|flash|^shutter \d", text)) and not re.search(
                    r"^(no strobe|strobe off|off|open|closed|shutter open|shutter closed)\b", text):
                try:
                    ranges.append([int(c.get("Min") or 0), int(c.get("Max") or 0)])
                except ValueError:
                    pass
        row["strobe_ranges"] = ranges or None
        for c in caps:
            text = (c.text or "").strip()
            if c.get("Preset") == "ShutterOpen" or (_OPEN_TEXT.match(text) and "close" not in text.lower()):
                try:
                    row["open_from"] = int(c.get("Min") or 0)
                except ValueError:
                    pass
                break
    if role in ("wheel", "gobo"):
        found = []
        for i, c in enumerate(caps, start=1):
            text = (c.text or "").strip()
            try:
                lo, hi = int(c.get("Min") or 0), int(c.get("Max") or 0)
            except ValueError:
                continue
            if not text or hi - lo > 48 or _NOT_A_SLOT.search(text):
                continue
            if c.get("Preset") in ("ColorDoubleMacro",):
                continue                    # a split between two colours
            hexcol = None
            for attr in ("Res1", "Res", "Color"):
                v = c.get(attr) or ""
                if v.startswith("#") and len(v) == 7:
                    hexcol = v
                    break
            if role == "wheel" and hexcol is None and re.match(r"^\s*(open|white)\b", text, re.I):
                hexcol = "#ffffff"
            slot = {"name": text[:40], "hex": hexcol, "slot": i}
            img = (c.get("Res1") or c.get("Res") or "").strip()
            if role == "gobo" and re.search(r"\.(svg|png)$", img, re.I) and not re.search(r"(^|/)open\.svg$", img, re.I):
                slot["img"] = "qlc:" + img          # the gobo's picture (QLC+ gobos folder)
            found.append((lo, hi, slot))
        row["slots"] = _slot_rows(found) if len(found) >= 2 else None
    return row


def parse_qxf(xml: bytes | str) -> list[dict]:
    """One QLC+ fixture definition (.qxf) -> the parse_gdtf shape."""
    root = ET.fromstring(xml)
    if _local(root.tag) != "FixtureDefinition":
        raise ValueError("not a QLC+ fixture definition")

    def text(name):
        n = _kid(root, name)
        return (n.text or "").strip() if n is not None else ""
    channels = {c.get("Name"): c for c in _kids(root, "Channel")}

    def focus_of(node):
        phys = _kid(node, "Physical")
        foc = _kid(phys, "Focus") if phys is not None else None
        if foc is None:
            return None, None

        def num(v):
            try:
                return float(v) if v not in (None, "", "0") else None
            except ValueError:
                return None
        return num(foc.get("PanMax")), num(foc.get("TiltMax"))
    default_focus = focus_of(root)
    modes = []
    for mode in _kids(root, "Mode"):
        pan_max, tilt_max = focus_of(mode)
        if pan_max is None and tilt_max is None:
            pan_max, tilt_max = default_focus
        order = []
        for c in _kids(mode, "Channel"):
            try:
                order.append((int(c.get("Number") or 0), (c.text or "").strip()))
            except ValueError:
                continue
        rows = []
        for _num, name in sorted(order):
            ch = channels.get(name)
            rows.append(_qxf_detail(ch, pan_max, tilt_max) if ch is not None else _detail(name or "Unused"))
        modes.append(_mode(mode.get("Name"), rows))
    if not modes:
        raise ValueError("the fixture has no modes")
    return [{"manufacturer": text("Manufacturer") or "Unknown",
             "model": text("Model") or "Fixture", "type": text("Type"),
             "modes": modes}]


# ---------------------------------------------------------------------------
# the bundled libraries
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_INDEX: dict[str, list[dict]] = {}


def _bundle(src: str) -> Path | None:
    meta = SOURCES.get(src)
    path = BUNDLE_DIR / meta["file"] if meta else None
    return path if path is not None and path.is_file() else None


def index(src: str) -> list[dict]:
    """[{key, manufacturer, model, type, modes: [[name, channels]]}]."""
    with _LOCK:
        if src not in _INDEX:
            path = _bundle(src)
            rows: list[dict] = []
            if path is not None and src == "jarvis":
                try:
                    rows = [{"key": f["key"], "manufacturer": f["manufacturer"],
                             "model": f["model"], "type": f.get("type", ""),
                             "modes": [[m["name"], m["channel_count"]] for m in f["modes"]]}
                            for f in json.loads(path.read_text(encoding="utf-8"))]
                except (OSError, KeyError, ValueError):
                    rows = []
            elif path is not None:
                try:
                    with zipfile.ZipFile(path) as zf:
                        rows = json.loads(zf.read("index.json"))
                except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                    rows = []
            _INDEX[src] = rows
        return _INDEX[src]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def search(query: str, limit: int = 60) -> list[dict]:
    """Every bundled fixture whose maker + model matches all the words."""
    from . import searchmatch
    words = _norm(query).split()
    if not words:
        return []
    out = []
    for src in SOURCES:
        for row in index(src):
            hit = searchmatch.score(query, row["manufacturer"], row["model"], row.get("type", ""))
            if hit is not None:
                out.append(({**row, "src": src, "library": SOURCES[src]["name"],
                             "close": hit[0] > 0}, hit))
    # full matches first (typos after), then the exact phrase, then shorter
    # names (the base model before its variants), then by library order
    q = _norm(query)
    order = {s: i for i, s in enumerate(SOURCES)}
    out.sort(key=lambda t: (t[1], q not in _norm(f"{t[0]['manufacturer']} {t[0]['model']}"),
                            len(t[0]["model"]), order.get(t[0]["src"], 9), t[0]["manufacturer"].lower()))
    return [r for r, _ in out[:limit]]


def load(src: str, key: str) -> list[dict]:
    """Parse one bundled fixture into the parse_gdtf shape."""
    path = _bundle(src)
    if path is None:
        raise ValueError(f"the {src} library is not installed")
    row = next((r for r in index(src) if r["key"] == key), None)
    if row is None:
        raise ValueError(f"no fixture {key!r} in the {SOURCES[src]['name']}")
    if src == "jarvis":
        fixture = next(f for f in json.loads(path.read_text(encoding="utf-8")) if f["key"] == key)
        item = json.loads(json.dumps(fixture))          # a private copy
        for mode in item["modes"]:
            for row_ in mode["detail"]:
                row_.setdefault("fx_label", row_["label"])
        return [item]
    with zipfile.ZipFile(path) as zf:
        raw = zf.read("fixtures/" + key)
    if src == "ofl":
        parsed = parse_ofl(json.loads(raw), row["manufacturer"], key)
    else:
        parsed = parse_qxf(raw)
    for item in parsed:                     # the index names win (display)
        item["manufacturer"] = row["manufacturer"]
        item["model"] = row["model"]
    return parsed


def parse_file(path: Path) -> list[dict]:
    """A dropped-in .qxf or OFL .json file."""
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == ".qxf":
        return parse_qxf(data)
    doc = json.loads(data)
    return parse_ofl(doc, path.parent.name.replace("-", " ").title(), path.stem)


_BUILT: dict[str, str] = {}


def built(src: str) -> str:
    """When a bundled library was last brought up to date (its meta.json's
    'built', an ISO date), '' when it doesn't say."""
    if src not in _BUILT:
        when = ""
        path = _bundle(src)
        try:
            if path and path.suffix == ".zip":
                with zipfile.ZipFile(path) as zf:
                    when = str(json.loads(zf.read("meta.json")).get("built") or "")[:10]
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            when = ""
        _BUILT[src] = when
    return _BUILT[src]


def libraries() -> list[dict]:
    """What is bundled, for the UI's credits line."""
    return [{"src": s, **{k: v for k, v in m.items() if k != "file"},
             "fixtures": len(index(s)), "built": built(s)} for s, m in SOURCES.items()]


# ---------------------------------------------------------------------------
# special effects and lasers: classify, and make them safe
# ---------------------------------------------------------------------------
# Every library describes a fog machine's output or a laser's power as a
# "dimmer", a "shutter" or an "intensity" - so a Flash all button, Full or
# the auto show would fire the fog, open a CO2 valve or light a laser.
# `apply_fx` runs on EVERY fixture as it is stored (GDTF, OFL, QLC+, the
# Jarvis library, dropped files): it works out whether the fixture is a
# light, a laser or an SFX machine, and gives an effect's channels their
# own vocabulary (fx_fire, fx_arm, fog, laser_on, laser_pattern...), which
# no light action ever drives.  The value that means "fire", "armed" or
# "on" is taken from the fixture's own capability ranges, never guessed:
# a MagicFX Psyco2Jet's safety channel reads 100-155 as enabled and
# 156-255 as TEST MODE, so "arm = 255" would be wrong.

FX_KINDS = {
    # kind: (class, default longest continuous fire in seconds)
    "confetti": ("sfx", 30.0), "co2": ("sfx", 3.0), "flame": ("sfx", 2.0),
    "spark": ("sfx", 10.0), "fog": ("sfx", 20.0), "haze": ("sfx", 600.0),
    "bubble": ("sfx", 600.0), "snow": ("sfx", 600.0), "laser": ("laser", 600.0),
    "other": ("sfx", 5.0),
}

_KIND_PATTERNS = [
    ("confetti", r"confetti|funfetti|streamer|stadium ?(shot|blaster|blower)|swirl ?fan"),
    ("co2", r"\bco2\b|co\s?2 jet|cryo|psyco2|eco2"),
    ("flame", r"\bflames?\b|flamer|fire\s*(jet|machine|effect|burst)|g-flame|dragon"),
    ("spark", r"\bsparks?\b|cold\s*(fire|spark)|sparkular"),
    ("bubble", r"bubble"),
    ("snow", r"\bsnow(?!ball)"),
    ("haze", r"\bhaze|\bhazer|\bfaze\b"),
    ("fog", r"\bfog|\bsmoke|\bgeyser|\bsteam\b|\bjett?\b|\bmist\b"),
]
# Words that mean the fixture is really a light, whatever else it says.
_LIGHT_WORDS = r"\bpar(\b|\d)|parcan|\bwash|\bspot(\b|\d)|\bbeam|\bbar\b|\bpanel|\bprofile"

_OFF_TEXT = re.compile(r"\b(off|no (function|output|effect|fire)|closed|disabled?|safe|blackout|stop|none|idle)\b|^0$", re.I)
_FIRE_TEXT = re.compile(r"\b(on|fire|firing|shoot|shot|launch|burst|blast|output|valve open|open|go|max|full|trigger|ignit\w*)\b", re.I)
_ARM_TEXT = re.compile(r"\b(enabled?|armed?|ready|safety off|active|on)\b", re.I)
_DANGER_TEXT = re.compile(r"\btest\b|\breset\b|\bpurge\b|\bclean", re.I)


def fx_kind(manufacturer: str, model: str, type_text: str, labels: list[str]) -> str:
    """'' for a light, else a key of FX_KINDS."""
    text = f"{manufacturer} {model} {type_text}".lower()
    if "laser" in type_text.lower() or re.search(r"\blaser", text):
        return "laser"
    hazer = re.search(r"smoke|hazer|fog", str(type_text).lower())
    for kind, pattern in _KIND_PATTERNS:
        if re.search(pattern, text):
            if hazer and kind in ("flame", "spark"):
                continue                 # a hazer named "Dragon" is not a flame machine
            if kind in ("fog", "haze") and re.search(_LIGHT_WORDS, text) \
                    and not re.search(r"\bfog|\bhaze", str(type_text).lower()):
                return ""
            if re.search(_LIGHT_WORDS, text) and kind not in ("fog", "haze", "co2", "confetti"):
                continue
            return kind
    if re.search(r"smoke|hazer", str(type_text).lower()):
        return "haze" if "haz" in str(type_text).lower() else "fog"
    return ""


def _pick(caps, pattern: re.Pattern, avoid: re.Pattern | None = None) -> int | None:
    """The middle of the first range whose words match (and avoid danger)."""
    for lo, hi, text in caps or []:
        if pattern.search(str(text)) and not (avoid and avoid.search(str(text))) \
                and not _OFF_TEXT.search(str(text)):
            return (int(lo) + int(hi)) // 2
    return None


def _off_value(caps) -> int:
    for lo, _hi, text in caps or []:
        t = str(text)
        if re.search(r"safety\s*on|disabled?|pre-?heat\s*off|\bsafe\b", t, re.I) or (
                _OFF_TEXT.search(t) and not re.search(r"safety\s*off", t, re.I)):
            return int(lo)
    return 0


def _arm_value(caps) -> int | None:
    """Where an SFX machine is armed: 'Safety OFF', 'Enabled', 'Pre-heat
    ON'... never a test, reset or purge range."""
    for pattern in (r"safety\s*off|safe\s*off", r"pre-?heat\s*on",
                    r"\b(enabled?|armed?|ready|active)\b", r"\bon\b"):
        for lo, hi, text in caps or []:
            t = str(text)
            if re.search(pattern, t, re.I) and not _DANGER_TEXT.search(t) \
                    and not re.search(r"disabled?|safety\s*on|emergency", t, re.I):
                return (int(lo) + int(hi)) // 2
    return None


def _is_led(name: str) -> bool:
    return bool(re.search(r"\bled\b|colou?r|\brgb|\bred\b|\bgreen\b|\bblue\b|\bwhite\b|"
                          r"\bamber\b|\buv\b|light|lamp|dimmer led|led dimmer", name.lower()))


def _fx_role(kind: str, row: dict, has_rgb: bool) -> str | None:
    """The effects label for one channel of an FX fixture, or None to keep."""
    orig = (row.get("name") or "").lower()
    name = f"{orig} {row.get('label') or ''}".lower()
    role = row.get("role") or "raw"
    if role.endswith("_fine"):
        return None
    if role == "unused":
        if not orig or re.search(r"maintenance|(?<!p)reset|no function|not used|unused|reserved|^function$", orig):
            return None
        name = orig                                   # judge it by its own name
        role = "raw"
    caps_text = " ".join(str(c[2]) for c in row.get("caps") or []).lower()
    if kind == "laser":
        # a beam bar's diodes: "Beam 3", "Laser 3", "Diode 3", "Head 3"...
        beam = re.search(r"\b(?:beam|diode|laser|head|lens|output|ld)\s*#?\s*(\d{1,2})\b", name)
        if beam and 1 <= int(beam.group(1)) <= 16 and not re.search(
                r"pattern|speed|colou?r|size|rotat|mode|position|move|zoom", name):
            return f"Laser Beam {int(beam.group(1))}"
        if re.search(r"pattern|drawing|gobo|figure|effect|graphic|animation|\bshow\b", name) \
                and "speed" not in name:
            return "Laser Pattern"
        if re.search(r"rotat|rolling|roll|spin|twist", name) or role == "gobo_rot":
            return "Laser Rotation"
        if re.search(r"zoom|size|scale|scan(ning)? size", name) or role == "zoom":
            return "Laser Size"
        if re.search(r"colou?r|\bred\b|\bgreen\b|\bblue\b|\brgb", name) and role in (
                "wheel", "macro", "raw", "red", "green", "blue", "white", "cyan", "magenta",
                "yellow", "amber") and not re.search(r"dimmer|intensity|on/off|output|laser$", orig):
            return "Laser Colour" if role in ("wheel", "macro", "raw") else None
        if re.search(r"\bx\b|x[- ]?axis|horizontal|x move|x pos", name) or role == "pan":
            return "Laser X"
        if re.search(r"\by\b|y[- ]?axis|vertical|y move|y pos|\btilt|motor\s*(pos|position|angle)?$|\bangle", name) \
                or role == "tilt":
            return "Laser Y"
        if re.search(r"speed", name) or role == "speed":
            return "Laser Speed"
        if re.search(r"mode|control|sound|auto|program|function", name) or role == "macro":
            return "FX Mode"
        if role in ("dimmer", "zone_dimmer", "shutter") or re.search(
                r"on/off|output|power|enable|blackout|laser on", name) or (re.search(r"^lasers?$", orig.strip()) and role != "wheel"):
            return "Laser Output"
        if role == "strobe":
            return "FX Setting"
        if role == "gobo":
            return "Laser Pattern"
        if role in ("red", "green", "blue") and re.search(r"laser", name) \
                and re.search(r"\boff\b", caps_text) and re.search(r"\bon\b", caps_text):
            return "Laser Output"            # "Red Laser: off / on" - one of its outputs
        return None if role in ("red", "green", "blue", "white") else ("FX Setting" if role == "raw" else None)
    # SFX machines
    if re.search(r"safety|\barm\b|armed|ignit|enable|security|interlock", name) \
            or re.search(r"pre-?heat\s*on", caps_text):
        return "FX Arm"
    # the channel that makes it GO is its output, whatever else it says: a
    # MagicFX Psyco2Jet's preset mode fires from "GO" (200-249 continuous) -
    # filed as a setting, it fired whenever a cue or another setting put it
    # past 200, armed or not
    if re.search(r"^go$|\bgo\b|\btrigger\b|\bfire\b|\bshoot\b", orig):
        return "FX Fire"
    if kind == "confetti" and re.search(r"\bhopper|\bfeeder", name):
        return "FX Fire"                      # a confetti blower's hoppers feed it (MagicFX StadiumBlower)
    if (re.search(r"\bfan\b|blower|\bwind", name) and "speed" not in name or re.search(r"fan speed|blower", name)) \
            and not re.search(r"fog|smoke|haze|faze", orig):
        return "FX Fan"                       # ("Faze and Fan" on one channel is the output)
    if re.search(r"height|size|level of spark", name):
        return "FX Height"
    # a fog / haze machine's "Volume control" or "Output control" is its
    # output (the word "control" filed an Antari Fazer's as a mode: the fog
    # button could not reach it, and any programmed value fogged)
    if kind in ("fog", "haze") and re.search(r"volume|output|pump", name) \
            and not re.search(r"\bmode\b|program|preset|\bauto\b|sound|timer|interval|duration|delay|fan", name):
        return "Fog Output"
    if re.search(r"\bmode\b|program|preset|\bauto\b|sound|control|timer|interval|duration|\bdelay", name):
        return "FX Mode" if re.search(r"mode|program|preset|auto|sound|control", name) else "FX Setting"
    if role in ("pan", "tilt", "speed", "pan_fine", "tilt_fine"):
        return None                               # a CO2 jet's tilt stays tilt
    led = _is_led(row.get("name") or "")
    if led and (has_rgb or role in ("red", "green", "blue", "white", "amber", "uv")):
        return None                               # the machine's own LEDs
    if has_rgb and role in ("dimmer", "zone_dimmer", "strobe") and not re.search(
            r"fog|smoke|haze|output|volume|pump|fire|flame|spark|co2|confetti", orig):
        return None                               # the LEDs' master dimmer / strobe
    out_words = r"fog|smoke|haze|faze|output|volume|pump|on/off|fire|shoot|shot|launch|valve|" \
                r"flame|spark|co2|confetti|burst|blast|dimmer|intensity|trigger|\bon\b|" \
                r"fountain|jet|effect|height"
    if role in ("dimmer", "zone_dimmer", "shutter", "strobe", "raw", "macro") and (
            re.search(out_words, name) or role in ("dimmer", "zone_dimmer")
            or (role == "shutter" and kind in ("fog", "haze") and not has_rgb)):
        # (a fogger's "Shutter" - ADJ Fog Storm - is its fog output)
        return "Fog Output" if kind in ("fog", "haze", "bubble", "snow") else "FX Fire"
    return "FX Setting" if role == "raw" else None


# A light's channels no role fits, or a second channel on a role that is
# one-per-fixture (a second speed, shutter or wheel), used to become `raw`:
# DMX with no control behind it, held at 0 - a Wave 360's continuous pan
# rotation, built-in tilt programs, heads on/off and auto programs all sat
# dead.  Each now gets its own numbered control (aux1..aux24), named after
# the channel, with its ranges as named steps.  Maintenance channels (reset,
# settings) stay untouched on purpose.  Repeated per-head channels (4 tilts,
# 4 x RGBW) still share one control until multi-head support.
_MAINTENANCE = re.compile(r"(?<!p)reset|maintenance|settings?\b|no ?function|not used|unused|reserved|"
                          r"calibrat|test|firmware|display|fan\b|mode select|dimmer (curve|mode|speed)", re.I)
_ONE_PER_HEAD = {"speed", "shutter", "strobe", "wheel", "gobo", "gobo_rot", "prism", "focus",
                 "zoom", "frost", "iris", "macro"}


def apply_aux(item: dict) -> dict:
    """Give a light's otherwise uncontrollable channels their own controls
    (in place; returns the item).  Effects and lasers are left to apply_fx."""
    if item.get("fx_kind"):
        return item
    from .engine_support import channel_role as _role
    for mode in item.get("modes") or []:
        rows = mode.get("detail") or []
        seen: set = set()
        k = 0
        for i, row in enumerate(rows):
            role = row.get("role") or _role(row.get("label") or "")
            name = str(row.get("name") or row.get("label") or "").strip()
            base = role
            want = False
            if role == "raw" and name and not _MAINTENANCE.search(name):
                want = True
            elif role in _ONE_PER_HEAD and role in seen:
                want = True                      # a second speed / shutter / wheel
            seen.add(base)
            if not want or k >= 24:
                continue
            k += 1
            label = f"Aux {k} · {name or 'Channel ' + str(i + 1)}"[:60]
            row["label"] = label
            row["role"] = _role(label)
            if i < len(mode.get("channels") or []):
                mode["channels"][i] = label
            found = [(lo, hi, {"name": str(t)[:56], "hex": None, "slot": n})
                     for n, (lo, hi, t) in enumerate(_clean_caps(row.get("caps")), start=1)
                     if str(t).strip()]
            row["slots"] = _slot_rows(found) if len(found) >= 2 else None
    return item


def _clean_caps(caps) -> list[tuple[int, int, str]]:
    """Capability rows as (lo, hi, text) ints, skipping any a file left
    half-empty ([None, 10, "..."]) - one such row crashed the whole
    upgrade pass and left every fixture after it without its controls."""
    out = []
    for c in caps or []:
        try:
            lo, hi, text = c[0], c[1], c[2] if len(c) > 2 else ""
            out.append((int(lo), int(hi), str(text or "")))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def apply_fx(item: dict) -> dict:
    """Classify one parsed fixture and give its effect channels their own
    roles and values (in place; returns the item)."""
    from .engine_support import channel_role as _role
    labels = [c for m in item.get("modes") or [] for c in m.get("channels") or []]
    kind = item.get("fx_kind")
    if kind == "light":
        kind = ""                                 # the operator said: a light
    elif not kind:
        kind = fx_kind(item.get("manufacturer", ""), item.get("model", ""),
                       item.get("type", "") or "", labels)
    item["fx_kind"] = kind
    if not kind:
        return item
    cls, max_s = FX_KINDS[kind]
    for mode in item.get("modes") or []:
        rows = mode.get("detail") or []
        has_rgb = sum(1 for r in rows if r.get("role") in ("red", "green", "blue")) >= 3
        used: set = set()
        for i, row in enumerate(rows):
            new = row.get("fx_label") or _fx_role(kind, row, has_rgb)
            # a second channel landing on a role already taken would share it
            # (one control, the other channel dead): give it its own setting
            # (output channels - power, fire, fog, arm - share on purpose: every
            # one of a two-laser unit's outputs comes on together)
            if new and _role(new) in used and not _role(new).endswith("_fine") \
                    and _role(new) not in ("laser_on", "fx_fire", "fog", "fx_arm"):
                spare = next((k for k in range(2, 7) if f"fx_param{k}" not in used), None)
                new = f"FX Setting {spare}" if spare else new
            if new:
                row["label"] = new
                row["role"] = _role(new)
                if i < len(mode["channels"]):
                    mode["channels"][i] = new
            if row.get("role") not in (None, "raw", "unused"):
                used.add(row.get("role"))
            role = row.get("role")
            caps = row.get("caps")
            if role in ("fx_fire", "laser_on") or str(role).startswith("laser_beam"):
                steps = [c for c in caps or [] if not _OFF_TEXT.search(str(c[2]))
                         and not _DANGER_TEXT.search(str(c[2]))]
                fire = _pick(caps, _FIRE_TEXT, _DANGER_TEXT)
                if fire is None and role == "laser_on":
                    # a laser whose output channel is its mode switch ("0-49 off,
                    # 50-99 sound, ... 200-255 DMX mode"): on = under DMX control,
                    # the last DMX range (usually the one that frees every channel)
                    dmx = [c for c in steps if re.search(r"\bdmx\b", str(c[2]), re.I)]
                    if dmx:
                        fire = (int(dmx[-1][0]) + int(dmx[-1][1])) // 2
                if fire is None and len(steps) >= 2:  # graded (spark height): lowest step
                    fire = (int(steps[0][0]) + int(steps[0][1])) // 2
                row.setdefault("on_value", fire if fire is not None else 255)
                row.setdefault("off_value", _off_value(caps))
            elif role == "fx_arm":
                on = _arm_value(caps)
                row.setdefault("on_value", on if on is not None else 255)
                row.setdefault("off_value", _off_value(caps))
            elif role == "fog":
                row.setdefault("off_value", _off_value(caps))
            if role in ("laser_pattern", "laser_colour", "fx_mode", "fx_fire", "laser_on") and caps and not row.get("slots"):
                found = [(int(lo), int(hi), {"name": str(t)[:40], "hex": None, "slot": n})
                         for n, (lo, hi, t) in enumerate(caps, start=1)
                         if str(t).strip() and int(hi) - int(lo) <= 64
                         and not (role == "fx_fire" and _OFF_TEXT.search(str(t)))]
                row["slots"] = _slot_rows(found) if len(found) >= 2 else None
            if role in ("fx_fire", "fog", "laser_on"):
                row.setdefault("fx_kind", kind)
                row.setdefault("max_s", max_s)
        if kind == "laser" and not any(r.get("role") == "laser_on" or str(r.get("role")).startswith("laser_beam")
                                       for r in rows):
            # no power channel: its mode switch ("0-9 blackout ... 220-255 DMX
            # mode") is what turns it on and off - the output, so ARM and the
            # laser buttons drive it (and the 3D draws it)
            off_re = re.compile(r"\boff\b|black\s*-?\s*out|bl?ock\s*out|\bdark\b|\bclosed?\b", re.I)
            on_re = re.compile(r"\bdmx\b|manual|^\s*(laser\s*)?on\s*$", re.I)
            sw = next((r for r in rows if r.get("role") in ("fx_mode", "laser_pattern") and r.get("caps")
                       and any(on_re.search(str(c[2])) for c in r["caps"])
                       and any(off_re.search(str(c[2])) for c in r["caps"])
                       and (r.get("role") == "fx_mode" or any(re.search(r"\bdmx\b", str(c[2]), re.I) for c in r["caps"]))), None)
            if sw is not None:
                caps = sw["caps"]
                ons = [c for c in caps if on_re.search(str(c[2])) and not off_re.search(str(c[2]))]
                offs = [c for c in caps if off_re.search(str(c[2]))]
                i = rows.index(sw)
                sw.update({"label": "Laser Output", "role": "laser_on", "fx_kind": kind, "max_s": max_s,
                           "on_value": (int(ons[-1][0]) + int(ons[-1][1])) // 2,
                           "off_value": (int(offs[0][0]) + int(offs[0][1])) // 2 if offs else _off_value(caps)})
                if i < len(mode["channels"]):
                    mode["channels"][i] = "Laser Output"
        mode["fx_class"] = cls
    item["fx_class"] = cls
    return item


_PHYS: dict[str, dict] = {}


def physical(source: str) -> dict:
    """{kg, watts} from a bundled fixture's own file ("ofl:key" /
    "qlc:key"); {} when the file doesn't say (or isn't bundled)."""
    source = str(source or "")
    if source in _PHYS:
        return _PHYS[source]
    out: dict = {}
    src, _, key = source.partition(":")
    path = _bundle(src) if src in ("ofl", "qlc") and key else None
    if path is not None:
        try:
            with zipfile.ZipFile(path) as zf:
                raw = zf.read("fixtures/" + key)
            if src == "ofl":
                phys = json.loads(raw).get("physical") or {}
                if isinstance(phys.get("weight"), (int, float)) and phys["weight"] > 0:
                    out["kg"] = float(phys["weight"])
                if isinstance(phys.get("power"), (int, float)) and phys["power"] > 0:
                    out["watts"] = float(phys["power"])
            else:
                m = re.search(rb'<Dimensions[^>]*\bWeight="([0-9.]+)"', raw)
                if m and float(m.group(1)) > 0:
                    out["kg"] = float(m.group(1))
                m = re.search(rb'<Technical[^>]*\bPowerConsumption="([0-9.]+)"', raw)
                if m and float(m.group(1)) > 0:
                    out["watts"] = float(m.group(1))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            out = {}
    _PHYS[source] = out
    return out


# -- gobo pictures ----------------------------------------------------------
_GOBO_SLOTS: dict = {}
_GOBO_ZIP: list = []
GOBO_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


def gobo_slots(source: str, mode: str = "") -> list[list]:
    """[[from, to, "qlc:Maker/gobo.svg"], ...] for a bundled fixture's first
    gobo wheel, read from its own file ("ofl:key" / "qlc:key"); []."""
    key = (str(source or ""), str(mode or ""))
    if key in _GOBO_SLOTS:
        return _GOBO_SLOTS[key]
    out: list[list] = []
    src, _, fkey = key[0].partition(":")
    if src in ("ofl", "qlc") and fkey:
        try:
            parsed = load(src, fkey)
        except (ValueError, OSError, KeyError, zipfile.BadZipFile):
            parsed = []
        modes = [m for item in parsed for m in item.get("modes") or []]
        modes.sort(key=lambda m: m.get("name") != key[1])          # its own mode first
        for m in modes:
            rows = [[sl["from"], sl["to"], sl["img"]] for d in m.get("detail") or [] if d.get("role") == "gobo"
                    for sl in d.get("slots") or [] if sl.get("img")]
            if rows:
                out = rows
                break
    _GOBO_SLOTS[key] = out
    return out


_TWINS: dict[tuple, list[str]] = {}


def _brand(manufacturer: str) -> str:
    """The maker, as both libraries can spell it ("Chauvet DJ" and
    "Chauvet" are one maker)."""
    words = _norm(manufacturer).split()
    return words[0] if words else ""


def twin_gobos(manufacturer: str, model: str, count: int) -> list[str]:
    """The gobo pictures of the same light in a bundled library (the other
    one usually: OFL calls the slots "Slot 2..." where QLC+ has the
    pictures), in wheel order, open slot left out - only when it has the
    same number of gobos, so each picture lands on its own slot.  []."""
    key = (_brand(manufacturer), _norm(model), int(count))
    if key in _TWINS:
        return _TWINS[key]
    out: list[str] = []
    if key[0] and key[1] and count > 0:
        for src in ("qlc", "ofl"):
            for row in index(src):
                if _brand(row["manufacturer"]) != key[0] or _norm(row["model"]) != key[1]:
                    continue
                pics = [r[2] for r in gobo_slots(f"{src}:{row['key']}") if r[2]]
                if len(pics) == count:
                    out = pics
                    break
            if out:
                break
    _TWINS[key] = out
    return out


def gobo_picture(ref: str) -> tuple[bytes, str] | None:
    """The picture for "qlc:Maker/gobo.svg" / "ofl:name" from gobos.zip, or
    "gdtf:<hash>.png" - a maker's own gobo from an imported GDTF, kept in
    DATA/gobos."""
    src, _, name = str(ref or "").partition(":")
    if src == "gdtf":
        if not re.fullmatch(r"[0-9a-f]{20}\.(png|svg|jpg)", name):
            return None
        from . import config
        f = config.DATA / "gobos" / name
        if not f.is_file():
            return None
        return f.read_bytes(), {".jpg": "image/jpeg"}.get(f.suffix, GOBO_TYPES.get(f.suffix, "application/octet-stream"))
    if src not in ("ofl", "qlc") or not name or ".." in name or name.startswith("/"):
        return None
    path = BUNDLE_DIR / "gobos.zip"
    if not path.is_file():
        return None
    with _LOCK:
        if not _GOBO_ZIP:
            try:
                zf = zipfile.ZipFile(path)
                _GOBO_ZIP.append((zf, set(zf.namelist())))
            except (OSError, zipfile.BadZipFile):
                return None
        zf, names = _GOBO_ZIP[0]
        for cand in ([f"{src}/{name}"] if src == "qlc" else [f"ofl/{name}.svg", f"ofl/{name}.png"]):
            if cand in names:
                return zf.read(cand), GOBO_TYPES.get(Path(cand).suffix.lower(), "application/octet-stream")
    return None

