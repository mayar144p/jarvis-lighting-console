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

SAFETY (A13): it may do what is undoable and previewed; going live, ARM,
firing effects, saving / opening shows, replacing the room and every delete
are only PREPARED - the operator taps to do them (app/aitools.py); network,
the lock and calibration it never touches.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import aitools, config, console_ai, llm, showdesign
from . import venue as venue_mod
from .engine import ACTIONS as ENGINE_ACTIONS
from .engine import Engine

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
    "fx_tweak": ("id", "speed", "size", "times", "params"),
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
# the rest of the desk (A13): more the AI may simply do, and what it may only
# prepare for the operator's tap (going live, arming, firing, saving, deleting)
ALLOWED.update({a: aitools.params_of(Engine, a) for a in aitools.MORE})
CONFIRMABLE = {a: aitools.params_of(Engine, a) for a in aitools.CONFIRM}
for _a in aitools.NEVER:
    ALLOWED.pop(_a, None)
_UNKNOWN = [a for a in [*EXTRA, *aitools.MORE, *aitools.CONFIRM] if a not in ENGINE_ACTIONS]
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
            "action": {"type": "string", "enum": sorted(set(ALLOWED) | set(CONFIRMABLE))},
            "params": {"type": "object", "description": "the action's keyword arguments, e.g. {\"level\": 80}"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "find_fixtures",
        "description": "Search the WHOLE fixture library (thousands of real lights, typos are fine) and "
                       "the lights already installed, by brand / model / type words: \"chauvet spot\", "
                       "\"intimidator 360\", \"robe pointe\". Returns the best matches with their "
                       "type and modes. Use it before adding a light.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "add_fixture",
        "description": "Install one match from find_fixtures (its src and key) and patch it: qty lights, "
                       "in a mode (leave out for the best one). New lights hang where that kind of light "
                       "goes; move them after (attach_heads / set_place).",
        "parameters": {"type": "object", "properties": {
            "src": {"type": "string"}, "key": {"type": "string"},
            "qty": {"type": "integer"}, "mode": {"type": "string"}},
            "required": ["src", "key"]}}},
    {"type": "function", "function": {
        "name": "what_lights_can_do",
        "description": "What lights CAN and CAN'T do, per model, from the desk (never from memory): "
                       "move, colour (mixing / wheel only / none), dim, strobe, gobo, zoom... Check before "
                       "promising anything, and tell the operator what some lights can't do.",
        "parameters": {"type": "object", "properties": {
            "heads": {"type": "array", "items": {"type": "integer"}},
            "group": {"type": "string", "description": "a group's number or name"}}}}},
    {"type": "function", "function": {
        "name": "place_lights",
        "description": "Hang lights on a truss / pipe / pole in words: on=\"front truss\" (a name, an id, "
                       "or where it is: front, back, left, right, middle), where=\"middle\" | \"left\" | "
                       "\"right\" | \"both ends\" | \"spread\" | a number 0-1 along it; height_m hangs "
                       "that rig at that height (or, without on, puts the lights at that height).",
        "parameters": {"type": "object", "properties": {
            "heads": {"type": "array", "items": {"type": "integer"}},
            "group": {"type": "string"}, "on": {"type": "string"}, "where": {"type": "string"},
            "height_m": {"type": "number"}}}}},
    {"type": "function", "function": {
        "name": "add_to_room",
        "description": "Add to the room in words: a truss / pipe / pole / tower / stand, an object (dj_booth, "
                       "bar, speaker, screen, riser, mark...) or a zone (dancefloor, standing, stage, bar, vip...), "
                       "at where=\"front\" | \"back\" | \"left\" | \"right\" | \"middle\" | \"front left\"...; "
                       "sizes in metres (a truss runs across the room unless across=false).",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string"}, "name": {"type": "string"}, "where": {"type": "string"},
            "length_m": {"type": "number"}, "width_m": {"type": "number"}, "depth_m": {"type": "number"},
            "height_m": {"type": "number"}, "across": {"type": "boolean"}}, "required": ["kind"]}}},
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
  dark that should be lit - and fix what isn't right.  When you have see_3d, look at the
  3D view once at the end too, and fix what looks wrong.
* Be quick: put ALL the `do` calls for a step in ONE round (several tool calls at
  once), check once at the end, and stop.  Each round costs time.
