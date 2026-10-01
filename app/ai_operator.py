"""The AI operator: the AI runs the lights live, with the music.

Every phrase on the beat clock (8 / 16 / 32 bars) - and straight away when
the room's sound hears a drop - it looks at the music, at what the lights
are doing and at what it did lately, and makes ONE change that suits the
moment, through the same console actions as the assistant (app/assistant.py),
live.  Each change is one undo step.

The operator takes over the moment they touch the desk: any action from a
screen that changes the show (a cue, a button, a fader, the programmer...)
stops it, and so does the "I've got it" button.  A change the AI was still
thinking about when that happened is dropped, not run.

It cannot arm or fire effects, save, load or delete (assistant.ALLOWED).
"""
from __future__ import annotations

import json
import math
import threading
import time

from . import assistant, console_ai, llm
from .engine_base import _READ_ONLY

BARS = (8, 16, 32)
TICK = 0.25
MIN_GAP_S = 12.0           # never two changes closer than this (and the AI's rate limit)
DROP_GAP_S = 6.0
MAX_ROUNDS = 3
MAX_STEPS = 10
LOG_KEEP = 30

# actions from a screen that are not taking over: looking, choosing which
# lights, and setting up the room
_PASSIVE_PREFIX = ("select_", "venue_", "rig_", "show_versions", "paperwork")
PASSIVE = frozenset(_READ_ONLY | {"clear_selection", "highlight", "locate", "ready_check", "status",
                                  "pad_info", "venue_info", "rig_pieces", "rig_report", "venue_preview",
                                  "colour_cal_get", "motion_get", "fx_status", "sound_tempo"})

SYSTEM = """You are running the lights LIVE for the night, on a lighting desk, with the music.
Every phrase (and on a drop) you get the music now, what the lights are doing and what
you did lately.  Make ONE change that suits the moment, then stop:
* Keep the show moving: a new look, colour, movement or effect - not the one you just had.
* Bigger and brighter on a drop or when the room is loud; calmer and slower in a breakdown
  or when it is quiet; build up before an expected drop.
* Use 2-8 `do` calls in ONE round (select, then set), on real heads / groups / zones.
* Never black the whole room out, never strobe for long; follow the operator's brief.
* Your reply: a few words on what you did and why ("drop: white strobe on the PARs").

The operator's brief: {brief}

ACTIONS (for `do`; most act on the selection):
{actions}

Colour names: {colours}
Named effects (run_fx name=...): {effects}
"""


def _st(eng) -> dict:
    st = eng.__dict__.get("ai_operator")
    if st is None:
        st = eng.ai_operator = {"on": False, "brief": "", "bars": 16, "drops": True, "log": [],
                                "next_bar": None, "last_at": 0.0, "drop_seen": 0.0, "busy": False,
                                "gen": 0, "stopped": None, "error": None, "decisions": 0}
    return st


def status(eng) -> dict:
    st = _st(eng)
    now = time.monotonic()
    out = {k: st[k] for k in ("on", "brief", "bars", "drops", "busy", "stopped", "error", "decisions")}
    out["log"] = [{**e, "ago_s": round(now - e["at"])} for e in st["log"][-12:]][::-1]
    for e in out["log"]:
        e.pop("at", None)
    if st["on"] and st["next_bar"] is not None:
        bar = eng._tempo().beats(now) / 4.0
        out["bars_left"] = max(0, int(math.ceil(st["next_bar"] - bar)))
    return out


def start(eng, brief: str = "", bars: int = 16, drops: bool = True, thread: bool = True, chat=None) -> dict:
    st = _st(eng)
    if int(bars) not in BARS:
        raise ValueError("a change every 8, 16 or 32 bars")
    if not eng.patch:
        raise ValueError("patch some lights first")
    st.update({"brief": str(brief or "").strip()[:400], "bars": int(bars), "drops": bool(drops),
               "on": True, "next_bar": None, "stopped": None, "error": None, "gen": st["gen"] + 1,
               "drop_seen": eng.__dict__.get("_sound_drop_at", 0.0)})
    st["last_at"] = 0.0
    _log(st, "on", "I'm running the lights" + (f": {st['brief']}" if st["brief"] else ""), [])
    if thread:
        _spawn(eng, chat)
    return status(eng)


def stop(eng, reason: str = "stopped") -> dict:
    st = _st(eng)
    if st["on"]:
        st["on"] = False
        st["gen"] += 1                  # a change being thought about is dropped
        st["stopped"] = reason
        _log(st, "stop", reason, [])
    ev = eng.__dict__.get("_op_stop_ev")
    if ev:
        ev.set()
    return status(eng)


def take_over(eng, action: str) -> bool:
    """A screen did something: if it changes the show, the operator has
    it back.  True when that stopped the AI."""
    st = eng.__dict__.get("ai_operator")
    if not st or not st["on"]:
        return False
    a = str(action or "")
    if a in PASSIVE or a.startswith(_PASSIVE_PREFIX):
        return False
    stop(eng, f"you took over ({a.replace('_', ' ')})")
    return True


def _log(st: dict, why: str, text: str, steps: list[dict], ok: bool = True) -> None:
    st["log"].append({"at": time.monotonic(), "why": why, "text": text[:300], "ok": ok,
                      "steps": [s.get("summary") or s.get("action") for s in steps][:MAX_STEPS]})
    del st["log"][:-LOG_KEEP]


