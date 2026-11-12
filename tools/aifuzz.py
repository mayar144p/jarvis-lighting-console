"""The copilot under a storm of requests: thousands of operator prompts, each
answered by a stand-in AI that calls the desk's tools the way a real one
might - sensibly, sloppily (strings for numbers, settings in the wrong
place, names that don't exist, things out of range), or against the rules.
No AI key needed: it tests what the DESK does with whatever an AI asks.

    python tools/aifuzz.py                 2000 requests, seed 1
    python tools/aifuzz.py --turns 5000 --seed 7

After every request it checks:
  * nothing crashed (the turn, the desk, the screens' snapshot, the output);
  * nothing the desk calls a bug ("internal error") happened;
  * what needs the operator's tap was only prepared - no cue, palette,
    group, light or profile of theirs disappeared;
  * no roam over the DJ, the bar or the stage; nothing on a DJ request
    beyond the look;
  * one Ctrl+Z puts everything the request did back;
  * now and then: the show saves and opens again the same.
Failures are written to data/aifuzz/ with the calls that made them.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="haze-aifuzz-"))
os.environ["CONSOLE_DATA_DIR"] = str(SCRATCH)
os.environ.setdefault("CONSOLE_AUTOSAVE", "false")
sys.path.insert(0, str(ROOT))

from app import assistant, autoshow, djrequests, llm, roomsense, websearch  # noqa: E402
from app import engine as engine_mod  # noqa: E402

PROMPTS = [
    "warm wash on the VIP", "make the dance floor blue and slow", "the drop needs to hit harder", "calmer for the speeches",
    "design me a halloween show", "christmas with the same programming as halloween", "only buttons for tonight, no cues",
    "strobe everything", "strobe the bar", "roam the movers over the DJ", "blackout", "everything at full",
    "add 4 moving heads on the truss", "hang the pars on the left pole", "draw a VIP area front left",
    "delete cue 1 on playback 2", "go live", "arm the confetti", "save the show", "remember I like amber on the VIP",
    "from now on never strobe the VIP", "forget my notes", "never mind, make it red", "what's on?", "check my show",
    "make this button faster", "rename my group to Floor", "a colour chase in purple and green", "point the movers at the stage",
    "make a new profile of buttons for the christmas show", "replace the cues on playback 2 with a sunset", "ice blue and white",
    "listen to the song and find the drops", "shows for my whole set", "turn the speed down", "fan the pans out",
    "record this as a cue", "record a palette called Sunset", "put a flash button on page 3", "tap tempo 128",
    "lasers on", "fog please", "undo that", "make it look like a sunset at the beach", "something cheap looking",
    "", "asdfgh", "🔥🔥🔥", "a" * 400, "make the VIP strobe at 25 hz", "do the same as last time",
]
NAMES = ["Halloween", "Christmas", "My group", "Dance floor", "VIP", "Bar", "DJ", "Stage", "nonexistent", "", "x" * 80, "Sunset", "Floor"]
COLOURS = ["red", "amber", "#ff00ff", "#bfe6ff", "purple", "ice blue", "#zzzzzz", "", "rgb(1,2,3)", None, 42]


def val(rnd: random.Random, key: str, eng):
    """A value an AI might send for a setting called `key` - often fine,
    sometimes sloppy."""
    heads = [h["head_no"] for h in eng.patch] or [1]
    sloppy = rnd.random() < 0.18
    if sloppy:
        return rnd.choice([None, "", "lots", -5, 10 ** 9, 3.7, [], {}, "7", True, "all", [1, "2", None], {"x": 1}])
    k = key.lower()
    if k in ("heads", "ids", "lights"):
        return rnd.sample(heads, min(len(heads), rnd.randint(1, 4)))
    if k in ("head", "head_end", "n", "cue", "slot", "to_slot", "count", "qty"):
        return rnd.randint(1, 12)
    if k in ("playback", "page", "to_page", "palette", "group", "universe"):
        return rnd.choice([1, 2, 3, rnd.randint(1, 40), "My group", "Halloween · purple"])
    if k in ("level", "value", "fader", "speed", "size", "rate", "spread", "opacity", "phase", "density"):
        return rnd.choice([0, 25, 50, 100, 255, rnd.random() * 100])
    if k in ("hz",):
        return rnd.choice([2, 5, 12, 25])
    if k in ("hex", "colour"):
        return rnd.choice(COLOURS)
    if k == "colours":
        return rnd.sample(["#ff0000", "#00ff00", "#0000ff", "#ffffff", "purple", "amber"], 2)
    if k in ("name", "label", "new", "to", "rename", "text", "query", "spec", "show", "venue", "lib", "macro", "preset", "piece", "item"):
        return rnd.choice(NAMES)
    if k in ("zone", "zones"):
        return rnd.choice(["Dance floor", "VIP", "Bar", "DJ", "Stage", "nowhere", ["Dance floor"], ["DJ", "Bar"]])
    if k in ("x", "y", "z", "pan", "tilt", "width", "depth", "height", "radius", "t", "at", "seconds", "fade", "delay", "duration", "length"):
        return round(rnd.uniform(-5, 20), 2)
    if k in ("state", "on", "all", "clear", "delete", "copy", "confirm", "safe", "loop", "free", "immediate", "down", "go", "solo"):
        return rnd.choice([True, False])
    if k in ("bpm",):
        return rnd.choice([60, 128, 174, 0, 999])
    if k == "button":
        return {"kind": rnd.choice(["flash", "strobe", "colour", "fx", "go", "nonsense"]), "label": rnd.choice(NAMES),
                "target": {"heads": rnd.sample(heads, 1)}, "colour": "#ff0000", "fx": rnd.choice(["roam", "circle", "zzz"]),
                "params": {"zones": rnd.choice([["DJ"], ["Dance floor"], ["nonexistent"]])}}
    if k == "params":
        return {"speed": rnd.randint(1, 8), "zones": rnd.choice([["Dance floor"], ["DJ"]])}
    return rnd.choice([1, "x", True, None, [1, 2]])


def zone_ids(eng, names):
    z = {zz["name"].lower(): zz["id"] for zz in roomsense.plan(eng)["zones"]}
    return [z.get(str(n).lower(), n) for n in names] if isinstance(names, list) else names


def call(rnd: random.Random, eng) -> dict:
    """One tool call an AI might make."""
    tools = [t["function"]["name"] for t in assistant.TOOLS]
    name = rnd.choice(tools + ["do"] * 12 + ["made_up_tool"])
    args: dict = {}
    if name == "do":
        acts = list(assistant.ALLOWED) + list(assistant.CONFIRMABLE) + ["patch_clear", "set_lock", "undo", "not_an_action"]
        action = rnd.choice(acts)
        keys = assistant.ALLOWED.get(action) or assistant.CONFIRMABLE.get(action) or ("x",)
        params = {k: val(rnd, k, eng) for k in keys if rnd.random() < 0.7}
        if action == "roam" and rnd.random() < 0.6:
            params["zones"] = zone_ids(eng, rnd.choice([["DJ"], ["Bar"], ["Dance floor"], ["Stage", "VIP"]]))
        if rnd.random() < 0.15:
            args = {"action": action, **params}            # (settings beside "action")
        else:
            args = {"action": action, "params": params}
    elif name in ("design_show", "design_night"):
        args = {"brief": rnd.choice(["halloween", "christmas", "techno", "", "a wedding"]), "name": rnd.choice(NAMES),
                "make": rnd.choice(["all", "buttons", "cues", "nonsense"]), "playback": rnd.choice([None, 1, 2, 5])}
    elif name == "restyle_show":
        args = {"from_show": rnd.choice(["Halloween", "Christmas", "nope"]), "name": rnd.choice(NAMES),
                "colours": rnd.choice([["#ff0000", "#ffffff"], ["red"], []])}
    elif name == "remember":
        args = rnd.choice([{"note": rnd.choice(["likes amber", "never strobe the VIP", "", "x" * 300])}, {"forget": rnd.randint(-1, 5)}])
    elif name in ("place_lights", "add_to_room", "find_fixtures", "add_fixture", "check_lights", "propose_plan", "ask",
                  "search_web", "read_web"):
        args = {k: val(rnd, k, eng) for k in ("heads", "on", "height", "kind", "name", "where", "query", "src", "key", "qty",
                                              "summary", "steps", "question", "options", "url") if rnd.random() < 0.5}
    return {"id": f"c{rnd.randint(0, 10 ** 9)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, default=str) if rnd.random() > 0.03 else "{not json"}}


def stand_in(rnd: random.Random, eng):
    """A chat() that answers with 1-3 rounds of random tool calls, then words."""
    rounds = rnd.randint(1, 3)
    n = {"r": 0}

    def chat(messages, tools=None, **_):
        n["r"] += 1
        if n["r"] > rounds:
            return {"role": "assistant", "content": rnd.choice(["Done.", "", "I did what I could."])}
        return {"role": "assistant", "content": "", "tool_calls": [call(rnd, eng) for _ in range(rnd.randint(1, 4))]}
    return chat


# -- the desk the requests start on -------------------------------------------
def make_desk():
    eng = engine_mod.Engine(dry_run=True, show_dir=SCRATCH / f"shows-{time.monotonic_ns()}")
    eng.act("venue_template", name="lounge")
    eng.act("add_heads", query="moving head", qty=3)
    eng.act("add_heads", query="LED PAR", qty=6)
    # the operator's own work
    eng.act("select_heads", heads=[4, 5])
    eng.act("set_intensity", level=80)
    eng.act("set_colour", hex="#ff0000")
    eng.act("record_cue", playback=2, name="My look")
    eng.act("record_palette", kind="colour", name="My red")
    eng.act("group_create", name="My group")
    eng.act("clear_programmer")
    eng.act("quick_set", page=1, slot=1, button={"kind": "flash", "label": "My flash", "target": {"heads": [4]}})
    return eng


def counts(eng) -> dict:
    p = eng._profiles()
    return {"pb2 cues": len(eng._playback(2).get("stack") or []), "palettes": sum(len(v) for v in eng.palettes.values()),
            "groups": len(eng.groups), "lights": len(eng.patch), "profiles": len(p["order"]),
            "My flash": sum(1 for b in (eng.quick if p["active"] == "Default" else (p["sets"].get("Default") or {}).get("quick") or [])
                            if b.get("label") == "My flash")}


def content(eng) -> dict:
    """What the operator would call the show: its lights and where they are
    patched, cues, buttons in every profile, palettes, groups, the room, the
    timeline - and what the lights put out with the programmer clear."""
    p = eng._profiles()
    sets = {n: (eng.quick if n == p["active"] else (p["sets"].get(n) or {}).get("quick") or []) for n in p["order"]}
    v = eng.venue or {}
    # what the lights put out - only when nothing plays (a running cue, a held
    # button, an effect, the timeline don't carry over into a show opened)
    playing = bool(eng.fx or eng.quick_active or any(pb.get("active") for pb in eng.playbacks)
                   or (eng._tl_transport() or {}).get("playing"))
    with eng.lock:
        prog = eng.programmer
        eng.programmer = {}
        try:
            frames = None if playing else {u: bytes(b) for u, b in eng.build_frames(time.monotonic()).items()}
        finally:
            eng.programmer = prog
    return {"lights": sorted((h["head_no"], h["universe"], h["address"], h.get("model")) for h in eng.patch),
            "cues": [(pb["n"], [(c.get("name"), len(c.get("values") or {})) for c in pb.get("stack") or []]) for pb in eng.playbacks],
            "buttons": {n: sorted((b["id"], b.get("label"), b.get("kind")) for b in q) for n, q in sets.items()},
            "palettes": {k: sorted(str(x.get("name")) for x in lst) for k, lst in eng.palettes.items()},
            "groups": sorted((g.get("name"), tuple(g.get("heads") or [])) for g in eng.groups),
            "room": (len(v.get("zones") or []), len(v.get("rigging") or []), len(v.get("objects") or v.get("items") or [])),
            "timeline": (len((eng.timeline or {}).get("tracks") or []), len((eng.timeline or {}).get("markers") or [])),
            "output": frames}


def say(text: str) -> None:
    """Printed safely on any console (a Windows one can't show every character)."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(enc, "replace").decode(enc, "replace"), flush=True)


def _canon(v):
    """The same thing said the same way: a running effect's moment-to-moment
    values (keys starting "_") left out, an empty value and a missing one
    alike, -0.0 as 0."""
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items() if not str(k).startswith("_") and x is not None}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, float):
        return 0.0 if v == 0 else round(v, 6)
    return v


