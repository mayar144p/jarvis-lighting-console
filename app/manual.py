"""Make a fixture from its manual: the DMX chart as text -> a fixture.

For the lights and effects no library carries.  The operator pastes the
DMX chart, or drops the manual's PDF (the browser extracts its text with
the bundled pdf.js), and gets back a DRAFT:

    {"manufacturer", "model", "type",          # light | laser | confetti | co2 | ...
     "modes": [{"name", "channels": [{"name", "function", "ranges": [[lo, hi, label]]}]}],
     "via": "ai" | "offline", "warnings": [...]}

The draft is shown as an editable table and is only stored when the
operator saves it (`to_parsed` -> fixtures.store_parsed, which runs the
same effects classifier as every other source).  With an AI key the
copilot's model reads the chart; without one an offline reader handles
the usual chart layouts ("1  Dimmer", "000-009 Off", "010255 On").
"""
from __future__ import annotations

import re

from . import fixlib
from .engine_support import channel_role

# The functions a channel can have, and the label that gives it its role.
FUNCTIONS: dict[str, str] = {
    "dimmer": "Dimmer", "red": "Red", "green": "Green", "blue": "Blue",
    "white": "White", "amber": "Amber", "uv": "UV", "cyan": "Cyan",
    "magenta": "Magenta", "yellow": "Yellow",
    "pan": "Pan", "pan fine": "Pan fine", "tilt": "Tilt", "tilt fine": "Tilt fine",
    "pan/tilt speed": "Pan/Tilt Speed",
    "shutter": "Shutter", "strobe": "Strobe",
    "colour wheel": "Color Wheel", "colour macro": "Color Macro",
    "gobo wheel": "Gobo Wheel", "gobo rotation": "Gobo Rotate", "prism": "Prism",
    "zoom": "Zoom", "focus": "Focus", "frost": "Frost", "iris": "Iris",
    "fx fire": "FX Fire", "fx arm": "FX Arm", "fx fan": "FX Fan",
    "fog output": "Fog Output", "fx height": "FX Height", "fx mode": "FX Mode",
    "laser output": "Laser Output", "laser pattern": "Laser Pattern",
    "laser size": "Laser Size", "laser rotation": "Laser Rotation",
    "laser x": "Laser X", "laser y": "Laser Y", "laser speed": "Laser Speed",
    "laser colour": "Laser Colour",
    "setting": "", "unused": "Unused",
}
_ROLE_FUNCTION = {channel_role(label): fn for fn, label in FUNCTIONS.items() if label}
_ROLE_FUNCTION.update({"raw": "setting", "fx_param": "setting", "macro": "colour macro",
                       "speed": "pan/tilt speed", "gobo": "gobo wheel", "wheel": "colour wheel"})
TYPES = ("light", "laser", "confetti", "co2", "flame", "spark", "fog", "haze",
         "bubble", "snow", "other")
_COLOUR_HEX = {"red": "#ff2020", "green": "#20ff40", "blue": "#2040ff", "yellow": "#ffe020",
               "orange": "#ff8a1a", "magenta": "#ff20dd", "pink": "#ff70c0", "cyan": "#20ffee",
               "purple": "#9a33ff", "white": "#ffffff", "open": "#ffffff", "amber": "#ffb000",
               "lime": "#b0ff40", "uv": "#7a2cff"}

MAX_TEXT = 40_000


def _label_for(fn: str, name: str) -> str:
    label = FUNCTIONS.get(fn, "")
    if label:
        return label
    return f"Function {name}".strip() if fn == "setting" else (name or "Unused")


def _clean_ranges(raw) -> list[list]:
    out = []
    for r in raw or []:
        try:
            lo, hi, label = (list(r) + ["", "", ""])[:3] if isinstance(r, (list, tuple)) else (
                r.get("from"), r.get("to"), r.get("label"))
            lo, hi = int(lo), int(hi)
        except (TypeError, ValueError, AttributeError):
            continue
        lo, hi = max(0, min(255, lo)), max(0, min(255, hi))
        if hi < lo:
            lo, hi = hi, lo
        out.append([lo, hi, str(label or "").strip()[:60]])
    return sorted(out, key=lambda r: r[0])


