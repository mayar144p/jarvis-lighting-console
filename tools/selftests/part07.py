"""Self-test suites, part 7: button_tiles, button_speed, button_midi, group_flash_laser_button, cue_fx_parts, change_type, ready_versions, review_fixes, ...."""
from __future__ import annotations

import json
import re
import struct
import tempfile
import threading
from pathlib import Path

from app import fixtures
from tools.selftests.common import ROOT, check


def test_button_tiles() -> None:
    """A button can be 2 wide, 2 tall or both, and carry an icon; the
    editor offers exactly the icons the engine accepts."""
    print("button tiles (size and icon)")
    import tempfile

    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        e = eng.Engine(db_path=tmp / "f.db", dry_run=True, show_dir=tmp / "s")
        try:
            r = e.act("quick_set", page=1, slot=1, button={"kind": "blackout", "size": "big", "icon": "moon"})
            b = r.get("button") or {}
            check("a button keeps its size and icon", b.get("size") == "big" and b.get("icon") == "moon", str(b))
            r = e.act("quick_set", page=1, slot=2, button={"kind": "blackout", "size": "normal"})
            check("normal size is not stored", "size" not in (r.get("button") or {}), str(r.get("button")))
            r = e.act("quick_set", page=1, slot=3, button={"kind": "blackout", "size": "huge"})
            check("an unknown size is refused", not r.get("ok"), str(r))
            r = e.act("quick_set", page=1, slot=3, button={"kind": "blackout", "icon": "<svg>"})
            check("an unknown icon is refused", not r.get("ok"), str(r))
            r = e.act("quick_move", page=1, slot=1, to_page=2, to_slot=5)
            moved = [x for x in e.quick if x["page"] == 2 and x["slot"] == 5]
            check("moving a button keeps its size and icon", bool(moved) and moved[0].get("size") == "big"
                  and moved[0].get("icon") == "moon", str(moved))
        finally:
            e.shutdown()
    qb = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    m = re.search(r"const ICONS = \{(.*?)\n\};", qb, re.S)
    ui = set(re.findall(r"^\s+(\w+):", m.group(1), re.M)) if m else set()
    check("the editor's icons are the engine's icons", ui == set(eng.Engine.QUICK_ICONS),
          str(ui ^ set(eng.Engine.QUICK_ICONS)))
    check("big tiles span the grid and cover the slots under them", "gridColumn" in qb and "covered" in qb, "")


def test_button_speed() -> None:
    """A button's effects run at its own speed, changeable live, and can
    ignore the Speed master."""
    print("button speed")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=2, universe=1, address=1)
            r = e.act("quick_set", page=1, slot=1, button={"kind": "fx", "fx": "rainbow", "mode": "latch", "rate": 2})
            check("a button keeps its speed", (r.get("button") or {}).get("rate") == 2.0, str(r.get("button")))
            e.act("quick_press", id="q1-1")
            row = next((f for f in e.fx if f["id"] in e.quick_active["q1-1"]["fx_ids"]), {})
            check("its effect runs at that speed", row.get("rate") == 2.0, str(row.get("rate")))

            def advance(secs):
                t0 = row.get("_last", row["t0"])
                v0 = row.get("_v", 0.0)
                e._fx_values(t0 + secs)
                return row["_v"] - v0

            e.act("speed_master", value=0.5)
            check("...times the Speed master", abs(advance(1.0) - 1.0) < 1e-6, "")
            r = e.act("quick_rate", id="q1-1", rate=3, free=True)
            check("the speed changes live, and it can ignore the master",
                  r.get("ok") and abs(advance(1.0) - 3.0) < 1e-6, str(r))
            r = e.act("quick_rate", id="q1-1", rate=1, free=False)
            b = next(x for x in e.quick if x["id"] == "q1-1")
            check("back to normal stores nothing", "rate" not in b and "free" not in b, str(b))
            check("changing the speed is not an undo step", "quick_rate" in eng.UNDO_EXCLUDED, "")
            e.act("quick_set", page=1, slot=2, button={"kind": "flash"})
            r = e.act("quick_rate", id="q1-2", rate=2)
            check("a button without effects has no speed", not r.get("ok"), str(r))
        finally:
            e.shutdown()
    qb = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    check("the tile changes speed by scroll and right-click", '"wheel"' in qb and '"contextmenu"' in qb
          and "quick_rate" in qb, "")


def test_button_midi() -> None:
    """A MIDI note given to a button plays it (ahead of the map file), a
    hold button lets go on note off, and the last note is kept for Learn."""
    print("button MIDI notes")
    from app import engine as eng
    from app import midi

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            r = e.act("quick_set", page=1, slot=1, button={"kind": "flash", "mode": "hold", "midi": 36})
            check("a button keeps its MIDI note", (r.get("button") or {}).get("midi") == 36, str(r.get("button")))
            r = e.act("quick_set", page=1, slot=2, button={"kind": "flash", "midi": 200})
            check("a note past 127 is refused", not r.get("ok"), str(r))
            e.act("quick_set", page=1, slot=3, button={"kind": "fx", "fx": "rainbow", "mode": "latch", "midi": 40})
            m = midi.MidiManager(e, mapper=midi.MidiMapper(), source=midi.FakeMidiSource(), devices=[])
            m.handle(midi.parse_short(0x90, 36, 100))
            check("the note plays the button, not the map's GO",
                  "q1-1" in e.quick_active and not e.playbacks[0]["active"], str(list(e.quick_active)))
            check("the last note is kept for Learn", (m.status.get("last_note") or {}).get("number") == 36,
                  str(m.status.get("last_note")))
            m.handle(midi.parse_short(0x80, 36, 0))
            check("note off lets go of a hold button", "q1-1" not in e.quick_active, str(list(e.quick_active)))
            m.handle(midi.parse_short(0x90, 40, 100))
            m.handle(midi.parse_short(0x80, 40, 0))
            check("an on / off button stays on after note off", "q1-3" in e.quick_active, "")
            m.handle(midi.parse_short(0x90, 40, 100))
            check("...and the next note turns it off", "q1-3" not in e.quick_active, "")
            m.handle(midi.parse_short(0x90, 50, 100))
            check("other notes still go to the map", (m.status.get("last_note") or {}).get("number") == 50, "")
        finally:
            e.shutdown()
    qb = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    check("the editor learns a note from the MIDI input", "learnMidi" in qb and "last_note" in qb, "")
    wm = (ROOT / "web" / "app" / "webmidi.js").read_text(encoding="utf-8")
    check("a controller on the tablet plays buttons too (Web MIDI, off until switched on)",
          "requestMIDIAccess" in wm and "onNote(" in qb and "jarvis.webmidi" in wm, "")
    check("speed by touch: the tile's speed badge opens the menu", 'closest(".qrate")' in qb, "")


def test_group_flash_laser_button() -> None:
    """Holding a group chip flashes that group (never stored); the Laser
    tab turns the laser look in the programmer into an on / off button."""
    print("group flash and laser buttons")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        fixtures.store_parsed(db, fixlib.load("jarvis", "laserworld/beambar-10b-mk3"), "jarvis:laserworld/beambar-10b-mk3")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=2, universe=1, address=1)
            e.act("add_heads", query="BeamBar 10B MK3", qty=1, universe=1, address=40)
            e.act("group_create", name="Left", heads=[1])
            di = e.patch[0]["map"].index("dimmer")
            dim2 = e.patch[1]["address"] - 1 + e.patch[1]["map"].index("dimmer")
            undo_before = len(e._undo)
            r = e.act("group_flash", group=1, down=True)
            f = e.build_frames()[1]
            check("holding a group chip flashes that group only", r.get("ok") and f[di] == 255 and f[dim2] == 0,
                  str((r, f[di], f[dim2])))
            e.act("group_flash", down=False)
            check("...and letting go ends it", e.build_frames()[1][di] == 0 and "flash:chip" not in e.quick_active, "")
            check("a group flash is not an undo step", len(e._undo) == undo_before, "")
            r = e.act("group_flash", auto="kind:nothing", down=True)
            check("a group with no lights says so", not r.get("ok"), str(r))
            e.act("select_heads", heads=[3])
            e.act("set_attribute", attribute="laser_beam1", value=255)
            e.act("set_attribute", attribute="laser_beam3", value=200)
            r = e.act("quick_from_laser", heads=[3], label="Beams 1+3")
            b = r.get("button") or {}
            check("the laser look becomes an on / off laser button", r.get("ok") and b.get("kind") == "laser"
                  and b.get("mode") == "latch" and b.get("target") == {"heads": [3]}
                  and b.get("values", {}).get("laser_beam1") == 255 and b["values"].get("laser_beam3") == 200, str(r))
            r = e.act("quick_from_laser", heads=[1])
            check("a PAR can't make a laser button", not r.get("ok"), str(r))
        finally:
            e.shutdown()
    fx = (ROOT / "web" / "app" / "fxpanel.js").read_text(encoding="utf-8")
    fj = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    check("the Laser tab records a cue and makes a button", "quick_from_laser" in fx and "openCueDialog" in fx, "")
    check("group chips flash while held", "holdToFlash" in fj and "group_flash" in fj, "")


