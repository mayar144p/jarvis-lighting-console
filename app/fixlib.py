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
           "open_from": None, "slots": None, "coarse": True}
    row.update(extra)
    return row


def _centred(span: float | None) -> tuple[float | None, float | None]:
    if not span:
        return None, None
    return -abs(span) / 2.0, abs(span) / 2.0


def _mode(name: str, rows: list[dict]) -> dict:
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
    row = _detail(label, attribute=_ofl_main_type(caps), bits=bits)
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
        found.append((byte(lo), byte(hi), {"name": sname, "hex": hexcol, "slot": num}))
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
    row = _detail(label, attribute=ch.get("Preset") or "")
    role = row["role"]
    if role.endswith("_fine"):
        row.update(coarse=False, bits=16)
        return row
    if role == "pan":
        row["phys_from"], row["phys_to"] = _centred(pan_max)
    elif role == "tilt":
        row["phys_from"], row["phys_to"] = _centred(tilt_max)
    caps = _kids(ch, "Capability")
    if role in ("shutter", "strobe"):
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
            found.append((lo, hi, {"name": text[:40], "hex": hexcol, "slot": i}))
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
            if path is not None:
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
    words = _norm(query).split()
    if not words:
        return []
    out = []
    for src in SOURCES:
        for row in index(src):
            hay = _norm(f"{row['manufacturer']} {row['model']} {row.get('type', '')}")
            squashed = hay.replace(" ", "")
            if all(w in hay or w in squashed for w in words):
                out.append({**row, "src": src, "library": SOURCES[src]["name"]})
    # exact model words first, then shorter names (the base model before
    # its variants), then by library order
    q = _norm(query)
    out.sort(key=lambda r: (q not in _norm(f"{r['manufacturer']} {r['model']}"),
                            len(r["model"]), r["manufacturer"].lower()))
    return out[:limit]


def load(src: str, key: str) -> list[dict]:
    """Parse one bundled fixture into the parse_gdtf shape."""
    path = _bundle(src)
    if path is None:
        raise ValueError(f"the {src} library is not installed")
    row = next((r for r in index(src) if r["key"] == key), None)
    if row is None:
        raise ValueError(f"no fixture {key!r} in the {SOURCES[src]['name']}")
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


def libraries() -> list[dict]:
    """What is bundled, for the UI's credits line."""
    return [{"src": s, **{k: v for k, v in m.items() if k != "file"},
             "fixtures": len(index(s))} for s, m in SOURCES.items()]