def clean_draft(raw: dict) -> dict:
    """Any draft (from the model, the offline reader or the edited table)
    -> a well-formed one.  Raises ValueError when there is nothing usable."""
    if not isinstance(raw, dict):
        raise ValueError("not a fixture draft")
    typ = str(raw.get("type") or "light").lower().strip()
    typ = typ if typ in TYPES else "light"
    modes = []
    for m in raw.get("modes") or []:
        chans = []
        for c in (m or {}).get("channels") or []:
            if not isinstance(c, dict):
                continue
            fn = str(c.get("function") or "setting").lower().strip()
            fn = fn if fn in FUNCTIONS else "setting"
            chans.append({"name": str(c.get("name") or "").strip()[:48] or f"Channel {len(chans) + 1}",
                          "function": fn, "ranges": _clean_ranges(c.get("ranges"))})
        if chans:
            modes.append({"name": str(m.get("name") or f"{len(chans)}-channel").strip()[:32],
                          "channels": chans[:512]})
    if not modes:
        raise ValueError("no DMX channels found - paste the page with the DMX chart")
    return {"manufacturer": str(raw.get("manufacturer") or "").strip()[:64] or "Unknown",
            "model": str(raw.get("model") or "").strip()[:64] or "Fixture",
            "type": typ, "modes": modes,
            "via": raw.get("via") or "", "warnings": list(raw.get("warnings") or [])[:20]}


def _slots(ranges: list[list], role: str) -> list[dict] | None:
    found = []
    for n, (lo, hi, label) in enumerate(ranges, start=1):
        if not label or hi - lo > 48 or fixlib._NOT_A_SLOT.search(label):
            continue
        low = label.lower()
        hexcol = next((hx for word, hx in _COLOUR_HEX.items() if re.search(rf"\b{word}\b", low)), None) \
            if role in ("wheel", "laser_colour") else None
        found.append((lo, hi, {"name": label[:40], "hex": hexcol, "slot": n}))
    return fixlib._slot_rows(found) if len(found) >= 2 else None


def to_parsed(draft: dict) -> list[dict]:
    """A reviewed draft -> the parse_gdtf shape store_parsed takes."""
    d = clean_draft(draft)
    fx_roles = {"fx_fire", "fx_arm", "fx_fan", "fog", "fx_height", "fx_mode", "fx_param"}
    modes = []
    uses_laser = uses_sfx = False
    names: set[str] = set()
    for m in d["modes"]:
        base, k = m["name"], 2
        while m["name"] in names:                 # two modes may not share a name
            m["name"] = f"{base} ({k})"
            k += 1
        names.add(m["name"])
        rows = []
        for c in m["channels"]:
            label = _label_for(c["function"], c["name"])
            role = channel_role(label)
            uses_laser |= role.startswith("laser_")
            uses_sfx |= role in fx_roles
            caps = c["ranges"] or None
            row = {"label": label, "role": role, "name": c["name"], "attribute": c["function"],
                   "bits": 8, "coarse": not role.endswith("_fine"),
                   "dmx_from": None, "dmx_to": None, "phys_from": None, "phys_to": None,
                   "wheel": None, "open_from": None, "slots": None, "caps": caps,
                   "strobe_ranges": None, "fast_first": None}
            if role.startswith("laser_") or role in fx_roles:
                row["fx_label"] = label             # the operator said so
            if role in ("shutter", "strobe") and caps:
                opens = [lo for lo, _hi, t in caps if fixlib._OPEN_TEXT.match(t) and "close" not in t.lower()]
                row["open_from"] = min(opens) if opens else None
                row["strobe_ranges"] = [[lo, hi] for lo, hi, t in caps
                                        if re.search(r"strobe|pulse|random|flash", t, re.I)
                                        and not re.search(r"no strobe|strobe off", t, re.I)] or None
            if role in ("wheel", "gobo") and caps:
                row["slots"] = _slots(caps, role)
            if role == "speed" and caps:
                first = str(caps[0][2]).lower()
                row["fast_first"] = True if "fast" in first else (False if "slow" in first else None)
            rows.append(row)
        for n, row in enumerate(rows, start=1):
            row["n"] = n
        modes.append({"name": m["name"], "channel_count": len(rows),
                      "channels": [r["label"] for r in rows], "detail": rows})
    typ = d["type"]
    if typ == "light" and (uses_laser or uses_sfx):
        typ = "laser" if uses_laser else "other"
    return [{"manufacturer": d["manufacturer"], "model": d["model"],
             "type": typ.title(), "fx_kind": "light" if typ == "light" else typ,
             "modes": modes}]


def round_trip(draft: dict) -> dict:
    """The draft as Jarvis will store it: each channel's function is what
    the effects classifier and the role table make of it, so the review
    table shows exactly what will happen."""
    d = clean_draft(draft)
    parsed = fixlib.apply_fx(to_parsed(d)[0])
    for m, pm in zip(d["modes"], parsed["modes"]):
        for c, row in zip(m["channels"], pm["detail"]):
            c["function"] = _ROLE_FUNCTION.get(row["role"], c["function"]) \
                if row["role"] not in ("unused",) or c["function"] == "unused" else c["function"]
    kind = parsed.get("fx_kind") or ""
    if d["type"] == "light" and kind:
        d["type"] = kind if kind in TYPES else "other"
    return d


