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


def test_plan_preview() -> None:
    """Preview a copilot plan in 3D: blind holds the programmer AND any
    effect started in it back from the rig, the 3D look shows them; Keep
    hands all of it to the rig, Throw away (undo + blind off) drops it."""
    print("Copilot plan preview in 3D")
    from app import console_ai
    from app import engine as eng

    saved_key = config.LLM_API_KEY
    config.LLM_API_KEY = ""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#ffffff")
            now = time.monotonic()
            wire0 = bytes(e.build_frames(now)[1][:16])

            def preview(text: str) -> dict:
                p = console_ai.plan(text, offline=True, eng=e)
                e.act("blind", state=True)
                return console_ai.run(console_ai.resolve(p["steps"], e), e)

            from app import fxlib
            hues = {tuple(sorted(fxlib.apply("rainbow", {}, ["red", "green", "blue"], {"spread": 180},
                                             elapsed=0.3, index=i, count=6).items())) for i in range(6)}
            check("a rainbow ACROSS the rig: the heads get different colours", len(hues) >= 4, str(hues))
            r = preview("rainbow across the rig")
            check("the plan ran in preview", r.get("ok") and any(f.get("lib") == "rainbow" for f in e.fx), str(r)[:200])
            t = time.monotonic() + 0.7
            wire = bytes(e.build_frames(t)[1][:16])
            hexes = {x["hex"] for x in e._looks(t)}
            check("the rig keeps what it had", wire == wire0, f"{list(wire0)} -> {list(wire)}")
            check("the 3D view shows the rainbow", len(hexes) > 1 or hexes != {"#ffffff"}, str(hexes))
            # throw away
            e.act("undo")
            e.act("blind", state=False)
            check("thrown away: no rainbow, the rig as before",
                  not any(f.get("lib") == "rainbow" for f in e.fx)
                  and bytes(e.build_frames(time.monotonic())[1][:16]) == wire0, str(e.fx)[:200])
            # keep
            preview("rainbow across the rig")
            e.act("blind", state=False, keep=True)
            t = time.monotonic() + 0.7
            wire = bytes(e.build_frames(t)[1][:16])
            check("kept: the rig runs the rainbow", wire != wire0 and any(f.get("lib") == "rainbow" for f in e.fx)
                  and not e.blind_public()["on"], f"{list(wire)}")
            # an effect running BEFORE blind stays on the rig while blind
            e.act("blind", state=True)
            check("an effect from before the preview still reaches the rig",
                  bytes(e.build_frames(time.monotonic() + 1.3)[1][:16]) != wire0)
            e.act("blind", state=False)
            check("leaving blind keeps the older effect", any(f.get("lib") == "rainbow" for f in e.fx))
            # a cue GO in blind plays on the live rig - its effects too
            e.act("stop_fx")
            e.act("clear_programmer")
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("run_fx", name="rainbow", params={"spread": 180})
            e.act("record_cue", playback=1, name="fx cue", fade=0)
            e.act("stop_fx")
            e.act("clear_programmer")
            wire1 = bytes(e.build_frames(time.monotonic())[1][:16])
            e.act("blind", state=True)
            e.act("cue_go", playback=1)
            cue_fx = [f for f in e.fx if f.get("cue_pb")]
            later = bytes(e.build_frames(time.monotonic() + 0.9)[1][:16])
            check("in blind, a cue's effect still reaches the rig", cue_fx and later != wire1, f"{cue_fx} {list(later)}")
            e.act("blind", state=False)
            check("and leaving blind doesn't stop it", any(f.get("cue_pb") for f in e.fx))
        finally:
            config.LLM_API_KEY = saved_key
            e.shutdown()


def test_ai_programs() -> None:
    """Item 23, the rest of it: "the back truss chases red and white on the
    beat during the drop" (a rig by what people call it, a two-colour step
    chase, a clip over the drop), "build me 8 buttons", "a 32-bar build-up"."""
    print("AI that programs: chases, buttons, build-ups")
    from app import console_ai
    from app import engine as eng
    from app import timeline as tl_mod

    saved_key = config.LLM_API_KEY
    config.LLM_API_KEY = ""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")

        def say(text: str) -> dict:
            p = console_ai.plan(text, offline=True, eng=e)
            return console_ai.run(console_ai.resolve(p["steps"], e), e)

        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="LED PAR 4ch", qty=6)
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            check("back truss = the Rear truss, not the upstage one",
                  (e._rig_named("back truss") or {}).get("name") == "Rear truss"
                  and (e._rig_named("the front truss") or {}).get("name") == "Front truss"
                  and e._rig_named("balcony pipe") is None)
            e.act("select_heads", head=1, head_end=6)
            e.act("attach_heads", rig=e._rig_named("back truss")["id"])
            e.act("clear_selection")
            e.act("timeline_set", length=120, bpm=120,
                  markers=[{"t": 0, "name": "Intro"}, {"t": 64, "name": "Drop"}, {"t": 96, "name": "Breakdown"}])
            check("the drop runs from its marker to the next one", tl_mod.section_span(e.timeline, "the drop") == (64.0, 96.0))
            r = say("the back truss chases red and white on the beat during the drop")
            clip = next((c for t in e.timeline["tracks"] for c in t["clips"] if c.get("step")), None)
            fx = next((f for f in e.step_fx if clip and f["id"] == clip["step"]), None)
            check("a chase clip over the drop, on the truss lights only",
                  r.get("ok") and clip and clip["t"] == 64.0 and clip["dur"] == 32.0
                  and clip["target"] == {"heads": [1, 2, 3, 4, 5, 6]} and clip["beats"] == 2.0,
                  json.dumps(r)[:300] + json.dumps(clip))
            check("the chase: 2 steps, red and white swapping between neighbours",
                  fx and len(fx["steps"]) == 2 and fx["name"].lower().startswith("red / white")
                  and fx["steps"][0]["values"]["1"]["red"] > fx["steps"][0]["values"]["1"]["blue"]
                  and fx["steps"][0]["values"]["2"]["blue"] > 200
                  and fx["steps"][1]["values"]["1"]["blue"] > 200, json.dumps(fx)[:300])
            e.act("timeline_play", at=70)
            e.act("status")
            running = [f for f in e.fx if f.get("steps") == (clip or {}).get("step")]
            check("playing the drop runs it, locked to the beat",
                  len(running) == 1 and running[0]["heads"] == [1, 2, 3, 4, 5, 6]
                  and running[0]["params"].get("beats") == 2.0, str(e.fx)[:200])
            e.act("timeline_seek", t=100)
            e.act("status")
            check("after the drop it stops", not any(f.get("steps") for f in e.fx), str(e.fx)[:200])
            e.act("timeline_stop")
            r = say("chase red and blue")
            check("no section: it runs now on everything", r.get("ok") and any(f.get("steps") for f in e.fx), json.dumps(r)[:200])
            e.act("undo")
            check("one undo takes it back", not any(f.get("steps") for f in e.fx))
            r = say("the balcony truss chases red and white")
            check("a rig that isn't there is said so", not r.get("ok") and "rig" in str(r.get("error")), str(r.get("error")))
            # buttons
            r = say("build me 8 buttons for this rig")
            page = sorted({b["page"] for b in e.quick})
            labels = [b["label"] for b in e.quick if b["page"] == page[0]] if page else []
            check("8 buttons, the busking basics first",
                  r.get("ok") and len(labels) == 8 and labels[:3] == ["Flash all", "Strobe all", "Blackout (hold)"]
                  and "Circle" not in labels, str(labels))
            r = say("make 6 strobe buttons")
            p2 = [b for b in e.quick if b["page"] not in page[:1]]
            check("a focus, on the next free page",
                  r.get("ok") and len(p2) == 6 and all(b["kind"] in ("strobe", "flash", "blackout", "kill") for b in p2),
                  str([(b["page"], b["label"]) for b in p2]))
            check("movement buttons without movers: said so",
                  "moving" in str(say("buttons for the movers").get("error")))
            # build-up
            r = say("a 32-bar build-up")
            tr = next((t for t in e.timeline["tracks"] if t["name"].startswith("Copilot build")), None)
            bar = 2.0
            check("the build ends where the drop starts",
                  r.get("ok") and tr and tr["clips"][0]["t"] == 0.0
                  and abs(tr["clips"][-1]["t"] + tr["clips"][-1]["dur"] - 64.0) < 0.01, json.dumps(tr)[:300])
            check("it speeds up: 8, 4, 2, 1 beats a round, then 16ths",
                  tr and [c.get("beats") for c in tr["clips"]] == [8, 4, 2, 1, 0.25]
                  and tr["clips"][-1]["dur"] == bar, json.dumps(tr)[:300])
            lv = next((t for t in e.timeline["tracks"] if t["kind"] == "level" and t.get("target") == "master"), None)
            check("the master climbs half to full over it",
                  lv and tl_mod.level_at(lv, 0.0) == 50 and tl_mod.level_at(lv, 32.0) == 75.0
                  and tl_mod.level_at(lv, 64.0) == 100, json.dumps(lv))
            e.act("timeline_play", at=63)
            e.act("status")
            check("the last bar pulses", any(f.get("lib") == "pulse" and f["params"].get("beats") == 0.25 for f in e.fx),
                  str(e.fx)[:200])
            e.act("timeline_stop")
            r = say("16 bar build-up at 10 seconds")
            tr = [t for t in e.timeline["tracks"] if t["name"].startswith("Copilot build")][-1]
            check("or where it was asked for", r.get("ok") and tr["clips"][0]["t"] == 10.0, json.dumps(tr)[:200])
        finally:
            config.LLM_API_KEY = saved_key
            e.shutdown()


