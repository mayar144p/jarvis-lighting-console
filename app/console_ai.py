"""The copilot: plain words -> a plan of console steps -> the operator applies.

    plan(message, eng=None, history=None)
        Compile one instruction into {"reply", "steps", "answer"?}.  With an
        AI key, the model sees the actual rig (every fixture by type and
        brand, groups, the selection, palettes, presets, cues, running
        effects and the effects library) and answers through a JSON schema;
        without one, a deterministic keyword compiler does the same job for
        the common phrases.  Nothing runs here.

    resolve(steps, eng) / run(calls, eng)
        Turn validated steps into engine calls with explicit selection
        steps, and run them as ONE undoable edit that rolls back on failure.

    generate(prompt, eng=None)
        Show from a brief: the model designs two or three concepts for the
        rig that is patched (palettes, cue lists, timing per design role);
        the deterministic designer in showdesign.py is the fallback.

SAFETY - the model can only emit actions on an allowlist, each with a fixed
set of parameters.  It cannot arm the output, open or save show files,
import anything, replace or clear the patch, or delete heads or groups;
those stay on the operator's own buttons, behind a confirmation.  Every
plan is shown to the operator before it runs, and one Ctrl+Z undoes it.
"""
from __future__ import annotations

import re

from . import fixture_kind, fxlib, llm, showdesign
from . import venue as venue_mod
from .engine import ACTIONS as ENGINE_ACTIONS

MAX_STEPS = 16
MAX_REPLY = 400
MAX_HISTORY = 8

# Actions deliberately NOT in the AI's reach (see module docstring).
DENY_ACTIONS = frozenset({
    "set_output", "set_dry_run", "save_show", "load_show", "import_show",
    "import_scan", "patch_clear", "patch_from_csv", "remove_heads",
    "group_delete", "delete_preset", "delete_cue", "set_lock", "unlock",
    "set_limits", "clear_limits", "set_orient", "remap_heads", "export_patch",
})

# The allowlist: each action with the keyword arguments it accepts.
PARAMS: dict[str, tuple[str, ...]] = {
    "status": (),
    "blackout": ("state",),
    "master": ("level",),
    "locate": (),
    "clear_programmer": (),
    "clear_selection": (),
    "select_heads": ("head", "head_end", "heads", "add"),
    "select_group": ("group",),
    "select_all": (),
    "select_query": ("role", "universe", "kind"),
    "select_similar": ("head",),
    "set_intensity": ("level", "fade"),
    "set_attribute": ("attribute", "value"),
    "set_attr_range": ("attribute", "clear"),
    "set_colour": ("hex", "colour", "value"),
    "set_position": ("pan", "tilt", "unit"),
    "fan": ("attribute", "from_value", "to_value", "mode"),
    "align": ("axis",),
    "distribute": ("axis",),
    "mirror": ("axis",),
    "record_palette": ("kind", "name"),
    "include_palette": ("kind", "palette"),
    "record_preset": ("name",),
    "include_preset": ("preset",),
    "record_cue": ("playback", "name", "fade", "hold"),
    "cue_go": ("playback", "cue"),
    "cue_back": ("playback",),
    "cue_forward": ("playback",),
    "playback_level": ("playback", "level"),
    "playback_release": ("playback",),
    "playback_activate": ("playback",),
    "follow_set": ("playback", "delay", "on", "pause", "loop"),
    "group_create": ("name", "heads"),
    "rename_head": ("head", "name"),
    "run_fx": ("name", "params", "attribute", "kind", "wave", "speed",
               "spread", "phase", "base", "depth", "duration", "heads", "group"),
    "stop_fx": ("id", "fx"),
    "add_heads": ("query", "fixture_id", "mode", "qty", "universe",
                  "address", "role", "kind", "name", "x", "y", "z"),
    "auto_patch": (),
    "patch_list": ("fixtures", "start"),
    "set_address": ("head", "universe", "address"),
    "set_place": ("head", "heads", "x", "y", "z", "rig", "t", "stance", "snap"),
    "run_command": ("text",),
    # the room, the buttons and the timeline
    "aim_at": ("x", "y", "z", "mark", "heads"),
    "attach_heads": ("heads", "head", "rig", "spacing", "stance"),
    "venue_template": ("name", "width", "depth", "height"),
    "venue_crowd": ("style", "density", "show"),
    "quick_defaults": ("page",),
    "timeline_play": ("at",),
    "timeline_pause": (),
    "timeline_stop": (),
    "timeline_seek": ("t",),
    "timeline_from_playback": ("playback", "start"),
}

_UNKNOWN = [a for a in PARAMS if a not in ENGINE_ACTIONS]
if _UNKNOWN:
    raise RuntimeError(f"console_ai allowlist unknown to engine: {_UNKNOWN}")
_OVERLAP = DENY_ACTIONS & set(PARAMS)
if _OVERLAP:
    raise RuntimeError(f"an action is both allowed and denied: {_OVERLAP}")

ALLOWED_ACTIONS = tuple(sorted(PARAMS))

# Actions that act on the selected heads.
SELECTION_ACTIONS = frozenset({
    "set_intensity", "set_attribute", "set_colour", "set_position",
    "locate", "record_palette", "include_palette", "include_preset",
    "run_fx", "fan", "align", "distribute", "mirror", "set_attr_range",
    "record_preset", "aim_at", "attach_heads",
})


# ---------------------------------------------------------------------------
# what the model is told
# ---------------------------------------------------------------------------

def _span(nums) -> str:
    """[1,2,3,5,7,8] -> '1-3, 5, 7-8'."""
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)


