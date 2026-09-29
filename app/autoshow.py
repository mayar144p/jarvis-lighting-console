"""Build a whole show from the rig as it stands in the room.

Three layers, so the AI only ever makes design choices and never has to
know DMX:

    analyse(eng)     where every light is and what it can do: groups by
                     type and location (the front-truss movers, the PARs on
                     the stage lip...), aim targets from the venue (the DJ,
                     the dance floor, the crowd, the back wall), and the
                     timeline (BPM, song length, markers).
    design           sections of the song (intro, build, drop...) and, per
                     section, a look for each group plus effects, strobe
                     hits and master moves.  From the AI, or from
                     fallback_design() with no key.
    compile_calls    the design as engine actions, applied as ONE undo
                     step: groups, one cue per section, and the timeline
                     (cue track, effect track, hits track, master
                     automation) laid out on bars to fit the song.
"""
from __future__ import annotations

import copy
import re

from app import fixture_kind, fxlib, llm
from app import venue as venue_mod

ROLE_NAME = {"spot": "Movers", "beam": "Beams", "wash": "Washes", "par": "PARs",
             "bar": "Bars", "generic": "Lights"}
COLOUR_MIX = ("red", "green", "blue", "cyan", "magenta", "yellow")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
AUTO_PREFIX = "Auto · "
AUTO_PAGE = 4


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "x"


def _where(head: dict, venue: dict, front: float) -> str:
    m = head.get("mount") or {}
    r = venue_mod.rig(venue, m.get("rig")) if m.get("rig") else None
    if r:
        if venue_mod.is_vertical(r):
            return "Towers"
        return r.get("name") or r["kind"].title()
    if float(head.get("y") or 0) >= 2:
        return "Overhead"
    return "Stage lip" if float(head.get("z") or 0) >= front - 1.5 else "Upstage floor"


def _caps(head: dict) -> dict:
    roles = set(head.get("map") or [])
    return {"pan_tilt": "pan" in roles and "tilt" in roles,
            "colour": "mix" if roles & set(COLOUR_MIX) else ("wheel" if "wheel" in roles else None),
            "dimmer": "dimmer" in roles,
            "gate": "shutter" if "shutter" in roles else ("strobe" if "strobe" in roles else None),
            "gobo": "gobo" in roles, "zoom": "zoom" in roles, "prism": "prism" in roles}


def _agree(values: list):
    """One capability for a set of groups: shared, or absent."""
    if all(v == values[0] for v in values):
        return values[0]
    return False if isinstance(values[0], bool) else None