* Aim at a zone by its name: aim_at {{"zone": "Dance floor"}}; marks with "mark".
* Adding lights: find_fixtures with the operator's words first.  One clear match
  (or they named the exact model): add_fixture.  Several different models fit
  ("chauvet spot" -> Rogue R1 Spot, Rogue R2 Spot...): ask which, with the
  model names as the options (up to 4, the closest first).  No exact match (the
  result says so): never add a near model in its place - say that model isn't in
  the library and ask, offering the nearest names.
* Ask (ask) when the request is unclear or could mean very different things
  ("the back truss or the upstage one?"), and before anything you aren't sure of.
* Suggest a next idea when it would help, in one line.
* A QUESTION ("why is head 7 dark?", "what's on the movers?") is answered, not
  acted on: check first (check_lights / look_at_rig), say what you found and why,
  and offer the fix - change nothing unless they asked for it.  Never guess.
* Remember (remember) lasting preferences the operator states or clearly shows.
* Keep replies short: what you did and why, in the operator's language.

Rules:
* DESK ONLY.  You run this lighting desk: the lights, the show, the room, the music
  for the show.  Anything else (general chat, homework, news, code, other apps) gets
  ONE short line and nothing more, e.g. "I only run the desk - try: 'warm wash on the
  movers'".  No essays.
* HONEST ABOUT EACH LIGHT, FROM THE DESK.  What a light can do comes from the desk
  (WHAT EACH LIGHT CAN DO below, what_lights_can_do), never from what you remember of
  a model.  Do what is possible and say plainly what isn't: "Done on the 8 movers; the
  6 PARs can't tilt, so they stay."  A `do` result's "cant" lists the lights it couldn't
  touch - always pass that on.
* Only real heads, groups, zones and effects from the rig.
* Placing lights: place_lights ("middle of the front truss", "both ends", "6 m high").
  The room: add_to_room (a truss, a pole, a zone, a mark), venue_update / venue_rig to
  change one; groups, cues, playbacks, buttons, timeline, palettes, macros: `do` with
  their actions (make, edit, rename, move).
* THE OPERATOR CONFIRMS: going live, ARM, firing confetti / CO2 / flame / sparks /
  fog / lasers, saving or opening a show or venue, replacing the room, changing a
  light's type, and EVERY delete.  You may `do` these: they are PREPARED, not done -
  the operator gets a button to tap.  Say what you prepared and that they tap to do it.
* Everything else you do is previewed in 3D first and is one Ctrl+Z.

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


def _do(eng, action: str, params: dict, waiting: list | None = None) -> dict:
    action = str(action or "")
    params = params if isinstance(params, dict) else {}
    if action in CONFIRMABLE:
        if waiting is None:
            return {"ok": False, "error": f"'{action}' needs the operator - ask them"}
        item = aitools.prepare(action, {k: v for k, v in params.items() if k in CONFIRMABLE[action]})
        waiting.append(item)
        return {"ok": True, "prepared": item["label"],
                "summary": f"prepared for the operator to confirm: {item['label']}"}
    if action in aitools.NEVER or action not in ALLOWED or \
            (action in console_ai.DENY_ACTIONS and action not in aitools.MORE):
        return {"ok": False, "error": f"'{action}' is not something the assistant may do - ask the operator"}
    keep = {k: v for k, v in params.items() if k in ALLOWED[action]}
    dropped = sorted(set(params) - set(keep))
    if action == "set_colour" and keep.get("hex") is None:
        hx = console_ai._colour_hex(params.get("colour") or params.get("value") or "")
        if hx:
            keep = {"hex": hx}
    cant = aitools.cant_do(eng, action, keep)
    r = eng.act(action, **keep)
    out = {"ok": bool(r.get("ok")), "summary": r.get("summary") or r.get("error") or action}
    if not r.get("ok"):
        out["error"] = r.get("error")
    elif cant:
        out["cant"] = cant
        out["summary"] += " - but " + "; ".join(cant)      # the operator reads it too
    for k in ("cue", "contains", "report", "findings", "items", "info", "versions", "pieces", "id"):
        if k in r and k not in out:                        # what a read-only action found
            out[k] = r[k]
    if dropped:
        out["ignored"] = dropped
    return out


