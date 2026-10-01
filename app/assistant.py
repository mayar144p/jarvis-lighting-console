"""The AI assistant: a model that works the desk like an operator would.

The copilot (console_ai.py) is one shot - a message in, one list of steps
out.  The assistant works in a LOOP with tools: it looks at the rig, does
something, checks what the lights are doing now (level, colour, where each
beam lands), corrects itself, asks when it isn't sure, and remembers how you
like things.  It understands feelings as well as commands ("the drop needs
to hit harder", "calmer for the speeches") because the model decides the
colours, levels and movement itself.

    run_turn(eng, message, session="", image=None, preview=True)
        One request, any number of tool rounds.  Everything it does is one
        undo step; with `preview` it all happens in blind first (3D only)
        and the operator keeps it or throws it away.

SAFETY - the same allowlist as the copilot (plus a few edits that are
undoable): it can never arm the output, fire pyro / CO2 / lasers, save or
load shows, or delete anything; it is told to ask instead.
"""
from __future__ import annotations

import json
import threading
import time

from . import config, console_ai, llm, showdesign
from . import venue as venue_mod
from .engine import ACTIONS as ENGINE_ACTIONS

MAX_ROUNDS = 14
MAX_ACTIONS = 40
MAX_HISTORY = 12
MAX_NOTES = 40

# edits the assistant may make on top of the copilot's allowlist
EXTRA = {
    "clear_attrs": ("group",),
    "stop_fx": ("id", "fx", "programmer"),
    "fx_beats": ("id", "beats"),
    "fx_space": ("id", "space"),
    "run_gradient": ("colours", "space", "speed", "beats", "heads", "group"),
    "run_shape": ("id", "heads", "group", "speed", "size", "spread", "direction", "beats"),
    "shape_save": ("shape", "id"),
    "quick_set": ("page", "slot", "button"),
    "highlight": ("state", "solo"),
    "speed_master": ("level", "value"),
    "tempo_set": ("bpm",),
    "step_fx_run": ("id", "heads", "speed", "beats", "space"),
}
ALLOWED = {**console_ai.PARAMS, **{k: v for k, v in EXTRA.items() if k not in console_ai.DENY_ACTIONS}}
_UNKNOWN = [a for a in EXTRA if a not in ENGINE_ACTIONS]
if _UNKNOWN:
    raise RuntimeError(f"assistant actions unknown to the engine: {_UNKNOWN}")

TOOLS = [
    {"type": "function", "function": {
        "name": "look_at_rig",
        "description": "The rig as it is: every light by type, groups, the selection, palettes, cues, "
                       "running effects, the room (rigging, zones, marks) and the timeline.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "check_lights",
        "description": "What the lights are doing NOW, on the real output: level, colour, where each beam "
                       "lands (a zone of the room, or 'up / off the floor') and the effects on it. Use it "
                       "after changing things to check the result, and to answer questions.",
        "parameters": {"type": "object", "properties": {
            "heads": {"type": "array", "items": {"type": "integer"},
                      "description": "light numbers; leave out for every light"}}}}},
    {"type": "function", "function": {
        "name": "music",
        "description": "The music now: tempo and where it comes from, the room's sound (loudness, "
                       "bass, beats, drops) when a screen is listening, and the timeline's position "
                       "and part of the song.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "do",
        "description": "Run one console action (see ACTIONS in the instructions) with its parameters. "
                       "Most act on the SELECTION: select first (select_heads / select_group / "
                       "select_all). Returns what happened or the error.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": sorted(ALLOWED)},
            "params": {"type": "object", "description": "the action's keyword arguments, e.g. {\"level\": 80}"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "ask",
        "description": "Ask the operator something and stop here (when the request is unclear, or "
                       "before anything risky). Give 2-4 short answers they can tap.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}}},
            "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Keep a short note about how this operator likes things (\"warm white for "
                       "speeches\", \"never strobe the bar\"), or forget one by its number.",
        "parameters": {"type": "object", "properties": {
            "note": {"type": "string"}, "forget": {"type": "integer"}}}}},
]

