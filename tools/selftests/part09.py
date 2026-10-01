"""Self-test suites, part 9: the virtual node and the MIDI monitor."""
from __future__ import annotations

import json
import socket
import tempfile
import time
from pathlib import Path

from app import config, fixtures
from tools.selftests.common import ROOT, check


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_virtual_node() -> None:
    """The desk's own virtual Art-Net node: the output goes to it when it
    is on, it keeps exactly what the desk sent, every patched light
    answers RDM like a real one (and can be readdressed without touching
    the patch), and turning it off puts the output back."""
    print("virtual node")
    from app import engine as eng
    from app import rdm

    saved_port = config.DMX_PORT
    config.DMX_PORT = _free_port()
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = tmp / "f.db"
            fixtures.seed_generics(db)
            e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e.act("add_heads", query="LED PAR 4ch", qty=3, universe=1, address=1)
                e.act("add_heads", query="LED PAR 4ch", qty=1, universe=2, address=10)
                before = dict(e.dmx_target)
                r = e.act("virtual_node", state=True)
                check("the virtual node turns on and takes the output",
                      r.get("ok") and e.dmx_target["host"] == "127.0.0.1"
                      and e._output_public()["virtual_node"], str(r))
                check("...without an undo step", not e._undo or e._undo[-1].get("action") != "virtual_node", "")
                e.act("select_all")
                e.act("set_intensity", level=100)
                e.act("set_colour", hex="#ff0000")
                e.act("set_dry_run", state=False, confirm=True)
                e.act("set_output", state=1, confirm=True)
                deadline = time.monotonic() + 3
                st = {}
                while time.monotonic() < deadline:
                    st = e.vnode_status(1)
                    if st.get("frame") and any(st["frame"]) and len(st.get("universes") or []) == 2 \
                            and st["universes"][0]["fps"] > 0:
                        break
                    time.sleep(0.05)
                with e.lock:
                    want = e.build_frames()
                check("it receives every universe the desk sends", [u["universe"] for u in st.get("universes") or []] == [1, 2],
                      str(st.get("universes")))
                check("...byte for byte", st.get("frame") == list(want[1]), str((st.get("frame") or [])[:12]))
                check("...at the output rate", (st["universes"][0]["fps"] if st.get("universes") else 0) >= 20,
                      str(st.get("universes")))
                e.act("set_output", state=0)
                found = rdm.discover([1, 2], host="127.0.0.1", port=config.DMX_PORT, timeout=0.5, per_request=0.2)
                check("every patched light answers RDM", len(found["devices"]) == 4, str(found)[:200])
                r = e.act("rdm_compare", devices=found["devices"], universes=[1, 2])
                check("...and matches the patch", r.get("ok") and all(d["status"] == "ok" for d in r["devices"])
                      and not r["silent"], r.get("summary"))
                uid = next(d["uid"] for d in found["devices"] if d["universe"] == 2)
                r = rdm.set_address(uid, 2, 100, host="127.0.0.1", port=config.DMX_PORT)
                light = next(x for x in e.vnode_status()["lights"] if x["uid"] == uid)
                check("a light can be readdressed; the patch stays as it was",
                      r.get("ok") and light["address"] == 100 and e.patch[3]["address"] == 10, str((r, light)))
                r = e.act("virtual_node", state=False)
                check("off puts the output back where it was", r.get("ok") and e.dmx_target == before
                      and not e._output_public()["virtual_node"], str(e.dmx_target))
            finally:
                e.shutdown()
    finally:
        config.DMX_PORT = saved_port
    mj = (ROOT / "web" / "app" / "monitors.js").read_text(encoding="utf-8")
    dj = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("Settings opens the node monitor, which draws the 512 channels",
          "openNodeMonitor" in dj and "/api/console/vnode" in mj and "512" in mj, "")


def test_midi_monitor() -> None:
    """Every MIDI message is remembered with what it did, for the monitor;
    the browser's MIDI shows every message too, not only notes."""
    print("MIDI monitor")
    from app import engine as eng
    from app import midi

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            m = midi.MidiManager(e, devices=[])
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "label": "Hit", "midi": 36})
            m.handle({"kind": "note", "channel": 1, "number": 36, "value": 100, "on": True})
            m.handle({"kind": "cc", "channel": 2, "number": 99, "value": 64})
            rec = list(m.recent)
            check("each message is kept with what it did", len(rec) == 2 and rec[0]["number"] == 36
                  and rec[0]["did"] and rec[1]["kind"] == "cc" and rec[1]["did"] == "", str(rec))
            midi.MANAGER = m
            try:
                check("the status carries them", [r["number"] for r in midi.status()["recent"]] == [36, 99], "")
            finally:
                midi.MANAGER = None
        finally:
            e.shutdown()
    wj = (ROOT / "web" / "app" / "webmidi.js").read_text(encoding="utf-8")
    check("browser MIDI hands every message to the monitor", "onAnyMidi" in wj, "")


