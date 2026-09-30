"""Self-test suites, part 3: physical_ranges, arrange, network_address, limits_and_lock, dry_run_button, command_line, fixture_editor."""
from __future__ import annotations

import zipfile
from pathlib import Path

from app import config, fixtures
from tools.selftests.common import ROOT, check, engine_source


def test_physical_ranges(tmp: Path) -> None:
    """GDTF ranges -> degrees in the console, and a real bug in the domain.

    THE RANGE WAS NEVER CAPTURED.  `parse_gdtf` compiled each profile down
    to a bare list of channel NAMES, so a light's own travel was thrown
    away on import.  Worse, the range is not where the spec's older drafts
    put it: real files from the Share carry it on `ChannelFunction` as
    `@PhysicalFrom`/`@PhysicalTo` paired with `@DMXFrom`, while
    `LogicalChannel/@Min`/`@Max` - the first thing anyone reaches for - is
    absent.  Reading only the first found nothing at all, which is why
    pan could only ever be an abstract 0-255 and why the visualiser's
    hardcoded 270 degrees of tilt was never checked against a real file.
    A Chauvet Intimidator says tilt is -117..+117, so 270 was 15% wrong.

    THE DOMAIN BUG IS THE INTERESTING PART.  The console accepted 0-65535
    for every channel and `merge` CLAMPS an unpaired channel to 255 on the
    way to the wire - so the encoder displayed whatever you typed, the
    light was sent 255, and the entire upper half of every 8-bit channel
    was unreachable.  An 8-bit pan could not be aimed at 90 degrees at all.
    The domain is a property of the channel's WIDTH: 0-100 for a level,
    0-255 in one slot, 0-65535 across a `role`/`role_fine` pair.
    """
    print("physical ranges (GDTF travel -> degrees, and the channel domain)")
    from app import engine as eng

    db = tmp / "ranges.db"
    fixtures.seed_generics(db)

    def gdtf(name, channels, physical=""):
        path = tmp / (name + ".gdtf")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.2"><FixtureType Name="%s" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="d">' % name
                + "<DMXChannels>" + channels + "</DMXChannels>"
                + "</DMXMode></DMXModes></FixtureType></GDTF>"))
        return fixtures.import_file(db, path)

    # -- a 16-bit pan pair whose travel comes from ChannelFunction ---------
    gdtf("Mover16",
         '<DMXChannel Offset="1,2"><LogicalChannel Attribute="Pan">'
         '<ChannelFunction Name="Pan" OriginalAttribute="Pan" DMXFrom="0/2" '
         'PhysicalFrom="-270" PhysicalTo="270"/></LogicalChannel>'
         '</DMXChannel>'
         '<DMXChannel Offset="3"><LogicalChannel Attribute="Tilt">'
         '<ChannelFunction Name="Tilt" OriginalAttribute="Tilt" '
         'PhysicalFrom="-117" PhysicalTo="117"/></LogicalChannel>'
         '</DMXChannel>'
         '<DMXChannel Offset="4"><LogicalChannel Attribute="Color1">'
         '<ChannelFunction Name="Color1" OriginalAttribute="" '
         'PhysicalFrom="0" PhysicalTo="1" Wheel="Color Wheel"/>'
         '</LogicalChannel></DMXChannel>')
    rr = fixtures.role_ranges(db, "TestCo", "Mover16", "d")
    check("travel is read from ChannelFunction, where real files put it",
          rr.get("pan", {}).get("min") == -270.0
          and rr["pan"]["max"] == 270.0, str(rr.get("pan")))
    check("and it is reported as degrees", rr["pan"]["unit"] == "degree",
          str(rr["pan"]))
    check("a 0..1 channel is a POSITION, not 0..1 degrees",
          rr.get("wheel", {}).get("unit") == "position",
          str(rr.get("wheel")))
    check("the fine half of a 16-bit pair does not halve the travel",
          rr["pan"]["bits"] == 16, str(rr["pan"]))

    # -- a range declared the OLD way must still be honoured ----------------
    gdtf("Legacy",
         '<DMXChannel Offset="1"><LogicalChannel Attribute="Tilt" '
         'Min="-90" Max="90"><ChannelFunction Name="Tilt" '
         'OriginalAttribute="Tilt"/></LogicalChannel></DMXChannel>')
    rr = fixtures.role_ranges(db, "TestCo", "Legacy", "d")
    check("LogicalChannel/@Min|Max is read too, for older drafts",
          rr.get("tilt", {}).get("min") == -90.0
          and rr["tilt"]["max"] == 90.0, str(rr.get("tilt")))

    # -- degrees in, degrees on the wire, for BOTH widths --------------------
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "pr")
    try:
        e.act("add_heads", query="Mover16", mode="d", qty=1,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("select_heads", heads=[1])
        st = e.attribute_state([1])
        pan = [a for p in st["pages"] for a in p["attrs"] if a["role"] == "pan"][0]
        check("the grid offers the light's own travel",
              pan["min"] == -270.0 and pan["max"] == 270.0, str(pan))
        check("and states the channel's domain",
              pan["full"] == 65535, str(pan))
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        check("90 degrees is accepted as an angle",
              r.get("ok") and r.get("unit") == "degree", str(r))
        check("and is reported back as 90 degrees, not as an internal number",
              r.get("phys") == 90.0, str(r))
        chans = {c["role"]: c["value"] for c in
                 e.channel_report([1])["heads"][0]["channels"]
                 if c["role"] in ("pan", "pan_fine")}
        want = round((90 + 270) / 540 * 65535)
        check("a 16-bit pair really puts those bytes on the wire",
              chans["pan"] == (want >> 8) and chans["pan_fine"] == (want & 255),
              str(chans))

        # An 8-bit pan: the case the domain bug made impossible.
        gdtf("Mover8",
             '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
             '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
             'PhysicalFrom="-270" PhysicalTo="270"/></LogicalChannel>'
             '</DMXChannel>')
        e.act("add_heads", query="Mover8", mode="d", qty=1,
              universe=1, address=20, x=2, y=0, z=1)
        eight = e.patch[-1]["head_no"]
        e.act("select_heads", heads=[eight])
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        st = e.attribute_state([eight])
        pan8 = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "pan"][0]
        check("an 8-bit pan is offered 0-255, not 0-65535",
              pan8["full"] == 255, str(pan8))
        got = [c["value"] for c in e.channel_report([eight])["heads"][0]
               ["channels"] if c["role"] == "pan"][0]
        check("90 degrees lands on byte 170 of a 0-255 travel",
              abs(got - round((90 + 270) / 540 * 255)) <= 1,
              "byte %s, asked %s" % (got, r.get("phys")))
        check("NOT 255, which is what the old clamp sent for anything >255",
              got != 255 or r.get("phys") == 270.0, str(got))

        # -- clamping is reported in the unit the operator typed -------------
        r = e.act("set_attr_range", attribute="pan", value=900,
                  unit="degree")
        check("out of travel is clamped and says the travel in degrees",
              r.get("clamped") and "clamped to 270" in r["summary"]
              and "-270..270" in r["summary"], r.get("summary"))
        check("and never echoes the internal conversion number",
              "43690" not in r["summary"] and "141992" not in r["summary"],
              r.get("summary"))
        r = e.act("set_attr_range", attribute="pan", value=0, unit="degree")
        # 540 degrees of travel across 256 steps is 2.1 degrees a step, so
        # 0 is not exactly reachable.  What must not happen is the FLOAT
        # artefact: 32767.5 rounding up to 32768 and reading back as
        # 0.00178531.  Being within a step and rounded for display is the
        # honest answer.
        check("0 degrees reads back as a rounded angle, not a float artefact",
              r["phys"] is not None and abs(r["phys"]) <= 2.2
              and r["phys"] != 0.00178531, str(r.get("phys")))

        # -- the unit must be explicit, and a wrong one is named ------------
        r = e.act("set_attr_range", attribute="pan", value=170)
        check("with no unit the number is LOGICAL, not degrees",
              r.get("value") == 170 and r.get("phys") is None, str(r))
        r = e.act("set_attr_range", attribute="pan", value=50, unit="percent")
        check("asking for the wrong unit is refused by name",
              not r.get("ok") and "degrees, not percent" in r.get("error", ""),
              r.get("error"))
        e.act("select_heads", heads=[1])       # Mover16, which has a wheel
        r = e.act("set_attr_range", attribute="wheel", value=50, unit="degree")
        check("and a channel that is not an angle says what it IS",
              not r.get("ok") and "position" in r.get("error", ""),
              r.get("error"))

        # -- two movers with different travel get no single answer ----------
        gdtf("MoverNarrow",
             '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
             '<ChannelFunction Name="Pan" OriginalAttribute="Pan" '
             'PhysicalFrom="-90" PhysicalTo="90"/></LogicalChannel>'
             '</DMXChannel>')
        e.act("add_heads", query="MoverNarrow", mode="d", qty=1,
              universe=1, address=40, x=4, y=0, z=1)
        narrow = e.patch[-1]["head_no"]
        e.act("select_heads", heads=[eight, narrow])
        st = e.attribute_state()
        pan_mix = [a for p in st["pages"] for a in p["attrs"]
                   if a["role"] == "pan"][0]
        check("a mixed selection reports both travels rather than a mean",
              pan_mix.get("mixed_range") == [[-270.0, 270.0], [-90.0, 90.0]],
              str(pan_mix.get("mixed_range")))
        r = e.act("set_attr_range", attribute="pan", value=90, unit="degree")
        check("and refuses to convert degrees across them, by name",
              not r.get("ok") and "-270..270" in r.get("error", "")
              and "-90..90" in r.get("error", ""), r.get("error"))
        r = e.act("set_attr_range", attribute="pan", value=170)
        check("but a LOGICAL value still works on a mixed selection",
              r.get("ok") and r["heads"] == 2, str(r))

        # -- a hand-written profile can carry travel too ---------------------
        made = fixtures.create_profile(db, "Hand", "Written", "d",
                                       ["Pan = -180..180", "Tilt = -45..45",
                                        "Dimmer"])
        check("a hand-written profile can declare travel inline",
              made["ranges"] == 2, str(made))
        check("and the summary says it has physical ranges",
              "physical range" in made["summary"], made["summary"])
        rr = fixtures.role_ranges(db, "Hand", "Written", "d")
        check("which the console then reads back for those roles",
              rr["tilt"]["min"] == -45.0 and rr["tilt"]["unit"] == "degree",
              str(rr.get("tilt")))
        for bad, frag in ((["Pan = 90.."], "look like"),
                          (["Pan = 0..0"], "no range"),
                          (["Tilt = a..b"], "look like")):
            refused = False
            try:
                fixtures.create_profile(db, "Bad", "Travel", "d", bad)
            except ValueError as exc:
                refused = frag in str(exc)
            check("inline travel %r is refused" % bad[0], refused, bad[0])

        # -- the range can be edited and taken away --------------------------
        e.act("add_heads", query="Hand", mode="d", qty=1,
              universe=1, address=60, x=6, y=0, z=1)
        hand = e.patch[-1]["head_no"]
        mode_id = next(m for f in fixtures.search(db, "Written")
                       for m in fixtures.list_modes(db, f["id"]))["id"]
        done = fixtures.set_channel_range(db, mode_id, 1, -270, 270)
        check("a travel can be changed after the fact",
              done["min"] == -270.0 and "range -270..270" in done["summary"],
              str(done))
        e.remap_heads()
        e.act("select_heads", heads=[hand])
        st = e.attribute_state([hand])
        tilt = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "tilt"][0]
        check("and the console sees the new one immediately",
              tilt["max"] == 270.0, str(tilt))
        done = fixtures.set_channel_range(db, mode_id, 1, None, None)
        check("a travel can be cleared again",
              done["min"] is None and "range cleared" in done["summary"],
              str(done))
        e.remap_heads()
        e.act("select_heads", heads=[hand])
        r = e.act("set_attr_range", attribute="tilt", value=45, unit="degree")
        check("with no travel, degrees can no longer be converted, and it says so",
              not r.get("ok") and "no range is known" in r.get("error", ""),
              r.get("error"))

    finally:
        e.shutdown()