SYSTEM = """You are Jarvis, the lighting operator's assistant inside a lighting desk.
You work the desk yourself through tools, like a skilled operator next to them.

How you work:
* Understand intent, not just words.  "The drop needs to hit harder", "more sunset",
  "calmer for the speeches", "this looks cheap" are requests: you decide colours,
  levels, movement, effects and timing, and say in one short sentence why.
* Look before you act (look_at_rig), act (do), then CHECK (check_lights) that the
  real lights do what you meant - beams on the right zone, the right colour, nothing
  dark that should be lit - and fix what isn't right.
* Be quick: put ALL the `do` calls for a step in ONE round (several tool calls at
  once), check once at the end, and stop.  Each round costs time.
* Aim at a zone by its name: aim_at {{"zone": "Dance floor"}}; marks with "mark".
* Ask (ask) when the request is unclear or could mean very different things
  ("the back truss or the upstage one?"), and before anything you aren't sure of.
* Suggest a next idea when it would help, in one line.
* A QUESTION ("why is head 7 dark?", "what's on the movers?") is answered, not
  acted on: check first (check_lights / look_at_rig), say what you found and why,
  and offer the fix - change nothing unless they asked for it.  Never guess.
* Remember (remember) lasting preferences the operator states or clearly shows.
* Keep replies short: what you did and why, in the operator's language.

Rules:
* Only real heads, groups, zones and effects from the rig.  Colour on lights with
  colour, pan/tilt on movers.
* You cannot arm the output, fire pyro / CO2 / confetti / lasers, save or load
  shows, or delete anything - say so and suggest how the operator can.
* Everything you do is previewed in 3D first and is one Ctrl+Z.

ACTIONS (for `do`; most act on the selection):
{actions}

Colour names you can use: {colours}
Named effects (run_fx name=...): {effects}
"""


# ---------------------------------------------------------------------------
# memory: notes about how this operator likes things (this computer)
# ---------------------------------------------------------------------------
_mem_lock = threading.Lock()


def _mem_path():
    return config.DATA / "assistant_memory.json"


def notes() -> list[str]:
    try:
        data = json.loads(_mem_path().read_text(encoding="utf-8"))
        return [str(n)[:200] for n in data.get("notes") or []][:MAX_NOTES]
    except (OSError, ValueError, AttributeError):
        return []


def _save_notes(lst: list[str]) -> None:
    path = _mem_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"notes": lst[-MAX_NOTES:]}, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# sessions: the conversation so far (text only - the tool chatter is not kept)
# ---------------------------------------------------------------------------
_sessions: dict[str, list[dict]] = {}


def _history(session: str) -> list[dict]:
    return _sessions.setdefault(session or "default", [])


def forget_session(session: str) -> None:
    _sessions.pop(session or "default", None)


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------
def _landing(eng, h: dict, fp: float, ft: float) -> tuple[float, float, float] | None:
    """Where a head's beam meets the floor for pan / tilt fractions: the
    floor point whose aim solution matches them best (None: up or away)."""
    v = venue_mod.normalise(eng.venue)
    r = v["room"]
    x0, x1 = (r.get("cx") or 0) - r["width"] / 2, (r.get("cx") or 0) + r["width"] / 2
    z0, z1 = r.get("back", -1), r.get("back", -1) + r["depth"]
    best = None

    def err(x, z):
        s = eng._aim_solve(h, x, 0.0, z, closest=True)
        if s is None:
            return None
        return abs(s[0] - fp) + abs(s[1] - ft)
    for i in range(13):
        for j in range(13):
            x, z = x0 + (x1 - x0) * i / 12, z0 + (z1 - z0) * j / 12
            e = err(x, z)
            if e is not None and (best is None or e < best[0]):
                best = (e, x, z)
    if best is None:
        return None
    # refine around the best cell
    step = max(x1 - x0, z1 - z0) / 12
    e0, bx, bz = best
    for _ in range(3):
        step /= 3
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                e = err(bx + dx * step, bz + dz * step)
                if e is not None and e < e0:
                    e0, bx, bz = e, bx + dx * step, bz + dz * step
    return (round(bx, 1), round(bz, 1), e0)


def _zone_at(eng, x: float, z: float) -> str | None:
    from .engine_roam import _inside
    for zn in venue_mod.normalise(eng.venue).get("zones") or []:
        if zn.get("points") and _inside(zn["points"], x, z):
            return zn.get("name") or zn["kind"]
    return None