def find_fixtures(eng, query: str, limit: int = 8) -> dict:
    """The best library + installed matches for some words (for the AI)."""
    from . import fixlib, fixtures
    query = str(query or "").strip()
    if not query:
        return {"matches": [], "error": "say what to look for"}
    seen, out = set(), []
    try:
        with fixtures.db(eng.db_path) as conn:
            installed = {(str(r["manufacturer"]).lower(), str(r["model"]).lower())
                         for r in conn.execute("SELECT manufacturer, model FROM fixtures")}
    except Exception:                                  # (the badge is a nicety)
        installed = set()
    from . import searchmatch
    for r in fixlib.search(query, limit=40):
        name = f"{r['manufacturer']} {r['model']}"
        hit = searchmatch.score(query, r["manufacturer"], r["model"], r.get("type", "")) or (9, 9, 9)
        if name.lower() in seen:                       # one row per model (several libraries)
            continue
        seen.add(name.lower())
        out.append({"name": name, "type": r.get("type") or "", "src": r["src"], "key": r["key"],
                    "modes": [f"{m[0]} ({m[1]} ch)" for m in (r.get("modes") or [])][:6],
                    "installed": (r["manufacturer"].lower(), r["model"].lower()) in installed,
                    # every word they said is in the name (typos allowed); not
                    # exact = a word is missing: a near model, never the one
                    # they named
                    "exact": hit[0] == 0})
        if len(out) >= limit:
            break
    exact = [m for m in out if m["exact"]]
    note = ("" if exact else
            "NO EXACT MATCH: none of these is the light they named - say so, and ask with the nearest names"
            if out else "nothing in the library matches - say so")
    return {"matches": exact or out, "exact": len(exact), "note": note}


def add_fixture(eng, src: str, key: str, qty=1, mode=None) -> dict:
    """Install a library fixture (as the Add dialog does) and patch it."""
    from . import engine as engine_mod, fixlib, fixtures
    try:
        parsed = fixlib.load(str(src), str(key))
        done = fixtures.store_parsed(eng.db_path, parsed, f"{src}:{key}")
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    fixtures.invalidate_cache()
    engine_mod._FIXTURE_CACHE.clear()
    fid = ((done.get("imported") or [{}])[0]).get("fixture_id")
    if not fid:
        return {"ok": False, "error": "that fixture could not be installed"}
    params = {"fixture_id": fid, "qty": max(1, min(64, int(qty or 1)))}
    if mode:
        params["mode"] = str(mode)
    return _do(eng, "add_heads", params)


# ---------------------------------------------------------------------------
# a turn
# ---------------------------------------------------------------------------
def _system(eng) -> str:
    ref = "\n".join(f"    {a}({', '.join(ALLOWED[a])})" for a in sorted(ALLOWED))
    ref += "\n  prepared for the operator's tap:\n" + "\n".join(
        f"    {a}({', '.join(CONFIRMABLE[a])})" for a in sorted(CONFIRMABLE))
    text = SYSTEM.format(actions=ref, colours=", ".join(n for n, _h in showdesign.COLOR_NAMES[:24]),
                         effects=", ".join(sorted(console_ai.fxlib.FX)))
    mem = notes()
    if mem:
        text += "\nWhat this operator likes (your notes):\n" + "\n".join(f"  {i + 1}. {n}" for i, n in enumerate(mem))
    caps = aitools.capabilities(eng)["models"]
    if caps:
        text += "\n\nWHAT EACH LIGHT CAN DO (from the desk):\n" + "\n".join(
            f"  {m['model']} (heads {', '.join(map(str, m['heads'][:24]))}): can {', '.join(m['can'])}"
            + (f"; can't {', '.join(m['cannot'])}" if m["cannot"] else "") for m in caps)
    return text + "\n\nTHE RIG NOW:\n" + console_ai.rig_context(eng)


SEE_TOOL = {"type": "function", "function": {
    "name": "see_3d",
    "description": "A fresh picture of the 3D view as it is NOW, after your changes (the "
                   "operator's screen draws it).  Use it once at the end to judge the look - "
                   "beams, colours, where light lands - and fix what looks wrong.",
    "parameters": {"type": "object", "properties": {}}}}
