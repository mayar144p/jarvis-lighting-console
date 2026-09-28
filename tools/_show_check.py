"""Per-cue follow, and playback crossfades, through a real Engine.

These are the two show-building features whose contract is easy to state
and easy to break in a way nothing notices:

  follow is a property of the CUE, so a stack can be "wait here, wait
  here, then run by itself" - which is the difference between a list of
  looks and a show.  It was one delay per playback, so every cue in a stack
  behaved identically and the shape was unrepresentable.

  and `follow_s` must actually reach the wire in the public readout, or
  the whole feature is invisible in the browser while working perfectly on
  the DMX side.  `_pb_public` omitted it for a while, which looked like a
  UI problem and was a serialisation one.

Three of the bugs here were found by running the thing rather than reading
it, which is the only reason they are not still in:

  * new cues were written with `follow_s: 0`, and since a cue's own value
    BEATS the stack delay, that made `follow_set(delay=...)` unreachable
    and failed fourteen existing follow tests
  * `insert_cue` accepted a `follow` argument and threw it away
  * the padding cue record_cue creates to open a gap had no follow at all

SYNTHETIC: the patch is written here, nothing is read from disk.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


def run(check) -> int:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.engine import Engine

    bad = 0

    def ok(cond, label, detail=""):
        nonlocal bad
        if not cond:
            bad += 1
        check(label, bool(cond), str(detail) if not cond else "")

    tmp = Path(tempfile.mkdtemp())
    eng = Engine(db_path=tmp / "s.db", dry_run=True, show_dir=tmp / "shows",
                 autosave_path=tmp / "a.json", restore=False)
    eng.patch = [{
        "head_no": 1, "name": "PAR", "model": "LED PAR", "mode": "4ch",
        "channels": 4, "universe": 1, "address": 1, "x": 0, "y": 0, "z": 0,
        "kind": "floor", "role": "par", "curve": "linear",
        "map": ["red", "green", "blue", "dimmer"], "mapped": True}]

    def stack(pb=4):
        r = ((S.lookups if False else None),)
        return eng.snapshot()["playbacks"][pb - 1]

    def cues(pb=4):
        return stack(pb)["stack"]

    # ---- a stack with MIXED follow ----------------------------------------
    eng.act("insert_cue", playback=4, at=1, name="House", fade=3)
    eng.act("insert_cue", playback=4, at=2, name="Build", fade=2, follow=4)
    eng.act("insert_cue", playback=4, at=3, name="Peak", fade=1, follow=0)
    eng.act("insert_cue", playback=4, at=4, name="Out", fade=5)
    cs = cues()
    ok(len(cs) == 4, "four cues in the stack", len(cs))
    ok(cs[0].get("follow_s") is None,
       "a cue told nothing INHERITS - it does not become a hard wait, which "
       "is what made follow_set(delay=...) unreachable", cs[0])
    ok(cs[1].get("follow_s") == 4,
       "a cue given 4s stores 4 - insert_cue once accepted `follow` and "
       "threw it away, so the parameter looked like it worked", cs[1])
    ok(cs[2].get("follow_s") == 0,
       "a cue given 0 stores 0, which means WAIT and is a decision", cs[2])

    # ---- and the stack knows what it is about to do -----------------------
    eng.act("playback_activate", playback=4)
    eng.act("cue_go", playback=4, cue=2)
    f = eng.snapshot()["playbacks"][3]["follow"]
    ok(f.get("cue_follow_s") == 4 and f.get("waits") is False,
       "standing on the auto cue, the stack says 4s and does not wait", f)
    eng.act("cue_go", playback=4, cue=3)
    f = eng.snapshot()["playbacks"][3]["follow"]
    ok(f.get("cue_follow_s") is None and f.get("waits") is True,
       "standing on the wait cue, it says it waits", f)
    ok("on" in f,
       "and the arm flag is still reported separately, because a stack can be "
       "armed and still be sitting on a cue that holds")

    # ---- the follow actually fires, and only where it should --------------
    eng.act("follow_set", playback=4, on=True)
    eng.act("cue_go", playback=4, cue=3)        # the WAIT cue
    pb = eng.playbacks[3]
    ok(pb["follow"]["at"] is None,
       "a cue that waits is never armed, so the ticker cannot move it", pb["follow"])
    eng.act("cue_go", playback=4, cue=2)        # the AUTO cue
    pb = eng.playbacks[3]
    ok(pb["follow"]["at"] is not None,
       "and a cue that names a time is armed, so a mixed stack is a show "
       "rather than a list", pb["follow"])

    # ---- editing a follow, including releasing it -------------------------
    eng.act("edit_cue", playback=4, cue=2, follow=9)
    ok(cues()[1].get("follow_s") == 9, "a follow can be changed after the fact",
       cues()[1])
    eng.act("edit_cue", playback=4, cue=2, follow=None)
    ok(cues()[1].get("follow_s") is None,
       "and RELEASED back to inheriting - a cue pinned to wait that cannot "
       "be released is a decision the operator cannot take back", cues()[1])
    r = eng.act("edit_cue", playback=4, cue=2)
    ok(not r.get("ok", True),
       "an edit with nothing to change is refused, not silently accepted")
    ok("follow" in str(r.get("error") or ""),
       "and the error names the new thing it accepts", r.get("error"))

    # ---- the padding cue record_cue opens a gap --------------------------
    eng.act("select_heads", heads=[1])
    eng.act("set_intensity", level=50)
    eng.act("record_cue", playback=4, cue=6)
    cs = cues()
    ok(len(cs) == 6, "recording cue 6 pads the gap", len(cs))
    # The PADDING cues specifically - positions 5 and 6, opened by the
    # `while` loop.  Filtering by "has no values" is not the same thing:
    # cue 3 is an inserted slot that was never recorded into, and it has an
    # EXPLICIT follow of 0, so it would look like a padding cue that
    # auto-fires.  It is not; it is a cue somebody set to wait.
    #
    # Read the SAVED state, not the public readout.  `_pb_public` publishes
    # `follow_s` as null when the key is absent, because the cue list needs a
    # field to read - which is right for the UI and wrong for this check,
    # since the whole point here is whether the key is there at all.
    saved = Engine._pb_saved(eng.playbacks[3])["stack"]
    pad = saved[4:6]
    ok(len(pad) == 2 and all("follow_s" not in c for c in pad),
       "and both padding cues carry NO follow key at all - neither a hard "
       "stop nor an auto-fire over a slot nobody programmed",
       [c.get("follow_s") for c in pad])
    ok(saved[2].get("follow_s") == 0,
       "while an empty cue that was TOLD to wait still waits, because it "
       "is not padding", saved[2])

    # ---- the public readout carries it ------------------------------------
    pub = eng.snapshot()["playbacks"][3]
    ok("follow_s" in pub["stack"][0],
       "the public readout publishes follow_s, or the whole feature is "
       "invisible in the browser while working on the wire")
    ok(pub["stack"][0]["follow_s"] is None,
       "as null rather than omitted, because null and absent mean "
       "DIFFERENT things to the cue list: inherit versus wait",
       pub["stack"][0])

    # ---- crossfade ---------------------------------------------------------
    eng.act("playback_activate", playback=4)
    eng.act("playback_level", playback=4, level=0)
    t0 = eng._clock()
    r = eng.act("playback_level", playback=4, level=100, xfade=2)
    ok(r.get("xfade_s") == 2, "a crossfade time is accepted and reported", r)
    pb = eng.playbacks[3]
    ok(eng._pb_level_now(pb, t0) < 100,
       "and the level is still on its way, not snapped to the target",
       eng._pb_level_now(pb, t0))
    ok(eng._pb_level_now(pb, t0 + 1.0) > eng._pb_level_now(pb, t0),
       "and it climbs", eng._pb_level_now(pb, t0 + 1.0))
    ok(eng._pb_level_now(pb, t0 + 5.0) == 100,
       "and it arrives", eng._pb_level_now(pb, t0 + 5.0))
    ok(eng.playbacks[3].get("xfade") is None,
       "and stops reporting itself when it is done, rather than interpolating "
       "for ever")
    eng.act("playback_level", playback=4, level=40)
    ok(eng._pb_level_now(eng.playbacks[3], eng._clock()) == 40,
       "and with no crossfade the move is instant, which is what an "
       "operator wants while building")
    # a second change mid-crossfade starts from where it IS, not where it was
    eng.act("playback_level", playback=4, level=0, xfade=2)
    mid = eng._pb_level_now(eng.playbacks[3], eng._clock())
    eng.act("playback_level", playback=4, level=100, xfade=2)
    ok(eng.playbacks[3]["xfade"]["from"] >= mid - 1,
       "a second change mid-crossfade resumes from where the fader IS, not "
       "from the old start, or the fader visibly stutters",
       "%s vs %s" % (eng.playbacks[3]["xfade"]["from"], mid))
    ok(eng.snapshot()["playbacks"][3].get("xfade_s") in (2, 2.0),
       "and the crossfade time is published, so the UI can show it",
       eng.snapshot()["playbacks"][3].get("xfade_s"))
    return bad