def test_cue_fx_parts() -> None:
    """Effects running when a cue is recorded go into the cue: GO starts
    them, the next cue replaces them, release stops them, and they survive
    a save.  A cue part (colour, position...) can have a fade of its own."""
    print("effects in cues, cue part times, buttons from movements")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=2)
            e.act("select_all")
            e.act("set_intensity", level=100)
            r = e.act("run_fx", name="rainbow")
            check("an effect runs", r.get("ok"), str(r))
            r = e.act("record_cue", playback=1, name="Rainbow")
            cue = e.playbacks[0]["stack"][0]
            check("recording keeps the effect in the cue", r.get("ok") and [f["name"] for f in cue.get("fx") or []] == ["rainbow"],
                  str(cue.get("fx")))
            check("...and takes it off the programmer", not any(f.get("lib") == "rainbow" for f in e.fx), str(e.fx))
            e.act("select_all")
            e.act("set_colour", hex="#0000ff")
            e.act("record_cue", playback=1, name="Blue")
            e.act("cue_go", playback=1)
            check("GO starts the cue's effect", [f.get("cue_pb") for f in e.fx if f.get("lib") == "rainbow"] == [1], str(e.fx))
            e.act("cue_go", playback=1)
            check("the next cue (no effects) stops it", not any(f.get("lib") == "rainbow" for f in e.fx), str(e.fx))
            e.act("cue_go", playback=1, cue=1)
            e.act("playback_release", playback=1)
            check("releasing the playback stops its effects", not any(f.get("cue_pb") for f in e.fx), str(e.fx))
            snap = e.snapshot()
            pub = next(p for p in snap["playbacks"] if p["n"] == 1)["stack"][0]
            check("the cue list shows the cue's effects", pub.get("fx") == ["Rainbow"], str(pub))
            e.act("save_show", name="cuefx")
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e2.act("load_show", name="cuefx")
                check("a cue's effects survive save and load",
                      [f["name"] for f in e2.playbacks[0]["stack"][0].get("fx") or []] == ["rainbow"], "")
            finally:
                e2.shutdown()
            # part times: colour snaps, level keeps the cue's 4 s fade
            r = e.act("edit_cue", playback=1, cue=2, fade=4, times={"colour": 0})
            check("a cue part gets its own fade", r.get("ok") and e.playbacks[0]["stack"][1].get("times") == {"colour": 0.0}, str(r))
            r = e.act("edit_cue", playback=1, cue=2, times={"gobo": 1})
            check("an unknown part is refused", not r.get("ok"), str(r))
            t = [1000.0]
            e._clock = lambda: t[0]
            pb = e.playbacks[0]
            pb["index"] = 0
            pb["active"] = True
            pb["fade"] = None
            pb["target"] = None
            e.act("cue_go", playback=1, cue=2)
            t[0] += 2.0
            vals = e._pb_values(pb, t[0])[1]
            full = pb["stack"][1]["values"][1]
            colour_role = next(r for r in full if r in ("blue", "red", "green"))
            check("mid-fade: colour already there, level still on its way",
                  vals.get(colour_role) == full.get(colour_role), str((vals, full)))
            r = e.act("edit_cue", playback=1, cue=2, times={"colour": None})
            check("clearing the part puts it back on the cue's fade", "times" not in e.playbacks[0]["stack"][1], "")
            # a movement becomes a button in the first free slot
            r = e.act("quick_set", page=1, slot="free", button={"kind": "fx", "fx": "rainbow", "label": "Rainbow", "mode": "latch"})
            check("a button can go on the first free slot", r.get("ok") and r.get("id") == "q1-1", str(r))
            r = e.act("quick_set", page=1, slot="free", button={"kind": "flash"})
            check("...and the next one after it", r.get("id") == "q1-2", str(r))
        finally:
            e.shutdown()
    mv = (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8")
    check("the Move tab: tap tempo, make a button, record a cue", "BPM" in mv and '"Make a button"' in mv
          and "openCueDialog" in mv, "")
    dl = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("the cue list shows effects and part times", "cue-fx" in dl and "partTimes" in dl, "")


def test_change_type() -> None:
    """A patched light swaps fixture type in place: its number, position,
    groups and cues stay; the address stays when the new one fits."""
    print("change fixture type")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3, universe=1, address=1)
            e.act("set_place", head=2, x=1.5, y=3.0, z=-2.0)
            e.act("rename_head", head=2, name="DJ left")
            e.act("group_create", name="Front", heads=[2, 3])
            e.act("select_heads", heads=[2])
            e.act("set_colour", hex="#ff0000")
            e.act("record_cue", playback=1, name="Red")
            before = next(h for h in e.patch if h["head_no"] == 2)
            r = e.act("change_type", heads=[2], query="LED PARty RGBW")
            after = next(h for h in e.patch if h["head_no"] == 2)
            check("the light is the new type", r.get("ok") and "PARty" in after["model"], str(r))
            check("same number, place and name", (after["x"], after["y"], after["z"], after["name"])
                  == (before["x"], before["y"], before["z"], "DJ left"), str(after))
            check("still in its group", 2 in e.groups[0]["heads"], str(e.groups))
            check("its cue still drives it", 2 in e.playbacks[0]["stack"][0]["values"], "")
            check("moved to a free block when the new footprint doesn't fit",
                  r.get("moved") and after["address"] != before["address"], str((r.get("moved"), after["address"])))
            e.act("cue_go", playback=1)
            red_ch = after["address"] - 1 + after["map"].index("red")
            check("the cue plays on the new type", e.build_frames()[1][red_ch] > 0, "")
            r = e.act("change_type", heads=[3], query="LED PAR 4ch")
            h3 = next(h for h in e.patch if h["head_no"] == 3)
            check("same footprint: the address stays", r.get("ok") and not r.get("moved") and h3["address"] == 9,
                  str((r, h3["address"])))
            r = e.act("change_type", heads=[1], query="no such light xyz")
            check("an unknown type changes nothing", not r.get("ok") and e.patch[0]["model"] == "LED PAR 4ch", str(r))
            r = e.act("undo")
            check("undo puts the old type back", r.get("ok"), str(r))
        finally:
            e.shutdown()


def test_ready_versions() -> None:
    """The pre-gig check names what would bite; every save that changes a
    show keeps the one before, and an earlier version can be opened."""
    print("Ready? check and show versions")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            r = e.act("ready_check")
            check("an empty rig is not ready", r.get("ok") and r["worst"] == "bad"
                  and any("No lights" in i["text"] for i in r["items"]), str(r))
            e.act("add_heads", query="LED PAR 4ch", qty=2, universe=1, address=1)
            e.act("add_heads", query="LED PAR 4ch", qty=1, universe=1, address=20)
            e.patch[2]["address"] = 3                 # a clash, as a bad CSV import would leave
            r = e.act("ready_check")
            check("a DMX clash is named", any("clash" in i["text"] for i in r["items"] if i["level"] == "bad"), str(r["items"]))
            e.patch[2]["address"] = 20
            r = e.act("ready_check")
            texts = " ".join(i["text"] for i in r["items"])
            check("unsaved show, no cues and blind output are flagged", "never been saved" in texts
                  and "No cues" in texts and "BLIND" in texts, texts)
            undo_n = len(e._undo)
            check("the check changes nothing", len(e._undo) == undo_n, "")
            e.act("save_show", name="gig")
            r = e.act("show_versions", name="gig")
            check("the first save keeps no version", r.get("ok") and r["versions"] == [], str(r))
            e.act("save_show", name="gig")
            check("saving the same show again keeps no copy", e.act("show_versions", name="gig")["versions"] == [], "")
            e.act("rename_head", head=1, name="Changed")
            r = e.act("save_show", name="gig")
            vs = e.act("show_versions", name="gig")["versions"]
            check("a save that changed something keeps the one before", r.get("version_kept") and len(vs) == 1, str(vs))
            check("versions are not listed as shows", "versions" not in (e.snapshot().get("shows") or []), "")
            r = e.act("restore_version", name="gig", id=vs[0]["id"])
            check("an earlier version opens", r.get("ok") and e.patch[0]["name"] != "Changed", str(r))
            vs2 = e.act("show_versions", name="gig")["versions"]
            check("...and the newer one is kept as a version", len(vs2) == 2, str(vs2))
            r = e.act("restore_version", name="gig", id="../../etc")
            check("a bad version id is refused", not r.get("ok"), str(r))
            r = e.act("show_export", name="gig")
            check("export hands back the show file", r.get("ok") and r["filename"] == "gig.json"
                  and '"patch"' in r["text"], str(r.get("error")))
        finally:
            e.shutdown()


def test_review_fixes() -> None:
    """Found reviewing PR #28: undo after recording a cue lost the
    programmer's effect; cue effects outlived loading another show; one
    unpatched light dropped a whole cue effect; listing versions reloaded
    every screen; Ready? stayed red for the whole run after one error."""
    print("review of PR #28")
    import time as _time

    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=3)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("run_fx", name="rainbow")
            e.act("record_cue", playback=1, name="Rainbow")
            check("recording takes the effect off the programmer", not any(f.get("lib") == "rainbow" for f in e.fx), "")
            e.act("undo")
            check("undo of the record puts the programmer's effect back",
                  [f.get("cue_pb") for f in e.fx if f.get("lib") == "rainbow"] == [None], str(e.fx))
            e.act("record_cue", playback=1, name="Rainbow")
            e.act("save_show", name="other")
            e.act("cue_go", playback=1)
            check("GO starts the cue effect", any(f.get("cue_pb") == 1 for f in e.fx), str(e.fx))
            e.act("remove_heads", heads=[2])
            e.act("playback_release", playback=1)
            e.act("cue_go", playback=1)
            running = [f for f in e.fx if f.get("cue_pb") == 1]
            check("one unpatched light doesn't drop the effect from the others",
                  running and sorted(running[0]["heads"]) == [1, 3], str(running))
            e.playbacks[0]["stack"][0]["fx"] = []
            e.playbacks[0]["active"] = False
            e.act("save_show", name="plain")
            e.act("cue_go", playback=1, cue=1)
            e.playbacks[0]["stack"][0]["fx"] = [{"name": "rainbow", "heads": [1, 3], "params": {}}]
            e._cue_fx_start(e.playbacks[0], e.playbacks[0]["stack"][0])
            e.act("load_show", name="plain")
            check("loading another show stops the old cue effects", not any(f.get("cue_pb") for f in e.fx), str(e.fx))
            rev = e.act_rev
            e.act("show_versions", name="plain")
            e.act("show_export", name="plain")
            check("listing versions / exporting doesn't reload every screen", e.act_rev == rev, f"{rev} -> {e.act_rev}")
            e.output["errors"] = 3
            e.output["last_error"] = "boom"
            e.output["last_error_at"] = _time.monotonic()
            bad = lambda: any("DMX send error" in i["text"] for i in e.act("ready_check")["items"])  # noqa: E731
            check("a recent DMX error is flagged", bad(), "")
            e.output["last_error_at"] = _time.monotonic() - eng.READY_ERROR_WINDOW_S - 1
            check("...and an old one no longer is", not bad(), "")
        finally:
            e.shutdown()
    fj = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    check("a hold that slides off the chip doesn't swallow the next tap",
          'e.type === "pointerup"' in fj and "removeEventListener(\"click\", swallow" in fj, "")


