"""AI Console Compiler: plain text -> strict JSON console steps.

Four entry points:

* plan(message)        compile one instruction into the strict schema
                       {"reply", "steps":[{"target", "action",
                       "attributes", "fx", "timing"}]}.  Uses the LLM
                       when a key is configured, otherwise (or when asked
                       with offline=True) a deterministic keyword compiler,
                       so the panel, the E2E and offline installs never
                       depend on a live model.
* resolve(steps, eng)  expand targets into concrete, visible engine calls
                       - selection targets insert explicit select_* steps,
                       so a script never relies on hidden state.
* run(calls, engine)   execute the calls in order, stopping at the first
                       failure; every step reports ok/summary.
* brief/generate       show-from-a-prompt: extract a showdesign brief from
                       free text (LLM, then keyword fallback) and run
                       showdesign.design() to get 2-3 concepts.  The
                       concepts reach the engine only through the
                       operator's explicit confirm (import_show route).

SAFETY - steps are validated against an allowlist BEFORE anything can
reach the engine.  Not reachable from the AI at all: arming output
(set_output), show files (save/load), imports (import_show/import_scan),
bulk patch replacement (patch_clear/patch_from_*) and destructive edits
(remove_heads/group_delete).  Those stay on the operator's own buttons -
the ask-first rule.  Everything that IS allowed is non-destructive.
"""
from __future__ import annotations

import json
import re

from . import llm, showdesign
from .engine import ACTIONS as ENGINE_ACTIONS

MAX_STEPS = 16
MAX_REPLY = 240

# Actions deliberately NOT in the AI's reach (see module docstring).
# Kept as an explicit set so the error message can explain why.
DENY_ACTIONS = frozenset({
    "set_output", "save_show", "load_show", "import_show", "import_scan",
    "patch_clear", "patch_from_csv", "remove_heads",
    "group_delete",
})

# The allowlist = exactly the actions we can describe honestly, each with
# the keyword arguments its engine handler accepts.  An engine action that
# is missing here is simply not reachable from the AI.
PARAMS: dict[str, tuple[str, ...]] = {
    "status": (),
    "blackout": ("state",),
    "master": ("level",),
    "locate": (),
    "clear_programmer": (),
    "clear_selection": (),
    "select_heads": ("head", "head_end"),
    "select_group": ("group",),
    "select_all": (),
    "set_intensity": ("level", "fade"),
    "set_attribute": ("attribute", "value"),
    "set_colour": ("hex", "colour", "value"),
    "set_position": ("pan", "tilt"),
    "record_palette": ("kind", "name"),
    "include_palette": ("kind", "palette"),
    "record_cue": ("playback", "name", "fade", "hold"),
    "cue_go": ("playback",),
    "cue_back": ("playback",),
    "cue_forward": ("playback",),
    "playback_level": ("playback", "level"),
    "playback_release": ("playback",),
    "playback_activate": ("playback",),
    "follow_set": ("playback", "delay", "on", "pause", "loop"),
    "group_create": ("name", "heads"),
    "run_fx": ("attribute", "kind", "wave", "speed", "spread", "phase",
               "base", "depth", "duration", "heads", "group"),
    "stop_fx": ("id", "fx"),
    "add_heads": ("query", "fixture_id", "mode", "qty", "universe",
                  "address", "role", "kind", "name", "x", "y", "z"),
    "auto_patch": (),
    "patch_list": ("fixtures", "start"),
    "set_address": ("head", "universe", "address"),
}

# Fail loudly at import if the allowlist drifts out of sync with the engine
# (selftest imports this module, so a mismatch fails the suite).
_UNKNOWN = [a for a in PARAMS if a not in ENGINE_ACTIONS]
if _UNKNOWN:
    raise RuntimeError(f"console_ai allowlist unknown to engine: {_UNKNOWN}")

ALLOWED_ACTIONS = tuple(sorted(PARAMS))

# Actions that act on the selected heads - these are the ones that need an
# explicit select_* call when the target says so.
SELECTION_ACTIONS = frozenset({
    "set_intensity", "set_attribute", "set_colour", "set_position",
    "locate", "record_palette", "include_palette", "run_fx",
})


