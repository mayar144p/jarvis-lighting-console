"""Fixture database: SQLite + GDTF importer.

The GDTF library (https://gdtf-share.com) is the open, manufacturer-fed
fixture database - drop .gdtf files into jarvis/fixtures_inbox/ and run
`python tools/import_gdtf.py`. Everything is parsed locally with the
standard library only.

GDTF files are ZIP archives containing one XML document named "GDTF".
Relevant nodes (per the GDTF spec):

  <GDTF><Manufacturer/><Name/><DMXModes>
    <DMXMode Name="Mode1"> <DMXChannels>
      <DMXChannel Offset="3">                <- relative DMX address(es)
        <LogicalChannel Attribute="ColorAdd_R"> ...
"""
from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .engine_support import ROLES, channel_role  # noqa: F401  (re-exported)
from xml.etree import ElementTree as ET

# Friendly names for the GDTF attribute vocabulary.
ATTRIBUTE_LABELS = {
    "ColorAdd_R": "Red", "ColorAdd_G": "Green", "ColorAdd_B": "Blue",
    "ColorAdd_W": "White", "ColorAdd_Amber": "Amber", "ColorAdd_UV": "UV",
    "ColorMix_C": "Cyan", "ColorMix_M": "Magenta", "ColorMix_Y": "Yellow",
    "ColorMix_CMY": "CMY", "ColorMix_RGB": "RGB",
    "Dimmer": "Dimmer", "Pan": "Pan", "Tilt": "Tilt", "Shutter": "Shutter",
    "Iris": "Iris", "Focus": "Focus", "Zoom": "Zoom", "Frost": "Frost",
    "Gobo1": "Gobo 1", "Gobo2": "Gobo 2", "Gobo3": "Gobo 3", "Gobo4": "Gobo 4",
    "Rotate1": "Gobo Rotate", "WheelColor": "Colour Wheel",
    "Intensity": "Intensity", "AnimationRotate": "Animation Rotate",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer TEXT NOT NULL,
    model TEXT NOT NULL,
    source TEXT DEFAULT '',
    imported_at TEXT DEFAULT '',
    UNIQUE(manufacturer, model, source)
);
CREATE TABLE IF NOT EXISTS modes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    channel_count INTEGER NOT NULL DEFAULT 0,
    channels TEXT NOT NULL DEFAULT '[]',
    -- Per-channel RANGES, one row per channel slot: the DMX end and the
    -- physical end.  Added after the fact: the GDTF importer used to
    -- compile every profile down to a bare list of channel NAMES, so a
    -- light's real travel was thrown away and pan could only be set as an
    -- abstract 0-255.  A Chauvet Intimidator's 14-channel mode says tilt
    -- is -117..+117 degrees, which is 234 of travel and NOT the 270 the
    -- visualiser assumed - so this is not a nicety, it is a correction.
    detail TEXT NOT NULL DEFAULT '[]'
);
"""

# Bump when parse_gdtf learns something new from a file (3: shutter open
# values and colour/gobo wheel slots; 4: strobe ranges and the direction
# of the pan/tilt speed channel, for the visualiser; 5: special effects and
# lasers get their own safe roles).  On start, fixtures imported by an
# older parser are re-read from their .gdtf files (refresh_imports), so an
# update reaches the lights you already have without downloading again.
PARSER_VERSION = 5

# Columns added after the first release.  `connect` adds them to an
# existing database, so an old fixtures.db is upgraded in place rather than
# needing a rebuild - the operator's library is real work, not a cache.
MIGRATIONS = (("modes", "detail", "TEXT NOT NULL DEFAULT '[]'"),)


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite disables foreign keys PER CONNECTION and the default is
    # off, so the schema's `ON DELETE CASCADE` was declared and never
    # enforced: deleting a profile left its modes orphaned in the
    # table forever, invisible to every query that joins from fixtures.
    # Measured, not assumed - `PRAGMA foreign_keys` returned 0, and a
    # deleted fixture's mode was still there after a commit.
    #
    # It has to be set on EVERY connection, which is why it lives in
    # this one factory rather than at import time.  A pragma set once
    # would silently stop applying the moment a second connection
    # opened - and this module opens one per operation by design, so it
    # would stop applying immediately.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that post-date the first schema, in place.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a
    new column has to be added separately or every existing fixtures.db
    would keep the old shape and the new code would fail on a column
    that does not exist.  The operator's library is real work, so it is
    upgraded rather than rebuilt.
    """
    for table, column, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            conn.commit()


@contextmanager
def db(db_path: Path):
    """Open + commit + close (closing matters on Windows file locks).

    Every commit invalidates the read caches, so a caller that caches
    search()/get() results (the engine's _FIXTURE_CACHE) can never serve
    a definition that has just been replaced by an import.
    """
    conn = connect(db_path)
    changed = False
    try:
        yield conn
        conn.commit()
    finally:
        # only a write invalidates: a pure read (the 40 Hz tick reads
        # ranges and overrides) must not drop every cache it just filled
        changed = conn.total_changes > 0
        conn.close()
        if changed:
            invalidate_cache()


# --------------------------------------------------------------------------
# read cache invalidation
# --------------------------------------------------------------------------
# Callbacks registered by modules that memoise lookups.  Kept as callbacks
# rather than a direct import so fixtures.py stays free of app imports
# (and so an engine -> fixtures -> engine cycle is impossible).
_CACHE_LISTENERS: list = []


def on_cache_clear(callback) -> None:
    """Register a callback to run whenever the fixture DB is written."""
    if callback not in _CACHE_LISTENERS:
        _CACHE_LISTENERS.append(callback)


def invalidate_cache() -> None:
    """Drop every cached lookup. Called after each write transaction."""
    for callback in list(_CACHE_LISTENERS):
        try:
            callback()
        except Exception:                 # noqa: BLE001 - a bad listener
            pass                          # must never break a write


def seed_generics(db_path: Path) -> int:
    """A few built-in generic profiles so the demo works before any import."""
    generics = [
        ("Generic", "LED PAR 4ch", [("4ch RGBW", ["Dimmer", "Red", "Green", "Blue"])]),
        ("Generic", "LED PAR 8ch", [("8ch RGBWA+UV", ["Dimmer", "Red", "Green", "Blue",
                                                      "White", "Amber", "UV", "Speed"])]),
        ("Generic", "Moving Head Spot 16ch",
         [("16ch Advanced", ["Dimmer", "Shutter", "Red", "Green", "Blue", "White",
                             "Pan", "Tilt", "Pan/Tilt Speed", "Gobo 1", "Gobo 1 Rotate",
                             "Prism", "Frost", "Focus", "Dimmer Speed", "Reset"])]),
        ("Generic", "RGBW Bar 12ch",
         [("12ch Zone", ["Zone 1 Dimmer", "Zone 1 Red", "Zone 1 Green", "Zone 1 Blue",
                         "Zone 2 Dimmer", "Zone 2 Red", "Zone 2 Green", "Zone 2 Blue",
                         "Zone 3 Dimmer", "Zone 3 Red", "Zone 3 Green", "Zone 3 Blue"])]),
    ]
    added = 0
    with db(db_path) as conn:
        for manufacturer, model, modes in generics:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fixtures (manufacturer, model, source, imported_at)"
                " VALUES (?,?,?,?)",
                (manufacturer, model, "built-in", datetime.now(timezone.utc).isoformat()))
            if cur.rowcount == 0:
                continue
            fid = cur.lastrowid
            for name, channels in modes:
                conn.execute(
                    "INSERT INTO modes (fixture_id, name, channel_count, channels) VALUES (?,?,?,?)",
                    (fid, name, len(channels), json.dumps(channels)))
            added += 1
    return added