def test_steady_dmx() -> None:
    """Several universes change on the same frame (ArtSync / E1.31 sync);
    every screen shares one look per tick; the effect-availability lookup
    that was 70% of a frame is answered once per role set."""
    print("steady DMX")
    import socket
    import tempfile
    import time as _time

    from app import artnet, fxlib, sacn
    from app import engine as eng

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    port = sock.getsockname()[1]
    try:
        s = artnet.ArtNetSender("127.0.0.1", port, dry_run=False)
        s.send(1, bytes(512))
        s.send(2, bytes(512))
        check("ArtSync goes after a tick of two universes", s.sync(2), "")
        pkts = [sock.recvfrom(2048)[0] for _ in range(3)]
        sync = pkts[-1]
        check("...as a 14-byte OpSync packet", len(sync) == 14 and sync[:8] == b"Art-Net\0"
              and struct.unpack_from("<H", sync, 8)[0] == 0x5200 and sync[11] == 14, sync.hex())
        check("one universe needs no sync", not s.sync(1), "")
        b = artnet.ArtNetSender("255.255.255.255", port, dry_run=False)
        check("never after broadcast ArtDmx", not b.sync(4), "")
        check("a dry run sends none", not artnet.ArtNetSender("127.0.0.1", port, dry_run=True).sync(4), "")
        s.close()
        pkt = sacn.build_sync(7000, 5)
        check("an E1.31 sync packet is 49 bytes with the extended vectors",
              len(pkt) == 49 and struct.unpack_from(">I", pkt, 18)[0] == 8
              and struct.unpack_from(">I", pkt, 40)[0] == 1 and pkt[44] == 5
              and struct.unpack_from(">H", pkt, 45)[0] == 7000
              and struct.unpack_from(">H", pkt, 16)[0] == 0x7000 | 33
              and struct.unpack_from(">H", pkt, 38)[0] == 0x7000 | 11, pkt.hex())
        ss = sacn.SacnSender("127.0.0.1", port, dry_run=False, sync_universe=7000)
        ss.send(1, bytes(512))
        data = sock.recvfrom(2048)[0]
        check("sACN data names the sync universe", struct.unpack_from(">H", data, 109)[0] == 7000, "")
        check("...and the sync follows", ss.sync(1) and len(sock.recvfrom(2048)[0]) == 49, "")
        check("no sync universe, no sync", not sacn.SacnSender("127.0.0.1", port, dry_run=False).sync(3), "")
        ss.close()
    finally:
        sock.close()
    tb = (ROOT / "web" / "app" / "topbar.js").read_text(encoding="utf-8")
    check("the status bar shows frame timing and only recent errors",
          "#st-timing" in tb and "recent_error" in tb, "")
    check("CI runs the frame-timing check",
          "tools/frametiming.py" in (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"), "")
    fxlib._available_for.cache_clear()
    for _ in range(50):
        fxlib.available(["red", "green", "blue", "dimmer"])
    info = fxlib._available_for.cache_info()
    check("fifty asks for one role set work it out once", info.misses == 1 and info.hits == 49, str(info))
    got = fxlib.available(["red"])
    got.append("junk")
    check("the cached answer can't be spoiled by a caller", "junk" not in fxlib.available(["red"]), "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("select_all")
            e.act("set_intensity", level=100)
            calls = [0]
            real = e.look_rows

            def counted():
                calls[0] += 1
                return real()
            e.look_rows = counted
            texts = {e.look_text() for _ in range(3)}
            check("three screens asking at once share one look", calls[0] == 1 and len(texts) == 1, str(calls))
            _time.sleep(e.LOOK_SHARE_S + 0.01)
            e.act("set_intensity", level=10)
            check("...and a later ask sees the change", '"a":0.1' in e.look_text(), "")
            check("no timing dot while the output is stopped", e._output_public()["timing"] is None, "")
            e.output["running"] = True
            e._gaps.extend([25.0] * 20)
            check("even frames read as steady", e._output_public()["timing"]["state"] == "steady", "")
            e._gaps.extend([25.0] * 10 + [120.0] * 10)
            t = e._output_public()["timing"]
            check("long gaps read as stuttering", t["state"] == "stuttering" and t["late"] == 10, str(t))
            e.output["running"] = False
            pub = e._output_public()
            check("the monotonic error time stays on the server", "last_error_at" not in pub
                  and pub["recent_error"] is False, str(pub))
        finally:
            e.shutdown()


def test_tablets() -> None:
    """A tablet at a gig: the screen doesn't sleep, the console installs as
    a full-screen app, and a dead link (slept, changed Wi-Fi) is noticed in
    seconds and re-synced with a full snapshot."""
    print("tablets")
    web = ROOT / "web"
    man = json.loads((web / "manifest.webmanifest").read_text(encoding="utf-8"))
    sizes = {i["sizes"] for i in man["icons"] if i["type"] == "image/png"}
    check("the manifest opens full screen with 192 and 512 px icons",
          man["display"] == "fullscreen" and {"192x192", "512x512"} <= sizes
          and any("maskable" in i.get("purpose", "") for i in man["icons"]), str(man))
    check("every icon the manifest names exists",
          all((web / i["src"].lstrip("/")).is_file() for i in man["icons"]), "")
    html = (web / "index.html").read_text(encoding="utf-8")
    check("the page links the manifest and a home-screen icon",
          'rel="manifest"' in html and 'rel="apple-touch-icon"' in html
          and (web / "icons" / "icon-180.png").is_file(), "")
    from app import main as main_mod
    check("the manifest is served as a manifest", main_mod._MIME.get(".webmanifest") == "application/manifest+json", "")
    tj = (web / "app" / "tablet.js").read_text(encoding="utf-8")
    check("the wake lock is taken, and taken again when the tab comes back",
          'wakeLock.request("screen")' in tj and "visibilitychange" in tj and "beforeinstallprompt" in tj, "")
    api = (web / "app" / "api.js").read_text(encoding="utf-8")
    check("a quiet stream is dropped and re-opened; coming back online retries at once",
          "watchdog" in api and '"online"' in api and "wakeWait" in api, "")
    dj = (web / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("Settings has keep-awake and install", "wakeRow()" in dj and "installRow()" in dj, "")


def test_room_fit() -> None:
    """Shrinking or reshaping the room pulls rigging (and the lights hung
    on it) back inside; a truss keeps its length when it fits."""
    print("room reshape keeps rigging inside")
    from app import engine as eng
    from app import venue as venue_mod

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_room", width=20, depth=20, height=8)
            r = e.act("venue_add", item={"type": "rigging", "kind": "truss", "a": [4, 6, 15], "b": [8, 6, 15]})
            check("a truss near the back", r.get("ok"), str(r))
            rid = e.venue["rigging"][-1]["id"]
            e.act("add_heads", query="LED PAR 4ch", qty=1)
            r = e.act("attach_heads", heads=[1], rig=rid)
            check("a light hangs on it", r.get("ok"), str(r))
            r = e.act("venue_room", width=10, depth=10, height=5)
            rig = next(x for x in e.venue["rigging"] if x["id"] == rid)
            b = venue_mod.bounds(e.venue)
            ok_in = all(b["x0"] <= p[0] <= b["x1"] and b["z0"] <= p[2] <= b["z1"] and p[1] <= b["h"]
                        for p in (rig["a"], rig["b"]))
            check("the truss is back inside the smaller room", r.get("ok") and ok_in, str((rig, b)))
            check("...still 4 m long", abs(abs(rig["b"][0] - rig["a"][0]) - 4.0) < 0.01, str(rig))
            check("...and the summary says so", "rigging back inside" in r.get("summary", ""), r.get("summary"))
            h = e.patch[0]
            check("the light came with it", b["z0"] <= h["z"] <= b["z1"] and h["y"] <= b["h"], str((h["x"], h["y"], h["z"])))
            r = e.act("venue_room", width=10, depth=10)
            check("a resize that leaves it inside moves nothing", "rigging" not in r.get("summary", ""), r.get("summary"))
        finally:
            e.shutdown()
    vp = (ROOT / "web" / "app" / "venuepanel.js").read_text(encoding="utf-8")
    check("an empty desk asks which venue at start-up", "Where are you playing tonight?" in vp
          and "jarvis.venuepick" in vp, "")


def test_look_types() -> None:
    """A look keeps the kinds of light it was made on, so at another venue
    (other head numbers) it plays on every light of those kinds."""
    print("looks on any light of these types")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=2)
            e.act("select_all")
            e.act("set_colour", hex="#ff0000")
            r = e.act("record_preset", name="Red wash")
            look = e.presets[0]
            check("a look knows its kinds of light", r.get("ok") and look.get("types"), str(look.get("types")))
            e.act("clear_programmer")
            e.act("add_heads", query="LED PARty RGBW", qty=3)     # another venue: heads 3-5
            e.act("remove_heads", heads=[1, 2])
            e.act("clear_selection")
            r = e.act("include_preset", preset=look["n"])
            check("its lights aren't here: it plays on the same kind", r.get("ok") and r.get("heads") == 3, str(r))
            e.act("clear_programmer")
            e.act("select_heads", heads=[e.patch[0]["head_no"]])
            r = e.act("include_preset", preset=look["n"], on="types")
            check("'every light of these types' ignores the selection", r.get("ok") and r.get("heads") == 3, str(r))
        finally:
            e.shutdown()
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the Looks tab has a search and 'Play on every …'", "#look-search" in pj and 'on: "types"' in pj, "")


def test_more_models() -> None:
    """Lights that used to fall back to the generic model: mirror
    scanners and flower / derby effects have models of their own, and
    lasers, hazers and studio lights are recognised by name."""
    print("more 3D models")
    from app import fixture_kind as fk

    def kind(man, model, roles):
        return fk.describe({"manufacturer": man, "model": model, "map": roles})["type"]
    check("a mirror scanner", kind("Chauvet", "Intimidator Scan LED 300", ["pan", "tilt", "gobo", "shutter"]) == "scanner", "")
    check("a derby / flower effect", kind("American DJ", "Quad Gem DMX", ["red", "green", "blue", "white", "raw"]) == "effect", "")
    check("a laser by name", kind("Laserworld", "EL-400RGB MK2", ["raw", "raw", "raw"]) == "laser", "")
    check("a hazer by name", kind("American DJ", "Entour Faze", ["raw", "raw"]) == "atmos", "")
    check("a studio COB light", kind("Aputure", "LS 600D Pro", ["dimmer", "raw"]) == "fresnel", "")
    check("a moving head called Acrobat is still a moving head",
          kind("Showtec", "Acrobat", ["pan", "tilt", "gobo", "dimmer"]) == "moving_spot", "")
    check("a scanning laser is a laser", kind("Shehds", "Constellaser 12W Scan Laser", ["raw"]) != "scanner", "")
    js = (ROOT / "web" / "js" / "stage" / "models.js").read_text(encoding="utf-8")
    block = js[js.index("const BUILDERS = {"):]
    block = block[:block.index("};")]
    keys = set(re.findall(r"^\s+([a-z_0-9]+)(?::|,)", block, re.M))
    missing = sorted(set(fk.TYPES) - keys)
    check("every light type has a 3D builder", not missing, str(missing))
    st = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("a derby turns while it is lit", "sk.spin" in st, "")


def test_rdm() -> None:
    """RDM over Art-Net against a fake node: the Table of Devices, each
    light's model / address / footprint / mode, a readdress, and the
    comparison with the patch."""
    print("RDM discovery")
    import socket
    import tempfile

    from app import engine as eng
    from app import rdm

    lights = {
        bytes.fromhex("02A012345678"): {"man": b"Chauvet", "model": b"Intimidator Spot 360", "addr": 1, "fp": 14,
                                         "pers": 2, "mode": b"14-Channel"},
        bytes.fromhex("02A0DEADBEEF"): {"man": b"Eurolite", "model": b"LED PARty RGBW", "addr": 40, "fp": 6,
                                         "pers": 1, "mode": b"6 Channel"},
    }
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.settimeout(0.2)
    stop = threading.Event()
    reqs: list[tuple[int, int]] = []

    def node():
        while not stop.is_set():
            try:
                pkt, peer = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if pkt[8:10] == struct.pack("<H", rdm.TODREQUEST_OP):
                reqs.append((pkt[21], pkt[23]))          # Net, AdCount
                sock.sendto(rdm.build_tod_data(0, pkt[24], list(lights)), peer)
                continue
            got = rdm.parse_artrdm(pkt)
            msg = rdm.parse_rdm(got[1]) if got else None
            if not msg or msg["dest"] not in lights:
                continue
            L = lights[msg["dest"]]
            if msg["cc"] == rdm.SET and msg["pid"] == rdm.PID_DMX_START_ADDRESS:
                L["addr"] = struct.unpack(">H", msg["data"])[0]
                reply = rdm.build_rdm(msg["src"], rdm.SET_RESPONSE, msg["pid"], b"", tn=msg["tn"],
                                      src=msg["dest"], response_type=rdm.ACK)
            else:
                data = {rdm.PID_DEVICE_INFO: struct.pack(">HHHIHBBHHB", 0x0100, 7, 0x0101, 1, L["fp"], L["pers"],
                                                         3, L["addr"], 0, 0),
                        rdm.PID_MANUFACTURER_LABEL: L["man"], rdm.PID_DEVICE_MODEL_DESCRIPTION: L["model"],
                        rdm.PID_DEVICE_LABEL: b"",
                        rdm.PID_DMX_PERSONALITY_DESCRIPTION: bytes([L["pers"]]) + struct.pack(">H", L["fp"]) + L["mode"],
                        }.get(msg["pid"])
                if data is None:
                    continue
                reply = rdm.build_rdm(msg["src"], rdm.GET_RESPONSE, msg["pid"], data, tn=msg["tn"],
                                      src=msg["dest"], response_type=rdm.ACK)
            sock.sendto(rdm.build_artrdm(reply, got[0]), peer)

    t = threading.Thread(target=node, daemon=True)
    t.start()
    try:
        msg = rdm.build_rdm(bytes(6), rdm.GET, rdm.PID_DEVICE_INFO, b"\x01\x02", tn=9)
        back = rdm.parse_rdm(msg)
        check("an RDM message round-trips (checksum incl. the start code)", back and back["tn"] == 9
              and back["data"] == b"\x01\x02", str(back))
        check("a broken checksum is refused", rdm.parse_rdm(msg[:-1] + bytes([msg[-1] ^ 1])) is None, "")
        found = rdm.discover([1], host="127.0.0.1", port=port, timeout=0.6, per_request=0.3)
        devs = found["devices"]
        check("both lights answer", len(devs) == 2, str(found))
        spot = next((d for d in devs if d["uid"] == "02A0:12345678"), {})
        check("model, address, footprint and mode come back",
              spot.get("manufacturer") == "Chauvet" and spot.get("model") == "Intimidator Spot 360"
              and spot.get("address") == 1 and spot.get("footprint") == 14 and spot.get("mode") == "14-Channel", str(spot))
        reqs.clear()
        rdm.discover(list(range(1, 41)), host="127.0.0.1", port=port, timeout=0.6, per_request=0.1)
        check("more than 32 universes: one ToD request per 32", sorted(c for _, c in reqs) == [8, 32], str(reqs))
        reqs.clear()
        rdm.discover([250, 260], host="127.0.0.1", port=port, timeout=0.6, per_request=0.1)
        check("universes on two Art-Net Nets: one request per Net, never an error",
              sorted(reqs) == [(0, 1), (1, 1)], str(reqs))
        reqs.clear()
        rdm.discover([1], host="127.0.0.1", port=port, net=3, timeout=0.4, per_request=0.1)
        check("the configured DMX Net is asked", reqs and reqs[0][0] == 3, str(reqs))
        mj = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        check("the RDM route passes the DMX Net", mj.count("net=config.DMX_NET") >= 2, "")
        r = rdm.set_address("02A0:DEADBEEF", 1, 101, host="127.0.0.1", port=port)
        check("a light can be readdressed from the desk", r.get("ok") and lights[bytes.fromhex("02A0DEADBEEF")]["addr"] == 101, str(r))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "f.db"
            fixtures.seed_generics(db)
            e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e.act("add_heads", query="LED PAR 4ch", qty=1, universe=1, address=1)
                e.act("add_heads", query="LED PAR 4ch", qty=1, universe=1, address=200)
                devs = rdm.discover([1], host="127.0.0.1", port=port, timeout=0.6, per_request=0.3)["devices"]
                r = e.act("rdm_compare", devices=devs, universes=[1])
                st = {d["uid"]: d["status"] for d in r.get("devices") or []}
                check("a patched light the RDM light disagrees with is flagged", st.get("02A0:12345678") == "different", str(r))
                check("an unpatched RDM light is new", st.get("02A0:DEADBEEF") == "new", str(st))
                check("a patched light nobody answered for is silent",
                      [x["head"] for x in r.get("silent") or []] == [2], str(r.get("silent")))
            finally:
                e.shutdown()
    finally:
        stop.set()
        t.join(1)
        sock.close()
    fj = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    check("the fixture menu asks the lights", "openRdm" in fj and "/api/console/rdm" in fj, "")


def test_aim_debug() -> None:
    """Bugs found by aiming every kind of mover at the floor: a Wave 360
    mode with fine pan but no fine tilt was sent a 16-bit tilt (pinned at
    full tilt, pointing at the roof); a light high on a tower stood upright
    and couldn't reach the floor; an unreachable spot left the light at
    home (pointing up); a scanner aimed as if its beam left upwards."""
    print("aiming debug (towers, scanners, 8-bit tilt)")
    import math

    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        for src, key in (("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"),
                         ("qlc", "Chauvet/Chauvet-Intimidator-Scan-360.qxf"),
                         ("ofl", "chauvet-dj/intimidator-spot-260.json")):
            fixtures.store_parsed(db, fixlib.load(src, key), src)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("floor_safe", movement=False)
            truss = next(r["id"] for r in e.venue["rigging"] if r["kind"] == "truss")
            tower = next(r["id"] for r in e.venue["rigging"] if r["kind"] == "tower")
            wave = e.act("add_heads", query="Intimidator Wave 360", qty=1)["heads"][0]
            e.act("attach_heads", heads=[wave], rig=truss, stance="hang")
            h = e._head(wave)
            e.act("aim_at", x=0, y=0, z=6, heads=[wave])
            tilt_ch = h["address"] - 1 + h["map"].index("tilt")
            frame = e.build_frames()[h["universe"]]
            fp, ft = e._aim_solve(h, 0, 0, 6)[:2]
            check("a mode with fine pan but no fine tilt gets an 8-bit tilt",
                  abs(frame[tilt_ch] - round(ft * 255)) <= 1 and "tilt_fine" not in h["map"],
                  str((frame[tilt_ch], round(ft * 255))))
            row = next(r for r in e._looks() if r["n"] == wave)
            check("...and the 3D view gets the same tilt (not full tilt)", abs(row["tilt"] - ft) < 0.01
                  and all(abs(c["tilt"] - ft) < 0.01 for c in row.get("cells") or []), str(row.get("tilt")))
            spot = e.act("add_heads", query="Intimidator Spot 260", qty=1)["heads"][0]
            e.act("attach_heads", heads=[spot], rig=tower)
            check("a mover high on a tower hangs", e._head(spot)["stance"] == "hang", str(e._head(spot).get("stance")))
            e.act("set_place", head=spot, x=-3, y=0, z=3, stance="stand")
            r = e.act("aim_at", x=-3, y=0, z=3.2, heads=[spot])        # right at its own feet
            check("an unreachable spot: it points as close as it can, not at home",
                  r.get("ok") and spot in (r.get("clamped") or []) and e.programmer.get(spot, {}).get("tilt"),
                  str(r.get("summary")))
            scan = e.act("add_heads", query="Intimidator Scan 360", qty=1)["heads"][0]
            e.act("attach_heads", heads=[scan], rig=truss, stance="hang")
            sh = e._head(scan)
            got = e._aim_solve(sh, sh["x"], 0, sh["z"] + 8)      # ~28 degrees down: inside its mirror's 39
            check("a hung scanner reaches the floor in front of it", got is not None and len(got) == 4, str(got))
            p_deg, t_deg = got[2], got[3]
            ly = -math.sin(math.radians(t_deg))
            check("...by dipping its mirror, not by pointing up", ly > 0, f"pan {p_deg:.1f} tilt {t_deg:.1f}")
        finally:
            e.shutdown()


def test_rig_tools() -> None:
    """Bars can be turned, resized, stood up and hung from the ceiling; a
    rig is never left outside the walls or through the ceiling; a resized
    room's zones scale with it; a light dragged up high hangs."""
    print("rig tools")
    from app import engine as eng
    from app import venue as V

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_room", width=4, depth=4, height=3)
            r = e.act("venue_add", item={"type": "rigging", "kind": "truss", "a": [-3, 2.8, 2], "b": [3, 2.8, 2]})
            rid = r["item"]["id"]

            def g():
                return next(x for x in e.venue["rigging"] if x["id"] == rid)
            b = V.bounds(e.venue)
            inside = lambda rr: all(b["x0"] <= p[0] <= b["x1"] and b["z0"] <= p[2] <= b["z1"] and p[1] <= b["h"]  # noqa: E731
                                    for p in (rr["a"], rr["b"]))
            check("a 6 m bar added to a 4 m room stays inside it", inside(g()), str(g()))
            e.act("venue_update", id=rid, changes={"a": [4, 6, 9], "b": [8, 6, 9]})
            check("dragged out through the wall and ceiling, it stays in", inside(g()), str(g()))
            e.act("venue_rig", id=rid, length=2)
            ln = lambda rr: sum((rr["b"][i] - rr["a"][i]) ** 2 for i in range(3)) ** 0.5  # noqa: E731
            check("its length can be set", abs(ln(g()) - 2) < 0.01, str(g()))
            before = g()
            e.act("venue_rig", id=rid, turn=90)
            now = g()
            check("it turns about its middle", abs(now["a"][0] - now["b"][0]) < 0.01 and abs(now["a"][2] - now["b"][2]) > 1.9
                  and abs((now["a"][0] + now["b"][0]) / 2 - (before["a"][0] + before["b"][0]) / 2) < 0.01, str(now))
            e.act("venue_rig", id=rid, orient="vertical")
            check("it stands up as a pole on the floor", V.is_vertical(g()) and min(g()["a"][1], g()["b"][1]) == 0, str(g()))
            e.act("venue_rig", id=rid, orient="horizontal")
            e.act("venue_rig", id=rid, ceiling=True)
            check("it hangs just under the ceiling", abs(g()["a"][1] - (3 - 0.15 - 0.05)) < 0.06, str(g()))
            e.act("venue_template", name="club")
            e.act("venue_room", width=5, depth=5, height=3)
            b = V.bounds(e.venue)
            zs = e.venue["zones"]
            check("a smaller room: its zones scale into it, not squashed",
                  zs and all(b["x0"] - 0.01 <= q[0] <= b["x1"] + 0.01 and b["z0"] - 0.01 <= q[1] <= b["z1"] + 0.01
                             for z in zs for q in z["points"])
                  and all(max(q[1] for q in z["points"]) - min(q[1] for q in z["points"]) > 0.2 for z in zs), str(zs))
            e.act("add_heads", query="LED PAR 4ch", qty=1)
            n = e.patch[-1]["head_no"]
            e.act("set_place", head=n, x=0, y=0, z=1)
            e.act("set_place", head=n, x=0, y=2.6, z=1)
            h = e._head(n)
            check("a light dragged up high hangs", (h.get("stance") == "hang") or (not h.get("stance") and h.get("kind") == "truss"),
                  str((h.get("stance"), h.get("kind"))))
        finally:
            e.shutdown()
    ed = (ROOT / "web" / "js" / "stage" / "editor.js").read_text(encoding="utf-8")
    check("the editor turns rigs, keeps them in the room and frames them", 's.type === "rig")' in ed
          and "_rigDelta" in ed and "_frameRig" in ed, "")


def test_virtual_dimmer() -> None:
    """A light with colour emitters and no dimmer (a 3-channel RGB PAR):
    Full, the fader, cues, flash buttons and the master scale its colour
    (white with none set).  "Full" used to do nothing on them.  And the
    mode picked by default is one that can control the light."""
    print("virtual dimmer and default modes")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        for q in ("beamZ BAC302", "Ayra Compar Kit 1"):
            r = fixlib.search(q, limit=1)[0]
            fixtures.store_parsed(db, fixlib.load(r.get("src"), r["key"]), r.get("src"))
        fixtures.store_parsed(db, fixlib.load("ofl", "magicfx/psyco2jet.json"), "ofl")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="BAC302", qty=1)
            h = e.patch[0]
            check("the RGB PAR is patched in a colour mode with no dimmer", h["map"][:3] == ["red", "green", "blue"]
                  and "dimmer" not in h["map"], str(h["map"]))
            f = lambda: list(e.build_frames()[1][:3])  # noqa: E731
            e.act("select_all")
            r = e.act("set_intensity", level=100)
            check("Full lights it white", r.get("heads") == 1 and f() == [255, 255, 255], str((r.get("summary"), f())))
            e.act("set_colour", hex="#ff0000")
            e.act("set_intensity", level=50)
            check("the fader scales its colour", f() == [127, 0, 0], str(f()))
            e.act("record_cue", playback=1, name="half red")
            e.act("cue_go", playback=1)
            check("a cue plays it back at that level", f() == [127, 0, 0], str(f()))
            e.act("playback_release", playback=1)
            e.act("master", level=0)
            e.act("select_all")
            e.act("set_intensity", level=100)
            check("the grand master still takes it down", f() == [0, 0, 0], str(f()))
            e.act("master", level=100)
            e.act("clear_programmer")
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "target": {"all": True}})
            e.act("quick_press", id="q1-1")
            check("a flash button lights it", f() == [255, 255, 255], str(f()))
            e.act("quick_press", id="q1-1", down=False)
            e.act("select_all")
            e.act("set_colour", hex="#00ff00")
            check("a colour on its own still lights it as before", f() == [0, 255, 0], str(f()))
            e.act("add_heads", query="Compar Kit 1", qty=1)
            check("a light's 1-channel 'shows' mode is not the default", e.patch[-1]["channels"] > 1,
                  f"{e.patch[-1]['mode']} ({e.patch[-1]['channels']} ch)")
            e.act("add_heads", query="Psyco2Jet", qty=1)
            check("an SFX machine's default mode can fire", "fx_fire" in e.patch[-1]["map"], str(e.patch[-1]["map"]))
        finally:
            e.shutdown()