def rig_context(eng) -> str:
    """A compact, factual description of the rig for the model."""
    if eng is None:
        return "RIG: unknown (no engine)."
    with eng.lock:
        patch = [dict(h) for h in eng.patch]
        groups = [dict(g) for g in eng.groups]
        selected = list(eng.selected)
        palettes = {k: [p.get("name") for p in v] for k, v in eng.palettes.items()}
        presets = [p.get("name") for p in eng.presets]
        pbs = [(pb["n"], pb.get("name") or "",
                [c.get("name") for c in pb.get("stack") or []],
                pb.get("index", -1), pb.get("active")) for pb in eng.playbacks]
        fx = eng._fx_public()
        master, blackout = eng.master, eng.blackout
        room = venue_mod.describe(eng.venue) if not eng.venue.get("auto") else None
        tl = eng.timeline
        tl_state = eng._tl_transport()
    lines = []
    if not patch:
        lines.append("RIG: nothing is patched yet.")
    else:
        buckets: dict[tuple, list[int]] = {}
        caps: dict[tuple, set] = {}
        where: dict[tuple, set] = {}
        for h in patch:
            body = fixture_kind.describe(h)
            key = (body["brand_name"], h.get("model") or "", body["label"])
            buckets.setdefault(key, []).append(h["head_no"])
            caps.setdefault(key, set()).update(
                r for r in h.get("map") or []
                if r not in ("raw", "unused") and not r.endswith("_fine"))
            where.setdefault(key, set()).add(h.get("kind") or "floor")
        lines.append(f"RIG: {len(patch)} fixtures.")
        for key, heads in buckets.items():
            brand, model, label = key
            lines.append(f"- heads {_span(heads)}: {brand} {model} ({label}, "
                         f"{'/'.join(sorted(where[key]))}); "
                         f"can do: {', '.join(sorted(caps[key]))}")
    if groups:
        lines.append("GROUPS: " + "; ".join(
            f"group {g['n']} \"{g['name']}\" = heads {_span(g['heads'])}" for g in groups))
    lines.append("SELECTED: " + (f"heads {_span(selected)}" if selected else "nothing"))
    pal = [f"{k}: {', '.join(v)}" for k, v in palettes.items() if v]
    if pal:
        lines.append("PALETTES: " + "; ".join(pal))
    if presets:
        lines.append("PRESETS: " + ", ".join(presets))
    for n, name, cues, index, active in pbs:
        if cues:
            state = f"on cue {index + 1}" if active and index >= 0 else "idle"
            lines.append(f"PLAYBACK {n}{' ' + repr(name) if name else ''}: "
                         f"{len(cues)} cues ({', '.join(c or '?' for c in cues[:8])}), {state}")
    if fx:
        lines.append("RUNNING EFFECTS: " + ", ".join(
            f"#{f['id']} {f.get('label') or f.get('kind')} on "
            f"{len(f.get('heads') or [])} heads" for f in fx))
    lines.append(f"MASTER: {master}%{' - BLACKOUT is ON' if blackout else ''}")
    if room:
        w, d, hh = room["room"]
        lines.append(f"ROOM: {room['name'] or 'venue'} {w:g} x {d:g} m, {hh:g} m high.")
        if room["rigging"]:
            lines.append("RIGGING (attach_heads rig=id): " + "; ".join(
                f"{r['id']} {r['name'] or r['kind']} ({r['kind']}, {r['height']:g} m high)"
                for r in room["rigging"]))
        if room["marks"]:
            lines.append("MARKS (aim_at mark=name): " + ", ".join(m["name"] for m in room["marks"]))
        if room["zones"]:
            lines.append("ZONES (aim_at x/z of the centre): " + "; ".join(
                f"{z['name'] or z['kind']} at x {z['centre'][0]:g} z {z['centre'][1]:g}"
                for z in room["zones"]))
    if tl.get("tracks"):
        lines.append(f"TIMELINE: {len(tl['tracks'])} tracks, {tl['length']:g} s at "
                     f"{tl['bpm']:g} BPM, {'playing' if tl_state['playing'] else 'stopped'} "
                     f"at {tl_state['pos']:.1f} s")
    named = [f"{k} ({v['label']}, {v['group']})" for k, v in fxlib.FX.items()]
    lines.append("NAMED EFFECTS (run_fx name=...): " + ", ".join(named))
    return "\n".join(lines)


def _param_ref() -> str:
    return "\n".join(f"    {a}({', '.join(PARAMS[a])})" for a in ALLOWED_ACTIONS)


