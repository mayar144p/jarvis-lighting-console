"""Self-test suites, part 8: beam_bar, fixture_search, light_test, share_relogin, manual_fixture, fx_safety, show_building, realtime, ...."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from app import fixtures
from tools.selftests.common import (
    ROOT,
    _free_udp_port,
    _raises,
    _share_gdtf_bytes,
    _share_transport,
    _which,
    check,
    dialogs_js,
)


def test_beam_bar() -> None:
    """A laser beam bar (Laserworld BeamBar 10B MK3, from its manual's
    chart): every beam is its own programmable channel, but nothing lights
    unless the laser is armed and fired; its output channel's mode (own
    beams / built-in programs) is a per-laser setting."""
    print("beam bar lasers (per-beam channels, output only when armed)")
    from app import engine as eng
    from app import fixlib, fixtures
    from app.engine_support import channel_role

    item = {"manufacturer": "Acme", "model": "Laser Bar 6G", "type": "", "modes": [{"name": "8ch", "channels": [], "detail": []}]}
    for i, n in enumerate(["Mode", "Motor position"] + [f"Laser {k}" for k in range(1, 7)]):
        item["modes"][0]["channels"].append(n)
        item["modes"][0]["detail"].append({"n": i + 1, "label": n, "name": n, "role": channel_role(n)})
    fixlib.apply_fx(item)
    roles = [d["role"] for d in item["modes"][0]["detail"]]
    check("a laser's numbered outputs become its own beams, the motor its tilt",
          roles[1] == "laser_y" and roles[2:] == [f"laser_beam{k}" for k in range(1, 7)], str(roles))
    dup = {"manufacturer": "Acme", "model": "Laser X", "type": "", "modes": [{"name": "m", "channels": [], "detail": []}]}
    for i, n in enumerate(["Mode", "Sound", "Auto program"]):
        dup["modes"][0]["channels"].append(n)
        dup["modes"][0]["detail"].append({"n": i + 1, "label": n, "name": n, "role": "raw"})
    fixlib.apply_fx(dup)
    r = [d["role"] for d in dup["modes"][0]["detail"]]
    check("channels that would share one role each get their own control",
          len(set(r)) == 3, str(r))
    # the same bar from its manual: a chart with no name column ("1 0-49
    # laser off"), a rear-panel list and a German copy of the chart around it
    from app import manual
    chart = ["1 0-49 laser off", "50-99 sound mode", "100-149 automatic mode",
             "150-199 DMX mode (Channel 2 --> Channel 3 valid)", "200-255 DMX mode (Channel 4 --> Channel 13 valid)",
             "2 0-255 program / effect selection", "3 0-255 Speed (slow to fast, 21 levels) WEEE-Reg.-No. (G",
             "Each of the following channels corresponds with one laser output (from left to right, front view)"] + \
        [f"{k} 0-255 brightness adjustment (weak to bright)" for k in range(4, 14)]
    german = ["1 0-49 Laser aus", "50-99 Sound-Modus", "100-149 Automatik-Modus", "150-199 DMX-Modus (Kanal 2-3)",
              "200-255 DMX-Modus (Kanal 4-13)", "2 0-255 Programmauswahl", "3 0-255 Geschwindigkeit"] + \
        [f"{k} 0-255 Helligkeit" for k in range(4, 14)]
    text = "\n".join(["Laserworld BeamBar MK3 laser", "1 Power", "2 Key Switch", "3 Modes / Functions",
                      "DMX Control Chart", "Channel Value Function", *chart,
                      "1 Stromversorgung", "2 Schlüsselschalter", "3 Modi / Funktionen", *german])
    d = manual.read(text, "Laserworld", "BeamBar 10B MK3", offline=True)
    check("a manual's chart with no name column is read as one 13-channel mode (not the rear-panel list)",
          [len(m["channels"]) for m in d["modes"]] == [13], str([(m["name"], len(m["channels"])) for m in d["modes"]]))
    if d["modes"]:
        ch = d["modes"][0]["channels"]
        check("channel 1 keeps all its modes as the laser output",
              ch[0]["function"] == "laser output" and len(ch[0]["ranges"]) == 5, str(ch[0]))
        check("the ten 'brightness' rows become Laser 1..10",
              [c["name"] for c in ch[3:]] == [f"Laser {k}" for k in range(1, 11)], str([c["name"] for c in ch[3:]]))
        check("...and say 'laser beam' in the review table",
              {c["function"] for c in ch[3:]} == {"laser beam"}, str({c["function"] for c in ch[3:]}))
        check("a page footer glued onto a row is not part of the name",
              ch[2]["name"] == "Speed (slow to fast, 21 levels)", ch[2]["name"])
        it = fixlib.apply_fx(manual.to_parsed(d)[0])
        det = it["modes"][0]["detail"]
        check("saved: output fires in its DMX beam mode, each beam its own control",
              det[0]["role"] == "laser_on" and det[0]["on_value"] == 227
              and [r["role"] for r in det[3:]] == [f"laser_beam{k}" for k in range(1, 11)],
              str([(r["role"], r.get("on_value")) for r in det[:4]]))
    hits = fixlib.search("laserworld beambar 10b")
    check("the BeamBar 10B MK3 is in the Jarvis library", hits and hits[0]["model"] == "BeamBar 10B MK3", str(hits[:1]))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "b.db"
        fixtures.store_parsed(db, fixlib.load("jarvis", "laserworld/beambar-10b-mk3"), "jarvis:laserworld/beambar-10b-mk3")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="BeamBar 10B MK3", qty=1, universe=1, address=25)
            h = e.patch[0]
            check("patched at 25 with its 13 channels", h["address"] == 25 and len(h["map"]) == 13
                  and h["map"][0] == "laser_on" and h["map"][12] == "laser_beam10", str(h["map"]))

            def wire():
                return list(e.build_frames()[1][24:37])
            check("dark by default (channel 1 in 'laser off')", wire()[0] == 0 and not any(wire()[3:]), str(wire()))
            e.act("select_all", include_fx=True)
            e.act("set_intensity", level=100)
            check("Full never lights a laser", wire()[0] == 0 and not any(wire()[3:]), str(wire()))
            check("(Select all leaves lasers out: they're picked on purpose)",
                  e.act("select_all").get("ok") is False or 1 not in e.selected, "")
            e.act("select_heads", heads=[1])
            r = e.act("set_attribute", attribute="laser_beam1", value=255)
            e.act("set_attribute", attribute="laser_beam3", value=200)
            check("beams are programmable (and recorded like any attribute)", r.get("ok"), str(r))
            check("but a programmed beam stays dark until the laser is fired", not any(wire()[3:]) and wire()[0] == 0, str(wire()))
            bad = e.act("set_attribute", attribute="laser_on", value=0)
            check("the output channel is never switched off/on from the programmer", not bad.get("ok"), "")
            r = e.act("fx_laser", heads=[1], down=True, owner="t")
            check("firing needs ARM", not r.get("ok"), str(r))
            e.act("fx_arm", state=True)
            e.act("fx_laser", heads=[1], down=True, owner="t")
            w = wire()
            check("armed + fired: beam-control mode, the programmed beams only",
                  200 <= w[0] <= 255 and w[3] == 255 and w[5] == 200 and w[4] == 0 and w[6] == 0, str(w))
            look = e._fx_look(h, dict(zip(h["map"], w)))
            check("the 3D view gets which beams are lit, in blue",
                  look and look.get("beams", [])[:4] == [1, 0, 1, 0] and look.get("hex") == "#3355ff", str(look))
            e.act("blackout", state=True) if "blackout" in eng.ACTIONS else None
            check("blackout puts it out", e.build_frames()[1][24] == 0 and not any(e.build_frames()[1][27:37]), "")
            e.act("blackout", state=False)
            e.act("clear_programmer")
            check("blackout also disarmed it", not e.act("fx_laser", heads=[1], down=True, owner="t").get("ok"), "")
            e.act("fx_arm", state=True)
            e.act("fx_laser", heads=[1], down=True, owner="t")
            w = wire()
            check("fired with no beams programmed: every beam on", all(v == 255 for v in w[3:]), str(w))
            r = e.act("set_attribute", attribute="laser_on", value=175)
            check("the output mode is programmable (built-in programs)", r.get("ok") and wire()[0] == 175, str((r, wire()[0])))
            r = e.act("set_attribute", attribute="laser_on", value=10)
            check("but never 'laser off' from the programmer", not r.get("ok"), str(r))
            e.act("record_cue", playback=1, fade=0)
            check("the mode records into a cue", "laser_on" in json.dumps(e.playbacks[0]["stack"]), "")
            e.act("clear_programmer")
            check("cleared: back to the fixture's own on value", wire()[0] == 227, str(wire()[0]))
            e.act("cue_go", playback=1)
            check("the cue brings its mode back", wire()[0] == 175, str(wire()[0]))
            e.act("playback_release", playback=1) if "playback_release" in eng.ACTIONS else None
            e.act("select_heads", heads=[1])
            e.act("set_attribute", attribute="laser_on", value=175)
            e.act("fx_laser", heads=[1], down=False, owner="t")
            check("a programmed mode never lights it on its own", wire()[0] == 0, str(wire()[0]))
            e.act("clear_programmer")
            e.act("fx_laser", heads=[1], down=False, owner="t")
            check("released: off again", wire()[0] == 0 and not any(wire()[3:]), str(wire()))
            e.act("fx_laser", heads=[1], down=True, owner="t2", values={"laser_on": 75})
            check("a laser button can carry its own mode (sound)", wire()[0] == 75, str(wire()[0]))
            e.act("fx_kill")
            check("KILL FX stops it", wire()[0] == 0, "")
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "fxpanel.js").read_text(encoding="utf-8")
    check("the Laser tab has beams, patterns, output mode and every other channel",
          all(k in js for k in ("function beamsBlock", "BEAM_PATTERNS", "function modeBlock", "function otherBlock")), "")


def test_fixture_search() -> None:
    """'I type the right text and nothing is found': short brand names,
    extra describing words and typos find the light; brand + model typed
    together finds a GDTF Share entry; stale replies never win in the UI."""
    print("fixture search (forgiving, one matcher for every source)")
    from app import fixlib, fixtures, gdtfshare, searchmatch

    def top(q):
        r = fixlib.search(q, 5)
        return (r[0]["manufacturer"], r[0]["model"], r[0]["close"]) if r else None

    check("'ADJ' finds American DJ", (top("adj galaxian") or ("",))[0] == "American DJ", str(top("adj galaxian")))
    check("an extra describing word (LED) doesn't empty the list",
          (top("chauvet intimidator spot 110 led") or ("", ""))[1] == "Intimidator Spot 110", str(top("chauvet intimidator spot 110 led")))
    check("'moving head' after the model still finds it",
          (top("Intimidator Spot 110 moving head") or ("", ""))[1] == "Intimidator Spot 110", "")
    check("a typo finds it", (top("intimidater spot 110") or ("", ""))[1] == "Intimidator Spot 110", str(top("intimidater spot 110")))
    check("spacing doesn't matter ('beam z')", (top("beam z cobra") or ("", ""))[1] == "Cobra 720", str(top("beam z cobra")))
    check("a number is never a typo (100 is not 120)", searchmatch.score("cobra 100", "BeamZ", "Cobra 120") is None, "")
    check("a different model number is offered only as a close match, below exact ones",
          (searchmatch.score("beamz cobra 100 spot", "BeamZ", "Cobra 120 Spot") or (0,))[0] == 1
          and searchmatch.score("beamz cobra 120 spot", "BeamZ", "Cobra 120 Spot")[0] == 0, "")
    check("nonsense still finds nothing", fixlib.search("xyzzy qwv", 5) == [], "")
    entry = {"fixture": "Cobra 120 Spot", "manufacturer": "BeamZ"}
    check("GDTF Share: brand + model typed together matches",
          gdtfshare.GdtfShare._score(entry, "beamz cobra 120 spot", "") is not None
          and gdtfshare.GdtfShare._score(entry, "cobra", "") < gdtfshare.GdtfShare._score(entry, "beamz cobra 120", ""), "")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "s.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-110.json"), source="t")
        got = fixtures.search(db, "chauvet intimdator 110", fuzzy=True)
        check("installed search forgives a typo in the Add dialog", len(got) == 1, str(len(got)))
        check("but the engine's own lookups stay strict", fixtures.search(db, "chauvet intimdator 110") == [], "")
        bad = fixtures.search(db, "chauvet intimidator 110", fuzzy=True)[0]
        gone = fixtures.delete(db, bad["id"])
        check("a bad installed fixture can be deleted", gone and fixtures.search(db, "intimidator 110", fuzzy=True) == []
              and fixtures.get(db, bad["id"]) is None, str(gone))
        check("deleting one that isn't there says so", fixtures.delete(db, 99999) is None, "")
    js = dialogs_js()
    check("the Add dialog can delete an installed fixture and shows channel counts",
          "/api/fixtures/delete" in js and "chCounts(" in js, "")
    check("only the newest search may fill the list", js.count("if (my !== seq) return;") >= 5, str(js.count("if (my !== seq) return;")))


def test_light_test() -> None:
    """The 'Test this light' step after adding a model: lit white and
    centred, then the operator walks the shutter's likely open values - and
    if none lights it, every other channel - on the REAL light; what works
    is saved for the model, and a model that passed is not asked again."""
    print("test this light (walk the open values on the real light)")
    from app import console_ai, fixlib, fixtures
    from app import engine as eng
    from app.engine_support import channel_role

    # an LED head at Full with no colour set is white, not dark (Wave 360)
    with tempfile.TemporaryDirectory() as wd:
        wdb = Path(wd) / "w.db"
        fixtures.store_parsed(wdb, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"), "qlc")
        w = eng.Engine(db_path=wdb, dry_run=True, show_dir=Path(wd) / "s")
        try:
            w.act("add_heads", query="Intimidator Wave 360", mode="33 ch.", qty=1, universe=1, address=211)
            w.act("select_all")
            w.act("set_intensity", level=100)
            buf = w.build_frames()[1]
            check("Full on an untouched LED head lights it white (RGB full, white LED left off)",
                  list(buf[220:224]) == [255, 255, 255, 0] and buf[240] == 255, str(list(buf[220:224])))
            w.act("set_attribute", attribute="green", value=200)
            buf = w.build_frames()[1]
            check("setting any colour drops the default white entirely",
                  list(buf[220:224]) == [0, 200, 0, 0], str(list(buf[220:224])))
        finally:
            w.shutdown()
    check("GDTF 'Gobo1Pos' is the gobo's index, not a second gobo wheel",
          channel_role("Gobo1Pos") == "gobo_rot" and channel_role("Gobo1") == "gobo", "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "t.db"
        with zipfile.ZipFile(tmp / "c.gdtf", "w") as zf:      # the BeamZ Cobra 100 Spot shape
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.1"><FixtureType Name="Cobra Hunt" Manufacturer="BeamZ">'
                '<DMXModes><DMXMode Name="6ch"><DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="4"><LogicalChannel Attribute="Shutter1">'
                '<ChannelFunction Name="Shutter1" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="5"><LogicalChannel Attribute="Color1">'
                '<ChannelFunction Name="Color1" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="6"><LogicalChannel Attribute="Zoom">'
                '<ChannelFunction Name="Zoom" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, tmp / "c.gdtf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Cobra Hunt", mode="6ch", qty=2, universe=1, address=1)
            p = {x["head_no"]: x for x in e.snapshot()["patch"]}
            check("a new model starts untested", not p[1]["tested"], str(p[1].get("tested")))
            e.act("select_heads", heads=[1])
            e.act("set_attribute", attribute="zoom", value=77)
            r = e.act("light_test", head=1, step="start")
            buf = e.build_frames()[1]
            check("start: full, centred, shutter at its best guess",
                  r.get("ok") and buf[2] == 255 and 120 <= buf[0] <= 135 and 120 <= buf[1] <= 135, str(list(buf[:6])))
            cands = r.get("candidates") or []
            check("a shutter the file says nothing about gets the common open values to try",
                  len(cands) >= 6 and 0 in cands and 255 in cands and 32 in cands, str(cands))
            check("and the other channels to hunt through, not pan/tilt/dimmer",
                  [c["role"] for c in r.get("hunt") or []][:1] == ["wheel"]
                  and not {"pan", "tilt", "dimmer"} & {c["role"] for c in r["hunt"]}, str(r.get("hunt")))
            e.act("light_test", head=1, step="open", value=32)
            check("each value tried goes out on the shutter", e.build_frames()[1][3] == 32, "")
            e.act("light_test", head=1, step="channel", role="zoom", value=255)
            e.act("light_test", head=1, step="channel", role="wheel", value=255)
            buf = e.build_frames()[1]
            check("the hunt puts back the channel it tried before", buf[5] == 0 and buf[4] == 255, str(list(buf[:6])))
            e.act("light_test", head=1, step="pan", value=0.3)
            check("pan check moves pan", 70 <= e.build_frames()[1][0] <= 80, "")
            e.act("light_test", head=1, step="end")
            check("end gives the head back what it was doing",
                  e.programmer.get(1) == {"zoom": 77}, str(e.programmer.get(1)))
            e.act("remember_open", head=1, value=32)
            r = e.act("remember_open", head=1, role="zoom", value=200)
            check("a channel found by the hunt is remembered too", r.get("ok"), str(r))
            e.act("clear_programmer")
            e.act("select_heads", heads=[2])
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("every head of the model then lights on Full (shutter + found channel)",
                  buf[8] == 255 and buf[9] == 32 and buf[11] == 200, str(list(buf[6:12])))
            # channel faders: straight to the wire, whatever the file says
            r = e.act("light_test", head=1, step="start")
            check("the test lists every DMX channel with its label and address",
                  [c["n"] for c in r["slots"]] == [1, 2, 3, 4, 5, 6] and r["slots"][2]["abs"] == 3
                  and r["slots"][2]["value"] == 255, str(r.get("slots")))
            e.act("light_test", head=1, step="raw", slot=6, value=99)
            check("a channel fader writes the byte directly", e.build_frames()[1][5] == 99, "")
            bad = e.act("light_test", head=1, step="raw", slot=9, value=1)
            check("a slot outside the head is refused", not bad.get("ok"), str(bad))
            e.act("light_test", head=1, step="end")
            check("closing the test lets go of the faders", e.build_frames()[1][5] == 200, str(list(e.build_frames()[1][:6])))
            r = e.act("light_tested", head=1, light=True, move=True, colour=False)
            check("a wrong colour is not a pass, and says to check the mode",
                  not r["tested"] and any("mode" in a for a in r["advice"]), str(r))
            r = e.act("light_tested", head=1, light=True, move=True, colour=True)
            p = {x["head_no"]: x for x in e.snapshot()["patch"]}
            check("a pass is saved for the model: not asked again",
                  r["tested"] and p[1]["tested"] and p[2]["tested"], str(r))
            check("the copilot cannot drive the test", {"light_test", "light_tested"} <= set(console_ai.DENY_ACTIONS), "")
            check("the test is not an undo step", "light_test" in eng.UNDO_EXCLUDED, "")
        finally:
            e.shutdown()
        # a channel the file never named ("Control1" -> raw) that the light
        # needs at a value: found on the faders, kept, held for the model
        with zipfile.ZipFile(tmp / "r.gdtf", "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.1"><FixtureType Name="Raw Hold" Manufacturer="BeamZ">'
                '<DMXModes><DMXMode Name="3ch"><DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Reserved1">'
                '<ChannelFunction Name="Reserved1" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="3"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, tmp / "r.gdtf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s2")
        try:
            e.act("add_heads", query="Raw Hold", mode="3ch", qty=2, universe=1, address=1)
            check("(the unnamed channel has no role)", e.patch[0]["map"][1] in ("raw", "unused"), str(e.patch[0]["map"]))
            e.act("light_test", head=1, step="start")
            e.act("light_test", head=1, step="raw", slot=2, value=180)
            r = e.act("light_test", head=1, step="keep")
            e.act("light_test", head=1, step="end")
            buf = e.build_frames()[1]
            check("a kept unnamed channel is held on every head of the model",
                  r.get("ok") and buf[1] == 180 and buf[4] == 180, str(list(buf[:6])))
            e.act("blackout", state=True) if "blackout" in eng.ACTIONS else setattr(e, "blackout", True)
            check("and blackout still blacks it out", e.build_frames()[1][1] == 0, "")
        finally:
            e.shutdown()
    js = dialogs_js()
    check("adding a light no longer pops up the test (it stays on the right-click menu)",
          "openLightTest(first)" not in js and "export async function openLightTest" in js, "")


def test_share_relogin() -> None:
    """An expired GDTF Share session must not need a restart: with an
    account at hand the download signs in again and retries once."""
    print("gdtf share (expired session re-signs in)")
    from app import fixtures as fx
    from app import gdtfshare as gs
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "s.db"
        fx.seed_generics(db)
        good = _share_transport(_share_gdtf_bytes(), b'{"result":true,"list":[]}')

        def transport(method, url, *, body=None, headers=None, timeout=20.0):
            if "downloadFile.php" in url and "PHPSESSID=old" in (headers or {}).get("Cookie", ""):
                return (401, {"content-type": "application/json"}, b'{"result":false,"error":"expired"}')
            return good(method, url, body=body, headers=headers, timeout=timeout)
        c = gs.GdtfShare(db, tmp / "cache", user="me", password="pw", transport=transport)
        c.cookies = {"PHPSESSID": "old"}
        r = c.download(11)
        check("an expired session signs in again and the download succeeds",
              r.get("ok") and c.cookies.get("PHPSESSID") == "abc123", str(r))
        anon = gs.GdtfShare(db, tmp / "cache2", transport=transport)
        anon.cookies = {"PHPSESSID": "old"}
        try:
            anon.download(11)
            ok = False
        except gs.GdtfShareError as exc:
            ok = exc.code == "unauthorized"
        check("without an account it says so (and the dialog then asks to sign in)", ok, "")
        st = anon.status()
        check("and status no longer claims a session", not st["signed_in"], str(st))


def test_manual_fixture() -> None:
    """A manual's DMX chart becomes a fixture: offline reader, the
    review round trip, and storing it through the effects classifier."""
    print("fixture from its manual (DMX chart -> fixture)")
    from app import engine as eng
    from app import fixtures, manual

    # The Funfetti Shot's QRG, as a PDF extracts it (the arrow between the
    # two values is a private-use glyph, U+F0F3)
    qrg = ("Funfetti Shot QRG EN 7\nDMX Linking The Funfetti Shot works with a DMX controller.\n"
           "1. Fill the main tube with Funfetti Shot refills.\n2. Plug into a suitable power outlet.\n"
           "Description The Funfetti Shot is an electric confetti launcher.\n"
           "DMX Assignments \n1 Channel Channel Function Value Setting \n1 Off/On \n"
           "000\uf0f3009 Off \n010\uf0f3255 On \n")
    d = manual.read(qrg, "Chauvet DJ", "Funfetti Shot", offline=True)
    ch = d["modes"][0]["channels"] if d["modes"] else []
    check("the offline reader finds exactly the chart, not the numbered steps",
          len(d["modes"]) == 1 and len(ch) == 1 and ch[0]["ranges"] == [[0, 9, "Off"], [10, 255, "On"]],
          str(d))
    check("and knows a confetti launcher's channel fires it",
          d["type"] == "confetti" and ch[0]["function"] == "fx fire", str(d))
    browser = (" DMX Assignments\nChannel   Function   Value   Setting\n 1 Channel  \n000 \uf0f3 009   Off\n"
               " 1   Off/On  \n 010 \uf0f3 255   On\n 7\n Asignaciones DMX\n 1 Canal\n000 \uf0f3 009   Apaga\n"
               " 1   Canal  \n 010 \uf0f3 255   Enciende\n")
    b = manual.read(browser, "Chauvet DJ", "Funfetti Shot", offline=True)
    check("as the browser's PDF reader lays it out (values before the row, six languages)",
          len(b["modes"]) == 1 and b["modes"][0]["channels"][0]["ranges"] == [[0, 9, "Off"], [10, 255, "On"]]
          and b["modes"][0]["channels"][0]["function"] == "fx fire", str(b))
    d2 = manual.read(qrg, offline=True)
    check("even with no model name, from the manual's own words", d2["type"] == "confetti", d2["type"])
    spot = ("14-Channel\n1 Pan 000-255 0-540\n2 Fine Pan\n3 Tilt\n4 Fine Tilt\n5 Pan/Tilt Speed\n"
            "6 Color Wheel\n000-006 White\n007-013 Orange\n028-034 Red\n7 Gobo Wheel\n8 Gobo Rotation\n"
            "9 Prism\n10 Zoom\n11 Dimmer\n12 Strobe\n000-003 Closed\n004-007 Open\n008-076 Strobe slow-fast\n"
            "13 Function\n14 Movement Macros\n")
    s = manual.read(spot, "Chauvet DJ", "Spot 260", offline=True)
    fns = [c["function"] for c in s["modes"][0]["channels"]]
    check("a light's chart: every channel with its function",
          fns[:6] == ["pan", "pan fine", "tilt", "tilt fine", "pan/tilt speed", "colour wheel"]
          and fns[10:12] == ["dimmer", "strobe"] and fns[13] == "setting", str(fns))
    check("junk is refused with a reason", _raises(lambda: manual.to_parsed({"modes": []})), "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "m.db"
        fixtures.store_parsed(db, manual.to_parsed(d), "manual:chauvet-dj-funfetti-shot")
        fixtures.store_parsed(db, manual.to_parsed(s), "manual:chauvet-dj-spot-260")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Funfetti Shot", qty=1, universe=1, address=1)
            e.act("add_heads", query="Spot 260", qty=1, universe=1, address=10)
            check("the saved launcher is an SFX that fires 10-255",
                  e.patch[0]["map"] == ["fx_fire"] and e._head_class(e.patch[0]) == "sfx"
                  and 10 <= (e.head_ranges(e.patch[0]).get("fx_fire") or {}).get("on_value", 0) <= 255,
                  str(e.patch[0]["map"]))
            e.act("select_all")
            e.act("set_colour", hex="#ff1010")
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("the saved spot: red on its real slot, strobe open, launcher untouched",
                  buf[9 + 5] == 31 and buf[9 + 10] == 255 and buf[9 + 11] == 4 and buf[0] == 0,
                  str(list(buf[:24])))
        finally:
            e.shutdown()


def test_fx_safety() -> None:
    """Lasers and special effects never answer a light's controls, fire
    only while armed and within their limits, and stop on blackout."""
    print("lasers and special effects (classes, ARM, limits, kill)")
    import time as _t

    from app import console_ai, fixlib, fixtures
    from app import engine as eng

    kinds = {k: fixlib.fx_kind(*k.split("|"), "", []) for k in (
        "Chauvet DJ|Funfetti Shot", "MagicFX|Psyco2Jet", "Pro-Lights|Jet Wash19",
        "Ayrton|Mistral", "American DJ|Galaxian 3D Laser", "Antari|Z-1000 Fog")}
    check("confetti, CO2, lasers and fog are recognised; a wash and a Mistral stay lights",
          list(kinds.values()) == ["confetti", "co2", "", "", "laser", "fog"], str(kinds))
    co2 = fixlib.apply_fx(fixlib.load("qlc", "MagicFX/MagicFX-Psyco2Jet.qxf")[0])["modes"][0]["detail"]
    arm = next(r for r in co2 if r["role"] == "fx_arm")
    fire = next(r for r in co2 if r["role"] == "fx_fire")
    check("the CO2 jet arms at 'Device enabled' (100-155), never at 'Test Mode' (156-255)",
          100 <= arm["on_value"] <= 155 and arm["off_value"] == 0, str(arm))
    check("and fires with its valve open (200-255)", 200 <= fire["on_value"] <= 255, str(fire))
    flame = fixlib.apply_fx(fixlib.load("ofl", "magicfx/stage-flame.json")[0])["modes"][0]["detail"]
    farm = next(r for r in flame if r["role"] == "fx_arm")
    check("a flame unit's 'Safety OFF' range is its armed state", 140 <= farm["on_value"] <= 153, str(farm))
    geyser = fixlib.apply_fx(fixlib.load("ofl", "chauvet-dj/geyser-rgb.json")[0])["modes"][0]["detail"]
    check("a fog machine's LEDs stay lights, its output becomes fog",
          [r["role"] for r in geyser][:4] == ["fog", "red", "green", "blue"]
          and geyser[-1]["role"] == "dimmer", str([r["role"] for r in geyser]))
    check("the copilot is denied every FX action",
          {"fx_arm", "fx_fire", "fx_fog", "fx_laser", "fx_kill"} <= console_ai.DENY_ACTIONS, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "fx.db"
        for src, key in (("ofl", "chauvet-dj/intimidator-spot-260.json"), ("qlc", "MagicFX/MagicFX-Psyco2Jet.qxf"),
                         ("ofl", "american-dj/galaxian-3d.json"), ("ofl", "chauvet-dj/geyser-rgb.json"),
                         ("jarvis", "chauvet-dj/funfetti-shot")):
            fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=1, universe=1, address=1)
            e.act("add_heads", query="Psyco2Jet", qty=1, universe=1, address=20)
            e.act("add_heads", query="Galaxian 3D", mode="5-channel", qty=1, universe=1, address=30)
            e.act("add_heads", query="Geyser RGB", mode="8-channel", qty=1, universe=1, address=40)
            e.act("add_heads", query="Funfetti Shot", qty=1, universe=1, address=50)

            def f():
                return list(e.build_frames()[1][:52])
            check("classes", [e._head_class(h) for h in e.patch] == ["light", "sfx", "laser", "sfx", "sfx"], "")
            e.act("quick_defaults")
            e.act("select_all")
            check("select all selects only lights", e.selected == [1], str(e.selected))
            e.act("set_intensity", level=100)
            e.act("quick_press", id="q1-1", down=True)
            fr = f()
            check("Flash all flashes the spot and never touches CO2, laser, fog or confetti",
                  fr[10] == 255 and fr[19] == fr[22] == 0 and fr[29:31] == [0, 0]
                  and fr[39] == 0 and fr[49] == 0, str(fr))
            e.act("quick_press", id="q1-1", down=False)
            e.act("select_heads", heads=[2, 3, 5])
            check("the programmer cannot drive an effect's output",
                  not e.act("set_attribute", attribute="fx_fire", value=255).get("ok"), "")
            check("nor can an effect generator",
                  not e.act("run_fx", attribute="laser_on", wave="square").get("ok"), "")
            check("fire needs ARM", not e.act("fx_fire", heads=[2]).get("ok"), "")
            check("so does a laser", not e.act("fx_laser", heads=[3]).get("ok"), "")
            fx_btn = next(b for b in e.quick if b["kind"] == "sfx")
            check("and an FX button", not e.act("quick_press", id=fx_btn["id"], down=True).get("ok"), "")
            e.act("fx_arm", state=True)
            check("armed: the CO2 jet's safety goes to its enabled value", f()[19] == 127, str(f()[19]))
            e.act("fx_fire", heads=[2], owner="t")
            check("fire opens the valve", f()[22] >= 200, str(f()[22]))
            e.act("fx_fire", heads=[2], owner="t", down=False)
            check("release closes it", f()[22] == 0, "")
            e.act("fx_fire", heads=[2], owner="cap", seconds=0.2)
            _t.sleep(0.3)
            check("every burst stops at its limit, even if a button sticks", f()[22] == 0, "")
            e.act("fx_laser", heads=[3], owner="l")
            check("laser output on (its 'Open' value)", f()[29:31] == [11, 11], str(f()[29:31]))
            e.act("fx_fire", heads=[2, 5], owner="b")
            e.act("blackout", state=1)
            fr = f()
            check("blackout stops fire and laser and disarms",
                  fr[22] == 0 and fr[29:31] == [0, 0] and fr[19] == 0 and not e._sfx_armed(), str(fr))
            e.act("blackout", state=0)
            e.act("fx_fog", heads=[4], level=50, seconds=5)
            check("fog runs without arming", f()[39] == 128, str(f()[39]))
            e.act("fx_kill")
            check("KILL FX stops fog too", f()[39] == 0, "")
            e.act("fx_arm", state=True)
            e.act("fx_fire", heads=[5], owner="c")
            e.fx_runs["fire:c"]["since"] -= 10
            e.act("fx_fire", heads=[5], owner="c", down=False)
            loads = e._sfx_public()["loads"]
            check("a confetti tank counts down (25 s Funfetti)", loads[5]["left"] <= 15.1 and loads[5]["full"] == 25,
                  str(loads))
            e.fx_loads[5] = 0
            check("an empty tank will not fire", not e.act("fx_fire", heads=[5], owner="d").get("ok"), "")
            e.act("fx_reload", heads=[5])
            check("until it is reloaded", e.act("fx_fire", heads=[5], owner="d").get("ok"), "")
            e.act("fx_kill")
            pages = {b["page"] for b in e.quick if b["kind"] in ("sfx", "fog", "laser", "arm", "fxkill")}
            check("an FX page of buttons is made for the rig", pages == {2}, str(pages))
            e.act("load_show", name="nope")
            check("the desk starts (and a show loads) disarmed", not e._sfx_armed(), "")
        finally:
            e.shutdown()


def test_show_building() -> None:
    """Per-cue follow, playback crossfades, and the public readout.

    Both features have a contract that is easy to state and easy to break
    in a way nothing notices - follow in particular, because a cue that
    fails to auto-advance looks exactly like a cue that was never told to.

    The checks live in `tools/_show_check.py` and drive a real Engine.
    """
    print("show building (per-cue follow, crossfades, the public readout)")
    from tools import _show_check

    _show_check.run(check)


def check_js() -> None:
    """node --check every web script: catches the class of bug a browser
    only shows as a blank panel (a stray comma, a missing brace)."""
    print("javascript syntax")
    scripts = sorted((ROOT / "web").glob("*.js")) + sorted(
        (ROOT / "web" / "js").rglob("*.js"))
    if not scripts:
        check("web scripts present", False, "no *.js in web/")
        return
    node = _which("node")
    if node is None:
        print("  skip  node not found - cannot syntax-check the web scripts")
        return
    for path in scripts:
        proc = subprocess.run([node, "--check", str(path)],
                              capture_output=True, text=True)
        check(f"node --check {path.relative_to(ROOT / 'web').as_posix()}",
              proc.returncode == 0,
              (proc.stderr or "").strip()[:160])


def test_realtime(tmp: Path) -> None:
    """Performance ceilings on the real-time path.

    The output thread needs the engine lock every 25 ms at 40 Hz.  These
    are the numbers that keep an operator action from stalling a running
    fade, measured on this machine - generous ceilings, so a normal run
    passes with a wide margin and a real regression fails loudly.
    """
    print("realtime budget (40 Hz = 25 ms per tick)")
    from app import engine as eng
    from app import profiles

    db = tmp / "perf.db"
    fixtures.seed_generics(db)
    profiles.install(db)
    e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "perf_shows",
                   autosave_path=tmp / "perf_autosave.json")
    try:
        budget = 1000.0 / 40.0

        # --- frame building scales, and stays well inside the tick -------
        for heads, ceiling in ((4, budget), (120, budget * 0.6),
                               (250, budget * 0.8)):
            e.act("patch_clear")
            plan = e.plan_addresses([
                {"query": "LED PAR 4ch" if i % 2 == 0 else "intimidator spot 260",
                 "universe": 1 + (i * 2) // 34, "address": 1 + (i * 2) % 34}
                for i in range(heads)])
            e._replace_patch([e._build_head(r, i + 1)
                              for i, r in enumerate(plan)])
            e.act("select_all")
            e.act("set_intensity", level=80)
            e.act("record_cue", playback=1, fade=0)
            e.act("cue_go", playback=1)          # a live playback = worst case
            e.build_frames()                      # warm
            t0 = time.perf_counter()
            for _ in range(20):
                e.build_frames()
            ms = (time.perf_counter() - t0) / 20 * 1000
            check(f"build_frames {heads} heads under {ceiling:.1f} ms",
                  ms < ceiling, f"{ms:.2f} ms")

        # --- a bulk import must NOT hold the lock for the whole read -----
        # 120 rows = 120 fixture lookups.  If the map resolution ever goes
        # back under the lock again, this is the check that catches it.
        e.act("patch_clear")
        csv = ("Headno,Headname,Dmxno,Manufacturer,Type,Chans,X,Y,Z,Role\n" +
               "\n".join(f"{i + 1:04d},Par {i + 1},1-{1 + (i * 4) % 480:03d},"
                         f"Generic,LED PAR 4ch,4,0,0.3,0,generic"
                         for i in range(120)))
        e._a_patch_from_csv(csv=csv)             # warm the fixture cache
        e.act("patch_clear")
        worst = 0.0
        for _ in range(5):
            t0 = time.perf_counter()
            e.act("patch_from_csv", csv=csv)
            worst = max(worst, (time.perf_counter() - t0) * 1000)
        check("patch_from_csv 120 heads keeps the output lock brief",
              worst < budget * 6, f"{worst:.1f} ms worst (ceiling "
              f"{budget * 6:.0f} ms)")

        # --- one fixture lookup per patch, not per row -------------------
        e.act("patch_clear")
        calls = {"n": 0}
        real_search = fixtures.search

        def counting(db_path, query, limit=20):
            calls["n"] += 1
            return real_search(db_path, query, limit)
        fixtures.search = counting
        try:
            eng._FIXTURE_CACHE.clear()
            e.act("patch_from_csv", csv=csv)
            check("fixture lookups are cached across a patch",
                  calls["n"] <= 4, f"{calls['n']} queries for 120 heads")
        finally:
            fixtures.search = real_search

        # --- autosave writes off the output lock ------------------------
        e.act("patch_clear")
        e.act("patch_from_csv", csv=csv)
        payload_ms = 0.0
        with e.lock:
            t0 = time.perf_counter()
            e._autosave_payload()
            payload_ms = (time.perf_counter() - t0) * 1000
        check("autosave serialisation under the lock is cheap",
              payload_ms < budget * 0.5, f"{payload_ms:.2f} ms")
        e._autosave(force=True)
        check("forced autosave still reaches disk",
              (tmp / "perf_autosave.json").is_file(), "no autosave file")
    finally:
        e.shutdown()


def test_discovery() -> None:
    """Unicast discovery against a real Art-Net node on a real socket.

    The sweep is the one piece of this that has to work on a network
    where broadcast is filtered, so it is tested against a node that
    behaves like hardware: a real UDP socket on udp/6454 answering an
    ArtPoll with a real ArtPollReply and emitting real ArtDmx frames.

    Two bugs this caught, both of which would have made discovery report
    "nothing found" on a rig that was answering perfectly well:
      * the sweep polled from udp/6454, and on Windows SO_REUSEADDR lets
        that bind succeed alongside another socket already holding the
        port - the reply was then delivered to the *other* socket and the
        sweep heard nothing.  Nodes reply to the poll's SOURCE port, so
        an ephemeral port is both correct and collision-free.
      * scan()'s reply count only included the sweep's polls when the
        sweep had *succeeded*, so a genuine negative reported "3
        unicast address(es)" after polling 254.
    """
    print("rig discovery (unicast sweep + ArtDmx depth)")
    import socket

    from app import artnet

    # --- the reply we answer with must survive our own parser ------------
    reply = artnet.build_artpollreply("127.0.0.50", ports=(0, 1, 2))
    parsed = artnet.parse_artpollreply(reply)
    check("a built ArtPollReply parses back",
          parsed is not None and parsed["ip"] == "127.0.0.50"
          and parsed["output_ports"] == [0, 1, 2]
          and parsed["name"] == "Test Node", json.dumps(parsed))
    check("a truncated reply is still readable",
          artnet.parse_artpollreply(reply[:40]) is not None, "")
    check("a non-Art-Net packet is rejected",
          artnet.parse_artpollreply(b"hello world padding here") is None, "")

    # --- local subnet discovery -----------------------------------------
    subs = artnet.local_subnets()
    check("a local /24 is discovered without any dependency",
          all(s.count(".") == 3 and s.endswith(".") for s in subs), str(subs))

    # --- a real node on a real socket ------------------------------------
    port = _free_udp_port()
    stop = threading.Event()
    seen = {"polls": 0, "replies": 0, "frames": 0}

    def node() -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return
        s.settimeout(0.15)
        slots = bytearray(512)
        for i in range(40):                       # 40 channels in use
            slots[i] = 200
        addr = None
        while not stop.is_set():
            try:
                data, addr = s.recvfrom(2048)
            except (socket.timeout, TimeoutError):
                # keep emitting: that is how a live rig behaves, and it is
                # the only way channel depth can be observed.  Universe 1,
                # not 0 - the builder is 1-based and says so.
                if addr is None:
                    continue              # nobody has polled us yet
                try:
                    s.sendto(artnet.build_artdmx(1, bytes(slots),
                                                 sequence=seen["frames"] + 1),
                             addr)
                    seen["frames"] += 1
                except (OSError, ValueError):
                    pass
                continue
            except OSError:
                break
            if len(data) < 10 or data[0:8] != b"Art-Net\0":
                continue
            op = int.from_bytes(data[8:10], "little")
            if op == artnet.ARTPOLL_OP:
                seen["polls"] += 1
                try:
                    s.sendto(artnet.build_artpollreply("127.0.0.1", port=port,
                                                       ports=(0,)), addr)
                    seen["replies"] += 1
                except OSError:
                    pass

    thread = threading.Thread(target=node, daemon=True)
    thread.start()
    try:
        # the node must be up before we poll
        deadline = time.monotonic() + 2.0
        while seen["polls"] == 0 and time.monotonic() < deadline:
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probe.settimeout(0.3)
                probe.sendto(artnet.build_artpoll(), ("127.0.0.1", port))
                probe.recvfrom(2048)
                probe.close()
            except OSError:
                time.sleep(0.05)

        found = artnet.sweep(subnets=["127.0.0."], timeout=1.6, port=port)
        check("the sweep finds a node that answers unicast",
              len(found["nodes"]) == 1
              and found["nodes"][0]["ip"] == "127.0.0.1",
              json.dumps(found["nodes"]))
        check("the sweep reports the node's output port as a universe",
              [u["universe"] for u in found["universes"]] == [1],
              json.dumps(found["universes"]))
        check("the sweep counts every address it polled",
              found["swept"] == 254 and found["polls_sent"] == 254,
              json.dumps({k: found[k] for k in ("swept", "polls_sent")}))
        check("the sweep observed the node's live DMX depth",
              any(u["channels"] == 40 for u in found["universes"]),
              json.dumps(found["universes"]))
        check("the sweep binds an ephemeral port, not 6454",
              found["bound_port"] not in (0, artnet.ART_NET_PORT)
              and found["bound_port"] > 0, str(found["bound_port"]))

        # --- scan() falls through to the sweep and merges the results ----
        got = artnet.scan(timeout=0.6, port=port, sweep_timeout=1.2)
        check("scan finds the node too",
              len(got["nodes"]) == 1, json.dumps(got.get("nodes")))
        # scan only sweeps when the broadcast pass finds nothing, so a
        # positive result is legitimately just the 2-3 broadcast polls
        check("scan skips the sweep once it has found the node",
              "unicast sweep" not in got["tried"]
              and got["polls_sent"] <= 4,
              json.dumps({k: got[k] for k in ("polls_sent", "tried")}))
        check("scan says a negative was a real negative",
              "no Art-Net node answered" in got["message"]
              or f"{len(got['nodes'])} node(s)" in got["message"],
              got["message"])
    finally:
        stop.set()
        thread.join(timeout=2.0)

    # --- a genuinely empty network must SAY what it tried ----------------
    empty = artnet.scan(timeout=0.4, port=_free_udp_port(), sweep_timeout=0.4)
    check("an empty network reports no nodes",
          empty["nodes"] == [] and empty["universes"] == [], json.dumps(empty)[:160])
    check("an empty network reports how hard it looked",
          empty["polls_sent"] >= 254 and "unicast" in " ".join(empty["tried"]),
          json.dumps({k: empty[k] for k in ("polls_sent", "tried")}))
    check("an empty network message names the subnets swept",
          "broadcast" in empty["message"] and "unicast" in empty["message"],
          empty["message"])


def test_merge() -> None:
    """The real-time merge, tested on its own (app/merge.py).

    These functions run every 25 ms at 40 Hz and are shared by the wire
    and the visualiser, so they are deliberately free of engine state -
    which means they can be pinned down without building a console.
    """
    print("merge (the real-time core)")
    from app import engine_support as sup
    from app import merge

    # --- role vocabulary -------------------------------------------------
    check("label -> role, plain", sup.channel_role("Dimmer") == "dimmer", "")
    check("label -> role, 16-bit coarse",
          sup.channel_role("Pan (16-bit)") == "pan", "")
    check("label -> role, fine half",
          sup.channel_role("Tilt fine") == "tilt_fine", "")
    check("label -> role, maintenance is unused",
          sup.channel_role("Dimmer Speed") == "unused", "")
    check("label -> role, gobo rotation",
          sup.channel_role("Gobo 1 Rotate") == "gobo_rot", "")
    check("label -> role, unknown is raw",
          sup.channel_role("Zzz Qqq") == "raw", "")

    # --- 16-bit maths ----------------------------------------------------
    check("split 0 / mid / max",
          sup.split_16bit(0) == (0, 0)
          and sup.split_16bit(0x1234) == (0x12, 0x34)
          and sup.split_16bit(65535) == (255, 255), "")
    check("split round-trips through join",
          all(sup.join_16bit(*sup.split_16bit(v)) == v
              for v in (0, 1, 0x00FF, 0x1234, 0xFFFF)), "")
    check("fine_first swaps the pair",
          sup.split_16bit(0x1234, fine_first=True) == (0x34, 0x12), "")
    check("8-bit input scales to full 16-bit",
          sup.logical16(255) == 65535 and sup.logical16(128) == 32896, "")
    try:
        sup.split_16bit("banana")
        check("split rejects non-numbers", False, "no error raised")
    except ValueError:
        check("split rejects non-numbers", True, "")

    # --- pair map --------------------------------------------------------
    fine_of, base_of = merge.pair_map(["pan", "pan_fine", "tilt"])
    check("16-bit pair found in either order",
          fine_of == {0: 1} and base_of == {1: 0},
          f"{fine_of} {base_of}")
    fine_of2, base_of2 = merge.pair_map(["pan_fine", "pan"])
    check("fine-before-base still pairs",
          fine_of2 == {1: 0} and base_of2 == {0: 1},
          f"{fine_of2} {base_of2}")
    check("a fixture without a fine channel has no pairs",
          merge.pair_map(["dimmer", "red", "gobo"]) == ({}, {}), "")

    # --- precedence: HTP intensity, LTP everything else -----------------
    head = {"head_no": 1, "universe": 1, "address": 1, "channels": 4,
            "map": ["dimmer", "red", "green", "blue"],
            "curve": "linear", "role": "par"}
    # programmer 40, playback at 50% carrying 80 -> 40; HTP keeps 40
    vals = merge.resolve_head(head, {1: {"dimmer": 40}},
                              [(50, {1: {"dimmer": 80, "red": 200}})])
    check("HTP: the highest intensity wins", vals["dimmer"] == 40, str(vals))
    # the playback's red has no programmer value, so LTP adopts it
    check("LTP: the only source provides the colour",
          vals["red"] == 200, str(vals))
    # a running effect owns the roles it drives
    vals = merge.resolve_head(head, {1: {"dimmer": 40}},
                              [(100, {1: {"dimmer": 80}})],
                              fx_row={"dimmer": 10})
    check("an effect owns the role it drives", vals["dimmer"] == 10, str(vals))
    # blackout and master come last, on everything
    check("blackout kills intensity but not colour",
          merge.resolve_head(head, {1: {"dimmer": 90, "red": 128}}, [],
                             blackout=True)["dimmer"] == 0, "")
    check("grand master scales intensity",
          merge.resolve_head(head, {1: {"dimmer": 100}}, [],
                             master=50)["dimmer"] == 50, "")
    check("an untouched head is dark, not undefined",
          merge.resolve_head(head, {}, [])["dimmer"] == 0, "")

    # --- frames ----------------------------------------------------------
    frames = merge.build_frames([head], {1: {"dimmer": 100, "red": 255,
                                             "green": 0, "blue": 0}}, [])
    buf = frames[1]
    check("one 512-byte frame per universe",
          len(frames) == 1 and len(buf) == 512, str({u: len(b) for u, b in
                                                      frames.items()}))
    check("full dimmer writes 255", buf[0] == 255, str(buf[0]))
    check("colour lands on its own channel", buf[1] == 255, str(buf[1]))
    check("a colour we did not touch stays 0", buf[2] == 0 and buf[3] == 0, "")

    # 16-bit pair -> two bytes carrying ONE logical value
    head16 = {"head_no": 2, "universe": 1, "address": 10, "channels": 4,
              "map": ["pan", "pan_fine", "tilt", "tilt_fine"],
              "curve": "linear", "role": "spot"}
    buf = merge.build_frames([head16], {2: {"pan": 0x1234}}, [])[1]
    check("16-bit pan split across two bytes",
          buf[9] == 0x12 and buf[10] == 0x34,
          f"{buf[9]},{buf[10]}")
    check("the untouched 16-bit pair writes zero",
          buf[11] == 0 and buf[12] == 0, f"{buf[11]},{buf[12]}")

    # intensity 0-100 goes through the curve
    curved = dict(head, curve="square")
    check("the dimmer curve is applied on the wire",
          merge.build_frames([curved], {1: {"dimmer": 50}}, [])[1][0] == 63,
          str(merge.build_frames([curved], {1: {"dimmer": 50}}, [])[1][0]))

    # a head at the end of a universe must not write past it
    edge = {"head_no": 3, "universe": 1, "address": 512, "channels": 4,
            "map": ["dimmer", "red", "green", "blue"],
            "curve": "linear", "role": "par"}
    try:
        frames = merge.build_frames([edge], {3: {"dimmer": 255}}, [])
        check("a straddling head writes what fits and no more",
              len(frames[1]) == 512 and frames[1][511] == 255, "overflow")
    except Exception as exc:                        # noqa: BLE001
        check("a straddling head writes what fits and no more", False, str(exc))

    # two universes -> two frames, independent
    other = dict(head, head_no=4, universe=3, address=1)
    frames = merge.build_frames([head, other], {1: {"dimmer": 255},
                                                4: {"dimmer": 10}}, [])
    check("each universe gets its own frame",
          sorted(frames) == [1, 3] and frames[1][0] == 255
          and frames[3][0] == 25, str({u: b[0] for u, b in frames.items()}))

    # --- the merge must not touch the disk or the network ----------------
    # Checked on the parsed tree, not the text: a docstring that says
    # "no socket" must not fail the check, but an actual import of one
    # must.  This is the property that keeps the 25 ms budget.
    import ast
    tree = ast.parse((ROOT / "app" / "merge.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("merge.py imports nothing that can block",
          not (imported & {"sqlite3", "socket", "http", "urllib",
                            "requests", "subprocess", "time", "os",
                            "pathlib", "json"}),
          str(sorted(imported)))
    check("merge.py has no blocking calls at all",
          not [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ("open", "sleep", "input")],
          "")


def test_engine_api() -> None:
    """The new console actions the UI depends on (set_place, set_venue,
    look_feed) - the pieces the visualiser and the patch highlight call."""
    print("console api: place, venue, look feed")
    from app import engine as eng
    from app import fixtures

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "api.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("add_heads", query="intimidator spot 260", qty=1)

            # --- set_place: the 3D drag -------------------------------
            r = e.act("set_place", head=1, x=2.5, y=3.0, z=1.5)
            check("set_place moves a head",
                  r["ok"] and r["moved"] and r["x"] == 2.5
                  and r["y"] == 3.0 and r["z"] == 1.5, json.dumps(r))
            check("a head above 2 m becomes a truss fixture",
                  r["kind"] == "truss", r["kind"])
            e.act("set_place", head=1, y=0.4)
            check("a head on the deck is a floor fixture",
                  e.snapshot()["patch"][0]["kind"] == "floor",
                  str(e.snapshot()["patch"][0]["kind"]))
            check("set_place refuses a bad number",
                  e.act("set_place", head=1, x="banana")["ok"] is False, "")
            check("set_place refuses an unpatched head",
                  e.act("set_place", head=99, x=1)["ok"] is False, "")
            check("set_place needs a head",
                  e.act("set_place", x=1)["ok"] is False, "")

            # --- stage bounds ------------------------------------------
            # A 3D drag is a ray/plane intersection, so a light far from
            # the camera amplifies a small screen nudge into a large world
            # jump.  Repeated nudges used to walk a head to x=28 in an 18 m
            # room, where the operator cannot see it again.
            check("a head cannot be dragged off the stage",
                  e.act("set_place", head=1, x=9999, z=9999)["x"] <= 40.0
                  and e.act("set_place", head=1, x=9999, z=9999)["z"] <= 60.0,
                  json.dumps(e.act("set_place", head=1, x=9999, z=9999)))
            r = e.act("set_place", head=1, x=9999, y=-50, z=9999)
            check("the clamp is reported, not silent",
                  r["ok"] and r["clamped"] is True
                  and "clamped" in r["summary"], json.dumps(r)[:200])
            check("the clamp keeps the bounds in the result",
                  r["bounds"]["half_width_m"] == 40.0
                  and r["bounds"]["max_depth_m"] == 60.0, json.dumps(r["bounds"]))
            check("height cannot go below the floor",
                  r["y"] == 0.0, str(r["y"]))
            check("a head inside the bounds is not clamped",
                  e.act("set_place", head=1, x=1.0, y=3.0, z=2.0)["clamped"]
                  is False, "")
            # once a venue exists the bounds follow it (room back at -1)
            e.act("venue_room", width=18, depth=12, height=6, back=-1)
            r = e.act("set_place", head=1, x=500, z=500)
            check("the venue tightens the bounds",
                  abs(r["x"]) <= 13.0 and r["z"] <= 15.0, json.dumps(r)[:200])
            check("the venue also caps the height",
                  e.act("set_place", head=1, y=999)["y"] <= 10.0,
                  str(e.act("set_place", head=1, y=999)["y"]))

            # --- set_venue: the room the operator drew ------------------
            r = e.act("set_venue", width_m=18, depth_m=12, height_m=6,
                      name="Main Hall",
                      surfaces=[{"kind": "wall", "x1": -9, "y1": 0, "z1": 12,
                                 "x2": 9, "y2": 0, "z2": 12},
                                {"kind": "truss", "x1": -8, "y1": 5, "z1": 3,
                                 "x2": 8, "y2": 5, "z2": 3}])
            check("an old-style room converts to a v2 venue",
                  r["ok"] and r["venue"]["version"] == 2
                  and r["venue"]["name"] == "Main Hall"
                  and r["venue"]["stage"]["width"] == 18
                  and r["venue"]["room"]["height"] == 6, json.dumps(r)[:300])
            check("its truss line becomes rigging and its wall an object",
                  [x["kind"] for x in r["venue"]["rigging"]] == ["truss"]
                  and [x["kind"] for x in r["venue"]["objects"]] == ["wall"],
                  json.dumps(r["venue"])[:300])
            r = e.act("set_venue", width_m=10, surfaces=[{"kind": "x"},
                                                         "not a dict", 7])
            check("a malformed surface is dropped, not fatal",
                  r["ok"] and r["venue"]["rigging"] == []
                  and r["venue"]["objects"] == [], json.dumps(r)[:300])

            # --- look_feed: the per-tick light feed ---------------------
            e.act("select_all")
            e.act("set_intensity", level=100)
            feed = e.look_feed()
            check("look feed is sequenced and complete",
                  feed["seq"] >= 1 and feed["full"] is True
                  and len(feed["heads"]) == 3
                  and all(h["a"] > 0 for h in feed["heads"]),
                  json.dumps(feed)[:200])
            check("look feed rows carry colour + level",
                  all("hex" in h and "a" in h for h in feed["heads"]),
                  json.dumps(feed["heads"][:1]))
            # a hint one tick behind -> incremental; a stale hint -> full
            nxt = e.look_feed(since=feed["seq"])
            check("a fresh sequence is incremental",
                  nxt["seq"] == feed["seq"] + 1 and nxt["full"] is False,
                  json.dumps(nxt)[:120])
            check("a stale sequence forces a full snapshot",
                  e.look_feed(since=1)["full"] is True, "")

            # a dark head is NOT in the feed (that is the whole point)
            e.act("clear_programmer")
            dark = e.look_feed()
            check("the feed only carries emitting heads",
                  dark["heads"] == [] and dark["lit"] == 0
                  and dark["count"] == 3, json.dumps(dark)[:160])

            # it must agree with the wire, because it is the same merge
            e.act("set_intensity", level=100)
            frames = e.build_frames()
            a_on_wire = frames[1][0]
            a_on_feed = e.look_feed()["heads"][0]["a"]
            check("the feed and the wire agree",
                  abs(a_on_wire / 255 - a_on_feed) < 0.01,
                  f"wire {a_on_wire} feed {a_on_feed}")

            # --- stale_heads: the "playback does nothing" diagnosis -----
            e.act("record_cue", playback=1)
            check("a fresh cue has no stale heads",
                  e.snapshot()["stale_heads"] == [],
                  str(e.snapshot()["stale_heads"]))
            e.act("remove_heads", head=3)
            stale = e.snapshot()["stale_heads"]
            check("removing a head it was pointing at is reported",
                  stale == [3], str(stale))
            check("stale heads also ride the lite feed",
                  e.lite()["stale_heads"] == [3],
                  str(e.lite()["stale_heads"]))
            # loading a show that references unpatched heads says so too
            saved = e.act("save_show", name="with-stale")["file"]
            r = e.act("load_show", name=saved)
            check("load_show names the unpatched heads in its result",
                  r["ok"] and r["stale_heads"] == [3],
                  json.dumps(r)[:160])
        finally:
            e.shutdown()