def check_lights(eng, heads=None) -> dict:
    with eng.lock:
        rows = {r["n"]: r for r in eng._looks()}
        by = {h["head_no"]: h for h in eng.patch}
        want = [int(n) for n in heads] if heads else sorted(by)
        fx = eng._fx_public()
        out = []
        for n in want[:64]:
            h = by.get(n)
            if h is None:
                continue
            r = rows.get(n) or {}
            item = {"head": n, "name": f"{h.get('manufacturer') or ''} {h.get('model') or ''}".strip(),
                    "level": round(float(r.get("a") or 0) * 100), "colour": None}
            if r.get("on"):
                item["colour"] = showdesign._name_of(r.get("hex") or "#ffffff")
            if "pan" in h["map"] and "tilt" in h["map"] and isinstance(r.get("pan"), (int, float)):
                land = _landing(eng, h, float(r["pan"]), float(r.get("tilt") or 0.5))
                if land is None or land[2] > 0.06:
                    item["beam"] = "not on the floor (pointing up, at a wall or past the room)"
                else:
                    zone = _zone_at(eng, land[0], land[1])
                    item["beam"] = f"floor at x {land[0]} z {land[1]}" + (f" ({zone})" if zone else "")
            mine = [f["label"] for f in fx if n in (f.get("heads") or [])]
            if mine:
                item["effects"] = mine
            out.append(item)
    return {"lights": out, "blackout": eng.blackout, "master": eng.master}


def music(eng) -> dict:
    with eng.lock:
        out = {"tempo": eng.tempo_public(), "sound": None}
        snd = eng.sound_public()
        if snd.get("listening"):
            out["sound"] = snd.get("reading")
        tl = eng.timeline
        tr = eng._tl_transport()
        out["timeline"] = {"playing": tr.get("playing"), "position_s": round(tr.get("pos") or 0, 1),
                           "length_s": tl.get("length")}
        pos = tr.get("pos") or 0
        marks = sorted((m["t"], m.get("name") or "") for m in tl.get("markers") or [])
        here = [name for t, name in marks if t <= pos]
        if here:
            out["timeline"]["part"] = here[-1]
    return out


def _do(eng, action: str, params: dict) -> dict:
    action = str(action or "")
    if action in console_ai.DENY_ACTIONS or action not in ALLOWED:
        return {"ok": False, "error": f"'{action}' is not something the assistant may do - ask the operator"}
    params = params if isinstance(params, dict) else {}
    keep = {k: v for k, v in params.items() if k in ALLOWED[action]}
    dropped = sorted(set(params) - set(keep))
    if action == "set_colour" and keep.get("hex") is None:
        hx = console_ai._colour_hex(params.get("colour") or params.get("value") or "")
        if hx:
            keep = {"hex": hx}
    r = eng.act(action, **keep)
    out = {"ok": bool(r.get("ok")), "summary": r.get("summary") or r.get("error") or action}
    if not r.get("ok"):
        out["error"] = r.get("error")
    if dropped:
        out["ignored"] = dropped
    return out


# ---------------------------------------------------------------------------
# a turn
# ---------------------------------------------------------------------------
def _system(eng) -> str:
    ref = "\n".join(f"    {a}({', '.join(ALLOWED[a])})" for a in sorted(ALLOWED))
    text = SYSTEM.format(actions=ref, colours=", ".join(n for n, _h in showdesign.COLOR_NAMES[:24]),
                         effects=", ".join(sorted(console_ai.fxlib.FX)))
    mem = notes()
    if mem:
        text += "\nWhat this operator likes (your notes):\n" + "\n".join(f"  {i + 1}. {n}" for i, n in enumerate(mem))
    return text + "\n\nTHE RIG NOW:\n" + console_ai.rig_context(eng)