def test_cue_list_modes() -> None:
    """Recording over a cue: Replace makes it the programmer, Merge adds the
    programmer's changes and keeps the rest, Insert puts a new cue before
    it.  Updating a cue used to rename it "Cue 2" and zero its fade."""
    print("cue list (merge / replace / insert; update keeps name and times)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "c.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=2)
            e.act("select_heads", heads=[1, 2])
            e.act("set_colour", hex="#ff0000")
            e.act("record_cue", playback=1, name="Red", fade=3)
            e.act("select_heads", heads=[1])
            e.act("set_colour", hex="#0000ff")
            e.act("record_cue", playback=1, name="Blue one", fade=2)
            stack = e.playbacks[0]["stack"]
            e.act("select_heads", heads=[2])
            e.act("set_colour", hex="#00ff00")
            r = e.act("record_cue", playback=1, cue=2, mode="merge")
            c2 = e.playbacks[0]["stack"][1]
            check("merge adds the programmer's changes and keeps the rest of the cue",
                  r.get("ok") and c2["values"][1]["blue"] == 255 and c2["values"][2]["green"] == 255, str(c2["values"]))
            check("...and keeps its name and fade", c2["name"] == "Blue one" and c2["fade_s"] == 2.0, str(c2))
            e.act("select_heads", heads=[2])
            e.act("set_colour", hex="#ffffff")
            e.act("record_cue", playback=1, cue=2, mode="replace")
            c2 = e.playbacks[0]["stack"][1]
            check("replace makes it exactly the programmer (name and fade still kept)",
                  list(c2["values"]) == [2] and c2["name"] == "Blue one" and c2["fade_s"] == 2.0, str(c2))
            e.act("select_heads", heads=[1])
            e.act("set_colour", hex="#ff00ff")
            e.act("record_cue", playback=1, cue=2, mode="insert", name="Pink")
            names = [c["name"] for c in e.playbacks[0]["stack"]]
            check("insert puts a new cue before it and renumbers", names == ["Red", "Pink", "Blue one"]
                  and [c["n"] for c in e.playbacks[0]["stack"]] == [1, 2, 3], str(names))
            check("an unknown mode is refused", not e.act("record_cue", playback=1, cue=1, mode="squash").get("ok"), "")
            del stack
        finally:
            e.shutdown()
    dj = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("the cue list drags to reorder, Update offers merge / replace, the record dialog says where",
          '"dragstart"' in dj and 'mode: "merge"' in dj and "Insert before" in dj and "New cue" in dj, "")


