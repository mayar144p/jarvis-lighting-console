"""The copilot's practice exam: real requests, scored by what a good answer
must and must not do (tools/aiexam/cases.json).

    python tools/aiexam.py              every case, with the AI set up on this
                                        computer (Settings -> AI, or .env)
    python tools/aiexam.py --only roam  the cases whose id has "roam" in it
    python tools/aiexam.py --fake       the runner itself, with a stand-in AI
                                        (no key needed; the selftest uses it)

Each case runs on a fresh desk in a scratch folder (your shows are never
touched), the copilot gets the request, and the result is checked.  The
score and every answer go to data/aiexam/report-<date>.json, so a change to
the copilot's instructions can be compared with the last run.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = Path(os.environ.get("CONSOLE_DATA_DIR") or ROOT / "data")
SCRATCH = Path(tempfile.mkdtemp(prefix="haze-exam-"))
# the AI's own settings (the key, the mode) come along; nothing else does
if (REAL_DATA / "ai.json").is_file():
    shutil.copy(REAL_DATA / "ai.json", SCRATCH / "ai.json")
os.environ["CONSOLE_DATA_DIR"] = str(SCRATCH)
os.environ.setdefault("CONSOLE_AUTOSAVE", "false")
sys.path.insert(0, str(ROOT))

from app import assistant, autoshow, llm, roomsense, showkit, showreview  # noqa: E402
from app import engine as engine_mod  # noqa: E402

CASES = ROOT / "tools" / "aiexam" / "cases.json"


# -- the desks the cases start from ----------------------------------------------
def make_desk(rig: str, setup: list[str]):
    eng = engine_mod.Engine(dry_run=True, show_dir=SCRATCH / f"shows-{time.monotonic_ns()}")
    if rig != "empty":
        eng.act("venue_template", name="lounge")
        eng.act("add_heads", query="moving head", qty=3)
        eng.act("add_heads", query="LED PAR", qty=6)
    else:
        eng.ensure_venue()
        eng.act("venue_reset", keep_room=True)          # (a bare room: no zones, no furniture)
    for s in setup or []:
        if s in ("halloween_kit", "halloween_show"):
            a = autoshow.analyse(eng)
            d = autoshow.validate(autoshow.fallback_design(a, "halloween"), a)
            d["name"] = "Halloween"
            if s == "halloween_show":
                autoshow.build(eng, d, 1)
            showkit.build(eng, d, 1 if s == "halloween_show" else None)
        elif s == "one_dark":
            eng.act("select_heads", heads=list(range(2, 10)))
            eng.act("set_intensity", level=80)
            eng.act("clear_selection")
        elif s in ("one_cue", "bad_cue"):
            eng.act("select_heads", heads=[1, 2])
            eng.act("set_intensity", level=100)
            eng.act("aim_at", zone="Bar" if s == "bad_cue" else "Dance floor")
            eng.act("record_cue", playback=1, name="bar look" if s == "bad_cue" else "floor")
            eng.act("clear_programmer")
        elif s == "busy":
            eng.act("select_heads", heads=list(range(1, 10)))
            eng.act("set_intensity", level=100)
            eng.act("run_fx", name="rainbow", params={"speed": 4})
            eng.act("clear_selection")
        elif s == "songs":
            mk = [{"name": "Intro", "t": 0}, {"name": "Build", "t": 30}, {"name": "Drop", "t": 45}, {"name": "Outro", "t": 90}]
            eng.act("songs_import", tracks=[{"name": "Song A", "bpm": 124, "length": 120, "markers": mk},
                                            {"name": "Song B", "bpm": 126, "length": 120, "markers": mk}])
    return eng


# -- what a good answer does ---------------------------------------------------
def _undo_len(eng) -> int:
    return len(eng._undo)


def judge(eng, r: dict, exp: dict, before: dict) -> list[str]:
    """The checks a case asked for that failed (empty: passed)."""
    bad = []
    steps = r.get("steps") or []
    used = {s.get("action") for s in steps if s.get("ok")}
    reply = f"{r.get('reply') or ''} {r.get('question') or ''}".lower()
    asked = bool(r.get("question"))
    changed = bool(r.get("changed"))
    lights = assistant.check_lights(eng)["lights"]
    lit = [x for x in lights if x["level"] > 0]
    plan = roomsense.plan(eng)
    kinds = {z["id"]: z["kind"] for z in plan["zones"]}
    prof = eng._profiles()

    def need(ok, what):
        if not ok:
            bad.append(what)
    for key, want in exp.items():
        if key == "action_used":
            need(used & set(want), f"used one of {want} (used {sorted(x for x in used if x)})")
        elif key == "no_change":
            need(not changed and _undo_len(eng) == before["undo"], "changed nothing")
        elif key == "changed":
            need(changed, "changed something")
        elif key == "asked_or_refused":
            need(asked or any(w in reply for w in ("can't", "cannot", "won't", "instead", "not ", "conflict", "both",
                                                   "which", "contradict", "don't")), "asked or explained why not")
        elif key == "asked_or_changed":
            need(asked or changed, "asked or changed something")
        elif key == "looked_up_guide":
            # a how-to question: answered from the app's own guide (how_to), not memory
            need(bool(r.get("guide")) and r.get("guide") != "nothing found", "looked it up in the guide (how_to)")
        elif key == "reply_has_any":
            need(any(w.lower() in reply for w in want), f"said one of {want}")
        elif key == "reply_max_words":
            need(len(reply.split()) <= want, f"answered in {want} words or fewer ({len(reply.split())})")
        elif key == "prepared":
            got = {c.get("action") for c in r.get("confirm") or []}
            need(set(want) <= got, f"prepared {want} for the operator's tap (got {sorted(x for x in got if x)})")
        elif key == "prepared_any":
            need(bool(r.get("confirm")), "prepared it for the operator's tap")
        elif key == "not_done":
            need(not (set(want) & used) or bool(r.get("confirm")), f"did not do {want} itself")
        elif key == "no_roam_over":
            roams = [z for f in eng.fx if f.get("roam") for z in f["roam"]]
            need(not any(kinds.get(z["id"]) in want for z in roams), f"no roam over {want}")
        elif key == "profile_named":
            need(any(p.lower().startswith(want.lower()) for p in prof["order"]), f"a button profile named {want}")
        elif key == "pages":
            name = next((p for p in prof["order"] if p.lower().startswith(exp.get("profile_named", "").lower())), prof["active"])
            names = dict(getattr(eng, "quick_names", {}) or {}) if name == prof["active"] else \
                dict((prof["sets"].get(name) or {}).get("quick_names") or {})
            need(set(want) <= set(names.values()), f"pages {want} (got {sorted(names.values())})")
        elif key == "has_cues":
            need(any(pb.get("stack") for pb in eng.playbacks), "recorded cues")
        elif key == "no_cues":
            need(not any(pb.get("stack") for pb in eng.playbacks), "no cues (buttons only)")
        elif key == "review_clean":
            items = showreview.items(eng)
            need(not [i for i in items if i["level"] == "bad"], "nothing the review calls bad: "
                 + "; ".join(i["text"] for i in items if i["level"] == "bad")[:200])
        elif key == "lights_on":
            need(bool(lit), "some lights on")
        elif key == "lit_colour_any":
            cols = {str(x.get("colour") or "").lower() for x in lit}
            need(any(any(w in c for w in want) for c in cols), f"a lit colour like {want} (got {sorted(cols)})")
        elif key == "no_fx_running":
            need(not [f for f in eng.fx if (f.get("from") or "programmer") == "programmer"], "no effects running")
        elif key == "patched_at_least":
            need(len(eng.patch) >= want, f"at least {want} lights patched ({len(eng.patch)})")
        elif key == "patched_at_most":
            need(len(eng.patch) <= want, f"at most {want} lights patched ({len(eng.patch)})")
        elif key == "zones_at_least":
            need(len(plan["zones"]) >= want, f"at least {want} zones ({len(plan['zones'])})")
        elif key == "group_named":
            need(any(g["name"].lower() == want.lower() for g in eng.groups), f"a group called {want}")
        elif key == "buttons_on_at_least":
            need(len(eng.quick_active or []) >= want, f"at least {want} buttons on")
        elif key == "songs_with_show":
            need(sum(1 for s in eng._songs() if (s.get("timeline") or {}).get("tracks")) >= want, f"{want} songs with a show")
        elif key == "fx_colours":
            need(any((f.get("params") or {}).get("colours") for f in eng.fx), "an effect in its own colours")
    return bad


# -- a second AI judges the show it made (rules can't say "does it look good") --
JUDGE = """You judge a lighting show design made by an AI for a club, as a senior lighting
designer would.  Score each 1-5 (5 = excellent) and say the one thing most worth fixing:
  brief    - does it do what was asked (theme, colours, mood, what to avoid)?
  contrast - calm parts calmer than peaks; builds build; drops hit
  colour   - 2-3 colours a look, one leading, coherent; not a rainbow on everything
  room     - the room's rules kept (gentle VIP, nothing at the DJ / bar / stage, strobes only on the floor)
  rig      - uses what the lights can do (movers move, washes wash), shares them between zones"""
JUDGE_SCHEMA = {"type": "object", "properties": {
    **{k: {"type": "integer", "minimum": 1, "maximum": 5} for k in ("brief", "contrast", "colour", "room", "rig")},
    "fix": {"type": "string"}}, "required": ["brief", "contrast", "colour", "room", "rig"]}


def judge_show(eng, ask: str) -> dict | None:
    """Scores for the show this request made (None: no show, or no AI)."""
    d = (eng.timeline or {}).get("design")
    if not isinstance(d, dict) or not d.get("sections"):
        return None
    try:
        r = llm.structured([{"role": "system", "content": JUDGE + "\n\n" + roomsense.describe(roomsense.plan(eng))},
                            {"role": "user", "content": f"The request: {ask}\n\nThe design:\n"
                                                        + json.dumps(d, default=str)[:20000]}],
                           "scores", "The scores and the one fix.", JUDGE_SCHEMA, temperature=0.0)
    except (llm.LLMError, ValueError):
        return None
    out = {k: max(1, min(5, int(r.get(k) or 1))) for k in ("brief", "contrast", "colour", "room", "rig")}
    out["overall"] = round(sum(out.values()) / 5, 1)
    out["fix"] = str(r.get("fix") or "")[:200]
    return out


# -- a stand-in AI, for the selftest -----------------------------------------
def fake_chat(messages, tools=None, **_):
    """Looks once, then answers: enough to run the runner end to end."""
    if not any(m.get("role") == "tool" for m in messages):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "look_at_rig", "arguments": "{}"}}]}
    return {"role": "assistant", "content": "I only run the desk - try: warm wash on the movers."}


def run(only: str = "", fake: bool = False, out: Path | None = None, limit: int | None = None) -> dict:
    if not fake and not llm.available():
        raise SystemExit("No AI set up: add a key or the offline AI in Settings -> AI (or run with --fake to test the runner)")
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    cases = [c for c in cases if only.lower() in c["id"].lower()][:limit or None]
    results = []
    t0 = time.monotonic()
    for c in cases:
        eng = make_desk(c.get("rig", "club"), c.get("setup") or [])
        try:
            before = {"undo": _undo_len(eng)}
            t = time.monotonic()
            try:
                r = assistant.run_turn(eng, c["ask"], session=f"exam-{c['id']}", preview=False,
                                       chat=fake_chat if fake else None)
                if r.get("plan"):          # a big job: the plan first - the exam says go ahead
                    steps0 = r.get("steps") or []
                    r = assistant.run_turn(eng, "Go ahead with the plan, exactly as shown.", session=f"exam-{c['id']}",
                                           preview=False, chat=fake_chat if fake else None)
                    r["steps"] = steps0 + (r.get("steps") or [])
            except Exception as exc:  # noqa: BLE001 - a crash is a failed case, the exam goes on
                r = {"ok": False, "error": f"crashed: {exc}"}
            failed = judge(eng, r, c.get("expect") or {}, before) if r.get("ok") else [r.get("error") or "no answer"]
            st = r.get("steps") or []
            graded = judge_show(eng, c["ask"]) if not fake and r.get("ok") else None
            okf = [bool(s.get("ok")) for s in st if s.get("action")]
            results.append({"id": c["id"], "ask": c["ask"], "passed": not failed, "failed": failed,
                            "actions": len(okf), "actions_ok": sum(okf),
                            # a failed step put right later in the same request
                            "recovered": any(not okf[i] and any(okf[i + 1:]) for i in range(len(okf))),
                            "searched": sum(1 for s in st if s.get("action") == "search_web"),
                            **({"show_score": graded} if graded else {}),
                            "reply": (r.get("reply") or r.get("question") or "")[:600],
                            "steps": [s.get("summary") or s.get("action") for s in r.get("steps") or []][:30],
                            "seconds": round(time.monotonic() - t, 1)})
            mark = "PASS" if not failed else "FAIL"
            print(f"  {mark}  {c['id']:<26} {('; '.join(failed))[:110]}", flush=True)
        finally:
            eng.shutdown()
    passed = sum(r["passed"] for r in results)
    acts = sum(r.get("actions", 0) for r in results)
    fails = [r for r in results if r.get("actions", 0) > r.get("actions_ok", 0)]
    secs = sorted(r["seconds"] for r in results) or [0]
    metrics = {
        "action_accuracy": round(100 * sum(r.get("actions_ok", 0) for r in results) / acts) if acts else None,
        "recovery": round(100 * sum(1 for r in fails if r.get("recovered")) / len(fails)) if fails else None,
        "median_seconds": secs[len(secs) // 2], "slowest_seconds": secs[-1],
        "searches": sum(r.get("searched", 0) for r in results),
        "show_quality": (round(sum(r["show_score"]["overall"] for r in results if r.get("show_score"))
                               / max(1, sum(1 for r in results if r.get("show_score"))), 1)
                         if any(r.get("show_score") for r in results) else None)}
    report = {"at": time.strftime("%Y-%m-%d %H:%M"), "ai": "stand-in" if fake else llm.public().get("model"),
              "metrics": metrics,
              "passed": passed, "total": len(results), "score": round(100 * passed / max(1, len(results))),
              "seconds": round(time.monotonic() - t0), "cases": results}
    if out is None and not fake:
        out = REAL_DATA / "aiexam" / f"report-{time.strftime('%Y%m%d-%H%M')}.json"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed} of {len(results)} passed ({report['score']}%)" + (f" - report: {out}" if out else ""))
    pc = lambda v: "-" if v is None else f"{v}%"  # noqa: E731
    print(f"actions that worked: {pc(metrics['action_accuracy'])} | put right after a failure: {pc(metrics['recovery'])} | "
          f"answer time: {metrics['median_seconds']} s typical, {metrics['slowest_seconds']} s slowest | "
          f"look-ups online: {metrics['searches']}"
          + (f" | show quality (judged): {metrics['show_quality']} of 5" if metrics["show_quality"] else ""))
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else ""
    try:
        run(only=only, fake="--fake" in args)
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