def run_turn(eng, message: str, session: str = "", image: str | None = None,
             preview: bool = True, chat=None) -> dict:
    """One request.  `chat` replaces llm.chat (tests)."""
    chat = chat or llm.chat
    message = str(message or "").strip()[:2000]
    if not message:
        return {"ok": False, "error": "say what you'd like"}
    hist = _history(session)
    user: dict = {"role": "user", "content": message}
    if image and str(image).startswith("data:image/") and len(image) < 3_000_000:
        user = {"role": "user", "content": [{"type": "text", "text": message},
                                            {"type": "image_url", "image_url": {"url": image}}]}
    msgs = [{"role": "system", "content": _system(eng)}, *hist[-MAX_HISTORY:], user]

    with eng.lock:
        before = eng._undo_state()
        top = eng._undo[-1] if eng._undo else None
        was_blind = eng.blind_public()["on"]
    blind_started = False
    steps: list[dict] = []
    question = None
    options: list[str] = []
    reply = ""
    changed = False
    t0 = time.monotonic()
    try:
        for _round in range(MAX_ROUNDS):
            msg = chat(msgs, tools=TOOLS)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = str(msg.get("content") or "").strip()
                break
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            stop = False
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) \
                        else (fn.get("arguments") or {})
                except ValueError:
                    args = {}
                if name == "look_at_rig":
                    result = {"rig": console_ai.rig_context(eng)}
                elif name == "check_lights":
                    result = check_lights(eng, args.get("heads"))
                elif name == "music":
                    result = music(eng)
                elif name == "do":
                    if len(steps) >= MAX_ACTIONS:
                        result = {"ok": False, "error": "that's enough steps for one request - finish up"}
                    else:
                        if preview and not was_blind and not blind_started:
                            eng.act("blind", state=True)           # 3D only until the operator keeps it
                            blind_started = True
                        # some models put the arguments next to "action" instead of in "params"
                        params = args.get("params") if isinstance(args.get("params"), dict) and args.get("params") \
                            else {k: v for k, v in args.items() if k not in ("action", "params")}
                        result = _do(eng, args.get("action"), params)
                        steps.append({"action": args.get("action"), "params": params,
                                      "ok": result["ok"], "summary": result.get("summary")})
                        changed = changed or result["ok"]
                elif name == "ask":
                    question = str(args.get("question") or "").strip()[:300] or "Which one?"
                    options = [str(o)[:60] for o in (args.get("options") or [])][:4]
                    result = {"asked": True}
                    stop = True
                elif name == "remember":
                    with _mem_lock:
                        lst = notes()
                        if args.get("forget"):
                            k = int(args["forget"]) - 1
                            if 0 <= k < len(lst):
                                lst.pop(k)
                        if args.get("note"):
                            lst.append(str(args["note"]).strip()[:200])
                        _save_notes(lst)
                    result = {"notes": len(lst)}
                    steps.append({"action": "remember", "params": {"note": args.get("note"), "forget": args.get("forget")},
                                  "ok": True, "summary": "noted" if args.get("note") else "forgot a note"})
                else:
                    result = {"error": f"no tool {name!r}"}
                msgs.append({"role": "tool", "tool_call_id": call.get("id") or name, "name": name,
                             "content": json.dumps(result, default=str)[:12000]})
            if stop:
                reply = str(msg.get("content") or "").strip()
                break
        else:
            reply = reply or "I've done what I could in one go - tell me what to change."
    except llm.LLMError as exc:
        if blind_started:
            with eng.lock:
                eng._restore_state(before)
            eng.act("blind", state=False)
            _collapse(eng, top, before, None)
        return {"ok": False, "error": str(exc), "steps": steps}
    # everything it did is one undo step
    _collapse(eng, top, before, f"AI: {message[:40]}" if changed else None)
    if blind_started and not changed:
        eng.act("blind", state=False)
        blind_started = False
    hist.append({"role": "user", "content": message})
    hist.append({"role": "assistant", "content": (reply or question or "")[:1500]})
    del hist[:-MAX_HISTORY * 2]
    return {"ok": True, "reply": reply, "question": question, "options": options, "steps": steps,
            "changed": changed, "preview": blind_started, "seconds": round(time.monotonic() - t0, 1)}


def _collapse(eng, top, before, label: str | None) -> None:
    """The turn's own undo steps become one (or none, if nothing changed)."""
    from .engine_base import UNDO_LIMIT
    with eng.lock:
        k = next((i for i in range(len(eng._undo) - 1, -1, -1) if eng._undo[i] is top), -1) if top else -1
        added = len(eng._undo) - (k + 1)
        if not added:
            return
        del eng._undo[k + 1:]
        if label:
            eng._undo.append({"action": "assistant", "label": label, "state": before, "at": eng._clock()})
            if len(eng._undo) > UNDO_LIMIT:
                del eng._undo[0]
            eng._undo_label = label