SYSTEM = f"""You are Jarvis, the copilot inside a professional lighting console.
The operator types what they want in plain words; you turn it into a short
plan of console steps that they review and then apply.  You never run
anything yourself.

Answer with ONE JSON object:
  reply   one or two sentences: what the plan does, in the operator's language
  answer  optional: when they ASKED something (e.g. "how many movers do I
          have?"), the answer, from the rig description only
  steps   0-10 steps, each {{"target", "action", "attributes", "fx", "timing"}}

target: "auto" (the selection if there is one, else everything), "all",
  "selection", "group N", "heads 1-4" or "heads 1,3,5", "type movers",
  "type pars", "playback N", or "programmer".
action: exactly one of
{_param_ref()}
attributes: that action's keyword arguments.  level 0-100 (percent);
  value 0-255 (raw DMX); colour as "#rrggbb"; pan/tilt in degrees with
  unit "degree" when the rig lists pan/tilt ranges, otherwise 0-255 with
  unit "255"; playback 1-10.
fx: only for run_fx.  Prefer a NAMED effect: {{"name": "circle",
  "params": {{"speed": 0.5}}}}.  Otherwise a single-attribute wave:
  attribute, kind (sine|saw|square|triangle|random), speed Hz, spread and
  phase in degrees, duration seconds.
timing: {{"fade": seconds}} for set_intensity and record_cue.

Rules:
  * Use the rig below: real head numbers, groups and capabilities.  Target
    only fixtures that can do what is asked (colour on heads with colour,
    pan/tilt on movers).  Never invent heads, palettes or cues.
  * Only what was asked.  A look is usually: target -> intensity -> colour
    (-> position / beam / effect).
  * You cannot save or load shows, clear the patch, delete anything or arm
    the output; say so if asked, and leave "steps" empty.
  * "warmer", "slower", "brighter" and similar refer to the previous turn.
"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "answer": {"type": "string"},
        "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "action": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
                "attributes": {"type": "object"},
                "fx": {"type": "object"},
                "timing": {"type": "object"},
            },
            "required": ["action"],
        }},
    },
    "required": ["reply", "steps"],
}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    return llm.extract_json(text)


def _is_value(key: str, value) -> bool:
    """A parameter value: a scalar, a flat list, or a flat object (params)."""
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        if all(isinstance(v, (str, int, float, bool)) for v in value):
            return True
        if key == "fixtures" and all(isinstance(v, dict) for v in value):
            return all(_is_value(k, v) for v in value for k, v in v.items())
    if isinstance(value, dict) and key == "params":
        return all(isinstance(v, (int, float, str, bool)) for v in value.values())
    return False


def _validate(raw) -> dict:
    """Normalise a plan dict; raises ValueError with a step-precise message."""
    if not isinstance(raw, dict):
        raise ValueError("plan must be a JSON object")
    reply = str(raw.get("reply") or "").strip() or "OK."
    if len(reply) > MAX_REPLY:
        reply = reply[:MAX_REPLY - 1] + "…"
    rows = raw.get("steps") or []
    if not isinstance(rows, list):
        raise ValueError("'steps' must be a list")
    if len(rows) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps per instruction")
    steps = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"step {i} must be an object")
        action = str(row.get("action") or "").strip()
        if not action:
            raise ValueError(f"step {i} has no action")
        if action in DENY_ACTIONS:
            raise ValueError(
                f"step {i}: '{action}' is not available to the AI - "
                "use the console buttons for it")
        if action not in PARAMS:
            raise ValueError(f"step {i}: unknown action '{action}'")

        def bag(name: str) -> dict:
            value = row.get(name)
            if value in (None, "", {}):
                return {}
            if not isinstance(value, dict):
                raise ValueError(f"step {i}: '{name}' must be an object")
            out = {}
            for key, val in value.items():
                key = str(key)
                if key not in PARAMS[action]:
                    raise ValueError(
                        f"step {i}: '{key}' is not a parameter of {action}")
                if val is None:
                    continue
                if not _is_value(key, val):
                    raise ValueError(
                        f"step {i}: '{key}' must be a number, string "
                        "or flat list")
                out[key] = val
            return out

        if row.get("fx") and action != "run_fx":
            raise ValueError(f"step {i}: 'fx' only belongs to run_fx")
        attributes, fx, timing = bag("attributes"), bag("fx"), bag("timing")
        if action == "run_fx" and fx.get("name"):
            name = str(fx["name"]).lower()
            if name not in fxlib.FX:
                raise ValueError(f"step {i}: no effect called {name!r}")
            fx["name"] = name
        steps.append({
            "target": str(row.get("target") or "auto").strip(),
            "action": action,
            "attributes": attributes,
            "fx": fx,
            "timing": timing,
        })
    out = {"reply": reply, "steps": steps}
    answer = str(raw.get("answer") or "").strip()
    if answer:
        out["answer"] = answer[:1200]
    return out


# ---------------------------------------------------------------------------
# plan(): the model first, the deterministic compiler as the floor
# ---------------------------------------------------------------------------

def _history(history) -> list[dict]:
    out = []
    for turn in (history or [])[-MAX_HISTORY:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = str(turn.get("content") or "")[:800]
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def plan(message: str, offline: bool = False, eng=None, history=None) -> dict:
    """Compile one instruction -> {"reply", "steps", "source"[, "answer", "note"]}."""
    text = str(message or "").strip()
    if not text:
        return {"reply": "Tell me the look - e.g. 'everything to 70%', "
                         "'slow blue wash on the movers' or 'rainbow across the rig'.",
                "steps": [], "source": "fallback"}
    if not offline and llm.available():
        try:
            messages = [{"role": "system",
                         "content": SYSTEM + "\n\n" + rig_context(eng)}]
            messages += _history(history)
            messages.append({"role": "user", "content": text})
            raw = llm.structured(messages, "plan",
                                 "The console steps that do what the operator asked.",
                                 PLAN_SCHEMA)
            out = _validate(raw)
            out["source"] = "llm"
            return out
        except (llm.LLMError, ValueError) as exc:
            out = _validate(_fallback(text))
            out["source"] = "fallback"
            out["note"] = f"offline compiler (AI: {exc})"[:300]
            return out
    return {**_validate(_fallback(text)), "source": "fallback"}


# --- deterministic keyword compiler (offline floor) -----------------------

_FIXTURE_WORDS = [
    (r"\bmoving heads?\b|\bmovers?\b", "Moving Head Spot"),
    (r"\bspots?\b", "Moving Head Spot"),
    (r"\bbars?\b|\blight bars?\b", "RGBW Bar"),
    (r"\bpars?\b|\bled pars?\b", "LED PAR"),
]


def _fallback(text: str) -> dict:
    """Deterministic keyword compiler - no model, no network, no surprises."""
    low = " " + text.lower().strip() + " "
    steps: list[dict] = []

    def add(target: str, action: str, attributes: dict | None = None,
            fx: dict | None = None, timing: dict | None = None) -> None:
        if len(steps) < MAX_STEPS:
            steps.append({"target": target, "action": action,
                          "attributes": attributes or {}, "fx": fx or {},
                          "timing": timing or {}})

    # --- target chosen from the words, "auto" = selection else all -------
    target = "auto"
    nums = r"(\d+(?:\s*(?:-|to|thru|through)\s*\d+)?(?:\s*(?:,|and)\s*\d+)*)"
    group_hit = re.search(r"\bgroup\s*(\d+)\b", low)
    head_hit = (re.search(r"\b(?:heads?|fixtures?|lights?|units?)\s+" + nums, low)
                or re.search(r"(?:\bon\s+|#)" + nums + r"(?!\s*(?:%|s\b|sec|hz))", low)
                or re.match(r"\s*" + nums + r"\s+(?=[a-z])", low))
    add_words = re.search(r"\b(?:add|patch|put|hang|rig)\s+\d+", low)
    kind_hit = None if add_words else re.search(
        r"\b(movers?|moving\s+heads?|spots?|washes|pars?|bars?)\b", low)
    if group_hit:
        target = f"group {group_hit.group(1)}"
    elif head_hit:
        spec = re.sub(r"\s*(?:to|thru|through)\s*", "-", head_hit.group(1))
        target = "heads " + re.sub(r"\s*(?:and)\s*", ",", spec).replace(" ", "")
    elif kind_hit:
        word = kind_hit.group(1)
        target = ("type movers" if word.startswith(("mover", "moving", "spot"))
                  else "type pars")
    elif re.search(r"\b(everything|all|the rig|whole rig|everybody)\b", low):
        target = "all"
    elif re.search(r"\b(selection|selected heads?)\b", low):
        target = "selection"
    pb = re.search(r"\b(?:playback|pb|fader)\s*(\d{1,2})\b", low)
    if pb and target == "auto":
        target = f"playback {pb.group(1)}"

    consumed: list[tuple[int, int]] = []      # %-spans already given a step

    def free_percent() -> re.Match | None:
        for m in re.finditer(r"(\d{1,3})\s*%", low):
            if not any(a < m.end() and m.start() < b for a, b in consumed):
                return m
        return None

    # --- blackout is exclusive: nothing else should fire with it ---------
    if re.search(r"\b(unblackout|blackout off|lights back)\b", low) or \
            re.search(r"\b(end|stop|clear|cancel|release|undo|off)\b"
                      r"[^.]*\bblack ?out\b", low):
        add(target, "blackout", {"state": 0})
        return {"reply": "Blackout released.", "steps": steps}
    if re.search(r"\bblack ?out\b|\bpanic\b|\bkill\s+(?:the\s+)?"
                 r"(?:lights|rig)\b|\blights out\b", low):
        add(target, "blackout", {"state": 1})
        return {"reply": "Blackout on.", "steps": steps}

    # --- clears and stops compose with the rest --------------------------
    if re.search(r"\bclear\s+(?:the\s+)?selection\b", low):
        add("selection", "clear_selection")
    elif re.search(r"\bclear\b", low):
        add("selection", "clear_programmer")
    if re.search(r"\b(?:stop|kill|end|reset)\b[^.]*\b(?:effects?|fx|"
                 r"chasers?|runs?)\b", low):
        add("selection", "stop_fx")

    # --- patching: "add 4 pars", "patch 2 movers" ------------------------
    qty = None
    for pattern in (r"\b(?:add|patch|put|hang|rig)\s+(\d+)\s+(?:more\s+)?",
                    r"\b(\d+)\s+(?:more\s+)?(?:led\s+)?(?:pars?|movers?|"
                    r"moving\s+heads?|spots?|bars?|fixtures?)\b"):
        found = re.search(pattern, low)
        if found:
            qty = max(1, min(64, int(found.group(1))))
            break
    if qty is not None:
        for pattern, query in _FIXTURE_WORDS:
            if re.search(pattern, low):
                add("auto", "add_heads", {"query": query, "qty": qty})
                break
        else:
            add("auto", "add_heads", {"query": "LED PAR", "qty": qty})

    # --- master / playback faders (their % is not intensity) -------------
    master = re.search(r"\b(?:grand\s+)?master\s*(?:to\s*)?"
                       r"(\d{1,3})\s*%", low)
    if master:
        consumed.append((master.start(), master.end()))
        add("programmer", "master",
            {"level": max(0, min(100, int(master.group(1))))})
    elif re.search(r"\b(?:grand\s+)?master\s+(?:to\s+)?full\b", low):
        add("programmer", "master", {"level": 100})
    if pb:
        fader = re.search(r"(\d{1,3})\s*%", low[master.end() if master else 0:])
        if fader:
            base = master.end() if master else 0
            consumed.append((base + fader.start(), base + fader.end()))
            add(target, "playback_level",
                {"playback": int(pb.group(1)),
                 "level": max(0, min(100, int(fader.group(1))))})

    # --- cues -------------------------------------------------------------
    if re.search(r"\b(?:previous|last|back)\s+cue\b|\bcue\s+back\b", low):
        add(target, "cue_back")
    elif re.search(r"^\s*go\s*[.!]?\s*$|\bnext\s+cue\b|\bcue\s+go\b|\bpress\s+go\b|"
                   r"\bgo\s+(?:on\s+)?(?:playback|pb|cue)\b", low):
        add(target, "cue_go")

    # --- colour: first mentioned colour (skipping avoid/no constructions) -
    hex_match = re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", low)
    colour_hits: list[tuple[int, int, str]] = []
    for name, hexcode in sorted(showdesign.COLOR_NAMES,
                                key=lambda item: -len(item[0])):
        for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", low):
            before = low[max(0, m.start() - 12):m.start()]
            if re.search(r"\b(?:no|not|without|avoid|except|never)\s+$",
                         before):
                continue                    # "no red" is a ban, not a pick
            if any(s <= m.start() < e for s, e, _h in colour_hits):
                continue                    # part of a longer name already
            colour_hits.append((m.start(), m.end(), hexcode))
    if hex_match and (not colour_hits
                      or hex_match.start() < min(c[0] for c in colour_hits)):
        add(target, "set_colour", {"hex": hex_match.group(0)})
    elif colour_hits:
        colour_hits.sort()
        add(target, "set_colour", {"hex": colour_hits[0][2]})

    # --- intensity --------------------------------------------------------
    for m in re.finditer(r"\b(?:zoom|focus|iris|frost|prism|gobo|shutter)\s+"
                         r"(?:to\s+|at\s+)?\d{1,3}\s*%", low):
        consumed.append((m.start(), m.end()))
    pct = free_percent()
    if pct:
        consumed.append((pct.start(), pct.end()))
        add(target, "set_intensity",
            {"level": max(0, min(100, int(pct.group(1))))})
    elif re.search(r"\b(?:everything|all|lights|rig)\s+(?:to\s+)?full\b",
                   low) or re.search(r"\bat full\b|\bfull (?:level|intensity)",
                                     low):
        add(target, "set_intensity", {"level": 100})
    elif re.search(r"\b(?:everything|all)\s+(?:to\s+)?off\b", low) or \
            re.search(r"\bfade\b[^.]*\bout\b", low):
        add(target, "set_intensity", {"level": 0})
    elif re.search(r"\bhalf\b", low):
        add(target, "set_intensity", {"level": 50})
    elif re.search(r"\bfade\b[^.]*\bin\b", low):
        add(target, "set_intensity", {"level": 100})
    fade_time = re.search(r"\b(?:over|in|fade(?:\s+time)?)\s+(\d+(?:\.\d+)?)\s*"
                          r"(?:s\b|sec\b|secs\b|seconds?\b)", low)
    if fade_time and steps and steps[-1]["action"] == "set_intensity":
        steps[-1]["timing"] = {"fade": float(fade_time.group(1))}

    # --- aim: "pan to 90", "tilt 40", "pan 90 tilt -30" ------------------
    aim = {}
    for axis in ("pan", "tilt"):
        found = re.search(rf"\b{axis}\s+(?:to\s+|at\s+)?(-?\d+(?:\.\d+)?)"
                          r"(?!\s*(?:hz|s\b))", low)
        if found:
            aim[axis] = float(found.group(1))
    if aim:
        add(target, "set_position", aim)

    # --- beam attributes: "zoom 40", "gobo to 3", "focus 60%" ------------
    for word, role in (("zoom", "zoom"), ("focus", "focus"), ("iris", "iris"),
                       ("frost", "frost"), ("prism", "prism"),
                       ("gobo", "gobo"), ("colou?r wheel", "wheel"),
                       ("shutter", "shutter")):
        found = re.search(rf"\b{word}\s+(?:to\s+|at\s+)?(\d+(?:\.\d+)?)\s*(%)?", low)
        if found:
            value = float(found.group(1))
            if found.group(2):
                value = value * 2.55
            add(target, "set_attribute",
                {"attribute": role, "value": max(0, min(255, round(value)))})

    if re.search(r"\blocate\b|\bflash\s+(?:them|the heads|the fixtures)\b", low):
        add(target, "locate")

    # --- effects: the named library first, a plain wave otherwise ---------
    speed = None
    explicit = re.search(r"(\d+(?:\.\d+)?)\s*(?:hz|cycles?\b|per\s+sec)", low)
    if explicit:
        speed = float(explicit.group(1))
    elif re.search(r"\bslow(?:ly)?\b", low):
        speed = 0.3
    elif re.search(r"\bfast\b|\bquick(?:ly)?\b|\bpunchy\b", low):
        speed = 3.0
    spread = 180.0 if re.search(
        r"\bacross\b|\bcascade|\baround\b|\bfollow\b|\bwave\b", low) else 0.0
    duration = None
    dur_match = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*"
                          r"(?:s\b|sec\b|seconds?\b)", low)
    if dur_match:
        duration = float(dur_match.group(1))

    def named(effect: str) -> None:
        params = {}
        if speed is not None:
            params["speed"] = speed
        if spread:
            params["spread"] = spread
        row = {"name": effect}
        if params:
            row["params"] = params
        if duration is not None:
            row["duration"] = duration
        add(target, "run_fx", {}, row)

    def wave(attribute: str, kind: str, phase: float = 0.0) -> None:
        row = {"attribute": attribute, "kind": kind,
               "speed": speed if speed is not None else 1.5,
               "spread": spread, "phase": phase}
        if duration is not None:
            row["duration"] = duration
        add(target, "run_fx", {}, row)

    if re.search(r"\brainbow\b|colou?r\s+(?:chase|cycle|wheel)", low):
        named("rainbow")
    elif re.search(r"\bfigure\s?(?:8|eight)\b", low):
        named("figure_eight")
    elif re.search(r"\b(circle|circles|rotate|rotation|spin|spinning|swirl)\b", low):
        named("gobo_spin" if re.search(r"\bgobo\b", low) else "circle")
    elif re.search(r"\bpan\b[^.]*\b(?:sweep|back and forth|move)\b|\bsweep\b", low):
        named("pan_sweep")
    elif re.search(r"\b(pulse|breathe|breathing|throb)\b", low):
        named("breathe")
    elif re.search(r"\b(chase|swipe|build)\b", low):
        named("dimmer_chase")
    elif re.search(r"\b(strobe|blink|blinker)\b", low):
        wave("dimmer", "square")
        steps[-1]["fx"]["speed"] = max(steps[-1]["fx"]["speed"], 8.0)
    elif re.search(r"\b(twinkle|sparkle|sparks|flicker|shimmer)\b", low):
        named("sparks")
    elif re.search(r"\b(sine|saw|wave)\b", low) or re.search(
            r"\b(?:run|start)\s+(?:an?\s+)?(?:effect|fx)\b", low):
        wave("dimmer", "saw" if "saw" in low else "sine")

    # --- reply ------------------------------------------------------------
    if steps:
        labels = "; ".join(_label(s) for s in steps)
        reply = f"Compiled {len(steps)} step(s): {labels}."
    else:
        reply = ("I could not compile that offline. Try: 'blackout', 'everything "
                 "to 70%', 'red on 1-4', 'movers circle slowly', 'rainbow across "
                 "the rig', 'stop effects', 'go', 'add 4 pars'.")
    return {"reply": reply, "steps": steps}


def _label(row: dict) -> str:
    action, at = row["action"], row["attributes"]
    if action == "blackout":
        return "blackout " + ("off" if at.get("state") == 0 else "on")
    if action == "set_intensity":
        return f"intensity {at.get('level')}%"
    if action == "set_colour":
        return f"colour {at.get('hex') or at.get('colour')}"
    if action == "run_fx":
        fx = row.get("fx") or {}
        if fx.get("name"):
            return f"{fx['name'].replace('_', ' ')} effect"
        return f"{fx.get('kind', 'sine')} {fx.get('attribute', 'dimmer')} fx"
    if action == "add_heads":
        return f"{at.get('qty', 1)} x {at.get('query', 'fixture')}"
    if action == "master":
        return f"master {at.get('level')}%"
    if action == "playback_level":
        return f"playback {at.get('playback')} {at.get('level')}%"
    if action == "stop_fx":
        return "stop fx"
    return action.replace("_", " ")


# ---------------------------------------------------------------------------
# resolve(): targets -> concrete calls (explicit, visible select_* steps)
# ---------------------------------------------------------------------------

def _colour_hex(value) -> str | None:
    return showdesign._to_hex(str(value or ""))


def _head_numbers(target: str) -> list[int]:
    """'heads 1-4', 'heads 1,3,5', '2-4, 7' -> sorted head numbers."""
    out: set[int] = set()
    for a, b in re.findall(r"(\d+)\s*-\s*(\d+)", target):
        lo, hi = sorted((int(a), int(b)))
        out.update(range(lo, hi + 1))
    rest = re.sub(r"\d+\s*-\s*\d+", " ", target)
    out.update(int(x) for x in re.findall(r"\d+", rest))
    return sorted(out)


def resolve(steps: list[dict], eng=None) -> list[dict]:
    """Expand validated steps into ordered engine calls."""
    calls: list[dict] = []
    last_select: tuple | None = None

    for idx, step in enumerate(steps, 1):
        action = step["action"]
        params = {**(step.get("attributes") or {}),
                  **(step.get("fx") or {}),
                  **(step.get("timing") or {})}
        target = str(step.get("target") or "auto").strip().lower()
        select: tuple | None = None

        if action == "set_colour":
            raw = params.get("hex") or params.get("colour") or params.get("value")
            hx = _colour_hex(raw)
            if hx:
                params["hex"] = hx
                params.pop("colour", None)
                params.pop("value", None)

        def number(where: str) -> int:
            n = int(re.sub(r"\D", "", where) or 0)
            if n < 1:
                raise ValueError(f"step {idx}: target {where!r} needs a number")
            return n

        if target in ("auto", "selection", "selected", "programmer", ""):
            if (target == "auto" and eng is not None
                    and action in SELECTION_ACTIONS and not eng.selected):
                select = ("select_all", {})
        elif target in ("all", "everyone", "everything", "rig"):
            if action in SELECTION_ACTIONS:
                select = ("select_all", {})
        elif target.startswith("group"):
            n = number(target)
            if action in ("run_fx", "select_group"):
                params.setdefault("group", n)
            elif action in SELECTION_ACTIONS:
                select = ("select_group", {"group": n})
        elif target.startswith("heads") or target[:1].isdigit():
            nums = _head_numbers(target)
            if not nums:
                raise ValueError(f"step {idx}: target {target!r} needs head numbers")
            contiguous = nums == list(range(nums[0], nums[-1] + 1))
            if action in ("run_fx", "select_heads"):
                params.setdefault("heads", nums)
            elif action in SELECTION_ACTIONS:
                if len(nums) == 1:
                    select = ("select_heads", {"head": nums[0]})
                elif contiguous:
                    select = ("select_heads", {"head": nums[0], "head_end": nums[-1]})
                else:
                    select = ("select_heads", {"heads": nums})
        elif target.startswith("type"):
            movers = "mover" in target or "spot" in target or "moving" in target
            if action in SELECTION_ACTIONS:
                if eng is not None:
                    hits = sorted(h["head_no"] for h in eng.patch
                                  if ("pan" in (h.get("map") or [])) == movers)
                    if not hits:
                        raise ValueError(
                            f"step {idx}: no {'moving heads' if movers else 'fixed fixtures'}"
                            " are patched")
                    if action == "run_fx":
                        params.setdefault("heads", hits)
                    else:
                        select = ("select_heads", {"heads": hits})
                elif movers:
                    select = ("select_query", {"role": "pan"})
                else:
                    raise ValueError(f"step {idx}: {target!r} needs the rig")
        elif target.startswith(("playback", "pb", "fader")):
            params.setdefault("playback", number(target))
        else:
            raise ValueError(f"step {idx}: unknown target {step['target']!r}")

        if action == "select_all":
            last_select = ("select_all", {})
        elif action == "select_group":
            last_select = ("select_group", {"group": params.get("group")})
        elif action == "select_heads":
            last_select = ("select_heads",
                           {k: params[k] for k in ("head", "head_end", "heads")
                            if k in params})

        if select is not None and select != last_select:
            calls.append({"step": idx, "action": select[0], "params": select[1]})
            last_select = select
        calls.append({"step": idx, "action": action, "params": params})
    return calls


def run(calls: list[dict], eng) -> dict:
    """Execute calls as one undoable edit; roll everything back on failure."""
    if hasattr(eng, "act_batch"):
        return eng.act_batch(calls, label="copilot")
    results: list[dict] = []
    for call in calls:
        res = eng.act(call["action"], **call["params"])
        results.append({"step": call["step"], "action": call["action"],
                        "params": call["params"], "ok": bool(res.get("ok")),
                        "summary": str(res.get("summary") or res.get("error") or "")})
        if not res.get("ok"):
            return {"ok": False, "executed": len(results) - 1,
                    "steps_run": results,
                    "error": str(res.get("error") or "step failed")}
    return {"ok": True, "executed": len(results), "steps_run": results}


# ---------------------------------------------------------------------------
# show from a brief
# ---------------------------------------------------------------------------

_BRIEF_SYSTEM = """You turn one free-text sentence about a show into a
STRICT JSON brief for a lighting show designer (nothing but JSON):

