"""A whole gig, rehearsed by a script: build the venue, patch a mixed rig,
program looks, record cues, make buttons, build a timeline - then run the
show with the tempo, effects, buttons, faders and blackout, and finally
save it, load it into a fresh console and check nothing changed.

    python tools/gigcheck.py            # prints each step; exit 1 on failure

It uses a scratch fixture database and show folder: nothing in data/ is
touched.  The browser half (the screens) is tools/uicheck.mjs.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import engine as eng, fixlib, fixtures  # noqa: E402

LIBRARY = [("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), ("qlc", "Nicols/Nicols-Moover-Spot-120.qxf"),
           ("qlc", "Laserworld/Laserworld-RS400G.qxf"), ("ofl", "stairville/af-180-led-fogger.json"),
           ("qlc", "Showtec/Showtec-Pixel-Bar-12.qxf")]

fails: list[str] = []
oks = 0


def check(ok, what, detail=""):
    global oks
    if ok:
        oks += 1
    else:
        fails.append(f"{what}  {detail}".rstrip())
        print(f"  FAIL {what}  {detail}")
    return ok


def act(e, action_, ok=True, **p):
    try:
        r = e.act(action_, **p)
    except Exception as exc:                  # noqa: BLE001 - act() must never raise
        check(False, f"{action_} raised", "".join(traceback.format_exception_only(type(exc), exc)).strip())
        return {}
    if ok:
        check(r.get("ok"), f"{action_} {json.dumps(p)[:80]}", str(r.get("error")))
    return r


def frame(e, now=None) -> bytes:
    f = e.build_frames(now)
    return b"".join(bytes(f[u]) for u in sorted(f))


def state_of(e) -> dict:
    """What a saved show must bring back, as plain data."""
    def heads(h):
        return {k: h.get(k) for k in ("head_no", "manufacturer", "model", "mode", "universe", "address",
                                      "channels", "name", "x", "y", "z", "stance", "rot")}
    pbs = [{"n": i, "stack": [{"values": c.get("values"), "name": c.get("name"), "fade_s": c.get("fade_s"),
                               "fx": c.get("fx")} for c in pb.get("stack") or []],
            "name": pb.get("name")} for i, pb in enumerate(e.playbacks) if pb.get("stack")]
    snap = e.snapshot()
    return {
        "patch": [heads(h) for h in e.patch],
        "groups": [{k: g.get(k) for k in ("n", "name", "heads")} for g in e.groups],
        "palettes": snap.get("palettes"),
        "presets": [{k: p.get(k) for k in ("name", "values", "fx")} for p in (snap.get("presets") or [])],
        "playbacks": pbs,
        "quick": [{k: b.get(k) for k in ("id", "page", "slot", "label", "kind", "mode", "values", "fx_list", "fx",
                                         "colour", "hold", "keeps", "target")} for b in e.quick],
        "timeline": {"length": e.timeline.get("length"), "tracks": [
            {"kind": t.get("kind"), "clips": len(t.get("clips") or [])} for t in e.timeline.get("tracks") or []]},
        "zones": [(z.get("kind"), z.get("name")) for z in (e.venue.get("zones") or [])],
        "rigging": len(e.venue.get("rigging") or []),
        "moves": [m.get("name") for m in e.moves],
        "tempo": round(float((snap.get("tempo") or {}).get("bpm") or 0), 1),
    }


def diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            out += diff(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items, then {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
    elif a != b and not (isinstance(a, float) and isinstance(b, float) and abs(a - b) < 1e-6):
        out.append(f"{path}: {str(a)[:60]} -> {str(b)[:60]}")
    return out


def main() -> int:
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        for src, key in LIBRARY:
            fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows")
        try:
            print("1. venue")
            act(e, "venue_template", name="club")
            check(any(z.get("kind") == "dancefloor" for z in e.venue.get("zones") or []), "the club has a dance floor")
            print("2. rig")
            for q, n in (("LED PAR 4ch", 8), ("Moving Head Spot 16ch", 4), ("RGBW Bar 12ch", 4),
                         ("Intimidator Wave 360", 2), ("Moover Spot 120", 2), ("Pixel Bar 12", 2),
                         ("RS400G", 1), ("AF-180 LED Fogger", 1)):
                r = act(e, "add_heads", query=q, qty=n)
                check(len(r.get("heads") or []) == n, f"{n} x {q} patched", str(r.get("heads")))
            pars = [h["head_no"] for h in e.patch if h["model"] == "LED PAR 4ch"]
            spots = [h["head_no"] for h in e.patch if h["model"] in ("Moving Head Spot 16ch", "Moover Spot 120")]
            bars = [h["head_no"] for h in e.patch if h["model"] == "RGBW Bar 12ch"]
            laser = [h["head_no"] for h in e.patch if h["model"] == "RS400G"]
            fogger = [h["head_no"] for h in e.patch if "Fogger" in h["model"]]
            check(not act(e, "ready_check", ok=False).get("problems"), "Ready? finds nothing wrong",
                  str(e.act("ready_check").get("problems"))[:200])
            act(e, "group_create", name="PARs", heads=pars)
            act(e, "group_create", name="Spots", heads=spots)
            act(e, "group_create", name="Bars", heads=bars)
            print("3. looks and palettes")
            act(e, "select_heads", heads=pars + bars)
            act(e, "set_colour", colour="#ff0040")
            act(e, "record_palette", kind="colour", name="Hot pink")
            act(e, "set_colour", colour="#0066ff")
            act(e, "record_palette", kind="colour", name="Deep blue")
            act(e, "select_heads", heads=spots)
            act(e, "aim_spot", spot="floor")
            act(e, "record_palette", kind="position", name="Floor")
            act(e, "select_all")
            act(e, "set_intensity", level=80)
            act(e, "record_preset", name="Blue wash")
            print("4. cues")
            act(e, "clear_programmer")
            act(e, "select_heads", heads=pars + bars)
            act(e, "set_intensity", level=100)
            act(e, "set_colour", colour="#0066ff")
            act(e, "record_cue", playback=1, name="Intro", fade=1)
            act(e, "set_colour", colour="#ff0040")
            act(e, "select_heads", heads=spots)
            act(e, "set_intensity", level=100)
            act(e, "run_fx", name="circle")
            act(e, "select_heads", heads=pars + bars + spots)
            act(e, "record_cue", playback=1, name="Verse", fade=2)
            act(e, "run_fx", name="rainbow")
            act(e, "record_cue", playback=1, name="Drop", fade=0)
            act(e, "stop_fx")
            act(e, "clear_programmer")
            check(len(e.playbacks[0]["stack"]) == 3, "three cues on playback 1", str(len(e.playbacks[0]["stack"])))
            print("5. buttons")
            act(e, "quick_defaults")
            act(e, "select_heads", heads=spots)
            act(e, "set_intensity", level=60)
            act(e, "roam", zones=["dancefloor"])
            r = act(e, "quick_from_programmer", label="Roam floor", page=2)
            check("roam" in r.get("summary", "") and "brightness 60%" in r.get("summary", ""), "the hold button keeps roam + 60 %",
                  r.get("summary"))
            act(e, "quick_release_all")
            act(e, "quick_set", page=2, slot=5, button={"kind": "fog", "label": "Fog", "level": 50, "mode": "hold",
                                                        "target": {"heads": fogger}}, ok=False)
            act(e, "move_save", name="Floor circle", ok=False)
            print("6. timeline")
            act(e, "tempo_set", bpm=128)
            act(e, "timeline_from_playback", playback=1, ok=False)
            act(e, "timeline_build", bars=8, start=0, ok=False)
            check(any(t.get("clips") for t in e.timeline.get("tracks") or []), "the timeline has clips",
                  json.dumps(e.timeline)[:200])

            print("7. the show")
            act(e, "playback_level", playback=1, level=100)
            act(e, "cue_go", playback=1)
            now = time.monotonic()
            a, b = frame(e, now + 0.1), frame(e, now + 0.9)
            check(a != b, "Intro fades in (1 s fade: the frames move)")
            act(e, "cue_go", playback=1)
            check(any(f.get("from") == "cue" for f in e._fx_public()), "Verse brings its circle back")
            act(e, "cue_go", playback=1)
            check(any(f.get("lib") == "rainbow" for f in e._fx_public()), "Drop brings its rainbow")
            act(e, "cue_back", playback=1)
            act(e, "speed_master", pct=50)
            act(e, "speed_master", pct=100)
            # every button on page 1, pressed and let go
            for btn in [b for b in e.quick if b["page"] == 1][:24]:
                r = e.act("quick_press", id=btn["id"], down=True)
                if not r.get("ok") and "ARM" not in str(r.get("error")):
                    check(False, f"button {btn['label']} pressed", str(r.get("error")))
                frame(e)
                e.act("quick_press", id=btn["id"], down=False)
            act(e, "quick_release_all")
            act(e, "fx_arm", state=True)
            act(e, "fx_fog", heads=fogger, level=40, ok=False)
            act(e, "fx_laser", heads=laser, down=True, ok=False)
            act(e, "fx_kill")
            act(e, "timeline_play", at=0)
            f1 = frame(e)
            act(e, "timeline_stop")
            check(len(f1) > 0, "the timeline plays")
            act(e, "blackout", state=1)
            check(not any(frame(e)[i] for i in range(0)) and all(
                v == 0 for h in e.patch if h["head_no"] in pars for v in [e.build_frames()[h["universe"]][h["address"] - 1 + h["map"].index("dimmer")]]),
                "Blackout: every PAR dimmer at 0")
            act(e, "blackout", state=0)
            act(e, "master", level=50, ok=False)
            act(e, "master", level=100, ok=False)
            # Undo walks back without errors
            for _ in range(5):
                e.act("undo")

            print("8. save, reload, compare")
            before = state_of(e)
            # the programmer is a scratchpad, not part of a show (a desk's
            # too): compare what the show itself plays
            act(e, "clear_programmer")
            act(e, "playback_release", playback=1, ok=False)
            act(e, "cue_go", playback=1)
            ref = frame(e, time.monotonic() + 5)
            act(e, "save_show", name="Gig check")
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "shows")
            try:
                act(e2, "load_show", name="Gig check")
                after = state_of(e2)
                d = diff(before, after)
                check(not d, "the reloaded show is the same", "; ".join(d[:8]))
                # (a show comes back where each playback was - after a crash
                # it carries on; from the top here, like the reference)
                act(e2, "playback_release", playback=1, ok=False)
                act(e2, "playback_level", playback=1, level=100)
                act(e2, "cue_go", playback=1)
                again = frame(e2, time.monotonic() + 5)
                bad = [i for i, (x, y) in enumerate(zip(ref, again)) if x != y]
                where = []
                for i in bad[:6]:
                    u, ch = divmod(i, 512)
                    h = next((h for h in e2.patch if h["address"] - 1 <= ch < h["address"] - 1 + h["channels"]), None)
                    where.append(f"{h['model'] if h else '?'}#{h['head_no'] if h else '?'} {h['map'][ch - h['address'] + 1] if h else ch}: {ref[i]}->{again[i]}")
                check(ref == again, "...and its first cue puts out the same DMX", f"{len(bad)} channels differ: " + "; ".join(where))
            finally:
                e2.shutdown()
        finally:
            e.shutdown()
    print(f"\n{oks} ok, {len(fails)} failed in {time.time() - t0:.1f} s")
    for f in fails:
        print("  FAIL", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