def test_multi_head() -> None:
    """A Wave 360 has four tilts and four RGBW cells on one address; they
    used to share one value.  Each head can now be set on its own, cues and
    looks keep it, and effects run across the heads as if each were a light."""
    print("multi-head lights (Wave 360: per-head colour / tilt, effects across heads)")
    import time as _t

    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "w.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Wave 360", mode="33 ch.", qty=1, universe=1, address=1)
            e.act("select_all")
            e.act("set_colour", hex="#0000ff")
            e.act("set_colour", hex="#ff0000", cell=2)
            e.act("set_attribute", attribute="tilt", value=200, cell=[3])
            w = e.build_frames()[1]
            cells = [list(w[10 + 4 * k:14 + 4 * k]) for k in range(4)]
            check("one head of the light gets its own colour", cells[1] == [255, 0, 0, 0]
                  and cells[0] == cells[2] == cells[3] == [0, 0, 255, 0], str(cells))
            check("...and its own tilt", list(w[4:8]) == [0, 0, 200, 0], str(list(w[4:8])))
            r = e.act("set_attribute", attribute="pan", value=100, cell=[2])
            check("a channel the light has once (pan) just sets the light", r.get("ok") and e.programmer[1].get("pan") == 100, str(r))
            e.act("record_cue", playback=1)
            e.act("clear_programmer")
            e.act("playback_activate", playback=1)
            e.act("cue_go", playback=1)
            w = e.build_frames()[1]
            check("a cue keeps each head's own values", list(w[14:18]) == [255, 0, 0, 0] and w[6] == 200,
                  str((list(w[14:18]), w[6])))
            e.act("playback_release", playback=1)
            e.act("select_all")
            e.act("set_colour", hex="#00ff00")
            w = e.build_frames()[1]
            check("setting the whole light again covers every head", all(list(w[10 + 4 * k:13 + 4 * k]) == [0, 255, 0] for k in range(4)),
                  str([list(w[10 + 4 * k:13 + 4 * k]) for k in range(4)]))
            e.act("set_intensity", level=100)
            r = e.act("run_fx", name="colour_chase", params={"speed": 0.5}, across=True)
            _t.sleep(0.1)
            w = e.build_frames()[1]
            heads = [tuple(w[10 + 4 * k:13 + 4 * k]) for k in range(4)]
            check("an effect runs across the heads (a different colour on each)", r.get("ok") and len(set(heads)) == 4, str(heads))
            e.act("stop_fx")
            e.act("set_colour", hex="#ff0000", cell=[1])
            e.act("record_preset", name="One red")
            check("a look keeps a single head's colour", "red@1" in e.presets[-1]["values"], str(e.presets[-1]["values"]))
            e.act("set_attribute", attribute="tilt", value=128)
            e.act("run_fx", name="tilt_bounce", params={"size": 40, "speed": 0.5}, across=True)
            _t.sleep(0.3)
            w = e.build_frames()[1]
            check("a tilt wave runs through the heads (four different tilts)", len(set(w[4:8])) >= 3, str(list(w[4:8])))
            e.act("stop_fx")
            e.act("set_colour", hex="#00ff00", cell=[4])
            row = next(r for r in e._looks() if r["n"] == 1)
            check("the 3D view gets each head's own colour", row.get("cells") and len(row["cells"]) == 4
                  and row["cells"][3]["hex"] == "#00ff00" and row["cells"][0]["hex"] == "#ff0000", str(row.get("cells")))
            check("...and draws the light as that many heads", e.snapshot()["patch"][0]["body"].get("heads") == 4, "")
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    ac = (ROOT / "web" / "app" / "actions.js").read_text(encoding="utf-8")
    md = (ROOT / "web" / "js" / "stage" / "models.js").read_text(encoding="utf-8")
    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("a multi-head 3D model: heads on a bar, each tilting and coloured on its own",
          "function multiHead" in md and "sk.cells" in sj and "L.cells" in sj, "")
    check("the programmer offers Heads: All 1 2 3 4 and 'across each light's heads'",
          "renderProgCells" in js and "Across each light's heads" in js and "state.cells" in ac, "")


