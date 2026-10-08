"""The FX library's checks, as an importable function.

A throwaway cannot fail, so this had to become permanent.  It is a module
rather than a block pasted into selftest.py because pasting it in means
re-indenting every line by string surgery, and that is a reliable way to
end up with a test that silently stopped running.

WHAT IT GUARDS
==============

The promise the feature rests on: **an effect is offered only to a fixture
that can do it.**  A picker that lists Circle for a PAR is worse than one
that omits it, because the operator finds out on a rig that nothing
happened.  So the filter is a pure function in `app/fxlib.py`, the engine
and the UI both call it, and it is checked here.

SYNTHETIC, like every other fixture test in this repo: the four capability
sets are copied from the live rig, so nothing here reads gitignored data.
They are deliberately kept in the shape the real modes have - including the
Chauvet Intimidator Spot 260's 8ch mode, which has pan/tilt/wheel/gobo/
prism/focus/shutter and NO dimmer at all, because its shutter is the
dimmer.  A filter that required `dimmer` would offer that fixture nothing,
which is "technically correct" and useless.
"""
from __future__ import annotations

import json

SETS: dict[str, list[str]] = {
    "PAR r/g/b only (#7-12)": ["red", "green", "blue", "raw"],
    "Intimidator 8ch, no dimmer (#5,6)": [
        "pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "focus",
        "shutter"],
    "COLOR STRIKE 29ch (#1-4)": [
        "tilt", "tilt_fine", "frost", "dimmer", "dimmer_fine", "dimmer",
        "dimmer_fine", "strobe", "strobe", "strobe", "strobe", "strobe",
        "strobe", "raw", "raw", "raw", "raw", "raw", "raw", "red", "green",
        "blue", "white", "red", "green", "blue", "white"],
    "Slim Par T12 4ch (#13,14)": [
        "red", "green", "blue", "raw", "strobe", "raw", "dimmer", "unused"],
}

# TWO-STATE effects are two-state on purpose: a square wave has two values,
# a random flicker has on and off.  Demanding three distinct outputs of
# "Alternate" is demanding a third colour it was never going to have.
TWO_STATE = {"alternate", "fan", "pulse", "shutter_flicker", "sparks",
             "strobe_random"}


