"""Odd inputs: what happens when the desk is used the way nobody planned.

    python tools/oddcheck.py            # every section; exit 1 on a failure
    python tools/oddcheck.py empty big  # just these sections

Sections:
  empty    no lights patched: every action, then Undo all the way back
  junk     every action given nonsense (text for numbers, huge numbers,
           negative cue numbers, a 5,000-letter name...) on a small rig
  big      520 lights over many universes: patch, program, play, record,
           save and load - and the 40 Hz output keeps up
  corrupt  broken show files (cut short, empty, garbage, the wrong shape,
           impossible addresses): loading refuses or cleans them, the show
           already up stays, and a broken autosave never stops the start
  restart  lights patched from the built-in list survive a restart and a
           show load with their channel jobs
  undo     after a busy session, Undo all the way back and Redo all the
           way forward: no errors, and the redone show is the same

A finding is: an action that raises (act() must never raise), an
"internal error" (an unexpected crash inside an action), an action that
hangs, a frame or snapshot that fails, or a wrong result.  Expected
refusals ("no fixtures selected") are fine - they are the point.

"Unusual modes" is `python tools/libsweep.py --all-modes` (every mode of
every library light); the browser half (a dropped connection, two
browsers at once) is tools/oddcheck_ui.mjs.  Nothing in data/ is touched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DMX_HOST", "127.0.0.1")     # never the real node

from app import engine as eng, fixlib, fixtures  # noqa: E402
from app.engine_base import ACTIONS  # noqa: E402

fails: list[str] = []
oks = 0

# Actions that wait on the network or the outside world by design; given a
# short leash rather than skipped.
SLOW = {"import_scan", "rdm_compare", "tempo_link", "tempo_prodj", "virtual_node", "osc", "timecode"}


def check(ok, what, detail=""):
    global oks
    if ok:
        oks += 1
    else:
        fails.append(f"{what}  {detail}".rstrip())
        print(f"  FAIL {what}  {detail}")
    return ok


def call(e, action, timeout=8.0, **p) -> dict:
    """act() on a thread with a time limit: a raise, a hang or an internal
    error is a finding; anything else is returned."""
    box: dict = {}

    def go():
        try:
            box["r"] = e.act(action, **p)
        except Exception as exc:                  # noqa: BLE001 - act() must never raise
            box["exc"] = "".join(traceback.format_exception_only(type(exc), exc)).strip()

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    shown = json.dumps(p, default=str)[:90]
    if t.is_alive():
        check(False, f"{action} {shown} hangs", f"(over {timeout:.0f} s)")
        return {}
    if "exc" in box:
        check(False, f"{action} {shown} raised", box["exc"][:200])
        return {}
    r = box.get("r") or {}
    if "internal error" in str(r.get("error") or ""):
        check(False, f"{action} {shown}: internal error", str(r.get("error"))[:240])
    else:
        check(True, action)
    return r


def healthy(e, where: str) -> None:
    """The desk still works: frames build, the snapshot serialises."""
    try:
        e.build_frames()
        json.dumps(e.snapshot(), default=str)
        check(True, where)
    except Exception as exc:                      # noqa: BLE001
        check(False, f"{where}: the desk broke", "".join(traceback.format_exception_only(type(exc), exc)).strip()[:240])


def new_engine(tmp: Path, **kw) -> eng.Engine:
    return eng.Engine(db_path=tmp / "f.db", dry_run=True, show_dir=tmp / "shows", **kw)


def setup_db(tmp: Path) -> None:
    db = tmp / "f.db"
    fixtures.seed_generics(db)
    for src, key in (("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), ("qlc", "Laserworld/Laserworld-RS400G.qxf"),
                     ("ofl", "stairville/af-180-led-fogger.json"), ("qlc", "Showtec/Showtec-Pixel-Bar-12.qxf")):
        fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")


def small_rig(e) -> None:
    e.act("venue_template", name="club")
    for q, n in (("LED PAR 4ch", 4), ("Moving Head Spot 16ch", 2), ("Pixel Bar 12", 1), ("RS400G", 1), ("AF-180 LED Fogger", 1)):
        e.act("add_heads", query=q, qty=n)


def undo_all(e, where: str, limit: int = 200) -> int:
    n = 0
    while e._undo and n < limit:
        r = call(e, "undo")
        if not r.get("ok"):
            break
        n += 1
    check(not e._undo, f"{where}: Undo walks all the way back", f"{len(e._undo)} steps left after {n}")
    healthy(e, f"{where}: after undoing everything")
    return n


# --------------------------------------------------------------- sections
def sec_empty(tmp: Path) -> None:
    print("empty rig: every action with no lights patched")
    e = new_engine(tmp)
    try:
        healthy(e, "empty desk")
        for a in ACTIONS:
            if a in ("undo", "redo"):
                continue
            call(e, a, timeout=4.0 if a in SLOW else 8.0)
        healthy(e, "empty desk after every action")
        # the actions an operator presses most, with the usual values
        for a, p in (("select_all", {}), ("set_intensity", {"level": 100}), ("set_colour", {"colour": "#ff0000"}),
                     ("record_cue", {"playback": 1}), ("cue_go", {"playback": 1}), ("run_fx", {"name": "rainbow"}),
                     ("locate", {}), ("roam", {"zones": ["dancefloor"]}), ("quick_defaults", {}),
                     ("quick_from_programmer", {"label": "x"}), ("timeline_build", {"bars": 8}),
                     ("blackout", {"state": 1}), ("blackout", {"state": 0}), ("save_show", {"name": "empty"}),
                     ("load_show", {"name": "empty"}), ("highlight", {"state": True}), ("aim_spot", {"spot": "floor"})):
            call(e, a, **p)
        healthy(e, "empty desk after the usual presses")
        undo_all(e, "empty rig")
        call(e, "redo")
        healthy(e, "empty desk after a redo")
    finally:
        e.shutdown()


JUNK = {
    "heads": ["x", [None], [-1, 99999], {"a": 1}],
    "level": ["loud", -50, 1e12, None],
    "playback": [-5, "a", 10**9, 0],
    "cue": [None, -1, "x", 1e9],
    "name": [123, "a" * 5000, "", "../../etc/passwd", None],
    "colour": ["notacolour", "#zzzzzz", 12, ""],
    "fade": [-1, "slow", 1e9],
    "page": [999, -1, "p"],
    "slot": [-1, 1000, "s"],
    "id": ["nope", -3, None],
    "universe": [0, -1, 70000],
    "address": [0, 600, "a"],
    "qty": [10000, -3, "many"],
    "state": ["maybe", 7, None],
    "bpm": [0, -120, 1e6, "fast"],
    "zones": ["floor", [None], 5],
    "button": ["x", {"kind": "nope"}, None],
    "params": ["x", [1, 2]],
    "at": [-10, "now", 1e12],
}


def sec_junk(tmp: Path) -> None:
    print("junk: every action given nonsense, one value at a time")
    e = new_engine(tmp)
    try:
        small_rig(e)
        e.act("select_all")
        e.act("set_intensity", level=100)
        e.act("record_cue", playback=1)
        e.act("quick_defaults")
        before = len(fails)
        for a in ACTIONS:
            if a in ("undo", "redo", "set_dry_run", "set_output"):
                continue
            for key, values in JUNK.items():
                for v in values:
                    call(e, a, timeout=4.0 if a in SLOW else 8.0, **{key: v})
            healthy(e, f"after junk to {a}")
        # two taps in the same clock tick (Windows' clock ticks every 16 ms)
        now = time.monotonic()
        try:
            e.tempo.tap(now)
            e.tempo.tap(now)
            check(True, "two tempo taps at the same instant")
        except Exception as exc:                  # noqa: BLE001
            check(False, "two tempo taps at the same instant", repr(exc))
        check(len(fails) == before, "junk: no action crashed or hung", f"{len(fails) - before} findings")
        undo_all(e, "after the junk", limit=400)
    finally:
        e.shutdown()


def sec_big(tmp: Path) -> None:
    print("big rig: 520 lights")
    e = new_engine(tmp)
    try:
        e.act("venue_template", name="club")
        t0 = time.perf_counter()
        for q, n, times in (("LED PAR 4ch", 60, 5), ("Moving Head Spot 16ch", 50, 3), ("RGBW Bar 12ch", 40, 1), ("Pixel Bar 12", 30, 1)):
            for _ in range(times):
                r = call(e, "add_heads", query=q, qty=n)
                check(len(r.get("heads") or []) == n, f"{n} x {q} patched", str(r.get("error") or "")[:120])
        patch_s = time.perf_counter() - t0
        n_heads = len(e.patch)
        unis = sorted({h["universe"] for h in e.patch})
        print(f"  {n_heads} lights over {len(unis)} universes, patched in {patch_s:.1f} s")
        check(n_heads >= 500, "500+ lights patched", str(n_heads))
        # no two lights share a channel
        used: dict = {}
        clash = 0
        for h in e.patch:
            for c in range(h["address"], h["address"] + h["channels"]):
                clash += (h["universe"], c) in used
                used[(h["universe"], c)] = h["head_no"]
        check(clash == 0, "no two lights share a DMX channel", f"{clash} clashes")
        check(not (e.act("ready_check").get("problems")), "Ready? is happy with 520 lights",
              str(e.act("ready_check").get("problems"))[:200])

        def timed(label, fn, budget_ms):
            t = time.perf_counter()
            fn()
            ms = (time.perf_counter() - t) * 1000
            check(ms <= budget_ms, f"{label} in {ms:.0f} ms", f"(budget {budget_ms} ms)")
            return ms

        timed("select all", lambda: call(e, "select_all"), 500)
        timed("set colour on 520", lambda: call(e, "set_colour", colour="#ff3300"), 1000)
        timed("full on 520", lambda: call(e, "set_intensity", level=100), 1000)
        timed("rainbow on 520", lambda: call(e, "run_fx", name="rainbow"), 1500)
        timed("record a cue of 520", lambda: call(e, "record_cue", playback=1, name="Big"), 2000)
        call(e, "stop_fx")
        call(e, "clear_programmer")
        call(e, "playback_level", playback=1, level=100)
        call(e, "cue_go", playback=1)
        # the output loop runs 40 times a second: a frame must take well under 25 ms
        times = []
        for _ in range(40):
            t = time.perf_counter()
            e.build_frames()
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        med, worst = times[len(times) // 2], times[-1]
        print(f"  one frame: median {med:.1f} ms, worst {worst:.1f} ms")
        check(med < 20, "a frame of 520 lights builds fast enough for 40 Hz (25 ms)", f"median {med:.1f} ms")
        t = time.perf_counter()
        snap = json.dumps(e.snapshot(), default=str)
        snap_ms = (time.perf_counter() - t) * 1000
        print(f"  the screen's snapshot: {len(snap) / 1024:.0f} KB in {snap_ms:.0f} ms")
        check(snap_ms < 800, "the screen's snapshot builds in time", f"{snap_ms:.0f} ms")
        check(len(snap) < 6_000_000, "the snapshot is a sensible size", f"{len(snap) / 1024:.0f} KB")
        timed("Locate on 520", lambda: (call(e, "select_all"), call(e, "locate")), 1500)
        timed("Undo on 520", lambda: call(e, "undo"), 1000)
        timed("save the 520-light show", lambda: call(e, "save_show", name="Big"), 3000)
        e2 = new_engine(tmp)
        try:
            timed("load the 520-light show", lambda: call(e2, "load_show", name="Big"), 6000)
            check(len(e2.patch) == n_heads, "the loaded show has every light", f"{len(e2.patch)} of {n_heads}")
            healthy(e2, "loaded big show")
        finally:
            e2.shutdown()
        undo_all(e, "big rig")
        check(not e.patch, "Undo takes the big rig all the way back to empty", f"{len(e.patch)} lights left")
    finally:
        e.shutdown()


def _good_show(tmp: Path) -> dict:
    e = new_engine(tmp)
    try:
        small_rig(e)
        e.act("select_all")
        e.act("set_colour", colour="#0000ff")
        e.act("record_cue", playback=1)
        e.act("save_show", name="Good")
    finally:
        e.shutdown()
    return json.loads((tmp / "shows" / "Good.json").read_text(encoding="utf-8"))


def sec_corrupt(tmp: Path) -> None:
    print("corrupt show files")
    good = _good_show(tmp)
    text = json.dumps(good)
    bad: dict[str, str | bytes] = {
        "empty file": "",
        "cut short": text[: len(text) // 2],
        "garbage": b"\x00\xff\xfe garbage \x89PNG\r\n",
        "a list, not a show": "[1, 2, 3]",
        "just a number": "42",
        "null": "null",
        "patch is text": json.dumps(dict(good, patch="lights")),
        "a light with junk": json.dumps(dict(good, patch=[{"head_no": "a", "universe": -1, "address": 9999, "channels": "x"}])),
        "patch of nulls": json.dumps(dict(good, patch=[None, None])),
        "two lights on one address": json.dumps(dict(good, patch=good["patch"] + good["patch"][:1])),
        "playbacks junk": json.dumps(dict(good, playbacks=[None, "x", {"stack": "cues"}, {"stack": [None, {"values": "x"}]}])),
        "quick junk": json.dumps(dict(good, quick=[None, {"page": "a"}, {"page": 1, "slot": 2, "kind": 5}])),
        "timeline junk": json.dumps(dict(good, timeline=5)),
        "venue junk": json.dumps(dict(good, venue={"room": "big", "rigging": [None, 3]})),
        "groups junk": json.dumps(dict(good, groups=[None, {"heads": "x"}, 7])),
        "palettes junk": json.dumps(dict(good, palettes=[1, 2])),
        "presets junk": json.dumps(dict(good, presets=["x", {"values": 5}])),
        "moves junk": json.dumps(dict(good, moves=[None, {"name": 5, "params": "x"}])),
        "meta junk": json.dumps(dict(good, meta="x")),
        "tempo junk": json.dumps(dict(good, meta={"tempo": {"bpm": "fast"}, "master": "loud"})),
        "everything null": json.dumps({k: None for k in good}),
        "from the future": json.dumps(dict(good, version=999, brand_new_thing={"x": 1})),
        "huge name": json.dumps(dict(good, patch=[dict(good["patch"][0], name="n" * 100000)])),
    }
    shows = tmp / "shows"
    e = new_engine(tmp)
    try:
        call(e, "load_show", name="Good")
        ref = len(e.patch)
        for label, body in bad.items():
            name = "Bad " + label.replace(",", "")
            path = shows / f"{e._safe_name(name)}.json"
            path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
            r = call(e, "load_show", name=name)
            healthy(e, f"after loading the {label!r} show")
            if not r.get("ok"):
                check(len(e.patch) == ref, f"{label!r} refused, the show that was up stays",
                      f"{len(e.patch)} lights now, {ref} before; {str(r.get('error'))[:80]}")
            call(e, "load_show", name="Good")
        # the list of shows with broken files in it
        call(e, "show_versions")
        # a broken autosave never stops the desk from starting
        for label, body in bad.items():
            auto = tmp / "auto.json"
            auto.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
            try:
                e2 = new_engine(tmp, autosave_path=auto, restore=True)
            except Exception as exc:              # noqa: BLE001
                check(False, f"the desk starts with a {label!r} autosave",
                      "".join(traceback.format_exception_only(type(exc), exc)).strip()[:200])
                continue
            try:
                check(True, f"starts with {label!r} autosave")
                healthy(e2, f"started with a {label!r} autosave")
                call(e2, "select_all")
                call(e2, "set_intensity", level=50)
            finally:
                e2.shutdown()
    finally:
        e.shutdown()


def sec_undo(tmp: Path) -> None:
    print("undo after everything: a busy session, back to the start and forward again")
    e = new_engine(tmp)
    try:
        start = json.dumps(e._undo_state(), sort_keys=True, default=str)
        small_rig(e)
        steps = [("select_all", {}), ("set_intensity", {"level": 80}), ("set_colour", {"colour": "#00ff00"}),
                 ("run_fx", {"name": "rainbow"}), ("record_cue", {"playback": 1, "name": "A"}),
                 ("group_create", {"name": "All", "heads": [1, 2, 3]}), ("record_palette", {"kind": "colour", "name": "Green"}),
                 ("record_preset", {"name": "Look"}), ("quick_defaults", {}), ("quick_from_programmer", {"label": "Mine"}),
                 ("roam", {"zones": ["dancefloor"]}), ("move_save", {"name": "Roam"}), ("stop_fx", {}),
                 ("timeline_build", {"bars": 4}), ("venue_add", {"kind": "truss"}), ("rename_head", {"head": 1, "name": "Front"}),
                 ("set_address", {"head": 2, "universe": 2, "address": 1}), ("remove_heads", {"heads": [3]}),
                 ("clear_programmer", {})]
        for a, p in steps:
            call(e, a, **p)
        busy = json.dumps(e._undo_state(), sort_keys=True, default=str)
        n = undo_all(e, "busy session")
        back = json.dumps(e._undo_state(), sort_keys=True, default=str)
        check(back == start, "Undo all the way gives the empty desk back", "the state differs")
        for _ in range(n):
            if not call(e, "redo").get("ok"):
                break
        healthy(e, "after redoing everything")
        again = json.dumps(e._undo_state(), sort_keys=True, default=str)
        check(again == busy, "Redo all the way gives the busy show back", _first_diff(busy, again))
        # a long session: the undo history keeps its limit and stays usable
        for i in range(150):
            e.act("set_intensity", level=i % 100)
            e.act("clear_programmer")
        check(len(e._undo) <= 60, "the undo history keeps its limit", str(len(e._undo)))
        undo_all(e, "after a long session", limit=100)
    finally:
        e.shutdown()


def _first_diff(a: str, b: str) -> str:
    da, db = json.loads(a), json.loads(b)
    for k in sorted(set(da) | set(db)):
        if da.get(k) != db.get(k):
            return f"first difference in {k!r}"
    return ""


def sec_restart(tmp: Path) -> None:
    print("restart: lights patched from the built-in list (not installed) come back working")
    bare = tmp / "bare"
    bare.mkdir()
    db = bare / "empty.db"
    fixtures.connect(db).close()                          # an empty library
    auto = bare / "auto.json"
    e = eng.Engine(db_path=db, dry_run=True, show_dir=bare / "shows", autosave_path=auto)
    try:
        r = call(e, "add_heads", query="LED PAR 4ch", qty=2)
        check(bool(r.get("heads")), "a light patches from the built-in list", str(r.get("error")))
        want = list(e.patch[0]["map"]) if e.patch else []
        check(want and "raw" not in want, "...with its channel jobs", str(want))
        call(e, "save_show", name="Builtin")
    finally:
        e.shutdown()
    e2 = eng.Engine(db_path=db, dry_run=True, show_dir=bare / "shows", autosave_path=auto, restore=True)
    try:
        e2.remap_heads()                                  # what a server start does next
        got = e2.patch[0]["map"] if e2.patch else []
        check(got == want, "after a restart the light still has them", f"{got} (was {want})")
        call(e2, "patch_clear")
        call(e2, "load_show", name="Builtin")
        got = e2.patch[0]["map"] if e2.patch else []
        check(got == want, "loading the show brings them back too", f"{got} (was {want})")
    finally:
        e2.shutdown()


SECTIONS = {"empty": sec_empty, "junk": sec_junk, "big": sec_big, "corrupt": sec_corrupt, "undo": sec_undo, "restart": sec_restart}


def main() -> int:
    want = [a for a in sys.argv[1:] if a in SECTIONS] or list(SECTIONS)
    t0 = time.time()
    for name in want:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            setup_db(tmp)
            before = len(fails)
            try:
                SECTIONS[name](tmp)
            except Exception:                     # noqa: BLE001 - the check itself broke
                check(False, f"section {name} crashed", traceback.format_exc()[-600:])
            print(f"  -> {name}: {len(fails) - before} failed")
    print(f"\n{oks} ok, {len(fails)} failed in {time.time() - t0:.1f} s")
    for f in fails[:80]:
        print("  FAIL", f)
    if len(fails) > 80:
        print(f"  ... and {len(fails) - 80} more")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