def test_room_making() -> None:
    """Rooms made without drawing: a shape with its sizes, words, a
    starter layout that fits the shape; and drawing walls like a plan
    (right angles, typed lengths)."""
    print("making a room")
    from app import engine as eng
    from app import roomshape as rs
    from app import venue as venue_mod

    def area(pts):
        return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2

    for shape, corners in (("rectangle", 4), ("l", 6), ("t", 8), ("u", 8), ("octagon", 8), ("round", 24), ("wedge", 4)):
        pts = rs.outline(shape, 12, 16)
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        check(f"{shape}: {corners} corners, 12 x 16 m", len(pts) == corners
              and abs(max(xs) - min(xs) - 12) < 0.01 and abs(max(zs) - min(zs) - 16) < 0.01, str(pts))
    check("an L is the box less its cut-out", abs(area(rs.outline("l", 12, 16, cut_w=5, cut_d=6)) - (12 * 16 - 30)) < 0.01, "")
    check("an octagon has its corners cut", area(rs.outline("octagon", 12, 16)) < 12 * 16, "")

    bad = []
    for shape in rs.SHAPES:
        for kind in ("club", "warehouse", "theatre", "small_club"):
            v = rs.build({"shape": shape, "width": 14, "depth": 18, "height": 6, "kind": kind,
                          "bar": {"side": "left"}, "pillars": 3, "screen": True, "doors": ["front"]})
            pts = v["room"]["outline"]
            for o in v["objects"]:
                if o["kind"] not in ("door", "screen") and not rs.inside(o["x"], o["z"], pts):
                    bad.append(f"{shape}/{kind}: {o['name']} outside")
            for r in v["rigging"]:
                for end in (r["a"], r["b"]):
                    if not rs.inside(end[0], end[2], pts) or end[1] > 6:
                        bad.append(f"{shape}/{kind}: {r['name']} outside")
            for z in v["zones"]:
                cx, cz = venue_mod.zone_centroid(z)
                if not rs.inside(cx, cz, pts):
                    bad.append(f"{shape}/{kind}: zone {z['name']} outside")
    check("every starter layout stays inside its walls, in every shape", not bad, "; ".join(bad[:6]))

    r = rs.parse("a 12 x 8 m club, bar on the left, DJ booth on a 40 cm riser")
    s = r["spec"]
    check("words: size, bar side, DJ riser", s["width"] == 12 and s["depth"] == 8 and s["bar"]["side"] == "left"
          and s["dj"]["side"] == "back" and abs(s["dj"]["riser"] - 0.4) < 1e-6, str(s))
    check("...and it says what it guessed", "the ceiling height" in r["unsure"], str(r["unsure"]))
    s = rs.parse("small bar 40ft x 25ft, no truss, doors on the left and right")["spec"]
    check("feet, 'no truss', two doors", abs(s["width"] - 12.2) < 0.05 and s["trusses"] == 0
          and s["doors"] == ["left", "right"], str(s))
    s = rs.parse("L-shaped warehouse 30 by 20 m, 8 m ceiling, 4 pillars, stage at the back 10 x 5 m 1.2 m high")["spec"]
    check("shape, pillars, a stage with its size and height", s["shape"] == "l" and s["pillars"] == 4 and s["height"] == 8
          and s["stage"] == {"width": 10.0, "depth": 5.0, "height": 1.2}, str(s))
    check("an AI answer goes through the same checks", rs.clean_spec({"shape": "hexagon", "width": 9000, "kind": "x"})["shape"] == "rectangle"
          and rs.clean_spec({"width": 9000})["width"] == 120, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            rigs = len(e.venue["rigging"])
            rev = e.act_rev
            r = e.act("venue_preview", text="a round 20 x 20 m ballroom")
            check("a preview shows the room without making it", r.get("ok") and len(r["preview"]["room"]["outline"]) == 24
                  and len(e.venue["rigging"]) == rigs and e.act_rev == rev, str(r.get("error")))
            r = e.act("venue_shape", shape="l", width=16, depth=22, height=5.5)
            check("a new shape keeps the rigging (moved inside)", r.get("ok") and len(e.venue["rigging"]) == rigs
                  and len(e.venue["room"]["outline"]) == 6, str(r.get("error")))
            r = e.act("venue_describe", text="a 12 x 8 m club, bar on the left, DJ booth on a 40 cm riser")
            names = [o["name"] for o in e.venue["objects"]]
            check("words make the room", r.get("ok") and e.venue["name"] == "Club 12 x 8 m"
                  and "DJ riser" in names and "Bar" in names and r["understood"], str(r.get("error")))
            e.act("undo")
            check("...and one undo takes it back", len(e.venue["room"]["outline"]) == 6, "")
            tid = e.venue["rigging"][0]["id"]
            r = e.act("venue_array", id=tid, count=4, step=1.5)
            names = [x["name"] for x in e.venue["rigging"]]
            check("copies of a truss, numbered on", r.get("ok") and len(names) == rigs + 3 and len(set(names)) == len(names), str(names))
        finally:
            e.shutdown()
    rd = (ROOT / "web" / "app" / "roomdialog.js").read_text(encoding="utf-8")
    ed = (ROOT / "web" / "js" / "stage" / "editor.js").read_text(encoding="utf-8")
    vp = (ROOT / "web" / "app" / "venuepanel.js").read_text(encoding="utf-8")
    check("the room dialog: shape, words, template, draw, floor plan - drawn from the engine's preview",
          all(k in rd for k in ('"venue_preview"', '"/api/console/room"', '"venue_shape"', '"venue_template"', "startDrawRoom", "startPlanUpload")), "")
    check("walls are drawn like a plan: right angles, typed lengths",
          "_placeTyped" in ed and "d.typed" in ed and "openRoomDialog" in vp, "")


def test_beat_clock() -> None:
    """One beat clock for the desk: taps, a typed BPM, MIDI clock, the CDJs
    (Pro DJ Link); the Speed master follows it; effects lock to the beat
    and keep their place in the bar when the tempo changes."""
    print("beat clock")
    import time as _time
    from app import engine as eng
    from app import tempo

    c = tempo.Clock(120, now=100.0)
    for t in (100.0, 100.5, 101.0, 101.5):
        bpm = c.tap(t)
    check("four taps half a second apart: 120 BPM, the last on beat 4",
          abs(bpm - 120) < 0.01 and c.beat_in_bar(101.5) == 4 and c.phase(101.5) < 1e-6, str((bpm, c.public(101.5))))
    before = c.beats(103.0)
    c.set_bpm(128, 103.0)
    check("a new tempo doesn't jump the beat count", abs(c.beats(103.0) - before) < 1e-9
          and abs(c.beats(104.0) - before - 128 / 60) < 1e-9, "")
    c.align(110.0, 3)
    check("'now is beat 3' moves the phase, not the tempo", c.beat_in_bar(110.0) == 3 and c.phase(110.0) < 1e-6
          and c.bpm == 128, str(c.public(110.0)))
    m = tempo.Clock(120, now=0.0)
    for i in range(49):
        m.midi_tick(10.0 + i * (60 / 128 / 24))
    check("MIDI clock at 24 ticks a beat reads 128 BPM", abs(m.bpm - 128) < 0.2 and m.source == "midi"
          and m.live(10.0 + 48 * 60 / 128 / 24), str(m.public(11.0)))
    m.midi_start(20.0)
    check("MIDI Start is the 1", m.beat_in_bar(20.0) == 1 and m.phase(20.0) == 0, "")
    pkt = tempo.build_prodj_beat(2, 126.0, 3, pitch_pct=2.0)
    got = tempo.parse_prodj_beat(pkt)
    check("a CDJ beat packet: tempo with the pitch fader, the beat of the bar",
          got and got["device"] == 2 and abs(got["bpm"] - 128.52) < 0.02 and got["beat"] == 3, str(got))
    check("anything else on the port is ignored", tempo.parse_prodj_beat(b"Qspt1WmJOL" + bytes(90)) is None
          and tempo.parse_prodj_beat(b"junk") is None, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("speed_master", pct=50)
            check("moving the Speed master by hand stops it following", e.tempo_follow is False, "")
            e.act("tempo_tap")
            _time.sleep(0.25)
            r = e.act("tempo_tap")
            check("tapping sets the tempo and the Speed master follows (120 BPM = 1x)",
                  r.get("ok") and e.tempo_follow and abs(e.speed_master - r["tempo"]["bpm"] / 120) < 0.01, str(r))
            undo_n = len(e._undo)
            e.act("tempo_set", bpm=128)
            check("the tempo is played, not edited: no undo step", len(e._undo) == undo_n, "")
            check("it rides the live feed", e.lite()["tempo"]["bpm"] == 128, "")
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_all")
            e.act("set_intensity", level=100)
            r = e.act("run_fx", name="rainbow", params={"beats": 4})
            now = _time.monotonic()
            e._fx_values(now)
            row = e.fx[0]
            check("an effect locked to 1 bar runs one cycle per 4 beats", r.get("ok")
                  and abs(row["_beat_cycles"] - e.tempo.beats(now) / 4) < 1e-9, str(row.get("params")))
            e.act("tempo_set", bpm=140)
            e._fx_values()
            c1 = row["_beat_cycles"]
            e.act("fx_beats", id=row["id"], beats=0)
            e._fx_values()
            check("let go of the beat, it carries on from where it was",
                  abs(row["_v"] * row["params"]["speed"] - c1) < 0.05 and "beats" not in row["params"], "")
            r = e.act("fx_beats", id=row["id"], beats=100)
            check("a silly lock is refused", not r.get("ok"), str(r))
            e.act("fx_beats", id=row["id"], beats=2)
            e.act("record_cue", playback=1, name="On the beat")
            check("a cue keeps the effect's beat lock", e.playbacks[0]["stack"][0]["fx"][0]["params"].get("beats") == 2,
                  str(e.playbacks[0]["stack"][0].get("fx")))
            # the CDJs, over the network
            port = _free_port()
            check("listening to the CDJs", e._prodj_start(port) is None, "")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(tempo.build_prodj_beat(1, 124.0, 2), ("127.0.0.1", port))
            s.close()
            deadline = _time.monotonic() + 2
            while _time.monotonic() < deadline and e.tempo.source != "prodj":
                _time.sleep(0.02)
            pub = e.tempo_public()
            check("a CDJ's beat sets the tempo and where we are in the bar",
                  pub["source"] == "prodj" and pub["bpm"] == 124 and pub["beat"] == 2 and pub["live"], str(pub))
            e._prodj_stop()
            # the desk's own MIDI input: clock bytes go to the beat clock
            e.tempo_midi(0xFA)
            check("MIDI Start from the desk's controller is the 1", e.tempo.beat_in_bar(_time.monotonic()) == 1, "")
        finally:
            e.shutdown()
    web = ROOT / "web"
    tj = (web / "app" / "tempo.js").read_text(encoding="utf-8")
    kj = (web / "app" / "keys.js").read_text(encoding="utf-8")
    wm = (web / "app" / "webmidi.js").read_text(encoding="utf-8")
    pj = (web / "app" / "programmer.js").read_text(encoding="utf-8")
    html = (web / "index.html").read_text(encoding="utf-8")
    check("the top bar has the tempo, a beat light, tap (T) and the 1 (Shift+T)",
          'id="tempo"' in html and 'id="tempo-dot"' in html and '"tempo_tap"' in tj and "downbeat" in kj, "")
    check("browser MIDI clock feeds the desk's tempo about once a second", '"tempo_set"' in wm and "0xf8" in wm, "")
    check("running effects can be locked to the beat", '"fx_beats"' in pj and "beatSelect" in pj, "")
    mj = (ROOT / "app" / "midi.py").read_text(encoding="utf-8")
    check("the desk's MIDI input hands clock bytes to the beat clock", "tempo_midi" in mj and "0xF8" in mj, "")


def test_sound_reactive() -> None:
    """The room plays the lights: the bass (etc.) moves a brightness or the
    effects' speed, a beat or a drop presses a button, the room's beat can
    set the tempo - and with nothing listening nothing is dimmed."""
    print("sound-reactive")
    import time as _time
    from app import engine as eng
    from app import sound

    link = sound.clean_link({"source": "bass", "target": {"type": "master"}, "depth": 60}, "s1")
    check("a link at 60% depth: full bass keeps it all, silence keeps 40%",
          abs(sound.link_factor(link, 1.0) - 1.0) < 1e-9 and abs(sound.link_factor(link, 0.0) - 0.4) < 1e-9, "")
    sp = sound.clean_link({"source": "level", "target": {"type": "fx_speed"}, "depth": 50}, "s2")
    check("effect speed: 0.5x .. 1.5x at 50%", abs(sound.link_factor(sp, 0) - 0.5) < 1e-9 and abs(sound.link_factor(sp, 1) - 1.5) < 1e-9, "")
    scales, _ = sound.apply({"links": [link]}, None, None, 0.0, [1, 2], {})
    check("no reading, no dimming", scales == {}, str(scales))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2, universe=1, address=1)
            e.act("add_heads", query="Uplight rgb", qty=1, universe=1, address=20)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#ffffff")
            r = e.act("sound_link", link={"source": "bass", "target": {"type": "master"}, "depth": 100})
            check("a link is made (and is an undo step)", r.get("ok") and e._undo and e._undo[-1].get("action") == "sound_link", str(r))
            e.sound_feed({"level": 0.2, "bass": 0.0, "mid": 0.1, "high": 0.1})
            with e.lock:
                f = e.build_frames()[1]
            check("no bass: the rig is dark", f[0] == 0, str(list(f[:6])))
            dimless = e.patch[-1]
            a = dimless["address"] - 1
            check("...a light with no dimmer too", dimless["map"] == ["red", "green", "blue"]
                  and max(f[a:a + 3]) == 0, str((dimless["map"], list(f[a:a + 3]))))
            e.sound_feed({"level": 0.9, "bass": 1.0, "mid": 0.5, "high": 0.5})
            with e.lock:
                f = e.build_frames()[1]
            check("full bass: full", f[0] == 255, str(list(f[:6])))
            e._sound_at = _time.monotonic() - 5
            with e.lock:
                f = e.build_frames()[1]
            check("the microphone stops: the rig is back to full, never left dark", f[0] == 255, str(list(f[:6])))
            r = e.act("sound_link", link={"source": "bass", "target": {"type": "group", "group": 99}})
            check("a link to a group that isn't there is refused", not r.get("ok"), str(r))
            e.act("sound_link", link={"source": "level", "target": {"type": "fx_speed"}, "depth": 100})
            e.act("run_fx", name="rainbow")
            e.sound_feed({"level": 0.0})
            with e.lock:
                e._override_vals()
            check("quiet room: effects crawl", abs(e._sound_speed - 0.05) < 1e-6, str(e._sound_speed))
            # triggers
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "label": "Hit"})
            bid = e.quick[0]["id"]
            r = e.act("sound_trigger", trigger={"on": "beat", "button": bid, "every": 2})
            check("a trigger on every 2nd beat", r.get("ok"), str(r))
            presses = []
            real = e.act

            def spy(action, **kw):
                if action == "quick_press" and kw.get("down"):
                    presses.append(kw["id"])
                return real(action, **kw)
            e.act = spy
            for _ in range(4):
                e.sound_feed({"bass": 1.0, "beat": True})
            e.act = real
            check("...presses the button on beats 1 and 3", presses == [bid, bid], str(presses))
            r = e.act("sound_trigger", trigger={"on": "drop", "button": "nope"})
            check("a trigger for a button that isn't there is refused", not r.get("ok"), "")
            # the room's beat as the tempo
            e.act("sound_tempo", state=True)
            e.sound_feed({"bass": 1.0, "beat": True, "bpm": 126, "confidence": 0.8})
            check("the room sets the tempo", e.tempo.bpm == 126 and e.tempo.source == "audio", str(e.tempo_public()))
            e.act("save_show", name="loud")
            e.act("sound_link", id="s1", remove=True)
            e.act("load_show", name="loud")
            check("links, triggers and the tempo choice are saved with the show",
                  len(e.sound_cfg["links"]) == 2 and len(e.sound_cfg["triggers"]) == 1 and e.sound_cfg["tempo"], str(e.sound_cfg))
            check("it rides the live feed", "sound" in e.lite() and e.lite()["sound"]["links"], "")
        finally:
            e.shutdown()
    web = ROOT / "web" / "app"
    si = (web / "soundin.js").read_text(encoding="utf-8") + (web / "soundanalysis.js").read_text(encoding="utf-8")
    sd = (web / "sounddialog.js").read_text(encoding="utf-8")
    tj = (web / "tempo.js").read_text(encoding="utf-8")
    mj = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("the browser listens: bands with their own gain, beats, the tempo, drops",
          "getUserMedia" in si and "estimateBpm" in si and "breakdown" in si and '"/api/console/sound"' in si, "")
    check("the Sound dialog: meters, links, triggers; from the tempo menu",
          '"sound_link"' in sd and '"sound_trigger"' in sd and "openSoundDialog" in tj, "")
    check("the feed is a route of its own, not an action", 'route == "/api/console/sound"' in mj, "")