# ---------------------------------------------------------------------------
# reading a chart
# ---------------------------------------------------------------------------
_MODE_RE = re.compile(r"^\s*(\d{1,3})\s*[- ]?\s*(?:ch(?:annels?)?\.?|chan\.?)\b(?!\s*\d)", re.I)
# the separator between the two values: a dash, "to", or a symbol-font
# arrow that PDFs extract as a private-use character (Chauvet's is U+F0F3)
_SEP = r"(?:-|–|—|~|⇔|↔|to|\.\.|[\ue000-\uf8ff])"
_RANGE_RE = re.compile(rf"^\s*(\d{{1,3}})\s*{_SEP}\s*(\d{{1,3}})\s+(.+?)\s*$", re.I)
_INLINE_RE = re.compile(rf"\s+(\d{{1,3}})\s*{_SEP}\s*(\d{{1,3}})\b\s*(.*)$", re.I)
_RUN_RE = re.compile(r"^\s*(\d{3})\s?(\d{3})\s+(.+?)\s*$")        # "000009 Off" (a PDF table)
_CHAN_RE = re.compile(r"^\s*(\d{1,3})\s*[.):]?\s+([A-Za-z][^\n]{0,60}?)\s*$")


def read_offline(text: str) -> dict:
    """The usual DMX chart layouts, without a model."""
    modes: list[dict] = []
    mode = None
    last_no = 0
    chan = None
    pending: list[list] = []          # values seen before their channel's row
    for raw in str(text or "")[:MAX_TEXT].splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _MODE_RE.match(line)
        if m and not _RANGE_RE.match(line) and not _RUN_RE.match(line):
            mode = {"name": f"{int(m.group(1))}-channel", "channels": []}
            modes.append(mode)
            last_no, chan, pending = 0, None, []
            continue
        r = _RANGE_RE.match(line) or _RUN_RE.match(line)
        if r:
            row = [int(r.group(1)), int(r.group(2)), re.split(r"\s{2,}", r.group(3).strip())[0]]
            if chan is not None:
                chan["ranges"].append(row)
            elif mode is not None:
                pending.append(row)           # a PDF table cell centred vertically
            continue
        c = _CHAN_RE.match(line)
        if c:
            no = int(c.group(1))
            if not 1 <= no <= 512:
                continue
            if mode is None or no <= last_no and no == 1 and mode["channels"]:
                mode = {"name": "", "channels": []}
                modes.append(mode)
            if no != last_no + 1 and mode["channels"]:
                continue                           # not a channel row
            name = c.group(2).strip()
            ranges = []
            inline = _INLINE_RE.search(" " + name)
            if inline:                                 # "1 Off/On  000-009 Off"
                name = name[:max(0, inline.start() - 1)].strip()
                lo, hi, rest = int(inline.group(1)), int(inline.group(2)), inline.group(3).strip()
                if lo <= 255 and hi <= 255 and not (lo == 0 and hi == 255 and not rest):
                    ranges.append([lo, hi, re.split(r"\s{2,}", rest)[0] or name])
            name = re.sub(r"\s{2,}.*$", "", name).strip()
            if not name or name.endswith((".", ":", "!", "?")) or len(name.split()) > 6:
                continue                           # an instruction, not a channel
            chan = {"name": name, "function": _guess(name), "ranges": pending + ranges}
            pending = []
            mode["channels"].append(chan)
            last_no = no
    modes = [m for m in modes if m["channels"]]
    # A manual also has numbered steps; when some block carries value
    # ranges, the chart is the blocks that do.
    if any(c["ranges"] for m in modes for c in m["channels"]):
        modes = [m for m in modes if any(c["ranges"] for c in m["channels"])]
    # A multi-language manual repeats the chart in every language: the same
    # channel count and the same values is the same chart - keep the first.
    seen, unique = set(), []
    for m in modes:
        sig = (len(m["channels"]),
               tuple((lo, hi) for c in m["channels"] for lo, hi, _t in c["ranges"]))
        if sig in seen or any(s[0] == sig[0] and set(sig[1]) <= set(s[1]) and sig[1] for s in seen):
            continue
        seen.add(sig)
        unique.append(m)
    modes = unique
    for m in modes:
        m["name"] = m["name"] or f"{len(m['channels'])}-channel"
    return {"modes": modes, "via": "offline",
            "warnings": [] if modes else ["no DMX chart found - paste just the chart, or add an AI key"]}


