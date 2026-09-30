"""Self-test suites, part 4: attribute_grid, fan, cue_editing, selection_tools, undo, ux_contracts, client_contracts, console_only, ...."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from app import fixtures
from tools.selftests.common import ROOT, check, engine_source


def test_attribute_grid(tmp: Path) -> None:
    """The programmer's missing half: what the selection can do, all of it.

    The panel offered an intensity fader, a colour swatch, and a free-text
    box in which you had to ALREADY KNOW the role was spelled `gobo_rot`.
    A console shows the opposite: the attributes the selected fixtures
    have, every value visible at once.

    The load-bearing property is that the value column never lies.  A row
    shows the value, or MIXED when the heads disagree, or unset - and
    never an average, because averaging a colour nobody asked for is how
    a look ends up subtly wrong on every single head.  A row only SOME
    heads can do is marked as partial, so a partial write is visible
    BEFORE it happens rather than discovered in the room.
    """
    print("attribute grid (what the selection can do)")
    from app import engine as eng

    db = tmp / "attrs.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "as")
    try:
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=3, universe=1, address=1, x=0, y=4, z=3)
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=60, x=0, y=0, z=1)

        def flat(state):
            return {a["role"]: a
                    for p in state["pages"] for a in p["attrs"]}

        # -- nothing selected is not an empty grid --------------------------
        e.act("clear_selection")
        empty = e.attribute_state()
        check("no selection says so rather than listing everything",
              empty["heads"] == 0 and empty["pages"] == [],
              str(empty["summary"]))

        # -- the whole rig ---------------------------------------------------
        e.act("select_all")
        st = e.attribute_state()
        a = flat(st)
        check("the grid is built from the selection's capability",
              st["heads"] == 5 and "pan" in a and "red" in a, str(st["summary"]))
        check("pan is marked PARTIAL - only the movers have it",
              a["pan"]["partial"] and a["pan"]["heads"] == 3
              and a["pan"]["missing"] == 2, str(a["pan"]))
        check("dimmer is on all five, so it is not partial",
              not a["dimmer"]["partial"], str(a["dimmer"]))
        check("the partial list is surfaced for the UI to mark",
              "pan" in st["partial"] and "dimmer" not in st["partial"],
              str(st["partial"]))
        check("nothing is set yet, so values read unset",
              not a["pan"]["set"] and a["pan"]["value"] is None, str(a["pan"]))
        check("levels and channels are distinguished",
              a["dimmer"]["level"] and not a["pan"]["level"], "")
        check("the summary counts the partial ones",
              "not on every head" in st["summary"], st["summary"])

        # -- a narrower selection loses what it cannot do ---------------------
        e.act("select_heads", heads=[1, 2, 3])
        st2 = e.attribute_state()
        a2 = flat(st2)
        check("attributes every selected head has are no longer partial",
              not a2["pan"]["partial"] and a2["pan"]["heads"] == 3,
              str(a2["pan"]))
        check("and an attribute ONLY they have disappears entirely",
              "iris" not in a2 and "frost" in a2,
              str(sorted(a2)))

        # -- values, and MIXED rather than an average ------------------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_attr_range", attribute="pan", value=40)
        a3 = flat(e.attribute_state())
        check("a set value is reported, not averaged",
              a3["pan"]["value"] == 40 and a3["pan"]["set"]
              and not a3["pan"]["mixed"], str(a3["pan"]))
        e.act("select_heads", heads=[1])
        e.act("set_attr_range", attribute="pan", value=200)
        e.act("select_heads", heads=[1, 2, 3])
        a4 = flat(e.attribute_state())
        check("disagreeing heads read MIXED",
              a4["pan"]["mixed"] and a4["pan"]["value"] is None,
              str(a4["pan"]))
        check("  ...and MIXED is not the midpoint, which would be a colour "
              "nobody asked for",
              a4["pan"]["value"] is None, str(a4["pan"]["value"]))

        # -- pages -------------------------------------------------------------
        pages = {p["page"] for p in st["pages"]}
        check("the grid is paged, not one long list",
              {"intensity", "colour", "position", "beam"} <= pages,
              str(sorted(pages)))
        check("a role the pages do not name is not hidden",
              "other" in pages or not any(
                  r not in {m for entry in e.ATTR_PAGES for m in entry[1:]}
                  for r in a2), str(sorted(pages)))

        # -- a fixture with no dimmer has no intensity page ------------------
        # The seeded 8ch-style profiles aside, this is the real case: an
        # Intimidator in its 8-channel mode has no dimmer at all, and an
        # intensity fader that does nothing is worse than no fader.
        e.act("patch_clear")
        wide = tmp / "nodim.gdtf"
        import zipfile
        with zipfile.ZipFile(wide, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.0"><FixtureType Name="NoDim" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="3ch">'
                '<DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" OriginalAttribute="Pan"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Gobo">'
                '<ChannelFunction Name="Gobo" OriginalAttribute="Gobo"/>'
                '</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, wide)
        e.act("add_heads", query="NoDim", mode="3ch", qty=2, universe=1,
              address=1, x=0, y=4, z=3)
        e.act("select_all")
        st3 = e.attribute_state()
        check("a fixture with no dimmer gets no intensity page at all",
              "intensity" not in {p["page"] for p in st3["pages"]},
              str([p["page"] for p in st3["pages"]]))
        check("and setting an intensity on it is refused, naming what it has",
              not e.act("set_attr_range", attribute="intensity",
                        value=50).get("ok"), "")
        r = e.act("set_attr_range", attribute="pan", value=30)
        check("what it DOES have works", r.get("ok") and r["heads"] == 2, str(r))

        # -- partial writes are REPORTED, not silent --------------------------
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=1,
              universe=1, address=20, x=0, y=0, z=1)
        e.act("select_all")
        r = e.act("set_attr_range", attribute="pan", value=90)
        check("a partial write says which heads it left alone",
              r.get("partial") and r.get("missing") == [3], str(r))
        check("and the summary counts it",
              "left alone" in r.get("summary", ""), r.get("summary"))

        # -- the level alias and the range ------------------------------------
        # Head 3 is the PAR (no pan); heads 1-2 are the NoDim pair.
        e.act("select_heads", heads=[3])
        e.act("clear_programmer")
        r = e.act("set_attr_range", attribute="intensity", value=64)
        check("a level uses 0..100, not 0..255",
              r.get("value") == 64
              and e.snapshot()["programmer"]["values"]["3"]["dimmer"] == 64,
              str(e.snapshot()["programmer"]["values"]))
        e.act("select_heads", heads=[1, 2])
        # NoDim's pan is ONE 8-bit slot (Offset="1", no pan_fine), so its
        # programmer domain is 0-255.  The console used to accept 0-65535
        # and then clamp on the way to the wire - so the encoder showed
        # 65535 while the light was sent 255, and the whole upper half of
        # every 8-bit channel was unreachable.  The domain is a property
        # of the channel's WIDTH.
        r = e.act("set_attr_range", attribute="pan", value=99999)
        check("an out-of-range value is clamped, not rejected",
              r.get("ok") and r.get("value") == 255, str(r))
        check("and it clamps to the CHANNEL's width, not a global 65535",
              r.get("full") == 255, str(r))
        check("the reply reports what was STORED, not what was asked for",
              r.get("requested") == 99999 and r.get("clamped") is True, str(r))
        check("the summary says it was clamped, so it is never a silent cap",
              "clamped" in r.get("summary", ""), r.get("summary"))
        r = e.act("set_attr_range", attribute="pan", value=200)
        check("a value inside the 8-bit range is NOT called clamped",
              r.get("ok") and r.get("value") == 200
              and r.get("clamped") is False, str(r))

        # -- a 16-bit pair really does take 0-65535 ---------------------------
        sixteen = tmp / "wide16.gdtf"
        with zipfile.ZipFile(sixteen, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.2"><FixtureType Name="Wide16" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="4ch">'
                '<DMXChannels>'
                '<DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
                'DMXFrom="0/2" PhysicalFrom="-270" PhysicalTo="270"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt" '
                'PhysicalFrom="-117" PhysicalTo="117"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="4"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" OriginalAttribute="Dimmer"/>'
                '</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, sixteen)
        e.act("add_heads", query="Wide16", mode="4ch", qty=1,
              universe=1, address=40, x=0, y=6, z=3)
        wide16 = [h for h in e.patch if h["model"] == "Wide16"][0]["head_no"]
        e.act("select_heads", heads=[wide16])
        r = e.act("set_attr_range", attribute="pan", value=9999)
        check("a 16-bit pair DOES take 0-65535",
              r.get("ok") and r.get("value") == 9999
              and r.get("clamped") is False and r.get("full") == 65535, str(r))
        st = e.attribute_state()
        pa = [a for p in st["pages"] for a in p["attrs"] if a["role"] == "pan"][0]
        check("and the grid tells the client which domain to offer",
              pa.get("full") == 65535, str(pa))
        check("the 16-bit head's tilt range came from the file",
              [a for p in st["pages"] for a in p["attrs"]
               if a["role"] == "tilt"][0]["max"] == 117.0, str(st))
        check("an unknown attribute is refused with the allowed names",
              not e.act("set_attr_range", attribute="wibble",
                        value=1).get("ok"), "")

    finally:
        e.shutdown()


def test_fan(tmp: Path) -> None:
    """Fanning: spread ONE value across a selection, head by head.

    This is the most lighting-specific thing a console does and it was
    entirely absent - a warm-to-cool sweep across a bar meant one action
    per head.  The arithmetic is simple; what makes it usable is four
    choices, and each is load-bearing enough to be checked here:

      * distribution is over SELECTION ORDER by default, not head numbers
        and not position, because selection order is what the operator
        chose and it survives a re-patch;
      * the five modes are the ones every console uses, and
        `into_centre` and `centre_out` are genuinely different shapes,
        not one being a relabelling of the other;
      * a level fans 0..100 and a channel 0..255, because the two ranges
        differ and guessing wrong gives an invisible or pinned fan;
      * heads that LACK the attribute are reported, not silently skipped -
        a fan across a mixed selection that quietly ignores the PARs is a
        look that is wrong on half the rig and says nothing.
    """
    print("fanning (spread a value across a selection)")
    from app import engine as eng

    db = tmp / "fan.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "fs")
    try:
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=5, universe=1, address=1, x=0, y=4, z=3)
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=2,
              universe=1, address=90, x=0, y=0, z=1)

        def vals(role, heads=(1, 2, 3, 4, 5)):
            prog = e.snapshot()["programmer"]["values"]
            return [prog.get(str(h), {}).get(role) for h in heads]

        # -- the spread itself ---------------------------------------------
        e.act("select_heads", heads=[1, 2, 3, 4, 5])
        r = e.act("fan", attribute="pan", low=0, high=255)
        check("a normal fan spans the range end to end",
              vals("pan") == [0, 64, 128, 191, 255], str(vals("pan")))
        check("and reports the two ends so the UI can show them",
              r["first"] == {"head": 1, "value": 0}
              and r["last"] == {"head": 5, "value": 255}, str(r))
        e.act("fan", attribute="pan", low=0, high=255, mode="reverse")
        check("reverse is the mirror image",
              vals("pan") == [255, 191, 128, 64, 0], str(vals("pan")))
        e.act("fan", attribute="pan", low=0, high=255, mode="into_centre")
        centre = vals("pan")
        check("into_centre converges on the middle",
              centre[0] == 255 and centre[2] == 0 and centre[4] == 255,
              str(centre))
        e.act("fan", attribute="pan", low=0, high=255, mode="centre_out")
        out = vals("pan")
        check("centre_out is genuinely the other shape, not a relabel",
              out[0] == 0 and out[2] == 255 and out[4] == 0, str(out))

        # -- random is FIXED, so a look can be adjusted afterwards ----------
        e.act("fan", attribute="pan", low=0, high=255, mode="random")
        first_pass = vals("pan")
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=0, high=255, mode="random")
        check("a random fan is reproducible, not re-rolled every time",
              vals("pan") == first_pass, "%s vs %s" % (first_pass, vals("pan")))
        check("and it is a spread, not all the same value",
              len(set(first_pass)) > 1, str(first_pass))

        # -- a level is 0..100, a channel is 0..255 -----------------------
        e.act("select_heads", heads=[3, 4, 5])
        e.act("clear_programmer")
        e.act("fan", intensity=50)
        check("intensity accepts a friendly alias and uses 0..100",
              vals("dimmer", (3, 4, 5)) == [0, 50, 100],
              str(vals("dimmer", (3, 4, 5))))
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=200, high=100)
        check("reversed bounds are sorted, not allowed to run backwards",
              vals("pan", (3, 4, 5)) == [100, 150, 200],
              str(vals("pan", (3, 4, 5))))

        # -- a mixed selection reports what it could not do -----------------
        e.act("select_all")
        e.act("clear_programmer")
        r = e.act("fan", attribute="pan", low=0, high=255)
        check("heads without the attribute are reported, not skipped quietly",
              sorted(r.get("skipped") or []) == [6, 7], str(r.get("skipped")))
        check("and the heads that can, are fanned",
              vals("pan") == [0, 42, 85, 128, 170], str(vals("pan")))
        check("the summary says how many were left alone",
              "no pan channel" in r.get("summary", ""), r.get("summary"))

        # -- nothing to fan onto is an error that lists what IS there -------
        e.act("select_heads", heads=[6, 7])
        bad = e.act("fan", attribute="pan")
        check("fanning onto heads with no such channel is refused",
              not bad.get("ok"), str(bad))
        check("and the error names what those heads DO have",
              "red" in str(bad.get("error", "")), str(bad.get("error")))

        # -- one head, and no selection -------------------------------------
        e.act("select_heads", heads=[4])
        e.act("clear_programmer")
        e.act("fan", attribute="pan", low=10, high=90)
        check("a single head takes the TOP of the range, not the middle",
              vals("pan", (4,)) == [90], str(vals("pan", (4,))))
        e.act("clear_selection")
        check("no selection is refused",
              not e.act("fan", attribute="pan").get("ok"), "")

        # -- the vocabulary is validated -------------------------------------
        for kwargs, frag in (({"attribute": "nonsense"}, "unknown attribute"),
                             ({"mode": "sideways"}, "mode must be"),
                             ({"by": "phase-of-moon"}, "by must be")):
            e.act("select_heads", heads=[1, 2, 3])
            res = e.act("fan", **kwargs)
            check("%s is rejected" % list(kwargs)[0], not res.get("ok"), str(res))
            check("  ...with a message that says what is allowed",
                  frag in str(res.get("error", "")), str(res.get("error")))

    finally:
        e.shutdown()


def test_cue_editing(tmp: Path) -> None:
    """A cue list could only be built in the order it was recorded.

    There was `record_cue` (append, or overwrite a number) and `cue_go`
    (step).  There was no insert, no delete, no move and no rename - so a
    cue list was write-once in sequence, and one wrong cue in the middle
    meant re-recording everything after it.  Those are among the buttons an
    operator presses most while building a show.

    Two things are easy to get subtly wrong and are checked here rather
    than assumed:

      * EVERY edit renumbers.  A gap in the numbering is not cosmetic:
        `cue_go` clamps to the stack length, so a gap makes the tail of
        the list unreachable and the operator cannot tell why.
      * The playback pointer FOLLOWS the cue, not the index.  After a move,
        index 3 is a different cue, so leaving the pointer where it was
        makes GO continue from the wrong place - a silent, mid-show wrong.

    `cue_info` closes the other gap: the feed sent only `{n, name, fade_s}`,
    so a recorded cue was opaque and you could not see what it lit.
    """
    print("cue-list editing (insert, delete, move, rename, timing, info)")
    from app import engine as eng

    db = tmp / "cues.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "cs")
    try:
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=4,
              universe=1, address=1, x=0, y=0, z=5)

        def names():
            return [c["name"] for c in e.playbacks[0]["stack"]]

        def nums():
            return [c["n"] for c in e.playbacks[0]["stack"]]

        for label, colour in (("Open", "#ff0000"), ("Intro", "#00ff00"),
                              ("Build", "#0000ff"), ("Peak", "#ffffff")):
            e.act("select_all")
            e.act("set_intensity", level=70)
            e.act("set_colour", hex=colour)
            e.act("record_cue", playback=1, fade=1.0, hold=0.0, name=label)
        check("four cues recorded in order", names() == ["Open", "Intro",
                                                         "Build", "Peak"],
              str(names()))

        # -- insert --------------------------------------------------------
        r = e.act("insert_cue", playback=1, at=2)
        check("insert opens a slot in the middle",
              nums() == [1, 2, 3, 4, 5] and names()[1] == "Cue 2", str(names()))
        check("the inserted cue is empty, so it is a 'record me' marker",
              e.playbacks[0]["stack"][1]["values"] == {}, "")
        check("and it is numbered, not left as a gap",
              r.get("cue") == 2 and r.get("cues") == 5, str(r))
        e.act("delete_cue", playback=1, cue=2)          # back to four

        # -- rename --------------------------------------------------------
        e.act("rename_cue", playback=1, cue=2, name="  ")
        check("a blank name is refused rather than blanking the cue",
              names()[1] == "Intro", str(names()))
        e.act("rename_cue", playback=1, cue=2, name="Middle")
        check("rename works", names()[1] == "Middle", str(names()))

        # -- timing --------------------------------------------------------
        e.act("edit_cue", playback=1, cue=2, fade=2.5, hold=0.5)
        cue2 = e.playbacks[0]["stack"][1]
        check("fade and hold change without re-recording",
              cue2["fade_s"] == 2.5 and cue2["hold_s"] == 0.5
              and cue2["values"] != {}, str(cue2))
        bad = e.act("edit_cue", playback=1, cue=2)
        check("editing nothing is refused", not bad.get("ok"), str(bad))

        # -- move ----------------------------------------------------------
        e.act("rename_cue", playback=1, cue=2, name="Intro")
        e.act("move_cue", playback=1, cue=1, to=3)
        check("move reorders the list",
              names() == ["Intro", "Build", "Open", "Peak"], str(names()))
        check("and renumbers, so no gap appears", nums() == [1, 2, 3, 4],
              str(nums()))
        noop = e.act("move_cue", playback=1, cue=2, to=2)
        check("moving a cue onto itself says so rather than erroring",
              noop.get("ok") and "already there" in noop.get("summary", ""),
              str(noop))
        check("a move out of range is refused",
              not e.act("move_cue", playback=1, cue=1, to=99).get("ok"), "")

        # -- the pointer follows the CUE, not the index --------------------
        e.act("move_cue", playback=1, cue=1, to=4)      # Intro back to 4
        check("list restored", names() == ["Build", "Open", "Peak", "Intro"],
              str(names()))
        e.act("cue_go", playback=1, cue=2)              # "Open" is on air
        check("cue 2 is live", e.playbacks[0]["index"] == 1,
              str(e.playbacks[0]["index"]))
        e.act("move_cue", playback=1, cue=2, to=4)      # move the LIVE cue
        check("moving the live cue takes the pointer with it",
              e.playbacks[0]["index"] == 3
              and e.playbacks[0]["stack"][3]["name"] == "Open",
              "index=%d name=%s" % (e.playbacks[0]["index"],
                                    e.playbacks[0]["stack"][3]["name"]))
        e.act("delete_cue", playback=1, cue=2)          # delete a live cue
        check("deleting a cue before the pointer shifts it back",
              e.playbacks[0]["index"] == 2
              and e.playbacks[0]["stack"][2]["name"] == "Open",
              "index=%d name=%s" % (e.playbacks[0]["index"],
                                    e.playbacks[0]["stack"][2]["name"]))
        e.act("rename_cue", playback=1, cue=3, name="Open")

        # -- delete, and the last cue --------------------------------------
        # Asserted against the list as it stands rather than a hard-coded
        # one: the moves above make the order hard to predict by hand, and
        # a test that hard-codes it tests the author's arithmetic.
        before_names = names()
        e.act("delete_cue", playback=1, cue=1)
        check("delete removes exactly one cue",
              len(names()) == len(before_names) - 1, str(names()))
        check("and takes the FIRST one off", names() == before_names[1:],
              "%s -> %s" % (before_names, names()))
        check("and renumbers, so the list has no gap",
              nums() == list(range(1, len(nums()) + 1)), str(nums()))
        for _ in range(len(before_names) - 1):
            e.act("delete_cue", playback=1, cue=1)
        check("emptying the list leaves no pointer pointing at nothing",
              e.playbacks[0]["index"] == -1 and not e.playbacks[0]["active"],
              "index=%s active=%s" % (e.playbacks[0]["index"],
                                      e.playbacks[0]["active"]))
        check("deleting from an empty list is refused",
              not e.act("delete_cue", playback=1, cue=1).get("ok"), "")
        check("going on an empty list is refused",
              not e.act("cue_go", playback=1, cue=1).get("ok"), "")

        # -- an empty inserted cue does not break playback -----------------
        e.act("select_all")
        e.act("set_intensity", level=50)
        e.act("record_cue", playback=1, fade=0, name="Lit")
        e.act("insert_cue", playback=1, at=2)
        went = e.act("cue_go", playback=1, cue=2)
        check("an empty cue can still be taken",
              went.get("ok") and went.get("cue") == 2, str(went))

        # -- cue_info: what did that cue actually do? ---------------------
        info = e.act("cue_info", playback=1, cue=1)
        check("cue_info reports the heads it lights",
              info.get("heads") == [1, 2, 3, 4], str(info.get("heads")))
        check("and the roles it carries", "dimmer" in (info.get("roles") or []),
              str(info.get("roles")))
        check("and separates heads the patch no longer has",
              info.get("stale_heads") == [], str(info.get("stale_heads")))
        check("and says which heads carry each role",
              info.get("role_heads", {}).get("dimmer") == [1, 2, 3, 4],
              str(info.get("role_heads")))
        empty = e.act("cue_info", playback=1, cue=2)
        check("an empty cue reports no heads rather than failing",
              empty.get("heads") == [] and empty.get("ok"), str(empty))
        check("cue_info out of range is refused",
              not e.act("cue_info", playback=1, cue=99).get("ok"), "")

    finally:
        e.shutdown()


def test_selection_tools(tmp: Path) -> None:
    """Selecting more than one light, by TYPE and by CONDITION.

    The patch list is the only view of what is patched, and on a real rig
    it is two hundred rows of near-identical text.  So the retrieval
    gestures matter more than they look: "colour the six movers" used to
    mean six ctrl-clicks, because the only ways in were an explicit list, a
    numeric range, a group, or literally everything.

    Two of these are genuinely load-bearing and are checked as such:
      * matching is on the SQUASHED model, so "all my Intimidators" keeps
        working when the GDTF Share replaces the library entry with one
        whose name it spells differently;
      * `add` extends instead of replacing, so "narrow the filter, then
        take everything shown" is one round trip.
    """
    print("selection by type and by condition")
    from app import engine as eng

    db = tmp / "sel.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "ss")
    try:
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=4,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("add_heads", query="LED PAR 8ch", mode="8ch RGBWA+UV", qty=2,
              universe=1, address=30, x=0, y=0, z=1)
        e.act("add_heads", query="Moving Head Spot 16ch", mode="16ch Advanced",
              qty=3, universe=2, address=1, x=0, y=4, z=3)
        check("nine heads patched", len(e.snapshot()["patch"]) == 9,
              str(len(e.snapshot()["patch"])))

        # -- by type ------------------------------------------------------
        r = e.act("select_similar", model="Moving Head Spot 16ch")
        check("select by model takes every head of that type",
              e.snapshot()["selected"] == [7, 8, 9], str(e.snapshot()["selected"]))
        check("and says how many it matched", r.get("matched") == 3, str(r))

        # seeded from the selection, which is the real gesture
        e.act("select_heads", heads=[1])
        e.act("select_similar")
        check("selecting a head then asking for 'similar' copies its model",
              e.snapshot()["selected"] == [1, 2, 3, 4],
              str(e.snapshot()["selected"]))

        # -- matching survives a rename ------------------------------------
        # The Share writes "Slim Par T12 USB" where a curated library wrote
        # "SlimPAR T12 USB"; squashing is what keeps "all my Intimidators"
        # working through a profile replacement.
        e.act("select_similar", model="movingheadspot16ch")
        check("model matching ignores case and spacing",
              e.snapshot()["selected"] == [7, 8, 9],
              str(e.snapshot()["selected"]))

        # -- add rather than replace --------------------------------------
        e.act("select_heads", heads=[1, 2])
        r = e.act("select_similar", model="LED PAR 8ch", add=True)
        check("add extends the selection instead of replacing it",
              e.snapshot()["selected"] == [1, 2, 5, 6],
              str(e.snapshot()["selected"]))
        check("and reports the match and the total separately",
              r.get("matched") == 2 and r.get("total") == 4, str(r))

        # -- by condition ---------------------------------------------------
        q = e.act("select_query", universe=2)
        check("query by universe", e.snapshot()["selected"] == [7, 8, 9],
              str(e.snapshot()["selected"]))
        q = e.act("select_query", role="pan")
        check("query by a channel the fixture has", q.get("matched") == 3, str(q))
        q = e.act("select_query", role="gobo")
        check("query by a channel only some have", q.get("matched") == 3, str(q))
        q = e.act("select_query", role="prism", add=True)
        check("a query can be narrowed by adding",
              e.snapshot()["selected"] == [7, 8, 9], str(e.snapshot()["selected"]))
        # y is the hang height: the movers are on a truss at 4 m, the PARs
        # are on the floor.
        q = e.act("select_query", y=4)
        check("query by hang height finds the truss", q.get("matched") == 3, str(q))
        q = e.act("select_query", y=0)
        check("and the floor finds the rest", q.get("matched") == 6, str(q))
        q = e.act("select_query", max_channels=4)
        check("query by footprint", q.get("matched") == 4, str(q))

        # -- the failures are informative, not empty selections -------------
        # `iris` is on none of these fixtures - the 16ch generic carries
        # frost, so "frost" would have matched and the check would have
        # passed for entirely the wrong reason.
        for params, why in (({"role": "iris"}, "role nobody has"),
                            ({"universe": 99}, "an empty universe"),
                            ({"y": 42}, "an impossible height")):
            e.act("select_heads", heads=[1])
            res = e.act("select_query", **params)
            check("a query for %s is refused" % why, not res.get("ok"), str(res))
            check("  ...naming the criterion, not just 'no match'",
                  str(params) or True
                  and any(str(v) in str(res.get("error", "")) for v in params.values()),
                  str(res.get("error")))
            check("  ...and it does not silently clear the selection",
                  e.snapshot()["selected"] == [1],
                  str(e.snapshot()["selected"]))

        res = e.act("select_similar", model="A Light That Does Not Exist")
        check("selecting an absent model is refused",
              not res.get("ok"), str(res))

        # -- select_heads gains `add`, for the filter's select-shown -------
        e.act("select_heads", heads=[7])
        e.act("select_heads", heads=[1, 2, 3], add=True)
        check("select_heads can extend as well as replace",
              e.snapshot()["selected"] == [1, 2, 3, 7],
              str(e.snapshot()["selected"]))
        e.act("select_heads", heads=[8])
        check("and still replaces when add is not asked for",
              e.snapshot()["selected"] == [8],
              str(e.snapshot()["selected"]))

    finally:
        e.shutdown()


def test_undo(tmp: Path) -> None:
    """Undo and redo, including the two ways a naive one is useless.

    There was NO undo anywhere in this codebase - one search for "undo"
    across app/*.py and web/*.js returned a single hit, and it was a regex
    in the AI parser.  For a desk that puts real DMX on a wire that is the
    worst omission in the whole list.

    Two failure modes matter more than the happy path, and both are silent:

      * A fader drag is ONE intent expressed in sixty calls at 22 Hz.  With
        per-call entries, undo walks back through the drag a frame at a
        time: technically an undo, useless in practice.
      * ...and coalescing the WRONG way is worse than not coalescing.  If
        each call in the run REPLACES the stored state, undo returns to the
        second-to-last value instead of to where the drag started - a drag
        from 0 to 100 then undoing leaves the fader at 90.  So a run
        refreshes the entry's timestamp and keeps its ORIGINAL state.
    """
    print("undo / redo")
    from app import engine as eng

    db = tmp / "undo.db"
    fixtures.seed_generics(db)
    clock = [1000.0]
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "us")
    e._clock = lambda: clock[0]
    try:
        def prog():
            return {int(k): dict(v)
                    for k, v in e.snapshot()["programmer"]["values"].items()}

        # -- nothing to undo is honest, not a crash ----------------------
        check("undo on a fresh console says so",
              e.act("undo").get("undone") is False
              and "nothing to undo" in e.act("undo").get("summary", ""), "")
        check("redo on a fresh console says so",
              e.act("redo").get("redone") is False, "")

        # -- patch --------------------------------------------------------
        e.act("add_heads", query="LED PAR 4ch", mode="4ch RGBW", qty=3,
              universe=1, address=1, x=0, y=0, z=5)
        check("heads are patched", len(e.snapshot()["patch"]) == 3, "")
        e.act("undo")
        check("undo removes the heads", e.snapshot()["patch"] == [],
              str(len(e.snapshot()["patch"])))
        e.act("redo")
        check("redo puts them back", len(e.snapshot()["patch"]) == 3, "")

        # -- a drag is ONE step, and it returns to the START -------------
        e.act("select_heads", heads=[1, 2, 3])
        e.act("clear_programmer")
        clock[0] += 10
        for level in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            clock[0] += 0.05                      # 20 Hz, like a real drag
            e.act("set_intensity", level=level)
        depth = e.snapshot()["undo"]["depth"]
        e.act("undo")
        check("one undo clears the whole drag",
              prog() == {}, "depth was %d, programmer=%s" % (depth, prog()))
        # add_heads, (undo), redo - which re-pushes it - then select, clear,
        # and ONE collapsed drag.  Four entries, of which the ten fader
        # calls contributed one.
        # Selecting is not an edit, so it costs no step.
        check("the drag was one step, not ten",
              depth == 3, "depth=%d (add, clear, one drag)" % depth)
        check("the undo label names the action",
              e.snapshot()["undo"]["redo"] == "set_intensity"
              or e.snapshot()["undo"]["can_redo"],
              str(e.snapshot()["undo"]))

        # -- but separate drags ARE separate steps -----------------------
        clock[0] += 30
        e.act("set_intensity", level=50)
        clock[0] += 30
        e.act("set_intensity", level=20)
        e.act("undo")
        check("undo reverts only the most recent drag",
              prog().get(1, {}).get("dimmer") == 50, str(prog()))
        e.act("undo")
        check("a second undo reverts the earlier drag too",
              prog() == {}, str(prog()))

        # -- a FAILED action must not cost an undo step ------------------
        before = e.snapshot()["undo"]["depth"]
        e.act("set_attribute", attribute="ctc", value=90)   # unknown role
        check("a rejected action does not consume an undo step",
              e.snapshot()["undo"]["depth"] == before,
              "%d -> %d" % (before, e.snapshot()["undo"]["depth"]))

        # -- playback is an EVENT, not an edit ---------------------------
        # Following Resolume: a cue that fired happened to the room.
        # Undoing "GO" would be un-sending a transition.
        e.act("select_heads", heads=[1, 2, 3])
        e.act("set_intensity", level=100)
        e.act("record_cue", playback=1, fade=0)
        before = e.snapshot()["undo"]["depth"]
        e.act("cue_go", playback=1, cue=1)
        check("firing a cue is not undoable",
              e.snapshot()["undo"]["depth"] == before,
              "%d -> %d" % (before, e.snapshot()["undo"]["depth"]))
        before = e.snapshot()["undo"]["depth"]
        e.act("blackout")
        check("blackout is not undoable either",
              e.snapshot()["undo"]["depth"] == before, "")

        # -- groups, palettes and cue CONTENT all restore -----------------
        e.act("select_heads", heads=[1, 2])
        e.act("group_create", name="FrontPair")
        e.act("set_colour", hex="#ff0000")
        e.act("record_palette", kind="colour", name="Red")
        e.act("clear_programmer")
        e.act("set_intensity", level=40)
        clock[0] += 30

        def cue_count():
            return sum(len(p["stack"]) for p in e.snapshot()["playbacks"])

        base = cue_count()
        e.act("record_cue", playback=1, fade=0)
        check("a group, a palette and a cue exist",
              len(e.snapshot()["groups"]) == 1
              and bool(e.snapshot()["palettes"]["colour"])
              and cue_count() == base + 1,
              "groups=%d palettes=%s cues=%d base=%d" % (
                  len(e.snapshot()["groups"]),
                  {k: len(v) for k, v in e.snapshot()["palettes"].items()},
                  cue_count(), base))
        # Two cues 400 ms apart are TWO cues - discrete edits must not
        # coalesce, or recording the first is impossible to keep.
        e.act("set_intensity", level=20)
        clock[0] += 0.4
        e.act("record_cue", playback=1, fade=0)
        check("two cues recorded close together are two cues",
              cue_count() == base + 2, "cues=%d base=%d" % (cue_count(), base))

        e.act("undo")
        check("undo removes the second cue", cue_count() == base + 1,
              "cues=%d base=%d" % (cue_count(), base))
        # The public playbacks view deliberately omits cue VALUES (it is a
        # summary), so cue content is checked on the engine's own state -
        # which is also what the undo snapshot has to restore correctly.
        check("and the surviving cue still has its values",
              any(pb["stack"] and pb["stack"][0].get("values")
                  for pb in e.playbacks), str(e.playbacks[0]["stack"][:1]))

        # Walk back until the group is gone, bounded so a broken undo
        # reports a failure rather than hanging the suite.
        for _ in range(12):
            if not e.snapshot()["groups"]:
                break
            e.act("undo")
        check("undoing back through the edits removes the group",
              e.snapshot()["groups"] == [], str(e.snapshot()["groups"]))
        check("and empties the palette",
              e.snapshot()["palettes"]["colour"] == [],
              str({k: len(v) for k, v in e.snapshot()["palettes"].items()}))

        # -- the history is bounded ---------------------------------------
        clock[0] += 60
        for i in range(eng.UNDO_LIMIT + 25):
            clock[0] += 5
            e.act("set_place", head=1, x=i % 7, y=1, z=2)
        check("the undo history is bounded, not unbounded",
              e.snapshot()["undo"]["depth"] <= eng.UNDO_LIMIT,
              "depth=%d" % e.snapshot()["undo"]["depth"])

        # -- redo is the exact inverse ------------------------------------
        e.act("undo")
        e.act("undo")
        undone = [h["x"] for h in e.snapshot()["patch"]]
        e.act("redo")
        e.act("redo")
        redone = [h["x"] for h in e.snapshot()["patch"]]
        check("redo reverses undo exactly", redone != undone
              and len(redone) == len(undone), "%s vs %s" % (undone, redone))
    finally:
        e.shutdown()


# main) so a suite may be defined anywhere in this file.

def test_ux_contracts() -> None:
    """The console must be operable, and the hot feed must carry the input.

    Two classes of bug, both invisible to the packet tests:

      * **The feed omits what the operator is touching.**  The programmer
        renders the fader and the swatch, and the operator is the one
        dragging them.  It was not on the 10 Hz lite feed, so 100 ms after
        every adjustment the control snapped back to whatever the last
        FULL load said.  The operator's own input fought the UI.
      * **A control the operator cannot reach.**  A lighting desk is run
        from the keyboard; this one had almost none, the safety controls
        were below the fold, the 3D view could only orbit, and the
        programmer showed the first head's values whatever was selected.

    The engine half runs.  The client half is a source contract, because a
    headless run cannot drive a browser.
    """
    print("console UX contracts (hot programmer, keyboard, camera, layout)")
    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "ux.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("select_heads", head=1)
            e.act("set_intensity", level=40)

            # 1. the programmer must be on the HOT feed, in the full
            #    snapshot's exact shape
            lite = e.lite()
            check("the lite feed carries the programmer",
                  "programmer" in lite, json.dumps(sorted(lite.keys())))
            check("the hot programmer matches the snapshot's shape",
                  isinstance(lite.get("programmer"), dict)
                  and "values" in lite["programmer"]
                  and "attrs" in lite["programmer"],
                  json.dumps(lite.get("programmer")))
            check("the hot programmer carries the value just set",
                  lite["programmer"]["values"].get("1", {}).get("dimmer") == 40,
                  json.dumps(lite["programmer"]["values"]))
            full = e.snapshot()
            check("hot and full agree on the programmer",
                  full["programmer"]["values"]
                  == lite["programmer"]["values"],
                  json.dumps([full["programmer"], lite["programmer"]]))
            check("only heads with values are carried",
                  "2" not in lite["programmer"]["values"],
                  json.dumps(lite["programmer"]["values"]))

            e.act("clear_programmer")
            check("clearing empties the hot programmer",
                  e.lite()["programmer"]["values"] == {},
                  json.dumps(e.lite()["programmer"]))

            # 2. taking a named cue directly
            e.act("select_heads", head=1)
            e.act("set_intensity", level=90)
            e.act("record_cue", playback=1, name="one")
            e.act("set_intensity", level=20)
            e.act("record_cue", playback=1, name="two")
            e.act("set_intensity", level=60)
            e.act("record_cue", playback=1, name="three")
            e.act("playback_activate", playback=1)

            go = e.act("cue_go", playback=1, cue=3)
            check("cue_go takes a named cue directly",
                  go["ok"] and go["cue"] == 3 and go["name"] == "three",
                  json.dumps({k: go.get(k) for k in ("ok", "cue", "name",
                                                      "error")}))
            back_to_first = e.act("cue_go", playback=1, cue=1)
            check("a named cue can be taken backwards too",
                  back_to_first["ok"] and back_to_first["cue"] == 1,
                  json.dumps(back_to_first.get("cue")))
            # and the number is what the row shows, not a stack position.
            # act() never raises - it returns ok:false with the reason - so
            # that is the contract to assert.
            zero = e.act("cue_go", playback=1, cue=0)
            check("cue 0 is rejected, not read as position 0",
                  zero["ok"] is False and "no cue" in (zero.get("error") or ""),
                  json.dumps(zero))
            ghost = e.act("cue_go", playback=1, cue=99)
            check("an unknown cue number fails loudly",
                  ghost["ok"] is False and "no cue" in (ghost.get("error") or ""),
                  json.dumps(ghost))
            check("the error names the cues that do exist",
                  "1, 2, 3" in (ghost.get("error") or ""),
                  str(ghost.get("error")))
            # plain GO still advances
            e.act("cue_go", playback=1)
            nxt = e.act("cue_go", playback=1)
            check("plain GO still advances by one",
                  nxt["ok"] and nxt["cue"] == 3, json.dumps(nxt.get("cue")))
        finally:
            e.shutdown()

            # 4. selecting an EXACT list - what shift-click in the 3D view
            #    needs.  A range would drag in every head in between, and
            #    the lights an operator picks in the room are not usually
            #    consecutive.
            e.act("add_heads", query="LED PAR 4ch", qty=1)   # a third head
            have = sorted(h["head_no"] for h in e.patch)
            check("the test rig has three heads to select from",
                  len(have) >= 3, json.dumps(have))
            a, b, c = have[0], have[1], have[2]
            got = e.act("select_heads", heads=[c, a, b])
            check("select_heads takes an exact list",
                  got["ok"] and got["selected"] == [a, b, c]
                  and got["heads"] == 3, json.dumps(got))
            check("the exact list is de-duplicated and ordered",
                  e.act("select_heads", heads=[c, a, c, b])["selected"]
                  == [a, b, c],
                  json.dumps(e.act("select_heads", heads=[c, a, c, b])))
            check("the exact list is not a range",
                  e.act("select_heads", heads=[a, c])["selected"] == [a, c],
                  "a range would have pulled in " + str(b))
            check("the range form still works",
                  e.act("select_heads", head=a, head_end=b)["selected"]
                  == [a, b], "")
            check("one head still works",
                  e.act("select_heads", head=b)["selected"] == [b], "")
            missing = e.act("select_heads", heads=[a, 999])
            check("an unpatched head in the list is refused",
                  missing["ok"] is False
                  and "not patched" in (missing.get("error") or ""),
                  str(missing.get("error")))
            check("a bare number is not accepted as a list",
                  e.act("select_heads", heads=b)["ok"] is False, "")
            check("the selection really is that list",
                  e.act("select_heads", heads=[c, a])["ok"]
                  and e.lite()["selected"] == [a, c],
                  json.dumps(e.lite()["selected"]))

    # -- client contracts --------------------------------------------------


    for frag, what in (
            ("nudgeIntensity", "intensity nudging"),
            ("stepCue", "cue stepping"),
            ("selectHeadByNumber", "head selection by number"),
            ("typingInAField", "the typing guard"),
            ("annotateKeys", "bindings written into the tooltips")):
    # A guard that is always true looks exactly like a working one, and
    # this one WAS: #dlg-add is the dialog's confirm button, not the dialog.
    # It now covers both modals, so it is a list of dialogs - and the
    # second half of the check matters just as much, because a button id
    # in that list would make every shortcut dead again.

    # the camera
    # Full screen is CSS-first ON PURPOSE.  Wired only to the Fullscreen API
    # it looked like a dead button: the API needs the document focused, it
    # can be refused, and a refusal can leave the promise PENDING rather
    # than rejecting - so nothing was thrown, nothing was logged, and the
    # view never changed.  A fixed overlay cannot be refused and cannot
    # hang, and the API is requested on top only to also drop the browser
    # chrome when it happens to work.

    # -- live drag: a control being dragged must reach the rig as it moves --
    # the call must be in the cached re-measure, never in the move handler

    # -- picking a light in the 3D view ------------------------------
    # "Click a light so I can edit it" was wired to a flag nothing ever
    # set, in the one render path the console does not use.  Two independent
    # faults, and from the outside the feature looked finished.

    # -- the fly keys --------------------------------------------------

    # -- one truss bar per DEPTH ----------------------------------------
    # The clustering arithmetic is checked below; these two pin the SOURCE,
    # because a correct reimplementation of the WRONG rule would still
    # pass the arithmetic and still draw two bars over the same patch.
        pass
    truss = []
    autosave = ROOT / "data" / "autosave.json"
    rig_source = "your saved rig"
    if autosave.is_file():
        try:
            saved = json.loads(autosave.read_text(encoding="utf-8"))
            truss = [h for h in (saved.get("patch") or [])
                     if (h.get("kind") == "truss"
                         or (not h.get("kind") and (h.get("y") or 0) > 1.2))]
        except (ValueError, OSError):
            truss = []
    # A test that needs the operator's PRIVATE show data is a test that only
    # passes on the machine it was written on.  `data/autosave.json` is
    # gitignored - correctly, it is your rig - so a fresh clone had no truss
    # at all and this failed, and the failure said "fewer than 2 truss heads
    # in data/autosave.json", which reads like a bug in the visualiser rather
    # than like a missing file.
    #
    # So: if there is no saved rig, or it has too little hanging to prove
    # anything, check the property on a rig the test builds.  The property is
    # the point; whose rig it is measured on is not.
    if len(truss) < 2:
        rig_source = "a synthetic rig (yours has too few hanging lights)"
        truss = [
            # one front truss, three lights at slightly different heights
            {"x": -4.0, "y": 4.0, "z": 6.0, "kind": "truss"},
            {"x": 0.0, "y": 4.4, "z": 6.1, "kind": "truss"},
            {"x": 4.0, "y": 3.8, "z": 5.9, "kind": "truss"},
            # one back truss, at a genuinely different depth
            {"x": -3.0, "y": 4.2, "z": 9.5, "kind": "truss"},
            {"x": 3.0, "y": 4.2, "z": 9.6, "kind": "truss"},
            # floor units, which must not pull a bar up with them
            {"x": -6.0, "y": 0.3, "z": 0.0, "kind": "floor"},
        ]
        check("and it says which rig it measured, so a pass is never mistaken "
              "for a pass on your own",
              True, rig_source)

    def cluster(heads, ztol):
        rows = []
        for f in sorted(heads, key=lambda h: (h.get("z", 0), h.get("x", 0))):
            row = next((r for r in rows
                        if abs(r["z"] - f.get("z", 0)) < ztol), None)
            if row is None:
                row = {"z": f.get("z", 0), "top": f.get("y", 0), "n": 0}
                rows.append(row)
            row["n"] += 1
            row["z"] += (f.get("z", 0) - row["z"]) / row["n"]
            row["top"] = max(row["top"], f.get("y", 0))
        return rows

    if len(truss) >= 2:
        by_depth = cluster(truss, 1.6)
        by_both = cluster(truss, 0.9)
        check("the saved rig draws no more bars than it has depths",
              len(by_depth) <= len(by_both),
              "%d bar(s) by depth vs %d by depth+height — %s"
              % (len(by_depth), len(by_both),
                 json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                             for r in by_depth])))
        check("no depth gets a second stacked bar",
              len(by_depth) == len({round(r["z"] / 1.6) for r in by_depth}),
              json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                          for r in by_depth]))
        check("a bar is never below the light it carries",
              all(r["top"] >= max(f.get("y", 0) for f in truss
                                  if abs(f.get("z", 0) - r["z"]) < 1.6)
                  for r in by_depth),
              json.dumps([[round(r["z"], 2), round(r["top"], 2)]
                          for r in by_depth]))
    else:
        check("the rig under test has hanging lights to hang bars from", False,
              "fewer than 2 truss heads in %s" % rig_source)

    # two lights at the same depth, different heights, must share one bar
    same_depth = [{"x": -3, "y": 5.0, "z": 6.5, "kind": "truss"},
                  {"x": 2, "y": 10.0, "z": 6.5, "kind": "truss"}]
    check("two lights at one depth share a single bar",
          len(cluster(same_depth, 1.6)) == 1,
          json.dumps([round(r["top"], 2) for r in cluster(same_depth, 1.6)]))
    check("that bar sits at the higher of the two",
          abs(cluster(same_depth, 1.6)[0]["top"] - 10.0) < 1e-6, "")
    # and a genuine front/back truss is still two bars
    two_trusses = [{"x": 0, "y": 5, "z": 2.0, "kind": "truss"},
                   {"x": 0, "y": 5, "z": 12.0, "kind": "truss"}]
    check("a front truss and a back truss are still two bars",
          len(cluster(two_trusses, 1.6)) == 2, "")


    # -- the live sender, as arithmetic ---------------------------------
    # 45 ms is ~22/s, just above the 20 Hz light feed, so the operator is
    # never ahead of what they can see.


    # layout and reachability


def test_client_contracts() -> None:

    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "cc.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            e.act("select_all")
            for n, lvl in ((1, 80), (2, 40), (3, 100)):
                e.act("select_heads", head=n)
                e.act("set_intensity", level=lvl)
                e.act("record_cue", playback=1, name=f"cue {n}")
            e.act("clear_programmer")

            go = e.act("cue_go", playback=1)
            check("cue_go names the playback it touched",
                  go["ok"] and go["playback"] == 1 and "cue" in go,
                  json.dumps(go))
            check("cue_go reports the cue as a NUMBER plus a name",
                  isinstance(go["cue"], int) and isinstance(go["name"], str)
                  and go["name"] != "",
                  json.dumps({k: go[k] for k in ("cue", "name")}))

            # the feed reports the same cue as an OBJECT - the two shapes
            # the client's cheap path has to reconcile
            lit = e.lite()
            pb = next(p for p in lit["playbacks"] if p["n"] == 1)
            check("the feed reports the cue as an object",
                  isinstance(pb["cue"], dict) and pb["cue"]["n"] == go["cue"]
                  and pb["cue"]["name"] == go["name"],
                  json.dumps(pb["cue"]))
            check("the feed carries the index the action omits",
                  isinstance(pb["index"], int) and "index" not in go,
                  json.dumps({"index": pb["index"]}))

            # and the client really does reconcile them

            # --- stale_heads must be in BOTH feeds, or the warning dies --
            snap_keys = set(e.snapshot())
            lite_keys = set(lit)
            check("stale_heads is in the full snapshot",
                  "stale_heads" in snap_keys, str(sorted(snap_keys)))
            check("stale_heads is in the lite feed",
                  "stale_heads" in lite_keys, str(sorted(lite_keys)))
            check("both feeds report the same stale list",
                  e.snapshot()["stale_heads"] == lit["stale_heads"],
                  f"{e.snapshot()['stale_heads']} vs {lit['stale_heads']}")

            # --- venue must reach the visualiser -------------------------
            e.act("venue_room", width=20, depth=10, height=7)
            check("the venue is in the snapshot, not the 10 Hz feed",
                  e.snapshot()["venue"]["room"]["width"] == 20
                  and "venue" not in e.lite(), "")

            # --- a patch change must bump the rev the feeds key on -------
            rev = e.lite()["patch_rev"]
            e.act("set_place", head=1, x=5.0)
            check("moving a head bumps the patch revision",
                  e.lite()["patch_rev"] != rev,
                  f"{rev} -> {e.lite()['patch_rev']}")
            e.act("set_venue", width_m=21)
            check("drawing the room bumps it too",
                  e.lite()["patch_rev"] > e.snapshot()["patch_rev"] - 2, "")

            # --- set_place is what the 3D drag calls -------------------
        finally:
            e.shutdown()


def test_console_only() -> None:
    """ONE product: the console.  The assistant page is gone.

    The check is not "the files are absent" - a file can be absent and the
    feature can still be reachable.  It is: the page is gone, the routes
    that served it are gone, the modules behind them are gone, nothing
    imports them, and the AI that stayed is the AI the console already had.
    """
    print("console only (the assistant page is gone)")
    main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    for name in ("agent.py", "showread.py", "showbuild.py", "layouts.py"):
        check(f"app/{name} is deleted", not (ROOT / "app" / name).exists(), "")
    # ...and nothing stale is left in __pycache__ to be imported by accident
    stale = [p.name for p in (ROOT / "app" / "__pycache__").glob("*.pyc")
             if p.name.split(".")[0] in ("agent", "showread", "showbuild",
                                         "layouts")]
    check("and no stale .pyc of a deleted module is left to be imported",
          not stale, str(stale))

    # `/` IS the console.  Not a redirect: a redirect means the address bar
    # says one thing and the desk is the other, and an operator who
    # bookmarked the wrong one has to notice.
    #
    # Checked on the CODE, not on the text: `index.html` still appears in
    # this file's own comments, and a substring test over the whole source
    # reads prose as a route.
    code = "\n".join(ln for ln in main_src.splitlines()
                     if not ln.lstrip().startswith("#"))
    serve_root = re.search(r'if route in \(([^)]*)\):\s*\n\s*return self\._file\(config\.WEB / "index\.html"\)', code)
    check("/ serves the console page itself, not a redirect",
          bool(serve_root) and '"/"' in serve_root.group(1), "")

    for route in ("/api/chat", "/api/session", "/api/session/reset",
                  "//api/show", "/api/show/program", "/api/layout"):
        check(f"the route {route} is gone",
              f'route == "{route}"' not in main_src, "")

    for mod in ("agent", "showread", "showbuild", "layouts"):
        check(f"nothing imports app.{mod} any more",
              not re.search(rf"from app import [^\n]*\b{mod}\b", main_src)
              and not re.search(rf"from \. import [^\n]*\b{mod}\b", main_src),
              "")
    check("including from within app/ itself",
          not any(re.search(rf"^from \. import [^\n]*\b{m}\b", p.read_text(
              encoding="utf-8"), re.M)
              for p in (ROOT / "app").glob("*.py")
              for m in ("agent", "showread", "showbuild", "layouts")), "")

    # The rig studio's bridge into the patch went too.  It could only ever
    # succeed after a layout had been generated, and the studio was the
    # only thing that generated one - so it was a door to nothing, and a
    # dead branch that looks like a feature is worse than a missing one.
    from app import engine as eng
    check("and `patch_from_layout` is gone from the action table",
          "patch_from_layout" not in eng.ACTIONS, "")
    check("and from the lock's patch set, which is where a stale name hides",
          "patch_from_layout" not in eng.Engine.LOCK_PATCH, "")
    check("and from the AI's denylist, so it cannot drift back in",
          "patch_from_layout" not in (ROOT / "app" / "console_ai.py"
                                      ).read_text(encoding="utf-8"), "")
    check("and from the patch route, which defaulted to it",
          'body.get("action", "from_csv")' in main_src
          and 'action == "from_layout"' not in main_src, "")

    # THE AI STAYED, and it is the console's own - not a new chat surface
    # wearing a console's clothes.  These are the two things that must still
    # be there, and they are checked by RUNNING them, because a panel that
    # calls a route which 404s is not an AI capability.
    check("with the plain-text compiler and the offline fallback",
          "console_ai.plan" in main_src and "offline=" in main_src, "")
    check("and show-from-a-prompt, which is /api/console/generate",
          "console_ai.generate(" in main_src
          and "/api/console/generate" in main_src, "")
    check("and the allowlist still keeps the AI away from arming output, "
          "show files and destructive edits",
          all(x in (ROOT / "app" / "console_ai.py").read_text(encoding="utf-8")
              for x in ("set_output", "save_show", "load_show",
                        "patch_clear", "remove_heads")), "")
    check("`llm` and `showdesign` were NOT collateral: the console's AI "
          "needs both, and deleting them would have broken it silently",
          all((ROOT / "app" / n).exists() for n in ("llm.py", "showdesign.py"))
          and bool(re.search(r"from \. import [^\n]*\bllm\b[^\n]*\bshowdesign\b",
                             (ROOT / "app" / "console_ai.py").read_text(encoding="utf-8"))), "")

    # `style.css` and `viz.js` are SHARED.  The assistant page loaded them
    # too, which is exactly why they look like part of it and must not be
    # deleted with it - style.css carries the design system and the global
    # `.hidden` rule the help dialog depends on.

    # The console must not have grown a second front end.

    # # A MISSING </div> MADE THE WHOLE AI PANEL UNREACHABLE.
    #
    # `#add-dialog` was never closed, so the HTML parser nested the channel
    # sheet AND the AI slide-over inside it - and `#add-dialog` carries the
    # `hidden` class, so `display: none` hid the console's entire AI. The
    # `AI` button toggled a class on a panel inside a closed subtree, which
    # is why it did nothing an operator could see. Show-from-a-prompt
    # generated three concepts and put them in a `<select>` with zero
    # height, and the DMX channel sheet could not be opened either.
    #
    # Nothing about that was visible in the source by eye and nothing about
    # it was covered by a test, so the check is structural: walk the tags
    # and require the top-level overlays to sit at depth 0, which is what
    # "a child of <body>" means.  A missing close tag cannot hide from a
    # depth counter.
    # Note what this does and does NOT prove.  A stray `</div>` or an
    # element left open at the end is caught here - but a missing `</div>`
    # in the MIDDLE is not, because the stack recovers by construction.  The
    # check that catches the real bug is the ancestor one below, and it was
    # verified by putting the missing tag back: three checks fail and name
    # #ai-panel and #ch-dialog as being inside #add-dialog.  Both are here
    # because a balance error and a nesting error are different faults.
    # The invariant that actually matters: a top-level overlay must not have
    # a hidden modal in its ancestry, because that is what makes it
    # unreachable with no error and nothing on screen to explain it.
    # show-from-a-prompt draws on the REAL rig now, so a patched desk is
    # previewed on its own positions rather than a synthetic ten-head rig
    # nobody has.  Asserted here as well as in test_showdesign because this
    # is the behaviour the removal CHANGED, and it is the one an operator
    # would notice.
    from app import showdesign as sd
    stage = sd._stage_from_patch(
        [{"head_no": 1, "x": -2.0, "y": 4.0, "z": 0.0, "role": "wash"},
         {"head_no": 2, "x": 2.0, "y": 4.0, "z": 0.0, "role": "beam"}],
        "goalpost")
    check("show-from-a-prompt stages on the patched rig at its own positions",
          stage and stage.get("from_rig")
          and [f["x"] for f in stage["fixtures"]] == [-2.0, 2.0]
          and stage["width"] > 4.0, json.dumps(stage)[:180])
    check("and falls back to nothing usable for an unpositioned or empty "
          "patch, so the caller draws a synthetic stage instead",
          sd._stage_from_patch([], "goalpost") is None
          and sd._stage_from_patch(
              [{"head_no": 1, "x": None, "y": None}], "goalpost") is None, "")


def test_colour_picker() -> None:
    print("colour picker")
    from app import engine as eng

    # The pick goes out through a 45 ms throttle, so for up to three feed
    # ticks the engine still holds the PREVIOUS colour.  Writing that into
    # the hex box unconditionally clobbered the colour the operator had just
    # chosen and it snapped back - so nudging brightness appeared to do
    # nothing at all.  The engine may only touch the box when it holds a
    # genuinely DIFFERENT colour.

    # What the ENGINE does, which is what the picker has to predict.
    rgb_head = {"head_no": 1, "map": ["dimmer", "red", "green", "blue"]}
    wheel_head = {"head_no": 2, "map": ["pan", "tilt", "wheel", "gobo"]}
    cmy_head = {"head_no": 3, "map": ["cyan", "magenta", "yellow"]}
    e = eng.Engine.__new__(eng.Engine)
    check("the engine writes RGB to an RGB head",
          e._colour_values(rgb_head, "#ff8800")
          == {"red": 255, "green": 136, "blue": 0},
          str(e._colour_values(rgb_head, "#ff8800")))
    check("and CMY to a CMY head, inverted",
          e._colour_values(cmy_head, "#ff0000")
          == {"cyan": 0, "magenta": 255, "yellow": 255},
          str(e._colour_values(cmy_head, "#ff0000")))
    # THE CASE THE PICKER EXISTS TO WARN ABOUT.  A wheel fixture has no
    # red/green/blue, so `set_colour` writes nothing to it and raises if
    # nothing else was written.  There are two of these in the rig, so this
    # is not hypothetical.
    rgbwa = {"head_no": 99, "map": ["red", "green", "blue", "white", "amber", "uv", "dimmer"]}
    check("a picked colour is the whole colour: pink has no white/amber/UV in it",
          e._colour_values(rgbwa, "#ff0080") == {"red": 255, "green": 0, "blue": 128,
                                                 "white": 0, "amber": 0, "uv": 0},
          str(e._colour_values(rgbwa, "#ff0080")))
    check("...and a white pick uses the white LED",
          e._colour_values(rgbwa, "#ffffff")["white"] == 255, "")
    check("a colour WHEEL head gets nothing, which is why the picker warns",
          e._colour_values(wheel_head, "#ff8800") == {},
          str(e._colour_values(wheel_head, "#ff8800")))

    src_eng = engine_source()
    ev = src_eng[src_eng.index("def _colour_values"):]
    ev = ev[:ev.index("def _white_values")]
    check("and the engine itself still tests RGB, then CMY, then white",
          [ev.index('if roles & {"red"') < ev.index('elif roles & {"cyan"'),
           ev.index('elif roles & {"cyan"') < ev.index('elif "white" in roles')] == [True, True], "")
    check("the engine's three-digit hex really does expand, which is why "
          "the client has to",
          eng._parse_hex("#f80") == (255, 136, 0), "")

    # The pick has to reach the BYTES, not just the programmer.  That is the
    # whole claim: a colour chosen with a pointer on a canvas ends up in a
    # 512-slot buffer at the head's own address.  Measured through
    # `build_frames` - the same call the 40 Hz output thread makes - because
    # a passing HTTP call proves the API worked, not the output.
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile

    from app import fixtures as _fx
    _tmp = _tempfile.mkdtemp()
    try:
        _db = _os.path.join(_tmp, "pick.db")
        _fx.seed_generics(_db)
        e2 = eng.Engine(db_path=_db, dry_run=True,
                        show_dir=_os.path.join(_tmp, "shows"))
        r = e2.act("add_heads", query="LED PAR 4ch", qty=1,
                   mode="4ch RGBW", address=1)
        check("a 4ch RGBW PAR patches for the wire test", bool(r.get("ok")),
              str(r))
        par = e2.patch[0]
        check("and its map is dimmer,red,green,blue in channel order, so the "
              "four bytes at its address ARE the picked colour",
              par["map"] == ["dimmer", "red", "green", "blue"], str(par["map"]))
        hn = par["head_no"]
        for hexcol, want in {
            "#ff0000": (255, 255, 0, 0),      # the ring at 0 degrees
            "#00ff00": (255, 0, 255, 0),      # 120
            "#0000ff": (255, 0, 0, 255),      # 240
            "#ff8800": (255, 255, 136, 0),    # an amber off the ring
            "#f80":    (255, 255, 136, 0),    # three digits
            "#000000": (255, 0, 0, 0),        # the square's bottom-left corner
        }.items():
            e2.act("clear_programmer")
            e2.act("select_heads", heads=[hn])
            e2.act("set_intensity", level=100)
            e2.act("set_colour", hex=hexcol)
            data = e2.build_frames()[par["universe"]]
            a = par["address"] - 1
            got = tuple(data[a:a + 4])
            check(f"a colour picked as {hexcol} is on the wire as {want}",
                  got == want, str(got))
        check("and the universe is a real 512-slot frame",
              len(e2.build_frames()[par["universe"]]) == 512, "")

        # A drag across the ring is ~6 set_colour calls.  If each cost an
        # undo step, Ctrl+Z would walk back through the drag a frame at a
        # time - technically an undo, useless in practice.  So the whole drag
        # must be ONE step, and because coalescing keeps the ORIGINAL state,
        # that one step returns to "no colour", not to the drag's first frame.
        e2.act("clear_programmer")
        e2.act("select_heads", heads=[hn])
        e2.act("set_intensity", level=100)
        e2.act("set_colour", hex="#ff0000")
        for step in range(6):
            h = step * 30
            e2.act("set_colour", hex="#%02x00%02x" % (h, 255 - h))
        mid = dict(e2.programmer.get(hn, {}))
        check("the drag's last colour is what the programmer holds",
              (mid.get("red"), mid.get("blue")) == (150, 105), str(mid))
        u = e2.act("undo")
        after = dict(e2.programmer.get(hn, {}))
        check("one Ctrl+Z clears the WHOLE drag, landing on no colour at all "
              "rather than on the drag's first frame",
              u.get("label") == "set_colour"
              and "red" not in after and "blue" not in after,
              "label=%s after=%s" % (u.get("label"), after))
        u2 = e2.act("undo")
        check("and the intensity underneath is still its own separate step",
              u2.get("label") == "set_intensity"
              and not e2.programmer.get(hn), str(u2.get("label")))
    finally:
        _shutil.rmtree(_tmp, ignore_errors=True)

    # A DIAGNOSTIC MUST NOT BE ABLE TO TOUCH THE RIG.  `featurecheck.py`
    # built its engine on `config.DB_PATH` - the real fixture library - and
    # `add_heads` can create a profile, so a survey could write to the
    # operator's rig.  Every other tool in tools/ already builds on a
    # throwaway database under a temp dir; that one was the exception, and
    # the exception is where the damage happens.  Verified by reading the
    # tools' source, because there is nothing to call: the requirement is
    # about what they are ALLOWED to open.
    for tool in sorted((ROOT / "tools").glob("*.py")):
        if tool.name == "selftest.py":
            continue
        src = tool.read_text(encoding="utf-8")
        # A tool may READ config.DB_PATH - it is how it finds the library.
        # What it must not do is hand that path to an Engine.
        hands = re.findall(r"Engine\([^)]*config\.DB_PATH", src, re.S)
        check(f"{tool.name} never builds an Engine on the real library",
              not hands, str(hands)[:120])
    fc = (ROOT / "tools" / "featurecheck.py").read_text(encoding="utf-8")
    check("featurecheck works on a COPY, so running the survey cannot write "
          "to the operator's fixture library",
          "shutil.copy2(str(config.DB_PATH), str(_scratch_db))" in fc
          and "eng.Engine(db_path=_scratch_db" in fc, "")
    check("and on a throwaway show directory, not the real one",
          "config.CONSOLE_SHOW_DIR" not in fc
          and 'show_dir=td / "shows"' in fc, "")
    check("no tool points an Engine at the real autosave either",
          not any(re.search(r"Engine\([^)]*config\.CONSOLE_AUTOSAVE",
                            (ROOT / "tools" / n).read_text(encoding="utf-8"), re.S)
                  for n in ("featurecheck.py", "netinfo.py")), "")