def analyse(eng) -> dict:
    """The rig in words a designer uses: groups, targets and the song."""
    v = eng.venue or {}
    st = v.get("stage")
    front = (st["z"] + st["depth"]) if st else 4.0
    groups: dict[str, dict] = {}
    for h in eng.patch:
        if fixture_kind.describe(h).get("class", "light") != "light":
            continue                    # lasers and SFX are never auto-designed
        role = fixture_kind.design_role(h)
        where = _where(h, v, front)
        key = f"{role}@{_slug(where)}"
        g = groups.setdefault(key, {"name": f"{ROLE_NAME.get(role, role.title())} · {where}",
                                    "role": role, "where": where, "heads": [], "caps": None,
                                    "fx": None, "x": 0.0})
        g["heads"].append(h["head_no"])
        caps = _caps(h)
        if g["caps"] is None:
            g["caps"] = caps
        else:                                   # what EVERY head in it can do
            g["caps"] = {k: _agree([g["caps"][k], caps[k]]) for k in caps}
        fx_here = set(fxlib.available(h.get("map") or []))
        g["fx"] = fx_here if g["fx"] is None else (g["fx"] & fx_here)
        g["x"] += float(h.get("x") or 0)
    for g in groups.values():
        g["x"] = round(g["x"] / len(g["heads"]), 2)
        g["heads"].sort()
        g["fx"] = sorted(g["fx"] or [])
    # everything of one type, when it is spread over several places
    by_role: dict[str, list[int]] = {}
    for g in groups.values():
        by_role.setdefault(g["role"], []).extend(g["heads"])
    for role, heads in by_role.items():
        if sum(1 for g in groups.values() if g["role"] == role) > 1:
            parts = [g for g in groups.values() if g["role"] == role]
            groups[f"{role}@all"] = {
                "name": f"All {ROLE_NAME.get(role, role).lower()}", "role": role, "where": "everywhere",
                "heads": sorted(heads), "x": 0.0,
                "caps": {k: _agree([p["caps"][k] for p in parts]) for k in parts[0]["caps"]},
                "fx": sorted(set.intersection(*[set(p["fx"]) for p in parts]))}
    # aim targets from the room
    targets: dict[str, list[float]] = {}
    w, d, hgt = venue_mod.dims(v)
    b = venue_mod.bounds(v)
    mark = next((o for o in v.get("objects") or [] if o.get("kind") == "mark"), None)
    if mark:
        targets["dj"] = [mark["x"], float(mark.get("y") or 0) + 1.3, mark["z"]]
    elif st:
        targets["dj"] = [st["x"], st["height"] + 1.3, st["z"] + st["depth"] * 0.4]
    floor = next((z for z in v.get("zones") or [] if z["kind"] in ("dancefloor", "standing")), None)
    if floor:
        xs = [p[0] for p in floor["points"]]
        zs = [p[1] for p in floor["points"]]
        cx, cz = sum(xs) / len(xs), sum(zs) / len(zs)
        sx, sz = (max(xs) - min(xs)) / 2, (max(zs) - min(zs)) / 2
        targets.update({"floor": [cx, 0.0, cz], "floor_left": [cx - sx * 0.6, 0.0, cz],
                        "floor_right": [cx + sx * 0.6, 0.0, cz], "floor_front": [cx, 0.0, cz - sz * 0.6],
                        "floor_back": [cx, 0.0, cz + sz * 0.6], "crowd": [cx, 1.6, cz]})
    else:
        targets["floor"] = [0.0, 0.0, front + 5]
        targets["crowd"] = [0.0, 1.6, front + 5]
    if w and d:
        targets["back_wall"] = [b["x0"] + w / 2, (hgt or 6) * 0.6, b["z0"] + 0.3]
        targets["ceiling"] = [b["x0"] + w / 2, hgt or 6, (b["z0"] + b["z1"]) / 2]
    tl = eng.timeline
    audio = tl.get("audio")
    duration = float(audio["duration"]) if audio and audio.get("duration") else 0.0
    return {"groups": groups, "targets": {k: [round(c, 2) for c in t] for k, t in targets.items()},
            "bpm": float(tl.get("bpm") or 120), "duration": duration,
            "markers": list(tl.get("markers") or []), "venue": v.get("name") or v.get("template") or ""}


def describe(a: dict) -> str:
    """The analysis as a compact brief for the model."""
    lines = [f"Venue: {a['venue'] or 'room'}. Tempo {a['bpm']:g} BPM."
             + (f" Song length {a['duration']:.0f} s." if a["duration"] else " No audio: design about 3 minutes.")]
    if a["markers"]:
        lines.append("Markers: " + ", ".join(f"{m['name']}@{m['t']:.0f}s" for m in a["markers"]))
    lines.append("GROUPS (use these keys):")
    for key, g in a["groups"].items():
        c = g["caps"]
        can = [x for x, ok in (("pan/tilt", c["pan_tilt"]), ("colour", c["colour"] == "mix"),
                               ("gobo", c["gobo"]), ("zoom", c["zoom"])) if ok]
        lines.append(f"- {key}: {g['name']}, {len(g['heads'])} lights, x≈{g['x']:+.1f} m; "
                     f"can {', '.join(can) or 'intensity only'}; effects {', '.join(g['fx']) or 'none'}")
    lines.append("AIM TARGETS (only for groups with pan/tilt): " + ", ".join(a["targets"]))
    return "\n".join(lines)


DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "palette": {"type": "array", "items": {"type": "string"}, "description": "2-5 #rrggbb colours"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "bars": {"type": "integer", "description": "length in bars of 4 beats (4-64)"},
                "energy": {"type": "number", "description": "0 calm .. 1 peak"},
                "fade": {"type": "number", "description": "cue fade in seconds"},
                "master": {"type": "array", "items": {"type": "number"}, "description": "[from, to] 0-100"},
                "looks": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "group": {"type": "string"},
                        "intensity": {"type": "number"},
                        "colour": {"type": "string", "description": "#rrggbb"},
                        "aim": {"type": "string", "description": "an aim target key, or empty"},
                        "zoom": {"type": "string", "enum": ["", "narrow", "wide"]},
                        "gobo": {"type": "boolean"},
                        "fx": {"type": "string", "description": "an effect the group can run, or empty"},
                    },
                    "required": ["group", "intensity"]}},
                "hits": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["strobe", "flash"]},
                        "group": {"type": "string"},
                        "every": {"type": "string", "enum": ["beat", "half", "bar"]},
                        "last_bars": {"type": "integer", "description": "only in the last N bars (0 = whole section)"},
                    },
                    "required": ["kind", "group", "every"]}},
            },
            "required": ["name", "bars", "energy", "looks"]}},
    },
    "required": ["name", "sections"],
}

SYSTEM = """You are a lighting designer programming a show on a real rig.
You are given the rig as GROUPS (by fixture type and where they hang or
stand), AIM TARGETS in the room, and the song's tempo and length. Design
the whole show as SECTIONS of the song (e.g. Intro, Build, Drop,
Breakdown, Build 2, Drop 2, Outro), each a number of bars, and for each
section a look per group: intensity, colour, where movers aim, zoom,
gobo, and an effect the group can actually run. Add strobe/flash HITS on
the beat where the music peaks (usually the last bars of a build and the
start of a drop), and master [from, to] levels for fades.

Rules:
- Use only the group keys, target keys and effects listed. Aim only
  groups that can pan/tilt. Give colour only to groups that can colour.
- Make sections contrast: calm sections dim and slow, drops full and
  fast. Keep a coherent palette of 2-5 colours.
- Movers on a front truss suit aims at the crowd and the DJ; washes on a
  rear truss suit the dance floor; floor PARs and bars suit uplighting
  the back wall and chases; towers and blinders suit hits.
- Section bars should add up to roughly the song length."""


def _palette(brief: str) -> list[str]:
    t = brief.lower()
    table = [
        (("techno", "dark", "industrial", "berlin"), ["#ff1a1a", "#ffffff", "#2b00ff"]),
        (("house", "disco", "funk", "70s"), ["#ff00aa", "#ffb000", "#00e5ff", "#ffffff"]),
        (("chill", "lounge", "calm", "ambient", "jazz"), ["#ff8a3d", "#6a00ff", "#ffd9a8"]),
        (("wedding", "elegant", "warm", "gala"), ["#ffd9a8", "#ffb36b", "#ffffff"]),
        (("ice", "winter", "cool", "blue"), ["#00aaff", "#e6f0ff", "#5b2bff"]),
        (("edm", "festival", "rave", "hype", "big room"), ["#00ffcc", "#ff00ff", "#ffffff", "#2244ff"]),
        (("latin", "reggaeton", "carnival"), ["#ffcc00", "#ff2a55", "#00ff88"]),
        (("hip hop", "hiphop", "trap", "rap"), ["#aa00ff", "#ff0055", "#ffffff"]),
    ]
    for words, pal in table:
        if any(w in t for w in words):
            return pal
    return ["#1f4bff", "#ff00d4", "#ffffff", "#00e5ff"]