def test_dmx_clashes() -> None:
    """Two lights on overlapping channels fight each other.  The load-time
    check missed a light that started INSIDE an earlier one (laser A at 20
    with 13 channels, laser B at 25): now every clash is listed, and one tap
    moves a light to the first free block of addresses."""
    print("DMX map (clashes found both ways; move to free)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "d.db"
        fixtures.store_parsed(db, fixlib.load("jarvis", "laserworld/beambar-10b-mk3"), "jarvis:laserworld/beambar-10b-mk3")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="BeamBar 10B MK3", qty=2)
            a, b = e.patch
            # the way a clash gets in: a show saved elsewhere / an older check
            a["universe"], a["address"] = 1, 20
            b["universe"], b["address"] = 1, 25
            cl = e._patch_clashes()
            check("a light starting inside another is a clash (20+13 ch vs 25)",
                  len(cl) == 1 and {cl[0]["a"], cl[0]["b"]} == {a["head_no"], b["head_no"]}
                  and cl[0]["from"] == 25 and cl[0]["to"] == 32, str(cl))
            check("...and the console gets it in the snapshot", e.snapshot().get("clashes"), "")
            r = e.act("set_address", head=a["head_no"], universe=1, address=30)
            check("setting an address onto another light is refused", not r.get("ok") and "overlaps" in r.get("error", ""), str(r))
            r = e.act("patch_move_free", head=b["head_no"])
            nb = e._head(b["head_no"])
            check("one tap moves it to the first free block (1-19 is free: 1.001)",
                  r.get("ok") and not e._patch_clashes() and nb["address"] == 1, str((r, nb["address"])))
            r = e.act("patch_move_free", head=a["head_no"])
            check("...and the next one goes right after it", r.get("ok") and e._head(a["head_no"])["address"] == 14
                  and not e._patch_clashes(), str(e._head(a["head_no"])["address"]))
        finally:
            e.shutdown()
    fx = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    check("the list warns about clashes and opens a DMX map", "openDmxMap" in fx and '"patch_move_free"' in fx
          and "fx-clash" in fx, "")