_TEXT_KINDS = [("confetti", r"confetti|funfetti|streamer"), ("laser", r"\blaser\b"),
               ("co2", r"\bco\s?2\b(?! ?free)(?!.*requires no co ?2)"), ("flame", r"\bflame"),
               ("spark", r"cold spark|spark machine|sparkular"), ("haze", r"\bhazer\b"),
               ("fog", r"\bfog machine|\bfogger\b|smoke machine"), ("bubble", r"bubble machine"),
               ("snow", r"snow machine")]


def _kind_from_text(text: str) -> str:
    """What the manual is about, from words only an effect's manual uses
    often (the first pages: title and description)."""
    head = str(text or "")[:6000].lower()
    best, hits = "light", 0
    for kind, pattern in _TEXT_KINDS:
        n = len(re.findall(pattern, head))
        if n >= 2 and n > hits:
            best, hits = kind, n
    return best


def _guess(name: str) -> str:
    low = name.lower().strip()
    fine = re.match(r"^fine\s+(\w+)", low)
    if fine:
        low = f"{fine.group(1)} fine"               # "Fine Pan" -> "Pan fine"
    role = channel_role(low)
    if role == "macro" and not re.search(r"colou?r", low):
        return "setting"                           # a movement macro is not colour
    return _ROLE_FUNCTION.get(role, "setting")


SCHEMA = {
    "type": "object",
    "properties": {
        "manufacturer": {"type": "string"},
        "model": {"type": "string"},
        "type": {"type": "string", "enum": list(TYPES)},
        "modes": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "channels": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "function": {"type": "string", "enum": list(FUNCTIONS)},
                "ranges": {"type": "array", "items": {"type": "object", "properties": {
                    "from": {"type": "integer"}, "to": {"type": "integer"},
                    "label": {"type": "string"}}, "required": ["from", "to", "label"]}},
            }, "required": ["name", "function"]}},
        }, "required": ["name", "channels"]}},
    },
    "required": ["manufacturer", "model", "type", "modes"],
}

PROMPT = (
    "You read lighting and special-effects fixture manuals and extract the DMX chart exactly. "
    "Return every DMX mode with its channels in order. For each channel give the manual's name, "
    "the closest function from the list, and every value range with the manual's words "
    "(e.g. 0-9 'Off', 10-255 'On'). Never invent channels or values; if the text has no chart, "
    "return no modes. Use 'setting' for maintenance, programmes, sound or auto channels, "
    "'fx fire' for the channel that launches confetti / opens a CO2 valve / makes flame or sparks, "
    "'fx arm' for safety / enable / pre-heat channels, 'fog output' for fog or haze output, "
    "and 'laser output' for a laser's on/off or intensity.")


def read(text: str, manufacturer: str = "", model: str = "", offline: bool = False) -> dict:
    """Text of a manual -> a draft (not stored)."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("paste the DMX chart, or drop the manual's PDF")
    draft = None
    if not offline:
        from . import config, llm
        if config.LLM_API_KEY:
            try:
                draft = llm.structured(
                    [{"role": "system", "content": PROMPT},
                     {"role": "user", "content": text[:MAX_TEXT]}],
                    "fixture_from_manual", "The fixture's DMX chart", SCHEMA, temperature=0.1)
                draft["via"] = "ai"
            except Exception as exc:          # noqa: BLE001 - fall back, and say so
                draft = read_offline(text)
                draft["warnings"] = [f"the AI could not read it ({exc}); used the offline reader"] \
                    + draft.get("warnings", [])
    if draft is None:
        draft = read_offline(text)
    if manufacturer:
        draft["manufacturer"] = manufacturer
    if model:
        draft["model"] = model
    draft.setdefault("manufacturer", manufacturer)
    draft.setdefault("model", model)
    draft.setdefault("type", fixlib.fx_kind(manufacturer, model, "", []) or _kind_from_text(text))
    if draft["type"] in ("", "light"):
        draft["type"] = fixlib.fx_kind(manufacturer, model, "", []) or _kind_from_text(text)
    warnings = list(draft.get("warnings") or [])
    try:
        out = round_trip(draft)
    except ValueError as exc:
        return {"manufacturer": draft.get("manufacturer") or "", "model": draft.get("model") or "",
                "type": "light", "modes": [], "via": draft.get("via", ""),
                "warnings": warnings + [str(exc)]}
    out["warnings"] = warnings
    return out