def fallback_design(a: dict, brief: str = "") -> dict:
    """A sound club show with no model: a template song structure, looks
    chosen per group from what it is and where it hangs."""
    pal = _palette(brief)
    sections = [("Intro", 16, 0.25), ("Build", 8, 0.6), ("Drop", 16, 1.0), ("Breakdown", 8, 0.35),
                ("Build 2", 8, 0.7), ("Drop 2", 16, 1.0), ("Outro", 8, 0.2)]
    if "chill" in brief.lower() or "wedding" in brief.lower() or "lounge" in brief.lower():
        sections = [("Welcome", 16, 0.3), ("Groove", 16, 0.5), ("Lift", 16, 0.7),
                    ("Groove 2", 16, 0.5), ("Close", 8, 0.25)]
    groups = a["groups"]
    targets = a["targets"]
    base = [k for k in groups if not k.endswith("@all")]
    strobey = [k for k in base if groups[k]["role"] in ("par", "bar", "wash")] or base[:1]
    out = []
    for i, (name, bars, energy) in enumerate(sections):
        looks = []
        for j, key in enumerate(base):
            g = groups[key]
            c = g["caps"]
            role = g["role"]
            colour = pal[(i + j) % len(pal)]
            look = {"group": key, "intensity": round(35 + 65 * energy)}
            if c["colour"] == "mix":
                look["colour"] = colour
            if c["pan_tilt"]:
                if energy >= 0.9:
                    look["aim"] = "crowd" if "crowd" in targets else "floor"
                elif energy >= 0.55:
                    look["aim"] = "floor_left" if (i % 2) else ("floor_right" if "floor_right" in targets else "floor")
                elif "back_wall" in targets and name.lower().startswith("break"):
                    look["aim"] = "back_wall"
                else:
                    look["aim"] = "dj" if "dj" in targets else "floor"
                look["zoom"] = "narrow" if energy >= 0.6 else "wide"
                look["gobo"] = bool(c["gobo"]) and 0.3 < energy < 0.9
            fx_pref = {
                "spot": ["circle", "figure_eight", "pan_sweep"] if energy >= 0.55 else (["tilt_bounce"] if energy >= 0.4 else []),
                "beam": ["fan_pan", "circle"] if energy >= 0.55 else [],
                "wash": ["rainbow", "breathe"] if energy >= 0.9 else (["breathe"] if energy < 0.4 else []),
                "par": ["dimmer_chase", "sparks"] if energy >= 0.9 else (["breathe"] if energy < 0.4 else ["colour_chase"]),
                "bar": ["rainbow", "dimmer_chase"] if energy >= 0.55 else ["breathe"],
            }.get(role, [])
            fx = next((f for f in fx_pref if f in g["fx"]), "")
            if fx:
                look["fx"] = fx
            looks.append(look)
        hits = []
        if name.lower().startswith("build"):
            hits.append({"kind": "strobe", "group": strobey[0], "every": "beat", "last_bars": 2})
        if name.lower().startswith("drop"):
            hits.append({"kind": "flash", "group": strobey[0], "every": "bar", "last_bars": 0})
        master = [round(40 + 60 * energy)] * 2
        if i == 0:
            master = [0, master[1]]
        if i == len(sections) - 1:
            master = [master[0], 0]
        out.append({"name": name, "bars": bars, "energy": energy,
                    "fade": 0.0 if energy >= 0.9 else (1.0 if energy >= 0.55 else 3.0),
                    "master": master, "looks": looks, "hits": hits})
    return {"name": (brief.strip()[:40] or "Club show").title(), "palette": pal, "sections": out}