# -- when ---------------------------------------------------------------------
def tick(eng, now: float | None = None, chat=None) -> dict | None:
    """Once per tick: a phrase passed, or a drop?  Then one change (the
    tests' entry too).  Returns the log entry of a change made."""
    st = _st(eng)
    if not st["on"] or st["busy"]:
        return None
    now = time.monotonic() if now is None else now
    bar = eng._tempo().beats(now) / 4.0
    bars = st["bars"]
    why = None
    drop_at = eng.__dict__.get("_sound_drop_at", 0.0)
    if st["drops"] and drop_at > st["drop_seen"]:
        st["drop_seen"] = drop_at
        if now - st["last_at"] >= DROP_GAP_S:
            why = "drop"
    if st["next_bar"] is None:
        st["next_bar"] = (math.floor(bar / bars) + 1) * bars
        if why is None and st["last_at"] == 0.0:
            why = "start"                       # the first look straight away
    elif why is None and bar >= st["next_bar"]:
        while st["next_bar"] <= bar:
            st["next_bar"] += bars
        if now - st["last_at"] >= MIN_GAP_S:
            why = "phrase"
    if why is None:
        return None
    if why == "drop":
        st["next_bar"] = (math.floor(bar / bars) + 1) * bars
    return decide(eng, why, chat=chat, now=now)


# -- what ---------------------------------------------------------------------
def _brief_text(st: dict) -> str:
    return st["brief"] or "a good club night: follow the music, keep it varied"


def _system(eng, st: dict) -> str:
    ref = "\n".join(f"    {a}({', '.join(assistant.ALLOWED[a])})" for a in sorted(assistant.ALLOWED))
    text = SYSTEM.format(brief=_brief_text(st), actions=ref,
                         colours=", ".join(n for n, _h in assistant.showdesign.COLOR_NAMES[:24]),
                         effects=", ".join(sorted(console_ai.fxlib.FX)))
    mem = assistant.notes()
    if mem:
        text += "\nWhat this operator likes:\n" + "\n".join(f"  - {n}" for n in mem)
    return text + "\n\nTHE RIG:\n" + console_ai.rig_context(eng)


def decide(eng, why: str, chat=None, now: float | None = None) -> dict | None:
    st = _st(eng)
    chat = chat or llm.chat
    gen = st["gen"]
    st["busy"] = True
    st["last_at"] = time.monotonic() if now is None else now
    try:
        lights = assistant.check_lights(eng)
        moment = {"why": why, "music": assistant.music(eng),
                  "lights_now": lights["lights"][:40],
                  "you_did_lately": [{"why": e["why"], "did": e["text"]} for e in st["log"][-6:] if e["why"] not in ("on", "stop")]}
        msgs = [{"role": "system", "content": _system(eng, st)},
                {"role": "user", "content": json.dumps(moment, default=str)[:12000]}]
        tools = [t for t in assistant.TOOLS if t["function"]["name"] in ("do", "check_lights")]
        steps: list[dict] = []
        reply = ""
        with eng.lock:
            top = eng._undo[-1] if eng._undo else None
            before = eng._undo_state()
        for _round in range(MAX_ROUNDS):
            msg = chat(msgs, tools=tools)
            if st["gen"] != gen or not st["on"]:
                return None                     # the operator took over while it thought
            calls = msg.get("tool_calls") or []
            if not calls:
                reply = str(msg.get("content") or "").strip()
                break
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) \
                        else (fn.get("arguments") or {})
                except ValueError:
                    args = {}
                if fn.get("name") == "do" and len(steps) < MAX_STEPS and st["gen"] == gen:
                    params = args.get("params") if isinstance(args.get("params"), dict) and args.get("params") \
                        else {k: v for k, v in args.items() if k not in ("action", "params")}
                    result = assistant._do(eng, args.get("action"), params)
                    steps.append({"action": args.get("action"), "ok": result["ok"], "summary": result.get("summary")})
                elif fn.get("name") == "check_lights":
                    result = assistant.check_lights(eng, args.get("heads"))
                else:
                    result = {"error": "not now"}
                msgs.append({"role": "tool", "tool_call_id": call.get("id") or fn.get("name"), "name": fn.get("name"),
                             "content": json.dumps(result, default=str)[:8000]})
            if steps and _round >= 1:
                reply = str(msg.get("content") or "").strip()
                break
        done = [s for s in steps if s["ok"]]
        assistant._collapse(eng, top, before, f"AI operator: {why}" if done else None)
        st["decisions"] += 1
        st["error"] = None
        said = reply or ("; ".join(str(x.get("summary") or x["action"]) for x in done
                                  if not str(x["action"]).startswith("select"))[:200] if done else "kept it as it is")
        _log(st, why, said or f"{len(done)} change(s)", steps, ok=bool(done) or not steps)
        return st["log"][-1]
    except llm.LLMError as exc:
        st["error"] = str(exc)
        _log(st, why, f"the AI didn't answer: {exc}", [], ok=False)
        return None
    finally:
        st["busy"] = False


def _spawn(eng, chat=None) -> None:
    th = eng.__dict__.get("_op_thread")
    old = eng.__dict__.get("_op_stop_ev")
    if th is not None and th.is_alive() and old is not None and not old.is_set():
        return                                  # already running
    stop_ev = eng._op_stop_ev = threading.Event()

    def loop():
        while not stop_ev.wait(TICK):
            if not _st(eng)["on"]:
                break
            try:
                tick(eng, chat=chat)
            except Exception as exc:            # noqa: BLE001 - never die silently
                _st(eng)["error"] = f"{exc}"
    th = eng._op_thread = threading.Thread(target=loop, name="jarvis-ai-operator", daemon=True)
    th.start()