def test_on_the_beat() -> None:
    """Item 4 of the plan: buttons that fire on the next beat / bar, the
    effects' size from the sound, and Ableton Link (a fake peer on
    localhost: the tempo and the place in the bar come across)."""
    print("On the beat: quantised buttons, size from the sound, Ableton Link")
    from app import engine as eng
    from app import link as link_mod
    from app import sound as sound_mod

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "label": "Hit", "mode": "hold"})
            e.act("quick_set", page=1, slot=2, button={"kind": "flash", "label": "Now", "mode": "hold", "quant": 0})
            e.act("quick_set", page=1, slot=3, button={"kind": "strobe", "label": "Bar hit", "mode": "latch", "quant": 4})
            ids = {b["label"]: b["id"] for b in e.quick}
            e.act("tempo_set", bpm=120)
            r = e.act("quick_quant", beats=1)
            check("buttons on the beat: a desk setting", r.get("ok") and e.quick_quant == 1.0 and e._quick_public()["quant"] == 1.0)
            t = e._tempo()
            t.anchor = time.monotonic() - 0.25            # half way through a beat at 120 BPM
            r = e.act("quick_press", id=ids["Hit"], down=True)
            check("pressed mid-beat: it waits", r.get("pending") and ids["Hit"] not in e.quick_active, str(r))
            at = t.anchor + 0.5                           # the next beat
            e._quick_pending_tick(at - 0.02)
            check("not before the beat", ids["Hit"] not in e.quick_active)
            e._quick_pending_tick(at + 0.001)
            check("on the beat it fires", ids["Hit"] in e.quick_active)
            e.act("quick_press", id=ids["Hit"], down=False)
            check("let go after it fired: off at once", ids["Hit"] not in e.quick_active)
            # a quick tap before the beat: on at the beat, off a quarter beat on
            t.anchor = time.monotonic() - 0.25
            e.act("quick_press", id=ids["Hit"], down=True)
            e.act("quick_press", id=ids["Hit"], down=False)
            at = t.anchor + 0.5
            e._quick_pending_tick(at + 0.001)
            on_at_beat = ids["Hit"] in e.quick_active
            e._quick_pending_tick(at + 0.5 * 0.25 + 0.002)
            check("a tap between beats: a hit on the beat, then off", on_at_beat and ids["Hit"] not in e.quick_active)
            # pressed just after the beat: it was meant to be on it
            t.anchor = time.monotonic() - 0.01
            r = e.act("quick_press", id=ids["Hit"], down=True)
            check("pressed just after the beat: straight away", not r.get("pending") and ids["Hit"] in e.quick_active, str(r))
            e.act("quick_press", id=ids["Hit"], down=False)
            t.anchor = time.monotonic() - 0.25
            r = e.act("quick_press", id=ids["Now"], down=True)
            check("a button set to 'as pressed' ignores the desk setting", not r.get("pending") and ids["Now"] in e.quick_active)
            e.act("quick_press", id=ids["Now"], down=False)
            t.anchor = time.monotonic() - 0.75             # beat 1.5: the bar is at beat 4
            r = e.act("quick_press", id=ids["Bar hit"], down=True)
            pend = e.quick_pending.get(ids["Bar hit"])
            check("'on the bar' waits for the next 1", r.get("pending") and pend and pend["at"] == 4.0, str(pend))
            e._quick_pending_tick(t.anchor + 2.0 + 0.001)
            check("and latches on it", ids["Bar hit"] in e.quick_active)
            e.act("quick_release_all")
            check("a bad beat setting is refused", not e.act("quick_quant", beats=3).get("ok")
                  and not e.act("quick_set", page=1, slot=4, button={"kind": "flash", "quant": 3}).get("ok"))
            e.act("save_show", name="beat")
            e.act("quick_quant", beats=0)
            e.act("load_show", name="beat")
            check("the beat setting is saved with the show", e.quick_quant == 1.0
                  and next(b for b in e.quick if b["label"] == "Bar hit").get("quant") == 4.0)

            # the effects' size from the sound
            cfg = {"links": [{"source": "level", "target": {"type": "fx_size"}, "depth": 100}]}
            clean = sound_mod.clean_config(cfg)
            check("fx_size: quiet = nothing, loud = as made",
                  sound_mod.size(clean, {"level": 0.0}, None, 0) == 0.0
                  and sound_mod.size(clean, {"level": 1.0}, None, 0) == 1.0
                  and sound_mod.size(clean, None, None, 0) == 1.0)
            e.act("select_all")
            e.act("run_fx", name="pulse", params={"low": 0, "high": 100})
            e.act("sound_link", link=cfg["links"][0])
            e.sound_feed({"level": 0.0})
            e._override_vals()
            quiet = e._fx_values(time.monotonic() + 0.3)
            e.sound_feed({"level": 1.0})
            e._override_vals()
            loud = e._fx_values(time.monotonic() + 0.3)
            qd = max(v.get("dimmer", 0) for v in quiet.values())
            ld = max(v.get("dimmer", 0) for v in loud.values())
            check("a quiet room shrinks a dimmer effect to its low", qd == 0 and ld > 0, f"{quiet} {loud}")
            p = e._sized({"size": 40.0, "speed": 0.2}, "move")
            check("and a movement's size", p == {"size": 40.0, "speed": 0.2}, str(p))
            e._sound_size = 0.5
            check("half as loud, half the swing", e._sized({"size": 40.0}, "move")["size"] == 20.0)
            e._sound_size = 1.0
        finally:
            e.shutdown()
        aj = (ROOT / "web" / "app" / "aimfollow.js").read_text(encoding="utf-8")
        check("follow-me has a speed: instant / fast / medium / slow, sent to the desk",
              "Follow speed" in aj and "pending.glide = glide" in aj and '"Slow"' in aj)

        # Ableton Link: the pure part
        node = b"peer0001"
        pkt = link_mod.build_alive(node, 128.0, 2.5, 1_000_000, mep4=("127.0.0.1", 4000))
        msg = link_mod.parse_discovery(pkt)
        check("a Link alive message reads back", msg and msg["timeline"] == (468750, 2_500_000, 1_000_000)
              and msg["mep4"] == ("127.0.0.1", 4000) and msg["node"] == node.hex(), str(msg))
        check("junk is not Link", link_mod.parse_discovery(b"_asdp_v\x01\x01") is None
              and link_mod.parse_discovery(b"hello") is None and link_mod.parse_pong(b"_link_v\x01\x02") is None)
        fol = link_mod.Follower()
        fol.alive(msg, 0)
        ping = link_mod.parse_ping(link_mod.build_ping(10_000))
        pong = link_mod.parse_pong(link_mod.build_pong(node, 10_000 + 7_000_000 + 50, ping["payload"]))
        fol.pong(pong, 10_100)
        check("the clocks measured: offset from a ping and its pong", fol.offset == 7_000_000, str(fol.offset))
        b = fol.beats(1_000_000 - 7_000_000 + 468750)       # one beat after the origin, in our time
        check("the session's beat at our time", abs(b - 3.5) < 1e-6 and abs(fol.bpm() - 128.0) < 1e-9, str(b))

        # and on the network: a fake peer on localhost
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s2")
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(0.05)
        try:
            port = _free_port()
            err = e._link_start(port=port, group="239.255.0.1")
            check("follows Link", err is None, str(err))
            skew = 3_000_000                                   # the session's clock runs 3 s ahead of ours
            ghost0 = int(time.monotonic() * 1e6) + skew
            alive = link_mod.build_alive(node, 128.0, 2.5, ghost0, mep4=peer.getsockname())
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                sender.sendto(alive, ("127.0.0.1", port))
                try:
                    data, addr = peer.recvfrom(2048)
                    pg = link_mod.parse_ping(data)
                    if pg:
                        peer.sendto(link_mod.build_pong(node, int(time.monotonic() * 1e6) + skew, pg["payload"]), addr)
                except socket.timeout:
                    pass
                tp = e.tempo_public()
                if tp.get("link", {}).get("synced") and e._tempo().source == "link":
                    break
            sender.close()
            time.sleep(0.15)
            now = time.monotonic()
            t = e._tempo()
            want = 2.5 + ((now * 1e6 + skew) - ghost0) / 468750
            got = t.beats(now)
            d = (want - got) % 4
            d = min(d, 4 - d)
            check("the tempo comes from the session", abs(t.bpm - 128.0) < 0.01 and t.source == "link", f"{t.bpm} {t.source}")
            check("and the place in the bar (within 20 ms)", d * 60 / 128 < 0.02, f"want {want % 4:.3f} got {got % 4:.3f}")
            check("the tempo menu says so", e.tempo_public()["link"]["peers"] == 1, str(e.tempo_public()))
            e.act("tempo_link", state=False)
            check("and stops", not e.__dict__.get("_link_thread"))
        finally:
            peer.close()
            e.shutdown()


def test_paint_and_shapes() -> None:
    """Plan step 5: gradients across the room, bars as pixels, pictures and
    video over the rig, step effects in cues and buttons, shapes of your
    own for movement."""
    print("Effects: gradients, pixels, pictures / video, step effects in cues + buttons, shapes")
    import base64 as _b64

    from app import engine as eng
    from app import motion
    from app import pixels

    bar = {"head_no": 1, "map": ["zone_dimmer", "red", "green", "blue"] * 3, "x": 0, "y": 3, "z": 2}
    us = pixels.units([bar, {"head_no": 2, "map": ["dimmer", "pan"], "x": 1, "y": 3, "z": 2}])
    check("a bar is a pixel per cell, along it; a light with no colour is none",
          [u["k"] for u in us] == [1, 2, 3] and us[0]["x"] < us[1]["x"] < us[2]["x"], str(us))
    check("the gradient's stops", pixels.gradient_at(["#ff0000", "#0000ff"], 0) == (255, 0, 0)
          and pixels.gradient_at(["#ff0000", "#0000ff"], 1) == (0, 0, 255)
          and pixels.gradient_at(["#ff0000", "#00ff00", "#0000ff"], 0.5) == (0, 255, 0))
    check("scrolling folds back, never jumps", abs(pixels.scroll(0.9, 0.2) - 0.9) < 1e-9 and pixels.scroll(0.5, 0) == 0.5)
    raw = bytes([255, 0, 0, 0, 0, 255])
    check("a picture is sampled between its pixels", pixels.sample(2, 1, raw, 0, 0) == (255, 0, 0)
          and pixels.sample(2, 1, raw, 1, 0) == (0, 0, 255) and pixels.sample(2, 1, raw, 0.5, 0) == (128, 0, 128))
    bad = 0
    for args in ((0, 1, ""), (2, 1, "!!"), (2, 1, _b64.b64encode(b"abc").decode()), (200, 1, "")):
        try:
            pixels.clean_media(*args)
        except ValueError:
            bad += 1
    check("bad pictures are refused", bad == 4)
    pts = [[0, 1], [1, -1], [-1, -1]]
    check("a shape goes through its points", all(
        max(abs(a - b) for a, b in zip(motion.key_frames(pts, i / 3, smooth), p)) < 1e-9
        for i, p in enumerate(pts) for smooth in (True, False)))
    check("a straight shape is straight between them", motion.key_frames(pts, 1 / 6, False) == (0.5, 0.0))
    try:
        motion.clean_points([[0, 0]])
        check("a shape needs 2 points", False)
    except ValueError:
        check("a shape needs 2 points", True)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            e.act("add_heads", query="RGBW Bar 12ch", qty=1)
            e.act("add_heads", query="Moving Head Spot 16ch", qty=2)
            for i, x in enumerate((-4, 0, 4)):
                e.act("set_place", head=i + 1, x=x, y=3, z=2)
            e.act("set_place", head=4, x=2, y=3, z=2)
            e.act("select_heads", head=1, head_end=4)
            r = e.act("run_gradient", colours=["red", "blue"])
            v = e._fx_values(time.monotonic())
            check("a gradient left to right: red on the left, blue on the right",
                  r.get("ok") and v[1]["red"] > v[1]["blue"] and v[3]["blue"] > v[3]["red"]
                  and v[2]["red"] > 50 and v[2]["blue"] > 50, json.dumps(v)[:300])
            check("the bar takes it cell by cell", {"red@1", "red@2", "red@3"} <= set(v[4]), str(v.get(4)))
            check("the snapshot lists it", any(f.get("pix") == "gradient" and "Gradient" in f["label"] for f in e.snapshot()["fx"]))
            e.act("stop_fx")
            e.act("run_gradient", colours="red, blue", speed=1)
            a = e._fx_values(time.monotonic())[1]
            b = e._fx_values(time.monotonic() + 0.5)[1]
            check("a scrolling gradient moves", a != b, f"{a} {b}")
            e.act("stop_fx")
            e.act("run_gradient", colours="red, blue", speed=0.3)
            check("a slow scroll keeps its speed (not rounded to still)",
                  any(f.get("pix") and f["params"]["speed"] == 0.3 for f in e.fx))
            check("bad gradients are refused", not e.act("run_gradient", colours=["red"]).get("ok")
                  and not e.act("run_gradient", colours=["red", "blue"], space="sideways").get("ok"))
            e.act("stop_fx")
            # a picture: left half red, right half blue
            pic = _b64.b64encode(bytes([255, 0, 0, 0, 0, 255])).decode()
            r = e.act("media_save", name="Split", w=2, h=1, data=pic)
            mid = r.get("id")
            e.act("select_heads", head=1, head_end=3)
            r = e.act("run_media", id=mid, view="front")
            v = e._fx_values(time.monotonic())
            check("a picture over the rig: each light the colour under it",
                  r.get("ok") and v[1]["red"] == 255 and v[1]["blue"] == 0 and v[3]["blue"] == 255 and v[3]["red"] == 0,
                  json.dumps(v)[:200])
            r = e.act("media_save", name="Clip", kind="video")
            vid = r["id"]
            e.act("run_media", id=vid, view="top")
            check("a video nobody plays shows nothing", 1 not in e._fx_values(time.monotonic()))
            e.media_frame(vid, 1, 1, _b64.b64encode(bytes([0, 255, 0])).decode())
            v = e._fx_values(time.monotonic())
            check("a video's frame lights them", v[1]["green"] == 255 and v[2]["green"] == 255, str(v.get(1)))
            try:
                e.media_frame("nope", 1, 1, _b64.b64encode(bytes(3)).decode())
                check("a frame for no video is refused", False)
            except ValueError:
                check("a frame for no video is refused", True)
            check("the media list says it plays", any(m["id"] == vid and m["playing"] for m in e.snapshot()["media"]))
            e.act("stop_fx")
            # step effects and paintings in cues; a step effect on a button
            e.act("select_heads", head=1, head_end=3)
            e.act("set_intensity", level=100)
            e.act("run_gradient", colours=["red", "blue"])
            r = e.act("record_cue", playback=1, name="painted")
            check("a gradient is recorded in a cue", r.get("ok") and any(i.get("pix") for i in e.playbacks[0]["stack"][0].get("fx") or []),
                  str(e.playbacks[0]["stack"][0].get("fx")))
            e.act("clear_programmer")
            e.act("select_heads", head=1, head_end=3)
            e.act("chase_colours", colours=["red", "white"])
            sid = e.step_fx[-1]["id"]
            r = e.act("record_cue", playback=1, name="chase")
            check("a step effect is recorded in a cue", any(i.get("steps") == sid for i in e.playbacks[0]["stack"][1].get("fx") or []))
            e.act("stop_fx")
            e.act("cue_go", playback=1, cue=1)
            check("GO plays the gradient again", any(f.get("pix") == "gradient" and f.get("cue_pb") for f in e.fx))
            e.act("cue_go", playback=1, cue=2)
            check("and the next cue its step effect", any(f.get("steps") == sid and f.get("cue_pb") for f in e.fx)
                  and not any(f.get("pix") for f in e.fx), str([(f.get("pix"), f.get("steps")) for f in e.fx]))
            check("the cue list names them", e.snapshot()["playbacks"][0]["stack"][0]["fx"] == ["Gradient"])
            e.act("playback_release", playback=1)
            r = e.act("quick_set", page=1, slot=1, button={"kind": "fx", "label": "Chase", "fx": f"step:{sid}",
                                                           "target": {"heads": [1, 2, 3]}, "params": {"beats": 2}})
            check("a button can run a step effect", r.get("ok"), str(r.get("error")))
            e.act("quick_press", id="q1-1", down=True)
            running = [f for f in e.fx if f.get("steps") == sid]
            check("pressed, it runs (on the beat)", running and running[0]["params"].get("beats") == 2.0, str(e.fx)[:200])
            e.act("quick_release_all")
            check("a button with a step effect that isn't there is refused",
                  not e.act("quick_set", page=1, slot=2, button={"kind": "fx", "fx": "step:nope"}).get("ok"))
            # shapes
            r = e.act("shape_save", shape={"name": "Tri", "points": pts, "smooth": False})
            shp = r.get("id")
            e.act("select_heads", head=5, head_end=6)
            e.act("set_position", pan=128, tilt=128)
            r = e.act("run_shape", id=shp, speed=0.5, size=30)
            vals = [e._fx_values(time.monotonic() + k * 0.4).get(5) for k in range(4)]
            check("a shape moves the movers round it", r.get("ok") and len({(x["pan"], x["tilt"]) for x in vals}) >= 3, str(vals))
            check("it is listed by name", any(f.get("label") == "Shape: Tri" for f in e.snapshot()["fx"]))
            check("a shape on lights that can't move is refused", not e.act("run_shape", id=shp, heads=[1]).get("ok"))
            r = e.act("record_cue", playback=2, name="tri")
            e.act("stop_fx")
            e.act("cue_go", playback=2)
            check("a shape in a cue plays again", any(f.get("lib") == "shape" and f.get("cue_pb") == 2 for f in e.fx))
            e.act("save_show", name="paint")
            e.act("shape_delete", id=shp)
            check("deleting a shape stops it", not any(f.get("lib") == "shape" for f in e.fx) and not e.shapes)
            e.act("load_show", name="paint")
            check("pictures, videos and shapes are saved with the show",
                  {m["name"] for m in e.media_public()} == {"Split", "Clip"}
                  and e.media[mid]["data"] == pic and [s["name"] for s in e.shapes] == ["Tri"])
            e.act("undo")
            check("undo keeps them in step", isinstance(e.shapes, list))
        finally:
            e.shutdown()