{"mood": "<short feel/genre phrase or empty>",
 "event": "<wedding|club night|concert|corporate|gala|festival|birthday|"
          "theatre|church|party|other or empty>",
 "pace": "slow|medium|fast|mixed|",
 "structure": "goalpost|box|proscenium|ground|open|",
 "colours": ["<colour name or #rrggbb actually mentioned, 0-4>"],
 "avoid": "<colour/thing to leave out or empty>"}

Use empty strings when the text does not say; never invent answers the
operator did not give.  Keep every field short."""

_PACES = ("slow", "medium", "fast", "mixed", "")


def _clean_brief(raw: dict) -> dict:
    brief = {
        "mood": str(raw.get("mood") or "").strip()[:200],
        "event": str(raw.get("event") or "").strip()[:60],
        "pace": str(raw.get("pace") or "").strip().lower(),
        "structure": str(raw.get("structure") or "").strip().lower(),
        "colours": [],
        "avoid": str(raw.get("avoid") or "").strip()[:120],
    }
    if brief["pace"] not in _PACES:
        brief["pace"] = ""
    if brief["structure"] not in ("", "goalpost", "box", "proscenium",
                                  "ground", "open"):
        brief["structure"] = ""
    colours = raw.get("colours")
    if isinstance(colours, (list, tuple)):
        for c in colours:
            name = str(c).strip()
            if name and name not in brief["colours"] and len(brief["colours"]) < 4:
                brief["colours"].append(name)
    return brief


def _brief_fallback(prompt: str) -> dict:
    """Keyword extraction - deterministic and honest about what it missed."""
    low = " " + prompt.lower().strip() + " "
    brief = {"mood": " ".join(prompt.split())[:200], "event": "",
             "pace": "", "structure": "", "colours": [], "avoid": ""}
    for word, value in (("goalpost", "goalpost"), ("box truss", "box"),
                        ("full box", "box"), ("proscenium", "proscenium"),
                        ("theatre", "proscenium"), ("theater", "proscenium"),
                        ("ground", "ground"), ("tower", "ground"),
                        ("stand", "ground"), ("open floor", "open"),
                        ("no rig", "open")):
        if word in low:
            brief["structure"] = value
            break
    if re.search(r"\bslow\b|\belegan|\bcalm\b|\bballad\b|\bmellow\b", low):
        brief["pace"] = "slow"
    elif re.search(r"\bfast\b|\bpunch|\benerget|\baggress|\bupbeat\b|"
                   r"\bhigh[- ]energy\b", low):
        brief["pace"] = "fast"
    elif re.search(r"\bmixed\b|\bvaried\b|\bbuild\b", low):
        brief["pace"] = "mixed"
    for word, event in (("wedding", "wedding"), ("club", "club night"),
                        ("techno", "club night"), ("rave", "club night"),
                        ("corporate", "corporate"), ("gala", "gala"),
                        ("festival", "festival"), ("birthday", "birthday"),
                        ("theatre", "theatre"), ("theater", "theatre"),
                        ("church", "church"), ("concert", "concert"),
                        ("gig", "concert"), ("party", "party")):
        if re.search(rf"\b{re.escape(word)}\b", low):
            brief["event"] = event
            break
    ban = re.search(r"\b(?:avoid|no|without|never)\s+([^,.;]{2,40})", low)
    ban_span = (ban.start(), ban.end()) if ban else None
    if ban:
        brief["avoid"] = ban.group(1).strip()

    def mentioned(term: str) -> bool:
        for m in re.finditer(rf"(?<![\w-]){re.escape(term)}(?![\w-])", low):
            if ban_span and ban_span[0] <= m.start() < ban_span[1]:
                continue
            return True
        return False

    hits = []
    for name, hexcode in showdesign.COLOR_NAMES:
        if mentioned(name):
            hits.append(name)
        elif mentioned(hexcode):
            hits.append(hexcode)
    brief["colours"] = hits[:4]
    return brief


def extract_brief(prompt: str, offline: bool = False) -> dict:
    """Free text -> {"brief": {...}, "source": "llm"|"fallback"}."""
    text = str(prompt or "").strip()
    if not text:
        return {"brief": _clean_brief({}), "source": "fallback"}
    if not offline and llm.available():
        try:
            answer = llm.chat([{"role": "system", "content": _BRIEF_SYSTEM},
                               {"role": "user", "content": text}])
            raw = _extract_json(str(answer.get("content") or ""))
            return {"brief": _clean_brief(raw), "source": "llm"}
        except (llm.LLMError, ValueError) as exc:
            return {"brief": _clean_brief(_brief_fallback(text)),
                    "source": "fallback",
                    "note": f"offline brief (AI: {exc})"[:300]}
    return {"brief": _clean_brief(_brief_fallback(text)), "source": "fallback"}


DESIGN_SYSTEM = """You are a lighting designer.  Design two or three genuinely
DIFFERENT show concepts for the brief, for the rig described below - each
with its own palette, dynamics and timing.