MAX_VIEWS = 2                # pictures per request
PENDING_S = 120.0            # a turn waiting for its picture is dropped after this
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()


def run_turn(eng, message: str, session: str = "", image: str | None = None,
             preview: bool = True, chat=None, can_see: bool = False) -> dict:
    """One request.  `chat` replaces llm.chat (tests).  `can_see`: the
    screen asking can draw the 3D view, so the AI may ask to see it after
    its changes (the turn then pauses: resume_turn carries the picture)."""
    message = str(message or "").strip()[:2000]
    if not message:
        return {"ok": False, "error": "say what you'd like"}
    _finish_pending(eng, session)
    hist = _history(session)
    user: dict = {"role": "user", "content": message}
    if image and str(image).startswith("data:image/") and len(image) < 3_000_000:
        user = {"role": "user", "content": [{"type": "text", "text": message},
                                            {"type": "image_url", "image_url": {"url": image}}]}
    with eng.lock:
        st = {"message": message, "session": session, "preview": preview, "can_see": bool(can_see),
              "msgs": [{"role": "system", "content": _system(eng)}, *hist[-MAX_HISTORY:], user],
              "before": eng._undo_state(), "top": eng._undo[-1] if eng._undo else None,
              "was_blind": eng.blind_public()["on"], "blind_started": False, "steps": [],
              "changed": False, "rounds": 0, "views": 0, "t0": time.monotonic(), "waiting": []}
    return _go(eng, st, chat or llm.chat)


def resume_turn(eng, turn: str, image: str | None, chat=None) -> dict:
    """Carry on a turn that asked to see the 3D view, with the picture."""
    with _pending_lock:
        st = _pending.pop(str(turn or ""), None)
    if st is None:
        return {"ok": False, "error": "that request is no longer waiting - ask again"}
    ok = bool(image) and str(image).startswith("data:image/") and len(image) < 3_000_000
    st["msgs"].append({"role": "tool", "tool_call_id": st.pop("see_id"), "name": "see_3d",
                       "content": json.dumps({"ok": ok, "note": "the picture follows" if ok
                                              else "no picture could be taken - use check_lights"})})
    if ok:
        st["msgs"].append({"role": "user", "content": [
            {"type": "text", "text": "(The 3D view now, after your changes.)"},
            {"type": "image_url", "image_url": {"url": image}}]})
    return _go(eng, st, chat or llm.chat)


def _finish_pending(eng, session: str) -> None:
    """A turn still waiting for its picture (the screen went away, or a new
    request came first) ends as it is: kept as one undo step, previewed."""
    now = time.monotonic()
    with _pending_lock:
        done = [k for k, v in _pending.items() if v["session"] == session or now - v["paused"] > PENDING_S]
        olds = [_pending.pop(k) for k in done]
    for st in olds:
        _collapse(eng, st["top"], st["before"], f"AI: {st['message'][:40]}" if st["changed"] else None)