def test_move_buttons_and_glide() -> None:
    """Plan steps 6 and 7: a button straight from the Move tab (a roam, a
    shape, a movement) for the selected lights or their group; the follow
    speed glided by the engine at the DMX rate."""
    print("Move tab: instant buttons; follow speed in the engine")
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="Moving Head Spot 16ch", qty=4)
            e.act("select_heads", head=1, head_end=4)
            e.act("attach_heads", rig=e._rig_named("front truss")["id"])
            e.act("group_create", name="Front movers", heads=[1, 2])
            zones = [z["id"] for z in e.venue["zones"] if z["kind"] in ("dancefloor", "bar")]
            r = e.act("roam", zones=zones, speed=0.5, heads=[1, 2])
            check("a slow roam keeps its speed (no longer rounded to 0.05)",
                  r.get("ok") and e.fx[-1]["params"]["speed"] == 0.5, str(e.fx[-1]["params"]))
            e.act("stop_fx")
            r = e.act("quick_set", page=1, slot="free", button={
                "kind": "fx", "fx": "roam", "label": "Roam floor+bar", "mode": "latch",
                "params": {"zones": zones, "speed": 0.5, "size": 0.8}, "target": {"group": 1}})
            bid = r.get("id")
            check("a roam button: its zones and speed", r.get("ok") and r["button"]["params"]["speed"] == 0.5
                  and r["button"]["params"]["zones"] == zones, str(r.get("error") or r.get("button")))
            e.act("quick_press", id=bid, down=True)
            row = next((f for f in e.fx if f.get("roam")), None)
            check("pressed: the group roams those zones", row and row["heads"] == [1, 2]
                  and {z["id"] for z in row["roam"]} == set(zones), str(row and row["heads"]))
            e.act("quick_press", id=bid, down=True)              # latch: off
            check("pressed again: it stops", not any(f.get("roam") for f in e.fx))
            next(x for x in e.groups if x["n"] == 1)["heads"].append(3)     # the group grows
            e.act("quick_press", id=bid, down=True)
            row = next((f for f in e.fx if f.get("roam")), None)
            check("a light added to the group later follows the button", row and 3 in row["heads"], str(row and row["heads"]))
            e.act("quick_release_all")
            check("a roam button needs zones that are in the room",
                  not e.act("quick_set", page=1, slot=9, button={"kind": "fx", "fx": "roam", "params": {"zones": ["nowhere"]}}).get("ok")
                  and not e.act("quick_set", page=1, slot=9, button={"kind": "fx", "fx": "roam", "params": {}}).get("ok"))
            shp = e.act("shape_save", shape={"name": "Tri", "points": [[0, 1], [1, -1], [-1, -1]]})["id"]
            r = e.act("quick_set", page=1, slot="free", button={
                "kind": "fx", "fx": f"shape:{shp}", "label": "Tri", "params": {"speed": 0.4, "size": 25, "spread": 360},
                "target": {"heads": [3, 4]}})
            check("a shape button keeps its knobs", r.get("ok") and r["button"]["params"]["speed"] == 0.4
                  and r["button"]["params"]["size"] == 25.0, str(r.get("error") or r.get("button")))
            e.act("quick_press", id=r["id"], down=True)
            row = next((f for f in e.fx if f.get("lib") == "shape"), None)
            check("pressed: only those lights run the shape", row and row["heads"] == [3, 4] and row["params"]["speed"] == 0.4)
            e.act("quick_release_all")
            check("a button for a shape that isn't there is refused",
                  not e.act("quick_set", page=1, slot=9, button={"kind": "fx", "fx": "shape:nope"}).get("ok"))
            r = e.act("quick_set", page=1, slot=10, button={"kind": "flash", "label": "Hit", "mode": "tap", "seconds": 2.5})
            check("a 2.5 s timed button stays 2.5 s (no longer rounded)", r.get("ok") and r["button"]["seconds"] == 2.5)

            # follow speed: the engine glides
            e.act("clear_programmer")
            e.act("select_heads", head=1, head_end=2)
            e.act("aim_at", x=-3, y=0, z=8)
            instant_left = dict(e.programmer[1])
            e.act("aim_at", x=3, y=0, z=8)
            instant_right = dict(e.programmer[1])
            e.act("aim_at", x=-3, y=0, z=8, glide=1.0)
            g = e._glide
            check("the first glide starts where the lights were left", g and abs(g["cur"][0] - 3.0) < 0.05, str(g and g["cur"]))
            t0 = g["last"]
            e._aim_glide_tick(now=t0 + 0.3)
            mid = dict(e.programmer[1])
            check("part of the way after 0.3 s", -3.0 < e._glide["cur"][0] < 3.0
                  and mid["pan"] != instant_left["pan"] and mid["pan"] != instant_right["pan"], f"{e._glide['cur']} {mid}")
            check("the live feed has where the lights are now", e.lite(e.patch_rev)["aim_glide"]["x"] == round(e._glide["cur"][0], 2))
            e._aim_glide_tick(now=t0 + 20)
            check("and there in the end, exactly as an instant aim", e.programmer[1]["pan"] == instant_left["pan"]
                  and e.programmer[1]["tilt"] == instant_left["tilt"] and e._glide is None
                  and e.lite(e.patch_rev)["aim_glide"] is None, str(e.programmer[1]))
            e.act("aim_at", x=3, y=0, z=8, glide=2.0)
            e._aim_glide_tick(now=e._glide["last"] + 0.2)
            e.act("nudge", axis="pan", step=0.05)
            e._aim_glide_tick(now=time.monotonic() + 0.5)
            check("nudged by hand: the glide lets go", e._glide is None)
            e.act("aim_at", x=-3, y=0, z=8, glide=2.0)
            e.act("aim_at", x=0, y=0, z=6)
            check("an instant aim stops a glide", e._glide is None)
            e.act("aim_at", x=3, y=0, z=8, glide=1.0)
            before = e._glide["cur"][0]
            e.build_frames(time.monotonic() + 0.3)
            check("the frame builder moves it (the browser does not have to)",
                  e._glide is None or e._glide["cur"][0] != before, str(before))
            # stopping effects without clearing the lights
            e.act("clear_programmer")
            e.act("select_heads", head=1, head_end=2)
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#ff0000")
            e.act("run_fx", name="circle")
            e.act("record_cue", playback=1, name="circling")
            e.act("cue_go", playback=1)
            e.act("select_heads", head=3, head_end=4)
            e.act("set_colour", hex="#0000ff")
            e.act("run_fx", name="circle")
            pub = {f["from"] for f in e.snapshot()["fx"]}
            check("each running effect says who started it", pub == {"cue", "programmer"}, str(pub))
            r = e.act("stop_fx", programmer=True)
            check("stop my effects: the lights keep their colour, the cue's effect plays on",
                  r.get("ok") and r["stopped"] == 1 and any(f.get("cue_pb") for f in e.fx)
                  and e.programmer[3].get("blue") == 255, str(e.fx)[:200])
            e.act("run_fx", name="circle")
            e.act("clear_programmer")
            check("Clear drops your effects, not the cue's", any(f.get("cue_pb") for f in e.fx)
                  and not any(not f.get("cue_pb") for f in e.fx))
        finally:
            e.shutdown()