def _param_ref() -> str:
    lines = [f"    {action}({', '.join(PARAMS[action])})"
             for action in ALLOWED_ACTIONS]
    return "\n".join(lines)


SYSTEM = f"""You are Jarvis, the AI compiler built into a professional lighting
console.  Translate the operator's instruction into ONE strict JSON object
and nothing else - no markdown, no prose outside the JSON:

{{"reply": "<one short sentence shown in the transcript>",
 "steps": [{{"target": "...", "action": "...", "attributes": {{}},
            "fx": {{}}, "timing": {{}}}}]}}

Field rules
  target    "auto" (use the selection if any, else all), "selection",
            "all", "group N", "heads A-B", "playback N" or "programmer".
  action    exactly one of these engine actions:
{_param_ref()}
  attributes  keyword arguments for that action (numbers as JSON numbers).
            level 0-100; value 0-255; attribute = engine role (dimmer,
            red, green, blue, white, pan, tilt, shutter, gobo, zoom,
            speed, ...); colour as "#rrggbb" (hex) or a colour name;
            qty 1-64 with a real fixture query for add_heads;
            playback 1-10.
  fx        only for run_fx: attribute (role), kind (sine|saw|square|
            triangle|random), speed Hz 0.01-20, spread degrees, phase
            degrees, base, depth, duration seconds, group N, heads [...].
  timing    {{"fade": seconds}} for set_intensity/record_cue or
            {{"duration": seconds}} for run_fx.

General rules
  * Selection-based actions (set_intensity, set_colour, set_attribute,
    set_position, locate, record_palette, run_fx) need heads selected:
    give the step target "all"/"group N"/"heads A-B", or emit a select_*
    step first.
  * 1-6 steps, only what was asked for; read numbers and colours from the
    operator's words; never invent addresses or show names.
  * You cannot save/load shows, touch the patch destructively or arm
    output - the operator keeps those buttons.
  * reply <= 120 characters, in the operator's language.
"""