def driven(eng) -> set:
    """The playbacks the autopilot, auto-follow or a playing timeline drives:
    they move cue to cue by themselves while a request runs - where they are
    is the show playing, not an edit."""
    ap = eng._ap()
    out = {ap.get("playback")} if ap.get("on") else set()
    if (eng._tl_transport() or {}).get("playing"):
        out |= {tr.get("playback") for tr in (eng.timeline or {}).get("tracks") or [] if tr.get("playback")}
    return out | {pb["n"] for pb in eng.playbacks if (pb.get("follow") or {}).get("on")}


def unplayed(st: dict, nums: set) -> dict:
    """The state with where those playbacks are (cue, fade, level of play) left out."""
    for pb in st.get("playbacks") or []:
        if isinstance(pb, dict) and pb.get("n") in nums:
            for k in ("index", "active", "fade", "target", "target_mib", "order", "at", "xfade"):
                pb.pop(k, None)
            if isinstance(pb.get("follow"), dict):
                pb["follow"].pop("at", None)
    return st


def state_json(eng) -> str:
    with eng.lock:
        st = eng._undo_state()
    st = json.loads(json.dumps(st, default=str))
    # (an effect timed to end by itself may end mid-check: not an edit)
    # and one put back by Ctrl+Z restarts (a new start time and number): the
    # same effect on the same lights
    st["prog_fx"] = sorted(({k: v for k, v in f.items() if k not in ("t0", "id")}
                            for f in st.get("prog_fx") or [] if not f.get("duration")), key=lambda f: json.dumps(f, sort_keys=True))
    for h in st.get("patch") or []:
        if isinstance(h, dict):
            h.pop("mapped", None)                     # (a runtime flag, not the rig)
            if h.get("yaw") in (None, 0, 0.0):
                h.pop("yaw", None)
            m = h.get("mount")
            if isinstance(m, dict) and m.get("yaw0") in (0, 0.0):
                m.pop("yaw0", None)
    for h in (st.get("patch_extra") or {}).values():
        m = h.get("mount") if isinstance(h, dict) else None
        if isinstance(m, dict) and m.get("yaw0") in (0, 0.0):
            m.pop("yaw0", None)
    return json.dumps(_canon(st), sort_keys=True, default=str)