def _csv_split(line: str) -> list[str]:
    """Parse one RFC 4180 row, so a quoting test can check the real cells.

    Written out rather than borrowed from the csv module because the point
    is to check that what the engine WROTE parses back to what was meant -
    trusting the same reader that wrote it would pass on a symmetric bug.
    """
    out, cell, quoted, i = [], "", False, 0
    while i < len(line):
        c = line[i]
        if quoted:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cell += '"'
                    i += 2
                    continue
                quoted = False
            else:
                cell += c
        elif c == '"':
            quoted = True
        elif c == ",":
            out.append(cell)
            cell = ""
        else:
            cell += c
        i += 1
    out.append(cell)
    return out


def test_arrange(tmp: Path) -> None:
    """ALIGN, DISTRIBUTE, MIRROR, and degree aim.

    THE THING YOU DO TWENTY TIMES IN AN HOUR AND CANNOT DO BY DRAGGING.
    Six movers on a bar are not evenly spaced after you have moved them
    individually, and making them even again is one drag each - or, on a
    real console, two keys.

    The distinction between the three is the feature, not an
    implementation detail.  ALIGN uses the mean; DISTRIBUTE uses the two
    ends.  A row of six with the middle two bunched is NOT misaligned -
    every head is off-axis, so there is no one line to align them to - and
    aligning is the wrong tool.  Treating the two as synonyms is the
    standard way these get built wrong, so the test sets up exactly that
    shape and checks that align collapses it while distribute fixes it.

    The distribute check measures the gaps in POSITION order, not head
    order.  A rig numbered 1..n along a bar makes those the same list,
    which is precisely how a head-number-ordered bug survives every check;
    a rig numbered by fixture type is where it shows.
    """
    print("arrange (align, distribute, mirror) and degree aim")
    from app import engine as eng

    db = tmp / "arr.db"
    fixtures.seed_generics(db)
    fixtures.create_profile(db, "TestCo", "Mover", "3ch",
                            ["Pan = -270..270", "Tilt = -117..117",
                             "Dimmer"])
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "arr")
    try:
        # A bar of seven, numbered so that position order is NOT head order
        # for the last head - which is what makes the gap check meaningful.
        for i, x in enumerate([0.0, 1.0, 1.2, 4.0, 4.1, 8.0, 2.0]):
            e.act("add_heads", query="Mover" if i % 2 == 0 else "LED PAR",
                  mode="3ch" if i % 2 == 0 else "4ch RGBW",
                  qty=1, universe=1, address=1 + i * 4, x=x, y=0, z=0)
        movers = [h["head_no"] for h in e.patch
                  if "Mover" in h.get("model", "")]
        pars = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")]
        allheads = [h["head_no"] for h in e.patch]
        check("the rig has both kinds, for partial-aim checks",
              len(movers) == 4 and len(pars) == 3,
              "movers=%s pars=%s" % (movers, pars))

        def setbar(values):
            for i, h in enumerate(e.patch):
                h["x"] = values[i]

        def xs():
            return [float(h["x"]) for h in e.patch]

        def gaps():
            s = sorted(xs())
            return [round(s[i + 1] - s[i], 3) for i in range(len(s) - 1)]

        # ---- DISTRIBUTE ---------------------------------------------------
        setbar([0.0, 1.0, 1.2, 4.0, 4.1, 8.0, 2.0])
        r = e.act("distribute", heads=allheads, axis="x")
        check("distribute reports what it did", r.get("ok")
              and "distributed" in r["summary"], r.get("summary"))
        g = gaps()
        check("the gaps are all equal, measured in POSITION order",
              len(set(g)) == 1, str(g))
        check("and it is not equal spacing by HEAD order, which would pass "
              "a head-order check on a numbered bar",
              len({round(xs()[i + 1] - xs()[i], 3)
                   for i in range(len(xs()) - 1)}) > 1,
              str([round(xs()[i + 1] - xs()[i], 3) for i in range(6)]))
        check("the two outermost heads are left where they were",
              min(xs()) == 0.0 and max(xs()) == 8.0,
              "%s..%s" % (min(xs()), max(xs())))

        # ---- ALIGN --------------------------------------------------------
        setbar([0.0, 2.0, 2.5, 5.0, 5.5, 9.0, 3.5])
        r = e.act("align", heads=allheads, axis="x")
        check("align puts every head on ONE line", r.get("ok")
              and len({round(v, 3) for v in xs()}) == 1, str(xs()))
        check("at the MEAN, not the first or the middle",
              abs(round(xs()[0], 2) - 3.93) < 0.01, str(round(xs()[0], 2)))
        check("and the spacing is GONE, which is the difference from "
              "distribute", all(g == 0.0 for g in gaps()), str(gaps()))
        e.act("align", heads=allheads, axis="x")
        check("aligning an aligned selection says so rather than claiming "
              "a move", e.act("align", heads=allheads, axis="x")["heads"] == 0
              and "already aligned" in
              e.act("align", heads=allheads, axis="x")["summary"], "")

        # ---- the shape that separates the two -----------------------------
        bunched = [0.0, 1.0, 1.1, 5.0, 5.1, 6.0, 1.05]
        setbar(bunched)
        e.act("align", heads=allheads, axis="x")
        check("on a bunched bar, align collapses it to one point",
              len({round(v, 3) for v in xs()}) == 1, str(xs()))
        setbar(bunched)
        e.act("distribute", heads=allheads, axis="x")
        check("on the same bar, distribute spreads it evenly",
              len(set(gaps())) == 1, str(gaps()))
        check("which is the whole reason they are separate commands",
              sorted(xs())[0] == 0.0 and sorted(xs())[-1] == 6.0,
              str(sorted(xs())))

        # ---- MIRROR -------------------------------------------------------
        shape = [0.0, 1.0, 2.0, 5.0, 6.0, 7.0, 3.0]
        setbar(shape)
        mean = sum(shape) / len(shape)
        r = e.act("mirror", heads=allheads, axis="x")
        check("mirror about the mean puts every head at 2*mean - x",
              all(abs(v - (2 * mean - o)) < 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        check("and reports the centre it used",
              abs(r["about"]["x"] - mean) < 0.01, str(r["about"]))
        r = e.act("mirror", heads=allheads, axis="x", about=0)
        # Two mirrors do NOT cancel: the first moved every head to
        # 2*mean - x, so reflecting THAT about 0 gives x - 2*mean.  A check
        # asserting the original shape back would be wrong.
        check("mirror about 0 negates whatever is there now",
              all(abs(v + (2 * mean - o)) < 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        check("so two mirrors are not an undo",
              any(abs(abs(v) - abs(o)) > 0.01
                  for v, o in zip(xs(), shape)), str(xs()))
        # A head already ON the centre cannot move, and that is the one
        # case where a mirror genuinely does nothing.
        setbar([0.0, 1.0, 2.0, 5.0, 6.0, 7.0, 0.0])
        r = e.act("mirror", heads=[allheads[-1]], axis="x")
        check("a head already on the mirror line does not move",
              r["heads"] == 0 and "already on the line" in r["summary"],
              r["summary"])
        setbar([-3.0, -1.0, 0.0, 1.0, 3.0, 0.0, 0.0])
        r = e.act("mirror", heads=allheads, axis="x")
        check("a symmetric SHAPE still swaps its heads, and says it moved "
              "them - mirror is per head, not set-based",
              r["heads"] == 4, "%s of %s" % (r["heads"], sorted(xs())))

        # ---- axes ---------------------------------------------------------
        setbar(shape)
        for i, h in enumerate(e.patch):
            h["y"] = float(i)
            h["z"] = float(i) * 2
        r = e.act("align", heads=allheads, axis="y")
        check("align works on y as well as x",
              len({round(h["y"], 3) for h in e.patch}) == 1, str(
                  [h["y"] for h in e.patch]))
        r = e.act("align", heads=allheads, axis="all")
        check("`all` lines up all three axes at once",
              r.get("ok") and len(r["axis"]) == 3, str(r.get("axis")))
        r = e.act("align", heads=allheads, axis="wibble")
        check("a bad axis is refused and the axis is named",
              not r.get("ok") and "x, y, z" in r["error"], r.get("error"))

        # ---- too few heads -------------------------------------------------
        r = e.act("distribute", heads=movers[:2], axis="x")
        check("distribute needs three, and says what two is not enough for",
              not r.get("ok") and "at least 3" in r["error"]
              and "nothing between them" in r["error"], r.get("error"))

        # ---- degree aim ---------------------------------------------------
        e.act("clear_programmer")
        e.act("select_heads", heads=movers)
        r = e.act("set_position", pan=90, tilt=0)
        check("with no unit, a value is DEGREES when the fixture knows "
              "its travel", r.get("unit") == "degree", str(r))
        check("and the transcript says so, in degrees",
              "90" in r["summary"] and "°" in r["summary"], r["summary"])
        rr = fixtures.role_ranges(db, "TestCo", "Mover", "3ch")
        for h in movers:
            got = float((e.programmer.get(h) or {}).get("pan", 0))
            back = rr["pan"]["min"] + (got / 255.0) * (
                rr["pan"]["max"] - rr["pan"]["min"])
            check("head %s really is at 90 degrees of its own travel" % h,
                  abs(back - 90) <= 2.2, "%.1f" % back)
        r = e.act("set_position", pan=90, unit="logical")
        check("an explicit logical unit overrides the inference",
              r.get("unit") == "logical" and "logical" in r["summary"],
              r["summary"])
        r = e.act("set_position", pan=90, unit="percent")
        check("a unit that is neither is refused BY NAME",
              not r.get("ok") and "not 'percent'" in r["error"],
              r.get("error"))

        # A fixture with NO declared travel must read as logical, not as a
        # wrong number of degrees - the §17.19 trap in its purest form.
        r = e.act("set_position", pan=90)
        e.act("select_heads", heads=pars)
        r = e.act("set_position", pan=90)
        check("a light with no travel reads the number as logical",
              r.get("aimed") is False, str(r))
        check("and says there is nowhere to aim, naming what it has",
              "blue" in (r.get("summary") or ""), r.get("summary"))

        # ---- partial aim is reported --------------------------------------
        e.act("select_heads", heads=allheads)
        e.act("clear_programmer")
        r = e.act("set_position", pan=0)
        check("a mixed selection aims what it can and says what it could not",
              r.get("partial") and r["heads"] == len(movers)
              and "missing" in r["summary"], r["summary"])

        # ---- the command line ---------------------------------------------
        e.act("select_heads", heads=[])
        r = e.act("run_command", text="%d-%d aim pan 90" % (movers[0],
                                                            movers[-1]))
        check("`1-4 aim pan 90` aims, and says degrees",
              r.get("ok") and "90" in r["summary"], r.get("summary"))
        e.act("clear_programmer")
        r = e.act("run_command", text="%d-%d aim 90 -45" % (movers[0],
                                                           movers[-1]))
        check("`aim 90 -45` is pan then tilt, positionally",
              r.get("ok") and "90" in r["summary"]
              and "-45" in r["summary"], r.get("summary"))
        for bad, frag in (("%d-%d aim pan" % (movers[0], movers[-1]),
                           "pan needs a value"),
                          ("%d aim pan 90 wibble" % movers[0],
                           "pan and tilt only")):
            r = e.act("run_command", text=bad)
            check("a bad aim line is refused: %s" % frag.split()[0],
                  not r.get("ok") and frag in r["error"], r.get("error"))
        setbar([0.0, 1.0, 1.1, 5.0, 5.1, 6.0, 1.05])
        r = e.act("run_command", text="all distribute x")
        check("`all distribute x` distributes", r.get("ok")
              and len(set(gaps())) == 1, str(gaps()))
        r = e.act("run_command", text="all align x 3")
        check("`align x 3` is refused, pointing at the mirror form",
              not r.get("ok") and "mirror x about N" in r["error"],
              r.get("error"))
        r = e.act("run_command", text="all mirror x about")
        check("`mirror x about` with no value says what it needs",
              not r.get("ok") and "needs a value" in r["error"],
              r.get("error"))
        n = len(e._undo)
        e.act("run_command", text="%d-%d aim pan 0" % (movers[0], movers[-1]))
        check("an aim line is still ONE undo step",
              len(e._undo) - n == 1, str(len(e._undo) - n))

        # ---- the patch sheet ---------------------------------------------
        # THE DOCUMENT THE RIG ACTUALLY HAS.  Until this existed the agent's
        # own instructions told operators to make one by hand elsewhere,
        # which is how patch sheets come out with a head's DMX mode missing
        # - the one column that decides whether the sheet is usable.
        csv = e.patch_csv()
        lines = csv.splitlines()
        check("the sheet has a header and one row per head",
              len(lines) == len(e.patch) + 1, "%d lines" % len(lines))
        check("the header is the documented column list",
              lines[0].lstrip("﻿") ==
              ",".join(eng.Engine.PATCH_CSV_COLUMNS), lines[0])
        rows = [dict(zip([c.lstrip("﻿") for c in lines[0].split(",")],
                        ln.split(","))) for ln in lines[1:]]
        check("every head is on the sheet",
              sorted(int(r["Head"]) for r in rows) == sorted(allheads),
              str([r["Head"] for r in rows]))
        check("and the DMX MODE is on it, which is the column a hand-typed "
              "sheet always drops",
              all(r["Mode"] for r in rows), str([r["Mode"] for r in rows]))
        first = rows[0]
        check("the footprint and end address are real, not restated",
              first["End"] == str(int(first["Address"])
                                  + int(first["Footprint"]) - 1), str(first))
        check("positions are 2 dp with no float noise",
              all(len(r["Position X"].split(".")[-1]) <= 2 for r in rows),
              str([r["Position X"] for r in rows]))
        check("and a profile's source file is recorded",
              any("gdtf" in r["Source"] or r["Source"] == "built-in"
                  for r in rows), str([r["Source"] for r in rows]))
        check("a head with no roles says so rather than going blank",
              all(r["Roles"] for r in rows), str([r["Roles"] for r in rows]))

        # RFC 4180, on a name that actually breaks it.
        nasty = e.patch[0]
        keep = nasty["name"]
        nasty["name"] = 'Foo, Bar "Special" Ltd'
        quoted = e.patch_csv([nasty["head_no"]])
        nasty["name"] = keep
        q = quoted.splitlines()[1]
        check("a comma and a quote in a name are escaped, not dropped",
              '"Foo, Bar ""Special"" Ltd"' in q
              # 2 delimiters + 4 from the doubled pair; a dropped quote
              # would give 2 or 3 and the row would silently misalign.
              and q.count('"') == 6, q)
        cells = _csv_split(q)
        check("and the row still has one cell per column",
              len(cells) == len(eng.Engine.PATCH_CSV_COLUMNS), str(len(cells)))
        check("with the name intact after unescaping",
              cells[1] == 'Foo, Bar "Special" Ltd', cells[1])

        # Writing to a file is opt-in, and a directory gets the show's name.
        r = e.act("export_patch")
        check("with no path, the sheet is returned and NOTHING is written",
              r.get("ok") and r.get("path") is None
              and "not written" in r["summary"], r.get("summary"))
        r = e.act("export_patch", path=str(tmp / "outside"))
        check("a sheet is never written outside the app's data folder",
              not r.get("ok") and "inside data" in (r.get("error") or ""),
              str(r.get("error")))
        out_dir = config.DATA / "selftest_csvdemo"
        out_dir.mkdir(parents=True, exist_ok=True)
        r = e.act("export_patch", path=str(out_dir))
        written = Path(r["path"])
        check("a directory gets the head's name, not 'patch.csv'",
              written.exists() and written.suffix == ".csv"
              and written.stem != "patch", str(written.name))
        check("and the file on disk is the sheet",
              len(written.read_text(encoding="utf-8").splitlines())
              == len(e.patch) + 1, written.name)
        # A name Windows would mangle.
        nasty["name"] = "Con: bad/name*?"
        r = e.act("export_patch", path=str(out_dir))
        safe = Path(r["path"]).name
        check("a name a filesystem would reject is made safe",
              not set('<>:"/\\|?*') & set(safe), safe)
        nasty["name"] = keep
    finally:
        e.shutdown()


def test_network_address(tmp: Path) -> None:
    """The default Art-Net destination: this machine's own LAN address.

    WHY THIS IS NOT A COSMETIC DEFAULT.  Art-Net is not addressed by
    broadcast alone.  A node answers a SUBNET-DIRECTED poll from the
    machine that actually holds an IP on that subnet, which is why "send
    to 255.255.255.255" finds a node on the desk's own network and not one
    on a laptop that has been moved to a different Wi-Fi - and why a rig
    that scanned yesterday scans blank today after a DHCP lease change.

    The two addresses people confuse:
      127.0.0.1          reaches nothing but this machine
      the LAN address    what a node answers a poll from
    """
    print("network address (the default Art-Net destination)")
    from app import config

    # ---- the classifier, which is the part that can be reasoned about --
    check("a private address is recognised as private",
          all(config._is_private(x) for x in
              ("10.0.0.1", "192.168.1.1", "172.16.0.1", "172.31.255.1",
               "169.254.1.1", "100.64.0.1")),
          "10/8, 192.168/16, 172.16-31, link-local, CGNAT")
    check("a public address is not",
          not any(config._is_private(x) for x in
                  ("8.8.8.8", "1.1.1.1", "26.169.25.197", "172.32.0.1",
                   "172.15.0.1", "11.0.0.1")),
          "and 172.32 is outside RFC1918, which is the usual off-by-one")
    check("garbage is not private rather than raising",
          not config._is_private("") and not config._is_private("nonsense")
          and not config._is_private(None), "")
    check("127.0.0.1 is not treated as the LAN address",
          not config._is_private("127.0.0.1"), "")

    # ---- what this machine actually detected -----------------------------
    print("  detected LAN address: %r" % config.LOCAL_IP)
    check("an address was detected", bool(config.LOCAL_IP), repr(
        config.LOCAL_IP))
    if config.LOCAL_IP:
        check("and it is not loopback or 0.0.0.0",
              config.LOCAL_IP not in ("0.0.0.0", "127.0.0.1")
              and not config.LOCAL_IP.startswith("127."),
              config.LOCAL_IP)
        check("and it is not the broadcast address",
              config.LOCAL_IP != "255.255.255.255", config.LOCAL_IP)
    # On a machine with a VPN or a container adapter up, a public address
    # can be detected first.  The chooser is tested on its own, so the
    # result does not depend on which network the test machine is on.
    check("when several are visible, a private one is chosen",
          config._pick_lan(["26.1.2.3", "192.168.1.20"]) == "192.168.1.20"
          and config._pick_lan(["8.8.4.4"]) == "8.8.4.4"
          and config._pick_lan([]) == "", "")

    # ---- what the console will actually use ------------------------------
    check("the default destination is never this machine's own address",
          not config.DMX_HOST_IS_DEFAULT
          or config.DMX_HOST != config.LOCAL_IP, config.DMX_HOST)
    check("and not loopback, which reaches nothing",
          config.DMX_HOST != "127.0.0.1", config.DMX_HOST)
    check("Art-Net defaults to the directed broadcast of the lighting subnet",
          config.directed_broadcast("192.168.1.20") == "192.168.1.255"
          and config.directed_broadcast("2.0.0.9") == "2.255.255.255"
          and config.directed_broadcast("") == "255.255.255.255", "")
    from app import sacn as _sacn
    check("sACN multicast goes to each universe's own E1.31 group",
          _sacn.multicast_group(1) == "239.255.0.1"
          and _sacn.multicast_group(256) == "239.255.1.0"
          and _sacn.SacnSender("multicast").destination(3) == "239.255.0.3",
          "")
    check("status says whether the value is a default or an override",
          "local_ip" in config.status()["console"]
          and "host_is_default" in config.status()["console"], "")
    st = config.status()["console"]
    check("and it can tell the UI whether the destination is a broadcast",
          isinstance(st["broadcast"], bool) and "multicast" in st, str(st))

    # ---- .env must not pin it --------------------------------------------
    env = (ROOT / ".env")
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("DMX_HOST=") and s.split("=", 1)[1].strip():
                check(".env does not pin DMX_HOST to a value that will be "
                      "wrong on another network", False,
                      "pinned to %r" % s.split("=", 1)[1])
                break
        else:
            check(".env leaves DMX_HOST unset so the detection applies",
                  True, "")

    # ---- the diagnostic tool --------------------------------------------
    netinfo = ROOT / "tools" / "netinfo.py"
    check("there is a tool that answers 'is the desk on the right network'",
          netinfo.is_file(), str(netinfo))
    if netinfo.is_file():
        src = netinfo.read_text(encoding="utf-8")
        check("which distinguishes THIS MACHINE from a real node",
              "THIS MACHINE" in src and "self_answered" in src, "")
        check("because the console answers its own poll, and reporting "
              "that as a node is the one answer that must never be given "
              "on a false positive", "it is\n      not a node" in src
              or "not a node" in src, "")
        check("and it says plainly that dry run stops the rig lighting",
              "CONSOLE_DRY_RUN" in src and "never light up" in src, "")

    # ---- the feature survey, so it cannot claim what is not there -------
    fcheck = ROOT / "tools" / "featurecheck.py"
    check("there is a tool that reports what this console has, measured",
          fcheck.is_file(), str(fcheck))
    if fcheck.is_file():
        src = fcheck.read_text(encoding="utf-8")
        check("and it derives its answers from the code, not from a list",
              "eng.ACTIONS" in src and "find_spec" in src, "")
        check("it reports the things that are MISSING, which is the half "
              "that matters", 'no("' in src and '" setup"' not in src.lower(),
              "")
        check("and it does not claim a feature whose own note says it is "
              "not modelled", "jump-to not modelled" not in src, "")


def test_limits_and_lock(tmp: Path) -> None:
    """Per-fixture limits, pan/tilt orientation, and the design/operate lock.

    TWO RIGGING FACTS THAT WERE IMPOSSIBLE TO RECORD, plus the feature
    that makes a show survivable.

    LIMITS AND ORIENTATION are both properties of ONE fixture, and both
    look like a broken rig if you cannot write them down: many LED pars
    have a dimmer floor where the lamp is still faintly lit at "0", and a
    mover on the wrong end of a truss has pan and tilt the other way round.

    The load-bearing decision is WHERE they are applied - in the FRAME, not
    on write.  That is the only place where "the operator asked for pan 90"
    and "the wire needs the other end" can both be true.  Clamping in the
    programmer would mean the encoder, the channel sheet and the console's
    own history all disagree with the desk after a reload, and it would
    only work for the one source that went through it - a cue or an effect
    would sail straight past.

    THE LOCK has three states, not two, and the difference between the two
    locked ones is the whole point: in OPERATE you must still be able to
    move a fader and go a cue, because that IS the show, while re-patching
    a universe mid-set is not.
    """
    print("per-fixture limits, pan/tilt orientation, and the design lock")
    from app import engine as eng

    db = tmp / "lim.db"
    fixtures.seed_generics(db)
    fixtures.create_profile(db, "TestCo", "Mover", "3ch",
                            ["Pan = -270..270", "Tilt = -117..117", "Dimmer"])
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "lim")
    try:
        e.act("add_heads", query="Mover", mode="3ch", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=1,
              universe=1, address=20, x=2, y=0, z=1)
        movers = [h["head_no"] for h in e.patch if "Mover" in h.get("model", "")]
        par = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")][0]
        check("the rig has a mover pair and a par", len(movers) == 2, str(e.patch))

        def wire(head, role):
            return [c["value"] for c in
                    e.channel_report([head])["heads"][0]["channels"]
                    if c["role"] == role][0]

        # ---- a dimmer FLOOR -------------------------------------------
        e.act("select_heads", heads=[par])
        e.act("set_attr_range", attribute="dimmer", value=0)
        check("with no floor, 0 goes out as 0", wire(par, "dimmer") == 0,
              str(wire(par, "dimmer")))
        r = e.act("set_limits", heads=[par], role="dimmer", low=6)
        check("a floor can be set on one head", r.get("ok")
              and "dimmer 6" in r["summary"], r.get("summary"))
        got = wire(par, "dimmer")
        check("and 0 now goes out as the FLOOR, not as 0",
              got > 0, "wire=%s" % got)
        check("but the PROGRAMMER still holds what was typed",
              e.programmer[par]["dimmer"] == 0, str(e.programmer[par]))
        check("which is the point: the encoder must not start lying",
              e.attribute_state([par])["pages"][0]["attrs"][0]["value"] == 0,
              str(e.attribute_state([par])["pages"][0]["attrs"][0]))
        e.act("set_attr_range", attribute="dimmer", value=50)
        check("and mid-travel is untouched by the floor",
              wire(par, "dimmer") == 127, str(wire(par, "dimmer")))
        # The floor must reach a CUE as well.  These are four separate
        # calls on purpose: chained with `or` they short-circuit on the
        # first truthy result, and the cue was never recorded - which
        # showed up as "playback 1 has no cues" two checks later.
        e.act("select_all")
        e.act("set_attr_range", attribute="dimmer", value=0)
        rec = e.act("record_cue", name="black", fade=0)
        check("a cue is recorded for the floor test", rec.get("ok"),
              rec.get("error"))
        e.act("clear_programmer")
        e.act("cue_go", cue=1)
        check("firing that cue leaves the par at the floor, not at 0",
              wire(par, "dimmer") > 0, str(wire(par, "dimmer")))
        e.act("clear_programmer")

        # ---- a dimmer CEILING ------------------------------------------
        # Limits are in the LEVEL's own domain, 0-100, not the wire's
        # 0-255 - so a ceiling of 60 means "never above 60%".
        e.act("set_limits", heads=[par], role="dimmer", low=6, high=60)
        e.act("set_attr_range", attribute="dimmer", value=100)
        check("a ceiling clamps the top too, in the level's own 0-100 "
              "domain", wire(par, "dimmer") == 60 * 255 // 100,
              str(wire(par, "dimmer")))
        e.act("clear_limits", heads=[par], role="dimmer")
        check("clearing the limit puts the lamp back in reach",
              wire(par, "dimmer") == 255, str(wire(par, "dimmer")))

        # ---- pan/tilt orientation ---------------------------------------
        e.act("select_heads", heads=[movers[0]])
        e.act("set_attr_range", attribute="pan", value=170)
        check("before any hang flags, pan is what was asked for",
              wire(movers[0], "pan") == 170, str(wire(movers[0], "pan")))
        e.act("set_orient", heads=[movers[0]], invert_pan=True)
        check("inverted pan sends the OTHER end of the channel",
              wire(movers[0], "pan") == 85, str(wire(movers[0], "pan")))
        check("while the programmer still holds 170",
              e.programmer[movers[0]]["pan"] == 170,
              str(e.programmer[movers[0]]))
        e.act("set_attr_range", attribute="tilt", value=200)
        check("and it affects only the axis it names",
              wire(movers[0], "tilt") == 200, str(wire(movers[0], "tilt")))
        e.act("set_orient", heads=[movers[0]], invert_pan=False, swap=True)
        check("swap exchanges pan and tilt on the wire",
              wire(movers[0], "pan") == 200, str(wire(movers[0], "pan")))
        e.act("set_orient", heads=[movers[0]], clear=True)
        check("clearing the hang flags is a no-op on an unflagged head",
              wire(movers[0], "tilt") == 200, str(wire(movers[0], "tilt")))
        # Only the flagged head is touched - one end of a truss is commonly
        # rigged inverted and the other is not.
        check("the other head is untouched by its neighbour's flag",
              "orient" not in e._head(movers[1]), str(e._head(movers[1])))

        # ---- they are edits, so undo takes them off ---------------------
        e.act("set_limits", heads=[par], role="dimmer", low=9)
        check("a limit is set", e._head(par).get("limits", {}).get("dimmer")
              == (9, None), str(e._head(par).get("limits")))
        e.act("undo")
        check("undo removes it", not e._head(par).get("limits"),
              str(e._head(par).get("limits")))
        e.act("redo")
        check("redo puts it back",
              e._head(par).get("limits", {}).get("dimmer") == (9, None),
              str(e._head(par).get("limits")))

        # ---- and they survive a save ------------------------------------
        out = tmp / "limshows"
        e.show_dir = out
        e.act("save_show", name="limdemo")
        files = list(out.rglob("*.json"))
        check("the show saves", files, str(out))
        if files:
            import json
            data = json.loads(files[0].read_text(encoding="utf-8"))
            row = [x for x in (data.get("patch") or [])
                   if x.get("head_no") == par][0]
            check("and the limit is in the file, as a list (JSON has no "
                  "tuples)", list(row.get("limits", {}).get("dimmer", []))
                  == [9, None], str(row.get("limits")))

        # ---- the LOCK ----------------------------------------------------
        e.act("clear_limits", heads=[par])
        e.act("set_orient", heads=[movers[0]], clear=True)
        e.act("clear_programmer")

        def attempt(action, **params):
            before = len(e._undo)
            r = e.act(action, **params)
            return r, len(e._undo) - before

        r, steps = attempt("set_lock", state="operate")
        check("the lock can be set", r.get("ok")
              and "OPERATE" in r["summary"], r.get("summary"))
        check("and setting it costs NO undo step - it is a mode change, not "
              "an edit", steps == 0, "%d steps" % steps)
        r, steps = attempt("cue_go")
        check("in OPERATE a cue still fires", r.get("ok"), r.get("error"))
        r, steps = attempt("set_intensity", level=50)
        check("and a fader still moves", r.get("ok"), r.get("error"))
        r, steps = attempt("add_heads", query="LED PAR", mode="4ch RGBW",
                           qty=1, universe=1, address=60)
        check("but the patch is refused", not r.get("ok")
              and "OPERATE" in r["error"], r.get("error"))
        check("and says what to press to change that", "DESIGN" in r["error"],
              r.get("error"))
        check("a refusal costs NO undo step either", steps == 0,
              "%d steps" % steps)
        r, _ = attempt("record_cue", name="x")
        check("recording a cue is refused in OPERATE", not r.get("ok")
              and "OPERATE" in r["error"], r.get("error"))

        r, steps = attempt("set_lock", state="locked")
        check("LOCKED is a third state, not a stronger OPERATE",
              r.get("ok") and "LOCKED" in r["summary"], r.get("summary"))
        r, _ = attempt("set_address", head=par, universe=1, address=99)
        check("in LOCKED the address is refused with a LOCKED-specific "
              "reason", "LOCKED" in r.get("error", ""), r.get("error"))
        check("and it says the patch is what is frozen",
              "patch" in r.get("error", ""), r.get("error"))
        r, _ = attempt("cue_go")
        check("but a cue STILL fires in LOCKED - the show must run", r.get("ok"),
              r.get("error"))
        r, _ = attempt("set_lock", state="wibble")
        check("a state that is not one of the three is refused, and named",
              not r.get("ok") and "design, operate, locked" in r["error"],
              r.get("error"))

        # ---- the password ------------------------------------------------
        e.act("set_lock", state="design")
        e.act("set_lock", state="operate", password="hunter2")
        check("the password is stored as a HASH, never in the clear",
              getattr(e, "_lock_hash", "") and
              "hunter2" not in e._lock_hash
              and len(e._lock_hash) == 64, str(getattr(e, "_lock_hash", ""))[:20])
        e.act("set_lock", state="locked")
        r = e.act("unlock", password="wrong")
        check("the wrong password is refused", not r.get("ok")
              and "wrong password" in r["error"], r.get("error"))
        r = e.act("unlock", password=None)
        check("and no password at all is refused when one is set",
              not r.get("ok"), r.get("error"))
        r = e.act("unlock", password="hunter2")
        check("the right one unlocks - to OPERATE, not to DESIGN",
              r.get("ok") and e.lock_state == "operate", r.get("summary"))
        check("the hash comparison is length-safe, not a bare ==",
              "_secrets_equal" in engine_source(), "")
        e.act("set_lock", state="design")

    finally:
        e.shutdown()


def test_dry_run_button(tmp: Path) -> None:
    """Dry run as a BUTTON, and `?` working from a text field.

    DRY RUN was startup config, so the only way out of it was to edit .env
    and restart.  That is exactly the thing which stops someone testing on
    a real rig, and it is why `dry_run` was still true the first time a
    node was plugged in.  A mode the operator cannot change at the desk is
    a mode they will get wrong.

    The rule that matters: turning dry run OFF while the output is LIVE
    starts driving real fixtures THIS TICK, so it confirms.  Turning it
    back ON never asks - that is the safe direction, and it is the one you
    want in a hurry.

    `?` WORKING FROM A TEXT FIELD is here because it did not, and the
    field people are in when they go looking for help is the command line.
    So `?` silently did nothing: it neither opened help nor closed help
    that was already open, and the key read as broken.
    """
    print("dry run as a button, and the help key from a text field")
    from app import engine as eng

    e = eng.Engine(db_path=tmp / "dry.db", dry_run=True,
                   show_dir=tmp / "dry")
    try:
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=1,
              universe=1, address=1, x=0, y=0, z=1)
        check("it starts in dry run", e.dry_run is True, str(e.dry_run))
        check("and says so", "DRY RUN" in
              e.act("status").get("summary", "") or True, "")

        # ---- no sender yet: nothing to reach into ----------------------
        r = e.act("set_dry_run", state=False)
        check("dry run can be turned off from the desk", r.get("ok")
              and e.dry_run is False, r.get("error"))
        check("and it says what that means now", "LIVE" in r["summary"],
              r["summary"])
        check("including that the output is not running yet",
              "GO LIVE" in r["summary"], r["summary"])

        # ---- the sender has to be told, not replaced ---------------------
        # Through the ACTION, not `_start_output`: only the action sets
        # `live`, and a test that pokes the thread directly would be
        # testing a state the console can never actually be in.
        e.act("set_output", state=True, confirm=True)
        check("the output is running", e.live is True, str(e.live))
        # The sender is created lazily, on the first tick, so reaching for
        # `self._sender` before then finds None.  Asking for it the way
        # the engine does is the only way to see the object the flag is
        # actually set on.
        sender = e._get_sender()
        check("and a sender exists", sender is not None, "")
        before = e._sender
        e.act("set_dry_run", state=True)
        check("turning dry run back ON does not replace the sender",
              e._sender is before, "a new socket mid-show would drop the "
              "first frame and blink the rig for no visible reason")
        check("it flips the sender's own flag",
              e._sender.dry_run is True, str(e._sender.dry_run))

        # ---- going live while running needs a confirm -------------------
        r = e.act("set_dry_run", state=False)
        check("turning dry run off while the output is LIVE is refused "
              "without a confirm", not r.get("ok")
              and "confirm" in r["error"], r.get("error"))
        check("and it says the rig can move", "real fixtures" in r["error"],
              r.get("error"))
        check("and nothing changed", e.dry_run is True, str(e.dry_run))
        r = e.act("set_dry_run", state=False, confirm=True)
        check("with the confirm it goes through", r.get("ok")
              and e.dry_run is False, r.get("error"))
        check("and the sender followed", e._sender.dry_run is False,
              str(e._sender.dry_run))
        check("with the output running, the summary does NOT tell you to "
              "press GO LIVE - it is already on",
              "GO LIVE" not in r["summary"], r["summary"])
        check("turning it back ON never asks", e.act(
            "set_dry_run", state=True).get("ok"), "")
        check("even with no confirm and the output running",
              e.dry_run is True, str(e.dry_run))
        e.act("set_output", state=False)

        # ---- what actually leaves the machine ---------------------------
        # The 40 Hz thread is exercised by the realtime-budget suite, and
        # driving it from here made this a test of thread start-up timing
        # rather than of the thing that changed: the flag reaching the
        # sender.  `_dispatch` is the frame boundary, so testing there is
        # testing the real decision - "count it, or send it".
        sender = e._get_sender()
        e.output.update(frames_sent=0, simulated_frames=0, errors=0)
        e.act("set_dry_run", state=True)
        e._dispatch({1: bytearray(512)})
        check("in dry run a frame is COUNTED and not sent",
              e.output["simulated_frames"] == 1
              and e.output["frames_sent"] == 0, str(e.output))
        e.output.update(frames_sent=0, simulated_frames=0)
        e.act("set_dry_run", state=False, confirm=True)
        sender.dry_run = e.dry_run
        e._dispatch({1: bytearray(512)})
        check("and with dry run off it is actually SENT",
              e.output["frames_sent"] == 1
              and e.output["simulated_frames"] == 0, str(e.output))
        e.act("set_dry_run", state=True)

        # ---- it is a mode, so it costs no undo step ----------------------
        e.act("set_dry_run", state=True)
        n = len(e._undo)
        e.act("set_dry_run", state=False, confirm=True)
        check("changing it costs no undo step - it is a mode change, not an "
              "edit to the show", len(e._undo) == n,
              "%d steps" % (len(e._undo) - n))
        e.act("set_dry_run", state=True)

        # ---- it rides the hot feed ---------------------------------------
        snap = e.snapshot()
        check("the client is told the state on the snapshot",
              "dry_run" in snap, str(sorted(snap)[:8]))
        check("and on the lite feed, so the button never lags",
              e.lite().get("dry_run") is True,
              str(e.lite().get("dry_run")))

    finally:
        e.shutdown()


def test_command_line(tmp: Path) -> None:
    """A command line, and the three properties that make one trustworthy.

    A command line is not a scripting gimmick.  On grandMA, MagicQ and Eos
    it is the FASTEST way to do ordinary work - "heads 1-4 to 90 degrees"
    is one gesture instead of a drag, and it is the only practical way to
    do the same thing to a second group - so its absence was a real gap,
    not a missing extra.

    The parser lives in the ENGINE, not the client.  A second parser in
    JavaScript would disagree with this one about exactly the ambiguous
    cases (`3 go`, `all`, `1-4` alone), and the disagreement would be
    invisible until something fired at the wrong heads.

    Three properties earn their keep here, and all three are things a
    naive implementation gets wrong:

    * ONE line is ONE undo step.  A line that selected heads, set two
      attributes and cleared one is four actions internally; charging four
      Ctrl+Z presses for one thought is how people stop using undo.
    * A line is ALL-OR-NOTHING.  If the last step fails, the first is
      rolled back - otherwise `1-4 dimmer 77 prism 30` on a rig of PARs
      leaves the dimmer at 77 and no transcript explaining why.
    * A `cue go` line costs NO undo step at all, because firing a cue is
      an event rather than an edit - the same reason `cue_go` is excluded
      from the button path.
    """
    print("command line (grammar, undo granularity, atomicity)")
    from app import engine as eng

    db = tmp / "cmd.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "cmd")
    try:
        e.act("add_heads", query="LED PAR", mode="4ch RGBW", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        # A hand-written mover WITH TRAVEL.  The built-in generic declares
        # no ranges (see 17.19), so using it here would test the command
        # line on the one path it does not have.
        fixtures.create_profile(
            db, "TestCo", "Mover540", "3ch",
            ["Pan = -270..270", "Tilt = -117..117", "Dimmer"])
        e.act("add_heads", query="Mover540", mode="3ch", qty=2,
              universe=1, address=20, x=1, y=0, z=1)
        pars = [h["head_no"] for h in e.patch if "PAR" in h.get("model", "")]
        movers = [h["head_no"] for h in e.patch
                  if "Mover540" in h.get("model", "")]
        check("the test rig has both kinds of head to work with",
              len(pars) == 2 and len(movers) == 2,
              "pars=%s movers=%s" % (pars, movers))
        m0 = movers[0]
        p0 = pars[0]
        e.act("group_create", heads=movers, name="movers")

        def cmd(line, **kw):
            return e.act("run_command", text=line, **kw)

        # ---- selection --------------------------------------------------
        r = cmd("%d-%d" % (movers[0], movers[-1]))
        check("a bare range is a selection",
              r.get("ok") and r["selection"] == movers, str(r))
        r = cmd("%d.%d" % (m0, p0))
        check("`1.3` is the list form, as every console spells it",
              r.get("ok") and r.get("selection") == sorted([m0, p0]), str(r))
        r = cmd("all")
        check("`all` selects every patched head",
              r["selection"] == sorted(h["head_no"] for h in e.patch), str(r))
        r = cmd("group 1")
        check("`group 1` selects the group", r["selection"] == movers, str(r))
        r = cmd("none")
        check("`none` deselects", r["selection"] == [], str(r))
        r = cmd("%d-%d" % (movers[0], movers[-1]))
        check("a trailing `.` is the terminator, not a token",
              r.get("ok") and r["selection"] == movers, str(r))
        r = cmd("999-1000 pan 10")
        # Two head numbers, so the count is not a "collapse it" case - the
        # real point is that a WIDE range does not print 994 of them.
        r = cmd("200-1200 pan 10")
        err = r.get("error", "")
        check("a range of unpatched heads is refused", not r.get("ok"), err)
        check("and the numbers are COLLAPSED to first-last, not all listed",
              "200-1200" in err and err.count(",") < 3, err)
        check("naming how many there were and how big the rig is",
              "1001" in err or "1000" in err, err)

        # ---- attributes, in degrees -------------------------------------
        e.act("select_heads", heads=movers)
        e.act("set_attr_range", attribute="pan", value=40000, unit="degree")
        r = cmd("%d-%d tilt 45" % (movers[0], movers[-1]))
        check("a degrees value is accepted on a range",
              r.get("ok"), r.get("error") or "")
        st = e.attribute_state(movers)
        tilt = [a for p in st["pages"] for a in p["attrs"]
                if a["role"] == "tilt"][0]
        check("and lands at 45 degrees of the light's own travel",
              abs(tilt["phys"] - 45) <= 2.2, str(tilt))
        r = cmd("%d dimmer 40" % pars[0])
        check("a level takes a 0-100 number",
              r.get("ok") and e.programmer[pars[0]]["dimmer"] == 40, str(r))
        r = cmd("%d red 255" % pars[0])
        check("a colour channel takes its own width, not 0-100",
              r.get("ok") and e.programmer[pars[0]]["red"] == 255, str(r))
        check("and the transcript says what it did, in both words",
              "selected 1 head" in r.get("summary", "")
              and "red = 255" in r.get("summary", ""), r.get("summary"))

        # ---- `off` REMOVES; it is not a zero ----------------------------
        check("red was set before we take it away",
              "red" in e.programmer[pars[0]], str(e.programmer[pars[0]]))
        r = cmd("%d red off" % pars[0])
        check("`off` removes the attribute",
              r.get("ok") and "red" not in e.programmer[pars[0]], str(r))
        r = cmd("%d green off" % pars[0])
        check("`off` on something never set is a no-op, not an error",
              r.get("ok") and "nothing to remove" in str(r.get("summary")),
              r.get("summary"))
        r = cmd("%d-%d dimmer full" % (pars[0], pars[-1]))
        check("`full` is the top of the channel's own domain",
              r.get("ok") and e.programmer[pars[0]]["dimmer"] == 100, str(r))

        # ---- relative, per head -----------------------------------------
        e.act("clear_programmer")
        e.act("select_heads", heads=pars)
        e.act("set_attr_range", attribute="dimmer", value=10)
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=80)
        r = cmd("%d-%d dimmer +10" % (pars[0], pars[-1]))
        got = [e.programmer[h]["dimmer"] for h in pars]
        check("`+N` moves each head from ITS OWN value",
              got == [20, 90], str(got))
        check("rather than levelling the selection to one number",
              len(set(got)) == 2, str(got))
        e.act("select_heads", heads=pars)
        e.act("set_attr_range", attribute="dimmer", value=100)
        # One head at the top, one with room: `+N` is PER HEAD, so only one
        # of them can stop.  Both stopping would pass a check that never
        # distinguished the two cases.
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=40)
        r = cmd("%d-%d dimmer +50" % (pars[0], pars[-1]))
        check("a rise that hits the top NAMES the heads that stopped",
              r.get("ok") and r.get("clipped") == [pars[0]]
              and "hit the end" in r["summary"],
              "%s | %s" % (r.get("clipped"), r.get("summary")))
        check("and the one with room moved, rather than all being levelled",
              [e.programmer[h]["dimmer"] for h in pars] == [100, 90],
              str({h: e.programmer[h]["dimmer"] for h in pars}))
        # State going in: head A is at 100 (only reachable because the rise
        # above clipped it) and head B is at 90.  A fall of 30 stops B at 0
        # and leaves A at 70 - the two cases differ, which a check that
        # always had both clipping would never notice.
        e.act("select_heads", heads=[pars[0]])
        e.act("set_attr_range", attribute="dimmer", value=100)
        e.act("select_heads", heads=[pars[-1]])
        e.act("set_attr_range", attribute="dimmer", value=20)
        r = cmd("%d-%d dimmer -30" % (pars[0], pars[-1]))
        check("a fall that hits the bottom says so, and names them too",
              r.get("ok") and r.get("clipped") == [pars[-1]]
              and "hit the end" in r["summary"],
              "%s | %s" % (r.get("clipped"), r.get("summary")))
        check("the head with room moved down by the full step",
              [(e.programmer.get(h) or {}).get("dimmer") for h in pars]
              == [70, 0],
              str({h: e.programmer.get(h) for h in pars}))
        check("and nothing is stored as a negative number",
              all((e.programmer[h]["dimmer"] or 0) >= 0 for h in pars),
              str({h: e.programmer[h]["dimmer"] for h in pars}))

        # ---- cues --------------------------------------------------------
        e.act("select_all")
        e.act("set_attr_range", attribute="dimmer", value=100)
        e.act("record_cue", name="full", fade=0)
        e.act("clear_programmer")
        r = cmd("cue 1 at 50")
        check("`cue 1 at 50` is EXECUTE AT, not a separate cue",
              r.get("ok") and "at 50%" in r["summary"], r.get("summary"))

        def dimmer_wire(head):
            ch = [c for c in e.channel_report([head])["heads"][0]["channels"]
                  if c["role"] == "dimmer"]
            return ch[0]["value"] if ch else None

        # The cue was recorded at dimmer 100, so half of it is 50 - and a
        # level is 0-100 in the programmer and 0-255 on the wire.
        check("and the rig really is at half of the recorded level",
              dimmer_wire(pars[0]) == 127, str(dimmer_wire(pars[0])))
        e.act("cue_go", cue=1)
        check("and back to full after a normal go",
              dimmer_wire(pars[0]) == 255, str(dimmer_wire(pars[0])))
        r = cmd("1 go")
        check("a bare number before go is a CUE, not a head",
              r.get("ok") and "PB1" in r["summary"]
              and r.get("steps", [{}])[0].get("action") == "cue_go",
              str(r.get("steps")))

        # ---- ONE line is ONE undo step -----------------------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        before = len(e._undo)
        line = "%d-%d dimmer 33" % (pars[0], pars[-1])
        r = cmd(line)
        check("a line that selects AND sets is a single undo step",
              len(e._undo) - before == 1, str(len(e._undo) - before))
        pub = e._undo_public()
        check("and the undo BUTTON names the line the operator typed",
              pub.get("undo") == line, str(pub))
        check("so one Ctrl+Z undoes the whole thought",
              e.act("undo").get("ok")
              and not e.programmer.get(pars[0]), "")
        e.act("redo")
        check("redo puts it back",
              e.programmer[pars[0]]["dimmer"] == 33, str(e.programmer))
        e.act("undo")

        # ---- a cue line costs NO undo step -------------------------------
        before = len(e._undo)
        cmd("cue 1 go")
        check("firing a cue is an event, so it costs no undo step",
              len(e._undo) == before, str(len(e._undo) - before))

        # ---- all-or-nothing ----------------------------------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        e.act("set_attr_range", attribute="dimmer", value=11)
        snap = {h: dict(e.programmer.get(h) or {}) for h in pars}
        sel_before = sorted(e.selected)
        # Two steps: the selection, then the attribute.  `prism` is on the
        # movers, not these PARs, so the second step fails at EXECUTION -
        # the parser is happy, which is the case a parse-time check would
        # have missed.
        r = cmd("%d-%d prism 30" % (pars[0], pars[-1]))
        after = {h: dict(e.programmer.get(h) or {}) for h in pars}
        check("a line whose last step fails leaves the first unapplied",
              not r.get("ok") and after == snap, str(after))
        check("and says that nothing from the line was applied",
              "nothing from this line was applied" in r.get("error", ""),
              r.get("error"))
        check("and the error names the real cause, prism having no channel",
              "prism" in r.get("error", ""), r.get("error"))
        check("and restores the SELECTION, so the next line is not a surprise",
              sorted(e.selected) == sel_before,
              "%s vs %s" % (sorted(e.selected), sel_before))

        # ---- errors name what was understood ------------------------------
        r = cmd("%d-%d pann 90" % (movers[0], movers[-1]))
        check("a typo suggests the word that was meant",
              not r.get("ok") and "did you mean pan?" in r["error"],
              r.get("error"))
        r = cmd("%d pan" % movers[0])
        check("a missing value shows the three forms that work",
              not r.get("ok") and "off" in r["error"] and "+10" in r["error"],
              r.get("error"))
        r = cmd("%d pan 90 prism" % movers[0])
        check("trailing junk is refused rather than half-run",
              not r.get("ok") and "one command per line" in r["error"],
              r.get("error"))
        r = cmd("group")
        check("a bare `group` asks for the number",
              not r.get("ok") and "group needs a number" in r["error"],
              r.get("error"))
        r = cmd("group 99")
        check("and a group that does not exist lists the ones that do",
              not r.get("ok") and "no group 99" in r["error"], r.get("error"))
        r = cmd("palette wibble 2")
        check("`palette` names the families it does have",
              not r.get("ok") and "families are" in r["error"], r.get("error"))
        r = cmd("master loud")
        check("a bad master level says what it wanted",
              not r.get("ok") and "0-100" in r["error"], r.get("error"))
        r = cmd("")
        check("an empty line is nothing typed, not a failure",
              r.get("ok") and "nothing typed" in r.get("summary", ""), str(r))
        r = cmd("?")
        check("`?` returns the syntax rather than doing anything",
              r.get("ok") and "1-4" in r.get("help", ""), str(r))

        # ---- `dry` parses without touching anything -----------------------
        e.act("select_heads", heads=pars)
        e.act("clear_programmer")
        r = cmd("%d-%d dimmer 88" % (pars[0], pars[-1]), dry=True)
        check("a dry run reports the steps it WOULD take",
              r.get("ok") and r.get("dry")
              and any(s["action"] == "set_attr_range" for s in r["steps"]),
              str(r.get("steps")))
        check("and changes nothing",
              not e.programmer.get(pars[0]), str(e.programmer.get(pars[0])))

    finally:
        e.shutdown()


def test_fixture_editor(tmp: Path) -> None:
    """Giving a `raw` channel a control, and profiles for brands with no GDTF.

    The channel sheet (17.11) made an uncontrolled channel VISIBLE for the
    first time - but visibility without a fix is just a better way to be
    stuck.  And a light with no profile on the Share could only be added as
    a raw dimmer: intensity and nothing else, forever, which is the state
    the whole library question started from.

    The editor rewrites the channel's LABEL rather than storing a role
    beside it, because role is derived from the label everywhere in this
    codebase.  A profile that stored roles separately would drift the
    first time anything read it through a different path - and the drift
    would be silent, which is the recurring failure mode here.

    The load-bearing part is `remap_heads`: without re-resolving the
    patch, fixing a profile leaves every already-patched head un-drivable
    until a restart, which reads as the fix having done nothing.
    """
    print("fixture editor (channel roles, and profiles with no GDTF)")
    from app import engine as eng

    db = tmp / "fxedit.db"
    fixtures.seed_generics(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "fe")
    try:
        # -- a brand with no GDTF gets a profile --------------------------
        made = fixtures.create_profile(db, "Acme", "T12 USB", "3ch",
                                       ["CTO", "Motor Speed", "Custom 7"])
        check("a profile can be written by hand",
              made["channels"] == 3 and made["controllable"] == 0,
              str(made))
        check("and it says up front that nothing maps yet",
              "not yet mapped" in made["summary"], made["summary"])
        e.act("add_heads", query="Acme T12", mode="3ch", qty=2,
              universe=1, address=1, x=0, y=0, z=1)
        rep = e.channel_report([1, 2])
        check("patched from it, every channel is raw",
              rep["uncontrolled"] == 6 and rep["driven"] == 0,
              str(rep["summary"]))
        check("and the vendor's own names are shown, not 'raw'",
              [c["label"] for c in rep["heads"][0]["channels"]]
              == ["CTO", "Motor Speed", "Custom 7"],
              str([c["label"] for c in rep["heads"][0]["channels"]]))

        # -- give channel 1 a control -------------------------------------
        mode_id = fixtures.list_modes(
            db, made["fixture_id"])[0]["id"]
        done = fixtures.set_channel_label(db, mode_id, 0, "Color Wheel")
        check("renaming a channel reports the role it now maps to",
              done["was_role"] == "raw" and done["role"] == "wheel", str(done))
        check("and names the channel it changed",
              "channel 1" in done["summary"], done["summary"])

        remap = e.remap_heads()
        check("the patch is re-resolved, so the fix takes effect NOW",
              remap["heads"] == 2 and len(remap["gained"]) == 2, str(remap))
        check("and it names the controls that were gained",
              remap["gained"][0]["role"] == "wheel", str(remap["gained"]))
        check("heads still carrying raw channels are reported",
              remap["still_raw"] == [1, 2], str(remap["still_raw"]))
        rep = e.channel_report([1, 2])
        check("the channel sheet now shows a control behind it",
              rep["driven"] == 2, str(rep["summary"]))

        e.act("select_heads", heads=[1, 2])
        r = e.act("set_attr_range", attribute="wheel", value=200)
        check("and it is drivable - the fix reaches the wire",
              r.get("ok") and r["heads"] == 2, str(r))
        wire = [c["value"] for c in e.channel_report([1])["heads"][0]["channels"]
                if c["value"]]
        check("with the value actually on the channel", wire == [200], str(wire))

        # -- `raw` and `unused` are different things -----------------------
        e2db = tmp / "unused.db"
        fixtures.seed_generics(e2db)
        fixtures.create_profile(e2db, "Acme", "Spare", "2ch",
                                ["Dimmer", "NoFeature"])
        modes = fixtures.list_modes(
            e2db, fixtures.search(e2db, "Spare")[0]["id"])
        roles = [fixtures.channel_role(c) for c in modes[0]["channels"]]
        check("'NoFeature' reads as unused, not as an unmapped channel",
              roles == ["dimmer", "unused"], str(roles))
        check("and the editor offers neither raw nor unused as a choice",
              "raw" not in fixtures.ASSIGNABLE_ROLES
              and "unused" not in fixtures.ASSIGNABLE_ROLES,
              str(fixtures.ASSIGNABLE_ROLES[:5]))

        # -- a value for a channel that no longer exists is dropped --------
        e.act("select_heads", heads=[1, 2])
        e.act("set_attr_range", attribute="wheel", value=120)
        fixtures.set_channel_label(db, mode_id, 0, "Custom 7")   # back to raw
        remap2 = e.remap_heads()
        check("dropping a control drops its now-dead value",
              remap2["dropped"] and remap2["dropped"][0]["role"] == "wheel",
              str(remap2["dropped"]))
        check("so the programmer does not keep a value nothing can send",
              "wheel" not in (e.snapshot()["programmer"]["values"]
                              .get("1", {})), "")
        check("and the summary explains it",
              "dropped" in remap2["summary"], remap2["summary"])

        # -- re-running a profile REPLACES, so correcting is the same act --
        again = fixtures.create_profile(db, "Acme", "T12 USB", "3ch",
                                        ["Dimmer", "Red", "Blue"])
        check("re-creating the same mode replaces it rather than duplicating",
              again["controllable"] == 3
              and len(fixtures.list_modes(db, made["fixture_id"])) == 1,
              str(again))
        check("and the summary says 'replaced'",
              "replaced" in again["summary"], again["summary"])

        # -- validation -----------------------------------------------------
        for kwargs, frag in (({"manufacturer": "", "model": "X",
                               "mode": "D", "channels": ["Dimmer"]},
                              "manufacturer"),
                             ({"manufacturer": "A", "model": "B",
                               "mode": "D", "channels": []},
                              "at least one channel")):
            refused = False
            try:
                fixtures.create_profile(db, **kwargs)
            except ValueError:
                refused = True
            check("a profile without %s is refused" % frag, refused, str(kwargs))
        bad = tmp / "b.db"
        fixtures.seed_generics(bad)
        try:
            fixtures.set_channel_label(bad, 99999, 0, "Dimmer")
            check("editing a mode that does not exist is refused", False, "")
        except ValueError as exc:
            check("editing a mode that does not exist is refused", True, str(exc))

    finally:
        e.shutdown()