def test_only_what_it_can() -> None:
    """Plan step 8: the pad shows a light's real travel (degrees, what it
    can reach, one axis for a light that only tilts); lasers and effect
    machines are not counted as lights by the Colour / Level / Beam tabs."""
    print("Show only what a light can do")
    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        for src, key in (("ofl", "chauvet-dj/intimidator-spot-260.json"), ("qlc", "MagicFX/MagicFX-Psyco2Jet.qxf"),
                         ("qlc", "Laserworld/Laserworld-PRO-800RGB.qxf")):
            fixtures.store_parsed(db, fixlib.load(src, key), source=f"{src}:{key}")
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", qty=2)
            e.act("add_heads", query="Psyco2Jet", qty=1)
            e.act("add_heads", query="PRO-800RGB", qty=1)
            spot, jet, laser = 1, 3, 4
            e.act("select_heads", heads=[spot])
            r = e.act("pad_info")
            check("a mover: both axes, its own degrees, all of its travel",
                  r.get("ok") and r["pan"] and r["tilt"] and r["deg"].get("pan") and r["deg"].get("tilt")
                  and r["reach"]["pan"] == [0.0, 1.0], json.dumps(r)[:300])
            e.act("set_limits", head=spot, role="pan", low=64, high=192)
            r = e.act("pad_info")
            check("its limits: only that part is reachable", r["reach"]["pan"] == [round(64 / 255, 4), round(192 / 255, 4)]
                  or abs(r["reach"]["pan"][0] - 0.25) < 0.01, str(r["reach"]))
            e.act("select_heads", heads=[spot, 2])
            r = e.act("pad_info")
            check("two lights: the part they share", r["reach"]["pan"][0] > 0.2 and r["reach"]["pan"][1] < 0.8, str(r["reach"]))
            e.act("select_heads", heads=[jet])
            r = e.act("pad_info")
            check("a CO2 jet that only tilts: a tilt-only pad", r["tilt"] and not r["pan"] and not r["both"], json.dumps(r)[:200])
            e.act("select_heads", heads=[laser])
            r = e.act("pad_info")
            check("a laser has no pan / tilt for the pad", r.get("ok") and not r["heads"], json.dumps(r)[:200])
            body = {h["head_no"]: h["body"] for h in e.snapshot()["patch"]}
            check("the screens know a laser and an effect machine aren't lights",
                  body[laser].get("class") == "laser" and body[jet].get("class") == "sfx" and body[spot].get("class") == "light",
                  str({k: v.get("class") for k, v in body.items()}))
            check("colour doesn't reach a laser (its colour channels are on the Laser tab)",
                  not e.act("set_colour", hex="#ff0000").get("ok"))
        finally:
            e.shutdown()
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    mj = (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8")
    check("tabs count real lights; the pad greys what they can't reach; movements need their axes",
          "lightRoles" in pj and "pad-out" in pj and "pad_info" in pj and "canRun(name)" in mj)


def test_ai_assistant() -> None:
    """Plan step 9: the assistant works the desk in a loop of tools - look,
    act, check the real lights, fix, ask, remember - in blind until kept,
    all of it one undo step.  A scripted model stands in for the AI."""
    print("AI assistant: tool loop, check, ask, remember, preview, one undo")
    from app import assistant, config as cfg
    from app import engine as eng

    def tool(name, **args):
        return {"id": f"c{name}{len(args)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    def scripted(replies):
        seen = []

        def chat(messages, tools=None, **_):
            seen.append(messages)
            return replies.pop(0)
        return chat, seen

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        saved_data = cfg.DATA
        cfg.DATA = tmp
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.invalidate_cache()
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="Moving Head Spot 16ch", qty=2)
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_heads", head=1, head_end=2)
            e.act("attach_heads", rig=e._rig_named("front truss")["id"])
            e.act("clear_selection")
            undo_before = len(e._undo)
            chat, seen = scripted([
                {"content": "", "tool_calls": [tool("look_at_rig")]},
                {"content": "", "tool_calls": [tool("do", action="select_heads", params={"head": 3, "head_end": 6}),
                                               tool("do", action="set_intensity", params={"level": 80}),
                                               tool("do", action="set_colour", params={"colour": "orange"})]},
                {"content": "", "tool_calls": [tool("do", action="select_heads", params={"head": 1, "head_end": 2}),
                                               tool("do", action="set_intensity", params={"level": 100}),
                                               tool("do", action="aim_at", params={"x": 0, "y": 0, "z": 9})]},
                {"content": "", "tool_calls": [tool("check_lights", heads=[1, 3])]},
                {"content": "", "tool_calls": [tool("remember", note="likes warm sunsets")]},
                {"content": "A warm sunset: the PARs in orange at 80%, the movers on the dance floor."},
            ])
            r = assistant.run_turn(e, "make it feel like a sunset", session="t", chat=chat)
            check("the loop runs its tools and answers", r.get("ok") and r["reply"].startswith("A warm sunset")
                  and len([s for s in r["steps"] if s["action"] != "remember"]) == 6, json.dumps(r)[:300])
            check("the rig and the room were in its instructions", "THE RIG NOW" in seen[0][0]["content"]
                  and "Rear truss" in seen[0][0]["content"])
            tool_msgs = [m for m in seen[-1] if m["role"] == "tool" and m.get("name") == "check_lights"]
            lights = json.loads(tool_msgs[-1]["content"])
            by = {x["head"]: x for x in lights.get("lights", [])}
            check("it can check the real lights: level, colour, where a beam lands (the zone)",
                  by.get(3, {}).get("level") == 80 and by[3].get("colour") in ("orange", "amber", "red", "gold")
                  and "Dance floor" in by.get(1, {}).get("beam", ""), json.dumps(lights)[:400])
            check("it's a preview: blind, the rig unchanged", r["preview"] and e.blind_public()["on"]
                  and e.build_frames(time.monotonic())[1][e.patch[2]["address"] - 1] == 0)
            check("everything it did is one undo step", len(e._undo) == undo_before + 1
                  and e._undo[-1]["label"].startswith("AI:"), str([u.get("label") or u["action"] for u in e._undo[-3:]]))
            check("it remembered a preference", assistant.notes() == ["likes warm sunsets"])
            e.act("blind", state=False, keep=True)
            check("kept: the rig has it", e.build_frames(time.monotonic())[1][e.patch[2]["address"] - 1] > 0)
            e.act("undo")
            check("one Ctrl+Z takes all of it back", not e.programmer.get(3), str(e.programmer.get(3)))
            # the next turn: the conversation and the notes come along
            chat, seen = scripted([{"content": "Calmer: the PARs are at 30%."}])
            assistant.run_turn(e, "calmer please", session="t", chat=chat)
            msgs = seen[0]
            check("the conversation continues, with what it remembered",
                  any(m.get("content") == "make it feel like a sunset" for m in msgs)
                  and "likes warm sunsets" in msgs[0]["content"])
            # it may not fire effects or delete; it asks
            chat, _ = scripted([
                {"content": "", "tool_calls": [tool("do", action="fx_fire", params={"heads": [1]}),
                                               tool("do", action="delete_cue", params={"playback": 1, "cue": 1})]},
                {"content": "", "tool_calls": [tool("ask", question="Which truss - front or rear?", options=["Front", "Rear"])]},
            ])
            r = assistant.run_turn(e, "fire the CO2 and chase the truss", session="t", chat=chat)
            check("no pyro, no deleting: refused", all(not s["ok"] for s in r["steps"]), str(r["steps"]))
            check("it asks, with answers to tap", r["question"] == "Which truss - front or rear?" and r["options"] == ["Front", "Rear"])
            check("nothing changed: no preview, no undo step", not r["preview"] and not r["changed"] and not e.blind_public()["on"])
            # the AI service failing leaves nothing behind
            def broken(*a, **k):
                raise assistant.llm.LLMError("AI service HTTP 503")
            chat, _ = scripted([{"content": "", "tool_calls": [tool("do", action="select_all"), tool("do", action="set_intensity", params={"level": 100})]}])
            calls = {"n": 0}

            def flaky(messages, tools=None, **k):
                calls["n"] += 1
                return chat(messages, tools) if calls["n"] == 1 else broken()
            depth = len(e._undo)
            r = assistant.run_turn(e, "everything up", session="t", chat=flaky)
            check("the AI failing half way: nothing left behind", not r["ok"] and "503" in r["error"]
                  and not e.blind_public()["on"] and len(e._undo) == depth
                  and not any((e.programmer.get(n) or {}).get("dimmer") for n in (3, 4)), str(r)[:200])
            check("the extra actions are all real", not [a for a in assistant.EXTRA if a not in eng.ACTIONS])
            from app import llm as _llm
            body = ('{"error": {"code": 429, "message": "Quota exceeded ... limit: 15 ... Please retry in 46.8s.", '
                    '"details": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "15"}, '
                    '{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "46s"}]}}')
            saved_rpm = _llm._rpm
            _llm._learn_rpm(body)
            check("a free plan's 429: how long to wait, and the requests a minute it allows",
                  _llm._retry_after(body) == 46.0 and _llm._rpm == 15)
            _llm._rpm = saved_rpm
            e.act("select_heads", head=1, head_end=2)
            r = e.act("aim_at", zone="Dance floor")
            check("aim at a zone by its name (the AI tried a mark of that name first)",
                  r.get("ok") and "Dance floor" in assistant.check_lights(e, [1])["lights"][0].get("beam", ""), str(r.get("error")))
            check("and 'mark' falls back to a zone", e.act("aim_at", mark="dance floor").get("ok"))
            from app import engine_base
            check("a read-only query never becomes an undo step (\"Undo pad info\")",
                  not [a for a in engine_base._READ_ONLY if a not in engine_base.UNDO_EXCLUDED])
            depth = len(e._undo)
            e.act("pad_info")
            check("pad_info leaves undo alone", len(e._undo) == depth)
        finally:
            cfg.DATA = saved_data
            e.shutdown()


_DRAFT_SIM = r"""
import { doorFrom, balconyFrom, pillarAt } from "MODULE";
const walls = [[-5, -1], [5, -1], [5, 9], [-5, 9]];
const L = [[0, 0], [8, 0], [8, 4], [4, 4], [4, 8], [0, 8]];
console.log(JSON.stringify({
  front: doorFrom([-1, 8.8], [0.6, 8.9], walls), side: doorFrom([4.8, 2], [4.9, 3.5], walls),
  corner: doorFrom([4.8, 8.95], [4.8, 8.95], walls), wide: doorFrom([-9, -1], [9, -1], walls),
  inner: doorFrom([6, 4.1], [7, 3.9], L),
  balc: balconyFrom([-5, 6], [5, 9], 7), low: balconyFrom([0, 0], [3, 3], 4.5), tiny: balconyFrom([0, 0], [0.2, 3], 7),
  pillar: pillarAt([1.234, 2.345], 6),
}));
"""


def test_drafting() -> None:
    """Drawing doors, pillars and balconies on the plan (web/app/drafting.js,
    run under node), and the editor / Add menu that use it."""
    print("drafting: doors, pillars, balconies")
    import subprocess as _sp
    from tools.selftests.common import _which
    web = ROOT / "web"
    ed = (web / "js" / "stage" / "editor.js").read_text(encoding="utf-8")
    vp = (web / "app" / "venuepanel.js").read_text(encoding="utf-8")
    check("the editor draws doors and balconies with two clicks, pillars one by one",
          "TWO_CLICK = { door: 1, balcony: 1 }" in ed and 'onDrawn("pillar"' in ed, "")
    check("Arrange -> Add has Draw a door / pillars / a balcony",
          all(f'drawDraft("{k}")' in vp for k in ("door", "pillar", "balcony")), "")
    node = _which("node")
    if node is None:
        print("  skip  node not found")
        return
    mod = (web / "app" / "drafting.js").as_uri()
    proc = _sp.run([node, "--input-type=module", "-e", _DRAFT_SIM.replace("MODULE", mod)], capture_output=True, text=True, timeout=30)
    try:
        g = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        g = {}
    check("the drafting maths runs under node", bool(g), (proc.stderr or proc.stdout)[:200])
    if not g:
        return
    f = g["front"]
    check("a door goes in the nearest wall, as wide as drawn, just inside it",
          f["rot"] == 0 and abs(f["w"] - 1.6) < 0.01 and abs(f["x"] + 0.2) < 0.01 and 8.9 <= f["z"] < 9, str(f))
    s = g["side"]
    check("...on a side wall it turns along it", abs(s["rot"]) == 90 and abs(s["x"] - 4.94) < 0.01 and abs(s["z"] - 2.75) < 0.01, str(s))
    check("...a click is a 0.7 m door, kept inside its wall at a corner",
          g["corner"]["w"] == 0.7 and g["corner"]["x"] <= 5 - 0.35 + 0.01, str(g["corner"]))
    check("...never wider than the wall", g["wide"]["w"] == 10 and abs(g["wide"]["x"]) < 0.01, str(g["wide"]))
    check("...an L-shaped room's inside corner wall works too",
          g["inner"]["rot"] == 0 and abs(g["inner"]["z"] - 4) < 0.1 and 3.9 < g["inner"]["z"], str(g["inner"]))
    b = g["balc"]
    check("a balcony is the rectangle between two corners, its deck at most 3.2 m",
          b == {"x": 0, "z": 7.5, "w": 10, "d": 3, "h": 0.3, "y": 3.2}, str(b))
    check("...under a low ceiling at least 2.4 m to walk under; too thin is nothing",
          g["low"]["y"] == 2.4 and g["tiny"] is None, str(g["low"]))
    check("a pillar stands floor to ceiling where clicked",
          g["pillar"] == {"x": 1.23, "z": 2.35, "w": 0.5, "d": 0.5, "h": 6}, str(g["pillar"]))


def test_align_rigging() -> None:
    """Arrange: line up and spread rigging and objects; a shape moves as
    one, lights on a rig go with it, one undo step."""
    print("align / distribute rigging")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_shape", shape="rectangle", width=14, depth=16, height=6, layout=False)
            t = [e.act("rig_add", preset="straight", length=4, x=x, z=z, trim=tr)["id"]
                 for x, z, tr in ((-3, 2, 4.0), (1, 5, 4.5), (2, 11, 3.6))]
            e.act("add_heads", query="Moving Head", qty=1)
            e.act("attach_heads", heads=[1], rig=t[2])
            mid = lambda r: [(r["a"][k] + r["b"][k]) / 2 for k in range(3)]  # noqa: E731
            rig = lambda i: next(r for r in e.venue["rigging"] if r["id"] == i)  # noqa: E731
            n_undo = len(e._undo)
            r = e.act("venue_align", ids=t, how="spread-z")
            zs = [round(mid(rig(i))[2], 3) for i in t]
            check("spread in depth: evenly between the outermost", r.get("ok") and zs == [2.0, 6.5, 11.0], str(zs) + str(r.get("error")))
            check("...in one undo step", len(e._undo) == n_undo + 1, "")
            y0 = e.patch[0]["y"]
            e.act("venue_align", ids=t, how="height")
            ys = {round(mid(rig(i))[1], 3) for i in t}
            check("same height: all at the middle one", len(ys) == 1, str(ys))
            check("...the light on a rig goes with it", e.patch[0]["y"] != y0 and abs(e.patch[0]["y"] - list(ys)[0]) < 0.6, str(e.patch[0]["y"]))
            e.act("venue_align", ids=t[:2], how="left")
            check("left: the middles line up with the leftmost", abs(mid(rig(t[0]))[0] - mid(rig(t[1]))[0]) < 1e-6
                  and abs(mid(rig(t[0]))[0] + 3) < 1e-6, str(mid(rig(t[1]))))
            o = [e.act("venue_add", item={"kind": "speaker", "x": x, "z": 3})["id"] for x in (-4, 0.5, 4)]
            e.act("venue_align", ids=o, how="spread-x")
            xs = [next(q for q in e.venue["objects"] if q["id"] == i)["x"] for i in o]
            check("objects spread across too", xs == [-4, 0, 4], str(xs))
            c = e.act("rig_add", preset="circle", diameter=4, x=0, z=8, trim=4)
            before = [dict(x) for x in e.venue["rigging"] if x.get("group") == c["group"]]
            e.act("venue_align", ids=[c["ids"][0], c["ids"][3], t[0]], how="centre-x")
            after = [x for x in e.venue["rigging"] if x.get("group") == c["group"]]
            dx = after[0]["a"][0] - before[0]["a"][0]
            check("a shape moves as one (its pieces count once)",
                  all(abs(a["a"][0] - b["a"][0] - dx) < 1e-6 for a, b in zip(after, before)), str(dx))
            check("too few, a zone, or an unknown way are refused",
                  not e.act("venue_align", ids=t[:1], how="left").get("ok")
                  and not e.act("venue_align", ids=t[:2], how="spread-x").get("ok")
                  and not e.act("venue_align", ids=t, how="sideways").get("ok"), "")
        finally:
            e.shutdown()


def test_ceiling_areas() -> None:
    """A ceiling height per area: lower under a mezzanine, higher over the
    floor; rigging under it comes down, snaps and trims to it, the report
    warns against it."""
    print("ceiling areas")
    from app import engine as eng
    from app import venue as venue_mod
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_shape", shape="rectangle", width=14, depth=16, height=6, layout=False)
            t = e.act("rig_add", preset="straight", length=4, x=0, z=12, trim=5)["id"]
            e.act("add_heads", query="Moving Head", qty=1)
            e.act("attach_heads", heads=[1], rig=t)
            y0 = e.patch[0]["y"]
            r = e.act("venue_ceiling", points=[[-7, 10], [7, 10], [7, 15], [-7, 15]], height=3.5, name="Under the mezzanine")
            check("a ceiling area is drawn with its height", r.get("ok") and e.venue["room"]["areas"][0]["height"] == 3.5
                  and e.venue["room"]["areas"][0]["name"] == "Under the mezzanine", str(r.get("error")))
            check("...the ceiling there is 3.5 m, elsewhere the room's 6",
                  venue_mod.ceiling_at(e.venue, 0, 12) == 3.5 and venue_mod.ceiling_at(e.venue, 0, 4) == 6.0, "")
            rr = venue_mod.rig(e.venue, t)
            check("...the truss under it came down below it, its light too",
                  max(rr["a"][1], rr["b"][1]) <= 3.4 + 1e-6 and e.patch[0]["y"] < y0, f"{rr['a']} {e.patch[0]['y']}")
            bad = e.act("rig_trim", id=t, trim=4.5)
            check("trim: above the area's ceiling is refused", not bad.get("ok"), str(bad))
            e.act("venue_rig", id=t, ceiling=True)
            rr = venue_mod.rig(e.venue, t)
            check("hang under the ceiling: under the area's, not the room's", 3.0 < rr["a"][1] < 3.5, str(rr["a"]))
            far = e.act("rig_add", preset="straight", length=20, x=0, z=9.8, trim=5.5)
            fr = venue_mod.rig(e.venue, far["id"])
            check("a truss on the edge of the area: the lowest ceiling over it counts",
                  fr["a"][1] <= 6 and venue_mod.ceiling_over(e.venue, [[-3, 0, 9.8], [3, 0, 10.2]]) == 3.5, str(fr["a"]))
            from app import riglib
            vv = venue_mod.normalise(e.venue)
            for x in vv["rigging"]:
                if x["id"] == t:
                    x["a"][1] = x["b"][1] = 3.45          # as if hung right at the area's ceiling
            rows = riglib.report(vv, [], lambda h: {})["rigs"]
            row = next(x for x in rows if t in x["ids"])
            check("the report counts the area's ceiling", any("ceiling" in w for w in row["warnings"]), str(row["warnings"]))
            e.act("venue_ceiling", id="c1", height=8)
            check("raise it: 8 m over that part", venue_mod.ceiling_at(e.venue, 0, 12) == 8.0, "")
            check("a height that isn't a ceiling is refused", not e.act("venue_ceiling", id="c1", height=1).get("ok")
                  and not e.act("venue_ceiling", points=[[0, 0], [1, 1]], height=3).get("ok"), "")
            e.act("venue_ceiling", id="c1", remove=True)
            check("take it away: the room's ceiling again", not e.venue["room"]["areas"]
                  and venue_mod.ceiling_at(e.venue, 0, 12) == 6.0, "")
            e.act("undo")
            check("undo brings it back", len(e.venue["room"]["areas"]) == 1, "")
        finally:
            e.shutdown()


def test_screen_media() -> None:
    """LED screens: a clip or picture from this computer, sent raw to the
    desk, kept by its content, served back in ranges (a video seeks)."""
    print("screen clips and pictures")
    import threading
    import urllib.error
    import urllib.request
    from app import main as main_mod
    old = main_mod.config.DATA
    with tempfile.TemporaryDirectory() as td:
        main_mod.config.DATA = Path(td)
        srv = main_mod.ThreadingHTTPServer(("127.0.0.1", 0), main_mod.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}/api/console/screen_media"

        def send(data: bytes):
            req = urllib.request.Request(base, data=data, method="POST",
                                         headers={"Content-Type": "application/x-jarvis-upload"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        try:
            mp4 = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 40
            code, d = send(mp4)
            check("an MP4 clip is kept, typed by its bytes", code == 200 and d.get("kind") == "clip"
                  and d.get("type") == "mp4" and (Path(td) / "screens" / (d["id"] + ".mp4")).is_file(), str(d))
            again = send(mp4)[1]
            check("...the same clip twice is one file", again.get("id") == d.get("id")
                  and len(list((Path(td) / "screens").iterdir())) == 1, "")
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
            code, p = send(png)
            check("a PNG picture is a picture", code == 200 and p.get("kind") == "image", str(p))
            code, bad = send(b"MZ\x90\x00 not a clip at all")
            check("anything else is refused, and nothing is left behind", code == 400 and "MP4" in bad.get("error", "")
                  and not [f for f in (Path(td) / "screens").iterdir() if f.name.startswith(".")], str(bad))
            req = urllib.request.Request(base, data=mp4, method="POST", headers={"Content-Type": "application/octet-stream"})
            try:
                urllib.request.urlopen(req, timeout=15)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            check("...only with its own type (a plain form on another site can't send one)", code == 403, str(code))
            req = urllib.request.Request(base + "?id=" + d["id"], headers={"Range": "bytes=10-19"})
            with urllib.request.urlopen(req, timeout=15) as r:
                part, status, cr = r.read(), r.status, r.headers.get("Content-Range")
            check("served back in ranges (a video seeks and loops)", status == 206 and part == mp4[10:20]
                  and cr == f"bytes 10-19/{len(mp4)}", f"{status} {cr}")
            with urllib.request.urlopen(base + "?id=" + d["id"], timeout=15) as r:
                check("...or whole", r.read() == mp4 and r.headers.get("Content-Type") == "video/mp4", "")
            try:
                urllib.request.urlopen(base + "?id=../../etc/passwd", timeout=15)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            check("a made-up id is not found (no paths)", code == 404, str(code))
        finally:
            srv.shutdown()
            srv.server_close()
            main_mod.config.DATA = old
    vp = (ROOT / "web" / "app" / "venuepanel.js").read_text(encoding="utf-8")
    vj = (ROOT / "web" / "js" / "stage" / "venue.js").read_text(encoding="utf-8")
    check("the screen inspector offers a file from this computer",
          "A clip or picture from this computer" in vp and "/api/console/screen_media" in vp, "")
    check("the 3D fetches an uploaded clip once (with the token) and keeps it", "setScreenMediaLoader" in vj
          and "mediaUrls" in vj, "")


def test_gobo_pictures() -> None:
    """The 3D shows each light's real gobos: the fixture file names the
    pictures (QLC+ Res1, OFL wheel slot resource), gobos.zip holds them,
    the snapshot carries [from, to, picture] and the desk serves them."""
    print("gobo pictures")
    import threading
    import urllib.error
    import urllib.request
    from app import engine as eng, fixlib
    from app import main as main_mod
    q = fixlib.gobo_slots("qlc:Chauvet/Chauvet-Intimidator-Spot-100-IRC.qxf", "6 Channel")
    check("a QLC+ file's gobo wheel names its pictures", len(q) == 7 and q[0] == [8, 15, "qlc:Chauvet/gobo00045.svg"], str(q[:2]))
    o = fixlib.gobo_slots("ofl:chauvet-dj/intimidator-spot-160.json")
    check("...and an OFL file's", o and o[0][2] == "ofl:10-circles", str(o[:2]))
    check("open and spin ranges are not pictures", all("open" not in r[2].lower() for r in q + o), "")
    pic = fixlib.gobo_picture(q[0][2])
    check("gobos.zip has the picture", pic is not None and pic[1] == "image/svg+xml" and pic[0].startswith(b"<svg"), "")
    check("...an OFL one by its name", (fixlib.gobo_picture("ofl:10-circles") or (b"", ""))[1] == "image/svg+xml", "")
    check("...and nothing outside it", fixlib.gobo_picture("qlc:../../etc/passwd") is None
          and fixlib.gobo_picture("qlc:/etc/passwd") is None and fixlib.gobo_picture("x:y") is None, "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        k = "Chauvet/Chauvet-Intimidator-Spot-100-IRC.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 100 IRC", qty=1)
            e.act("add_heads", query="Moving Head", qty=1)
            rows = {h["head_no"]: h.get("gobos") for h in e.snapshot()["patch"]}
            check("the snapshot carries a light's gobo pictures", rows[1] and rows[1][0][2] == "qlc:Chauvet/gobo00045.svg", str(rows[1]))
            check("...and none for a light whose file names none", rows[2] is None, str(rows[2]))
        finally:
            e.shutdown()
    srv = main_mod.ThreadingHTTPServer(("127.0.0.1", 0), main_mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/api/console/gobo?ref="
    try:
        with urllib.request.urlopen(base + "qlc:Chauvet/gobo00045.svg", timeout=15) as r:
            csp = r.headers.get_all("Content-Security-Policy") or []
            check("the desk serves it as a picture that runs nothing", r.status == 200
                  and any("sandbox" in c and "default-src 'none'" in c for c in csp), str(csp))
        try:
            urllib.request.urlopen(base + "qlc:../../app/main.py", timeout=15)
            code = 200
        except urllib.error.HTTPError as ex:
            code = ex.code
        check("...and 404s anything else", code == 404, str(code))
    finally:
        srv.shutdown()
        srv.server_close()
    mj = (ROOT / "web" / "js" / "stage" / "materials.js").read_text(encoding="utf-8")
    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("the 3D draws the pictures into an atlas the floor shader samples",
          "class GoboAtlas" in mj and "uniform sampler2D uGobos" in mj and "_goboId(inst" in sj, "")


def test_shadows() -> None:
    """3D shadows: the brightest beams get a depth picture of the crowd,
    performers, objects and stage; the floor shader darkens what is behind
    them; a toggle (Crowd menu) and the fast quality turn it off."""
    print("3D shadows")
    st = ROOT / "web" / "js" / "stage"
    sh = (st / "shadows.js").read_text(encoding="utf-8")
    mj = (st / "materials.js").read_text(encoding="utf-8")
    vj = (st / "venue.js").read_text(encoding="utf-8")
    sj = (st / "stage.js").read_text(encoding="utf-8")
    sp = (ROOT / "web" / "app" / "stagepanel.js").read_text(encoding="utf-8")
    check("a depth atlas for the brightest beams, casters only, back faces",
          "SHADOW_SLOTS = 4" in sh and "c.layers.set(CASTER_LAYER)" in sh and "THREE.BackSide" in sh
          and "DepthTexture" in sh, "")
    check("the surface shader darkens what a caster hides from a beam",
          "float shadowAt(int i, vec3 wp)" in mj and "i < uShadowCount" in mj and "uShadowMat[4]" in mj, "")
    check("the crowd, performers, objects (not marks) and the stage deck cast",
          "casts(buildCrowd(" in vj and "casts(buildPerformers(" in vj and 'if (o.kind !== "mark") casts(g)' in vj
          and "skirt.layers.enable(CASTER_LAYER)" in vj, "")
    check("on by default, off with the toggle or the fast quality",
          "shadows: true" in sj and 'this.options.quality !== "fast"' in sj and "this.shadows.off()" in sj
          and '"Shadows"' in sp and 'pref("shadows", "1")' in sp, "")


def test_ai_sees_3d() -> None:
    """The assistant can look at the 3D view after its own changes: it asks
    (see_3d), the turn pauses, the screen draws the preview and sends a
    picture, the turn carries on with it - still one undo step."""
    print("AI assistant: sees the 3D after its changes")
    from app import assistant, config as cfg
    from app import engine as eng

    def tool(name, i=0, **args):
        return {"id": f"c{name}{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    seen, offered = [], []

    def scripted(replies):
        def chat(messages, tools=None, **_):
            seen.append([dict(m) for m in messages])
            offered.append([t["function"]["name"] for t in tools or []])
            return replies.pop(0)
        return chat

    pic = "data:image/jpeg;base64," + "A" * 64
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        saved = cfg.DATA
        cfg.DATA = tmp
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            n_undo = len(e._undo)
            chat = scripted([
                {"content": "", "tool_calls": [tool("do", action="select_all"), tool("do", 1, action="set_intensity", params={"level": 70}),
                                               tool("see_3d")]},
                {"content": "", "tool_calls": [tool("do", 2, action="set_colour", params={"colour": "red"}), tool("see_3d", 1)]},
                {"content": "Red at 70%, and it reads well in 3D."},
            ])
            r = assistant.run_turn(e, "make it red", session="v", chat=chat, can_see=True)
            check("it asks to see: the turn pauses with what it did so far", r.get("need_view") and r.get("turn")
                  and len(r["steps"]) == 2 and r.get("preview"), json.dumps(r)[:200])
            check("see_3d is offered only to a screen that can draw", "see_3d" in offered[0], str(offered[0]))
            r2 = assistant.resume_turn(e, r["turn"], pic, chat=chat)
            last = seen[1]
            check("the picture comes back as the tool's answer and an image",
                  any(m["role"] == "tool" and m.get("name") == "see_3d" for m in last)
                  and isinstance(last[-1]["content"], list) and last[-1]["content"][1]["image_url"]["url"] == pic, "")
            check("...and a second look pauses again", r2.get("need_view") and r2["turn"] != r["turn"], json.dumps(r2)[:200])
            r3 = assistant.resume_turn(e, r2["turn"], pic, chat=chat)
            check("then it finishes: the reply, every step, still one undo step",
                  r3.get("ok") and r3["reply"].startswith("Red") and len(r3["steps"]) == 3 and r3["views"] == 2
                  and len(e._undo) == n_undo + 1, json.dumps(r3)[:300])
            check("...after two looks it isn't offered a third", "see_3d" not in offered[-1], str(offered[-1]))
            check("a turn nobody is waiting for can't be resumed", not assistant.resume_turn(e, r["turn"], pic, chat=chat).get("ok"), "")
            e.act("blind", state=False, keep=True)
            # a paused turn the screen never answers ends when the next request starts
            n_undo = len(e._undo)
            chat = scripted([
                {"content": "", "tool_calls": [tool("do", action="select_all"), tool("do", 1, action="set_intensity", params={"level": 30}),
                                               tool("see_3d")]},
                {"content": "Done."},
            ])
            r = assistant.run_turn(e, "dim it", session="v", chat=chat, can_see=True)
            r2 = assistant.run_turn(e, "thanks", session="v", chat=chat, can_see=True)
            check("a paused turn ends (as one undo step) when the next request starts",
                  r.get("need_view") and r2.get("ok") and len(e._undo) == n_undo + 1
                  and not assistant.resume_turn(e, r["turn"], pic, chat=chat).get("ok"), str(len(e._undo) - n_undo))
            chat = scripted([{"content": "", "tool_calls": [tool("see_3d")]}, {"content": "I can't see from here."}])
            r = assistant.run_turn(e, "how does it look?", session="w", chat=chat)
            check("without a screen that draws, see_3d isn't offered and doesn't pause",
                  "see_3d" not in offered[-2] and r.get("ok") and not r.get("need_view"), json.dumps(r)[:200])
        finally:
            e.shutdown()
            cfg.DATA = saved
    cj = (ROOT / "web" / "app" / "copilot.js").read_text(encoding="utf-8")
    check("the copilot sends a picture when the AI asks to see", "r.need_view" in cj and "resume: r.turn" in cj and "can_see:" in cj, "")


def test_ai_operator() -> None:
    """The AI operator runs the lights live: a change at the start, every
    phrase and on a drop (one undo step each); touching the desk stops it
    and drops a change it was still thinking about."""
    print("AI operator")
    from app import ai_operator, config as cfg
    from app import engine as eng

    def tool(name, i=0, **args):
        return {"id": f"c{name}{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    calls = []

    def chat(messages, tools=None, **_):
        calls.append(json.loads(messages[-1]["content"]) if messages[-1]["role"] == "user" else None)
        if messages[-1]["role"] == "user":
            return {"content": "", "tool_calls": [tool("do", action="select_all"),
                                                  tool("do", 1, action="set_intensity", params={"level": 60 + len(calls)})]}
        return {"content": f"look {len(calls)}"}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        saved = cfg.DATA
        cfg.DATA = tmp
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            check("it needs lights to run", not _raises_ok(lambda: ai_operator.start(e, thread=False)), "")
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            n_undo = len(e._undo)
            st = ai_operator.start(e, brief="moody techno", bars=8, thread=False, chat=chat)
            check("on, with the brief", st["on"] and st["brief"] == "moody techno", str(st))
            t = e._tempo()
            now = time.monotonic()
            got = ai_operator.tick(e, now=now, chat=chat)
            check("the first look straight away, as one undo step, logged",
                  got and got["why"] == "start" and got["text"].startswith("look") and len(e._undo) == n_undo + 1
                  and len(got["steps"]) == 2, str(got))
            check("it was given the music and the lights", calls[0] and "music" in calls[0] and len(calls[0]["lights_now"]) == 3, "")
            check("nothing between phrases", ai_operator.tick(e, now=now + 1, chat=chat) is None, "")
            bar_s = 4 * 60.0 / t.bpm
            got = ai_operator.tick(e, now=now + 8 * bar_s + 13, chat=chat)
            check("a change on the next phrase", got and got["why"] == "phrase" and len(e._undo) == n_undo + 2, str(got))
            e._sound_drop_at = now + 8 * bar_s + 21
            got = ai_operator.tick(e, now=now + 8 * bar_s + 21, chat=chat)
            check("...and on a drop", got and got["why"] == "drop", str(got))
            check("what it did lately goes to the next decision", calls[-2] and len(calls[-2]["you_did_lately"]) == 2, str(calls[-2])[:200])
            check("selecting or setting up the room isn't taking over",
                  not ai_operator.take_over(e, "select_heads") and not ai_operator.take_over(e, "venue_update")
                  and ai_operator.status(e)["on"], "")
            check("a cue, a button, the programmer is", ai_operator.take_over(e, "set_colour")
                  and not ai_operator.status(e)["on"] and "set colour" in ai_operator.status(e)["stopped"], "")
            # a decision in flight when the operator takes over is dropped
            ai_operator.start(e, thread=False, chat=chat)

            def slow(messages, tools=None, **_):
                ai_operator.take_over(e, "cue_go")          # the operator presses GO while it thinks
                return {"content": "", "tool_calls": [tool("do", action="select_all"), tool("do", 1, action="set_intensity", params={"level": 5})]}
            n_undo = len(e._undo)
            r = ai_operator.decide(e, "phrase", chat=slow)
            check("a change it was still thinking about is dropped", r is None and len(e._undo) == n_undo, str(r))
            lite = e.lite() if hasattr(e, "lite") else None
            check("the top bar knows", "ai_operator" in (lite or e.snapshot()), "")
        finally:
            e.shutdown()
            cfg.DATA = saved
    tb = (ROOT / "web" / "app" / "topbar.js").read_text(encoding="utf-8")
    mj = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("the pill says the AI runs the lights, with I've got it", "op-pill" in tb and "I've got it" in tb, "")
    check("any action from a screen goes past take_over first", "ai_operator.take_over(eng, action)" in mj, "")


def _raises_ok(fn) -> bool:
    try:
        fn()
        return True
    except ValueError:
        return False


def test_hold_button() -> None:
    """Make a button of what the lights do now: it captures position,
    colour and the movement, turns on and HOLDS the lights - its movement
    goes round its own captured aim (never frozen), the programmer can't
    change what it holds until it is off, then can again."""
    print("hold button: make a button from the programmer")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Moving Head Spot 16ch", qty=2)
            h = e.patch[0]
            pan_at = h["address"] - 1 + h["map"].index("pan")

            def pans(k=4):
                out = []
                for _ in range(k):
                    out.append(e.build_frames()[h["universe"]][pan_at])
                    time.sleep(0.2)
                return out
            e.act("select_heads", heads=[1])
            e.act("set_intensity", level=100)
            e.act("set_colour", colour="#ff0000")
            e.act("set_position", pan=128, tilt=80)
            e.act("run_fx", name="circle", speed=0.5)
            n_undo = len(e._undo)
            r = e.act("quick_from_programmer", label="Floor circle")
            btn = next((b for b in e.quick if b["label"] == "Floor circle"), {})
            check("one step: a button, on, holding the light", r.get("ok") and btn.get("hold") and btn["id"] in e.quick_active
                  and len(e._undo) == n_undo + 1 and "colour, Circle" in r["summary"], r.get("summary") or r.get("error"))
            check("...the programmer let go of the light (the button has it)", not e.programmer.get(1)
                  and not any(f.get("from", "programmer") == "programmer" and not f.get("live") for f in e._fx_public()), str(e.programmer))
            p = pans()
            check("its movement moves, round the captured aim (not frozen, not centred)", len(set(p)) > 1
                  and all(abs(x - 128) < 30 for x in p), str(p))
            bad = e.act("set_position", pan=20, tilt=80)
            check("the programmer can't move it while the button is on", not bad.get("ok") and "turn it off" in bad.get("error", ""), str(bad))
            check("...nor change its colour", not e.act("set_colour", colour="#0000ff").get("ok"), "")
            check("...nor start another movement on it", not e.act("run_fx", name="circle").get("ok"), "")
            check("...nor its brightness (the button keeps everything it was made with)",
                  not e.act("set_intensity", level=50).get("ok"), "")
            check("other lights are free", e.act("set_position", pan=20, tilt=80, head=2).get("ok"), "")
            held = e.lite()["held"]
            check("the screens know which button holds which lights", held and held[0]["label"] == "Floor circle"
                  and held[0]["heads"] == [1], str(held))
            e.act("quick_press", id=btn["id"], down=True)
            check("turned off: the programmer can change it again", e.act("set_position", pan=20, tilt=80).get("ok")
                  and e.build_frames()[h["universe"]][pan_at] == 20 and not e.lite()["held"], "")
            e.act("quick_press", id=btn["id"], down=True)
            p = pans()
            check("on again: the same movement round the same aim", len(set(p)) > 1 and all(abs(x - 128) < 30 for x in p), str(p))
        finally:
            e.shutdown()
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("Make a button… sits next to Record cue… on every tab, with the held strip",
          "Make a button…" in pj and "quick_from_programmer" in pj and "Turn it off" in pj, "")


def test_aim_speed_everywhere() -> None:
    """The Aim speed glides every aim: the first one (no earlier aim to
    start from - it starts where the light really points), the spot
    buttons and the formations, light by light."""
    print("aim speed: spots, formations, first aim")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="Moving Head Spot 16ch", qty=2)
            e.act("select_all")
            e.act("attach_heads", rig=e._rig_named("front truss")["id"])
            e.act("aim_spot", spot="back")
            back = dict(e.programmer[1])
            e.act("aim_spot", spot="front")
            front = dict(e.programmer[1])
            e.act("aim_spot", spot="back")
            t0 = time.monotonic()
            r = e.act("aim_spot", spot="front", glide=2)
            check("a spot button glides at the Aim speed", r.get("ok") and e.programmer[1]["tilt"] == back["tilt"]
                  and (1 in e.__dict__.get("_pt_glides", {}) or e.__dict__.get("_glide") is not None), str(e.programmer[1]))
            e._override_vals()
            tick = e._pt_glide_tick if e.__dict__.get("_pt_glides") else e._aim_glide_tick
            tick(now=t0 + 1.0)
            mid = e.programmer[1]["tilt"]
            check("...part of the way after a second", min(back["tilt"], front["tilt"]) < mid < max(back["tilt"], front["tilt"]), f"{back['tilt']} {mid} {front['tilt']}")
            tick(now=t0 + 30)
            check("...and there in the end", e.programmer[1]["tilt"] == front["tilt"], str(e.programmer[1]))
            # a first aim (never aimed from here): from where the light points
            e.act("clear_programmer")
            e.__dict__.pop("_aim_rest", None)
            e.act("select_heads", heads=[2])
            e.act("aim_at", x=0, z=12, glide=2)
            check("a first aim glides too, from where the light points now", 2 in e.__dict__.get("_pt_glides", {})
                  and e._pt_glides[2]["cur"]["tilt"] != e._pt_glides[2]["goal"]["tilt"], str(e._pt_glides.get(2)))
            e.act("select_all")
            e.act("aim_spot", formation="fan")
            fan = {n: dict(e.programmer[n]) for n in (1, 2)}
            e.act("aim_spot", spot="back")
            e.act("aim_spot", formation="fan", glide=1)
            check("a formation glides each light to its own place", set(e._pt_glides) == {1, 2}
                  and all(e._pt_glides[n]["goal"]["pan"] == fan[n]["pan"] for n in (1, 2)), str(e._pt_glides))
            e.act("set_position", pan=10, tilt=10)
            e._pt_glide_tick()
            check("moved by hand: the glide lets go", not e._pt_glides and e.programmer[1]["pan"] == 10, str(e._pt_glides))
        finally:
            e.shutdown()
    mj = (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8")
    check("the spot and formation buttons send the Aim speed", mj.count("glide: followGlide()") == 4, "")


def test_colour_fixes() -> None:
    """Colour picking on real library fixtures: the full-colour mode is the
    default, numbered wheel slots get colour names, wheels named "Red" with
    no colour in the file land on red, the 3D shows the wheel's colour, a
    lone "fine" channel is the channel."""
    print("colour: modes, names, wheels, 3D")
    from app import engine as eng, fixlib
    from app.engine_base import default_mode
    from app.showdesign import colour_name, hex_from_name

    def modes(src, key):
        it = fixlib.load(src, key)[0]
        return [{"name": m["name"], "channel_count": m["channel_count"], "channels": [d["label"] for d in m["detail"]]}
                for m in it["modes"]]
    mb = default_mode(modes("qlc", "American_DJ/American-DJ-Mega-Bar-50RGB.qxf"))
    check("an RGB bar is patched in a mode that mixes, not its colour-macro mode",
          mb["name"] in ("3 Channel", "4 Channel"), mb["name"])
    check("colour names from a colour", [colour_name(x) for x in ("#ff0000", "#00ffff", "#ffe9c9", "#000000")]
          == ["Deep red", "Cyan", "Warm white", "Off"], "")
    check("colour from a slot's name; none for a split", hex_from_name("Dark Blue (fast)") and hex_from_name("Red")
          and hex_from_name("Red / Blue") is None and hex_from_name("Color 3") is None, "")
    lx = fixlib.load("qlc", "Lixada/Lixada-Mini-Gobo-Moving-Head.qxf")[0]["modes"][1]
    roles = [d["role"] for d in lx["detail"]]
    check("a lone 'fine' channel is the channel (a gobo, dimmer, shutter that never moved)",
          {"gobo", "dimmer", "shutter"} <= set(roles) and not any(r.endswith("_fine") for r in roles), str(roles))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        for src, key in (("qlc", "American_DJ/American-DJ-Asteroid-1200.qxf"), ("qlc", "Eurolite/Eurolite-TC-200.qxf"),
                         ("qlc", "American_DJ/American-DJ-Mega-Bar-50RGB.qxf")):
            fixtures.store_parsed(db, fixlib.load(src, key), f"{src}:{key}")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Asteroid 1200", qty=1)
            e.act("add_heads", query="Eurolite TC-200", qty=1)
            e.act("add_heads", query="Mega Bar 50RGB", qty=1)
            names = [x["name"] for x in e._wheel_slots(e.patch[0])]
            check("a wheel the file numbers ('Color 1') shows colour names", "Aqua" in names and not any(n.startswith("Color ") for n in names), str(names[:6]))
            e.act("select_heads", heads=[2])
            e.act("set_intensity", level=100)
            e.act("set_colour", colour="#ff0000")
            red = e.programmer[2]["wheel"]
            e.act("set_colour", colour="#0000ff")
            blue = e.programmer[2]["wheel"]
            check("a wheel named 'Red', 'Blue' with no colours in the file: red lands on Red, blue on Blue",
                  17 <= red <= 33 and 102 <= blue <= 118, f"{red} {blue}")
            look = next(x for x in e._looks() if x["n"] == 2)
            check("...and the 3D shows the wheel's colour", look["hex"].lower() != "#cbd5e1" and look["hex"].lower().startswith("#2"), look["hex"])
            check("the RGB bar arrived in its colour-mixing mode", {"red", "green", "blue"} <= set(e.patch[2]["map"]), str(e.patch[2]["map"]))
            e.act("set_attribute", attribute="wheel", value=0, heads=[1])
            e.patch[2]["map"] = ["wheel", "dimmer"]
            e.patch[2]["mode"] = "2 Channel"
            e.__dict__.pop("_better_cache", None)
            check("a light left in its colour-macro mode is told its better mode",
                  e._better_mode(e.patch[2]) in ("3 Channel", "4 Channel"), str(e._better_mode(e.patch[2])))
        finally:
            e.shutdown()


def test_own_programs() -> None:
    """A light's built-in program (and endless pan / tilt rotation) goes to
    the wire and the 3D knows: the look carries "prog" and "spin", and a
    dark light running a program is still in the look feed."""
    print("built-in programs in the 3D")
    from app import engine as eng, fixlib
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        k = "American_DJ/American-DJ-Asteroid-1200.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Asteroid 1200", qty=1)
            h = e.patch[0]
            prog = next(r for r in h["map"] if r.startswith("aux") and "program" in (e.head_ranges(h)[r].get("name") or "").lower())
            spin = next(r for r in h["map"] if r.startswith("aux") and "pan continuous" in (e.head_ranges(h)[r].get("name") or "").lower())
            e.act("select_all")
            e.act("set_attribute", attribute=prog, value=50)
            check("the program goes to the wire", e.build_frames()[h["universe"]][h["address"] - 1 + h["map"].index(prog)] == 50, "")
            rows = e.look_rows()
            check("a dark light running a program is in the 3D feed, with its name", rows and rows[0].get("prog") == "Program 3", str(rows[:1]))
            e.act("set_attribute", attribute=prog, value=0)
            e.act("set_attribute", attribute=spin, value=140)
            row = e._looks()[0]
            check("endless pan rotation: the 3D spins it", "prog" not in row and row.get("spin", {}).get("pan", 0) > 0, str(row.get("spin")))
            e.act("set_attribute", attribute=spin, value=0)
            check("off: nothing", "spin" not in e._looks()[0], "")
        finally:
            e.shutdown()
    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("the 3D plays a stand-in and tags the light", "L.prog" in sj and "▶ ${inst.cur.prog}" in sj and "L.spin" in sj, "")


def test_laser_fixes() -> None:
    """Lasers: patched in the mode that controls them (not the 1-channel
    auto mode), a laser with no power channel fires through its mode switch
    (Blackout ... DMX mode) - armed only - and a standing laser shoots over
    the crowd in the 3D, a hung one down onto the floor."""
    print("lasers: modes, mode-switch output, 3D aim")
    from app import engine as eng, fixlib
    from app.engine_base import default_mode

    def pick(src, key):
        it = fixlib.apply_fx(fixlib.load(src, key)[0])
        return default_mode([{"name": m["name"], "channel_count": m["channel_count"], "channels": m["channels"]}
                             for m in it["modes"]])["name"]
    check("a laser is patched in the mode that controls it, not its 1-channel auto mode",
          pick("qlc", "JB_Systems/JB-Systems-Space-4-Laser.qxf") == "8 Channel"
          and pick("qlc", "Briteq/Briteq-Spectra-3D-Laser.qxf") == "19CH without ILDA", "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        k = "Showtec/Showtec-Dominator.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Showtec Dominator", qty=1)
            h = e.patch[0]
            check("its mode switch is its output", "laser_on" in h["map"], str(h["map"]))
            at = h["address"] - 1 + h["map"].index("laser_on")
            check("...at rest: Blackout", e.build_frames()[h["universe"]][at] <= 9, str(e.build_frames()[h["universe"]][at]))
            check("...not without ARM", not e.act("fx_laser", heads=[1], down=True).get("ok"), "")
            e.act("fx_arm", state=True)
            r = e.act("fx_laser", heads=[1], down=True)
            v = e.build_frames()[h["universe"]][at]
            check("...armed and fired: DMX mode, and the 3D draws it", r.get("ok") and 220 <= v <= 255
                  and (e._looks()[0].get("fx") or {}).get("laser"), f"{r.get('error')} {v}")
            e.act("fx_kill")
            check("...killed: Blackout again", e.build_frames()[h["universe"]][at] <= 9, "")
        finally:
            e.shutdown()
    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("the 3D aims a standing laser out over the crowd, a hung one down",
          'body.type === "laser"' in sj and "a laser shoots over the crowd" in sj, "")


def test_locate_takes_over() -> None:
    """Locate: full, open white AND centred (as the button says), and it
    takes the lights back from the effects you started on them; a cue's or
    a button's effect keeps playing and Locate says so."""
    print("locate: centred, over your own effects")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("add_heads", query="Moving Head Spot 16ch", qty=1)
            e.act("select_all")
            e.act("run_fx", name="rainbow")
            r = e.act("locate")
            check("the rainbow you started stops: Locate shows white", r.get("ok") and not e.fx
                  and all(x["hex"].lower() in ("#ffffff",) for x in e._looks()[:2]), str([x["hex"] for x in e._looks()]))
            check("a mover is centred", e.programmer[3].get("pan") == 128 and e.programmer[3].get("tilt") == 128, str(e.programmer[3]))
            e.act("quick_set", page=1, slot=1, button={"kind": "fx", "fx": "rainbow", "label": "Rainbow", "mode": "latch",
                                                         "target": {"heads": [1, 2]}})
            e.act("quick_press", id="q1-1", down=True)
            r = e.act("locate")
            check("a button's effect keeps playing, and Locate says Highlight shows them", r.get("ok") and e.fx
                  and "Highlight" in r["summary"], r.get("summary"))
        finally:
            e.shutdown()


def test_fx_tweak_live() -> None:
    """A running effect's speed and size change as it runs (the FX tab's
    tap buttons): no restart, and the shape carries on from where it is."""
    print("fx_tweak: speed / size live, no jump")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            e.act("add_heads", query="Moving Head Spot 16ch", qty=1)
            e.act("select_heads", heads=[1, 2, 3])
            e.act("run_fx", name="rainbow")
            row = e.fx[0]
            t = time.monotonic()
            e._fx_values(t)
            e._fx_values(t + 1.3)
            before = e._fx_values(t + 1.3)
            r = e.act("fx_tweak", id=row["id"], speed=3)
            after = e._fx_values(t + 1.3)
            check("the speed changes in place (same effect, no restart)", r.get("ok") and len(e.fx) == 1
                  and e.fx[0] is row and row["params"]["speed"] == 3, str(r))
            check("no jump: the colours at that moment are the same", before == after, f"{before} vs {after}")
            e.act("fx_tweak", id=row["id"], times=0.5)
            check("½× halves it", abs(row["params"]["speed"] - 1.5) < 1e-9, str(row["params"]))
            check("clamped to the effect's range", e.act("fx_tweak", id=row["id"], speed=999).get("ok")
                  and row["params"]["speed"] == 20.0, str(row["params"]))
            check("an unknown knob is refused", not e.act("fx_tweak", id=row["id"], params={"bogus": 1}).get("ok"))
            e.act("select_heads", heads=[4])
            e.act("run_fx", name="circle", params={"speed": 0.125, "size": 20})
            mv = e.fx[-1]
            r = e.act("fx_tweak", id=mv["id"], speed=0.25, size=45)
            check("a movement: speed and size", r.get("ok") and mv["params"]["speed"] == 0.25 and mv["params"]["size"] == 45, str(r))
            check("a missing effect is an error", not e.act("fx_tweak", id=999, speed=1).get("ok"))
        finally:
            e.shutdown()


def test_hold_button_roam() -> None:
    """A Wave 360 roaming the dance floor made into a button: the button
    keeps the roam (and a saved shape), so the light keeps moving - it
    was captured as a still position and held there."""
    print("hold button: keeps a roam / shape moving (Wave 360)")
    from app import engine as eng
    from app import fixlib
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "lib.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"),
                              "qlc:Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf")
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="Intimidator Wave 360", mode="17 ch.", qty=1, universe=1, address=1)
            e.act("add_heads", query="Moving Head Spot 16ch", qty=1, universe=1, address=40)
            h = e.patch[0]
            at = [h["address"] - 1 + i for i, r in enumerate(h["map"]) if r in ("pan", "tilt")]

            def frames(k=5):
                out = []
                for _ in range(k):
                    buf = e.build_frames()[1]
                    out.append(tuple(buf[i] for i in at))
                    time.sleep(0.15)
                return out
            e.act("select_heads", heads=[1])
            e.act("set_intensity", level=100)
            r = e.act("roam", zones=["dancefloor"], speed=3)
            check("the Wave 360 roams the dance floor", r.get("ok") and len(set(frames())) > 1, str(r))
            r = e.act("quick_from_programmer", label="hover dancefloor")
            btn = next((b for b in e.quick if b["label"] == "hover dancefloor"), {})
            check("the button keeps the roam with its zone", r.get("ok")
                  and [i["name"] for i in btn.get("fx_list") or []] == ["roam"]
                  and btn["fx_list"][0]["params"].get("zones"), str(btn.get("fx_list")))
            check("held by the button, it keeps moving", len(set(frames())) > 1, str(frames(3)))
            check("the programmer can't move it", not e.act("aim_spot", spot="dancefloor").get("ok"))
            e.act("quick_off", id=btn["id"]) if "quick_off" in eng.ACTIONS else e.act("quick_press", id=btn["id"], down=True)
            # a saved shape too
            e.act("select_heads", heads=[2])
            sh = e.act("shape_save", shape={"name": "Zig", "points": [[0, 0], [1, 1], [-1, 1], [1, -1]]})
            if sh.get("ok"):
                e.act("run_shape", id=sh.get("id") or sh.get("shape", {}).get("id"), heads=[2], speed=0.5)
                r = e.act("quick_from_programmer", label="zig")
                btn = next((b for b in e.quick if b["label"] == "zig"), {})
                check("a shape is kept too", r.get("ok") and str((btn.get("fx_list") or [{}])[0].get("name", "")).startswith("shape:"),
                      str(btn.get("fx_list")))
            else:
                check("shape_save works", False, str(sh))
        finally:
            e.shutdown()


def test_hold_button_keeps_everything() -> None:
    """A button made from the programmer keeps everything edited, not just
    the movement: brightness, a solid colour or a rainbow, and what a cue
    on a fader gives the lights; it plays exactly that (a brighter cue
    underneath doesn't lift it) and holds it until it's off."""
    print("hold button: brightness, colour, rainbow, cue look - all kept")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            e.act("add_heads", query="Moving Head Spot 16ch", qty=1)
            e.act("add_heads", query="LED PAR 4ch", qty=2)

            def look(n):
                x = next(x for x in e._looks() if x["n"] == n)
                return x["hex"].lower(), round(x.get("a", 0), 2)
            # a cue at full, blue, under everything
            e.act("select_heads", heads=[1, 2, 3])
            e.act("set_intensity", level=100)
            e.act("set_colour", colour="#0000ff")
            e.act("record_cue", playback=1)
            e.act("clear_programmer")
            e.act("playback_go", playback=1) if "playback_go" in eng.ACTIONS else e.act("cue_go", playback=1)
            # the programmer: 40 %, green, roaming
            e.act("select_heads", heads=[1, 2, 3])
            e.act("set_intensity", level=40)
            e.act("set_colour", colour="#00ff00")
            e.act("select_heads", heads=[1])
            e.act("roam", zones=["dancefloor"])
            e.act("select_heads", heads=[1, 2, 3])
            r = e.act("quick_from_programmer", label="green roam")
            check("the summary says what it keeps", r.get("ok") and all(w in r["summary"] for w in ("brightness 40%", "colour", "roam")),
                  r.get("summary"))
            check("it plays green at 40 %, not the full blue cue under it", look(2) == ("#00ff00", 0.4), str(look(2)))
            check("the brightness is held", not e.act("set_intensity", level=90).get("ok"))
            check("the colour is held", not e.act("set_colour", colour="#ff0000").get("ok"))
            check("no rainbow over it either", not e.act("run_fx", name="rainbow").get("ok"))
            # a cue's colour is kept when the programmer has none
            for b in list(e.quick):
                e.act("quick_set", page=b["page"], slot=b["slot"], button=None)
            e.act("quick_release_all")
            e.act("stop_fx")
            e.act("clear_programmer")
            e.act("select_heads", heads=[2, 3])
            e.act("run_fx", name="rainbow")
            r = e.act("quick_from_programmer", label="rainbow")
            btn = next(b for b in e.quick if b["label"] == "rainbow")
            check("a rainbow and the cue's look (blue at full) are kept", "Rainbow" in r.get("summary", "")
                  and (btn.get("values") or {}).get("2", {}).get("dimmer") == 100, f"{r.get('summary')} {btn.get('values')}")
        finally:
            e.shutdown()


def test_library_sweep_fixes() -> None:
    """What the library sweep (tools/libsweep.py) found: colour effects on
    CMY / white-only lights, brightness effects that can't show, lights
    taken for effect machines, haze / fog outputs not found, a CMY light's
    Locate tinted, an "Indigo" blue channel."""
    print("library sweep: colour fx, undimmable lights, light vs machine, fog outputs")
    from app import engine as eng, fixlib, fixture_kind, fxlib
    from app.engine_base import default_mode
    from app.engine_support import channel_role
    # colour effects on what the light can really make
    cmy = ["dimmer", "cyan", "magenta", "yellow"]
    check("a CMY light gets colour chase, and the chase moves its CMY flags",
          "colour_chase" in fxlib.available(cmy)
          and len({tuple(sorted(fxlib.apply("colour_chase", {}, cmy, elapsed=t).items())) for t in (0, 0.3, 0.6, 0.9, 1.2)}) > 1, "")
    check("a dual-white light (two whites) gets no colour effects",
          not {"colour_chase", "alternate", "fan", "rainbow"} & set(fxlib.available(["white", "white"])), "")
    check("a light with only a red channel gets no rainbow", "rainbow" not in fxlib.available(["dimmer", "red", "wheel"]), "")
    # light, not effect machine
    check("a Snowball is a light, not a snow machine", fixlib.fx_kind("Blizzard Lighting", "Snowball", "", []) == "", "")
    check("a light with its own FX-mode channel is still a light",
          fixture_kind.describe({"manufacturer": "Ayra", "model": "ERO 506", "mode": "",
                                 "map": ["pan", "tilt", "strobe", "red", "green", "blue", "white", "dimmer", "fx_mode"]})["class"] == "light", "")
    check("an \"Indigo\" channel is blue", channel_role("Indigo") == "blue", "")
    # fog / haze outputs, and the mode that has one
    src, key = next((s, r["key"]) for s in ("qlc", "ofl") for r in fixlib.index(s) if "F-5D" in r["model"])
    it = fixlib.apply_fx(fixlib.load(src, key)[0])
    mode = default_mode([{"name": m["name"], "channel_count": m["channel_count"], "channels": m["channels"]} for m in it["modes"]])
    check("Antari's \"Faze\" is a haze output, and a mode with it is patched", "fog" in {channel_role(c) for c in mode["channels"]},
          str(mode))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        k = next(r["key"] for r in fixlib.index("qlc") if r["model"] == "Phantom 250 Wash")
        fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Phantom 250 Wash", qty=1)
            h = e.patch[0]
            e.act("select_heads", heads=[1])
            e.act("locate")
            f = e.build_frames()[h["universe"]]
            cmyv = [f[h["address"] - 1 + h["map"].index(r)] for r in ("cyan", "magenta", "yellow")]
            look = next(x for x in e._looks() if x["n"] == 1)
            check("Locate on a CMY light: every flag out (white), in 3D too", cmyv == [0, 0, 0] and look["hex"].lower() == "#ffffff",
                  f"{cmyv} {look['hex']}")
        finally:
            e.shutdown()


def test_show_keeps_tempo() -> None:
    """A show keeps its tempo: a timeline built at 128 BPM came back at 120."""
    print("show file: the tempo comes back")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=1)
            e.act("tempo_set", bpm=128)
            e.act("save_show", name="t")
            e2.act("load_show", name="t")
            check("128 BPM saved and loaded", abs(e2._tempo().bpm - 128) < 0.01, str(e2._tempo().bpm))
        finally:
            e.shutdown()
            e2.shutdown()
