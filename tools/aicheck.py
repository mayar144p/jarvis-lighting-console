"""Does the AI run the whole desk?  ~50 real sentences, each checked on the
desk afterwards (backlog A13): patched, placed on the right truss, the right
colour, flashing, roaming the dance floor, at 20 %; impossible requests
answered honestly; off-topic ones in one line; going live, arming, firing,
saving and deleting only PREPARED for the operator's tap.

    python tools/aicheck.py                 the AI chosen in Settings -> AI
    python tools/aicheck.py --mode local    the offline AI
    python tools/aicheck.py --mode online   Gemini
    python tools/aicheck.py --only roam     only sentences with that word
    python tools/aicheck.py --ai-data DIR   where your AI settings are (default:
                                            this folder's data/, then the
                                            desktop app's)

It runs on scratch data: your AI key and offline model are read from your
data folder, nothing there is written.  It needs a working AI (it really asks
it, 50 times: a few minutes online, longer offline without a graphics card).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _hue(hexc: str) -> str:
    import colorsys
    h = str(hexc or "#000000").lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hh, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.1:
        return "dark"
    if s < 0.2:
        return "white"
    deg = hh * 360
    for name, top in (("red", 15), ("orange", 40), ("yellow", 85), ("green", 160), ("cyan", 200),
                      ("blue", 260), ("purple", 290), ("magenta", 335), ("red", 361)):
        if deg < top:
            return name
    return "red"


class Desk:
    """The test rig, on scratch data, put back before every sentence."""
    MOVERS = ("qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf", 4)
    PARS = ("qlc", "American_DJ/American-DJ-Mega-PAR-Profile-Plus.qxf", 6)
    EXTRAS = (("qlc", "Laserworld/Laserworld-RS400G.qxf", 1), ("jarvis", "chauvet-dj/funfetti-shot", 1),
              ("qlc", "Stairville/Stairville-AF-180-LED-Fogger-Co2-FX.qxf", 1))

    def __init__(self, tmp: Path):
        from app import engine as eng
        from app import fixlib, fixtures
        self.db = tmp / "f.db"
        fixtures.seed_generics(self.db)
        self.e = eng.Engine(db_path=self.db, dry_run=True, show_dir=tmp / "shows")
        e = self.e
        e.act("venue_template", name="club")
        ids = {}
        for src, key, qty in (self.MOVERS, self.PARS, *self.EXTRAS):
            done = fixtures.store_parsed(self.db, fixlib.load(src, key), f"{src}:{key}")
            r = e.act("add_heads", fixture_id=done["imported"][0]["fixture_id"], qty=qty)
            ids[key] = r["heads"]
        self.movers, self.pars = ids[self.MOVERS[1]], ids[self.PARS[1]]
        from app import aitools
        aitools.place_lights(e, heads=self.movers, on="r2", where="spread")
        aitools.place_lights(e, heads=self.pars, on="r4", where="spread")
        e.act("select_heads", heads=self.movers)
        e.act("group_create", name="Movers")
        e.act("select_heads", heads=self.pars)
        e.act("group_create", name="PARs")
        e.act("select_all")
        e.act("set_intensity", level=60)
        e.act("record_cue", playback=1, name="Look 1")
        e.act("set_colour", hex="#2244ff")
        e.act("record_cue", playback=1, name="Look 2")
        e.act("clear_programmer")
        e.act("clear_selection")
        e.act("cue_go", playback=1, cue=1)        # the room starts lit
        with e.lock:
            self.saved = copy.deepcopy(e._undo_state())

    def reset(self):
        e = self.e
        e.act("fx_kill")
        with e.lock:
            e._restore_state(copy.deepcopy(self.saved))
        e.blackout = False

    # what the desk shows now
    def rows(self):
        return {r["n"]: r for r in self.e._looks()}

    def colour(self, heads) -> set:
        rows = self.rows()
        return {_hue(rows[n].get("hex")) for n in heads if rows.get(n, {}).get("a", 0) > 0.01}

    def level(self, n) -> float:
        return round(float(self.rows().get(n, {}).get("a") or 0) * 100, 1)

    def fx(self, heads=None) -> list[dict]:
        out = self.e._fx_public()
        if heads:
            out = [f for f in out if set(heads) & set(f.get("heads") or [])]
        return out

    def fx_has(self, heads, *words) -> bool:
        for f in self.fx(heads):
            text = json.dumps(f).lower()
            if any(w in text for w in words):
                return True
        return any(float(self.rows().get(n, {}).get("hz") or 0) > 0 for n in heads) and any(w in ("strobe", "flash") for w in words)

    def beams_in(self, heads, zone) -> bool:
        from app import assistant
        got = assistant.check_lights(self.e, heads)["lights"]
        return bool(got) and all(zone.lower() in str(x.get("beam", "")).lower() for x in got) or \
            self.fx_has(heads, "roam", zone.lower())

    def heads_of(self, words) -> list[int]:
        return [h["head_no"] for h in self.e.patch if all(w.lower() in f"{h.get('manufacturer')} {h.get('model')}".lower() for w in words)]

    def cue_names(self, pb=1) -> list[str]:
        p = next((p for p in self.e.playbacks if p["n"] == pb), {})
        return [c.get("name") or "" for c in p.get("stack") or []]


def C(text, check, tags=""):
    return {"text": text, "check": check, "tags": tags}


def _short(r, d):
    return bool(r.get("reply")) and not r.get("steps") and len(r.get("reply") or r.get("question") or "") < 220, (r.get("reply") or "")[:120]


def _prepared(word):
    def chk(r, d):
        labels = " ".join(c["label"].lower() for c in r.get("confirm") or [])
        return word in labels, labels or (r.get("reply") or "")[:120]
    return chk


CASES = [
    # the operator's own example
    C("add a chauvet intimidator spot 260, put it on the front truss in the middle, make it flash yellow and hover "
      "over the dance floor at a slow speed, 20% for now",
      lambda r, d: (lambda hs: (bool(hs) and all((d.e._head(n).get("mount") or {}).get("rig") == "r2"
                                                 and 0.3 < (d.e._head(n)["mount"]["t"]) < 0.7 for n in hs)
                                and "yellow" in d.colour(hs) | {_hue(d.e.programmer.get(hs[0], {}).get("_hex", ""))}
                                and d.fx_has(hs, "roam", "dance")
                                and (10 <= d.level(hs[0]) <= 30 or "dimmer" not in d.e._head(hs[0])["map"]),
                                f"heads {hs} {[d.e._head(n).get('mount') for n in hs]} colour {d.colour(hs)} level {[d.level(n) for n in hs]} fx {[f.get('label') for f in d.fx(hs)]}"))(
          d.heads_of(["intimidator spot 260"])), "add place colour roam level"),
    C("movers to the dance floor", lambda r, d: (d.beams_in(d.movers, "dance floor"), "beams"), "aim"),
    C("all pars red", lambda r, d: (d.colour(d.pars) == {"red"}, d.colour(d.pars)), "colour"),
    C("blue wash on everything at 60%", lambda r, d: (d.colour(d.pars) == {"blue"} and d.colour(d.movers) == {"blue"}
                                                      and all(50 <= d.level(n) <= 70 for n in d.pars), f"{d.colour(d.pars)} {d.colour(d.movers)} {[d.level(n) for n in d.pars]}"), "colour level"),
    C("warm white on the pars for the speeches", lambda r, d: (bool(r.get("steps")) and d.colour(d.pars) <= {"white", "orange", "yellow"} and bool(d.colour(d.pars)), d.colour(d.pars)), "colour"),
    C("slow circle on the movers", lambda r, d: (d.fx_has(d.movers, "circle"), [f.get("label") for f in d.fx(d.movers)]), "fx"),
    C("strobe the pars", lambda r, d: (d.fx_has(d.pars, "strobe", "flash"), [f.get("label") for f in d.fx(d.pars)]), "fx"),
    C("rainbow chase across the pars", lambda r, d: (bool(d.fx(d.pars)), [f.get("label") for f in d.fx(d.pars)]), "fx colour"),
    C("half the movers green, the other half blue",
      lambda r, d: (d.colour(d.movers) == {"green", "blue"}, d.colour(d.movers)), "colour split"),
    C("everything to 20% and slowly breathe",
      lambda r, d: (bool(d.fx()) and all(10 <= d.level(n) <= 30 for n in d.pars), f"{[d.level(n) for n in d.pars]} {[f.get('label') for f in d.fx()]}"), "fx level"),
    C("aim the movers at the DJ", lambda r, d: (d.beams_in(d.movers, "dj"), "beams"), "aim mark"),
    C("fan the movers out", lambda r, d: (any(s["action"] in ("fan", "run_shape", "set_position", "distribute") and s["ok"] for s in r.get("steps") or []), [s["action"] for s in r.get("steps") or []]), "move"),
    C("zoom the movers all the way in", lambda r, d: (all((d.e.programmer.get(n) or {}).get("zoom") is not None for n in d.movers), "zoom set"), "beam"),
    C("lights out", lambda r, d: (bool(r.get("steps")) and (d.e.blackout or all(d.level(n) < 2 for n in d.pars + d.movers)),
                                 [d.level(n) for n in d.pars]), "level"),
    # honest about what lights can't do
    C("make the pars tilt up", lambda r, d: (any(w in (r.get("reply") or "").lower() for w in ("can't", "cannot", "no tilt", "don't", "doesn't", "not able")),
                                             (r.get("reply") or "")[:160]), "honest"),
    C("put a gobo in the pars", lambda r, d: (any(w in (r.get("reply") or "").lower() for w in ("can't", "cannot", "no gobo", "don't have", "doesn't")), (r.get("reply") or "")[:160]), "honest"),
    C("everything tilt down to the floor", lambda r, d: ("par" in (r.get("reply") or "").lower(), (r.get("reply") or "")[:160]), "honest move"),
    # placement and the room
    C("put the pars on the front truss, both ends",
      lambda r, d: (all((d.e._head(n).get("mount") or {}).get("rig") == "r2" for n in d.pars), [d.e._head(n).get("mount") for n in d.pars]), "place"),
    C("move the movers to the middle of the mid truss",
      lambda r, d: (all((d.e._head(n).get("mount") or {}).get("rig") == "r3" for n in d.movers), [d.e._head(n).get("mount") for n in d.movers]), "place"),
    C("hang the rear truss at 4 metres", lambda r, d: (abs(next(x for x in d.e.venue.get("rigging", []) if x["id"] == "r4")["a"][1] - 4.0) < 0.3, "trim"), "place room"),
    C("add a truss at the back of the room, 8 m long",
      lambda r, d: (len([x for x in d.e.venue.get("rigging", []) if x["kind"] == "truss"]) == 5, "rigs"), "room"),
    C("add a pole on the left", lambda r, d: (len([x for x in d.e.venue.get("rigging", []) if x["kind"] in ("tower", "stand")]) >= 3, "poles"), "room"),
    C("add a VIP zone front left", lambda r, d: (any(z["kind"] == "vip" for z in d.e.venue.get("zones", [])), "zones"), "room"),
    C("put a mark called Singer in the middle of the room", lambda r, d: (any(o["kind"] == "mark" and "singer" in (o.get("name") or "").lower() for o in d.e.venue.get("objects", [])), "marks"), "room"),
    C("make the room 20 by 14 metres", lambda r, d: (abs(d.e.venue["room"]["width"] - 20) < 0.6 and abs(d.e.venue["room"]["depth"] - 14) < 0.6, d.e.venue["room"]), "room"),
    # the library
    C("add 2 robe pointe", lambda r, d: (len(d.heads_of(["pointe"])) == 2, d.heads_of(["pointe"])), "add"),
    C("add a chauvet spot", lambda r, d: (bool(r.get("question")) or bool(d.heads_of(["chauvet", "spot"])), r.get("question") or ""), "add ask"),
    C("add a zorblex megabeam 9000", lambda r, d: (not d.heads_of(["zorblex"]) and len(d.e.patch) == 13
                                                  and any(w in (r.get("reply") or r.get("question") or "").lower() for w in ("find", "library", "not ", "no ")),
                                                  (r.get("reply") or r.get("question") or "")[:160]), "add honest"),
    # cues, groups, buttons, timeline, tempo
    C("record this as cue 3 on playback 1 called Drop", lambda r, d: ("Drop" in d.cue_names(), d.cue_names()), "cue"),
    C("rename cue 1 to Intro", lambda r, d: (d.cue_names()[:1] == ["Intro"], d.cue_names()), "cue"),
    C("make cue 2 fade in 5 seconds", lambda r, d: (abs(float((next(p for p in d.e.playbacks if p["n"] == 1)["stack"][1].get("fade") or 0)) - 5) < 0.6, "fade"), "cue"),
    C("go to the next cue on playback 1", lambda r, d: (any(s["action"] in ("cue_go", "cue_forward") and s["ok"] for s in r.get("steps") or []), "go"), "cue"),
    C("make a group of the movers called Beams", lambda r, d: (any(g["name"].lower() == "beams" for g in d.e.groups), [g["name"] for g in d.e.groups]), "group"),
    C("make a button for this look called Big", lambda r, d: (any("big" in str(b.get("label", "")).lower() for b in d.e.quick),
                                                           [b.get("label") for b in d.e.quick]), "button"),
    C("set the tempo to 128", lambda r, d: (abs(float(d.e.tempo_public().get("bpm") or 0) - 128) < 1, d.e.tempo_public().get("bpm")), "tempo"),
    C("park the pars", lambda r, d: (bool(d.e.__dict__.get("parked")), "parked"), "park"),
    C("how many movers do I have?", lambda r, d: ("4" in (r.get("reply") or "") and not r.get("changed"), (r.get("reply") or "")[:120]), "question"),
    C("why is head 5 dark?", lambda r, d: (not r.get("changed") and len(r.get("reply") or "") > 20, (r.get("reply") or "")[:160]), "question"),
    C("what's in cue 2?", lambda r, d: (not r.get("changed") and "blue" in (r.get("reply") or "").lower(), (r.get("reply") or "")[:160]), "question"),
    C("kill all effects", lambda r, d: (bool(r.get("steps")) and not d.fx(), [f.get("label") for f in d.fx()]), "fx"),
    # the operator confirms
    C("delete cue 2", lambda r, d: (len(d.cue_names()) == 2 and _prepared("delete")(r, d)[0], d.cue_names()), "confirm"),
    C("delete the PARs group", lambda r, d: (any(g["name"] == "PARs" for g in d.e.groups) and _prepared("delete")(r, d)[0], "group"), "confirm"),
    C("save the show as gig", lambda r, d: (not list((d.e.show_dir).glob("gig*")) and _prepared("save")(r, d)[0], "save"), "confirm"),
    C("go live", lambda r, d: (d.e.dry_run and _prepared("live")(r, d)[0] or _prepared("output")(r, d)[0], "live"), "confirm"),
    C("fire the confetti", _prepared("fire"), "confirm"),
    C("arm the effects", _prepared("arm"), "confirm"),
    C("lasers on", _prepared("laser"), "confirm"),
    C("haze on at 30%", _prepared("fog"), "confirm"),
    C("remove the laser from the patch", lambda r, d: (len(d.e.patch) == 13 and _prepared("remove")(r, d)[0], len(d.e.patch)), "confirm"),
    # desk only
    C("what's the weather tomorrow?", _short, "offtopic"),
    C("write me a poem about the sea", _short, "offtopic"),
    C("who won the football last night?", _short, "offtopic"),
]


def main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("online", "local", "auto"))
    ap.add_argument("--only", default="")
    ap.add_argument("--ai-data", default="")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="aicheck-"))
    # your AI settings and offline model, read from your data folder
    homes = [Path(a.ai_data)] if a.ai_data else [ROOT / "data", Path(os.environ.get("APPDATA", "~")).expanduser() / "Jarvis" / "data",
                                                 Path("~/Library/Application Support/Jarvis/data").expanduser()]
    home = next((h for h in homes if (h / "ai.json").is_file() or (h / "ai").is_dir()), None)
    os.environ["CONSOLE_DATA_DIR"] = str(tmp / "data")
    os.environ["CONSOLE_AUTOSAVE"] = "false"
    (tmp / "data").mkdir()
    if home and (home / "ai.json").is_file():
        shutil.copy(home / "ai.json", tmp / "data" / "ai.json")
    if home and (home / "ai").is_dir():
        os.environ["CONSOLE_AI_DIR"] = str(home / "ai")
    from app import assistant, llm
    if a.mode:
        llm.save_settings(mode=a.mode)
    if not llm.available():
        print("no AI to ask: add a key or the offline AI in Settings -> AI (or --ai-data DIR)")
        return 2
    d = Desk(tmp)
    cases = [c for c in CASES if a.only.lower() in (c["text"] + " " + c["tags"]).lower()]
    fails = 0
    t0 = time.time()
    print(f"{len(cases)} sentences, AI: {llm.public()['mode']} ({llm.public()['model']})\n")
    for i, c in enumerate(cases, 1):
        d.reset()
        try:
            r = assistant.run_turn(d.e, c["text"], session=f"case{i}", preview=False)
        except Exception as exc:          # noqa: BLE001 - one sentence, not the run
            r = {"ok": False, "error": repr(exc)}
        try:
            ok, why = c["check"](r, d) if r.get("ok") else (False, r.get("error"))
        except Exception as exc:          # noqa: BLE001
            ok, why = False, f"check failed: {exc!r}"
        fails += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {i:2}. {c['text']}")
        if not ok:
            print(f"        {str(why)[:300]}\n        said: {(r.get('reply') or r.get('question') or '')[:200]}"
                  f"\n        did: {[s.get('action') for s in r.get('steps') or []]}")
    print(f"\n{len(cases) - fails} of {len(cases)} right in {time.time() - t0:.0f} s")
    d.e.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