def test_my_venues() -> None:
    """A venue is saved on its own - room (any shape), rigging (horizontal
    truss, vertical poles), zones and, if wanted, the lights in it - and
    opened again at the next gig there, with or without its lights."""
    print("My venues (save / open / delete; room shape; poles; with or without lights)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "v.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", key="club")
            e.act("venue_room", outline=[[-7, 0], [7, 0], [7, 6], [3, 6], [3, 12], [-7, 12]])
            r = e.act("venue_add", item={"kind": "pipe", "name": "Pole", "a": [2, 0, 4], "b": [2, 3, 4]})
            pole = next((x for x in e.venue["rigging"] if x["name"] == "Pole"), None)
            check("a vertical pole is a pipe standing up", r.get("ok") and pole and pole["a"][1] == 0 and pole["b"][1] == 3,
                  str(pole))
            e.act("add_heads", query="LED PARty RGBW", qty=6)
            r = e.act("venue_save", name="Tom's Bar")
            check("save the venue with its lights", r.get("ok") and (tmp / "s" / "venues" / "Toms Bar.json").exists(), str(r))
            lst = e.snapshot().get("venues") or []
            check("...it is listed (name, shape, rigging, lights)",
                  lst and lst[0]["name"] == "Tom's Bar" and lst[0]["lights"] == 6, str(lst))
            check("...and a saved venue is not listed as a show", "venues" not in e._show_names(), str(e._show_names()))
            e.act("venue_template", key="ballroom")
            e.act("patch_clear")
            r = e.act("venue_open", name="Tom's Bar", lights=False)
            check("open it again: the room comes back (room only keeps the patch)",
                  r.get("ok") and any(x["name"] == "Pole" for x in e.venue["rigging"]) and not e.patch, str(r))
            e.act("venue_open", name="tom's bar")
            check("...or with its lights (name in any case)", len(e.patch) == 6, str(len(e.patch)))
            check("the room shape survives the round trip", len((e.venue.get("room") or {}).get("outline") or []) == 6,
                  str((e.venue.get("room") or {}).get("outline")))
            check("an unknown venue is refused", not e.act("venue_open", name="Nowhere").get("ok"), "")
            e.act("venue_delete", name="Tom's Bar")
            check("delete a saved venue", not (e.snapshot().get("venues") or []), "")
        finally:
            e.shutdown()
    vp = (ROOT / "web" / "app" / "venuepanel.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    pbj = (ROOT / "web" / "app" / "playbacks.js").read_text(encoding="utf-8")
    stj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    tbj = (ROOT / "web" / "app" / "topbar.js").read_text(encoding="utf-8")
    check("less clutter: used playbacks + one empty, short 3D labels, a plain-words status bar",
          "firstEmpty" in pbj and "+ Record a cue" in pbj and "const clash = placed.some" in stj
          and "safe to program, nothing reaches the lights" in tbj, "")
    fxj = (ROOT / "web" / "app" / "fxpanel.js").read_text(encoding="utf-8")
    check("SFX: CO2 hold + 0.5 / 1 / 3 s shots, confetti only after a 1 s hold, haze levels",
          "HOLD TO FIRE" in fxj and "[0.5, 1, 3]" in fxj and "confettiButton" in fxj
          and "}, 1000);" in fxj and '"prog-haze"' in fxj, "")
    check("Arrange has Venues, Draw room shape, + Truss / + Pole / + Pipe up front",
          all(k in html for k in ('id="vt-venues"', 'id="vt-truss"', 'id="vt-pole"', 'id="vt-pipe"', "Draw room shape"))
          and '"venue_save"' in vp and '"venue_open"' in vp, "")


def test_big_rig_groups() -> None:
    """With 20+ lights, groups Jarvis makes by itself (by kind of light and
    by the truss they hang on), odd / even / left / right splits of any
    selection, buttons aimed at those groups, a folded list and a box
    select on the stage."""
    print("grouping for big rigs (auto groups, splits, folded list, box select)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "g.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"), "ofl")
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", key="club")
            e.act("add_heads", query="Intimidator Spot 260", qty=6)
            e.act("add_heads", query="LED PARty RGBW", qty=16)
            groups = {g["key"]: g for g in e._auto_groups()}
            check("automatic groups by kind of light", len(groups.get("type:par", {}).get("heads", [])) == 16
                  and len(groups.get("type:moving_spot", {}).get("heads", [])) == 6, str(list(groups)))
            rigs = [g for g in groups.values() if g["kind"] == "rig"]
            check("...and by the truss (or floor) they hang on", rigs and sum(len(g["heads"]) for g in rigs) == 22,
                  str([(g["name"], len(g["heads"])) for g in rigs]))
            check("...and the console gets them in the snapshot", e.snapshot().get("auto_groups"), "")
            r = e.act("select_group", key="type:par")
            check("tap an automatic group to select it", r.get("ok") and len(e.selected) == 16, str(r))
            e.act("select_group", key="type:moving_spot", add=True)
            check("...Shift adds another", len(e.selected) == 22, str(len(e.selected)))
            e.act("select_group", key="type:par")
            e.act("select_split", split="even")
            check("split the selection: even", e.selected == list(range(8, 23, 2)), str(e.selected))
            e.act("select_group", key="type:par")
            e.act("select_split", split="left")
            check("...left half by where they hang", len(e.selected) == 8, str(e.selected))
            check("...and choosing lights is not an undo step", "select_split" in eng.UNDO_EXCLUDED, "")
            r = e.act("quick_set", page=1, slot=1, button={"kind": "flash", "target": {"auto": "type:par", "split": "odd"}})
            e.act("quick_press", id="q1-1")
            check("a button aimed at an automatic group (and a split of it)",
                  r.get("ok") and len(e.quick_active["q1-1"]["heads"]) == 8, str(e.quick_active.get("q1-1")))
            e.act("add_heads", query="LED PARty RGBW", qty=2)
            e.act("quick_release_all")
            e.act("quick_press", id="q1-1")
            check("...which follows the rig: two more PARs, one more in its odd half",
                  len(e.quick_active["q1-1"]["heads"]) == 9, str(len(e.quick_active["q1-1"]["heads"])))
        finally:
            e.shutdown()
    fx = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    st = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("the list folds lights of one model into one row, with automatic group chips and splits",
          "FOLD_MIN" in fx and "auto_groups" in fx and '"select_split"' in fx, "")
    check("Shift-drag on the stage box-selects", "headsInRect" in st and "onBox" in st, "")
    css = (ROOT / "web" / "app" / "app.css").read_text(encoding="utf-8")
    mj = (ROOT / "web" / "app" / "main.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    check("gig mode: 44 px controls, on by itself on touch screens, a switch in Settings",
          "body.gig .btn" in css and "min-height: 44px" in css and "(pointer: coarse)" in mj
          and "jarvis.gig" in (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8"), "")
    check("a phone keeps Blackout in its bottom bar on every tab", 'id="mobile-bo"' in html
          and "button[data-mview]" in mj, "")


def test_looks() -> None:
    """A look is saved under a name with its colours, positions and the
    effects running (a movement too), and one tap brings all of it back -
    on the selection, or with nothing selected on the lights it came from.
    Parts can be left out; it can be renamed, updated, made a button."""
    print("Looks (named; values + effects; one tap; parts; rename; button)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "l.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"), "ofl")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=4, universe=1, address=1)
            e.act("select_heads", heads=[1, 2])
            e.act("set_intensity", level=70)
            e.act("set_colour", hex="#ff0000")
            e.act("run_fx", name="circle", params={"arc": 180, "direction": -1})
            r = e.act("record_preset", name="Opening sweep")
            look = e.presets[-1]
            check("a look saves colours, level and the running movement",
                  r.get("ok") and look["fx"] and look["fx"][0]["name"] == "circle"
                  and look["fx"][0]["params"]["arc"] == 180 and "wheel" in look["values"]
                  and look["head_list"] == [1, 2], str(look)[:300])
            check("...with a colour preview and tags for its tile",
                  look.get("hexes") and "Colour" in look.get("tags", []) and "Circle" in look.get("tags", []),
                  str((look.get("hexes"), look.get("tags"))))
            e.act("clear_programmer")
            e.act("clear_selection")
            r = e.act("include_preset", preset="Opening sweep")
            check("one tap with nothing selected plays it on its own lights - values AND the movement",
                  r.get("ok") and e.programmer.get(1, {}).get("dimmer") == 70
                  and [f["lib"] for f in e.fx] == ["circle"] and e.fx[0]["heads"] == [1, 2], str(r))
            e.act("include_preset", preset="Opening sweep")
            check("...playing it again doesn't stack a second movement", len(e.fx) == 1, str(len(e.fx)))
            e.act("select_heads", heads=[3, 4])
            e.act("clear_programmer")
            e.act("include_preset", preset="Opening sweep")
            check("...on a selection it plays on those lights", e.fx and e.fx[-1]["heads"] == [3, 4]
                  and e.programmer.get(3, {}).get("dimmer") == 70, str(e.fx))
            e.act("set_intensity", level=40)
            r = e.act("record_preset", name="Only colour", include=["colour"])
            check("a look can leave parts out (just the colour)",
                  r.get("ok") and set(e.presets[-1]["values"]) <= {"wheel", "red", "green", "blue", "white", "macro"}
                  and not e.presets[-1]["fx"], str(e.presets[-1]))
            check("...an unknown part is refused", not e.act("record_preset", name="x", include=["smell"]).get("ok"), "")
            e.act("rename_preset", preset="Only colour", name="Red")
            check("rename a look", any(p["name"] == "Red" for p in e.presets), "")
            r = e.act("quick_set", page=1, slot=1, button={"kind": "preset", "preset": look["n"], "label": "Opening"})
            e.act("stop_fx")
            e.act("clear_selection")
            e.act("quick_press", id="q1-1")
            check("a button plays a look in one tap", [f["lib"] for f in e.fx] == ["circle"], str(e.fx))
        finally:
            e.shutdown()
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the Looks tab shows named look tiles with Save look",
          "+ Save look" in html and 'id="preset-list"' in html and "lookTile" in js and '"rename_preset"' in js, "")


def test_my_moves() -> None:
    """A movement you made ("slow half turn, counter-clockwise") saved under
    a name, played on any lights from a list or a button, looping until
    stopped - not a cue, and not tied to the lights it was made on."""
    print("My moves (save, play, rename, delete, on a button, saved with the show)")
    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "m.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"), "ofl")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=4, universe=1, address=1)
            e.act("select_heads", heads=[1, 2])
            e.act("run_fx", name="circle", params={"arc": 180, "direction": -1, "speed": 1 / 12})
            r = e.act("move_save", name="Slow half-turn CCW")
            mv = r.get("move") or {}
            check("save the movement running now under a name", r.get("ok") and mv.get("lib") == "circle"
                  and mv["params"].get("arc") == 180 and mv["params"].get("direction") == -1, str(r))
            check("...a second move with the same name is refused",
                  not e.act("move_save", name="slow half-turn ccw").get("ok"), "")
            r = e.act("move_save", name="Wide sweep", lib="pan_sweep", params={"arc": 90, "size": 40})
            check("...or save one from its shape and knobs", r.get("ok") and r["move"]["params"]["size"] == 40.0, str(r))
            e.act("stop_fx")
            r = e.act("move_play", id=mv["id"], heads=[3, 4])
            check("play it on OTHER lights (not tied to where it was made)",
                  r.get("ok") and e.fx[-1]["lib"] == "circle" and e.fx[-1]["heads"] == [3, 4]
                  and e.fx[-1]["params"]["arc"] == 180, str(e.fx))
            check("...and the live feed says which move is playing",
                  any(f.get("move") == mv["id"] for f in e.snapshot().get("fx") or []), str(e.snapshot().get("fx")))
            e.act("move_play", name="Wide sweep", heads=[3, 4])
            check("...another move on the same lights takes over (one movement at a time)",
                  [f["lib"] for f in e.fx] == ["pan_sweep"], str([f["lib"] for f in e.fx]))
            e.act("stop_fx")
            e.act("quick_set", page=1, slot=1, button={"kind": "move", "move": mv["id"], "mode": "latch",
                                                       "target": {"heads": [1, 2]}})
            e.act("quick_press", id="q1-1")
            check("a button plays one of My moves", [f["lib"] for f in e.fx] == ["circle"], str(e.fx))
            e.act("move_rename", id=mv["id"], name="Half turn")
            e.act("move_save", name="Half turn", id=mv["id"], lib="circle", params={"arc": 270, "direction": -1})
            e.act("quick_press", id="q1-1")
            e.act("quick_press", id="q1-1")
            check("...and follows the move when it is updated", e.fx and e.fx[-1]["params"]["arc"] == 270, str(e.fx))
            e.act("save_show", name="mv")
            e.act("move_delete", id=mv["id"])
            check("delete a move", all(m["id"] != mv["id"] for m in e.moves), "")
            e.act("load_show", name="mv")
            check("My moves are saved with the show", [m["name"] for m in e.moves] == ["Half turn", "Wide sweep"],
                  str([m["name"] for m in e.moves]))
            check("the Move tab gets them in the snapshot", len(e.snapshot().get("moves") or []) == 2, "")
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8")
    check("the Move tab lists My moves with save / rename / delete",
          all(k in js for k in ('"move_save"', '"move_play"', '"move_rename"', '"move_delete"', "Save this as my move")), "")


def test_arm_for_set() -> None:
    """ARM used to switch itself off after 10 minutes, and a laser had a
    600 s cap of its own: at a gig the lasers went dark mid-set.  Now ARM
    can last until you disarm, and a laser stays on while armed."""
    print("ARM until disarmed, lasers without a time cap")
    import math

    from app import engine as eng
    from app import fixlib, fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "a.db"
        fixtures.store_parsed(db, fixlib.load("jarvis", "laserworld/beambar-10b-mk3"), "jarvis:laserworld/beambar-10b-mk3")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="BeamBar 10B MK3", qty=1, universe=1, address=25)
            r = e.act("fx_arm", state=True, minutes="until")
            st = e._sfx_public()
            check("ARM can last until you disarm", r.get("ok") and st["armed"] and st["armed_forever"]
                  and math.isinf(e.fx_armed_until), str(st))
            e.act("fx_arm", state=True, minutes=60)
            check("...or an hour", 3500 < e._sfx_public()["armed_left"] <= 3600, str(e._sfx_public()["armed_left"]))
            e.act("fx_arm", state=True, minutes="until")
            e.act("fx_laser", heads=[1], down=True)
            run = next(iter(e.fx_runs.values()))
            check("a laser switched on has no time cap of its own", math.isinf(run["until"]), str(run["until"]))
            check("...and the live feed still reads (no infinite numbers in JSON)",
                  __import__("json").dumps(e._sfx_public()) and e._sfx_public()["runs"][0]["left"] is None, "")
            e.act("fx_arm", state=False)
            check("disarming still stops it at once", not e.fx_runs, str(e.fx_runs))
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "fxpanel.js").read_text(encoding="utf-8")
    check("the ARM dialog offers 10 min / 1 hour / until I disarm", "Until I disarm" in js and 'minutes: armFor' in js, "")
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("tab dots use the same kinds as the programmer bar", "GROUP_OF[r.replace" in pj, "")
    check("a light with no dimmer gets big On / Off, not a fader that does nothing",
          "gateOnly" in pj and '$(".big-fader-row").hidden = gateOnly' in pj, "")
    check("a colour-wheel light leads with its own wheel colours", "wheel-only" in pj and "pickerAnyway" in pj, "")