Each concept: a name, a one-line tagline, a palette of 3-5 "#rrggbb"
colours, and 4-8 cues in running order.  Each cue sets, for each DESIGN ROLE
of the rig that should be lit, a colour (from the palette, or "#ffffff") and
an intensity 0-100; roles left out are dark.  Give each cue a fade (seconds,
0.1-20) and a hold (seconds before an automatic next cue, 0 = wait for GO).
Respect colours the brief bans.  Use only the roles listed."""

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tagline": {"type": "string"},
                "palette": {"type": "array", "items": {"type": "string"}},
                "cues": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "fade_s": {"type": "number"},
                        "hold_s": {"type": "number"},
                        "looks": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"role": {"type": "string"},
                                           "hex": {"type": "string"},
                                           "level": {"type": "number"}},
                            "required": ["role", "level"]}},
                    },
                    "required": ["name", "looks"]}},
            },
            "required": ["name", "cues"]}},
    },
    "required": ["concepts"],
}


def _rig_roles(eng) -> dict[str, list[int]]:
    roles: dict[str, list[int]] = {}
    if eng is None:
        return roles
    with eng.lock:
        for h in eng.patch:
            roles.setdefault(fixture_kind.design_role(h), []).append(h["head_no"])
    return roles


def _clean_concepts(raw: dict, roles: dict, stage: dict) -> list[dict]:
    """The model's concepts in the importer's format, or ValueError."""

    def num(v, lo, hi, default):
        try:
            return round(max(lo, min(hi, float(v))), 2)
        except (TypeError, ValueError):
            return default

    out = []
    for i, c in enumerate((raw.get("concepts") or [])[:3]):
        if not isinstance(c, dict):
            continue
        palette = [h for h in (showdesign._to_hex(str(x))
                               for x in c.get("palette") or []) if h][:6]
        cues = []
        for n, q in enumerate((c.get("cues") or [])[:10], 1):
            if not isinstance(q, dict):
                continue
            colours, intensity, active = {}, {}, []
            for look in q.get("looks") or []:
                if not isinstance(look, dict):
                    continue
                role = str(look.get("role") or "").strip().lower()
                if role not in roles:
                    continue
                level = int(num(look.get("level"), 0, 100, 0))
                colours[role] = showdesign._to_hex(str(look.get("hex") or "")) or "#ffffff"
                intensity[role] = level
                if level > 0:
                    active.append(role)
            if not intensity:
                continue
            cues.append({"n": len(cues) + 1,
                         "name": str(q.get("name") or f"Cue {n}")[:40],
                         "fade_s": num(q.get("fade_s"), 0.1, 30, 2.0),
                         "hold_s": num(q.get("hold_s"), 0, 300, 0.0),
                         "active": active, "colours": colours,
                         "intensity": intensity})
        if not cues:
            continue
        out.append({"id": i, "name": str(c.get("name") or f"Concept {i + 1}")[:48],
                    "tagline": str(c.get("tagline") or "")[:140],
                    "strategy": "ai",
                    "palette": [{"name": showdesign._name_of(h), "hex": h}
                                for h in palette],
                    "cues": cues, "stage": stage})
    if not out:
        raise ValueError("the AI returned no usable concepts")
    return out


def generate(prompt: str, variant: int = 0, offline: bool = False, eng=None) -> dict:
    """Brief -> 2-3 concepts for the rig.  Nothing touches the engine."""
    text = str(prompt or "").strip()
    roles = _rig_roles(eng)
    note = None
    if text and roles and not offline and llm.available():
        try:
            stage = showdesign._stage_from_patch(eng.patch, "goalpost") or {}
            rig = "\n".join(f"- role {r}: heads {_span(h)}" for r, h in roles.items())
            raw = llm.structured(
                [{"role": "system",
                  "content": DESIGN_SYSTEM + "\n\nRIG DESIGN ROLES:\n" + rig},
                 {"role": "user", "content": text}],
                "design", "Two or three show concepts for this rig.",
                DESIGN_SCHEMA, temperature=0.8)
            concepts = _clean_concepts(raw, roles, stage)
            brief = _clean_brief(_brief_fallback(text))
            return {"brief": brief, "source": "llm", "design": {
                "ok": True, "concepts": concepts, "brief": brief,
                "assumptions": [f"designed for the {sum(len(h) for h in roles.values())}"
                                " fixture(s) you have patched"],
                "questions": [], "variant_index": -1}}
        except (llm.LLMError, ValueError) as exc:
            note = f"template designer (AI: {exc})"[:300]
    taken = extract_brief(text, offline=True if note else offline)
    design = showdesign.design(taken["brief"], variant_index=int(variant))
    out = {"brief": taken["brief"], "source": taken["source"], "design": design}
    if note or taken.get("note"):
        out["note"] = note or taken["note"]
    return out