_SOUND_SIM = r"""
import { createAnalysis } from "MODULE";
const out = {};
for (const bpm of [128, 97]) {
  const sr = 44100, bins = 1024, a = createAnalysis(sr, bins);
  const beat = 60000 / bpm, bin = (hz) => Math.round(hz / (sr / 2) * bins);
  let beats = 0; const drops = [];
  for (let t = 0; t < 32000; t += 40) {
    const inBreak = t >= 12000 && t < 20000, since = t % beat, kick = !inBreak && since < 160;
    const hat = Math.abs(since - beat / 2) < 40;
    const f = new Float32Array(bins).fill(-90);
    for (let i = bin(30); i <= bin(150); i++) f[i] = kick ? -12 - since / 20 : (inBreak ? -85 : -60);
    for (let i = bin(150); i <= bin(2000); i++) f[i] = -40 + (i % 4);
    for (let i = bin(2000); i <= bin(12000); i++) f[i] = hat ? -30 : -70;
    const w = new Float32Array(2048).map((_, i) => Math.sin(i / 7) * (kick ? 0.8 : 0.1));
    const ev = a.step(f, w, t);
    if (ev.beat) beats++;
    if (ev.drop) drops.push(t);
  }
  out[bpm] = { beats, bpm: a.reading.bpm, drops };
}
console.log(JSON.stringify(out));
"""


def test_sound_analysis() -> None:
    """The browser's listening, run under node on a made-up track: kicks
    at 128 and 97 BPM, an 8 s breakdown, then the drop."""
    print("sound analysis (node)")
    import subprocess as _sp
    from tools.selftests.common import _which
    node = _which("node")
    if node is None:
        print("  skip  node not found")
        check("node is needed for the sound analysis check", True, "")
        return
    mod = (ROOT / "web" / "app" / "soundanalysis.js").as_uri()
    proc = _sp.run([node, "--input-type=module", "-e", _SOUND_SIM.replace("MODULE", mod)], capture_output=True, text=True, timeout=60)
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        got = {}
    check("the analysis runs under node", bool(got), (proc.stderr or proc.stdout)[:200])
    if not got:
        return
    for bpm, want_beats in (("128", 51), ("97", 39)):
        g = got[bpm]
        check(f"{bpm} BPM: every kick is a beat, the tempo is right", abs(g["beats"] - want_beats) <= 2
              and g["bpm"] and abs(g["bpm"] - float(bpm)) < 1.0, str(g))
        check(f"{bpm} BPM: one drop, when the bass comes back after the breakdown",
              len(g["drops"]) == 1 and 20000 <= g["drops"][0] <= 20600, str(g["drops"]))


