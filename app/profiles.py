"""Fixture definition library - DATA only, no controller logic.

Adding a new fixture never touches engine, patch, merge or output code:
append one dict to PROFILES below and run install() (main.py does it on
boot).  This module holds the rich definition - manufacturer, model, DMX
modes, per-channel function, value range, default and bit depth - and
compiles the channel LABELS into the existing SQLite fixture DB
(app/fixtures.py) that the engine already consumes:

    profile mode -> ["Pan (16-bit)", "Pan fine", "Tilt", ...]
        -> engine.channel_role() maps each label to an engine role
        -> engine pairs "<role>" with "<role>_fine" and splits ONE
           logical 16-bit value across both DMX bytes (build_frames)

So the split of responsibilities is:

    profiles.py    WHAT a fixture is (definition data, per manufacturer)
    fixtures.py    WHERE definitions live (SQLite persistence + search)
    engine.py      HOW to drive any fixture (generic, shared logic)
    artnet/sacn.py HOW bytes reach the wire (transport, shared sender API)

Verified against open-fixture-library.org JSON exports (2019-2022 data).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "profile"

# ---------------------------------------------------------------------------
# channel authoring
# ---------------------------------------------------------------------------

def ch(name: str, bits: int = 8, default: int = 0, vmin: int | None = None,
       vmax: int | None = None, role: str | None = None,
       invert: bool = False, light_from: int = 0) -> dict:
    """One DMX channel function.

    name    function label as the fixture manual prints it (the engine
            derives its generic role from this via channel_role())
    bits    8 = single byte, 16 = coarse + fine pair (compiled to two
            label rows: "<name> (16-bit)" + "<name> fine")
    default value the fixture sits at when un-driven (0 = NoFunction /
            Open for wheels and strobe, centred for pan/tilt fixtures
            that declare it)
    vmin    value range the function responds to (0..255 typical,
            0..65535 for 16-bit parameters)
    invert  True when higher DMX = lower physical value (e.g. zoom:
            0 = wide 17 deg, 255 = narrow 12 deg)
    role    optional documentation hint; the engine still derives its
            own role from the label - definitions never drive logic
    light_from  for the channels that GATE light (shutter/strobe), the
            lowest DMX value through which light passes.  Most fixtures
            use 0 (0 = open, higher = strobing); the Chauvet Intimidator
            is the exception - its Strobe channel reads 0-3 as CLOSED
            and 4-7 as Open (OFL defaultValue 4).  The engine needs this
            to open/close a dimmer-less fixture honestly instead of
            writing 0 and wondering why the lamp stays dark.
    """
    if bits not in (8, 16):
        raise ValueError(f"{name}: bits must be 8 or 16")
    if vmin is None:
        vmin = 0
    if vmax is None:
        vmax = 65535 if bits == 16 else 255
    if not 0 <= light_from <= 255:
        raise ValueError(f"{name}: light_from must be 0..255")
    return {"name": name, "bits": bits, "default": default,
            "min": vmin, "max": vmax, "role": role, "invert": bool(invert),
            "light_from": int(light_from)}


# ---------------------------------------------------------------------------
# the library - append fixtures here, nothing else needs changing
# ---------------------------------------------------------------------------

PROFILES: list[dict] = [
    {
        "manufacturer": "CHAUVET DJ",
        "model": "Intimidator Spot 260",
        "notes": "75W LED moving head spot; 12-17 deg beam; 540 deg pan / "
                 "270 deg tilt; fixed colour wheel, 7-gobo wheel, 3-facet "
                 "prism, motorised zoom.  Source: OFL 2019.",
        "modes": [
            {"name": "14ch", "channels": [
                ch("Pan", bits=16),
                ch("Tilt", bits=16),
                ch("Pan/Tilt Speed"),
                ch("Color Wheel"),
                ch("Gobo Wheel"),
                ch("Gobo Rotation"),
                ch("Prism"),
                ch("Zoom", invert=True),
                ch("Dimmer"),
                # OFL: 0-3 Closed, 4-7 Open, 8+ strobe, default 4 (Open)
                ch("Strobe", default=4, light_from=4),
                ch("Function", role="unused"),      # maintenance/reset
                ch("Movement Macro", role="macro"),
            ]},
            {"name": "8ch", "channels": [
                ch("Pan"),
                ch("Tilt"),
                ch("Color Wheel"),
                ch("Gobo Wheel"),
                ch("Gobo Rotation"),
                ch("Prism"),
                ch("Zoom", invert=True),
                ch("Strobe", default=4, light_from=4),
            ]},
        ],
    },
    {
        "manufacturer": "CHAUVET DJ",
        "model": "SlimPAR Pro RGBA",
        "notes": "42x 1W RGBA LED wash; 21 deg beam.  Source: OFL 2022.",
        "modes": [
            {"name": "4ch", "channels": [
                ch("Red"), ch("Green"), ch("Blue"), ch("Amber"),
            ]},
            {"name": "5ch", "channels": [
                ch("Dimmer"), ch("Red"), ch("Green"), ch("Blue"),
                ch("Amber"),
            ]},
            {"name": "10ch", "channels": [
                ch("Dimmer"), ch("Red"), ch("Green"), ch("Blue"),
                ch("Amber"), ch("Color Macros", role="macro"),
                ch("Strobe"), ch("Auto Programs"),
                ch("Auto Program Speed"), ch("Dimmer Curve", role="unused"),
            ]},
        ],
    },
    {
        "manufacturer": "CHAUVET DJ",
        "model": "SlimPAR T12 USB",
        "notes": "12x 3W RGB LED wash with sound-active mode.  "
                 "Source: OFL 2022.",
        "modes": [
            {"name": "3ch", "channels": [ch("Red"), ch("Green"), ch("Blue")]},
            {"name": "8ch", "channels": [
                ch("Red"), ch("Green"), ch("Blue"),
                ch("Color Macros", role="macro"),
                ch("Strobe / Speed / Sensitivity"),
                ch("Mode"),                          # 0 = DMX, 1 = auto/sound
                ch("Dimmer"),
                ch("Dimmer Speed", role="unused"),
            ]},
        ],
    },
]


# ---------------------------------------------------------------------------
# compiling definitions -> DB label rows
# ---------------------------------------------------------------------------

def labels_for(mode: dict) -> list[str]:
    """One mode's channel list in the flat label format the engine eats.

    A 16-bit channel compiles to TWO label rows: the coarse half keeps
    the manual's name ("Pan (16-bit)" or plain "Pan"), the fine half
    gets a " fine" suffix - exactly the shapes engine.channel_role()
    pairs up (pan -> pan, pan_fine).
    """
    labels: list[str] = []
    for channel in mode.get("channels") or []:
        if channel["bits"] == 16:
            labels.append(f"{channel['name']} (16-bit)")
            labels.append(f"{channel['name']} fine")
        else:
            labels.append(channel["name"])
    return labels


def detail_for(mode: dict) -> list[dict]:
    """Per-channel definition rows (function, role, range, default, bits)
    for API/tests - the DB keeps the compiled labels only."""
    rows = []
    n = 0
    for channel in mode.get("channels") or []:
        rows.append({"n": n + 1, "name": channel["name"],
                     "role": channel.get("role"),
                     "bits": channel["bits"],
                     "min": channel["min"], "max": channel["max"],
                     "default": channel["default"],
                     "light_from": channel.get("light_from", 0),
                     "invert": channel["invert"]})
        n += 1 if channel["bits"] == 8 else 2
        rows[-1]["positions"] = [n if channel["bits"] == 8 else n - 1, n]
    return rows


def defaults_for(mode: dict, role_of) -> dict[str, int]:
    """{engine role: default DMX value} for one mode, or {}.

    `role_of` is the engine's channel_role() label->role mapper, passed
    in so this module stays free of engine imports (it is DATA only).
    Defaults are what an UN-driven channel should sit at, so the merge
    stage can write them instead of 0 - a shutter channel whose manual
    default is "open" must not be pinned shut just because nobody
    touched it.
    """
    if not mode:
        return {}
    out: dict[str, int] = {}
    for channel in mode.get("channels") or []:
        role = role_of(labels_for({"channels": [channel]})[0])
        if role in ("raw", "unused") or role in out:
            continue
        out[role] = int(channel["default"])
    return out


def open_values_for(mode: dict, role_of) -> dict[str, int]:
    """{engine role: lowest DMX value through which light passes}."""
    if not mode:
        return {}
    out: dict[str, int] = {}
    for channel in mode.get("channels") or []:
        role = role_of(labels_for({"channels": [channel]})[0])
        if role in ("raw", "unused") or role in out:
            continue
        out[role] = int(channel.get("light_from", 0))
    return out


def get(manufacturer: str, model: str) -> dict | None:
    """One definition by exact (manufacturer, model), else None."""
    want_man = str(manufacturer or "").strip().lower()
    want_mod = str(model or "").strip().lower()
    for profile in PROFILES:
        if (profile["manufacturer"].lower() == want_man
                and profile["model"].lower() == want_mod):
            return profile
    return None


def find(query: str) -> list[dict]:
    """In-memory search of the definition library (substring match)."""
    q = str(query or "").strip().lower()
    if not q:
        return [dict(p) for p in PROFILES]
    words = q.split()
    hits = []
    for profile in PROFILES:
        hay = f"{profile['manufacturer']} {profile['model']}".lower()
        if all(word in hay for word in words):
            hits.append(dict(profile))
    return hits


def export(profile: dict) -> dict:
    """Wire-safe shape of one definition (for API/tests)."""
    modes = []
    for mode in profile.get("modes") or []:
        modes.append({
            "name": mode["name"],
            "channel_count": len(labels_for(mode)),
            "channels": labels_for(mode),
            "detail": detail_for(mode),
        })
    return {"manufacturer": profile["manufacturer"],
            "model": profile["model"],
            "source": SOURCE,
            "notes": profile.get("notes", ""),
            "modes": modes}


# ---------------------------------------------------------------------------
# persistence - idempotent seeding into the shared fixture DB
# ---------------------------------------------------------------------------

def install(db_path: Path) -> dict:
    """Seed every profile into the SQLite fixture DB (idempotent).

    Fixtures are keyed (manufacturer, model, source); an existing row -
    from any source, including an earlier install - is left alone, and a
    fixture row that somehow lost its modes gets them restored.  Returns
    counts so callers/tests can report what happened.
    """
    from app import fixtures as fixdb          # local import: fixtures is
    # also the schema owner, and profiles must not be imported by it.
    added = modes_added = skipped = 0
    now = datetime.now(timezone.utc).isoformat()
    with fixdb.db(db_path) as conn:
        for profile in PROFILES:
            cur = conn.execute(
                "INSERT OR IGNORE INTO fixtures"
                " (manufacturer, model, source, imported_at) VALUES (?,?,?,?)",
                (profile["manufacturer"], profile["model"], SOURCE, now))
            if cur.rowcount:
                fid = cur.lastrowid
                added += 1
            else:
                row = conn.execute(
                    "SELECT id FROM fixtures WHERE manufacturer=? AND model=?",
                    (profile["manufacturer"], profile["model"])).fetchone()
                if row is None:
                    skipped += 1
                    continue
                fid = row["id"]
            have = conn.execute(
                "SELECT COUNT(*) AS n FROM modes WHERE fixture_id=?",
                (fid,)).fetchone()["n"]
            if have:
                skipped += 1
                continue
            for mode in profile.get("modes") or []:
                labels = labels_for(mode)
                conn.execute(
                    "INSERT INTO modes"
                    " (fixture_id, name, channel_count, channels)"
                    " VALUES (?,?,?,?)",
                    (fid, mode["name"], len(labels), json.dumps(labels)))
                modes_added += 1
    return {"fixtures": added, "modes": modes_added, "skipped": skipped,
            "library": len(PROFILES)}