# ---------------------------------------------------------------------------
# JSON extraction + validation
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (bare or fenced)."""
    body = str(text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    if not body.startswith("{"):
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model reply contains no JSON object")
        body = body[start:end + 1]
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("model reply JSON is not an object")
    return data


def _is_value(key: str, value) -> bool:
    """A parameter value: scalar, list of scalars (heads), fixture rows."""
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        if all(isinstance(v, (str, int, float, bool)) for v in value):
            return True
        # patch_list(fixtures=[{query, qty, ...}, ...]) - the dynamic
        # addressing list form; rows must themselves be flat.
        if key == "fixtures" and all(isinstance(v, dict) for v in value):
            return all(_is_value(k, v) for v in value
                       for k, v in v.items())
    return False


def _validate(raw) -> dict:
    """Normalise a plan dict; raises ValueError with a step-precise message."""
    if not isinstance(raw, dict):
        raise ValueError("plan must be a JSON object")
    reply = str(raw.get("reply") or "").strip() or "OK."
    if len(reply) > MAX_REPLY:
        reply = reply[:MAX_REPLY - 1] + "\u2026"
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
        steps.append({
            "target": str(row.get("target") or "auto").strip(),
            "action": action,
            "attributes": attributes,
            "fx": fx,
            "timing": timing,
        })
    return {"reply": reply, "steps": steps}


# ---------------------------------------------------------------------------
# plan(): LLM first, deterministic keyword compiler as the floor
# ---------------------------------------------------------------------------

def plan(message: str, offline: bool = False) -> dict:
    """Compile one instruction -> {"reply", "steps", "source"[, "note"]}."""
    text = str(message or "").strip()
    if not text:
        return {"reply": "Type an instruction - e.g. 'blackout', "
                         "'everything to 70%' or 'rainbow across the rig'.",
                "steps": [], "source": "fallback"}
    if not offline and llm.available():
        try:
            answer = llm.chat([{"role": "system", "content": SYSTEM},
                               {"role": "user", "content": text}])
            out = _validate(_extract_json(str(answer.get("content") or "")))
            out["source"] = "llm"
            return out
        except (llm.LLMError, ValueError) as exc:
            # JSONDecodeError is a ValueError; both fall through to the
            # deterministic compiler so the panel always stays usable.
            out = _validate(_fallback(text))
            out["source"] = "fallback"
            out["note"] = f"offline compiler (LLM path: {exc})"[:300]
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
    group_hit = re.search(r"\bgroup\s*(\d+)\b", low)
    head_hit = re.search(r"\bheads?\s+(\d+\s*-\s*\d+|\d+(?:\s*,\s*\d+)*)", low)
    if group_hit:
        target = f"group {group_hit.group(1)}"
    elif head_hit:
        target = "heads " + head_hit.group(1).replace(" ", "")
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
    fader = None
    if pb:
        fader = re.search(r"(\d{1,3})\s*%", low[master.end() if master
                                                else 0:])
        if fader:
            base = master.end() if master else 0
            consumed.append((base + fader.start(), base + fader.end()))
            add(target, "playback_level",
                {"playback": int(pb.group(1)),
                 "level": max(0, min(100, int(fader.group(1))))})

    # --- cues -------------------------------------------------------------
    if re.search(r"\b(?:previous|last|back)\s+cue\b|\bcue\s+back\b", low):
        add(target, "cue_back")
    elif re.search(r"\bgo\b|\bnext\s+cue\b", low):
        add(target, "cue_go")

    # --- colour: first mentioned colour (skipping avoid/no constructions) -
    hex_match = re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", low)
    colour_hits = []
    for name, hexcode in sorted(showdesign.COLOR_NAMES,
                                key=lambda item: -len(item[0])):
        for m in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", low):
            before = low[max(0, m.start() - 12):m.start()]
            if re.search(r"\b(?:no|not|without|avoid|except|never)\s+$",
                         before):
                continue                    # "no red" is a ban, not a pick
            colour_hits.append((m.start(), name, hexcode))
    if hex_match and (not colour_hits
                      or hex_match.start() < colour_hits[0][0]):
        add(target, "set_colour", {"hex": hex_match.group(0)})
    elif colour_hits:
        colour_hits.sort()
        # Emit hex, not the name: set_colour's contract is #rrggbb
        # (entries are (pos, name, hexcode)).
        add(target, "set_colour", {"hex": colour_hits[0][2]})

    # --- intensity --------------------------------------------------------
    pct = free_percent()
    if pct:
        consumed.append((pct.start(), pct.end()))
        add(target, "set_intensity",
            {"level": max(0, min(100, int(pct.group(1))))})
    elif re.search(r"\b(?:everything|all|lights|rig)\s+(?:to\s+)?full\b",
                   low) or re.search(r"\bat full\b|\bfull (?:level|intensity)",
                                     low):
        add(target, "set_intensity", {"level": 100})
    elif re.search(r"\b(?:everything|all)\s+(?:to\s+)?off\b", low):
        add(target, "set_intensity", {"level": 0})

    if re.search(r"\blocate\b|\bflash\s+(?:them|the heads|the fixtures)\b",
                 low):
        add(target, "locate")

    # --- effects ----------------------------------------------------------
    speed = 1.5
    explicit = re.search(r"(\d+(?:\.\d+)?)\s*(?:hz|cycles?\b|per\s+sec)",
                         low)
    if explicit:
        speed = float(explicit.group(1))
    elif re.search(r"\bslow\b", low):
        speed = 0.4
    elif re.search(r"\bfast\b|\bquick\b|\bpunchy\b", low):
        speed = 6.0
    spread = 180.0 if re.search(
        r"\bacross\b|\bcascade|\baround\b|\bfollow\b|\bwave\b", low) else 0.0
    duration = None
    dur_match = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*"
                          r"(?:s\b|sec\b|seconds?\b)", low)
    if dur_match:
        duration = float(dur_match.group(1))

    def fx_step(attribute: str, kind: str, phase: float = 0.0) -> None:
        row = {"attribute": attribute, "kind": kind, "speed": speed,
               "spread": spread, "phase": phase}
        if duration is not None:
            row["duration"] = duration
        add(target, "run_fx", {}, row)

    if re.search(r"\brainbow\b|colou?r\s+(?:chase|cycle|wheel)", low):
        fx_step("red", "sine", 0)
        fx_step("green", "sine", 120)
        fx_step("blue", "sine", 240)
    elif re.search(r"\b(circle|circles|rotate|rotation|spin|spinning|"
                   r"swirl|figure\s?8|pan)\b", low):
        fx_step("pan", "sine", 0)
        fx_step("tilt", "sine", 90)
    elif re.search(r"\b(pulse|breathe|breathing|throb|sine)\b", low):
        fx_step("dimmer", "sine")
    elif re.search(r"\b(chase|sweep|swipe|build|saw)\b", low):
        fx_step("dimmer", "saw")
    elif re.search(r"\b(strobe|blink|flash|blinker)\b", low):
        fx_step("dimmer", "square", 0)
        steps[-1]["fx"]["speed"] = max(speed, 8.0)
    elif re.search(r"\b(twinkle|sparkle|random|flicker|shimmer)\b", low):
        fx_step("dimmer", "random")
    elif re.search(r"\b(?:run|start)\s+(?:an?\s+)?"
                   r"(?:effect|fx|wave)\b", low):
        fx_step("dimmer", "sine")

    # --- reply ------------------------------------------------------------
    if steps:
        labels = "; ".join(_label(s) for s in steps)
        reply = f"Compiled {len(steps)} step(s): {labels}."
    else:
        reply = ("I could not compile that. Try: 'blackout', 'everything "
                 "to 70%', 'full red on the rig', 'locate', 'rainbow "
                 "across the rig', 'stop effects', 'go', 'add 4 pars'.")
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
        return f"{fx.get('kind', 'sine')} {fx.get('attribute', 'dimmer')} fx"
    if action == "add_heads":
        return f"{at.get('qty', 1)} x {at.get('query', 'fixture')}"
    if action == "master":
        return f"master {at.get('level')}%"
    if action == "playback_level":
        return (f"playback {at.get('playback')} "
                f"{at.get('level')}%")
    if action == "stop_fx":
        return "stop fx"
    if action.startswith("select_") or action.startswith("clear_"):
        return action.replace("_", " ")
    return action.replace("_", " ")


# ---------------------------------------------------------------------------
# resolve(): targets -> concrete calls (explicit, visible select_* steps)
# ---------------------------------------------------------------------------

def _colour_hex(value) -> str | None:
    """Colour name or #hex -> canonical #rrggbb, or None if unknown.

    showdesign._to_hex already maps '#f00' / '#ff0000' / colour names, so
    this is a thin wrapper. Unknown values pass through untouched so the
    engine still raises its own "bad colour" error with step context.
    """
    return showdesign._to_hex(str(value or ""))


def resolve(steps: list[dict], eng=None) -> list[dict]:
    """Expand validated steps into ordered engine calls.

    Each call is {"step": n, "action": ..., "params": {...}}.  Selection
    targets become explicit select_* calls (deduplicated between steps),
    "playback N" injects the playback kwarg, run_fx takes group/heads
    natively.  With an engine, target "auto" reads the live selection and
    falls back to select_all when nothing is selected.
    """
    calls: list[dict] = []
    last_select: tuple | None = None

    for idx, step in enumerate(steps, 1):
        action = step["action"]
        params = {**(step.get("attributes") or {}),
                  **(step.get("fx") or {}),
                  **(step.get("timing") or {})}
        target = str(step.get("target") or "auto").strip().lower()
        select: tuple | None = None

        # The system prompt lets the model answer with a colour NAME
        # ("red") but set_colour's contract is #rrggbb - translate here so
        # both the LLM and fallback paths reach the engine valid.
        if action == "set_colour":
            raw = params.get("hex") or params.get("colour") \
                or params.get("value")
            hx = _colour_hex(raw)
            if hx:
                params["hex"] = hx
                params.pop("colour", None)
                params.pop("value", None)

        def group_num(where: str) -> int:
            n = int(re.sub(r"\D", "", where) or 0)
            if n < 1:
                raise ValueError(f"step {idx}: target {where!r} needs a "
                                 "group number")
            return n

        if target in ("auto", "selection", "selected", "programmer", ""):
            if (target == "auto" and eng is not None
                    and action in SELECTION_ACTIONS and not eng.selected):
                select = ("select_all", {})
        elif target in ("all", "everyone", "everything", "rig"):
            if action in SELECTION_ACTIONS:
                select = ("select_all", {})
        elif target.startswith("group"):
            n = group_num(target)
            if action == "run_fx":
                params.setdefault("group", n)
            elif action == "select_group":
                params.setdefault("group", n)
            elif action in SELECTION_ACTIONS:
                select = ("select_group", {"group": n})
        elif target.startswith("heads") or target[:1].isdigit():
            nums = sorted({int(x) for x in re.findall(r"\d+", target)})
            if not nums:
                raise ValueError(f"step {idx}: target {target!r} needs "
                                 "head numbers")
            contiguous = nums == list(range(nums[0], nums[-1] + 1))
            if action == "run_fx":
                params.setdefault("heads", nums)
            elif action == "select_heads":
                params.setdefault("head", nums[0])
                if len(nums) > 1:
                    params.setdefault("head_end", nums[-1])
            elif len(nums) == 1:
                select = ("select_heads", {"head": nums[0]})
            elif contiguous:
                select = ("select_heads",
                          {"head": nums[0], "head_end": nums[-1]})
            else:
                raise ValueError(
                    f"step {idx}: heads {nums} are not contiguous - "
                    "use a range like 'heads 1-4'")
        elif target.startswith(("playback", "pb", "fader")):
            n = group_num(target)
            params.setdefault("playback", n)
        else:
            raise ValueError(f"step {idx}: unknown target "
                             f"{step['target']!r}")

        # A select_* step IS the selection update - track it for dedupe.
        if action == "select_all":
            last_select = ("select_all", {})
        elif action == "select_group":
            last_select = ("select_group", {"group": params.get("group")})
        elif action == "select_heads":
            last_select = ("select_heads",
                           {"head": params.get("head"),
                            "head_end": params.get("head_end")})

        if select is not None and select != last_select:
            calls.append({"step": idx, "action": select[0],
                          "params": select[1]})
            last_select = select
        calls.append({"step": idx, "action": action, "params": params})
    return calls


def run(calls: list[dict], eng) -> dict:
    """Execute calls in order; stop at the first failure."""
    results: list[dict] = []
    for call in calls:
        res = eng.act(call["action"], **call["params"])
        results.append({
            "step": call["step"],
            "action": call["action"],
            "params": call["params"],
            "ok": bool(res.get("ok")),
            "summary": str(res.get("summary") or res.get("error") or ""),
        })
        if not res.get("ok"):
            return {"ok": False, "executed": len(results) - 1,
                    "steps_run": results,
                    "error": str(res.get("error") or "step failed")}
    return {"ok": True, "executed": len(results), "steps_run": results}


# ---------------------------------------------------------------------------
# show generation: prompt -> brief -> showdesign.design()
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

_BRIEF_KEYS = ("mood", "event", "pace", "structure", "colours", "avoid")
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
        """Colour really named - matches inside the ban clause don't count."""
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
            if not isinstance(raw, dict):
                raise ValueError("brief JSON is not an object")
            return {"brief": _clean_brief(raw), "source": "llm"}
        except (llm.LLMError, ValueError) as exc:
            return {"brief": _clean_brief(_brief_fallback(text)),
                    "source": "fallback",
                    "note": f"offline brief (LLM path: {exc})"[:300]}
    return {"brief": _clean_brief(_brief_fallback(text)),
            "source": "fallback"}


def generate(prompt: str, variant: int = 0, offline: bool = False) -> dict:
    """prompt -> brief -> showdesign.design() concepts for the confirm step.

    The returned concepts only reach the engine if the operator presses
    confirm (POST /api/console/import_show) - never automatically.
    """
    taken = extract_brief(prompt, offline=offline)
    design = showdesign.design(taken["brief"], variant_index=int(variant))
    return {"brief": taken["brief"], "source": taken["source"],
            "design": design, **({"note": taken["note"]}
                                 if taken.get("note") else {})}