def run(check) -> int:
    """Run every check.  `check(label, ok, detail)` is the selftest's."""
    from app import fxlib as FX

    bad = 0

    def ok(cond, label, detail=""):
        nonlocal bad
        if not cond:
            bad += 1
        check(label, bool(cond), detail if not cond else "")

    intim = SETS["Intimidator 8ch, no dimmer (#5,6)"]
    par = SETS["PAR r/g/b only (#7-12)"]
    cs = SETS["COLOR STRIKE 29ch (#1-4)"]

    # ---- the filter, on the rig's real capability sets ---------------------
    ok("circle" in FX.available(intim),
       "a head with pan AND tilt is offered Circle")
    ok("circle" not in FX.available(par),
       "a PAR with no pan is NOT offered Circle")
    ok("rainbow" not in FX.available(intim),
       "a head with no colour is NOT offered Rainbow")
    ok("rainbow" in FX.available(par), "an RGB PAR IS offered Rainbow")
    ok("gobo_spin" in FX.available(intim),
       "a head with gobo_rot IS offered Gobo spin")
    ok("gobo_spin" not in FX.available(par), "a PAR is NOT offered Gobo spin")
    ok("shutter_flicker" in FX.available(intim),
       "a head with a shutter IS offered Shutter flicker")
    ok("shutter_flicker" not in FX.available(par),
       "a PAR is NOT offered Shutter flicker")
    ok("strobe_random" not in FX.available(intim),
       "the Intimidator has NO strobe channel, so it is not offered one")
    ok("strobe_random" in FX.available(cs),
       "the 29ch head has a strobe channel and IS offered one")
    ok("breathe" in FX.available(intim),
       "a head with NO dimmer is still offered Breathe, because its shutter "
       "is the dimmer - requiring `dimmer` would offer that fixture nothing")
    ok("breathe" not in FX.available(["raw", "unused"]),
       "a fixture with only raw channels is offered nothing at all")
    ok("tilt" in FX.normalise(cs) and "tilt_fine" not in FX.normalise(cs),
       "a tilt_fine channel folds into the coarse `tilt` role")
    ok("dimmer" in FX.normalise(cs),
       "and dimmer_fine folds into `dimmer`, so a 16-bit head is not "
       "excluded from anything")
    ok(not any("raw" in FX.normalise(r) for r in SETS.values()),
       "`raw` and `unused` never survive normalisation")
    ok(FX.available([]) == [], "an empty role set offers nothing")
    ok(FX.available(None) == [], "a null role set offers nothing, not a crash")

    # ---- `needs` written as a bare string must not silently match nothing --
    # This is the bug that made EVERY beam effect unavailable to EVERY
    # fixture: `needs=("gobo_rot",)` is a string, and _satisfies iterated it
    # character by character.  What has to hold is that _groups() turns
    # both spellings into the same thing - NOT that some fixture satisfies
    # it, which is a different question and was the first version of this
    # check getting it wrong.
    for name, spec in FX.FX.items():
        bare = spec.get("needs") or ()
        as_groups = FX._groups(spec)
        ok(len(as_groups) == len(bare),
           "effect %s keeps one group per declared need" % name,
           "%d groups for %d needs" % (len(as_groups), len(bare)))
        for g, grp in zip(bare, as_groups):
            want = (g,) if isinstance(g, str) else tuple(g)
            ok(grp == want,
               "effect %s reads a need written as %r as a group, not as "
               "%d separate items" % (name, g, len(g) if isinstance(g, str)
                                      else 0),
               "%r vs %r" % (grp, want))

    # ---- every effect is pure, moves, and writes only roles it has -------
    for name in sorted(FX.FX):
        roles = next((r for r in SETS.values() if name in FX.available(r)), None)
        if roles is None:
            # Not a failure: an effect no fixture here supports is the
            # filter working.  Nothing in this rig has a zoom channel.
            check("effect %s is offered to nobody on this rig, because no "
                  "fixture has %s" % (name, FX.why_not(cs, name)),
                  True, "")
            continue
        have = FX.normalise(roles)
        # A SWEEP, not three samples: three points can coincide on a
        # stepped effect and make a working one look static.
        sweep = [FX.apply(name, {}, roles, elapsed=t, index=1, count=4)
                 for t in (0.0, 0.31, 0.62, 0.94, 1.25, 1.57, 1.88, 2.19)]
        distinct = len({json.dumps(s, sort_keys=True) for s in sweep})
        want = 2 if name in TWO_STATE else 3
        ok(distinct >= want,
           "effect %s moves over time (%d distinct values in 2.2 s, "
           "wants %d)" % (name, distinct, want), json.dumps(sweep[0]))
        # "_level" / "_flash" are notes to the engine (it turns them into
        # this light's own open / closed / strobe values), never DMX
        stray = sorted({r for s in sweep for r in s if not r.startswith("_")} - have)
        ok(not stray,
           "effect %s writes only roles the fixture has" % name,
           "wrote %s" % ", ".join(stray))

    # ---- the fan reaches the middle head ---------------------------------
    # An earlier version scaled the whole value by distance from the centre,
    # so the head AT the centre never moved - and the centre was computed
    # with //2, which for four heads is 1, putting two heads at zero.
    for count in (2, 3, 4, 7):
        for idx in range(count):
            a = FX.apply("fan_pan", {}, intim, elapsed=0.0, index=idx,
                         count=count)
            b = FX.apply("fan_pan", {}, intim, elapsed=0.9, index=idx,
                         count=count)
            ok(a != b,
               "fan_pan moves the head at index %d of %d, including the "
               "middle one" % (idx, count),
               "%s vs %s" % (json.dumps(a), json.dumps(b)))

    # ---- an effect a light cannot do is refused, not silently ignored -----
    for name, roles in (("circle", par), ("gobo_spin", par),
                        ("rainbow", intim), ("strobe_random", intim)):
        try:
            FX.apply(name, {}, roles)
            ok(False, "%s is refused on a light that lacks it" % name)
        except ValueError as exc:
            ok(bool(str(exc)),
               "%s is refused on a light that lacks it" % name, str(exc))

    # ---- describe() is the picker's data, and matches available() ---------
    for label, roles in SETS.items():
        got = [d["name"] for d in FX.describe(roles)]
        ok(got == FX.available(roles),
           "describe() and available() agree for the %s" % label,
           "%s vs %s" % (got, FX.available(roles)))
        for d in FX.describe(roles):
            keys = {p["key"] for p in d["params"]}
            ok(d["params"] == [] or keys,
               "effect %s publishes its knobs to the picker" % d["name"])
            # a knob an effect ignores is worse than no knob
            params = dict((k, v) for (k, _l, v, _a, _b)
                          in FX.FX[d["name"]]["params"])
            ok(set(params) == keys,
               "effect %s's published knobs are exactly its declared params"
               % d["name"], "%s vs %s" % (sorted(params), sorted(keys)))
    return bad