def _go(eng, st: dict, chat) -> dict:
    """The tool loop, from where the turn is."""
    msgs, steps = st["msgs"], st["steps"]
    tools = TOOLS + ([SEE_TOOL] if st["can_see"] and st["views"] < MAX_VIEWS else [])
    question = None
    options: list[str] = []
    reply = ""
    try:
        while st["rounds"] < MAX_ROUNDS:
            st["rounds"] += 1
            msg = chat(msgs, tools=tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = str(msg.get("content") or "").strip()
                break
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            stop = False
            see_id = None
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) \
                        else (fn.get("arguments") or {})
                except ValueError:
                    args = {}
                if name == "see_3d":
                    if st["can_see"] and st["views"] < MAX_VIEWS and see_id is None:
                        see_id = call.get("id") or name           # answered when the picture comes
                        continue
                    result = {"error": "no more pictures this time - use check_lights"}
                elif name == "look_at_rig":
                    result = {"rig": console_ai.rig_context(eng)}
                elif name == "check_lights":
                    result = check_lights(eng, args.get("heads"))
                elif name == "music":
                    result = music(eng)
                elif name == "do":
                    if len(steps) >= MAX_ACTIONS:
                        result = {"ok": False, "error": "that's enough steps for one request - finish up"}
                    else:
                        held = args.get("action") in CONFIRMABLE        # prepared only: nothing changes
                        if st["preview"] and not st["was_blind"] and not st["blind_started"] and not held:
                            eng.act("blind", state=True)           # 3D only until the operator keeps it
                            st["blind_started"] = True
                        # some models put the arguments next to "action" instead of in "params"
                        params = args.get("params") if isinstance(args.get("params"), dict) and args.get("params") \
                            else {k: v for k, v in args.items() if k not in ("action", "params")}
                        result = _do(eng, args.get("action"), params, st["waiting"])
                        steps.append({"action": args.get("action"), "params": params,
                                      "ok": result["ok"], "summary": result.get("summary")})
                        st["changed"] = st["changed"] or (result["ok"] and not held)
                elif name == "what_lights_can_do":
                    result = aitools.capabilities(eng, args.get("heads"), args.get("group"))
                elif name in ("place_lights", "add_to_room"):
                    if len(steps) >= MAX_ACTIONS:
                        result = {"ok": False, "error": "that's enough steps for one request - finish up"}
                    else:
                        try:
                            if name == "place_lights":
                                result = aitools.place_lights(eng, args.get("heads"), args.get("group"), args.get("on") or "",
                                                              args.get("where") or "middle", args.get("height_m"))
                            else:
                                result = aitools.add_to_room(eng, args.get("kind") or "", args.get("name") or "",
                                                             args.get("where") or "middle", args.get("length_m"),
                                                             args.get("width_m"), args.get("depth_m"), args.get("height_m"),
                                                             args.get("across") is not False)
                        except (ValueError, TypeError) as exc:
                            result = {"ok": False, "error": str(exc)}
                        steps.append({"action": name, "params": args, "ok": result.get("ok"), "summary": result.get("summary") or result.get("error")})
                        st["changed"] = st["changed"] or bool(result.get("ok"))
                elif name == "find_fixtures":
                    result = find_fixtures(eng, args.get("query"))
                elif name == "add_fixture":
                    if len(steps) >= MAX_ACTIONS:
                        result = {"ok": False, "error": "that's enough steps for one request - finish up"}
                    else:
                        result = add_fixture(eng, args.get("src"), args.get("key"), args.get("qty") or 1, args.get("mode"))
                        steps.append({"action": "add_heads", "params": {"src": args.get("src"), "key": args.get("key"),
                                      "qty": args.get("qty") or 1}, "ok": result["ok"], "summary": result.get("summary")})
                        st["changed"] = st["changed"] or result["ok"]
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
            if see_id is not None and not stop:
                # pause: the screen draws the view and sends it (resume_turn)
                st["views"] += 1
                st["see_id"] = see_id
                st["paused"] = time.monotonic()
                turn = os.urandom(8).hex()
                with _pending_lock:
                    _pending[turn] = st
                return {"ok": True, "need_view": True, "turn": turn, "steps": list(steps),
                        "preview": st["blind_started"], "seconds": round(time.monotonic() - st["t0"], 1)}
            if see_id is not None:
                msgs.append({"role": "tool", "tool_call_id": see_id, "name": "see_3d",
                             "content": json.dumps({"skipped": "asked the operator first"})})
            if stop:
                reply = str(msg.get("content") or "").strip()
                break
        else:
            reply = reply or "I've done what I could in one go - tell me what to change."
    except llm.LLMError as exc:
        if st["blind_started"]:
            with eng.lock:
                eng._restore_state(st["before"])
            eng.act("blind", state=False)
            _collapse(eng, st["top"], st["before"], None)
        return {"ok": False, "error": str(exc), "steps": steps}
    # everything it did is one undo step
    _collapse(eng, st["top"], st["before"], f"AI: {st['message'][:40]}" if st["changed"] else None)
    if st["blind_started"] and not st["changed"]:
        eng.act("blind", state=False)
        st["blind_started"] = False
    hist = _history(st["session"])
    hist.append({"role": "user", "content": st["message"]})
    hist.append({"role": "assistant", "content": (reply or question or "")[:1500]})
    del hist[:-MAX_HISTORY * 2]
    return {"ok": True, "reply": reply, "question": question, "options": options, "steps": steps,
            "confirm": st.get("waiting") or [],
            "changed": st["changed"], "preview": st["blind_started"], "views": st["views"],
            "seconds": round(time.monotonic() - st["t0"], 1)}


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