def validate(raw, a: dict) -> dict:
    """Keep only what the rig can do; clamp everything else."""
    if not isinstance(raw, dict):
        raise ValueError("design is not an object")
    groups, targets = a["groups"], a["targets"]
    pal = [c for c in raw.get("palette") or [] if isinstance(c, str) and HEX.match(c)][:5]
    sections = []
    for s in (raw.get("sections") or [])[:24]:
        if not isinstance(s, dict):
            continue
        looks = []
        for lk in s.get("looks") or []:
            if not isinstance(lk, dict) or lk.get("group") not in groups:
                continue
            g = groups[lk["group"]]
            c = g["caps"]
            try:
                inten = max(0, min(100, int(round(float(lk.get("intensity", 100))))))
            except (TypeError, ValueError):
                inten = 100
            look = {"group": lk["group"], "intensity": inten}
            col = str(lk.get("colour") or "")
            if c["colour"] == "mix" and HEX.match(col):
                look["colour"] = col.lower()
            if c["pan_tilt"] and lk.get("aim") in targets:
                look["aim"] = lk["aim"]
            if c["zoom"] and lk.get("zoom") in ("narrow", "wide"):
                look["zoom"] = lk["zoom"]
            if c["gobo"] and lk.get("gobo") is True:
                look["gobo"] = True
            if lk.get("fx") in g["fx"]:
                look["fx"] = lk["fx"]
            looks.append(look)
        hits = []
        for hit in (s.get("hits") or [])[:6]:
            if isinstance(hit, dict) and hit.get("group") in groups and hit.get("kind") in ("strobe", "flash"):
                hits.append({"kind": hit["kind"], "group": hit["group"],
                             "every": hit.get("every") if hit.get("every") in ("beat", "half", "bar") else "beat",
                             "last_bars": max(0, int(hit.get("last_bars") or 0))})
        try:
            bars = max(2, min(64, int(s.get("bars") or 8)))
            energy = max(0.0, min(1.0, float(s.get("energy", 0.5))))
            fade = max(0.0, min(20.0, float(s.get("fade", 1.0))))
        except (TypeError, ValueError):
            continue
        master = s.get("master") if isinstance(s.get("master"), list) else None
        try:
            master = [max(0, min(100, int(float(m)))) for m in master][:2] if master else None
        except (TypeError, ValueError):
            master = None
        if master and len(master) == 1:
            master = master * 2
        if not looks:
            continue
        sections.append({"name": str(s.get("name") or f"Section {len(sections) + 1}")[:30],
                         "bars": bars, "energy": round(energy, 2), "fade": round(fade, 2),
                         "master": master, "looks": looks, "hits": hits})
    if not sections:
        raise ValueError("the design has no usable sections for this rig")
    return {"name": str(raw.get("name") or "Show")[:40], "palette": pal, "sections": sections}


def design(eng, brief: str = "", offline: bool = False) -> dict:
    """Analyse the rig and design a show for it: {design, analysis, source}."""
    a = analyse(eng)
    if not a["groups"]:
        raise ValueError("patch some lights first - there is nothing to design for")
    note = None
    if not offline and llm.available():
        try:
            raw = llm.structured(
                [{"role": "system", "content": SYSTEM + "\n\n" + describe(a)},
                 {"role": "user", "content": brief or "A high-energy club night."}],
                "show_design", "The whole show for this rig, section by section.",
                DESIGN_SCHEMA, temperature=0.7)
            return {"design": validate(raw, a), "analysis": _public(a), "source": "llm"}
        except (llm.LLMError, ValueError) as exc:
            note = f"designed offline (AI: {str(exc)[:160]})"
    out = {"design": validate(fallback_design(a, brief), a), "analysis": _public(a), "source": "offline"}
    if note:
        out["note"] = note
    return out


def _public(a: dict) -> dict:
    return {"groups": {k: {"name": g["name"], "heads": g["heads"], "role": g["role"]}
                       for k, g in a["groups"].items()},
            "targets": a["targets"], "bpm": a["bpm"], "duration": a["duration"]}