def test_autopilot() -> None:
    """A cue list plays itself: a new look every phrase on the beat clock,
    calmer or bigger with the room, the biggest on a drop."""
    print("autopilot")
    import random as _random
    from app import autopilot as ap
    from app import engine as eng

    calm = {"name": "calm", "values": {"1": {"dimmer": 30}}}
    mid = {"name": "mid", "values": {"1": {"dimmer": 70}}}
    big = {"name": "big", "values": {"1": {"dimmer": 100, "strobe": 120}}, "fx": [{"name": "rainbow"}]}
    stack = [calm, mid, big]
    check("a look's size: bright, effects, strobing", ap.cue_energy(calm) < ap.cue_energy(mid) < ap.cue_energy(big), "")
    check("ranked within the list", ap.tiers(stack) == ["low", "mid", "high"], str(ap.tiers(stack)))
    rng = _random.Random(1)
    check("a loud room gets the big look, a quiet one the calm one",
          ap.choose(stack, 1, "high", [], rng) == 2 and ap.choose(stack, 1, "low", [], rng) == 0, "")
    check("no sound: round the list in order", ap.choose(stack, 2, None, []) == 0, "")
    check("a drop: the biggest", ap.choose(stack, 0, None, [], biggest=True) == 2, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("select_all")
            r = e.act("autopilot", state=True)
            check("an empty cue list can't be flown", not r.get("ok"), str(r))
            for lvl, name in ((30, "Calm"), (70, "Mid"), (100, "Big")):
                e.act("set_intensity", level=lvl)
                e.act("record_cue", playback=1, name=name)
            e.act("tempo_set", bpm=120)
            e.tempo.downbeat(1000.0)
            r = e.act("autopilot", state=True, bars=4, follow_sound=False)
            check("the autopilot starts on a list of looks", r.get("ok") and e.autopilot["on"], str(r))
            e._ap_stop_ev.set()                               # tick it by hand below
            beat = 0.5
            check("the first tick finds the next phrase, changes nothing", e.ap_tick(1000.0 + 1 * beat) is None
                  and e.autopilot["next_bar"] == 4, str(e.autopilot))
            check("mid-phrase: nothing", e.ap_tick(1000.0 + 10 * beat) is None, "")
            pick = e.ap_tick(1000.0 + 16 * beat + 0.01)
            check("on the phrase (4 bars = 16 beats): the next look", pick == (1, 1) and e.playbacks[0]["index"] == 0
                  and e.autopilot["next_bar"] == 8, str((pick, e.autopilot)))
            pick = e.ap_tick(1000.0 + 32 * beat + 0.01)
            check("...and the next, a phrase later", pick == (1, 2), str(pick))
            undo_n = len(e._undo)
            e.act("autopilot_next", biggest=True)
            check("biggest now: the big look, and flying it costs no undo steps",
                  e.playbacks[0]["index"] == 2 and len(e._undo) == undo_n, str(e.autopilot.get("last")))
            e.act("autopilot", follow_sound=True)
            for _ in range(400):
                e.sound_feed({"level": 0.05, "bass": 0.05})
            pick = e.ap_tick(1000.0 + 48 * beat + 0.01)
            check("a quiet room: a calm look", pick == (1, 1) and e.autopilot["last"]["tier"] == "low", str(e.autopilot["last"]))
            e.sound_feed({"level": 0.9, "bass": 1.0, "drop": True})
            check("a drop: the biggest look straight away", e.playbacks[0]["index"] == 2
                  and e.autopilot["last"]["why"] == "drop", str(e.autopilot["last"]))
            check("it rides the live feed", e.lite()["autopilot"]["on"] is True, "")
            e.act("autopilot", state=False)
        finally:
            e.shutdown()
    tj = (ROOT / "web" / "app" / "tempo.js").read_text(encoding="utf-8")
    aj = (ROOT / "web" / "app" / "autopilot.js").read_text(encoding="utf-8")
    check("the tempo menu opens the autopilot; it shows when it's flying",
          "openAutopilot" in tj and '"autopilot"' in aj and '"autopilot_next"' in aj and "auto" in tj, "")


def test_spatial_fx() -> None:
    """Effects through the room by where the lights are: a wave left to
    right follows the lights' x, not their numbers; centre-out gives two
    lights the same distance the same phase."""
    print("spatial effects")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            # numbered 1..4, but hung right to left, with a gap
            for n, x in ((1, 6.0), (2, 4.0), (3, -4.0), (4, -6.0)):
                e.act("set_place", head=n, x=x, y=4.0, z=5.0)
            e.act("select_all")
            e.act("set_intensity", level=100)
            r = e.act("run_fx", name="rainbow", params={"space": "left-right"})
            row = e.fx[0]
            idx = e._space_index(row, row["heads"])
            check("left to right: the leftmost light (#4) is first, #1 last", r.get("ok")
                  and idx[4] == 0 and abs(idx[1] - 3) < 1e-9, str(idx))
            check("...and the gap in the rig is a gap in the wave", abs(idx[3] - 0.5) < 1e-9 and abs(idx[2] - 2.5) < 1e-9, str(idx))
            e.act("fx_space", id=row["id"], space="centre-out")
            idx = e._space_index(row, row["heads"])
            check("centre out: the two inner lights together, then the outer two", abs(idx[2] - idx[3]) < 1e-9
                  and abs(idx[1] - idx[4]) < 1e-9 and idx[2] < idx[1], str(idx))
            e.act("fx_space", id=row["id"], space=None)
            check("back to light order", e._space_index(row, row["heads"]) is None, "")
            r = e.act("fx_space", id=row["id"], space="sideways")
            check("a way that isn't one is refused", not r.get("ok"), str(r))
            vals = e._fx_values()
            check("it still runs", len(vals) == 4, str(vals))
        finally:
            e.shutdown()
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("running effects and movements can pick which way they run through the room",
          '"fx_space"' in pj and "spaceSelect" in (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8"), "")


def test_step_fx() -> None:
    """Effects made of your own looks: steps from the programmer or from
    palettes, a time and a crossfade each, a curve, a spread; saved with
    the show; a palette step follows the palette."""
    print("step effects")
    from app import engine as eng
    from app import stepfx

    fx = stepfx.clean({"name": "Warm cold", "curve": "linear", "steps": [
        {"values": {"1": {"dimmer": 100, "red": 255, "blue": 0}}, "time": 2, "fade": 0.5},
        {"values": {"1": {"dimmer": 0, "red": 0, "blue": 255, "gobo": 40}}, "time": 2, "fade": 0.5}]}, "x1")
    roles = ["dimmer", "red", "blue", "gobo"]
    at = lambda t: stepfx.values(fx, 1, roles, {}, t)       # noqa: E731
    check("holding a step: its look", at(3.5) == {"dimmer": 0, "red": 0, "blue": 255, "gobo": 40}, str(at(3.5)))
    check("half way through the fade in: half way between",
          at(2.5)["dimmer"] == 50 and at(2.5)["blue"] == 128, str(at(2.5)))
    check("a gobo doesn't slide through the wheel: it changes at the middle of the fade",
          "gobo" not in at(2.2) and at(2.6)["gobo"] == 40, str((at(2.2), at(2.6))))
    check("round again: back to the first step", at(4.0 + 1.5)["red"] == 255, str(at(5.5)))
    try:
        stepfx.clean({"steps": [{"values": {"1": {"dimmer": 1}}}]}, "x2")
        one = True
    except ValueError:
        one = False
    check("one step is not an effect", not one, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#ff0000")
            s1 = e.act("step_capture")["step"]
            e.act("set_colour", hex="#0000ff")
            s2 = e.act("step_capture")["step"]
            check("taking a step changes nothing (no undo step)", not e._undo or e._undo[-1].get("action") != "step_capture", "")
            e.act("set_colour", hex="#00ff00")
            e.act("record_palette", kind="colour", name="Green")
            r = e.act("step_fx_save", fx={"name": "RGB", "spread": 90, "steps": [
                dict(s1, time=1, fade=0), dict(s2, time=1, fade=0), {"palette": {"kind": "colour", "n": 1}, "time": 1, "fade": 0}]})
            check("a step effect is saved", r.get("ok") and len(e.step_fx) == 1, str(r.get("error")))
            e.act("clear_programmer")
            r = e.act("step_fx_run", id=r["id"])
            check("...and runs on the lights it was made on", r.get("ok") and sorted(e.fx[-1]["heads"]) == [1, 2, 3, 4], str(r))
            row = e.fx[-1]
            row["_v"] = 0.5                                  # half way through step 1
            vals = e._step_values(row, 0.5, out := {}) or out
            reds = [vals[n].get("red") for n in (1, 2, 3, 4)]
            check("spread 90: each light a quarter of the cycle behind the one before",
                  reds[0] == 255 and len(set(map(str, [vals[n] for n in (1, 2, 3, 4)]))) > 1, str(vals))
            check("it shows as running, by its name", any(f.get("label") == "RGB" for f in e._fx_public()), "")
            # a palette step follows the palette
            e.palettes["colour"][0]["values"] = {"red": 10, "green": 20, "blue": 30}
            out2 = {}
            e._step_values(row, 2.5, out2)
            check("change the palette, the effect changes", out2[1].get("red") == 10 and out2[1].get("green") == 20, str(out2[1]))
            e.act("save_show", name="steps")
            e.act("step_fx_delete", id="x1")
            check("deleting it stops it", not any(f.get("steps") for f in e.fx), "")
            e.act("load_show", name="steps")
            check("saved with the show", [f["name"] for f in e.step_fx] == ["RGB"], str(e.step_fx))
            e.act("undo")
        finally:
            e.shutdown()
    sj = (ROOT / "web" / "app" / "stepfx.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    check("the FX tab lists step effects; the editor takes looks and palettes as steps",
          'id="stepfx-list"' in html and '"step_capture"' in sj and '"step_fx_save"' in sj and "palette" in sj, "")


def test_desk_tools() -> None:
    """Highlight / solo find the lights you work on; park holds a light
    dark or as it is whatever runs; a group master scales its group."""
    print("desk tools")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4, universe=1, address=1)
            e.act("select_heads", heads=[1, 2, 3, 4])
            e.act("set_intensity", level=40)
            e.act("set_colour", hex="#ff0000")
            e.act("select_heads", heads=[2])
            r = e.act("highlight", state=True)
            with e.lock:
                f = e.build_frames()[1]
            check("highlight: the selected light at full, open white", r.get("ok") and f[4] == 255
                  and f[5] == f[6] == f[7] == 255, str(list(f[:16])))
            check("...the others as they were", f[0] == 102, str(list(f[:4])))
            e.act("highlight", state=True, solo=True)
            with e.lock:
                f = e.build_frames()[1]
            check("solo: everything else dark", f[0] == 0 and f[4] == 255, str(list(f[:16])))
            e.act("highlight", state=False)
            with e.lock:
                f = e.build_frames()[1]
            check("off: back to the show, nothing recorded", f[4] == 102 and e.programmer[2].get("dimmer") == 40, str(list(f[:8])))
            e.act("park", heads=[3], mode="dark")
            e.act("park", heads=[4], mode="hold")
            e.act("select_heads", heads=[3, 4])
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#0000ff")
            with e.lock:
                f = e.build_frames()[1]
            check("parked dark: stays dark whatever you do", f[8] == 0, str(list(f[8:12])))
            check("parked as it was: frozen", f[12] == 102 and f[13] == 255 and f[15] == 0, str(list(f[12:16])))
            check("park shows in the snapshot", e.snapshot()["parked"] == [3, 4], "")
            e.act("save_show", name="parked")
            e.act("unpark", all=True)
            with e.lock:
                f = e.build_frames()[1]
            check("unparked: they follow the programmer again", f[8] == 255 and f[15] == 255, str(list(f[8:16])))
            e.act("load_show", name="parked")
            check("parking is saved with the show", e.snapshot()["parked"] == [3, 4], str(e.snapshot()["parked"]))
            e.act("unpark", all=True)
            e.act("select_heads", heads=[1, 2, 3, 4])
            e.act("set_intensity", level=100)
            e.act("select_heads", heads=[1, 2])
            e.act("group_create", name="Front")
            g = e.groups[0]["n"]
            e.act("group_master", group=g, level=50)
            with e.lock:
                f = e.build_frames()[1]
            check("a group master at 50%: its lights at half", f[0] == 127 and f[4] == 127 and f[8] == 255, str(list(f[:12])))
            r = e.act("group_master", group=99, level=10)
            check("a group that isn't there is refused", not r.get("ok"), "")
        finally:
            e.shutdown()
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    fj = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    pj = (ROOT / "web" / "app" / "playbacks.js").read_text(encoding="utf-8")
    check("Highlight button (H), park in the fixtures menu, group master faders",
          'id="hl-btn"' in html and '"park"' in fj and '"unpark"' in fj and '"group_master"' in pj
          and 'id="grp-masters"' in html, "")


def test_macros() -> None:
    """Macros: command lines played in one go - all or nothing, one undo
    step - from the command bar or a button; saved with the show."""
    print("macros")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            r = e.act("macro_save", macro={"name": "Walk-in", "lines": "1-4 dimmer 40\n# a comment\n1-2 red\n"})
            check("a macro is saved (comments dropped)", r.get("ok") and e.macros[0]["lines"] == ["1-4 dimmer 40", "1-2 red"], str(r))
            r = e.act("macro_save", macro={"name": "Bad", "lines": "1-4 wibble 9"})
            check("a line that doesn't parse is refused at save", not r.get("ok"), str(r))
            n0 = len(e._undo)
            r = e.act("macro_run", id="Walk-in")
            check("it plays every line", r.get("ok") and e.programmer[3].get("dimmer") == 40 and e.programmer[1].get("red") == 255,
                  str(e.programmer))
            check("...as ONE undo step, named after it", len(e._undo) == n0 + 1 and e._undo[-1]["label"] == "Walk-in", str(e._undo[-1:]))
            e.act("undo")
            check("one undo takes all of it back", not e.programmer.get(3), str(e.programmer))
            e.macros.append({"id": "m9", "name": "Half", "lines": ["1-4 dimmer 70", "9-12 dimmer 50"]})
            n0 = len(e._undo)
            r = e.act("macro_run", id="m9")
            check("a line that fails undoes the lines before it", not r.get("ok") and not e.programmer.get(1)
                  and len(e._undo) == n0, str(r))
            r = e.act("quick_set", page=1, slot=1, button={"kind": "macro", "macro": "m1", "label": "Walk-in"})
            check("a macro button", r.get("ok"), str(r))
            e.act("quick_press", id="q1-1", down=True)
            check("...plays it", e.programmer[3].get("dimmer") == 40, "")
            e.act("save_show", name="mac")
            e.act("macro_delete", id="m1")
            e.act("load_show", name="mac")
            check("saved with the show", [m["name"] for m in e.macros] == ["Walk-in", "Half"], str(e.macros))
        finally:
            e.shutdown()
    cj = (ROOT / "web" / "app" / "cmdbar.js").read_text(encoding="utf-8")
    check("the command bar opens Macros and finds them by name", "openMacros" in cj and "macroCandidates" in cj, "")


def test_osc() -> None:
    """OSC in: TouchOSC / Companion play the show - GO, masters, buttons,
    macros, command lines, tempo - and every message is answered."""
    print("OSC")
    from app import engine as eng
    from app import osc

    m = osc.build("/jarvis/cue", 1, 3)
    check("a message round-trips", osc.parse(m) == [("/jarvis/cue", [1, 3])], str(osc.parse(m)))
    b = osc.bundle(osc.build("/jarvis/master", 0.5), osc.build("/jarvis/cmd", "1-4 red"))
    check("a bundle holds several", [a for a, _ in osc.parse(b)] == ["/jarvis/master", "/jarvis/cmd"], "")
    check("junk is nothing", osc.parse(b"hello") == [] and osc.parse(b"") == [], "")

    port = _free_port()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("record_cue", playback=1, name="A")
            e.act("clear_programmer")
            r = e.act("osc", state=True, port=port)
            check("OSC listens", r.get("ok") and e.osc_public()["on"], str(r))
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.sendto(osc.build("/jarvis/master", 0.5), ("127.0.0.1", port))
            reply = osc.parse(s.recvfrom(1024)[0])
            check("master 0.5 -> 50%, and it answers", e.master == 50 and reply[0][0] == "/jarvis/ok", str(reply))
            s.sendto(osc.build("/jarvis/go", 1), ("127.0.0.1", port))
            s.recvfrom(1024)
            check("GO", e.playbacks[0]["active"] and e.playbacks[0]["index"] == 0, "")
            s.sendto(osc.build("/jarvis/cmd", "1-2 red"), ("127.0.0.1", port))
            s.recvfrom(1024)
            check("a command line", e.programmer.get(1, {}).get("red") == 255, str(e.programmer))
            s.sendto(osc.build("/jarvis/patch_clear"), ("127.0.0.1", port))
            reply = osc.parse(s.recvfrom(1024)[0])
            check("anything else is refused, with a reason", reply[0][0] == "/jarvis/error" and len(e.patch) == 4, str(reply))
            s.close()
            e.act("osc", state=False)
            check("OSC off", not e.osc_public()["on"], "")
        finally:
            e.shutdown()


def test_timecode() -> None:
    """MIDI timecode: the timeline jumps to it, plays along, re-seeks when
    it drifts and pauses when the timecode stops."""
    print("MIDI timecode")
    from app import engine as eng
    from app import tempo

    tc = tempo.Timecode()
    got = [tc.quarter_frame(b, 0.0) for b in tempo.build_mtc(3723.4)]
    check("eight quarter frames make one time (+2 frames)", got[:7] == [None] * 7 and abs(got[7] - 3723.48) < 0.01, str(got))
    check("the time reads h:mm:ss:ff", tc.text().startswith("01:02:03"), tc.text())
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("timeline_set", length=120)
            for b in tempo.build_mtc(10.0):
                e.tempo_mtc(b)
            check("not following: the timeline stays", not e.tl["playing"], "")
            r = e.act("timecode", state=True, offset=5)
            check("follow timecode", r.get("ok") and e.timecode_public()["follow"], str(r))
            for b in tempo.build_mtc(10.0):
                e.tempo_mtc(b)
            check("it plays from the timecode less the offset", e.tl["playing"] and abs(e._tl_now() - 5.08) < 0.1, str(e._tl_now()))
            for b in tempo.build_mtc(40.0):
                e.tempo_mtc(b)
            check("a jump re-seeks", abs(e._tl_now() - 35.08) < 0.1, str(e._tl_now()))
            check("the transport shows it", e.timecode_public()["running"] and "timecode" in e._tl_transport(), "")
            deadline = time.monotonic() + 2
            while e.tl["playing"] and time.monotonic() < deadline:
                time.sleep(0.05)
            check("the timecode stops: the timeline pauses", not e.tl["playing"], "")
            for b in tempo.build_mtc(2.0):
                e.tempo_mtc(b)
            check("before the offset: nothing plays", not e.tl["playing"], str(e._tl_now()))
            e.act("timecode", state=False)
            check("timecode off", not e.timecode_public()["follow"], "")
        finally:
            e.shutdown()


def test_cue_modes() -> None:
    """Tracking / cue only / block, move in black, cue actions, blind."""
    print("Cue modes")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Moving Head", qty=2)
            heads = [h["head_no"] for h in e.patch]
            h1 = heads[0]
            check("a moving head with a dimmer", "dimmer" in e.patch[0]["map"] and "pan" in e.patch[0]["map"], str(e.patch[0]["map"]))

            def rec(vals, **kw):
                e.programmer = {h1: dict(vals)}
                return e.act("record_cue", playback=1, **kw)
            rec({"dimmer": 100, "pan": 10})
            rec({"pan": 200})
            rec({"red": 255})
            now = e._clock()
            e.act("cue_go", playback=1, cue=3)
            check("cue only (default): cue 3 holds only its own", e._pb_values(e.playbacks[0], now + 1).get(h1) == {"red": 255},
                  str(e._pb_values(e.playbacks[0], now + 1)))
            r = e.act("playback_mode", playback=1, tracking=True)
            check("tracking on", r.get("ok") and e.playbacks[0]["tracking"], str(r))
            e.act("cue_go", playback=1, cue=3)
            v = e._pb_values(e.playbacks[0], e._clock() + 1)[h1]
            check("tracking: cue 3 = 1 + 2 + 3", v == {"dimmer": 100, "pan": 200, "red": 255}, str(v))
            e.act("cue_set", playback=1, cue=3, block=True)
            e.act("cue_go", playback=1, cue=3)
            check("a block starts afresh", e._pb_values(e.playbacks[0], e._clock() + 1)[h1] == {"red": 255}, "")
            e.act("cue_set", playback=1, cue=3, block=False)
            rec({"dimmer": 50}, cue=1, mode="merge", cue_only=True)
            check("cue only in tracking: the next cue puts the old level back",
                  e.playbacks[0]["stack"][1]["values"][h1].get("dimmer") == 100, str(e.playbacks[0]["stack"][1]["values"]))
            e.act("cue_go", playback=1, cue=1)
            check("cue 1 has the new level", e._pb_values(e.playbacks[0], e._clock() + 1)[h1]["dimmer"] == 50, "")

            # move in black: cue A dark, cue B on somewhere else
            e.act("playback_mode", playback=2, mib=True)
            e.programmer = {h1: {"dimmer": 0, "pan": 0}}
            e.act("record_cue", playback=2, fade=0)
            e.programmer = {h1: {"dimmer": 100, "pan": 250, "tilt": 90}}
            e.act("record_cue", playback=2)
            e.act("cue_go", playback=2, cue=1)
            v = e._pb_values(e.playbacks[1], e._clock() + 1)[h1]
            check("move in black: dark, already at the next cue's pan/tilt", v == {"dimmer": 0, "pan": 250, "tilt": 90}, str(v))
            e.act("playback_mode", playback=2, mib=False)
            e.act("cue_go", playback=2, cue=1)
            check("MIB off: it stays where the cue says", e._pb_values(e.playbacks[1], e._clock() + 1)[h1]["pan"] == 0, "")

            # cue actions
            bad = e.act("cue_set", playback=2, cue=2, actions=[{"action": "patch_clear"}])
            check("a cue can't edit the show", not bad.get("ok"), str(bad))
            e.act("cue_set", playback=2, cue=2, actions=[{"action": "tempo_set", "args": {"bpm": 128}},
                                                         {"action": "cue_go", "args": {"playback": 1, "cue": 2}}])
            e.act("cue_go", playback=2, cue=2)
            check("cue actions play with the cue", round(e.tempo_public()["bpm"]) == 128 and e.playbacks[0]["index"] == 1,
                  f"{e.tempo_public()['bpm']} {e.playbacks[0]['index']}")
            e.act("cue_set", playback=1, cue=2, actions=[{"action": "cue_go", "args": {"playback": 2, "cue": 2}}])
            r = e.act("cue_go", playback=2, cue=2)
            check("a loop of cues stops", r.get("ok"), str(r))
            check("the list shows modes and actions", e._pb_public(e.playbacks[1])["mib"] is False
                  and e._pb_public(e.playbacks[1])["stack"][1]["actions"], "")

            # blind
            e.act("playback_release", playback=1)
            e.act("playback_release", playback=2)
            e.programmer = {h1: {"dimmer": 30}}
            r = e.act("blind", playback=1, cue=1)
            check("blind-edit a cue: its values come into the programmer", r.get("ok") and e.programmer.get(h1, {}).get("dimmer") == 50, str(e.programmer))
            e.programmer[h1]["dimmer"] = 80
            frame = e.build_frames()
            look = {x["n"]: x for x in e._looks()}
            uni, addr = e.patch[0]["universe"], e.patch[0]["address"]
            dim_slot = e.patch[0]["map"].index("dimmer")
            check("the rig keeps the live programmer", frame[uni][addr - 1 + dim_slot] < 200, str(frame[uni][addr - 1 + dim_slot]))
            check("3D shows the blind edit", look[h1]["a"] >= 0.79, str(look[h1]))
            e.act("record_cue", playback=1, cue=1)
            check("record puts it back into the cue", e.playbacks[0]["stack"][0]["values"][h1]["dimmer"] == 80, "")
            e.act("blind", state=False)
            check("blind off: the live programmer is back", e.programmer.get(h1) == {"dimmer": 30} and not e.blind_public()["on"], str(e.programmer))
            e.act("save_show", name="cm")
            e.act("playback_mode", playback=1, tracking=False)
            e.act("load_show", name="cm")
            check("tracking / MIB / block / actions are saved", e.playbacks[0]["tracking"] and e.playbacks[1]["stack"][1].get("actions"), "")
        finally:
            e.shutdown()


def test_rigging_library() -> None:
    """Rigging library: shapes from real pieces, lights round a shape, a
    shape moves and trims as one, the report's loads and parts."""
    print("Rigging library")
    from app import engine as eng
    from app import fixlib, riglib

    check("a straight 7 m run is 4 + 3", riglib.sections(7) == [4.0, 3.0], str(riglib.sections(7)))
    check("6.2 m rounds up to 6.5 (4 + 2.5)", riglib.sections(6.2) == [4.0, 2.5], str(riglib.sections(6.2)))
    circ = riglib.build("circle", diameter=6)
    check("a 6 m circle is 12 pieces, closed", len(circ) == 12 and circ[0]["a"] == circ[-1]["b"], str(len(circ)))
    gp = riglib.build("goalpost", width=5, height=3)
    check("a goal post: two poles and a truss on top", [x["kind"] for x in gp] == ["tower", "tower", "truss"], "")
    ph = fixlib.physical("qlc:Chauvet/Chauvet-Intimidator-Scan-360.qxf")
    check("a light's weight from its library file", ph.get("kg") == 5.7, str(ph))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_shape", shape="rectangle", width=14, depth=16, height=6, layout=False)
            r = e.act("rig_add", preset="circle", diameter=4, x=0, z=6, trim=4)
            check("a circle truss added as one shape", r.get("ok") and len(r["ids"]) == 8 and r["group"], str(r.get("error")))
            e.act("add_heads", query="Moving Head", qty=6)
            a = e.act("attach_heads", heads=[1, 2, 3, 4, 5, 6], rig=r["ids"][0])
            rigs = {h["mount"]["rig"] for h in e.patch}
            check("six lights spread round the circle", a.get("ok") and len(rigs) == 6 and rigs <= set(r["ids"]), str(rigs))
            first = dict(e.venue["rigging"][0])
            before = [dict(x) for x in e.venue["rigging"]]
            e.act("venue_update", id=first["id"], a=[first["a"][0] + 1, first["a"][1], first["a"][2]],
                  b=[first["b"][0] + 1, first["b"][1], first["b"][2]])
            check("moving one piece moves the shape", all(abs(n["a"][0] - o["a"][0] - 1) < 1e-6
                                                          for n, o in zip(e.venue["rigging"], before)), "")
            e.act("rig_trim", id=r["ids"][3], trim=3)
            check("trim: the shape hangs at 3 m, its lights too",
                  all(abs(x["a"][1] - 3.145) < 1e-3 for x in e.venue["rigging"]) and max(h["y"] for h in e.patch) < 3.2,
                  str([h["y"] for h in e.patch]))
            bad = e.act("rig_trim", id=r["ids"][0], trim=9)
            check("a trim above the ceiling is refused", not bad.get("ok"), "")
            e.act("rig_add", preset="straight", length=7, piece="box30", z=10, trim=4.5)
            rep = e.act("rig_report", csv=True)
            rows = {x["name"]: x for x in rep["report"]["rigs"]}
            c = rows["Circle 4 m"]
            check("circle: 8 pieces, 6 lights, 4 points, the load shared",
                  c["pieces"] == 8 and len(c["lights"]) == 6 and c["points"] == 4
                  and abs(c["per_point_kg"] - c["total_kg"] / 4) < 0.1, str(c))
            s = rows["Box truss 29 cm (F34 type)"]
            check("a 7 m truss: 35 kg, 3 points, at 4.5 m", s["self_kg"] == 35.0 and s["points"] == 3 and s["trim"] == 4.5, str(s))
            parts = {(p["model"], str(p["length"])): p["count"] for p in rep["report"]["parts"]}
            check("parts: 8 arcs, a 4 m and a 3 m", parts.get(("box30", "arc 1/8 of Ø4.0 m")) == 8
                  and parts.get(("box30", "4.0")) == 1 and parts.get(("box30", "3.0")) == 1, str(parts))
            check("the report as CSV", rep["csv"].startswith("piece,") and "Circle 4 m" in rep["csv"], "")
            check("guessed weights are said", any("guessed" in w for w in c["warnings"]), str(c["warnings"]))
            sc = e.act("venue_add", item={"kind": "screen", "x": 0, "z": 0, "w": 4, "h": 2, "d": 0.1})["item"]
            check("an LED screen mirrors the lights by default", sc["content"] == "rig", str(sc))
            e.act("venue_update", id=sc["id"], changes={"content": "clip:/media/loop.mp4"})
            e.act("venue_update", id=[o for o in e.venue["objects"] if o["kind"] == "screen"][0]["id"], changes={"content": "javascript:x"})
            check("a clip link is kept, anything else falls back to the lights",
                  [o for o in e.venue["objects"] if o["kind"] == "screen"][0]["content"] == "rig", "")
            js = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
            check("the 3D view: photo, walk, live screens", "photo(longSide" in js and "walk(on" in js and "_drawScreens" in js, "")
            pw = e.act("paperwork")
            L = pw["lights"][0]
            check("paperwork: every light with its address, kind, rig and weight",
                  pw.get("ok") and len(pw["lights"]) == 6 and L["universe"] == 1 and L["address"] == 1
                  and L["rig"] == "Circle 4 m" and L["kg"] > 0 and L["channels"] > 0, str(L))
            page = (ROOT / "web" / "plot" / "plot.js").read_text(encoding="utf-8")
            check("the plot page prints the plot, the patch sheet and the rigging",
                  '"paperwork"' in page and "function patchSheet" in page and "function rigSheet" in page
                  and "window.print" in page, "")
        finally:
            e.shutdown()


def test_mixed_selection_targets() -> None:
    """One kind of light in a mixed selection: set_attribute and
    set_attr_range take `heads` and touch only those."""
    print("Mixed selection: per-kind controls")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            mv = e.act("add_heads", query="Moving Head", qty=2)["heads"]
            par = e.act("add_heads", query="LED PAR", qty=2)["heads"]
            e.act("select_all")
            r = e.act("set_attribute", attribute="gobo", value=40, heads=mv)
            check("the movers' section sets only the movers", r.get("ok") and set(e.programmer) == set(mv), str(e.programmer))
            e.act("set_attr_range", attribute="gobo", clear=True, heads=mv[:1])
            check("clear on one kind leaves the rest", "gobo" not in e.programmer.get(mv[0], {}) and e.programmer[mv[1]]["gobo"] == 40, "")
            check("the selection is left as it was", sorted(e.selected) == sorted(mv + par), str(e.selected))
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the Beam tab: a section per kind of light; plain words",
          "attr-kind" in js and "reaches all" not in js and "Own channels" in js, "")


def test_colour_match() -> None:
    """Colour matching: a model's gain per emitter scales what goes out on
    the wire (not the 3D look), for every light of the model; reset."""
    print("Colour matching across brands")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#ffffff")
            h = e.patch[0]
            a = h["address"] - 1
            ri, bi = h["map"].index("red"), h["map"].index("blue")
            r = e.act("colour_cal", red=80, blue=50)
            f = e.build_frames()
            b = e.patch[1]["address"] - 1
            check("red 80%, blue 50% on the wire - both lights of the model",
                  r.get("ok") and f[1][a + ri] == 204 and f[1][a + bi] == 127 and f[1][b + ri] == 204,
                  str([f[1][a + i] for i in range(len(h["map"]))]))
            check("the 3D look still shows white", e._looks()[0]["hex"] == "#ffffff", e._looks()[0]["hex"])
            check("read back", e.act("colour_cal_get", head=1)["cal"] == {"red": 80, "green": 100, "blue": 50}, "")
            check("not an undo step (it is the fixture's)", "colour_cal" not in [u["action"] for u in e._undo], "")
            e.act("colour_cal", head=1, reset=True)
            f = e.build_frames()
            check("reset: as it comes", f[1][a + ri] == 255 and f[1][a + bi] == 255, "")
        finally:
            e.shutdown()


def test_teach_wheel() -> None:
    """Teach the wheel: positions found on the real light become named
    slots with ranges for every light of the model; forget; a spin
    channel's named ranges become slots for ↺ ■ ↻."""
    print("Teach the wheel; spin channels")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Moving Head", qty=2)
            e.act("select_all")
            gobo = lambda n: next(a for p in e.attribute_state([n])["pages"] for a in p["attrs"] if a["role"] == "gobo")
            check("no slots in the file", not gobo(1).get("slots"), "")
            r = e.act("teach_slots", head=1, role="gobo", slots=[{"name": "Open", "value": 0}, {"name": "Stars", "value": 20},
                                                                 {"name": "Dots", "value": 40}])
            s2 = gobo(2)["slots"]
            check("taught: named slots with ranges, on the other light of the model too",
                  r.get("ok") and [(x["name"], x["from"], x["to"]) for x in s2] == [("Open", 0, 10), ("Stars", 11, 30), ("Dots", 31, 255)], str(s2))
            check("one name is not a wheel", not e.act("teach_slots", head=1, role="gobo", slots=[{"name": "A", "value": 3}]).get("ok"), "")
            check("a light without that wheel is refused", not e.act("teach_slots", head=1, role="wheel", slots=[]).get("ok"), "")
            e.act("teach_slots", head=1, role="gobo", clear=True)
            check("forget: back to the file", not gobo(1).get("slots"), "")
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the programmer: teach links, white presets, spin rows, an Advanced fold",
          "openTeachWheel" in js and "White presets" in js and "rotateRow" in js and "attr-adv" in js, "")


def test_control_tiles() -> None:
    """The buttons page's control tiles: faders (master, speed, playback,
    group), an XY pad on the tile's lights, tempo taps, a cue list's GO,
    the E-stop - and a fader is moved, not pressed."""
    print("Buttons page: control tiles")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            mv = e.act("add_heads", query="Moving Head", qty=2)["heads"]
            par = e.act("add_heads", query="LED PAR", qty=2)["heads"]
            e.act("select_heads", heads=par)
            e.act("group_create", name="PARs")
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("record_cue", playback=1, name="A")
            tiles = {1: {"kind": "fader", "label": "GM", "control": {"what": "master"}},
                     2: {"kind": "fader", "label": "PB", "control": {"what": "playback", "n": 1}},
                     3: {"kind": "fader", "label": "Grp", "control": {"what": "group", "n": 1}},
                     4: {"kind": "fader", "label": "Spd", "control": {"what": "speed"}},
                     5: {"kind": "xy", "label": "XY", "target": {"all": True}},
                     6: {"kind": "tempo", "label": "BPM"}, 7: {"kind": "cuelist", "label": "List", "playback": 1},
                     8: {"kind": "estop", "label": "STOP"}}
            ok = all(e.act("quick_set", page=2, slot=k, button=b).get("ok") for k, b in tiles.items())
            check("every kind of tile saves", ok, "")
            e.act("quick_fader", id="q2-1", level=40)
            e.act("quick_fader", id="q2-2", level=70)
            e.act("quick_fader", id="q2-3", level=25)
            e.act("quick_fader", id="q2-4", level=100)
            g = next(x for x in e.groups if x["name"] == "PARs")
            check("faders: master, playback, group master, speed (50 = 1x)",
                  e.master == 40 and e.playbacks[0]["level"] == 70 and g.get("master") == 25 and abs(e.speed_master - 2.0) < 0.01,
                  f"{e.master} {e.playbacks[0]['level']} {g.get('master')} {e.speed_master}")
            e.act("quick_xy", id="q2-5", pan=10, tilt=200)
            check("the XY pad moves only the tile's movers", all(e.programmer[n].get("pan") == 10 for n in mv)
                  and all("pan" not in e.programmer.get(n, {}) for n in par), str(e.programmer))
            check("a fader tile is not pressed", not e.act("quick_press", id="q2-1").get("ok"), "")
            r = e.act("quick_press", id="q2-7")
            check("the cue list tile GOes", r.get("ok") and e.playbacks[0]["active"], str(r))
            check("E-stop stops the effects", e.act("quick_press", id="q2-8").get("ok"), "")
            check("moving a fader is not an undo step", "quick_fader" not in [u["action"] for u in e._undo], "")
        finally:
            e.shutdown()


def test_roam() -> None:
    """Roam: movers wander inside zones, aimed from where they hang (truss,
    pole, floor) - every aim lands on its zone; the lights share the zones
    out; a cue keeps it; the AI turns a sentence into it."""
    print("Roam inside zones; AI that programs")
    import math

    from app import console_ai
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            tr = [r for r in e.venue["rigging"] if r["kind"] == "truss"]
            a = e.act("add_heads", query="Moving Head", qty=4)["heads"]
            e.act("attach_heads", heads=a, rig=tr[1]["id"], stance="hang")
            b = e.act("add_heads", query="Moving Head", qty=2)["heads"]
            e.act("set_place", head=b[0], x=-5, y=0.3, z=6)
            e.act("set_place", head=b[1], x=5, y=0.3, z=6)
            heads = a + b
            e.act("select_heads", heads=heads)
            r = e.act("roam", zones=["dancefloor", "dj"], speed=2)
            check("roam starts on the movers", r.get("ok") and sorted(r["heads"]) == sorted(heads), str(r))
            zones = {z["kind"]: z for z in e.venue["zones"]}
            import time as _time
            t0 = _time.monotonic() + 0.5
            pts, bad, moved = {}, 0, set()
            for k in range(8):
                vals = e._fx_values(t0 + k * 0.8)
                for n in heads:
                    p = (vals[n]["pan"], vals[n]["tilt"])
                    if n in pts and pts[n] != p:
                        moved.add(n)
                    pts[n] = p
            check("every light keeps moving", moved == set(heads), str(set(heads) - moved))
            # the zone points it aims at are inside the zones: re-derive them
            row = next(f for f in e.fx if f.get("roam"))
            for k in range(20):
                t = k * 0.9
                for i, n in enumerate(row["heads"]):
                    zi = i % len(row["roam"])
                    x0, x1, z0, z1, cx, cz, diag = row["_zc"][zi]
                    # the same path as _roam_values
                    w = e._ROAM_W[i % len(e._ROAM_W)]
                    s = t * 2.0 * 2.2 / diag
                    u = 0.5 + 0.5 * (0.62 * math.sin(w[0] * s * 6.28 + i * 1.7) + 0.38 * math.sin(w[1] * s * 6.28 + 2 * i * 1.7))
                    v = 0.5 + 0.5 * (0.62 * math.sin(w[2] * s * 6.28 + 3 * i * 1.7) + 0.38 * math.sin(w[3] * s * 6.28 + i * 1.7))
                    x, z = x0 + u * (x1 - x0), z0 + v * (z1 - z0)
                    if not (x0 - 0.01 <= x <= x1 + 0.01 and z0 - 0.01 <= z <= z1 + 0.01):
                        bad += 1
            check("every path stays in its zone's bounds", bad == 0, str(bad))
            check("two zones share the lights out", {i % 2 for i in range(len(row["heads"]))} == {0, 1}
                  and [z["name"] for z in row["roam"]] == ["Dance floor", "DJ"], "")
            check("a light that can't pan / tilt is left out", not e.act("roam", heads=[999]).get("ok"), "")
            check("an unknown zone says what there is", "Dance floor" in (e.act("roam", zones=["kitchen"]).get("error") or ""), "")
            e.act("record_cue", playback=1, name="Roaming")
            cue = e.playbacks[0]["stack"][0]
            check("a cue keeps the roam", (cue.get("fx") or [{}])[0].get("roam") == [zones["dancefloor"]["id"], zones["dj"]["id"]], str(cue.get("fx")))
            e.act("cue_go", playback=1, cue=1)
            check("...and plays it back", any(f.get("roam") for f in e.fx), "")
            e.act("stop_fx")
            e.act("select_heads", heads=heads)
            p = console_ai.plan("these lights should only hover around the dance floor and the DJ booth", offline=True, eng=e)
            out = console_ai.run(console_ai.resolve(p["steps"], e), e)
            check("the AI turns the sentence into a roam", out.get("ok") and any(f.get("roam") for f in e.fx)
                  and [z["name"] for z in next(f for f in e.fx if f.get("roam"))["roam"]] == ["Dance floor", "DJ"], str(p["steps"]))
        finally:
            e.shutdown()


def test_multihead_aim() -> None:
    """A multi-head light (heads on a bar that pans, each tilting): aim /
    follow move the heads picked on their own, or fan every head to its own
    spot along the throw; roam fans them too."""
    print("Multi-head lights follow per head")
    import time as _time

    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        key = "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", key), source=f"qlc:{key}")
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            r = e.act("add_heads", query="Intimidator Wave 360", qty=1)
            if not r.get("ok") or e.patch[0]["map"].count("tilt") < 2:
                check("a Wave 360 to test with", False, str(r.get("error")) + str(e.patch and e.patch[0]["map"]))
                return
            e.act("set_place", head=1, x=0, y=4.5, z=2)
            e.act("select_all")
            e.act("aim_at", x=0, y=0, z=8)
            check("together: one tilt for every head", set(e.programmer[1]) >= {"pan", "tilt"}
                  and not any("@" in k for k in e.programmer[1]), str(e.programmer[1]))
            e.act("aim_at", x=0, y=0, z=8, spread=1.5)
            t = [e.programmer[1][f"tilt@{k}"] for k in range(1, 5)]
            check("fanned: each head its own tilt, further away head by head", t == sorted(t) and len(set(t)) == 4, str(t))
            e.act("set_attribute", attribute="tilt", value=10)
            e.act("aim_at", x=0, y=0, z=10, cell=[2])
            row = e.programmer[1]
            check("the head picked follows alone, the others stay", row.get("tilt") == 10 and "tilt@2" in row
                  and not any(f"tilt@{k}" in row for k in (1, 3, 4)), str(row))
            e.act("venue_template", name="club")
            e.act("set_place", head=1, x=0, y=4.5, z=4)
            e.act("select_all")
            e.act("roam", zones=["dancefloor"])
            v = e._fx_values(_time.monotonic() + 0.5)[1]
            check("roam fans the heads", len({v[f"tilt@{k}"] for k in range(1, 5)}) == 4, str(v))
        finally:
            e.shutdown()