def test_co2_preset() -> None:
    """MagicFX Psyco2Jet, Preset mode: its GO channel (200-249 continuous)
    was filed as a plain setting - sharing Direction's control before, its
    own setting after - so a Direction of "Right" (192-255), a cue or a
    fader fired the jet over and over whenever it was armed.  GO is its fire
    output now: armed + held only, capped, released when the finger lifts."""
    print("CO2 jet preset mode (GO is the fire channel)")
    from app import engine as eng
    from app import fixlib, fixtures
    from app.engine_support import channel_role

    check("'Preset' is not a reset/maintenance channel", channel_role("Preset") != "unused"
          and channel_role("FixtureGlobalReset") == "unused", channel_role("Preset"))
    for src, key in (("ofl", "magicfx/psyco2jet.json"), ("qlc", "MagicFX/MagicFX-Psyco2Jet.qxf")):
        it = fixlib.apply_fx(fixlib.load(src, key)[0])
        pre = next(m for m in it["modes"] if m["name"].lower() == "preset")
        roles = {d["name"].lower(): d for d in pre["detail"]}
        check(f"{src}: GO is the jet's fire output (continuous while held), Preset its mode",
              roles["go"]["role"] == "fx_fire" and roles["go"]["on_value"] == 224
              and roles["go"]["off_value"] == 0 and roles["preset"]["role"] == "fx_mode",
              str({k: v["role"] for k, v in roles.items()}))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "c.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "MagicFX/MagicFX-Psyco2Jet.qxf"), "qlc:MagicFX/MagicFX-Psyco2Jet.qxf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Psyco2Jet", mode="Preset", qty=1, universe=1, address=1)
            e.act("select_heads", heads=[1])
            e.act("set_attribute", attribute="fx_param", value=230)       # Direction: Right
            e.act("fx_arm", state=True)

            def go():
                return e.build_frames()[1][4]
            check("Direction 'Right' + armed does NOT fire it", go() == 0, str(list(e.build_frames()[1][:5])))
            check("GO can't be set from the programmer",
                  not e.act("set_attribute", attribute="fx_fire", value=224).get("ok"), "")
            e.act("record_cue", playback=1, fade=0)
            e.act("cue_go", playback=1)
            check("...nor from a cue", go() == 0, str(go()))
            e.act("fx_fire", heads=[1], down=True, owner="t")
            check("armed + held: it sprays (continuous)", go() == 224, str(go()))
            e.act("fx_fire", heads=[1], down=False, owner="t")
            check("released: it stops", go() == 0, str(go()))
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "fxpanel.js").read_text(encoding="utf-8")
    qb = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    check("a hold is released by any pointer release, and stop waits for start",
          'addEventListener("pointerup", releaseAll, true)' in js and "started.then(() => stop())" in js
          and "before.then(() => run(\"quick_press\"" in qb, "")


def test_aux_channels() -> None:
    """A light's channels no role fits (Chauvet Intimidator Wave 360:
    continuous pan rotation, built-in auto tilt, heads on/off, auto
    programs) used to be `raw` - DMX with no control, held at 0.  Each now
    has its own named control with its ranges as steps."""
    print("every channel gets a control (Wave 360's missing movement)")
    from app import engine as eng
    from app import fixlib, fixtures

    it = fixlib.apply_aux(fixlib.apply_fx(fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf")[0]))
    det = {d["n"]: d for d in it["modes"][0]["detail"]}
    check("continuous pan rotation and built-in tilt get their own controls",
          det[4]["role"] == "aux1" and det[9]["role"] == "aux2", str((det[4]["role"], det[9]["role"])))
    check("...named after the channel, with its ranges as steps",
          "Continuous Pan Rotating" in det[4]["label"] and len(det[4]["slots"] or []) == 3
          and len(det[9]["slots"] or []) >= 20, det[4]["label"])
    check("a second speed channel no longer shares pan speed's fader",
          det[3]["role"] == "speed" and det[10]["role"].startswith("aux"), str((det[3]["role"], det[10]["role"])))
    check("heads on/off, auto programs and program speed are controllable",
          all(det[n]["role"].startswith("aux") for n in (28, 29, 30)), "")
    check("the maintenance channel (settings) stays untouched", det[33]["role"] == "unused", det[33]["role"])
    laser = fixlib.apply_aux(fixlib.apply_fx(fixlib.load("jarvis", "laserworld/beambar-10b-mk3")[0]))
    check("effects and lasers are left to their own roles",
          not any(d["role"].startswith("aux") for d in laser["modes"][0]["detail"]), "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "a.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), "q")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Wave 360", mode="33 ch.", qty=1, universe=1, address=98)
            others = {x["role"]: x for p in e.attribute_state([1])["pages"] for x in p["attrs"]}
            check("the programmer lists them by name with their steps",
                  others.get("aux1", {}).get("name") == "Continuous Pan Rotating"
                  and len(others["aux5"].get("slots") or []) >= 40, str(others.get("aux1")))
            e.act("select_heads", heads=[1])
            r = e.act("set_attribute", attribute="aux1", value=200)
            check("setting one reaches its own DMX channel", r.get("ok") and e.build_frames()[1][100] == 200,
                  str(e.build_frames()[1][100]))
            e.act("record_cue", playback=1, fade=0)
            check("and records into a cue", "aux1" in json.dumps(e.playbacks[0]["stack"]), "")
        finally:
            e.shutdown()
        # a Wave stored by an older Jarvis, its original file gone (a GDTF
        # Share download no longer cached): upgraded in the database on start
        old = tmp / "old.db"
        from app import fixlib as _fl
        keep = _fl.apply_aux
        _fl.apply_aux = lambda item: item                 # store it the old way
        try:
            fixtures.store_parsed(old, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"),
                                  "gone-from-cache.gdtf")
        finally:
            _fl.apply_aux = keep
        e = eng.Engine(db_path=old, dry_run=True, show_dir=tmp / "s2")
        try:
            e.act("add_heads", query="Intimidator Wave 360", mode="33 ch.", qty=1, universe=1, address=211)
            check("(stored the old way: continuous pan had no control)", e.patch[0]["map"][3] == "raw",
                  e.patch[0]["map"][3])
            done = fixtures.refresh_imports(old, [tmp / "nowhere"])
            e.remap_heads()
            check("start-up upgrades it without the original file",
                  done["refreshed"] >= 1 and e.patch[0]["map"][3] == "aux1" and e.patch[0]["map"][9] == "aux3",
                  str((done, e.patch[0]["map"][:10])))
            names = {x["role"]: x.get("name") for p in e.attribute_state([1])["pages"] for x in p["attrs"]}
            check("...and the programmer shows its controls by name",
                  names.get("aux1") == "Continuous Pan Rotating" and names.get("aux5") == "Auto Programs", str(names))
        finally:
            e.shutdown()
        # THE OPERATOR'S CASE: the old one-shot upgrade crashed on an odd
        # fixture (a capability row with a missing end), recorded itself as
        # done, and the Wave stored after it never got its controls
        broken = tmp / "broken.db"
        _fl.apply_aux = lambda item: item
        try:
            fixtures.store_parsed(broken, [{"manufacturer": "Odd", "model": "Manual Read", "modes": [
                {"name": "2ch", "channel_count": 2, "channels": ["Odd", "Mode"],
                 "detail": [{"n": 1, "label": "Odd", "name": "Odd", "role": "raw", "caps": [[None, 10, "a"], [11, 255, "b"]]},
                            {"n": 2, "label": "Mode", "name": "Mode", "role": "raw", "caps": [[0, 9, "x"], [10, 255, "y"]]}]}]}],
                "manual:odd")
            fixtures.store_parsed(broken, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), "gone.gdtf")
        finally:
            _fl.apply_aux = keep
        with fixtures.db(broken) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('parser_version', ?)", (str(fixtures.PARSER_VERSION),))
        fixtures.refresh_imports(broken, [])
        labels = fixtures.mode_channels(broken, "Chauvet", "Intimidator Wave 360 IRC", "33 ch.")
        check("an odd fixture never stops the rest being upgraded, and a finished version still repairs",
              labels[8].startswith("Aux ") and labels[28].startswith("Aux "), str(labels[8:10]))
        check("a capability row with a missing end is skipped, not a crash",
              fixlib.apply_aux({"fx_kind": "", "modes": [{"channels": ["X"], "detail": [
                  {"label": "X", "name": "X", "role": "raw", "caps": [[None, 1, "a"], [2, 3, "b"], [4, 9, "c"]]}]}]})
              ["modes"][0]["detail"][0]["role"] == "aux1", "")
    js = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the Beam tab shows them under their own names", "a.name || attrName(a.role)" in js, "")