# ---------------------------------------------------------------------------
# compile: the design as engine actions
# ---------------------------------------------------------------------------
def _layout(sections: list[dict], a: dict) -> list[tuple[float, float]]:
    """(start, end) seconds per section: bars at the tempo, stretched to fill
    the song when there is audio, snapped to markers that share a name."""
    spb = 240.0 / (a["bpm"] or 120.0)
    bars = [s["bars"] for s in sections]
    total = sum(bars) * spb
    if a["duration"] and total > 0:
        k = a["duration"] / total
        bars = [max(2, int(round(b * k / 2)) * 2) for b in bars]
    times, t = [], 0.0
    marks = {m["name"].strip().lower(): m["t"] for m in a["markers"] if m.get("name")}
    for s, b in zip(sections, bars):
        start = marks.get(s["name"].strip().lower(), t)
        t = start + b * spb
        times.append((round(start, 3), round(t, 3)))
    if a["duration"]:
        times = [(s, min(e, a["duration"])) for s, e in times if s < a["duration"]]
    return times


def compile_calls(d: dict, a: dict, eng, playback: int = 1) -> tuple[list[dict], dict]:
    """The design as engine calls (applied together, one undo step)."""
    groups, targets = a["groups"], a["targets"]
    heads_by = {h["head_no"]: h for h in eng.patch}
    calls: list[dict] = []
    pb = eng._playback(playback)
    for n in range(len(pb["stack"]), 0, -1):
        calls.append({"action": "delete_cue", "params": {"playback": playback, "cue": n}})
    # groups (replacing earlier auto groups)
    for g in eng.groups:
        if str(g["name"]).startswith(AUTO_PREFIX):
            calls.append({"action": "group_delete", "params": {"n": g["n"]}})
    used = sorted({lk["group"] for s in d["sections"] for lk in s["looks"]})
    for key in used:
        calls.append({"action": "group_create", "params": {
            "name": AUTO_PREFIX + groups[key]["name"], "heads": groups[key]["heads"]}})
    times = _layout(d["sections"], a)
    # one cue per section
    for s, _span in zip(d["sections"], times):
        calls.append({"action": "clear_programmer", "params": {}})
        for lk in s["looks"]:
            g = groups[lk["group"]]
            calls.append({"action": "select_heads", "params": {"heads": g["heads"]}})
            calls.append({"action": "set_intensity", "params": {"level": lk["intensity"]}})
            gated = [heads_by[n] for n in g["heads"] if n in heads_by
                     and "dimmer" not in heads_by[n]["map"] and _caps(heads_by[n])["gate"]]
            if gated:
                role = _caps(gated[0])["gate"]
                value = eng._open_value(gated[0], role) if lk["intensity"] > 0 else 0
                calls.append({"action": "set_attribute", "params": {"attribute": role, "value": value}})
            if lk.get("colour"):
                calls.append({"action": "set_colour", "params": {"hex": lk["colour"]}})
            if lk.get("aim"):
                x, y, z = targets[lk["aim"]]
                calls.append({"action": "aim_at", "params": {"x": x, "y": y, "z": z}})
            if lk.get("zoom"):
                calls.append({"action": "set_attribute", "params": {
                    "attribute": "zoom", "value": 40 if lk["zoom"] == "narrow" else 215}})
            if lk.get("gobo"):
                calls.append({"action": "set_attribute", "params": {"attribute": "gobo", "value": 40}})
        calls.append({"action": "record_cue", "params": {
            "playback": playback, "name": s["name"], "fade": s["fade"], "hold": 0, "follow": 0}})
    calls.append({"action": "clear_programmer", "params": {}})
    calls.append({"action": "clear_selection", "params": {}})
    # quick buttons for the hits, on the auto page
    hit_btn: dict[tuple, str] = {}
    slot = 0
    for s in d["sections"]:
        for hit in s["hits"]:
            key = (hit["kind"], hit["group"])
            if key in hit_btn or slot >= 24:
                continue
            slot += 1
            hit_btn[key] = f"q{AUTO_PAGE}-{slot}"
            g = groups[hit["group"]]
            button = {"kind": hit["kind"], "label": f"{hit['kind'].title()} {g['name']}"[:24],
                      "mode": "hold", "target": {"heads": g["heads"]}}
            if hit["kind"] == "strobe":
                button["hz"] = 12
            calls.append({"action": "quick_set", "params": {"page": AUTO_PAGE, "slot": slot, "button": button}})
    # the timeline: keep the user's own tracks, replace the auto ones
    doc = copy.deepcopy(eng.timeline)
    doc["tracks"] = [t for t in doc.get("tracks") or [] if not str(t.get("name", "")).startswith(AUTO_PREFIX)]
    beat = 60.0 / (a["bpm"] or 120.0)
    cue_clips, hit_clips, keys = [], [], []
    fx_by_group: dict[str, list[dict]] = {}
    for i, (s, (start, end)) in enumerate(zip(d["sections"], times)):
        cue_clips.append({"t": start, "cue": i + 1, "label": s["name"]})
        for lk in s["looks"]:
            if lk.get("fx") and end - start > 0.2:
                fx_by_group.setdefault(lk["group"], []).append({
                    "t": start, "dur": round(end - start, 3), "fx": lk["fx"],
                    "target": {"heads": groups[lk["group"]]["heads"]},
                    "label": lk["fx"].replace("_", " ")})
        for hit in s["hits"]:
            step = {"beat": beat, "half": beat / 2, "bar": beat * 4}[hit["every"]]
            first = start if not hit["last_bars"] else max(start, end - hit["last_bars"] * 4 * beat)
            t = first
            while t < end - 1e-3 and len(hit_clips) < 400:
                hit_clips.append({"t": round(t, 3), "dur": round(min(step, beat) * 0.5, 3),
                                  "button": hit_btn[(hit["kind"], hit["group"])]})
                t += step
        if s.get("master"):
            keys.append({"t": start, "v": s["master"][0]})
            keys.append({"t": max(start, end - 0.05), "v": s["master"][1]})
    doc["tracks"].append({"kind": "cue", "name": AUTO_PREFIX + "Cues", "playback": playback, "clips": cue_clips})
    # one effect track per group, so no two clips ever share a lane
    for key in sorted(fx_by_group, key=lambda k: groups[k]["name"]):
        doc["tracks"].append({"kind": "fx", "name": AUTO_PREFIX + groups[key]["name"],
                              "clips": fx_by_group[key]})
    fx_clips = [c for clips in fx_by_group.values() for c in clips]
    if hit_clips:
        doc["tracks"].append({"kind": "button", "name": AUTO_PREFIX + "Hits", "clips": hit_clips})
    if keys:
        doc["tracks"].append({"kind": "level", "name": AUTO_PREFIX + "Master", "target": "master", "clips": keys})
    end = times[-1][1] if times else 60.0
    doc["length"] = max(float(a["duration"] or 0), end + 2.0)
    calls.append({"action": "timeline_set", "params": {"timeline": doc}})
    summary = {"sections": [{"name": s["name"], "start": st, "end": en, "energy": s["energy"]}
                            for s, (st, en) in zip(d["sections"], times)],
               "groups": len(used), "cues": len(times), "fx_clips": len(fx_clips),
               "hits": len(hit_clips), "length": round(doc["length"], 1)}
    return calls, summary


def build(eng, d: dict, playback: int = 1) -> dict:
    """Apply a (previewed) design: one undo step, all or nothing."""
    a = analyse(eng)
    d = validate(d, a)
    calls, summary = compile_calls(d, a, eng, playback)
    res = eng.act_batch(calls, label="auto show")
    if not res.get("ok"):
        raise ValueError(res.get("error") or "the show could not be built")
    return {"summary": f"built \"{d['name']}\": {summary['cues']} cues, "
                       f"{summary['fx_clips']} effect clips, {summary['hits']} hits, "
                       f"{summary['length']:.0f} s on the timeline", "built": summary}