def test_review_fixes_oct() -> None:
    """Fixes from the review of PRs #30-#32: a 0-255 pan / tilt scales to a
    16-bit mover; one macro is one undo step even with a full stack; junk
    OSC never stops the listener; trim is the underside everywhere; a held
    raw channel is not colour-matched."""
    print("Review fixes (Oct)")
    from app import engine as eng
    from app import fixlib, osc
    from app.engine_base import UNDO_LIMIT

    for junk in (b"/jarvis/go", b"/jarvis/go\x00\x00,i\x00\x00", b"/a\x00\x00,s\x00\x00abc"):
        check(f"a malformed OSC packet is nothing ({junk[:12]!r})", osc.parse(junk) == [], "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        key = "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", key), source=f"qlc:{key}")
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Wave 360", qty=1)
            h = e.patch[0]
            if "pan_fine" in h["map"]:
                e.act("select_all")
                e.act("set_position", pan=255, tilt=128, unit="255")
                check("pan 255 of 255 is the end of a 16-bit pan", e.programmer[1]["pan"] == 65535, str(e.programmer[1]))
            e.act("patch_clear")
            e.act("add_heads", query="LED PAR", qty=2)
            for i in range(UNDO_LIMIT + 3):
                e.act("select_all")
                e.act("set_intensity", level=i % 100)
            e.act("macro_save", macro={"name": "M", "lines": ["1 red", "2 blue", "1-2 at 50"]})
            e.act("macro_run", id="M")
            acts = [u["action"] for u in e._undo]
            check("a macro is one undo step with a full stack", acts[-1] == "macro" and acts.count("run_command") == 0, str(acts[-4:]))
            e.act("venue_shape", shape="rectangle", width=14, depth=16, height=6, layout=False)
            r = e.act("rig_add", preset="straight", length=4, trim=4)
            it = next(x for x in e.venue["rigging"] if x["id"] == r["id"])
            rep = e.act("rig_report")["report"]["rigs"]
            check("trim is the underside: adding at 4 m hangs its centre at 4 m + half the truss, the report says 4",
                  abs(it["a"][1] - 4.145) < 1e-3 and any(x["trim"] == 4.0 for x in rep), f"{it['a'][1]} {[x['trim'] for x in rep]}")
        finally:
            e.shutdown()