def run(turns: int = 2000, seed: int = 1, out: Path | None = None, quiet: bool = False) -> dict:
    rnd = random.Random(seed)
    llm.available = lambda: True                       # (the stand-in answers)
    websearch.allowed = lambda: False                  # (no internet here)
    real_design = autoshow.design
    autoshow.design = lambda eng, brief="", offline=False, variants=1: real_design(eng, brief, offline=True)
    internal: list = []
    eng = make_desk()
    real_act = eng.act

    def watched(action, **params):
        r = real_act(action, **params)
        if r.get("internal"):
            internal.append({"action": action, "params": params, "error": r.get("error")})
        return r
    eng.act = watched
    fails: list[dict] = []
    t0 = time.monotonic()
    for i in range(turns):
        if i and i % 150 == 0:                         # a fresh desk now and then
            eng.shutdown()
            eng = make_desk()
            real_act = eng.act
            eng.act = watched
        prompt = rnd.choice(PROMPTS)
        before = counts(eng)
        undo_len = len(eng._undo)
        st_before = state_json(eng)
        drv = driven(eng)
        internal.clear()
        dj = rnd.random() < 0.08
        chat = stand_in(rnd, eng)
        problems = []
        try:
            if dj:
                item = djrequests.add(eng, prompt or "more energy", who="DJ", client=f"c{i}")
                r = assistant.do_request(eng, item["id"], chat=chat)
                djrequests.answer(eng, item["id"], bool(r.get("ok")), "")
            else:
                r = assistant.run_turn(eng, prompt, session=f"s{i % 5}", preview=rnd.random() < 0.5, chat=chat)
            json.dumps(r, default=str)
            if r.get("need_view"):
                r = assistant.resume_turn(eng, r["turn"], None, chat=chat)
        except Exception:  # noqa: BLE001 - that's what we're looking for
            problems.append("the turn crashed: " + traceback.format_exc()[-800:])
            r = {}
        if eng.blind_public()["on"]:
            eng.act("blind", state=False, keep=True)       # (the operator keeps the preview)
        if internal:
            problems.append(f"desk bug(s): {internal[:3]}")
        after = counts(eng)
        for k in ("pb2 cues", "palettes", "groups", "lights", "My flash"):
            if after[k] < before[k] and not any((s.get("action") or "") in ("undo",) for s in r.get("steps") or []):
                problems.append(f"{k} went from {before[k]} to {after[k]} without the operator's tap")
        if after["profiles"] < before["profiles"]:
            problems.append("a button profile disappeared")
        p = roomsense.plan(eng)
        shy = set(p["no_roam"])
        for f in eng.fx:
            if f.get("roam") and any(z.get("id") in shy for z in f["roam"]):
                problems.append(f"a roam over {[z.get('id') for z in f['roam']]}")
        try:
            snap = eng.snapshot() if hasattr(eng, "snapshot") else None
            json.dumps(snap, default=str)
            frames = eng.build_frames()
            if any(len(b) != 512 for b in frames.values()):
                problems.append("a DMX frame isn't 512 bytes")
            assistant._system(eng, prompt)
        except Exception:  # noqa: BLE001
            problems.append("the desk broke after it: " + traceback.format_exc()[-800:])
        # one Ctrl+Z puts it all back - and a request that changed something
        # leaves that Ctrl+Z
        now_st = json.loads(state_json(eng))
        drv |= driven(eng)
        changed_now = [k for k, v in json.loads(st_before).items() if k != "selected"
                       and json.dumps(v, sort_keys=True) != json.dumps(now_st.get(k), sort_keys=True)]
        if "prog_fx" in changed_now:
            # (an effect with a set length that simply finished is no edit)
            was = {f.get("id") for f in json.loads(st_before).get("prog_fx") or []}
            if {f.get("id") for f in now_st.get("prog_fx") or []} <= was:
                changed_now.remove("prog_fx")
        if "playbacks" in changed_now:
            # (playing the show - GO, a fader, a release - is no edit)
            strip = lambda pbs: [{k: v for k, v in pb.items() if k in ("n", "name", "stack", "mode", "keys", "midi")}  # noqa: E731
                                 for pb in pbs or []]
            if json.dumps(strip(json.loads(st_before).get("playbacks")), sort_keys=True) == json.dumps(strip(now_st.get("playbacks")), sort_keys=True):
                changed_now.remove("playbacks")
        if changed_now and len(eng._undo) == undo_len and not problems:
            detail = {}
            b0 = json.loads(st_before)
            for k in changed_now[:2]:
                x, y = b0.get(k), now_st.get(k)
                if isinstance(x, list) and isinstance(y, list):
                    d = [(i, {kk: (p.get(kk), q.get(kk)) for kk in set(p) | set(q) if p.get(kk) != q.get(kk)})
                         for i, (p, q) in enumerate(zip(x, y)) if isinstance(p, dict) and isinstance(q, dict) and p != q]
                    detail[k] = str(d[:2])[:300]
                elif isinstance(x, dict) and isinstance(y, dict):
                    detail[k] = str({kk: (x.get(kk), y.get(kk)) for kk in set(x) | set(y) if x.get(kk) != y.get(kk)})[:300]
            acts = [s.get("action") for s in r.get("steps") or [] if s.get("ok")]
            problems.append(f"it changed {changed_now[:4]} but left no Ctrl+Z (by {acts[:8]}): {detail}")
        if len(eng._undo) > undo_len and not problems:
            added = len(eng._undo) - undo_len
            if added > 1:
                problems.append(f"the request left {added} undo steps (should be one)")
            try:
                for _ in range(added):
                    real_act("undo")
                if eng.blind_public()["on"]:
                    eng.act("blind", state=False)
                drv |= driven(eng)
                a, b = unplayed(json.loads(st_before), drv), unplayed(json.loads(state_json(eng)), drv)
                diff = [k for k in a if a[k] != b.get(k) and k != "selected"]      # (choosing lights isn't an edit)
                if diff:
                    problems.append(f"Ctrl+Z didn't put it all back: {diff[:6]} "
                                    + str({k: (a.get(k), b.get(k)) for k in diff[:1]})[:500] + f" undo labels {[u.get('label') or u.get('action') for u in eng._undo[-2:]]}")
                real_act("redo") if added == 1 else None
            except Exception:  # noqa: BLE001
                problems.append("undo crashed: " + traceback.format_exc()[-600:])
        # now and then: saved and opened again, the same
        if i % 97 == 0 and not problems:
            try:
                name = f"fuzz{i}"
                real_act("save_show", name=name)
                e2 = engine_mod.Engine(dry_run=True, show_dir=eng.show_dir)
                e2.act("load_show", name=name)
                a, b = content(eng), content(e2)
                diff = [k for k in a if a[k] != b.get(k) and not (k == "output" and a[k] is None)]
                e2.shutdown()
                if diff:
                    more = {}
                    if "output" in diff:
                        for u in set(a["output"]) | set(b["output"]):
                            x, y = a["output"].get(u, b""), b["output"].get(u, b"")
                            ch = [(k + 1, x[k], y[k]) for k in range(min(len(x), len(y))) if x[k] != y[k]]
                            if ch:
                                heads = [(hh["head_no"], hh.get("model"), hh["map"][c - hh["address"]]) for c, _x, _y in ch[:4]
                                         for hh in eng.patch if hh["universe"] == u and hh["address"] <= c < hh["address"] + len(hh["map"])]
                                more[f"u{u}"] = (ch[:4], heads[:4])
                    if "buttons" in diff:
                        for n in a["buttons"]:
                            if a["buttons"][n] != b["buttons"].get(n):
                                more[n] = ([x for x in a["buttons"][n] if x not in (b["buttons"].get(n) or [])][:3],
                                           [x for x in (b["buttons"].get(n) or []) if x not in a["buttons"][n]][:3])
                    problems.append(f"saved and opened again, not the same: {diff} {str(more)[:600]}")
            except Exception:  # noqa: BLE001
                problems.append("save / open crashed: " + traceback.format_exc()[-600:])
        if problems:
            fails.append({"turn": i, "prompt": prompt[:120], "dj": dj, "problems": problems,
                          "steps": [(s.get("action"), s.get("ok"), (s.get("summary") or "")[:100]) for s in r.get("steps") or []][:20]})
            if not quiet:
                say(f"  FAIL turn {i} ({prompt[:40]!r}): {problems[0][:300]}")
        if not quiet and i % 250 == 0:
            print(f"  {i}/{turns} turns, {len(fails)} problem(s), {time.monotonic() - t0:.0f} s", flush=True)
    eng.shutdown()
    autoshow.design = real_design
    report = {"turns": turns, "seed": seed, "problems": len(fails), "seconds": round(time.monotonic() - t0), "fails": fails}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\n{turns} requests, {len(fails)} with a problem ({report['seconds']} s)")
    return report


if __name__ == "__main__":
    a = sys.argv[1:]
    turns = int(a[a.index("--turns") + 1]) if "--turns" in a else 2000
    seed = int(a[a.index("--seed") + 1]) if "--seed" in a else 1
    try:
        run(turns, seed, out=Path(os.environ.get("AIFUZZ_OUT") or (ROOT / "data" / "aifuzz" / f"report-{seed}.json")))
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
