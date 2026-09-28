"""The engine's named-FX path, checked through a REAL Engine.

The library checks in `_fx_check` prove the effects are pure and the
capability filter is right.  They cannot prove the engine WIRES them, and
wiring is where the bugs were:

  * `_fx_public` indexed `row["role"]`, which a named row does not have -
    so the console's own 10 Hz snapshot raised KeyError and died the moment
    an operator pressed one of these buttons
  * `_fx_values` assigns one role per head per fx row, so a named effect
    that writes red AND green AND blue would have kept only the last
  * a mixed selection either refused outright or drove heads that cannot
    do the effect, silently

So this drives a real Engine with a synthetic patch - two PARs
(red/green/blue/dimmer) and two movers (pan/tilt/shutter, no dimmer, the
shape a Chauvet Intimidator Spot 260's 8ch mode actually has) - and checks
the whole path: available, start, run, skip, refuse, publish, expire, stop.

SYNTHETIC by construction: the patch is written here, not read from disk.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path


def run(check) -> int:
    import sys

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
    eng = Engine(db_path=tmp / "fx.db", dry_run=True, show_dir=tmp / "shows",
                 autosave_path=tmp / "auto.json", restore=False)

    def head(no, name, roles, kind, y, x, addr, model):
        return {"head_no": no, "name": name, "model": model, "mode": "test",
                "channels": len(roles), "universe": 1, "address": addr,
                "x": x, "y": y, "z": 0, "kind": kind, "role": "generic",
                "curve": "linear", "map": list(roles), "mapped": True}

    # Two PARs with red/green/blue/dimmer, and two movers with pan, tilt
    # and a shutter but NO dimmer - the shape that made "require dimmer"
    # offer the commonest fixture in the rig nothing at all.
    eng.patch = [
        head(1, "PAR 1", ("red", "green", "blue", "dimmer"), "floor", 0, -3, 1,
             "LED PAR"),
        head(2, "PAR 2", ("red", "green", "blue", "dimmer"), "floor", 0, -1.5,
             5, "LED PAR"),
        head(3, "Mover 3",
             ("pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "focus",
              "shutter"), "truss", 6, 0, 9, "Intimidator"),
        head(4, "Mover 4",
             ("pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "focus",
              "shutter"), "truss", 6, 1.5, 17, "Intimidator"),
    ]

    # ---- fx_available: the picker's only source of truth ------------------
    av = eng.act("fx_available", heads=[1, 2, 3, 4])
    ok(av.get("ok", True) and "rainbow" in av["names"],
       "a mixed selection is offered Rainbow, because the PARs can do it")
    ok("circle" in av["names"],
       "...and Circle, because the movers can do that")
    ok("strobe_random" not in av["names"],
       "...and NOT Random strobe, because no fixture here has a strobe "
       "channel - the filter is per capability, not per fixture count")
    ok(av.get("per_head", {}).get("1")
       and "circle" not in av["per_head"]["1"],
       "and it reports PER HEAD, so the picker can say who can do what",
       av.get("per_head", {}).get("1"))
    ok(all(d["name"] in av["names"] for d in av.get("available", [])),
       "describe() and names[] agree, so the list and the buttons cannot "
       "drift apart")

    # ---- a named effect, running -----------------------------------------
    eng.fx = []
    r = eng.act("run_fx", name="rainbow", heads=[1, 2])
    ok(r.get("effect") == "rainbow" and r.get("heads") == 2,
       "Rainbow starts on both PARs", r)
    vals = eng._fx_values()
    ok(set(vals.get(1, {})) >= {"red", "green", "blue"},
       "and writes SEVERAL roles on one head - which the single-role fx "
       "path cannot, and which is the whole reason named effects exist",
       vals.get(1))
    time.sleep(0.25)
    ok(vals.get(1) != eng._fx_values().get(1),
       "and the values MOVE over time")
    ok(all(0 <= x <= 255 for x in eng._fx_values().get(1, {}).values()),
       "and stay inside the 0-255 wire range",
       eng._fx_values().get(1))

    # ---- a MIXED selection ------------------------------------------------
    eng.fx = []
    r = eng.act("run_fx", name="circle", heads=[1, 2, 3, 4])
    ok(r.get("heads") == 2 and sorted(r.get("skipped") or []) == [1, 2],
       "Circle on a mixed selection runs on the movers and SKIPS the PARs",
       r)
    ok("skipped" in (r.get("summary") or ""),
       "and the summary says which were skipped - a silent skip reads as an "
       "effect that did not run")
    vals = eng._fx_values()
    ok("pan" in vals.get(3, {}) and 1 not in vals,
       "the PARs are genuinely absent from the output, not driven silently")
    # Over TIME, not at t=0: a sine at elapsed 0 IS 0, so an immediate
    # assertion only tests that the clock has not started.
    pans = []
    for _ in range(6):
        pans.append(eng._fx_values().get(3, {}).get("pan"))
        time.sleep(0.12)
    ok(max(p for p in pans if p is not None) > 20,
       "and pan reaches a real 0-255 value, not the 0-or-1 the first "
       "version of the effects wrote", pans)
    ok(len(set(pans)) > 2, "and actually sweeps", pans)

    # ---- refused ----------------------------------------------------------
    # `act` RETURNS an error, it does not raise - that is the console API's
    # contract, and a test expecting a raise tests nothing.
    eng.fx = []
    r = eng.act("run_fx", name="circle", heads=[1, 2])
    ok(not r.get("ok", True), "Circle on PARs only is REFUSED", r)
    ok("pan" in str(r.get("error") or ""),
       "and the reason names the channel that is missing, so it is "
       "actionable", r.get("error"))
    r = eng.act("run_fx", name="no_such_effect", heads=[1])
    ok(not r.get("ok", True) and "unknown effect" in str(r.get("error") or ""),
       "an unknown effect name is refused and says so", r)

    # ---- the 10 Hz snapshot -----------------------------------------------
    eng.fx = []
    eng.act("run_fx", name="breathe", heads=[1, 2])
    try:
        pub = eng._fx_public()
        ok(len(pub) == 1 and pub[0].get("lib") == "breathe",
           "the snapshot publishes a NAMED effect without raising - it "
           "indexes row['role'], which a named row does not have", pub)
        ok("label" in pub[0], "and gives it a human label for the UI")
        ok(bool(eng.snapshot().get("fx")),
           "and the whole snapshot survives one running")
    except KeyError as exc:
        ok(False, "the snapshot raises KeyError during a named effect: %s" % exc)

    # ---- expiry -----------------------------------------------------------
    eng.fx = []
    eng.act("run_fx", name="pulse", heads=[1, 2], duration=0.05)
    ok(len(eng.fx) == 1, "a named effect can be given a duration")
    time.sleep(0.2)
    ok(eng._fx_values() == {}, "and expires itself", eng._fx_values())

    # ---- the ORIGINAL single-role path is untouched -----------------------
    eng.fx = []
    r = eng.act("run_fx", attribute="dimmer", wave="sine", speed=2,
                heads=[1, 2])
    ok(bool(r.get("fx")), "the original LFO still starts", r)
    pub = eng._fx_public()
    ok(pub and pub[0].get("role") == "dimmer"
       and pub[0].get("kind") == "sine",
       "and still publishes role and kind", pub)
    ok(set(eng._fx_values().get(1, {})) == {"dimmer"},
       "and still writes exactly one role, so nothing regressed",
       eng._fx_values().get(1))

    # ---- stop -------------------------------------------------------------
    eng.fx = []
    eng.act("run_fx", name="rainbow", heads=[1, 2])
    ok(len(eng.fx) == 1, "a named effect is an ordinary stoppable fx row")
    r = eng.act("stop_fx", id=eng.fx[0]["id"])
    ok(r.get("stopped") == 1 and not eng.fx, "and stop_fx stops it", r)
    return bad