def search(db_path: Path, query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip().lower()
    with db(db_path) as conn:
        if query:
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM fixtures WHERE lower(manufacturer || ' ' || model) LIKE ?"
                " ORDER BY manufacturer, model LIMIT ?", (like, limit)).fetchall()
            if not rows:  # fall back to word-by-word matching
                words = [w for w in query.split() if w]
                rows = conn.execute(
                    "SELECT * FROM fixtures ORDER BY manufacturer, model").fetchall()
                rows = [r for r in rows
                        if all(w in (r["manufacturer"] + " " + r["model"]).lower() for w in words)][:limit]
        else:
            rows = conn.execute(
                "SELECT * FROM fixtures ORDER BY manufacturer, model LIMIT ?", (limit,)).fetchall()
        return [_with_modes(conn, r) for r in rows]


def get(db_path: Path, fixture_id: int) -> dict | None:
    with db(db_path) as conn:
        row = conn.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)).fetchone()
        return _with_modes(conn, row) if row else None


def count(db_path: Path) -> int:
    try:
        with db(db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0]
    except sqlite3.Error:
        return 0


def _with_modes(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    modes = conn.execute(
        "SELECT name, channel_count, channels FROM modes WHERE fixture_id = ?"
        " ORDER BY channel_count, name", (row["id"],)).fetchall()
    return {
        "id": row["id"],
        "manufacturer": row["manufacturer"],
        "model": row["model"],
        "source": row["source"],
        "modes": [
            {"name": m["name"], "channel_count": m["channel_count"],
             "channels": json.loads(m["channels"])}
            for m in modes
        ],
    }


# --------------------------------------------------------------------------
# GDTF parsing
# --------------------------------------------------------------------------

def _local(tag: str) -> str:
    """Strip an optional XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    found = _children(node, name)
    return found[0] if found else None


def _label(attribute: str | None) -> str:
    if not attribute:
        return "?"
    return ATTRIBUTE_LABELS.get(attribute, attribute.replace("_", " ").strip())


def _description_entry(zf: zipfile.ZipFile) -> str | None:
    """Find the GDTF document inside the archive.

    The spec puts it at the archive root as `description.xml`, and every
    real file follows that.  Older Jarvis tooling and several tools in
    the wild name the entry `something.gdtf` or plain `GDTF` instead, so
    those are tried too - but they are tried AFTER the spec location, and
    the deciding test is whether the bytes actually contain a <GDTF
    root, so a mis-named .xml (there are several in real archives) is
    skipped rather than parsed as a fixture.

    This ordering is the whole reason the first live import failed: the
    old check only matched a name ending in "GDTF", which no
    spec-compliant file uses, and the synthetic fixture in the self-test
    was written with the name that check wanted.
    """
    names = zf.namelist()
    preferred: list[str] = []
    fallback: list[str] = []
    for name in names:
        low = name.lower()
        if low == "description.xml":
            preferred.append(name)
        elif low.endswith((".gdtf", ".xml")) or low in ("gdtf", "description"):
            fallback.append(name)
    for name in preferred + fallback:
        try:
            head = zf.read(name)[:16384]
        except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
            continue
        if b"<GDTF" in head:
            return name
    return None


def role_ranges(db_path: Path, manufacturer: str, model: str,
                mode: str) -> dict:
    """{role: {min, max, unit, bits}} for one fixture mode.

    This is what turns "pan = 128" into "pan = 0 degrees" - the value an
    operator is actually thinking in.  The range comes from the fixture's
    own file, so a light whose tilt travels 234 degrees says 234, not the
    270 the visualiser assumed.

    A role's range is taken from its COARSE channel: a 16-bit pair is one
    function over two slots, and the fine half inherits it (the two are the
    same logical value split in two), so asking for a range from the fine
    slot would report half of the travel.
    """
    row = match_model(db_path, model, manufacturer)
    if row is None:
        return {}
    modes = list_modes(db_path, int(row["id"]))
    chosen = None
    if mode:
        for m in modes:
            if (m["name"] or "").strip().lower() == str(mode).strip().lower():
                chosen = m
                break
    if chosen is None and modes:
        chosen = modes[0]
    if chosen is None:
        return {}
    out: dict[str, dict] = {}
    for i, d in enumerate(chosen.get("detail") or []):
        if not isinstance(d, dict):
            continue
        role = d.get("role") or channel_role(
            chosen["channels"][i] if i < len(chosen["channels"]) else "")
        if role in ("raw", "unused", ""):
            continue
        if d.get("coarse") is False and role in out:
            continue                      # the coarse half already spoke
        lo, hi = d.get("phys_from"), d.get("phys_to")
        unit = "raw"
        if lo is not None and hi is not None:
            span = abs(hi - lo)
            if role in ("pan", "tilt") or (
                    abs(span - 360.0) < 0.5 and role in ("pan", "tilt",
                                                          "gobo_rot")):
                # A light's own travel, in the unit the operator thinks in.
                unit = "degree"
            elif span < 2.0:
                # A colour or a gobo wheel is a 0..1 POSITION, not a
                # measurement.  Calling that "0 to 1 degrees" would be
                # worse than saying nothing at all.  A span of exactly
                # zero is not even that - the file declares no range - so
                # it goes back to "raw" rather than pretending.
                unit = "position" if span else "raw"
        out[role] = {
            "min": lo, "max": hi, "unit": unit,
            "bits": d.get("bits") or 8,
            "wheel": d.get("wheel") or "",
            "open_from": d.get("open_from"),
            "strobe_ranges": d.get("strobe_ranges") or None,
            "fast_first": d.get("fast_first"),
            # special effects: what "fire / armed / on" and "off" are, and
            # how long the device may run in one go
            "on_value": d.get("on_value"), "off_value": d.get("off_value"),
            "fx_kind": d.get("fx_kind"), "max_s": d.get("max_s"),
            "caps": d.get("caps") or None,
            "slots": d.get("slots") or None,
            "dmx_from": d.get("dmx_from"), "dmx_to": d.get("dmx_to"),
            "inverted": bool(lo is not None and hi is not None and hi < lo),
        }
    for role, keys in get_overrides(db_path, manufacturer, model, mode).items():
        row = out.setdefault(role, {"min": None, "max": None, "unit": "raw", "bits": 8})
        row.update(keys)
        if "open_from" in keys:
            row["open_user"] = True
    return out


def _num_pair(low, high):
    """Two GDTF range numbers as floats, or (None, None).

    GDTF writes ranges as "0/2" for a 16-bit pair and as plain decimals
    for a physical range, and often gives only ONE end of a physical range
    (a colour channel is `PhysicalFrom 0, PhysicalTo 1` but a shutter may
    carry just `Default`).  A half-read range would be worse than none, so
    anything unparseable yields None for BOTH ends.
    """
    def one(value):
        if value is None:
            return None
        text = str(value).strip().split("/")[0]
        try:
            return float(text)
        except ValueError:
            return None

    lo = one(low)
    if lo is None:
        return (None, None)
    return (lo, one(high))


def _dmx_byte(text) -> int | None:
    """A GDTF DMX value ("4/1", "1024/2") as the coarse byte, or None."""
    if text is None or str(text).strip().lower() in ("", "none"):
        return None
    head, _, res = str(text).strip().partition("/")
    try:
        value = int(float(head))
        width = int(res) if res else 1
    except ValueError:
        return None
    if width > 1:
        value >>= 8 * (width - 1)
    return max(0, min(255, value))


_OPEN_WORDS = ("open", "on", "light")


def _gdtf_caps(logical) -> list[list] | None:
    """[[from, to, name]] for every ChannelSet (or function without
    sets) - what each range of the channel does, by the maker's words."""
    if logical is None:
        return None
    starts: list[tuple[int, str]] = []
    for func in _children(logical, "ChannelFunction"):
        f_from = _dmx_byte(func.get("DMXFrom"))
        sets = _children(func, "ChannelSet")
        if not sets and f_from is not None:
            starts.append((f_from, (func.get("Name") or func.get("Attribute") or "")[:48]))
        for cset in sets:
            s_from = _dmx_byte(cset.get("DMXFrom"))
            if s_from is None:
                s_from = f_from
            if s_from is not None:
                starts.append((s_from, (cset.get("Name") or func.get("Name") or "")[:48]))
    if not starts:
        return None
    starts.sort(key=lambda t: t[0])
    out = []
    for i, (lo, name) in enumerate(starts):
        hi = (starts[i + 1][0] - 1) if i + 1 < len(starts) else 255
        if hi >= lo:
            out.append([lo, hi, name])
    return out


def _gdtf_strobe_ranges(logical) -> list[list[int]] | None:
    """The DMX ranges where a shutter channel strobes (not open/closed),
    so the visualiser flickers only when the light really does."""
    if logical is None:
        return None
    marks, hits = [], []
    for func in _children(logical, "ChannelFunction"):
        start = _dmx_byte(func.get("DMXFrom"))
        if start is None:
            continue
        marks.append(start)
        words = f"{func.get('Attribute') or ''} {func.get('Name') or ''}".lower()
        if ("strobe" in words or "pulse" in words or "random" in words) \
                and "open" not in words and "closed" not in words:
            hits.append(start)
    if not hits:
        return None
    marks = sorted(set(marks))
    out = []
    for start in hits:
        later = [m for m in marks if m > start]
        out.append([start, (later[0] - 1) if later else 255])
    return out


def _gdtf_open_value(dmx_ch, logical) -> int | None:
    """Where a shutter lets light through, from the file itself.

    Fixtures disagree: most read 0 as open, but a Chauvet Intimidator
    reads 0-3 as CLOSED.  GDTF says so in two ways: the channel's
    `Highlight` value (full output), or a ChannelFunction / ChannelSet
    named "Open".
    """
    found = _dmx_byte(dmx_ch.get("Highlight"))
    if found is not None:
        return found
    if logical is None:
        return None
    def is_open(name: str) -> bool:
        # "Open", "Shutter open", "Open (no strobe)", "Shutter Open 1"...
        return (name in _OPEN_WORDS or re.search(r"\bopen\b", name) is not None) \
            and not re.search(r"\bclos", name)
    for func in _children(logical, "ChannelFunction"):
        name = (func.get("Name") or "").strip().lower()
        if is_open(name):
            got = _dmx_byte(func.get("DMXFrom"))
            if got is not None:
                return got
        for cset in _children(func, "ChannelSet"):
            name = (cset.get("Name") or "").strip().lower()
            if is_open(name):
                got = _dmx_byte(cset.get("DMXFrom"))
                if got is None:
                    got = _dmx_byte(func.get("DMXFrom"))
                if got is not None:
                    return got
    return None


def _xyY_hex(text) -> str | None:
    """A GDTF slot colour ("x,y,Y" in CIE 1931) as #rrggbb at full
    brightness, or None."""
    try:
        x, y, _Y = (float(v) for v in str(text).split(","))
    except (TypeError, ValueError):
        return None
    if y <= 0:
        return None
    X, Y, Z = x / y, 1.0, (1 - x - y) / y
    rgb = [3.2406 * X - 1.5372 * Y - 0.4986 * Z,
           -0.9689 * X + 1.8758 * Y + 0.0415 * Z,
           0.0557 * X - 0.2040 * Y + 1.0570 * Z]
    rgb = [max(0.0, c) for c in rgb]
    top = max(rgb) or 1.0
    out = []
    for c in rgb:
        c /= top
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        out.append(max(0, min(255, round(c * 255))))
    return "#%02x%02x%02x" % tuple(out)


def _gdtf_wheels(fixture_type) -> dict[str, list[dict]]:
    """{wheel name: [{name, hex}] by slot index (1-based)}."""
    wheels: dict[str, list[dict]] = {}
    node = _child(fixture_type, "Wheels")
    if node is None:
        return wheels
    for wheel in _children(node, "Wheel"):
        slots = []
        for slot in _children(wheel, "Slot"):
            slots.append({"name": (slot.get("Name") or "").strip(),
                          "hex": _xyY_hex(slot.get("Color"))})
        wheels[wheel.get("Name") or ""] = slots
    return wheels


def _gdtf_slots(logical, wheels: dict) -> list[dict] | None:
    """The slots a wheel channel can land on: [{name, value, from, to,
    hex}], each value in the middle of its own range, so a button lands
    on the colour and not on the split or the spin next to it."""
    if logical is None:
        return None
    marks: list[int] = []
    found: list[tuple[int, dict]] = []
    for func in _children(logical, "ChannelFunction"):
        f_from = _dmx_byte(func.get("DMXFrom"))
        if f_from is not None:
            marks.append(f_from)
        wheel = wheels.get(func.get("Wheel") or "")
        for cset in _children(func, "ChannelSet"):
            start = _dmx_byte(cset.get("DMXFrom"))
            if start is None:
                start = f_from
            if start is None:
                continue
            marks.append(start)
            index = cset.get("WheelSlotIndex")
            if wheel is None or not index or not str(index).strip().isdigit():
                continue
            i = int(index)
            if not 1 <= i <= len(wheel):
                continue
            name = (cset.get("Name") or "").strip() or wheel[i - 1]["name"]
            found.append((start, {"name": name or f"Slot {i}",
                                  "hex": wheel[i - 1]["hex"], "slot": i}))
    if not found:
        return None
    marks = sorted(set(marks))
    out, seen = [], set()
    for start, row in sorted(found, key=lambda t: t[0]):
        later = [m for m in marks if m > start]
        end = (later[0] - 1) if later else 255
        key = (row["slot"], row["name"].lower())
        if key in seen:
            continue                        # the same slot again (spin ranges)
        seen.add(key)
        out.append({**row, "from": start, "to": end, "value": (start + end) // 2})
    return out


def parse_gdtf(path: Path) -> list[dict]:
    """Return [{'manufacturer','model','modes':[{name,channel_count,channels}]}]."""
    with zipfile.ZipFile(path) as zf:
        xml_name = _description_entry(zf)
        if xml_name is None:
            names = ", ".join(zf.namelist()[:6]) or "(empty archive)"
            raise ValueError(
                "no GDTF document inside the archive (looked for "
                f"description.xml; entries: {names})")
        root = ET.fromstring(zf.read(xml_name))

    # GDTF 1.0 nests everything under <FixtureType>, and carries the maker
    # and the model as ATTRIBUTES of it - not as child elements, and not
    # on <GDTF>.  Earlier drafts used child elements, and the very first
    # live import landed in the library as model "rev9044" with a single
    # 0-channel mode called "Default", because all three lookups missed.
    # Both layouts are read; the spec one first.
    # NOTE: a leaf Element is falsy in ElementTree, so never use `or` on nodes.
    fixture_type = _child(root, "FixtureType") or root

    def _identity(attr: str, legacy: str) -> str:
        value = (fixture_type.get(attr) or "").strip()
        if value:
            return value
        node = _child(fixture_type, legacy)
        if node is not None and (node.text or "").strip():
            return node.text.strip()
        return ""

    manufacturer = _identity("Manufacturer", "Manufacturer")
    model = (_identity("Name", "Name") or _identity("LongName", "LongName")
             or _identity("ShortName", "ShortName"))
    if not model:
        model = path.stem

    wheels = _gdtf_wheels(fixture_type)
    modes: list[dict] = []
    modes_node = _child(fixture_type, "DMXModes") or _child(root, "DMXModes")
    if modes_node is not None:
        for mode in _children(modes_node, "DMXMode"):
            mode_name = mode.get("Name") or "Default"
            channels_node = _child(mode, "DMXChannels")
            entries: list[tuple[int, int, str]] = []   # (start offset, width, label)
            details: list[dict] = []
            if channels_node is not None:
                for index, dmx_ch in enumerate(_children(channels_node, "DMXChannel"), start=1):
                    offset_attr = (dmx_ch.get("Offset") or "").strip()
                    if offset_attr and offset_attr.lower() != "none":
                        offsets = [int(o) for o in offset_attr.split(",")
                                   if o.strip().lstrip("-").isdigit()]
                        start = min(offsets) if offsets else index
                        width = len(offsets) or 1
                    else:
                        start, width = index, 1  # spec default "None": fall back to order

                    logical = _child(dmx_ch, "LogicalChannel")
                    attribute = (logical.get("Attribute")
                                 if logical is not None else None)
                    label = _label(attribute)
                    func = _child(logical, "ChannelFunction") \
                        if logical is not None else None
                    if func is not None and func.get("OriginalAttribute"):
                        label = func.get("OriginalAttribute")
                    entries.append((start, width, label))

                    # THE RANGE, which is the part that used to be thrown
                    # away.  GDTF allows it in two places and real files
                    # use both: `LogicalChannel/@Min|Max` in the older
                    # drafts, and - in everything the Share actually serves
                    # - `ChannelFunction/@PhysicalFrom|PhysicalTo` paired
                    # with `@DMXFrom`.  Reading only the first found
                    # nothing at all, which is why a profile could say
                    # "Tilt" and offer nothing but an abstract 0-255, and
                    # why the visualiser's 270-degree assumption was never
                    # checked against a real file.  A Chauvet Intimidator
                    # 14ch says tilt is -117..+117, which is 234 of
                    # travel.
                    dmx_from = dmx_to = None
                    phys_from = phys_to = None
                    wheel = None
                    if logical is not None:
                        # `LogicalChannel/@Min|Max` is the PHYSICAL range -
                        # the angle the head actually travels.  Reading it
                        # into the DMX fields instead put a -90..90 TILT
                        # into `dmx_from`, where it is a plausible-looking
                        # number that means nothing.
                        phys_from, phys_to = _num_pair(
                            logical.get("Min"), logical.get("Max"))
                    if func is not None:
                        # `ChannelFunction/@DMXFrom|DMXTo` is the DMX end,
                        # and the `0/2` in `DMXFrom="0/2"` is a 16-bit
                        # PAIR, not a fraction.
                        pair = _num_pair(func.get("DMXFrom"), func.get("DMXTo"))
                        if pair[0] is not None:
                            dmx_from, dmx_to = pair
                        pair = _num_pair(func.get("PhysicalFrom"),
                                         func.get("PhysicalTo"))
                        if pair[0] is not None:
                            phys_from, phys_to = pair
                        wheel = func.get("Wheel") or wheel
                    if phys_from is None and phys_to is None \
                            and logical is not None:
                        phys_from, phys_to = _num_pair(
                            logical.get("PhysicalFrom"),
                            logical.get("PhysicalTo"))
                    details.append({
                        # The ROLE, not the pretty attribute name.  Storing
                        # the name in a field called "role" is a trap: every
                        # consumer then compares "Pan" against "pan", misses,
                        # and silently reports no range on a channel that
                        # has one.
                        "role": channel_role(label),
                        "attribute": attribute or "",
                        "label": label,
                        "bits": 8 * width,
                        "dmx_from": dmx_from, "dmx_to": dmx_to,
                        "phys_from": phys_from, "phys_to": phys_to,
                        "wheel": wheel,
                        "open_from": _gdtf_open_value(dmx_ch, logical),
                        "strobe_ranges": _gdtf_strobe_ranges(logical),
                        "slots": _gdtf_slots(logical, wheels),
                        "name": label,
                        "caps": _gdtf_caps(logical),
                    })

            channel_count = max((s + w - 1 for s, w, _ in entries), default=0)
            slots = [""] * channel_count
            for start, width, label in entries:
                if start - 1 < channel_count:
                    slots[start - 1] = label + (" (16-bit)" if width > 1 else "")
                for k in range(1, width):
                    if start - 1 + k < channel_count:
                        slots[start - 1 + k] = f"{label} fine"
            channels = [s if s else f"ch{i + 1}" for i, s in enumerate(slots)]

            # The detail list is per DMX SLOT, not per DMXChannel: a 16-bit
            # pair is one channel function and two slots, so the fine half
            # inherits the same range and is marked low-res.  Index drift
            # here would attach a fixture's tilt range to its zoom.
            by_slot: list[dict] = [{} for _ in range(channel_count)]
            for (start, width, _lbl), detail in zip(entries, details):
                for k in range(width):
                    slot = start - 1 + k
                    if not 0 <= slot < channel_count:
                        continue
                    row = dict(detail)
                    row["coarse"] = (k == 0)
                    by_slot[slot] = row
            for slot, row in enumerate(by_slot):
                row.setdefault("label", channels[slot])
                row.setdefault("role", channel_role(row.get("label", "")))
                row["n"] = slot + 1

            modes.append({
                "name": mode_name,
                "channel_count": channel_count,
                "channels": channels,
                "detail": by_slot,
            })

    if not modes:  # some files only describe a single implicit mode
        modes = [{"name": "Default", "channel_count": 0, "channels": []}]
    return [{"manufacturer": manufacturer.strip(), "model": model.strip(), "modes": modes}]


def import_file(db_path: Path, path: Path) -> dict:
    """Parse one fixture file and upsert it into the database: a .gdtf,
    a QLC+ .qxf or an Open Fixture Library .json."""
    path = Path(path)
    if path.suffix.lower() in (".qxf", ".json"):
        from . import fixlib
        return store_parsed(db_path, fixlib.parse_file(path), path.name)
    return store_parsed(db_path, parse_gdtf(path), path.name)


def store_parsed(db_path: Path, parsed: list[dict], source: str) -> dict:
    """Upsert parsed fixtures (the parse_gdtf shape) under `source`.

    Every fixture passes through fixlib.apply_fx on the way in, whatever
    its format: a fog machine's output or a laser's power is never stored
    as a light's dimmer."""
    from . import fixlib
    parsed = [fixlib.apply_fx(item) for item in parsed]
    results = []
    with db(db_path) as conn:
        for item in parsed:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fixtures (manufacturer, model, source, imported_at)"
                " VALUES (?,?,?,?)",
                (item["manufacturer"], item["model"], source,
                 datetime.now(timezone.utc).isoformat()))
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT id FROM fixtures WHERE manufacturer = ? AND model = ? AND source = ?",
                    (item["manufacturer"], item["model"], source)).fetchone()
                fid = row["id"] if row else None
                # INSERT OR IGNORE leaves the ORIGINAL timestamp behind, so
                # a profile that was just replaced still claimed the date
                # of the version it superseded.  An import time that lies
                # is worse than none: it is how you decide which of two
                # files to keep.
                if fid is not None:
                    conn.execute(
                        "UPDATE fixtures SET imported_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), fid))
            else:
                fid = cur.lastrowid
            if fid is None:
                continue
            # `modes` has no unique key, so `INSERT OR REPLACE` on it is a
            # plain INSERT: re-importing a file that is already installed
            # ADDED a second copy of every mode, and the mode dropdown
            # grew a duplicate each time you downloaded a profile you
            # already had.  Worse, the stale copy sorted first, so the
            # fixture kept being served the OLD channel list - the re-import
            # appeared to succeed and change nothing.  Match on the name
            # and replace deliberately.
            for mode in item["modes"]:
                conn.execute(
                    "DELETE FROM modes WHERE fixture_id = ? AND name = ?",
                    (fid, mode["name"]))
                conn.execute(
                    "INSERT INTO modes (fixture_id, name, channel_count, "
                    "channels, detail) VALUES (?,?,?,?,?)",
                    (fid, mode["name"], mode["channel_count"],
                     json.dumps(mode["channels"]),
                     json.dumps(mode.get("detail") or [])))
            # The source file is recorded on the FIXTURE, and the patch
            # sheet reads it, so a profile that came from rev9044.gdtf
            # says so in the export rather than looking hand-made.
            results.append({"fixture_id": fid, "manufacturer": item["manufacturer"],
                            "model": item["model"], "modes": len(item["modes"])})
    return {"file": source, "imported": results}


def refresh_imports(db_path: Path, folders) -> dict:
    """Re-read installed GDTF fixtures when the parser has improved.

    Each fixture remembers its source file name; the file is looked for
    in `folders` (the GDTF Share cache, the inbox).  Patched heads keep
    working because a re-import replaces modes by name.  Cheap when there
    is nothing to do: one read of the stored version.
    """
    with db(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute("SELECT value FROM meta WHERE key = 'parser_version'").fetchone()
        have = int(row["value"]) if row and str(row["value"]).isdigit() else 0
        if have >= PARSER_VERSION:
            return {"refreshed": 0, "missing": 0, "errors": []}
        sources = [r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM fixtures WHERE lower(source) LIKE '%.gdtf'"
            " OR lower(source) LIKE '%.qxf' OR lower(source) LIKE '%.json'"
            " OR source LIKE 'jarvis:%'")]
    refreshed, missing, errors = 0, 0, []
    for name in sources:
        if ":" in name and name.split(":", 1)[0] in ("ofl", "qlc", "jarvis"):
            try:                          # a bundled library fixture
                from . import fixlib
                src, key = name.split(":", 1)
                store_parsed(db_path, fixlib.load(src, key), name)
                refreshed += 1
            except Exception as exc:      # noqa: BLE001 - one bad fixture
                errors.append(f"{name}: {exc}")
            continue
        path = next((Path(f) / name for f in folders
                     if f and (Path(f) / name).is_file()), None)
        if path is None:
            missing += 1
            continue
        try:
            import_file(db_path, path)
            refreshed += 1
        except Exception as exc:          # noqa: BLE001 - one bad file
            errors.append(f"{name}: {exc}")
    with db(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('parser_version', ?)",
                     (str(PARSER_VERSION),))
    return {"refreshed": refreshed, "missing": missing, "errors": errors}


def _override_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS channel_overrides (manufacturer TEXT NOT NULL,"
                 " model TEXT NOT NULL, mode TEXT NOT NULL, role TEXT NOT NULL,"
                 " key TEXT NOT NULL, value TEXT, PRIMARY KEY (manufacturer, model, mode, role, key))")


def set_override(db_path: Path, manufacturer: str, model: str, mode: str,
                 role: str, key: str, value) -> None:
    """What the operator found out about a real light (e.g. the value that
    opens its shutter).  Kept apart from the imported detail, so a library
    update or a re-import never throws it away."""
    with db(db_path) as conn:
        _override_table(conn)
        if value is None:
            conn.execute("DELETE FROM channel_overrides WHERE manufacturer=? AND model=? AND mode=?"
                         " AND role=? AND key=?", (manufacturer or "", model or "", mode or "", role, key))
        else:
            conn.execute("INSERT OR REPLACE INTO channel_overrides VALUES (?,?,?,?,?,?)",
                         (manufacturer or "", model or "", mode or "", role, key, json.dumps(value)))


def get_overrides(db_path: Path, manufacturer: str, model: str, mode: str) -> dict:
    """{role: {key: value}} set by the operator for one fixture mode."""
    with db(db_path) as conn:
        _override_table(conn)
        rows = conn.execute("SELECT role, key, value FROM channel_overrides WHERE manufacturer=?"
                            " AND model=? AND mode=?", (manufacturer or "", model or "", mode or "")).fetchall()
    out: dict = {}
    for r in rows:
        try:
            out.setdefault(r["role"], {})[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            continue
    return out


def _motion_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS motion (manufacturer TEXT NOT NULL,"
                 " model TEXT NOT NULL, pan_s REAL, tilt_s REAL,"
                 " PRIMARY KEY (manufacturer, model))")


def get_motion(db_path: Path, manufacturer: str, model: str) -> dict | None:
    """A fixture model's measured movement: seconds for a full pan and a
    full tilt at top speed (Settings calibration), or None."""
    with db(db_path) as conn:
        _motion_table(conn)
        row = conn.execute("SELECT pan_s, tilt_s FROM motion WHERE manufacturer = ?"
                           " AND model = ?", (manufacturer or "", model or "")).fetchone()
    return {"pan_s": row["pan_s"], "tilt_s": row["tilt_s"]} if row else None


def set_motion(db_path: Path, manufacturer: str, model: str,
               pan_s: float | None, tilt_s: float | None) -> dict | None:
    """Store (or with both None, forget) a model's measured movement."""
    with db(db_path) as conn:
        _motion_table(conn)
        if pan_s is None and tilt_s is None:
            conn.execute("DELETE FROM motion WHERE manufacturer = ? AND model = ?",
                         (manufacturer or "", model or ""))
            return None
        conn.execute("INSERT OR REPLACE INTO motion (manufacturer, model, pan_s, tilt_s)"
                     " VALUES (?,?,?,?)", (manufacturer or "", model or "", pan_s, tilt_s))
    return {"pan_s": pan_s, "tilt_s": tilt_s}


def list_modes(db_path: Path, fixture_id: int) -> list[dict]:
    """Every mode of one fixture: id, name, footprint, and channel labels.

    This is what the fixture editor draws.  A profile with no modes is a
    stub (a mis-parsed import), so the labels can legitimately be empty -
    which callers must show as "no channels", not as "a fixture with none".
    """
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, channel_count, channels, detail FROM modes "
            "WHERE fixture_id = ? ORDER BY id", (int(fixture_id),)).fetchall()
    out = []
    for row in rows:
        try:
            labels = json.loads(row["channels"])
        except (TypeError, ValueError):
            labels = []
        try:
            detail = json.loads(row["detail"] or "[]")
        except (TypeError, ValueError):
            detail = []
        out.append({
            "id": row["id"], "name": row["name"],
            "channel_count": row["channel_count"] or 0,
            "channels": [str(x) for x in labels] if isinstance(labels, list) else [],
            "detail": detail if isinstance(detail, list) else [],
        })
    return out


# The label that produces each role, for the editor's dropdown.  Role is
# DERIVED from the label everywhere in this codebase, so the editor
# rewrites the label rather than inventing a second source of truth - a
# profile that stored roles separately would drift the first time anything
# read it through a different path.
ROLE_LABEL = {
    "dimmer": "Dimmer", "red": "Red", "green": "Green", "blue": "Blue",
    "white": "White", "amber": "Amber", "uv": "UV", "cyan": "Cyan",
    "magenta": "Magenta", "yellow": "Yellow",
    "pan": "Pan", "pan_fine": "Pan fine", "tilt": "Tilt",
    "tilt_fine": "Tilt fine", "speed": "Speed",
    "shutter": "Shutter", "strobe": "Strobe", "wheel": "Color Wheel",
    "gobo": "Gobo 1", "gobo_rot": "Gobo 1 Rotate", "zoom": "Zoom",
    "focus": "Focus", "iris": "Iris", "frost": "Frost", "prism": "Prism",
    "macro": "Macro",
}
# Roles an operator can assign.  `raw` and `unused` are excluded on
# purpose: `raw` is the state being FIXED, and offering it as a choice
# would make "this channel does nothing" a normal, clickable answer
# rather than something you have to mean.
ASSIGNABLE_ROLES = tuple(sorted(
    r for r in ROLES if r not in ("raw", "unused")))


def set_channel_label(db_path: Path, mode_id: int, index: int,
                      label: str) -> dict:
    """Rename one channel of one mode, and report the role it now maps to.

    THE ONLY WAY TO GIVE A `raw` CHANNEL A CONTROL.  A channel whose
    vendor label is not in the role table ("CTO", "Motor Speed", "Custom
    7") carries DMX and responds to the light, but the desk has no control
    for it - and that state was invisible everywhere until the channel
    sheet.  Rewriting the label is the fix, because role is derived from
    the label: one source of truth, and a profile that stored roles
    separately would drift the first time anything read it another way.
    """
    from .engine_support import channel_role

    text = str(label or "").strip()
    with db(db_path) as conn:
        row = conn.execute("SELECT channels FROM modes WHERE id = ?",
                           (int(mode_id),)).fetchone()
        if row is None:
            raise ValueError(f"no mode {mode_id}")
        try:
            labels = json.loads(row["channels"])
        except (TypeError, ValueError):
            raise ValueError("this mode has no readable channel list") from None
        if not isinstance(labels, list):
            raise ValueError("this mode has no readable channel list")
        i = int(index)
        if not 0 <= i < len(labels):
            raise ValueError(f"channel must be 1..{len(labels)}")
        was_role = channel_role(labels[i])
        labels[i] = text
        conn.execute("UPDATE modes SET channels = ? WHERE id = ?",
                     (json.dumps(labels), int(mode_id)))
    invalidate_cache()
    return {"mode_id": int(mode_id), "channel": i + 1,
            "label": text, "was_role": was_role,
            "role": channel_role(text),
            "summary": (f"channel {i + 1}: {was_role} -> "
                        f"{channel_role(text)} ({text or 'unnamed'})")}


def create_profile(db_path: Path, manufacturer: str, model: str,
                   mode: str, channels: list[str],
                   ranges: dict | None = None) -> dict:
    """Add a fixture profile from a list of channel names.

    For a brand with no GDTF on the Share.  Without this, an unknown light
    can only be added as a raw dimmer - you get intensity and nothing
    else, forever - which is the state the whole library question started
    from.  Ten lines of channel names and the fixture is fully drivable.

    `ranges` gives the physical span of a channel, as
    `{"pan": [-270, 270]}`, and is what makes a hand-written MOVER usable
    rather than merely addressable: without it the desk has no idea how far
    the head travels and can only offer an abstract 0-65535, which is the
    same gap a real GDTF fills.

    Re-running with the same manufacturer+model REPLACES the mode, so
    correcting a profile is the same gesture as creating it.
    """
    manufacturer, model = str(manufacturer).strip(), str(model).strip()
    mode = str(mode or "Default").strip() or "Default"
    names = [str(c).strip() for c in (channels or []) if str(c).strip()]
    if not manufacturer or not model:
        raise ValueError("a profile needs a manufacturer and a model")
    if not names:
        raise ValueError("a profile needs at least one channel")
    if len(names) > 512:
        raise ValueError("a DMX mode cannot exceed 512 channels")
    # A channel may carry its own travel inline: `Tilt = -117..117`.  One
    # list, order preserved, and the range cannot drift onto a different
    # channel the way a second parallel list would.
    inline: dict[str, tuple[float, float]] = {}
    cleaned: list[str] = []
    for entry in names:
        text, sep, tail = entry.partition("=")
        text = text.strip()
        if not text:
            continue
        if sep and tail.strip():
            inline[text] = _parse_span(tail.strip(), text)
        cleaned.append(text)
    names = cleaned
    if not names:
        raise ValueError("a profile needs at least one channel")
    merged = dict(inline)
    for key, span in (ranges or {}).items():
        role = channel_role(key) or str(key).strip().lower()
        try:
            lo, hi = (float(x) for x in span)
        except (TypeError, ValueError):
            raise ValueError(f"range for {key} must be [min, max]") from None
        if lo == hi:
            raise ValueError(f"range for {key} has no travel")
        merged[role] = (lo, hi)
    detail = _detail_from(names, merged)
    with db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO fixtures (manufacturer, model, source, "
            "imported_at) VALUES (?,?,?,?)",
            (manufacturer, model, "manual",
             datetime.now(timezone.utc).isoformat()))
        row = conn.execute(
            "SELECT id FROM fixtures WHERE manufacturer = ? AND model = ?",
            (manufacturer, model)).fetchone()
        if row is None:
            raise ValueError("could not create that fixture")
        fid = row["id"]
        existing = conn.execute(
            "SELECT id FROM modes WHERE fixture_id = ? AND name = ?",
            (fid, mode)).fetchone()
        if existing:
            conn.execute("UPDATE modes SET channel_count = ?, channels = ?, "
                         "detail = ? WHERE id = ?",
                         (len(names), json.dumps(names), json.dumps(detail),
                          existing["id"]))
            mode_id = existing["id"]
            verb = "replaced"
        else:
            cur = conn.execute(
                "INSERT INTO modes (fixture_id, name, channel_count, "
                "channels, detail) VALUES (?,?,?,?,?)",
                (fid, mode, len(names), json.dumps(names),
                 json.dumps(detail)))
            mode_id = cur.lastrowid
            verb = "added"
    invalidate_cache()
    mapped = sum(1 for c in names if channel_role(c) not in ("raw", "unused"))
    spans = sum(1 for d in detail if d.get("phys_from") is not None)
    return {"fixture_id": fid, "mode_id": mode_id, "mode": mode,
            "channels": len(names), "controllable": mapped,
            "uncontrollable": len(names) - mapped,
            "ranges": spans,
            "summary": (f"{verb} {manufacturer} {model} [{mode}] — "
                        f"{len(names)} channel(s), {mapped} controllable"
                        + (f", {len(names) - mapped} not yet mapped"
                           if mapped < len(names) else "")
                        + (f", {spans} with a physical range"
                           if spans else ", no physical ranges"))}


def _parse_span(text: str, who: str) -> tuple[float, float]:
    """`'-117..117'` or `'-117..-117.5'` -> a pair of floats.

    `..` is the separator rather than `-` because every negative number
    starts with a minus: `-270..270` split on the sign gives three pieces
    and a range that reads as garbage.
    """
    parts = text.replace("..", "\x00").split("\x00")
    if len(parts) != 2:
        raise ValueError(
            f"the travel for {who} should look like -117..117")
    try:
        lo, hi = (float(p.strip()) for p in parts)
    except (TypeError, ValueError):
        raise ValueError(
            f"the travel for {who} should look like -117..117") from None
    if lo == hi:
        raise ValueError(f"the travel for {who} has no range in it")
    return (lo, hi)


def _detail_from(names: list[str], ranges: dict | None) -> list[dict]:
    """A per-slot detail list for a hand-written profile.

    Kept in the same shape an import produces, so `role_ranges` cannot tell
    where a profile came from - a hand-written profile that reports ranges
    differently from an imported one would need two code paths forever
    after.
    """
    want = {}
    for key, span in (ranges or {}).items():
        role = channel_role(key) or str(key).strip().lower()
        try:
            lo, hi = (float(x) for x in span)
        except (TypeError, ValueError):
            raise ValueError(f"range for {key} must be [min, max]") from None
        if lo == hi:
            raise ValueError(f"range for {key} has no travel")
        want[role] = (lo, hi)
    out = []
    for i, label in enumerate(names):
        role = channel_role(label)
        lo, hi = want.get(role, (None, None))
        out.append({"role": role, "attribute": label, "label": label,
                    "bits": 8, "coarse": True, "n": i + 1,
                    "dmx_from": 0 if lo is None else None,
                    "dmx_to": 255 if lo is None else None,
                    "phys_from": lo, "phys_to": hi, "wheel": None})
    return out


def set_channel_range(db_path: Path, mode_id: int, index: int,
                      low=None, high=None) -> dict:
    """Give one channel a physical range, or take it away with None.

    The other half of `set_channel_label`: naming a channel "Tilt" says
    what it is, this says HOW FAR IT GOES.  Together they are the whole
    difference between a head you can aim at 90 degrees and one you can
    only send 18000 to and hope.
    """
    with db(db_path) as conn:
        row = conn.execute("SELECT channels, detail FROM modes WHERE id = ?",
                           (int(mode_id),)).fetchone()
        if row is None:
            raise ValueError(f"no mode {mode_id}")
        try:
            names = json.loads(row["channels"])
        except (TypeError, ValueError):
            raise ValueError("this mode has no readable channel list")
        try:
            detail = json.loads(row["detail"] or "[]")
        except (TypeError, ValueError):
            detail = []
        if not isinstance(names, list):
            raise ValueError("this mode has no readable channel list")
        i = int(index)
        if not 0 <= i < len(names):
            raise ValueError(f"channel must be 1..{len(names)}")
        while len(detail) <= i:
            detail.append({"role": channel_role(names[len(detail)]),
                           "label": names[len(detail)], "bits": 8,
                           "coarse": True, "n": len(detail) + 1,
                           "phys_from": None, "phys_to": None,
                           "wheel": None})
        entry = detail[i] if isinstance(detail[i], dict) else {}
        entry.setdefault("label", names[i])
        entry.setdefault("role", channel_role(names[i]))
        entry["n"] = i + 1
        if low is None and high is None:
            entry["phys_from"] = entry["phys_to"] = None
            phrase = "range cleared"
        else:
            try:
                lo, hi = (float(x) for x in (low, high))
            except (TypeError, ValueError):
                raise ValueError("a range needs two numbers, min and max"
                                 ) from None
            if lo == hi:
                raise ValueError("a range needs some travel in it")
            entry["phys_from"], entry["phys_to"] = lo, hi
            phrase = f"range {lo:g}..{hi:g}"
        detail[i] = entry
        conn.execute("UPDATE modes SET detail = ? WHERE id = ?",
                     (json.dumps(detail), int(mode_id)))
    invalidate_cache()
    return {"mode_id": int(mode_id), "channel": i + 1,
            "min": entry["phys_from"], "max": entry["phys_to"],
            "role": entry.get("role"),
            "summary": f"channel {i + 1}: {phrase}"}


def mode_channels(db_path: Path, manufacturer: str, model: str,
                  mode: str) -> list[str]:
    """The channel LABELS of one fixture mode, in DMX order.

    A patched head stores its `map` - the resolved ROLE per channel - but
    not the vendor's own name for that channel, and the role alone is not
    enough to answer "what is channel 7?" to an operator.  GDTF files and
    the curated library both keep the labels, so read them back here.

    Returns [] when the fixture is unknown, which callers must treat as
    "no labels available" and not as "no channels".
    """
    manufacturer = str(manufacturer or "").strip()
    model = str(model or "").strip()
    mode = str(mode or "").strip()
    if not model:
        return []
    # match_model, not a strict filter: a head records the manufacturer it
    # was PATCHED with, and a superseded profile is stored under a name
    # the Share spells differently.  A strict filter here returned no
    # labels at all, so the channel sheet showed the ROLE ("raw") where
    # the operator needs the vendor's own channel name.
    row = match_model(db_path, model, manufacturer)
    if row is None:
        return []
    with db(db_path) as conn:
        modes = conn.execute(
            "SELECT name, channel_count, channels FROM modes "
            "WHERE fixture_id = ?", (row["id"],)).fetchall()
    chosen = None
    if mode:
        for m in modes:
            if (m["name"] or "").strip().lower() == mode.lower():
                chosen = m
                break
    if chosen is None and modes:
        chosen = modes[0]
    if chosen is None:
        return []
    try:
        labels = json.loads(chosen["channels"])
    except (TypeError, ValueError):
        return []
    return [str(x) for x in labels] if isinstance(labels, list) else []


def match_model(db_path: Path, model: str, manufacturer: str = "") -> dict | None:
    """Find one fixture row by model, tolerating how sources spell it.

    Three passes, strictest first:
      1. exact (manufacturer, model) - what a freshly patched head records;
      2. model alone, case-insensitive - the manufacturer was renamed;
      3. model with spacing and case ignored, any manufacturer.

    Pass 3 exists because a head records the name it was PATCHED with and
    re-resolves its channel map from the library on every load, so when a
    profile is superseded by an equivalent one whose name is spelled
    differently - "SlimPAR T12 USB" curated, "Slim Par T12 USB" from the
    GDTF Share - a strict lookup leaves the head as `raw`: patched,
    listed, and unable to do anything.  It is a real failure, it is
    silent, and a renamed profile must never be able to cause it.

    Search cannot do pass 3: `LIKE '%slimpar t12 usb%'` does not match
    "Slim Par T12 USB", so this compares in Python over a list the
    database returns whole.  That is fine because callers cache the
    result, so it runs once per distinct fixture name.
    """
    model = str(model or "").strip()
    manufacturer = str(manufacturer or "").strip()
    if not model:
        return None
    with db(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, manufacturer, model, source FROM fixtures").fetchall()]
    for row in rows:
        if (row["model"].strip().lower() == model.lower()
                and (not manufacturer
                     or row["manufacturer"].strip().lower()
                     == manufacturer.lower())):
            return row
    for row in rows:
        if row["model"].strip().lower() == model.lower():
            return row
    tight = _squash(model)
    if tight:
        for row in rows:
            if _squash(row["model"]) == tight:
                return row
    return None


def _squash(text: str) -> str:
    """Lower-cased, spaces and punctuation removed.

    Sources disagree about spacing for the same product: the GDTF Share
    publishes "Slim Par T12 USB" where a curated profile library wrote
    "SlimPAR T12 USB".  Comparing those directly leaves the SAME LIGHT in
    the picker twice under two names, which is the exact confusion the
    replace-on-download path exists to prevent.
    """
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _match_rows(db_path: Path, manufacturer: str, model: str,
                loose: bool) -> list[sqlite3.Row]:
    """Rows for one fixture - exactly, or by model alone when loose."""
    manufacturer, model = str(manufacturer).strip(), str(model).strip()
    with db(db_path) as conn:
        if not loose:
            return conn.execute(
                "SELECT id, manufacturer, model, source FROM fixtures "
                "WHERE manufacturer = ? AND model = ?",
                (manufacturer, model)).fetchall()
        # Loose matching is a squash, not a LIKE, and cannot be pushed into
        # SQL portably - so it filters in Python.  The library is hundreds
        # of rows, not millions, and correctness beats cleverness here.
        want = _squash(model)
        return [r for r in conn.execute(
            "SELECT id, manufacturer, model, source FROM fixtures").fetchall()
            if _squash(r["model"]) == want]


def model_sources(db_path: Path, manufacturer: str, model: str,
                  loose: bool = False) -> list[str]:
    """The `source` of every stored copy of one model.

    `source` is the file the profile was imported from, so this is how a
    caller tells "already have this exact revision" from "a different
    revision is superseded" - two cases that look identical if all you
    do is count rows.

    `loose` matches on the model alone, ignoring case, spacing and
    manufacturer.  That is what the GDTF Share needs, because the Share
    and the curated library spell the same product differently.
    """
    return [r["source"] or "" for r in _match_rows(db_path, manufacturer, model, loose)]


def remove_model(db_path: Path, manufacturer: str, model: str,
                 loose: bool = False) -> list[str]:
    """Drop every stored copy of one fixture, modes included.

    `fixtures` is keyed on (manufacturer, model, source), so importing a
    second revision of the same light would otherwise leave the picker
    showing the same model twice with different modes - and the operator
    has no way to tell which one is current.  Callers that replace a
    fixture (the GDTF Share downloader does) run this first, so the
    library holds exactly one entry per model: the newest thing fetched.

    Returns "manufacturer/model" for each row removed (so the caller can
    tell the operator exactly what was superseded), or [] if there was
    nothing to remove.
    """
    with db(db_path) as conn:
        manufacturer, model = str(manufacturer).strip(), str(model).strip()
        if loose:
            want = _squash(model)
            found = [r for r in conn.execute(
                "SELECT id, manufacturer, model FROM fixtures").fetchall()
                if _squash(r["model"]) == want]
        else:
            found = conn.execute(
                "SELECT id, manufacturer, model FROM fixtures "
                "WHERE manufacturer = ? AND model = ?",
                (manufacturer, model)).fetchall()
        for row in found:
            conn.execute("DELETE FROM modes WHERE fixture_id = ?", (row["id"],))
            conn.execute("DELETE FROM fixtures WHERE id = ?", (row["id"],))
    return [f"{r['manufacturer']} {r['model']}" for r in found]


def import_directory(db_path: Path, folder: Path) -> dict:
    """Import every .gdtf/.zip/.qxf/OFL .json in a folder.

    Returns {imported, errors, scanned} - always all three keys.  Errors
    are a FIRST-class part of the result rather than a pseudo-entry
    appended to the import list, because a corrupt GDTF that only shows
    up as "0 imported" reads like a quiet no-op, and the operator then
    wonders why their fixture is missing.
    """
    out: list[dict] = []
    errors: list[dict] = []
    scanned = 0
    for path in sorted(folder.rglob("*")):
        if path.is_dir() or path.suffix.lower() not in (".gdtf", ".zip", ".qxf", ".json"):
            continue
        scanned += 1
        try:
            out.append(import_file(db_path, path))
        except Exception as exc:          # noqa: BLE001 - report and go on
            errors.append({"file": path.name, "error": str(exc)})
    return {"imported": out, "errors": errors, "scanned": scanned}
