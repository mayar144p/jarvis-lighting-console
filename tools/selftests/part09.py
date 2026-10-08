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
    from tools.selftests.common import _free_udp_port
    return _free_udp_port()


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
            check("pyro and deleting: only prepared for the operator's tap (A13)",
                  [c["action"] for c in r["confirm"]] == ["fx_fire", "delete_cue"] and not r["changed"], str(r)[:400])
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
          "class GoboAtlas" in mj and "texture(LIGHTS.uGobos.value" in mj and "_goboId(inst" in sj, "")


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
          "SHADOW_SLOTS = 8" in sh and "c.layers.set(CASTER_LAYER)" in sh and "THREE.BackSide" in sh
          and "DepthTexture" in sh, "")
    check("the surface shader darkens what a caster hides from a beam",
          "const shadowAt = Fn(([i, wp])" in mj and "i.lessThan(LIGHTS.uShadowCount)" in mj
          and 'arrayOf(SHADOW_SLOTS, () => new THREE.Matrix4(), "mat4")' in mj and "SHADOW_SLOTS = 8" in mj, "")
    check("High: 8 beams cast shadows, Medium 4",
          'this.options.quality === "high" ? 8 : 4' in sj and "Math.min(SHADOW_SLOTS, slots, n)" in sh, "")
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
          "Make a button…" in (ROOT / "web" / "index.html").read_text(encoding="utf-8") and "prog-record" in pj
          and "quick_from_programmer" in pj and "Turn it off" in pj, "")


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



def test_beam_switch_lasers() -> None:
    """A laser with no output channel (Laserworld RS400G: its Colour
    channel's "No beam" is its off) stays dark disarmed whatever the
    programmer says, fires armed, and the 3D agrees; an Antari Fazer's
    "Volume control" is its fog output (it was filed as a mode)."""
    print("lasers switched by a colour / mode channel; fog 'volume control'")
    from app import engine as eng, fixlib
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        for k in ("Laserworld/Laserworld-RS400G.qxf", "Antari/Antari-X-310-Pro-Fazer.qxf",
                  "Robe/Robe-Fog-1500-FT.qxf"):
            fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Laserworld RS400G", qty=1, universe=1, address=1)
            h = e.patch[0]
            col = h["map"].index("laser_colour")

            def wire():
                return list(e.build_frames()[1][:len(h["map"])])

            def drawn():
                return bool((next(x for x in e._looks() if x["n"] == 1).get("fx") or {}).get("laser"))
            check("it has no output channel, its colour channel switches the beam",
                  "laser_on" not in h["map"] and e._laser_switch(h).get("laser_colour"), str(h["map"]))
            e.act("select_heads", heads=[1])
            e.act("set_attribute", attribute="fx_mode", value=255)       # Manual mode
            e.act("set_attribute", attribute="laser_colour", value=255)  # "On"
            check("disarmed, Manual mode + colour 'On' programmed: still 'No beam'",
                  wire()[col] <= 63 and not drawn(), str(wire()))
            check("...not without ARM", not e.act("fx_laser", heads=[1], down=True).get("ok"), "")
            e.act("fx_arm", state=True)
            r = e.act("fx_laser", heads=[1], down=True)
            check("armed and fired: the programmed colour, and the 3D draws it",
                  r.get("ok") and wire()[col] == 255 and drawn(), f"{r.get('error')} {wire()}")
            e.act("clear_programmer")
            check("...with nothing programmed: its 'On'", 193 <= wire()[col] <= 255 and drawn(), str(wire()))
            e.act("blackout", state=1)
            check("Blackout: 'No beam' (still armed)", wire()[col] <= 63 and not drawn(), str(wire()))
            e.act("blackout", state=0)
            e.act("fx_kill")
            check("killed: 'No beam' again", wire()[col] <= 63 and not drawn(), str(wire()))
            for q in ("Antari X-310 Pro Fazer", "Robe Fog 1500 FT"):
                e.act("patch_clear")
                e.act("add_heads", query=q, qty=1, universe=1, address=1)
                h = e.patch[0]
                check(f"{q}: its volume channel is the fog output", "fog" in h["map"], str(h["map"]))
                at = h["map"].index("fog")
                rest = e.build_frames()[1][at]
                r = e.act("fx_fog", heads=[h["head_no"]], level=80, seconds=5)
                check(f"{q}: the fog button drives it (no ARM needed)",
                      r.get("ok") and e.build_frames()[1][at] > rest, f"{r.get('error')} {rest}")
                e.act("fx_kill")
        finally:
            e.shutdown()



def test_product_fit() -> None:
    """The 3D body is the kind of product the library says it is (a
    scanner, a flower, a pixel bar, a strobe); a moving fogger is never an
    always-lit lamp; MagicFX Stadium blasters are confetti and a hazer
    called "Dragon" is not a flame machine."""
    print("product fit: 3D body from the library type, effects machines")
    from app import engine as eng, fixlib, fixture_kind

    def body(src, key):
        it = fixlib.apply_fx(fixlib.load(src, key)[0])
        m = it["modes"][0]
        roles = [d.get("role") or "raw" for d in m.get("detail") or []]
        return it, fixture_kind.describe({"manufacturer": it["manufacturer"], "model": it["model"],
                                          "mode": m["name"], "map": roles, "channels": len(roles)})
    for key, want in (("SGM/SGM-Victory-250.qxf", "scanner"), ("Martin/Martin-Destroyer.qxf", "effect"),
                      ("Clay_Paky/Clay-Paky-Stormy-CC.qxf", "strobe")):
        _it, b = body("qlc", key)
        check(f"{key.split('/')[1][:-4]} is drawn as: {want}", b["type"] == want, b["type"])
    it, b = body("qlc", "MagicFX/MagicFX-StadiumBlaster.qxf")
    check("MagicFX StadiumBlaster is confetti (needs ARM)", it["fx_kind"] == "confetti" and b["type"] == "confetti",
          f"{it['fx_kind']} {b['type']}")
    it, _b = body("qlc", "MagicFX/MagicFX-SwirlFan-II.qxf")
    fire = next(d for d in it["modes"][0]["detail"] if d["role"] == "fx_fire")
    check("MagicFX SwirlFan II is confetti, and fire sends 'Confetti output' (not 'No output')",
          it["fx_kind"] == "confetti" and fire["on_value"] >= 10, f"{it['fx_kind']} {fire.get('on_value')}")
    it, b = body("qlc", "Showtec/Showtec-Dragon-F-350.qxf")
    check("Showtec Dragon F-350 is a hazer, not a flame machine", it["fx_kind"] == "haze" and b["type"] == "atmos",
          f"{it['fx_kind']} {b['type']}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        k = "American_DJ/American-DJ-Accu-Fog-1000.qxf"
        fixtures.store_parsed(db, fixlib.load("qlc", k), "qlc:" + k)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Accu Fog 1000", qty=1)
            check("a moving fogger (pan, tilt, no dimmer) is not drawn as a lit lamp",
                  e._looks()[0]["a"] == 0 and not e._lamp_only(e.patch[0]), str(e._looks()[0]))
            e.act("blackout", state=1)
            check("...nor in Blackout", e._looks()[0]["a"] == 0, "")
        finally:
            e.shutdown()
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("the colour picker is for lights that mix any colour (all of RGB or CMY)",
          '["red", "green", "blue"].every((r) => lightRoles.has(r))' in pj, "")
    check("a light that can't mix gets its own colours as buttons", "const MAKES = [" in pj and "These lights make" in pj, "")
    mj = (ROOT / "web" / "js" / "stage" / "models.js").read_text(encoding="utf-8")
    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("a pixel bar's cells are each coloured in 3D", "sk.pixels = inst" in mj and "if (sk.pixels)" in sj, "")



def test_ai_fixture_library() -> None:
    """The copilot searches the WHOLE fixture library (typos fine), adds a
    clear match, asks "which?" when several models fit, and never adds a near
    model when the one named isn't in the library."""
    print("AI: find and add fixtures from the whole library")
    from app import assistant, engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            r = assistant.find_fixtures(e, "intimdator scan 360")
            check("a typo still finds the one light (Intimidator Scan 360)",
                  r["exact"] == 1 and r["matches"][0]["name"].endswith("Intimidator Scan 360"), str(r)[:200])
            r = assistant.find_fixtures(e, "chauvet spot")
            check("several models fit: the AI gets a list to ask from",
                  r["exact"] >= 3 and any("Rogue R2 Spot" in m["name"] for m in r["matches"]), str(r)[:200])
            r = assistant.find_fixtures(e, "intimidator spot 360")
            check("a model not in the library: no exact match, flagged so the AI says so",
                  r["exact"] == 0 and "NO EXACT MATCH" in r["note"], str(r)[:200])
            check("nothing at all: says so", not assistant.find_fixtures(e, "zzqx nothing")["matches"], "")
            # a whole turn with a scripted AI: find, add, then reply
            scan = assistant.find_fixtures(e, "intimidator scan 360")["matches"][0]
            script = iter([
                {"tool_calls": [{"id": "1", "function": {"name": "find_fixtures",
                                                         "arguments": json.dumps({"query": "intimidator scan 360"})}}]},
                {"tool_calls": [{"id": "2", "function": {"name": "add_fixture",
                                                         "arguments": json.dumps({"src": scan["src"], "key": scan["key"], "qty": 2})}}]},
                {"content": "Added 2 Intimidator Scan 360."},
            ])
            out = assistant.run_turn(e, "add two chauvet intimidator scan 360", chat=lambda msgs, tools=None: next(script),
                                     preview=False)
            models = [h["model"] for h in e.patch]
            check("a turn: the AI searches, installs and patches the light",
                  models.count("Intimidator Scan 360") == 2 and "Added" in (out.get("reply") or ""), f"{models} {out}")
            # several fit: it asks with the model names
            script2 = iter([{"tool_calls": [{"id": "1", "function": {"name": "ask", "arguments": json.dumps(
                {"question": "Which Chauvet spot?", "options": ["Rogue R1 Spot", "Rogue R2 Spot"]})}}]}])
            out = assistant.run_turn(e, "add a chauvet spot", chat=lambda msgs, tools=None: next(script2), preview=False)
            check("several fit: the question and its tap answers reach the screen",
                  out.get("question") == "Which Chauvet spot?" and out.get("options") == ["Rogue R1 Spot", "Rogue R2 Spot"],
                  str(out)[:200])
            check("the AI is told: search the library first, ask when several fit, never a near model",
                  "find_fixtures" in assistant.SYSTEM and "never add a near model" in assistant.SYSTEM, "")
        finally:
            e.shutdown()


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


def test_shape_in_zone() -> None:
    """A movement shape kept on a zone: circle, sweep, bounce, figure 8 and
    fan drawn on the dance floor - every beam lands on it, from a truss and
    from the floor; size S / M / L covers less or more of it; a cue keeps
    the zone; a shape of your own takes one too; a deleted zone holds."""
    print("Movement shapes kept on a zone")
    import time as _time

    import math

    from app import engine as eng
    from app import fixture_kind
    from app.engine_roam import _inside

    def floor_hit(e, h, v):
        """Where the beam meets the floor, worked FORWARD from the DMX the
        desk sends (the inverse of the aim solver), or None if it points up."""
        fp = v["pan"] / (65535 if "pan_fine" in h["map"] else 255)
        ft = v["tilt"] / (65535 if "tilt_fine" in h["map"] else 255)
        rg = e.head_ranges(h)
        pr, tr = rg.get("pan") or {}, rg.get("tilt") or {}
        pmin, pmax = (pr["min"], pr["max"]) if pr.get("unit") == "degree" and pr.get("min") is not None else (-270.0, 270.0)
        tmin, tmax = (tr["min"], tr["max"]) if tr.get("unit") == "degree" and tr.get("min") is not None else (-135.0, 135.0)
        p, t = math.radians(pmin + fp * (pmax - pmin)), math.radians(tmin + ft * (tmax - tmin))
        lx, ly, lz = math.sin(t) * math.sin(p), math.cos(t), math.sin(t) * math.cos(p)
        hung = h.get("stance") == "hang" if h.get("stance") else h.get("kind") == "truss"
        dx, dy, dz = (-lx, -ly, lz) if hung else (lx, ly, lz)
        d = fixture_kind.describe(h)
        pivot = 0.372 if d.get("heads") else e._AIM_PIVOT.get(d["type"], 0.4)
        oy = h["y"] + (-pivot if hung else pivot)
        if dy >= -1e-6:
            return None
        k = -oy / dy
        return h["x"] + dx * k, h["z"] + dz * k

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            tr = [r for r in e.venue["rigging"] if r["kind"] == "truss"]
            a = e.act("add_heads", query="Moving Head", qty=2)["heads"]
            e.act("attach_heads", heads=a, rig=tr[1]["id"], stance="hang")
            b = e.act("add_heads", query="Moving Head", qty=1)["heads"]
            # on a stand: a light on the floor aims nearly flat at a far floor,
            # where one DMX step moves the beam a metre
            e.act("set_place", head=b[0], x=-5, y=2.5, z=6)
            heads = a + b
            by = {h["head_no"]: h for h in e.patch}
            floor = next(z for z in e.venue["zones"] if z["kind"] == "dancefloor")
            # grown a hair: DMX rounding (8-bit pan / tilt) moves a beam a few cm
            xs, zs = [p[0] for p in floor["points"]], [p[1] for p in floor["points"]]
            box = (min(xs) - 0.25, max(xs) + 0.25, min(zs) - 0.25, max(zs) + 0.25)

            def lands(kind, size=40, frames=10):
                e.act("stop_fx")
                r = e.act("run_fx", name=kind, heads=heads, params={"zone": "dancefloor", "size": size, "speed": 0.2})
                check(f"{kind} on the dance floor starts", r.get("ok"), str(r))
                t0 = _time.monotonic() + 0.2
                out, seen = 0, {}
                for k in range(frames):
                    vals = e._fx_values(t0 + k * 0.7)
                    for n in heads:
                        hit = floor_hit(e, by[n], vals[n])
                        if hit is None or not (box[0] <= hit[0] <= box[1] and box[2] <= hit[1] <= box[3]):
                            out += 1
                            continue
                        seen.setdefault(n, set()).add((round(hit[0], 1), round(hit[1], 1)))
                return out, seen

            for kind in ("circle", "figure_eight", "pan_sweep", "tilt_bounce", "fan_pan"):
                out, seen = lands(kind)
                check(f"{kind}: every beam lands on the dance floor", out == 0, f"{out} off it")
                check(f"{kind}: the beams move", all(len(v) > 2 for v in seen.values()), str({n: len(v) for n, v in seen.items()}))

            def spread(size):
                _, seen = lands("circle", size, 16)
                return max(max(x for x, _ in v) - min(x for x, _ in v) for v in seen.values())
            check("size L covers more of the floor than S", spread(40) > spread(10) * 2, f"{spread(10)} vs {spread(40)}")

            row = next(f for f in e.fx if f.get("lib") == "circle")
            check("the effect carries the zone", row["params"].get("zone") == floor["id"], str(row["params"]))
            e.act("select_heads", heads=heads)
            e.act("record_cue", playback=1, name="Floor circle")
            e.act("stop_fx")
            e.act("cue_go", playback=1, cue=1)
            row = next((f for f in e.fx if f.get("lib") == "circle"), None)
            check("a cue plays it on the zone again", row is not None and row["params"].get("zone") == floor["id"],
                  str(row and row["params"]))

            check("an unknown zone says what there is",
                  "Dance floor" in (e.act("run_fx", name="circle", heads=heads, params={"zone": "kitchen"}).get("error") or ""), "")
            e.act("stop_fx")
            sh = e.act("shape_save", shape={"name": "Box", "points": [[-1, -1], [1, -1], [1, 1], [-1, 1]]})
            sid = sh["shapes"][-1]["id"]
            r = e.act("run_shape", id=sid, heads=heads, zone="dancefloor", size=40)
            check("a shape of your own takes a zone", r.get("ok") and next(f for f in e.fx if f.get("lib") == "shape")["params"].get("zone") == floor["id"], str(r))
            vals = e._fx_values(_time.monotonic() + 1)
            hit = floor_hit(e, by[a[0]], vals[a[0]])
            check("and lands on it", hit is not None and (_inside(floor["points"], *hit)
                                                          or (box[0] <= hit[0] <= box[1] and box[2] <= hit[1] <= box[3])), str(hit))
            # the zone deleted while it runs: the lights hold, the desk goes on
            e.venue["zones"] = [z for z in e.venue["zones"] if z["id"] != floor["id"]]
            for f in e.fx:
                f.pop("_zone", None)
            try:
                e._fx_values(_time.monotonic() + 2)
                ok = True
            except Exception as ex:          # noqa: BLE001
                ok = str(ex)
            check("a deleted zone doesn't stop the desk", ok is True, str(ok))
        finally:
            e.shutdown()


def test_usb_dmx() -> None:
    """DMX out of a USB interface (Enttec DMX USB Pro and compatible): the
    exact message bytes, the engine's output choosing it and sending the
    light's channels through it (a pseudo-terminal stands in for the box),
    a bad port refused, unplugged retried without stopping the desk."""
    print("USB DMX interface (Enttec USB Pro protocol)")
    import os
    import time as _time

    from app import engine as eng
    from app import usbdmx

    f = usbdmx.build_frame(bytes([255, 128] + [0] * 510))
    check("a frame is 7E 06 len(513) 00 + 512 channels + E7",
          f[:4] == bytes([0x7E, 6, 0x01, 0x02]) and f[4] == 0 and f[5:7] == bytes([255, 128])
          and f[-1] == 0xE7 and len(f) == 518, f[:8].hex())
    check("ports look like COM3 or /dev/ttyUSB0", usbdmx.valid_port("COM3") and usbdmx.valid_port("/dev/ttyUSB0")
          and not usbdmx.valid_port("2.0.0.10") and not usbdmx.valid_port("COM3; rm -rf"), "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            check("a bad port is refused", not e.act("set_dmx_target", mode="usb", host="nowhere").get("ok"), "")
            if not hasattr(os, "openpty"):
                return                                   # Windows: no pseudo-terminal to stand in
            master, slave = os.openpty()
            name = os.ttyname(slave)
            r = e.act("set_dmx_target", mode="usb", host=name)
            check("the output can be a USB interface", r.get("ok") and r["target"] == {"mode": "usb", "host": name, "transport": "usbpro"}, str(r))
            heads = e.act("add_heads", query="Dimmer", qty=1)["heads"] or e.act("add_heads", query="PAR", qty=1)["heads"]
            e.act("select_heads", heads=heads)
            e.act("set_intensity", level=100)
            sender = e._get_sender()
            check("the engine sends through the USB sender", type(sender).__name__ == "UsbProSender", type(sender).__name__)
            sender.dry_run = False
            frames = e.build_frames()
            e._dispatch(frames)
            os.set_blocking(master, False)
            _time.sleep(0.1)
            got = b""
            try:
                while True:
                    got += os.read(master, 4096)
            except (BlockingIOError, OSError):
                pass
            want = usbdmx.build_frame(frames[1])
            check("the box gets the frame byte for byte (the light's channels at full)",
                  got == want and max(frames[1]) == 255, f"{len(got)} bytes")
            os.close(slave)
            os.close(master)
            sender.close()
            e.dmx_target["host"] = "/dev/does-not-exist"
            e._sender = None
            s2 = e._get_sender()
            s2.dry_run = False
            e._dispatch(e.build_frames())
            check("unplugged: an error to show, the desk goes on, it tries again later",
                  s2.errors == 1 and s2.last_error and e.output["errors"] >= 1, str(s2.stats()))
            info = e.network_info()
            check("Settings -> Output lists the USB ports and says this one isn't there",
                  "usb_ports" in info and info["check"] and not info["check"]["ok"], str(info.get("check")))
        finally:
            e.shutdown()


def test_3d_detail() -> None:
    """The 3D's detail (A9 step 2): gobos soften out of focus and with
    frost, a prism splits the pool and the gobo into three, gobo shafts in
    the haze, shiny floors mirror the lenses (per floor type), quality named
    High / Medium / Low with Low leaving the detail out; a light whose file
    names gobo slots but has no pictures gets a drawn gobo per slot."""
    print("3D detail: focus, frost, prism, gobo shafts, reflections")
    from app import engine as eng
    from app import fixlib

    st = ROOT / "web" / "js" / "stage"
    mj = (st / "materials.js").read_text(encoding="utf-8")
    sj = (st / "stage.js").read_text(encoding="utf-8")
    vj = (st / "venue.js").read_text(encoding="utf-8")
    dj = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("each light carries blur (focus, frost) and prism to the shaders",
          "uLook" in mj and "LIGHTS.uLook.value[i].set(l.blur" in sj and "L.beam.focus" in sj and "L.beam.prism" in sj, "")
    check("gobos soften, a prism makes its copies (one look at the picture a pixel)",
          "const goboMask = (id, uv0, look, detail)" in mj and "mix(goboShape(id, uv), 0.55" in mj and "float(6.2831853).div(n)" in mj, "")
    check("gobo shafts in the haze: the beam shader shows the same picture",
          "a.mulAssign(goboMask(u.uGobo, uv, vec4(u.uLook.xyz, 0), detail).mul(0.95).add(0.2))" in mj and "u.uGobo.value = goboId" in sj, "")
    check("shiny floors mirror the lenses, by floor type",
          "reflect(normalize(wp.sub(cameraPosition)), N)" in mj and "FLOOR_SHEEN" in vj and "black: 1" in vj and "grass: 0" in vj, "")
    check("Low leaves the detail out (the shaders are built without it), Medium and High draw it",
          "if (!detail) return goboShape(id, uv0)" in mj and "m.userData.detail(DETAIL.on)" in mj
          and 'setDetail(this.scene, this.options.quality !== "fast")' in sj, "")
    stage_dir = ROOT / "web" / "js" / "stage"
    check("the 3D runs on three.js's WebGPU renderer, no WebGL renderer or GLSL left",
          "new THREE.WebGPURenderer(rendererOpts(" in sj and "new THREE.RenderPipeline(renderer)" in sj
          and not any("WebGLRenderer" in f.read_text(encoding="utf-8") or "ShaderMaterial(" in f.read_text(encoding="utf-8")
                      for f in stage_dir.glob("*.js")), "")
    check("a browser whose WebGPU can't run it (or has none) gets the same renderer on WebGL 2",
          'tex.createView({ swizzle: "rgba" })' in sj and "forceWebGL: true" in sj and "export const GPU_DEVICE = await probeWebGPU()" in sj, "")
    check("fog, CO2, flame and confetti are sprites (WebGPU points are one pixel)",
          "new THREE.Sprite(m)" in (stage_dir / "sfx.js").read_text(encoding="utf-8"), "")
    check("quality is named High / Medium / Low",
          "High: sharpest" in dj and "Medium: adapts" in dj and "Low: older laptops" in dj, "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            done = fixtures.store_parsed(db, fixlib.load("qlc", "Robe/Robe-Pointe.qxf"), "qlc:Robe/Robe-Pointe.qxf")
            fixtures.invalidate_cache()
            fid = ((done.get("imported") or [{}])[0]).get("fixture_id")
            h = e.act("add_heads", fixture_id=fid, qty=1)["heads"][0]
            rows = e._gobo_images(next(x for x in e.patch if x["head_no"] == h)) or []
            opens = [r for r in rows if r[2] == ""]
            drawn = [r for r in rows if r[2] == "-"]
            check("named gobo slots with no picture: Open stays open, each gobo drawn",
                  opens and opens[0][0] == 0 and len(drawn) >= 7 and drawn[0][0] == 4, str(rows[:4]))
        finally:
            e.shutdown()


_REAL_SPOT = """<?xml version="1.0" encoding="UTF-8"?>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Manufacturer>Selftest</Manufacturer><Model>Real Spot</Model><Type>Moving Head</Type>
 <Channel Name="Pan" Preset="PositionPan"/>
 <Channel Name="Tilt" Preset="PositionTilt"/>
 <Channel Name="Dimmer" Preset="IntensityDimmer"/>
 <Channel Name="Shutter"><Group Byte="0">Shutter</Group>
  <Capability Min="0" Max="7">Closed</Capability><Capability Min="8" Max="15">Open</Capability>
  <Capability Min="16" Max="131">Strobe slow to fast</Capability><Capability Min="132" Max="139">Open</Capability>
  <Capability Min="140" Max="181">Pulse slow to fast</Capability><Capability Min="182" Max="189">Open</Capability>
  <Capability Min="190" Max="255">Random strobe slow to fast</Capability></Channel>
 <Channel Name="Color Wheel"><Group Byte="0">Colour</Group>
  <Capability Min="0" Max="9">Open</Capability><Capability Min="10" Max="19">Red</Capability>
  <Capability Min="20" Max="29">Red + Blue</Capability><Capability Min="30" Max="39">Blue</Capability>
  <Capability Min="40" Max="49">Green</Capability>
  <Capability Min="200" Max="255">Rainbow CW slow to fast</Capability></Channel>
 <Channel Name="Gobo Wheel"><Group Byte="0">Gobo</Group>
  <Capability Min="0" Max="9">Open</Capability><Capability Min="10" Max="19">Gobo 1</Capability>
  <Capability Min="20" Max="29">Gobo 1 shake slow to fast</Capability>
  <Capability Min="200" Max="255">Gobo wheel rotation CW slow to fast</Capability></Channel>
 <Channel Name="Gobo Rotation"><Group Byte="0">Gobo</Group>
  <Capability Min="0" Max="127">Gobo indexing 0° - 360°</Capability>
  <Capability Min="128" Max="255">Gobo rotation CCW slow to fast</Capability></Channel>
 <Channel Name="Prism"><Group Byte="0">Prism</Group>
  <Capability Min="0" Max="127">Prism out</Capability>
  <Capability Min="128" Max="191">8-facet prism into the beam</Capability>
  <Capability Min="192" Max="255">Linear 4-facet prism</Capability></Channel>
 <Channel Name="Prism Rotation"><Group Byte="0">Prism</Group>
  <Capability Min="0" Max="127">Prism indexing 0° - 360°</Capability>
  <Capability Min="128" Max="255">Continuous prism CW rotation slow to fast</Capability></Channel>
 <Mode Name="Std"><Channel Number="0">Pan</Channel><Channel Number="1">Tilt</Channel>
  <Channel Number="2">Dimmer</Channel><Channel Number="3">Shutter</Channel><Channel Number="4">Color Wheel</Channel>
  <Channel Number="5">Gobo Wheel</Channel><Channel Number="6">Gobo Rotation</Channel>
  <Channel Number="7">Prism</Channel><Channel Number="8">Prism Rotation</Channel></Mode>
</FixtureDefinition>"""

_REAL_TRIO = """<?xml version="1.0" encoding="UTF-8"?>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Manufacturer>Selftest</Manufacturer><Model>Trio Bar</Model><Type>Moving Head</Type>
 <Channel Name="Dimmer" Preset="IntensityDimmer"/>
 <Channel Name="Pan" Preset="PositionPan"/>
 <Channel Name="Tilt" Preset="PositionTilt"/>
 <Channel Name="Red" Preset="IntensityRed"/><Channel Name="Green" Preset="IntensityGreen"/>
 <Channel Name="Blue" Preset="IntensityBlue"/>
 <Channel Name="Zoom" Preset="BeamZoomSmallBig"/>
 <Mode Name="Per head">
  <Channel Number="0">Dimmer</Channel>
  <Channel Number="1">Pan</Channel><Channel Number="2">Tilt</Channel><Channel Number="3">Red</Channel>
  <Channel Number="4">Green</Channel><Channel Number="5">Blue</Channel><Channel Number="6">Zoom</Channel>
  <Channel Number="7">Pan</Channel><Channel Number="8">Tilt</Channel><Channel Number="9">Red</Channel>
  <Channel Number="10">Green</Channel><Channel Number="11">Blue</Channel><Channel Number="12">Zoom</Channel>
  <Channel Number="13">Pan</Channel><Channel Number="14">Tilt</Channel><Channel Number="15">Red</Channel>
  <Channel Number="16">Green</Channel><Channel Number="17">Blue</Channel><Channel Number="18">Zoom</Channel>
 </Mode>
</FixtureDefinition>"""


def test_real_light_look() -> None:
    """The 3D shows what the light is really doing: each channel's range
    read in the fixture file's own words (prism facets and turning, gobo
    shake / turn / scroll, split colours, a turning colour wheel, pulse
    and random strobes), also on a light's extra (Aux) channels, and each
    head of a multi-head light its own pan, tilt and colour."""
    print("Real-light 3D: the file's words, every head on its own")
    from app import beamlook, fixlib
    from app import engine as eng

    rot = [[0, 127, "Prism indexing 0° - 360°"], [128, 255, "Continuous prism CW rotation slow to fast"]]
    ins = [[0, 127, "Prism out"], [128, 255, "4-facet prism into the light beam"]]
    R = {"prism": {"caps_each": [ins, rot]}}
    check("a prism rotation channel is not read as a prism going in",
          beamlook.describe(R, {"prism": 0, "prism@2": 200}) == {"prot": {"spin": round(0.05 + 1.45 * 72 / 127, 3)}},
          str(beamlook.describe(R, {"prism": 0, "prism@2": 200})))
    check("words with no meaning add nothing", beamlook.describe({"gobo": {"caps": [[0, 255, "Gobo 1"]]}}, {"gobo": 9}) == {}, "")
    check("'LEE 790 - Moroccan pink' is one filter, not a split",
          "split" not in beamlook.describe({"wheel": {"caps": [[0, 255, "LEE 790 - Moroccan pink"]]}}, {"wheel": 9}), "")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        fixtures.store_parsed(db, fixlib.parse_qxf(_REAL_SPOT) + fixlib.parse_qxf(_REAL_TRIO), "selftest")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            spot = e.act("add_heads", query="Real Spot", qty=1)["heads"]
            trio = e.act("add_heads", query="Trio Bar", qty=1)["heads"]
            check("both test lights patch", len(spot) == 1 and len(trio) == 1, f"{spot} {trio}")
            e.act("select_heads", heads=spot)
            e.act("set_intensity", level=100)

            def look(**vals):
                for k, v in vals.items():
                    e.act("set_attribute", attribute=k, value=v, heads=spot)
                row = next(r for r in e._looks() if r["n"] == spot[0])
                return row.get("look") or {}
            lk = look(prism=150, gobo_rot=200)
            check("8-facet prism: the 3D gets 8 facets", lk.get("prism") == 8 and not lk.get("plin"), str(lk))
            check("the gobo turning the other way (CCW)", (lk.get("grot") or {}).get("spin", 0) < 0, str(lk))
            aux = next((r for r in e._head(spot[0])["map"] if r.startswith("aux")), None)
            lk = look(**{aux: 200}) if aux else {}
            check("the prism turning, from its rotation channel (stored as an Aux channel)",
                  (lk.get("prot") or {}).get("spin", 0) > 0, f"{aux} {lk}")
            lk = look(prism=220, gobo_rot=64)
            check("linear prism", lk.get("prism") == 4 and lk.get("plin") is True, str(lk))
            check("gobo indexed to about 180 degrees", abs((lk.get("grot") or {}).get("at", -1) - 181.4) < 2, str(lk))
            lk = look(prism=0, gobo=25, wheel=25, shutter=160)
            check("prism out: no prism", "prism" not in lk, str(lk))
            check("gobo 1 shaking", lk.get("gshake", 0) > 0, str(lk))
            check("half red, half blue", len(lk.get("split") or []) == 2, str(lk))
            check("a pulse, at a speed", lk.get("smode") == "pulse" and lk.get("shz", 0) > 0, str(lk))
            lk = look(gobo=230, wheel=230, shutter=230)
            check("the gobo wheel scrolling", lk.get("gscroll", 0) > 0, str(lk))
            check("the colour wheel turning through its colours",
                  (lk.get("cscroll") or {}).get("v", 0) > 0 and len(lk["cscroll"]["cols"]) >= 2, str(lk))
            check("a random strobe", lk.get("smode") == "random", str(lk))

            e.act("select_heads", heads=trio)
            e.act("set_intensity", level=100)
            for k, (pan, tilt) in enumerate([(10, 20), (128, 128), (250, 240)], 1):
                e.act("set_attribute", attribute="pan", value=pan, cell=k, heads=trio)
                e.act("set_attribute", attribute="tilt", value=tilt, cell=k, heads=trio)
            row = next(r for r in e._looks() if r["n"] == trio[0])
            cells = row.get("cells") or []
            check("three heads, each its own", len(cells) == 3, str(row))
            if len(cells) == 3:
                check("each head its own pan", [round(c.get("pan", -1), 2) for c in cells] == [round(10 / 255, 2), round(128 / 255, 2), round(250 / 255, 2)], str(cells))
                check("each head its own tilt", cells[0].get("tilt", 0) < cells[2].get("tilt", 0), str(cells))
            e.act("select_heads", heads=spot)
            row = next(r for r in e._looks() if r["n"] == spot[0])
            check("a one-head light sends no per-head list", "cells" not in row, str(row.get("cells")))
        finally:
            e.shutdown()

    sj = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    mj = (ROOT / "web" / "js" / "stage" / "materials.js").read_text(encoding="utf-8")
    mo = (ROOT / "web" / "js" / "stage" / "models.js").read_text(encoding="utf-8")
    check("the 3D reads the look: facets, turns, shake, scroll, split, strobe kinds",
          all(k in sj for k in ("lk.prism", "lk.prot", "lk.grot", "lk.gshake", "lk.gscroll", "lk.cscroll", "lk.split", "lk.smode")), "")
    check("prisms of any facet count, linear prisms, and half beams in the shader",
          "float(6.2831853).div(n)" in mj and "a linear prism" in mj and "look.w.greaterThan(0.5)" in mj and "uColor2" in mj, "")
    check("each head of a multi-head model can pan", "sk.cells.push({ pan: p, tilt: t, lens })" in mo and "c.pan.rotation.y" in sj, "")


def test_real_bodies() -> None:
    """The makers' 3D bodies from GDTF Share, for the 3D only: the same
    model by the same maker (spelled the Share's way), kept only when its
    file has a 3D model, and the light's own profile left alone."""
    print("Real 3D bodies from GDTF Share (the profile stays)")
    import io
    import json as _json
    import zipfile

    from app import gdtfshare as gs
    from tools.selftests.common import _share_login_ok

    def gdtf(models: bool) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("description.xml", "<GDTF/>")
            if models:
                z.writestr("models/3ds/Body.3ds", b"3ds\x00" + b"\x00" * 32)
        return buf.getvalue()
    files = {21: gdtf(True), 22: gdtf(False)}
    catalogue = _json.dumps({"result": True, "list": [
        {"rid": 20, "fixture": "Sharpy Plus Wash", "manufacturer": "Clay Paky", "revision": "r1", "modes": []},
        {"rid": 21, "fixture": "Sharpy Plus", "manufacturer": "Clay Paky", "revision": "r1", "modes": []},
        {"rid": 22, "fixture": "Rogue R2 Wash", "manufacturer": "Chauvet Professional", "revision": "r1", "modes": []},
    ]}).encode("utf-8")

    def transport(method, url, *, body=None, headers=None, timeout=20.0):
        if "login.php" in url:
            return _share_login_ok()
        if "getList.php" in url:
            return (200, {"content-type": "application/json"}, catalogue)
        rid = int(url.rsplit("=", 1)[-1])
        return (200, {"content-type": "application/octet-stream"}, files[rid])
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        c = gs.GdtfShare(db, tmp / "cache", user="me", password="pw", transport=transport)
        before = fixtures.model_sources(db, "Claypaky", "Sharpy Plus", loose=True)
        r = c.fetch_body("Claypaky", "Sharpy Plus")
        check("the same model by the same maker, spelled the Share's way (not 'Sharpy Plus Wash')",
              r.get("ok") and r.get("file") == "body-rev21.gdtf", str(r))
        check("its file is kept for the 3D", c.body_file("Claypaky", "Sharpy Plus") == "body-rev21.gdtf"
              and (tmp / "cache" / "body-rev21.gdtf").is_file(), "")
        check("the light's own profile is left alone (nothing installed or replaced)",
              fixtures.model_sources(db, "Claypaky", "Sharpy Plus", loose=True) == before, "")
        r = c.fetch_body("Chauvet", "Rogue R2 Wash")
        check("a Share file with no 3D model is not used", not r.get("ok") and "no 3D model" in r.get("reason", "")
              and c.body_file("Chauvet", "Rogue R2 Wash") is None, str(r))
        r = c.fetch_body("Acme", "Nothing 9000")
        check("a light that isn't on the Share says so", not r.get("ok") and "not on GDTF Share" in r.get("reason", ""), str(r))
        anon = gs.GdtfShare(db, tmp / "cache2", transport=transport)
        try:
            anon.fetch_body("Claypaky", "Sharpy Plus")
            refused = False
        except gs.GdtfShareError as exc:
            refused = exc.code == "no_session"
        check("signed out: it asks for a sign-in, it doesn't half-work", refused, "")
    mj = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    sp = (ROOT / "web" / "app" / "stagepanel.js").read_text(encoding="utf-8")
    check("the 3D model list uses a fetched body when the profile has none",
          'bodies.body_file(x.get("manufacturer") or "", x.get("model") or "")' in mj and '"/api/gdtf/bodies"' in mj, "")
    check("View -> The makers' 3D bodies fetches them and reloads the models",
          "The makers' 3D bodies" in sp and 'post("/api/gdtf/bodies"' in sp and 'modelSig = "";' in sp, "")


def test_lookcheck() -> None:
    """Every range of every channel of the test rig's lights: the 3D shows
    what the fixture file's words say (tools/lookcheck.py, no misses), and
    a QLC+ strobe range is known by its tag even when misspelled."""
    print("Every model, every range: the 3D shows what the file says")
    import contextlib
    import io

    from app import fixlib
    from tools import lookcheck
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = lookcheck.main([])
    text = out.getvalue()
    check("no range of the test rig's lights is MISSED in the 3D", code == 0 and "    MISSED" not in text,
          "\n".join(x for x in text.splitlines() if "MISSED" in x)[:600])
    check("the check really ran over the rig", "Sharpy Plus" in text and "MAC Aura" in text, text[-200:])
    sharpy = fixlib.load("qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf")[0]
    rng = next(d for d in sharpy["modes"][0]["detail"] if d.get("role") == "strobe").get("strobe_ranges") or []
    check("a Sharpy's 'Stobe (slow to fast)' and 'Pulsation' are strobe ranges (it strobes in 3D too)",
          [4, 103] in rng and [108, 207] in rng, str(rng))


def test_bug_report() -> None:
    """Report a problem from the desk (A11): the bug bundle carries the
    light's file, its DMX, what the 3D is told, a picture, the version and
    recent errors, and (ticked) the show; no secret survives in it; the
    GitHub link opens the right form, filled in."""
    print("Report a problem: the bug bundle and the GitHub link")
    import urllib.parse
    import zipfile

    from app import bugreport, fixlib
    from app import config as cfg
    from app import engine as eng
    key = "sk-selftest-" + "x" * 30
    saved = (cfg.DATA, cfg.LLM_API_KEY)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg.DATA, cfg.LLM_API_KEY = tmp / "data", key
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        done = fixtures.store_parsed(db, fixlib.load("qlc", "Clay_Paky/Clay-Paky-Sharpy-Plus.qxf"),
                                     "qlc:Clay_Paky/Clay-Paky-Sharpy-Plus.qxf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            h = e.act("add_heads", fixture_id=done["imported"][0]["fixture_id"], qty=1)["heads"][0]
            e.act("rename_head", head=h, name="Sharpy " + key)        # a key typed somewhere it shouldn't be
            e.act("select_heads", heads=[h])
            e.act("set_intensity", level=100)
            bugreport.note_error("/api/console", "boom " + key)
            pre = bugreport.report(e, h, "stays dark", ["dmx", "3d"], True, b"", ["page went wrong"], preview=True)
            names = [f["name"] for f in pre["files"]]
            check("the report carries the light's own file, how the desk read it, its DMX and what the 3D is told",
                  all(n in names for n in ("light/Clay-Paky-Sharpy-Plus.qxf", "light/fixture-as-the-desk-read-it.json",
                                           "light/dmx-channels.json", "light/what-the-3d-is-told.json",
                                           "report.json", "recent-errors.txt", "show.json")), str(names))
            check("a preview saves nothing", not (tmp / "data" / "bug_reports").exists(), "")
            check("never a .env", not any(".env" in n for n in names), "")
            r = bugreport.report(e, h, "Full and it stays dark\nsteps...", ["dmx", "3d"], True, b"\x89PNG fake", ["page went wrong"])
            z = tmp / "data" / "bug_reports" / r["zip"]
            check("saved as one zip in the desk's data folder", z.is_file() and r["zip"].endswith(".zip"), r.get("zip", ""))
            with zipfile.ZipFile(z) as zf:
                blobs = {n: zf.read(n) for n in zf.namelist()}
            check("no secret survives in any file (the AI key was in a light's name and an error)",
                  not any(key.encode() in b for b in blobs.values()) and b"[removed]" in blobs["show.json"], "")
            check("the 3D picture and both kinds of error are in it",
                  blobs.get("3d-view.png") == b"\x89PNG fake" and b"page went wrong" in blobs["recent-errors.txt"]
                  and b"/api/console" in blobs["recent-errors.txt"], "")
            q = urllib.parse.parse_qs(urllib.parse.urlparse(r["url"]).query)
            check("the GitHub link opens 'Problem with a light', filled in",
                  q.get("template") == ["light-bug.yml"] and "brand:Clay Paky" in q["labels"][0]
                  and q["light"][0].startswith("Clay Paky Sharpy Plus") and "The 3D view" in q["area"][0]
                  and q["what"][0].startswith("Full and it stays dark") and r["zip"] in q["files"][0], str(q))
            check("the link stays short enough for a browser", len(r["url"]) < 8000, str(len(r["url"])))
            r2 = bugreport.report(e, None, "the playbacks froze", [], False, b"", [])
            q2 = urllib.parse.parse_qs(urllib.parse.urlparse(r2["url"]).query)
            check("anything else: 'Something else is wrong', and no show when unticked",
                  q2.get("template") == ["bug.yml"] and not any(f["name"] == "show.json" for f in r2["files"]), str(q2))
            check("a saved report comes back by its name, nothing else does",
                  bugreport.read_saved(r["zip"]) == z.read_bytes() and bugreport.read_saved("../../.env") is None
                  and bugreport.read_saved("jarvis-report-x/../../a.zip") is None, "")
        finally:
            e.shutdown()
            cfg.DATA, cfg.LLM_API_KEY = saved
    fj = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    sp = (ROOT / "web" / "app" / "stagepanel.js").read_text(encoding="utf-8")
    dj = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    bj = (ROOT / "web" / "app" / "bugreport.js").read_text(encoding="utf-8")
    check("right-click a light (list or 3D) -> Report a problem with this light; Help -> Report a bug",
          "Report a problem with this light" in fj and '"contextmenu"' in fj and "onMenu:" in sp
          and "Report a bug" in dj, "")
    check("the reporter sees the list of files before anything is saved", "preview: true" in bj and "report-files" in bj, "")


def test_ai_switch() -> None:
    """The AI switch (A12): Online / Local / Auto - Auto carries on with the
    local AI when the online one is at its limit, and says so; the desk
    starts its own offline AI by itself; the model downloads (pause / resume,
    checked), never while live; an AI pack imports; Remove frees it; the key
    is never shown back and never in a bug report."""
    print("The AI switch and the desk's own offline AI")
    import hashlib
    import json as _json
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app import bugreport, llm, localai
    from app import config as cfg

    class Online(BaseHTTPRequestHandler):
        limited = False

        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if Online.limited:
                body, code = b'{"error":{"message":"Resource has been exhausted (e.g. check quota)."}}', 429
            else:
                body, code = _json.dumps({"choices": [{"message": {"role": "assistant", "content": "online says hi"}}]}).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = HTTPServer(("127.0.0.1", 0), Online)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved = (cfg.DATA, cfg.LLM_API_KEY, cfg.LLM_BASE_URL, os.environ.get("LLAMA_SERVER"), localai.transport)
    msgs = [{"role": "user", "content": "hi"}]
    with tempfile.TemporaryDirectory() as td:
        cfg.DATA = Path(td)
        cfg.LLM_API_KEY = ""
        cfg.LLM_BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}"
        os.environ["LLAMA_SERVER"] = str(ROOT / "tools" / "selftests" / "fake_llama.py")
        try:
            check("no key, no offline AI: the copilot says the AI isn't there", not llm.available(), "")
            key = "AIza" + "k" * 35
            llm.save_settings(mode="online", key=key)
            pub = llm.public()
            check("the key is saved in the desk and never shown back", pub["has_key"] and key not in _json.dumps(pub), str(pub))
            check("Online: the online AI answers", llm.chat(msgs)["content"] == "online says hi", "")
            Online.limited = True
            try:
                llm.chat(msgs)
                failed = False
            except llm.LLMError as exc:
                failed = "limit" in str(exc)
            check("Online at its limit: says so (no offline AI on Online)", failed, "")
            # the desk's own offline AI: an AI pack, then Auto
            (cfg.DATA / "ai").mkdir(parents=True, exist_ok=True)
            bad = Path(td) / "notes.gguf"
            bad.write_bytes(b"hello")
            pack = Path(td) / "tiny-pack.gguf"
            pack.write_bytes(b"GGUF" + b"\0" * 64)
            try:
                localai.import_pack(str(bad))
                refused = False
            except ValueError:
                refused = True
            check("an AI pack that isn't a model is refused", refused, "")
            localai.import_pack(str(pack))
            check("an AI pack (.gguf from a USB stick) is copied in", [m["file"] for m in localai.models()] == ["tiny-pack.gguf"], "")
            check("with a model and the engine, the offline AI is ready", localai.ready(), str(localai.runtime()))
            llm.save_settings(mode="auto")
            msg = llm.chat(msgs)
            check("Auto at Gemini's limit: the desk starts its own AI and carries on",
                  msg["content"] == "local says hi from tiny-pack" and llm.last_used == "local", str(msg))
            check("...and says so", llm.last_note == "Gemini limit reached - using the offline AI", llm.last_note)
            Online.limited = False
            check("Auto with Gemini back: Gemini again, no note",
                  llm.chat(msgs)["content"] == "online says hi" and not llm.last_note, "")
            llm.save_settings(mode="local")
            check("Local: always the offline AI", llm.chat(msgs)["content"].startswith("local says hi"), "")
            check("the switch's status: running, its model", llm.public()["local_running"]
                  and llm.public()["local_models"] == ["tiny-pack"], str(llm.public()))
            localai.stop()
            check("Stop frees it", localai.url() is None, "")
            for bad_args in ({"mode": "sometimes"}, {"local_url": "javascript:alert(1)"}):
                try:
                    llm.save_settings(**bad_args)
                    ok = False
                except ValueError:
                    ok = True
                check(f"a bad setting is refused: {list(bad_args)[0]}", ok, "")
            check("the desk's key never ends up in a bug report", key not in bugreport.scrub("my key " + key), "")
            localai.remove("tiny-pack.gguf")
            check("Remove gives the space back", localai.models() == [], "")
            # the download: from the model's own listing, resumable, checked
            blob = b"GGUF" + bytes(range(256)) * 40
            sha = hashlib.sha256(blob).hexdigest()
            listing = _json.dumps([{"path": "Qwen3-8B-Q4_K_M.gguf", "size": len(blob), "lfs": {"oid": sha, "size": len(blob)}},
                                   {"path": "Qwen3-8B-Q8_0.gguf", "size": 9}]).encode()
            state = {"pause_after": 3, "sent": 0, "damage": False}

            def fake(url, headers):
                if "/api/models/" in url:
                    return 200, {}, iter([listing])
                start = int(headers.get("Range", "bytes=0-")[6:].rstrip("-") or 0)
                data = blob[start:]
                if state["damage"]:
                    data = data[:-1] + b"X"

                def chunks():
                    for i in range(0, len(data), 1000):
                        state["sent"] += 1
                        if state["sent"] == state["pause_after"]:
                            localai.pause()
                        yield data[i:i + 1000]
                return (206 if start else 200), {}, chunks()
            localai.transport = fake
            try:
                localai.download("qwen3-8b", live=True)
                refused = False
            except ValueError:
                refused = True
            check("no download while the output is live", refused, "")
            localai.download("qwen3-8b", background=False)
            st = localai.status()["download"]
            check("Pause stops it part-way", st.get("done", 0) > 0 and not st.get("finished")
                  and (cfg.DATA / "ai" / "Qwen3-8B-Q4_K_M.gguf.part").is_file(), str(st))
            state["pause_after"] = -1
            localai.download("qwen3-8b", background=False)
            got = cfg.DATA / "ai" / "Qwen3-8B-Q4_K_M.gguf"
            check("Resume carries on from where it stopped, and the file is checked",
                  got.is_file() and got.read_bytes() == blob and localai.status()["download"].get("finished"), str(localai.status()["download"]))
            got.unlink()
            state["damage"] = True
            localai.download("qwen3-8b", background=False)
            check("a damaged download is thrown away, with a plain reason",
                  not got.exists() and "damaged" in localai.status()["download"].get("error", ""), str(localai.status()["download"]))
            sug = localai.suggest()
            check("the desk suggests a model from the computer", sug["id"] in ("qwen3-8b", "qwen3-14b") and "ram_gb" in sug, str(sug))
        finally:
            localai.stop()
            srv.shutdown()
            cfg.DATA, cfg.LLM_API_KEY, cfg.LLM_BASE_URL, env, localai.transport = saved
            if env is None:
                os.environ.pop("LLAMA_SERVER", None)
            else:
                os.environ["LLAMA_SERVER"] = env
            llm._local_seen["t"] = 0.0
    js = (ROOT / "web" / "app" / "aisettings.js").read_text(encoding="utf-8")
    cj = (ROOT / "web" / "app" / "copilot.js").read_text(encoding="utf-8")
    wf = (ROOT / ".github" / "workflows" / "desktop.yml").read_text(encoding="utf-8")
    check("Settings -> AI: the switch, the key, download / pause / AI pack / remove",
          all(k in js for k in ('"/api/ai"', '"/api/ai/local"', "Pause", "AI pack", "Remove")), "")
    check("the copilot's header switches the AI and shows the fallback note", "setAiMode" in cj and "r.ai_note" in cj, "")
    check("the desktop app ships the offline AI's engine (llama.cpp's server)",
          "ggml-org/llama.cpp" in wf and "CONSOLE_LLAMA_DIR" in (ROOT / "desktop" / "main.js").read_text(encoding="utf-8"), "")


def test_ai_engine_fetch() -> None:
    """An app installed before the engine shipped with it (or run.bat): the
    offline AI's download fetches llama.cpp's server first, from llama.cpp's
    own releases, the build for this computer; an unsafe archive is refused."""
    print("The offline AI's engine, fetched when the app has none")
    import hashlib
    import io
    import json as _json
    import os
    import zipfile

    from app import config as cfg
    from app import localai
    pat = localai.engine_pattern()
    if not pat:
        check("(no engine for this kind of computer: Ollama instead)", True, "")
        return
    name = {"nt": "llama-b9-bin-win-vulkan-x64.zip"}.get(os.name) or \
        ("llama-b9-bin-macos-arm64.zip" if "arm64" in pat else "llama-b9-bin-macos-x64.zip" if "macos" in pat
         else "llama-b9-bin-ubuntu-x64.zip")
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"

    def zipped(files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, b in files.items():
                z.writestr(n, b)
        return buf.getvalue()
    good = zipped({f"build/bin/{exe}": b"engine", "build/bin/ggml.dll": b"x"})
    evil = zipped({f"../../{exe}": b"evil"})
    blob = b"GGUF" + b"m" * 500
    listing = _json.dumps([{"path": "Qwen3-8B-Q4_K_M.gguf", "lfs": {"oid": hashlib.sha256(blob).hexdigest(), "size": len(blob)}}]).encode()
    served = {"engine": good}

    def fake(url, headers):
        if "api.github.com" in url:
            return 200, {}, iter([_json.dumps([
                {"tag_name": "b10", "assets": [{"name": "llama-b10-xcframework.zip", "size": 1}]},
                {"tag_name": "b9", "assets": [{"name": "cudart-llama-bin-win-cuda-x64.zip", "size": 1},
                                              {"name": name, "size": len(served["engine"]), "browser_download_url": "https://x/" + name}]},
            ]).encode()])
        if url.endswith(name):
            return 200, {}, iter([served["engine"]])
        if "/api/models/" in url:
            return 200, {}, iter([listing])
        return 200, {}, iter([blob])
    saved = (cfg.DATA, os.environ.pop("LLAMA_SERVER", None), os.environ.pop("CONSOLE_LLAMA_DIR", None), localai.transport)
    with tempfile.TemporaryDirectory() as td:
        cfg.DATA = Path(td)
        localai.transport = fake
        try:
            no_engine = localai.runtime() is None or "desktop" in localai.runtime()[0]
            st = localai.status()
            check("no engine yet, but one can be fetched: the download is offered", st["can_fetch_engine"], str(st))
            served["engine"] = evil
            localai.download("qwen3-8b", background=False)
            check("an archive that writes outside its folder is refused",
                  "not safe" in localai.status()["download"].get("error", "")
                  and not (Path(td).parent / exe).exists(), str(localai.status()["download"]))
            served["engine"] = good
            localai.download("qwen3-8b", background=False)
            got = localai.runtime()
            check("the engine comes first (the newest release with this computer's build), then the model",
                  got and got[0].endswith(exe) and str(Path(td)) in got[0]
                  and (Path(td) / "ai" / "Qwen3-8B-Q4_K_M.gguf").read_bytes() == blob, f"{got} {localai.status()['download']}")
            check("(the test ran without an engine already installed)", no_engine, "")
        finally:
            cfg.DATA, llama, llama_dir, localai.transport = saved
            if llama:
                os.environ["LLAMA_SERVER"] = llama
            if llama_dir:
                os.environ["CONSOLE_LLAMA_DIR"] = llama_dir


def test_ai_runs_desk() -> None:
    """Backlog A13: the AI has a hand on every part of the desk - what each
    light can really do (from its file), lights placed in words, the room,
    and the actions that go live, arm, fire, save or delete PREPARED for
    the operator's tap, never done.  The 50-sentence harness's checks are
    run here against a scripted AI (tools/aicheck.py runs them for real)."""
    print("The AI runs the whole desk: honest lights, placing in words, the operator confirms")
    import os

    from app import aitools, assistant
    from app import config as cfg
    from tools import aicheck

    def tool(name, i=0, **a):
        return {"id": f"c{name}{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(a)}}

    def scripted(replies):
        return lambda messages, tools=None, **_: replies.pop(0)

    with tempfile.TemporaryDirectory() as td:
        saved = cfg.DATA
        cfg.DATA = Path(td)
        d = aicheck.Desk(Path(td))
        e = d.e
        try:
            # what each light can do, from its own file
            caps = {m["model"]: m for m in aitools.capabilities(e)["models"]}
            par = next(v for k, v in caps.items() if "Mega PAR" in k)
            sharpy = next(v for k, v in caps.items() if "Sharpy" in k)
            fog = next(v for k, v in caps.items() if "AF-180" in k)
            check("each model's real abilities: the PAR mixes but can't move, the Sharpy moves, the fogger is a machine",
                  "any colour (it mixes)" in par["can"] and any("move" in c for c in par["cannot"])
                  and "moves (pan + tilt)" in sharpy["can"] and "effects machine" in fog["can"][0], json.dumps(caps)[:400])
            got = aitools.cant_do(e, "set_attribute", {"attribute": "tilt", "heads": d.pars})
            check("asking the PARs to tilt names them and why", len(got) == 1 and "6 x" in got[0] and "no tilt" in got[0], str(got))
            check("lights that can do it aren't listed", aitools.cant_do(e, "set_position", {"heads": d.movers}) == [])
            # placing in words, on the club's trusses
            rigs = {w: (aitools.find_rig(e, w) or {}).get("id") for w in ("front truss", "rear", "upstage", "mid truss", "left tower")}
            check("trusses found by name and by where they are", rigs == {"front truss": "r2", "rear": "r4", "upstage": "r1",
                                                                         "mid truss": "r3", "left tower": "r5"}, str(rigs))
            r = aitools.place_lights(e, heads=d.pars[:2], on="mid truss", where="both ends")
            ts = sorted(e._head(n)["mount"]["t"] for n in d.pars[:2])
            check("two lights at both ends of a truss", r["ok"] and e._head(d.pars[0])["mount"]["rig"] == "r3"
                  and ts[0] < 0.1 and ts[1] > 0.9, f"{r} {ts}")
            r = aitools.place_lights(e, heads=d.pars[:2], on="mid truss", height=6)
            check("a truss can't be hung above the room (the desk's own limit, said back)", not r["ok"] and "5.4" in r["error"], str(r))
            check("an unknown place is a question, not a guess",
                  not aitools.place_lights(e, heads=d.pars[:1], on="the moon")["ok"])
            n_rigs = len(e.venue["rigging"])
            r1 = aitools.add_to_room(e, "pole", "Pole L", "left")
            r2 = aitools.add_to_room(e, "vip", "VIP", "front left")
            check("a pole and a zone added to the room in words", r1["ok"] and r2["ok"] and len(e.venue["rigging"]) == n_rigs + 1
                  and any(z["kind"] == "vip" for z in e.venue["zones"]), f"{r1} {r2}")
            # the tiers: never / prepared / done
            check("calibration, locking, the network and undo are not the AI's",
                  not set(aitools.NEVER) & (set(assistant.ALLOWED) | set(assistant.CONFIRMABLE)))
            check("going live, arming, firing, saving and deleting only prepared",
                  {"set_output", "fx_arm", "fx_fire", "save_show", "delete_cue", "group_delete"} <= set(assistant.CONFIRMABLE)
                  and not {"set_output", "fx_fire", "save_show", "delete_cue"} & set(assistant.ALLOWED))
            d.reset()
            waiting: list = []
            r = assistant._do(e, "delete_cue", {"playback": 1, "cue": 2}, waiting)
            check("a delete is held, not done", r["ok"] and d.cue_names() == ["Look 1", "Look 2"] and len(waiting) == 1, str(r))
            check("'No' leaves it", aitools.confirm(e, waiting[0]["id"], False)["ok"] and d.cue_names() == ["Look 1", "Look 2"])
            check("an answered tap can't be used twice", not aitools.confirm(e, waiting[0]["id"])["ok"])
            assistant._do(e, "delete_cue", {"playback": 1, "cue": 2}, waiting)
            r = aitools.confirm(e, waiting[-1]["id"])
            check("'Do it' does it", r["ok"] and d.cue_names() == ["Look 1"], f"{r} {d.cue_names()}")
            r = assistant._do(e, "lock", {}, [])
            check("an action that's never the AI's is refused", not r["ok"], str(r))
            # the harness's own checks, against a scripted AI
            ex = aicheck.CASES[0]
            plays = {
                ex["text"]: [
                    {"content": "", "tool_calls": [tool("find_fixtures", query="chauvet intimidator spot 260")]},
                    {"content": "", "tool_calls": [tool("add_fixture", src="ofl", key="chauvet-dj/intimidator-spot-260.json")]},
                    {"content": "", "tool_calls": [tool("place_lights", heads=[14], on="front truss", where="middle")]},
                    {"content": "", "tool_calls": [
                        tool("do", 1, action="select_heads", params={"head": 14}),
                        tool("do", 2, action="set_colour", params={"colour": "yellow"}),
                        tool("do", 3, action="set_intensity", params={"level": 20}),
                        tool("do", 4, action="run_fx", params={"attribute": "dimmer", "wave": "square", "speed": 2}),
                        tool("do", 5, action="roam", params={"zones": ["Dance floor"], "speed": 0.3})]},
                    {"content": "Added it in the middle of the front truss: yellow, flashing, roaming the dance floor slowly."}],
                "make the pars tilt up": [
                    {"content": "", "tool_calls": [tool("do", 1, action="select_heads", params={"heads": d.pars}),
                                                   tool("do", 2, action="set_attribute", params={"attribute": "tilt", "value": 200})]},
                    {"content": "The PARs can't tilt: they have no pan or tilt."}],
                "delete cue 2": [
                    {"content": "", "tool_calls": [tool("do", action="delete_cue", params={"playback": 1, "cue": 2})]},
                    {"content": "Cue 2 is ready to delete: tap Do it."}],
                "fire the confetti": [
                    {"content": "", "tool_calls": [tool("do", action="fx_fire", params={"head": 12})]},
                    {"content": "Ready: tap Do it to fire."}],
                "what's the weather tomorrow?": [{"content": "I only run the lighting desk."}],
            }
            for text, play in plays.items():
                d.reset()
                case = next(c for c in aicheck.CASES if c["text"] == text)
                r = assistant.run_turn(e, text, session="a13" + text[:8], preview=False, chat=scripted(list(play)))
                ok, why = case["check"](r, d)
                check(f"harness: {text[:60]}", ok, f"{why} {r.get('steps')}")
            d.reset()
            r = assistant.run_turn(e, "make the pars tilt up", session="a13t", preview=False,
                                   chat=scripted(list(plays["make the pars tilt up"])))
            check("the step the operator reads says the PARs couldn't", "no tilt" in r["steps"][-1]["summary"], str(r["steps"]))
            check("a lazy answer fails the harness (it isn't fooled by doing nothing)",
                  not aicheck.CASES[0]["check"]({"ok": True, "reply": "done", "steps": []}, d)[0]
                  and not next(c for c in aicheck.CASES if c["text"] == "all pars red")["check"]({"ok": True}, d)[0])
            check("about 50 sentences", 45 <= len(aicheck.CASES) <= 60, str(len(aicheck.CASES)))
            prompt = assistant._system(e)
            check("its instructions: desk only, each light's abilities, the operator confirms",
                  "DESK ONLY" in prompt and "WHAT EACH LIGHT CAN DO" in prompt and "THE OPERATOR CONFIRMS" in prompt)
        finally:
            e.shutdown()
            cfg.DATA = saved
            os.environ.pop("CONSOLE_AI_DIR", None)


def test_button_looks() -> None:
    """Buttons as customisable as they look: any size (1-4 across, 1-3
    down; the old wide / tall / big still read), shape, fill, text size,
    name only, blinking while on, icon only, 32 icons; a page's own grid
    (buttons across, rows, row height); a look copied onto other buttons.
    All of it saved, undone and kept by the autosave; what a button DOES
    is never touched by its look."""
    print("Button looks: size, shape, fill, text, icons; a page's own grid")
    from app import engine as eng
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            r = e.act("quick_set", page=1, slot=1, button={
                "kind": "colour", "colour": "#ff0000", "label": "Red", "w": 3, "h": 2, "shape": "circle", "fill": "solid",
                "text": "xl", "plain": True, "blink": True, "icon": "party", "icon_only": True})
            b = r.get("button") or {}
            check("a tile 3 across and 2 down, round, solid, big text, name only, blinking, icon only",
                  (b.get("w"), b.get("h"), b.get("shape"), b.get("fill"), b.get("text"), b.get("plain"), b.get("blink"), b.get("icon_only"))
                  == (3, 2, "circle", "solid", "xl", True, True, True) and "size" not in b, str(b))
            b = e.act("quick_set", page=1, slot=9, button={"kind": "blackout", "size": "big"}).get("button") or {}
            check("an old show's 'big' is 2 x 2 (and still says big for older desks)",
                  (b.get("w"), b.get("h"), b.get("size")) == (2, 2, "big"), str(b))
            b = e.act("quick_set", page=1, slot=10, button={"kind": "blackout", "w": 2, "h": 1}).get("button") or {}
            check("2 x 1 is the old 'wide'", b.get("size") == "wide", str(b))
            b = e.act("quick_set", page=1, slot=11, button={"kind": "flash", "shape": "rounded", "fill": "outline", "text": "m"}).get("button") or {}
            check("the defaults aren't stored", not {"shape", "fill", "text", "w", "h", "size"} & set(b), str(b))
            for bad, why in (({"w": 5}, "too wide"), ({"h": 4}, "too tall"), ({"shape": "hexagon"}, "a shape it doesn't have"),
                             ({"fill": "neon"}, "a fill it doesn't have"), ({"text": "huge"}, "a text size it doesn't have"),
                             ({"icon": "unicorn"}, "an icon it doesn't have")):
                check(f"refused: {why}", not e.act("quick_set", page=1, slot=12, button={"kind": "flash", **bad}).get("ok"))
            b = e.act("quick_set", page=1, slot=12, button={"kind": "flash", "icon_only": True}).get("button") or {}
            check("icon only with no icon shows the name", "icon_only" not in b, str(b))
            check("32 icons", len(e.QUICK_ICONS) == 32 and len(set(e.QUICK_ICONS)) == 32)
            # a look copied onto other buttons, what they do untouched
            before = {k: v for k, v in next(x for x in e.quick if x["id"] == "q1-11").items()}
            look = {k: v for k, v in next(x for x in e.quick if x["id"] == "q1-1").items() if k in e.STYLE_KEYS}
            r = e.act("quick_style", ids=["q1-11"], style=look)
            after = next(x for x in e.quick if x["id"] == "q1-11")
            check("paste look: the same shape, fill, size, icon...", r.get("ok") and all(after.get(k) == v for k, v in look.items()), str(after))
            check("...and it still does what it did", after["kind"] == before["kind"] and after["label"] == before["label"]
                  and after["target"] == before["target"] and after["mode"] == before["mode"], str(after))
            r = e.act("quick_style", page=1, style={"shape": "pill"})
            check("paste look on a whole page", r.get("ok") and all(x.get("shape") == "pill" for x in e.quick if x["page"] == 1), r.get("summary"))
            check("a look naming only the shape leaves the rest (size, fill...)",
                  next(x for x in e.quick if x["id"] == "q1-1").get("w") == 3 and next(x for x in e.quick if x["id"] == "q1-1").get("fill") == "solid")
            e.act("quick_style", ids=["q1-11"], style={"shape": None, "fill": None, "w": None, "h": None, "size": None})
            after = next(x for x in e.quick if x["id"] == "q1-11")
            check("a full look (as Paste look sends it) takes off what the copied button doesn't have",
                  not {"shape", "fill", "w", "h", "size"} & set(after), str(after))
            check("paste look needs buttons", not e.act("quick_style", ids=[], style={"shape": "pill"}).get("ok"))
            # a page's own grid
            check("a page has the usual grid until it is changed", e._quick_public()["layout"] == {}
                  and e._quick_public()["layout_default"] == {"cols": 8, "rows": 3, "height": "m"})
            r = e.act("quick_layout", page=2, cols=4, rows=3, height="xl")
            check("4 big buttons across on page 2", r.get("ok") and e._quick_public()["layout"]["2"] == {"cols": 4, "rows": 3, "height": "xl"}, str(r))
            r = e.act("quick_layout", page=2, cols=12, rows=8)
            check("never more buttons than a page holds (12 across: 4 rows)", e._quick_public()["layout"]["2"]["rows"] == 4, str(r))
            check("refused: 7 across", not e.act("quick_layout", page=2, cols=7).get("ok"))
            check("refused: a row height it doesn't have", not e.act("quick_layout", page=2, height="giant").get("ok"))
            e.act("quick_set", page=3, slot=20, button={"kind": "flash"})
            r = e.act("quick_layout", page=3, cols=4, rows=2)
            check("a button past the last row is kept, and said", r.get("hidden") == [20] and any(x["id"] == "q3-20" for x in e.quick)
                  and "kept" in r["summary"], str(r))
            r = e.act("quick_layout", page=3, cols=8, rows=3, height="m")
            check("back to the usual grid stores nothing", "3" not in e._quick_public()["layout"], str(e._quick_public()["layout"]))
            r = e.act("quick_layout", page=1, cols=6, all_pages=True)
            check("one grid for every page", all(e._quick_public()["layout"][str(p)]["cols"] == 6 for p in range(1, 9)))
            check("slot 48 is a real slot (12 x 4)", e.act("quick_set", page=4, slot=48, button={"kind": "flash"}).get("ok"))
            e.act("undo")
            check("undo takes a button back", not any(x["id"] == "q4-48" for x in e.quick))
            e.act("undo")
            check("undo takes a grid back", e._quick_public()["layout"].get("1", {}).get("cols") != 6, str(e._quick_public()["layout"]))
            e.act("quick_layout", page=2, cols=5, rows=4, height="l")
            e.act("quick_page", page=2, name="Drops")
            e.act("save_show", name="looks")
            e.act("quick_layout", page=2, cols=8, rows=3, height="m")
            e.act("quick_set", page=1, slot=1, clear=True)
            e.act("load_show", name="looks")
            check("a saved show keeps its grids and looks", e._quick_public()["layout"].get("2") == {"cols": 5, "rows": 4, "height": "l"}
                  and next(x for x in e.quick if x["id"] == "q1-1").get("w") == 3, str(e._quick_public()["layout"]))
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                with e.lock:
                    pay = json.loads(e._autosave_payload())
                pay["quick_layout"]["9"] = {"cols": 8}
                pay["quick_layout"]["1"] = {"cols": 7}
                pay["quick_layout"]["3"] = "junk"
                e2._restore_payload(pay)
                lay = e2._quick_public()["layout"]
                check("after a restart: the grids and the page names come back (the names used to be lost)",
                      lay.get("2") == {"cols": 5, "rows": 4, "height": "l"} and e2.quick_names.get("2") == "Drops", f"{lay} {e2.quick_names}")
                check("a bad grid in a file is dropped, not trusted", not {"9", "1", "3"} & set(lay), str(lay))
            finally:
                e2.shutdown()
            from app import assistant
            check("the AI can style buttons and set a page's grid", {"quick_style", "quick_layout"} <= set(assistant.ALLOWED))
        finally:
            e.shutdown()


def _mvr_file(gdtf: bytes, extra: str = "", with_gdtf: bool = True) -> bytes:
    import io
    import zipfile
    hang, stand = "{1,0,0}{0,1,0}{0,0,1}", "{1,0,0}{0,-1,0}{0,0,-1}"

    def fx(name, x, y, z, addr, mode="6 Channel", spec="Acme@Beam900.gdtf", rot=hang):
        return (f'<Fixture name="{name}" uuid="{name}"><Matrix>{rot}{{{x},{y},{z}}}</Matrix><GDTFSpec>{spec}</GDTFSpec>'
                f'<GDTFMode>{mode}</GDTFMode><FixtureID>1</FixtureID><Addresses><Address break="0">{addr}</Address></Addresses></Fixture>')
    row = "".join(fx(f"Beam {i + 1}", -3000 + i * 2000, 4000, 6000, f"1.{1 + i * 6}") for i in range(4))
    grp = ('<GroupObject name="Floor" uuid="g1"><Matrix>{1,0,0}{0,1,0}{0,0,1}{0,-2000,0}</Matrix><ChildList>'
           + fx("Floor L", -2000, 1000, 0, 513, rot=stand) + fx("Floor R", 2000, 1000, 0, "2.7", mode="99 Channel", rot=stand)
           + "</ChildList></GroupObject>")
    ghost = fx("Ghost", 0, 0, 5000, "3.1", spec="Nobody@Nothing.gdtf")
    xml = ('<?xml version="1.0"?><GeneralSceneDescription verMajor="1" verMinor="6"><Scene><Layers>'
           f'<Layer name="Lights" uuid="L1"><ChildList>{row}{grp}{ghost}{extra}</ChildList></Layer></Layers></Scene></GeneralSceneDescription>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("GeneralSceneDescription.xml", xml)
        if with_gdtf:
            z.writestr("Acme@Beam900.gdtf", gdtf)
    return buf.getvalue()


def test_mvr() -> None:
    """MVR in and out (A10 item 3): a plot from Vectorworks / Capture /
    grandMA3 comes in with every light installed from its own GDTF, patched
    at its address in its mode and placed - hanging or standing, inside
    nested groups - and lights hung in a row get a truss they're mounted
    on.  What can't come in is said, never guessed.  One undo step.  Ours
    goes back out with the GDTF files and reads back the same."""
    print("MVR: a plot in (patched, placed, trusses), the rig out")
    import io
    import zipfile

    from app import engine as eng
    from app import mvr
    from tools.selftests.common import SPEC_GDTF
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("description.xml", SPEC_GDTF)
    gdtf = buf.getvalue()
    data = _mvr_file(gdtf)
    plot = mvr.read(data)
    by = {lt["name"]: lt for lt in plot["lights"]}
    check("the plot: 7 lights, the version", len(plot["lights"]) == 7 and plot["version"] == "1.6", str(len(plot["lights"])))
    check("addresses as 1.007 and as one number (513 = universe 2, 1)",
          by["Beam 2"]["address"] == (1, 7) and by["Floor L"]["address"] == (2, 1), str(by["Floor L"]["address"]))
    check("a light inside a group is placed by the group's matrix too", by["Floor L"]["pos"][1] == -1.0, str(by["Floor L"]["pos"]))
    check("turned over = standing; as drawn in GDTF = hanging", by["Floor L"]["stance"] == "stand" and by["Beam 1"]["stance"] == "hang")
    check("millimetres to metres", by["Beam 1"]["pos"] == [-3.0, 4.0, 6.0], str(by["Beam 1"]["pos"]))
    for bad, why in ((b"not a zip", "not a zip"), (_zip({"x.txt": b"hi"}), "no plot inside"),
                     (_zip({"GeneralSceneDescription.xml": b'<!DOCTYPE x [<!ENTITY a "b">]><GeneralSceneDescription/>'}), "XML entities"),
                     (_zip({"GeneralSceneDescription.xml": b"<GeneralSceneDescription><Scene/></GeneralSceneDescription>"}), "no lights")):
        try:
            got = mvr.read(bad)
            ok = why == "no lights" and not got["lights"]
        except ValueError:
            ok = why != "no lights"
        check(f"refused / empty: {why}", ok)
    check("a GDTF name with a path in it can't leave the folder", mvr.safe_name("../../evil.gdtf") == "evil.gdtf"
          and mvr.safe_name("a.exe") is None and mvr.safe_name("..gdtf") is None)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            rigs_before = len(e.venue["rigging"])
            undo_before = len(e._undo)
            r = mvr.import_into(e, data, tmp / "gdtf")
            heads = {h["name"]: h for h in e.patch}
            check("6 lights patched and placed, the one with no GDTF named", r["ok"] and len(heads) == 6 and r["skipped"] == 1
                  and "Nobody@Nothing.gdtf" in r["problems"][0], str(r))
            check("each at its own address, in its own mode", (heads["Beam 2"]["universe"], heads["Beam 2"]["address"]) == (1, 7)
                  and (heads["Floor L"]["universe"], heads["Floor L"]["address"]) == (2, 1) and heads["Beam 1"]["mode"] == "6 Channel")
            check("a mode its file doesn't have: the first mode, and said", heads["Floor R"]["mode"] == "6 Channel"
                  and any("99 Channel" in n for n in r["notes"]), str(r["notes"]))
            rig = (heads["Beam 1"].get("mount") or {}).get("rig")
            check("the 4 in a row hang on one new truss, left to right", r["trusses"] == 1 and len(e.venue["rigging"]) == rigs_before + 1
                  and all((heads[f"Beam {i}"].get("mount") or {}).get("rig") == rig for i in range(1, 5))
                  and heads["Beam 1"]["mount"]["t"] < heads["Beam 4"]["mount"]["t"], str([heads[f"Beam {i}"].get("mount") for i in range(1, 5)]))
            check("at the plot's height", abs(heads["Beam 1"]["y"] - 6.0) < 0.3, str(heads["Beam 1"]["y"]))
            check("the floor lights stand, nearer the audience than the truss",
                  heads["Floor L"]["stance"] == "stand" and heads["Floor L"]["z"] > heads["Beam 1"]["z"] + 4
                  and abs(heads["Floor L"]["x"] - heads["Floor R"]["x"] + 4) < 0.01, f"{heads['Floor L']} {heads['Beam 1']['z']}")
            check("its GDTF is kept (its 3D body works too)", (tmp / "gdtf" / "Acme@Beam900.gdtf").read_bytes() == gdtf)
            check("the whole import is one undo step", len(e._undo) == undo_before + 1 and e._undo[-1].get("label", "Import MVR") is not None)
            out = mvr.export_from(e, [tmp / "gdtf"], "t")
            back = mvr.read(out)
            byb = {lt["name"]: lt for lt in back["lights"]}
            check("out again: every light, its GDTF inside, the same address and mode",
                  len(back["lights"]) == 6 and "Acme@Beam900.gdtf" in back["gdtf"] and byb["Beam 2"]["address"] == (1, 7)
                  and byb["Floor L"]["mode"] == "6 Channel", str(byb.get("Beam 2")))
            check("...hanging and standing as they were, the trusses with them",
                  byb["Floor L"]["stance"] == "stand" and byb["Beam 1"]["stance"] == "hang" and len(back["trusses"]) >= 1)
            again = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s2")
            try:
                r2 = mvr.import_into(again, out, tmp / "gdtf2", replace=True)
                h2 = {h["name"]: h for h in again.patch}
                check("our own MVR reads back into a new desk: the same lights, addresses and spacing",
                      r2["ok"] and len(h2) == 6 and (h2["Beam 2"]["universe"], h2["Beam 2"]["address"]) == (1, 7)
                      and abs((h2["Beam 4"]["x"] - h2["Beam 1"]["x"]) - (heads["Beam 4"]["x"] - heads["Beam 1"]["x"])) < 0.05, str(r2))
            finally:
                again.shutdown()
            r = mvr.import_into(e, data, tmp / "gdtf", replace=True)
            check("start from the plot: the old lights and trusses go, the room fits the plot",
                  r["ok"] and len(e.patch) == 6 and len(e.venue["rigging"]) == 1, f"{len(e.patch)} {len(e.venue['rigging'])}")
            e.act("undo")
            check("...and one Ctrl+Z puts the show back", len(e.patch) == 6 and len(e.venue["rigging"]) == rigs_before + 1,
                  f"{len(e.patch)} {len(e.venue['rigging'])}")
            n = len(e.patch)
            r = mvr.import_into(e, _mvr_file(gdtf, with_gdtf=False), tmp / "gdtf3")
            check("a plot whose GDTF files are missing: the lights the desk already has come in from its own fixtures, and it says so",
                  r["ok"] and len(e.patch) > n and any("used the installed" in x for x in r["notes"])
                  and any("Nobody@Nothing" in p and "no library has it" in p for p in r["problems"]), str(r.get("notes"))[:200])
        finally:
            e.shutdown()


def _zip(files: dict) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_show_templates() -> None:
    """A new show that starts 80 % done (A10 item 2): each template builds
    a room, real lights from the library on the right trusses, groups,
    colour and position palettes, a page of buttons and a first set of
    cues, as ONE undo step; the demo starts playing; New show empties
    the desk (one undo brings the old show back)."""
    print("Show templates, New show, the demo")
    import time as _time

    from app import engine as eng
    from app import showtemplates
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=3)
            e.act("select_all")
            e.act("set_intensity", level=50)
            e.act("record_cue", playback=1, name="mine")
            listed = e.act("show_templates")
            check("five templates to pick from", listed.get("ok") and [t["id"] for t in listed["templates"]]
                  == ["club", "wedding", "band", "theatre", "corporate"], str(listed.get("templates")))
            undo0 = len(e._undo)
            for name in showtemplates.TEMPLATES:
                r = e.act("show_template", name=name)
                t = showtemplates.TEMPLATES[name]
                want = sum(x[3] for x in t["lights"])
                check(f"{t['label']}: every light, groups, palettes, buttons and cues",
                      r.get("ok") and r["heads"] == want and r["groups"] == len({x[0] for x in t["lights"]})
                      and r["palettes"] >= len(t["palettes"]) and r["buttons"] >= 8 and r["cues"] >= 3 and not r["notes"],
                      str({k: r.get(k) for k in ("heads", "groups", "palettes", "buttons", "cues", "notes", "error")}))
                hung = [h for h in e.patch if (h.get("mount") or {}).get("rig")]
                check(f"{t['label']}: the lights hang on the room's trusses",
                      len(hung) == sum(x[3] for x in t["lights"] if x[4]), f"{len(hung)}")
            check("each template is one undo step", len(e._undo) == undo0 + len(showtemplates.TEMPLATES), str(len(e._undo) - undo0))
            for _ in showtemplates.TEMPLATES:
                e.act("undo")
            check("...and undo brings the show that was there back",
                  len(e.patch) == 3 and [c.get("name") for c in e.playbacks[0]["stack"]] == ["mine"],
                  f"{len(e.patch)} {[c.get('name') for c in e.playbacks[0]['stack']]}")
            check("an unknown template is refused, nothing changed",
                  not e.act("show_template", name="rave cave").get("ok") and len(e.patch) == 3)
            r = e.act("show_new")
            check("New show: no lights, cues, groups, palettes, buttons or room",
                  r.get("ok") and not e.patch and not e.groups and not e.quick and not any(e.palettes.values())
                  and not any(p["stack"] for p in e.playbacks) and not e.venue.get("rigging"), str(r))
            e.act("undo")
            check("...one undo and it's all back", len(e.patch) == 3)
            r = e.act("show_template", name="club", demo=True)
            _time.sleep(0.3)
            check("the demo is playing straight away (the timeline, its cues and effects)",
                  r.get("ok") and e.tl["playing"] and e.playbacks[0].get("active"), str(e.tl["playing"]))
            e.act("timeline_stop")
            from app import aitools, engine_base
            check("the AI may only prepare New show / a template (the show that's there would go)",
                  {"show_new", "show_template"} <= set(aitools.CONFIRM))
            check("listing the templates is never an undo step", "show_templates" in engine_base.UNDO_EXCLUDED)
            from app.engine_patch import _plural
            check("group names in the plural: Moving washes, Spots, PARs",
                  [_plural(x) for x in ("Moving wash", "Spot", "PARs")] == ["Moving washes", "Spots", "PARs"])
        finally:
            e.shutdown()


def test_desktop_microphone() -> None:
    """The desktop app gave the page no microphone ("Permission denied" in
    Sound, 2026-10-08): it allows audio input now - and still no camera or
    screen capture.  The rule itself is taken out of desktop/main.js and run."""
    print("Desktop app: the microphone (audio only) is allowed")
    import re
    import shutil
    import subprocess
    src = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    m = re.search(r"const audioOnly = \(d, unknownOk\) => \{.*?\n  \};", src, re.S)
    check("the app has an audio-only rule for media requests", bool(m) and 'perm === "media" && audioOnly(details, false)' in src)
    node = shutil.which("node")
    if not (m and node):
        return
    js = m.group(0) + """
const out = {
  mic: audioOnly({ mediaTypes: ["audio"] }, false),
  cam: audioOnly({ mediaTypes: ["video"] }, false),
  both: audioOnly({ mediaTypes: ["audio", "video"] }, false),
  none: audioOnly({}, false),
  listCheck: audioOnly({ mediaType: "unknown" }, true),
  camCheck: audioOnly({ mediaType: "video" }, true),
};
console.log(JSON.stringify(out));"""
    got = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30).stdout or "{}")
    check("a microphone is allowed; a camera, camera + mic, or nothing named is not",
          got == {"mic": True, "cam": False, "both": False, "none": False, "listCheck": True, "camCheck": False}, str(got))
    pkg = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    check("a Mac asks the operator once (it says what the microphone is for)",
          "NSMicrophoneUsageDescription" in pkg["build"]["mac"].get("extendInfo", {}))


def test_controller_layouts() -> None:
    """Ready layouts for hardware controllers (A10 item 1): each one's
    pads, faders and buttons decoded, and the exact bytes that light a pad
    in a button's colour, blink a button and move a motor fader."""
    print("Controllers: APC mini / mk2, Launchpad Mini MK3 / X, Mackie Control")
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        check("(node not installed: controller layouts not checked)", True, "")
        return
    js = """
const P = await import(process.argv[1]);
const { PROFILES: L, profileFor, hexRgb } = P;
const out = {};
out.names = ["APC MINI", "APC mini mk2", "Launchpad Mini MK3 LPMiniMK3 MIDI", "LPX MIDI", "Launchpad X", "X-Touch", "X-TOUCH COMPACT", "X-Touch Mini", "nanoKONTROL2"].map(profileFor);
const a = L.apcmini, a2 = L.apcmini2, lp = L.launchpadmini3, lx = L.launchpadx, m = L.mackie;
out.apc = [a.decode([0x90, 56, 127]), a.decode([0x80, 7, 0]), a.decode([0xb0, 48, 127]), a.decode([0xb0, 56, 0]),
           a.decode([0x90, 64, 127]), a.decode([0x90, 89, 127]), a.decode([0x90, 99, 127])];
out.apcLed = [a.padLed(0, [255, 0, 0], false), a.padLed(0, [255, 0, 0], true), a.padLed(63, null, false), a.buttonLed("page", 2, true)];
out.apc2 = [a2.decode([0x90, 0, 100]), a2.decode([0x90, 100, 127]), a2.decode([0x90, 115, 127])];
out.apc2Led = [a2.padLed(0, [0, 0, 255], false), a2.padLed(0, [0, 0, 255], true)];
out.lp = [lp.decode([0x90, 81, 100]), lp.decode([0x90, 18, 0]), lp.decode([0xb0, 91, 127]), lp.decode([0xb0, 89, 127]), lp.decode([0xb0, 19, 127]), lp.decode([0x90, 9, 1])];
out.lpHello = [lp.hello(), lp.bye(), lx.hello()];
out.lpLed = [lp.padLed(0, [255, 0, 0], true), lp.padLed(63, [255, 0, 0], false)];
out.mk = [m.decode([0xe0, 0x7f, 0x7f]), m.decode([0xe8, 0, 0]), m.decode([0x90, 24, 127]), m.decode([0x90, 17, 127]), m.decode([0x90, 3, 127]), m.decode([0x90, 9, 127]), m.decode([0x90, 104, 127])];
out.mkOut = [m.faderOut(0, 1), m.faderOut(8, 0), m.faderOut(2, 0.5), m.buttonLed("go", 1, true), m.padLed(3, [1, 2, 3], true)];
out.hex = [hexRgb("#ff8800"), hexRgb("nope")];
const q = L.apc40, q2 = L.apc40mk2;
out.apc40names = ["Akai APC40", "APC40", "APC40 mkII", "APC40 MK2 MIDI"].map(profileFor);
out.apc40 = [q.hello(), q.bye(), q.decode([0x90, 53, 127]), q.decode([0x97, 57, 0]), q.decode([0xb3, 7, 127]), q.decode([0xb0, 14, 0]),
             q.decode([0x92, 51, 127]), q.decode([0x95, 52, 127]), q.decode([0x90, 84, 127]), q.decode([0x90, 99, 127])];
out.apc40Led = [q.padLed(0, [255, 0, 0], false), q.padLed(9, [255, 0, 0], true), q.padLed(39, null, false), q.buttonLed("go", 2, true), q.buttonLed("page", 4, true), q.buttonLed("page", 5, true)];
out.apc40mk2 = [q2.hello(), q2.decode([0x90, 32, 127]), q2.decode([0x90, 7, 0]), q2.decode([0x91, 0, 127])];
out.apc40mk2Led = [q2.padLed(0, [0, 0, 255], false), q2.padLed(0, [0, 0, 255], true), q2.padLed(39, null, false)];
console.log(JSON.stringify(out));
"""
    url = (ROOT / "web" / "app" / "ctrlprofiles.js").as_uri()
    r = subprocess.run([node, "--input-type=module", "-e", js, url], capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("the layout file loads in node", r.returncode == 0, r.stderr[-300:])
    if not got:
        return
    check("recognised by the port's name (and nothing else is)", got["names"] == ["apcmini", "apcmini2", "launchpadmini3", "launchpadx",
                                                                               "launchpadx", "mackie", "mackie", None, None], str(got["names"]))
    check("APC mini: the top-left pad is note 56, the faders are playbacks, 56 the master, the round buttons GO and the pages",
          got["apc"] == [{"pad": 0, "down": True}, {"pad": 63, "down": False}, {"fader": 0, "value": 1}, {"master": 0},
                         {"go": 0, "down": True}, {"page": 7, "down": True}, None], str(got["apc"]))
    check("APC mini pads: red, blinking red when on, off when empty; a page button lit",
          got["apcLed"] == [[[0x90, 56, 3]], [[0x90, 56, 4]], [[0x90, 7, 0]], [[0x90, 84, 1]]], str(got["apcLed"]))
    check("APC mini mk2: buttons under the pads 100-107, the side 112-119",
          got["apc2"] == [{"pad": 56, "down": True}, {"go": 0, "down": True}, {"page": 3, "down": True}], str(got["apc2"]))
    check("APC mini mk2: a blue button at 25 % when off, 100 % when on",
          got["apc2Led"] == [[[0x91, 56, 45]], [[0x96, 56, 45]]], str(got["apc2Led"]))
    check("Launchpad: pad 81 is top-left, 18 bottom-right; the top row GO, the right side pages",
          got["lp"] == [{"pad": 0, "down": True}, {"pad": 63, "down": False}, {"go": 0, "down": True},
                        {"page": 0, "down": True}, {"page": 7, "down": True}, None], str(got["lp"]))
    check("Launchpad: programmer mode on and off by SysEx (Mini MK3 is device 13, X is 12)",
          got["lpHello"] == [[[240, 0, 32, 41, 2, 13, 14, 1, 247]], [[240, 0, 32, 41, 2, 13, 14, 0, 247]], [[240, 0, 32, 41, 2, 12, 14, 1, 247]]],
          str(got["lpHello"]))
    check("Launchpad: a pad in the button's own colour - full when on, dim when off",
          got["lpLed"] == [[[240, 0, 32, 41, 2, 13, 3, 3, 81, 127, 0, 0, 247]], [[240, 0, 32, 41, 2, 13, 3, 3, 18, 18, 0, 0, 247]]],
          str(got["lpLed"]))
    check("Mackie: fader 1 top, the master fader, SELECT = GO, MUTE = release, REC = buttons, SOLO = pages, touch",
          got["mk"] == [{"fader": 0, "value": 1}, {"master": 0}, {"go": 0, "down": True}, {"release": 1, "down": True},
                        {"pad": 3, "down": True}, {"page": 1, "down": True}, {"touch": 0, "down": True}], str(got["mk"]))
    check("Mackie: the motor faders move to the playback (14-bit), the LEDs light",
          got["mkOut"] == [[[0xe0, 127, 127]], [[0xe8, 0, 0]], [[0xe2, 0, 64]], [[0x90, 25, 127]], [[0x90, 3, 127]]], str(got["mkOut"]))
    check("tile colours to RGB", got["hex"] == [[255, 136, 0], None])
    check("APC40 and APC40 mkII recognised by name", got["apc40names"] == ["apc40", "apc40", "apc40mk2", "apc40mk2"], str(got["apc40names"]))
    check("APC40: Ableton mode by SysEx (and back); the grid's top-left is note 53 on channel 1; faders CC 7 per channel, "
          "master CC 14; TRACK SELECT = GO, CLIP STOP = release, SCENE LAUNCH = pages",
          got["apc40"] == [[[0xf0, 0x47, 0x7f, 0x73, 0x60, 0, 4, 0x41, 8, 2, 1, 0xf7]], [[0xf0, 0x47, 0x7f, 0x73, 0x60, 0, 4, 0x40, 8, 2, 1, 0xf7]],
                           {"pad": 0, "down": True}, {"pad": 39, "down": False}, {"fader": 3, "value": 1}, {"master": 0},
                           {"go": 2, "down": True}, {"release": 5, "down": True}, {"page": 2, "down": True}, None], str(got["apc40"]))
    check("APC40 pads: red, blinking when on, off when empty; GO and page LEDs",
          got["apc40Led"] == [[[0x90, 53, 3]], [[0x91, 54, 4]], [[0x97, 57, 0]], [[0x92, 51, 1]], [[0x90, 86, 1]], []], str(got["apc40Led"]))
    check("APC40 mkII: its own SysEx; the grid from the bottom-left (note 32 = top-left)",
          got["apc40mk2"] == [[[0xf0, 0x47, 0x7f, 0x29, 0x60, 0, 4, 0x41, 9, 7, 1, 0xf7]], {"pad": 0, "down": True},
                              {"pad": 39, "down": False}, None], str(got["apc40mk2"]))
    check("APC40 mkII pads: the palette colour, pulsing when on",
          got["apc40mk2Led"] == [[[0x90, 32, 45]], [[0x98, 32, 45]], [[0x90, 7, 0]]], str(got["apc40mk2Led"]))


def test_gdtf_pan_tilt_parts() -> None:
    """A light that only tilted in 3D (asked 2026-10-08): its GDTF listed a
    handle and a display before the yoke, and pan / tilt were taken by the
    ORDER of the parts.  The DMX channels name the part they drive; that
    is used first, the order only when a file names none."""
    print("3D: pan and tilt turn the parts the file's channels name")
    from app import gdtf_geom as g
    xml = b'''<GDTF><FixtureType Name="X" Manufacturer="LTS"><Geometries><Geometry Name="Base" Model="Base">
<Geometry Name="Handle" Model="Handle"/><Geometry Name="Display" Model="D"/>
<Axis Name="Yoke" Model="Yoke"><Axis Name="Head" Model="Head"><Beam Name="Beam"/></Axis></Axis></Geometry></Geometries>
<DMXModes><DMXMode Name="M" Geometry="Base"><DMXChannels>
<DMXChannel Geometry="Head" Offset="3"><LogicalChannel Attribute="Tilt"/></DMXChannel>
<DMXChannel Geometry="Yoke" Offset="1"><LogicalChannel Attribute="Pan"/></DMXChannel>
</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'''
    geo = g.parse_geometry(xml)
    k = g.resolve_kinematics(geo, True, True)
    check("pan turns the yoke, tilt the head (not the handle and the display)",
          g.node_at(geo, k["pan"])["name"] == "Yoke" and g.node_at(geo, k["tilt"])["name"] == "Head" and k.get("by") == "channels", str(k))
    k = g.resolve_kinematics(geo, False, True)
    check("a light with tilt only: just the head", k["pan"] is None and g.node_at(geo, k["tilt"])["name"] == "Head", str(k))
    geo["moves"] = {}
    k = g.resolve_kinematics(geo, True, True)
    check("a file whose channels name no part: the order of the parts, as before", k["pan"] and "by" not in k, str(k))


def test_output_routes_and_wheels() -> None:
    """More universes (A10 item 5): a universe can go to a node of its own
    (one node per truss), Art-Net and sACN; sACN's priority per show.  And
    the on-screen wheels (item 9): any attribute moves by a share of its
    travel, coarse or fine."""
    print("Output: a node per universe, sACN priority; the wheels")
    import socket as _socket

    from app import engine as eng
    from app.artnet import ArtNetSender
    from app.sacn import SacnSender
    a = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    a.bind(("127.0.0.1", 0))
    port = a.getsockname()[1]
    b = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        b.bind(("127.0.0.2", port))
        two = True
    except OSError:
        two = False                       # (no 127.0.0.2 on this computer: macOS)
    for s_ in (a, b):
        s_.settimeout(1.0)
    try:
        snd = ArtNetSender("127.0.0.1", port, 0, dry_run=False)
        snd.routes = {2: "127.0.0.2"} if two else {}
        snd.send(1, bytes(512))
        snd.send(2, bytes(512))
        got_main = a.recv(2000)
        got_routed = b.recv(2000) if two else b"skip"
        check("Art-Net: universe 1 to the target, universe 2 to its own node", bool(got_main) and bool(got_routed))
    finally:
        a.close()
        b.close()
    sc = SacnSender("multicast", 5568, 0, True)
    sc.routes = {3: "2.0.0.13"}
    check("sACN: a routed universe goes to its node, the rest to their multicast group",
          sc.destination(3) == "2.0.0.13" and sc.destination(1).startswith("239.255."))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            r = e.act("set_dmx_target", mode="node", host="2.0.0.10", routes={"2": "2.0.0.12", "3": "2.0.0.13"}, priority=150)
            check("routes and priority set", r.get("ok") and r["target"]["routes"] == {"2": "2.0.0.12", "3": "2.0.0.13"}
                  and r["target"]["priority"] == 150, str(r.get("target")))
            check("the output uses them", e._get_sender().routes == {2: "2.0.0.12", 3: "2.0.0.13"})
            e.act("set_dmx_target", transport="sacn")
            check("sACN gets the show's priority", e._get_sender().priority == 150)
            check("refused: a route that isn't an IP", not e.act("set_dmx_target", routes={"2": "the truss"}).get("ok"))
            check("refused: priority 300", not e.act("set_dmx_target", priority=300).get("ok"))
            e.act("save_show", name="routes")
            e.act("set_dmx_target", routes={}, priority="")
            check("cleared", "routes" not in e.dmx_target and "priority" not in e.dmx_target, str(e.dmx_target))
            e.act("load_show", name="routes")
            check("saved with the show (each venue keeps its nodes)", e.dmx_target.get("routes", {}).get("3") == "2.0.0.13"
                  and e.dmx_target.get("priority") == 150, str(e.dmx_target))
            # the wheels
            e.act("add_heads", query="Moving Head Spot 16ch", qty=2)
            e.act("select_all")
            r = e.act("nudge", attribute="dimmer", step=0.1)
            first = e.programmer[1].get("dimmer")
            e.act("nudge", attribute="dimmer", step=0.001)
            check("a wheel turns any attribute: coarse, then fine", r.get("ok") and first and e.programmer[1]["dimmer"] >= first, str(e.programmer[1]))
            check("pan and tilt as before", e.act("nudge", axis="pan", step=0.02).get("ok"))
            check("an attribute no selected light has: said", "none of the selected" in str(e.act("nudge", attribute="zoom", step=0.1).get("error")))
        finally:
            e.shutdown()


def test_ai_model_choice() -> None:
    """Settings -> AI offered one model (asked 2026-10-08: "only Qwen 5 GB?").
    Every model is listed with whether it fits; the recommendation counts
    the graphics card's memory, so a 10 GB card gets the 14B."""
    print("Offline AI: every model to pick from, the card's memory counted")
    from app import localai
    saved = (localai.memory_gb, localai.vram_gb, localai.shutil.disk_usage)

    class Disk:
        def __init__(self, gb):
            self.free = gb * 2 ** 30
    try:
        for ram, vram, free, want, why in ((16, 10, 100, "qwen3-14b", "16 GB + an RTX 3080 (10 GB): the 14B"),
                                           (16, 0, 100, "qwen3-8b", "16 GB, no big card: the 8B"),
                                           (32, 0, 100, "qwen3-14b", "32 GB: the 14B"),
                                           (16, 10, 8, "qwen3-8b", "a nearly full disk: what fits")):
            localai.memory_gb, localai.vram_gb = (lambda r=ram: r), (lambda v=vram: v)
            localai.shutil.disk_usage = lambda _p, f=free: Disk(f)
            s = localai.suggest()
            check(f"{why}", s["id"] == want and len(s["options"]) == len(localai.CATALOG), str(s))
        localai.memory_gb, localai.vram_gb = (lambda: 16), (lambda: 0)
        localai.shutil.disk_usage = lambda _p: Disk(100)
        s = localai.suggest()
        big = next(o for o in s["options"] if o["id"] == "qwen3-14b")
        check("the bigger model on a smaller computer: still yours to pick, marked slow", big["ok"] and big["slow"]
              and "graphics card" in big["why"] and s["id"] == "qwen3-8b", str(big))
        localai.shutil.disk_usage = lambda _p: Disk(4)
        s = localai.suggest()
        check("no room on the disk: not offered, and why", not any(o["ok"] for o in s["options"]) and "free on the disk" in s["why"], str(s))
    finally:
        localai.memory_gb, localai.vram_gb, localai.shutil.disk_usage = saved


def test_dj_master_and_phrase() -> None:
    """DJ sync (A10 item 4): with two decks playing, the desk follows the
    MASTER (the players' status says who), else stays with the deck it
    follows until that goes quiet - never flip-flopping between two
    tempos.  And the phrase: 8 bars from a "phrase starts here", buttons
    that fire on the next 2 / 4 bars or phrase."""
    print("DJ sync: the master deck, the phrase")
    from app import engine as eng
    from app import tempo as T
    f = T.DeckFollower()
    check("no master known: the first deck leads", f.take(1, 0.0) and not f.take(2, 0.1) and f.take(1, 0.5))
    check("...until it goes quiet for 2 s, then the other one", f.take(2, 2.6) and not f.take(1, 2.7))
    f.saw_status(T.parse_prodj_status(T.build_prodj_status(1, master=True)), 3.0)
    check("the players say deck 1 is master: deck 1 leads", f.take(1, 3.1) and not f.take(2, 3.2))
    check("a master that stopped saying so for 3 s no longer counts", f.master(6.5) is None)
    st = T.parse_prodj_status(T.build_prodj_status(3, master=False, playing=True, name="XDJ-1000"))
    check("a status packet read: device, name, playing, master",
          st == {"device": 3, "name": "XDJ-1000", "playing": True, "master": False, "synced": False, "on_air": False}, str(st))
    check("not a status packet: nothing", T.parse_prodj_status(b"x" * 200) is None and T.parse_prodj_status(T.build_prodj_beat(1, 120, 1)) is None)
    c = T.Clock(128, 0.0)
    c.mark_phrase(10.0)
    spb = 60 / 128
    check("bars counted in the phrase (1..8)", c.bar_in_phrase(10.0) == 1 and c.bar_in_phrase(10.0 + 4 * spb + 0.01) == 2
          and c.bar_in_phrase(10.0 + 31 * spb) == 8 and c.bar_in_phrase(10.0 + 32 * spb + 0.01) == 1)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            e.act("quick_set", page=1, slot=1, button={"kind": "flash", "quant": 32})
            r = e.act("quick_set", page=1, slot=2, button={"kind": "flash", "quant": 7})
            check("a button can wait for the phrase (and 7 beats is refused)", not r.get("ok")
                  and next(b for b in e.quick if b["id"] == "q1-1")["quant"] == 32.0)
            e.act("tempo_set", bpm=120)
            e.act("tempo_phrase")
            e._tempo().anchor -= 0.5                       # half a second into the phrase (one beat)
            r = e.act("quick_press", id="q1-1", down=True)
            check("pressed in bar 1: waits for the next phrase", r.get("pending") and "phrase" in r.get("summary", ""), str(r))
            check("...which is 32 beats from the phrase start", e.quick_pending["q1-1"]["at"] % 32 == 0)
            check("the desk's buttons can all fire on the phrase", e.act("quick_quant", beats=32).get("ok"))
            check("the tempo says which bar of the phrase", "bar_in_phrase" in e.tempo_public())
        finally:
            e.shutdown()


def test_workspaces() -> None:
    """Workspaces (A10 item 6): ready ones for programming, busking,
    theatre and running a show, each a sane layout; a damaged saved one
    can't break the screen; the top bar has the switcher and Alt+1..9."""
    print("Workspaces: the screen arranged for the job")
    import shutil
    import subprocess
    web = ROOT / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    src = (web / "app" / "workspaces.js").read_text(encoding="utf-8")
    main = (web / "app" / "main.js").read_text(encoding="utf-8")
    check("the switcher is in the top bar and starts with the desk", 'id="ws-btn"' in html and "initWorkspaces()" in main)
    check("Alt+1..9 switches (by the key, so a Mac's Alt symbols don't matter)", "Digit([1-9])" in src)
    check("a second window keeps its own layout", "dataset.window" in src)
    node = shutil.which("node")
    if not node:
        check("(node not installed: the layouts not checked)", True, "")
        return
    js = """
const W = await import(process.argv[1]);
console.log(JSON.stringify({ ids: W.BUILTIN.map((w) => w.id), ok: W.BUILTIN.map((w) => JSON.stringify(W.clean(w)) === JSON.stringify(w)),
  bad: W.clean({ id: "x", name: "y".repeat(90), fix: 0, fixW: 5000, progW: 10, bottom: "<b>", dock: -3, tab: "<img>" }),
  show: W.BUILTIN.find((w) => w.id === "show"), busk: W.BUILTIN.find((w) => w.id === "busking") }));
"""
    url = (web / "app" / "wslayouts.js").as_uri()
    r = subprocess.run([node, "--input-type=module", "-e", js, url], capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("the layouts load in node", r.returncode == 0, r.stderr[-300:])
    if not got:
        return
    check("four ready ones: programming, busking, theatre, show", got["ids"] == ["programming", "busking", "theatre", "show"], str(got["ids"]))
    check("each ready one is already a clean layout", all(got["ok"]), str(got["ok"]))
    bad = got["bad"]
    check("a damaged one: widths kept in range, the dock a known mode, no stray markup",
          bad["fixW"] == 620 and bad["progW"] == 220 and bad["bottom"] == "faders" and bad["dock"] == 0
          and bad["tab"] == "" and len(bad["name"]) == 40 and bad["fix"] is True, str(bad))
    check("running a show: the 3D and the buttons, nothing to program with", not got["show"]["fix"] and not got["show"]["prog"]
          and got["show"]["bottom"] == "buttons")
    check("busking: the buttons and the programmer, no fixture list", got["busk"]["prog"] and not got["busk"]["fix"]
          and got["busk"]["bottom"] == "buttons")


def test_bug_report_never_waits_on_git() -> None:
    """A report that sat at "Saving the report..." for ever (both the list
    and the save waited): the desk's version came from running git, and on
    Windows a slow git under a timeout can keep the request waiting for
    good.  The version is read from .git's own files now, and the dialog
    gives up after 25 s with what to do."""
    print("Bug report: never waits on git")
    import inspect
    from app import bugreport
    check("no git program is run", "subprocess" not in inspect.getsource(bugreport))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g = root / ".git"
        (g / "refs" / "heads").mkdir(parents=True)
        (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (g / "refs" / "heads" / "main").write_text("0123456789abcdef\n", encoding="utf-8")
        check("a branch: its commit and name", bugreport._read_version(root) == "0123456 main")
        (g / "refs" / "heads" / "main").unlink()
        (g / "packed-refs").write_text("# pack-refs\nfedcba9876543210 refs/heads/main\n", encoding="utf-8")
        check("a packed branch too", bugreport._read_version(root) == "fedcba9 main")
        (g / "HEAD").write_text("abcdef0123456789\n", encoding="utf-8")
        check("a detached checkout: the commit", bugreport._read_version(root) == "abcdef0")
        check("no .git at all: unknown", bugreport._read_version(root / "nothing") == "unknown")
    js = (ROOT / "web" / "app" / "bugreport.js").read_text(encoding="utf-8")
    check("the dialog gives up in time and says what to do", "AbortController" in js and "WAIT_MS" in js)
    check("Save (and Send) can't be pressed twice while it works",
          "saveBtn.disabled = true" in js or "saveBtn.disabled = sendBtn.disabled = true" in js)


def test_wave360_strobe_and_wheel_colours() -> None:
    """From a report on the Chauvet Intimidator Wave 360 IRC: "no rainbow
    effect", "strobe does not match all lights".  Its strobe ranges run
    FAST to slow, and the programmer's Slow / Medium / Fast were fixed
    numbers (64 / 160 / 250) - 250 is no strobe at all on it.  And in its
    17-channel mode it can't mix, only step through numbered colour
    macros, so it was offered no colour effect at all."""
    print("Intimidator Wave 360: the strobe the right way round, colour effects on its wheel")
    import time as _t
    from app import assistant, engine as eng, fxlib
    key = "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            if not assistant.add_fixture(e, "qlc", key, qty=2, mode="33 ch.").get("ok"):
                check("(the QLC+ library isn't bundled here: not checked)", True, "")
                return
            h = e._head(1)
            st = e.strobe_steps(h, "shutter")
            check("Fast / Medium / Slow are inside its first strobe range (25-64)",
                  all(25 <= st[k] <= 64 for k in ("slow", "medium", "fast")), str(st))
            check("...and the right way round: fast to slow means Fast is the LOW end", st["fast"] < st["medium"] < st["slow"], str(st))
            check("the 3D strobes fast on Fast, slow on Slow",
                  e._strobe_hz(h, {"shutter": st["fast"]}, None) > 15 > 3 > e._strobe_hz(h, {"shutter": st["slow"]}, None))
            check("Off is its open value", st["off"] == 20, str(st))
            e.act("remove_heads", heads=[1, 2])
            assistant.add_fixture(e, "qlc", key, qty=2, mode="17 ch.")
            n1, n2 = sorted(x["head_no"] for x in e.patch)[:2]
            names = e.act("fx_available", heads=[n1])["names"]
            check("17 channels (no mixing): Rainbow, Colour chase and Alternate are offered",
                  all(n in names for n in fxlib.WHEEL_FX), str(names))
            e.act("select_heads", heads=[n1, n2])
            e.act("set_intensity", level=100)
            r = e.act("run_fx", name="colour_chase")
            check("Colour chase runs on its colour macros", r.get("ok"), r.get("error") or "")
            seen, hexes = set(), set()
            a1 = e._head(n1)["address"]
            for _ in range(5):
                _t.sleep(0.35)
                seen.add(e.build_frames()[1][a1 - 1 + 10])
                hexes |= {x.get("hex") for x in e._looks() if x.get("n") in (n1, n2)}
            check("the wheel channel steps through its colours", len(seen) >= 2, str(sorted(seen)))
            check("the 3D shows a colour per numbered macro, not grey", len(hexes) >= 2 and "#cbd5e1" not in hexes, str(hexes))
            check("a wheel step is one of its slots' values",
                  fxlib.wheel_step("rainbow", [11, 19, 27], None, 0.0, 0, 1) in (11, 19, 27)
                  and fxlib.wheel_step("rainbow", [11], None, 0.0, 0, 1) is None)
        finally:
            e.shutdown()


def test_video_render() -> None:
    """Offline programming with a video render (A10 item 7): the 3D view
    recorded as a video for the client - MP4 where the browser makes it,
    named after the show, the timeline's length when it plays the timeline."""
    print("Video render: the 3D view as a video")
    import shutil
    import subprocess
    web = ROOT / "web"
    panel = (web / "app" / "stagepanel.js").read_text(encoding="utf-8")
    stage = (web / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("View -> Record a video", "Record a video" in panel and "openVideoDialog" in panel)
    check("the 3D holds its quality while it records (no auto-lowering)", "this.recording ||" in stage and "startRecording(" in stage)
    node = shutil.which("node")
    if not node:
        check("(node not installed: the video choices not checked)", True, "")
        return
    js = """
const V = await import(process.argv[1]);
console.log(JSON.stringify({
  mp4: V.pickFormat((m) => m.startsWith("video/mp4")).ext, webm: V.pickFormat((m) => m === "video/webm").ext,
  none: V.pickFormat(() => false), throws: V.pickFormat((m) => { if (m.includes("avc1")) throw new Error("x"); return m === "video/mp4"; }).ext,
  tl: V.plannedSeconds("timeline", 30, 161.2), live: [V.plannedSeconds("live", 2), V.plannedSeconds("live", 9999), V.plannedSeconds("live", "x")],
  name: V.fileName("shows/Club night.json", new Date(2026, 9, 8, 21, 5).getTime(), "mp4"),
  odd: V.fileName('a<>:"/\\\\|?*b', 0, "webm").includes("<"), blank: V.fileName("", 0, "mp4").startsWith("jarvis-"),
  clock: V.clock(125) }));
"""
    r = subprocess.run([node, "--input-type=module", "-e", js, (web / "app" / "videoplan.js").as_uri()],
                       capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("the video choices load in node", r.returncode == 0, r.stderr[-300:])
    if not got:
        return
    check("MP4 first (a client's phone plays it), WebM where MP4 can't be made, nothing when neither",
          got["mp4"] == "mp4" and got["webm"] == "webm" and got["none"] is None and got["throws"] == "mp4", str(got))
    check("playing the timeline records its whole length (+1 s)", got["tl"] == 163, str(got["tl"]))
    check("recording by hand: 5 s to 10 min", got["live"] == [5, 600, 30], str(got["live"]))
    check("the file is named after the show and the time", got["name"] == "Club night-2026-10-08-21-05.mp4", got["name"])
    check("no characters a disk refuses; no name: jarvis-...", not got["odd"] and got["blank"])
    check("the clock reads m:ss", got["clock"] == "2:05")


def test_multihead_16bit_heads() -> None:
    """Found by the library rules (tools/rulecheck.py), not by a report:
    on a multi-head light with fine pan / tilt (American DJ Event Bar Pro:
    pan, pan fine, tilt, tilt fine x 4) every fine channel was paired with
    HEAD 1's pan, so heads 2-4 got a 16-bit value cut to 255 - stuck at
    the end of their travel whatever the desk asked."""
    print("Multi-head lights with 16-bit pan / tilt: every head goes where it's sent")
    from app import merge
    roles = ["pan", "pan_fine", "tilt", "tilt_fine"] * 4 + ["dimmer"]
    fine_of, _base = merge.pair_map(roles)
    check("each head's fine channel pairs with its own pan / tilt",
          all(fine_of.get(4 * k) == 4 * k + 1 and fine_of.get(4 * k + 2) == 4 * k + 3 for k in range(4)), str(fine_of))
    head = {"head_no": 1, "universe": 1, "address": 1, "map": roles, "channels": len(roles)}
    fr = merge.build_frames([head], {1: {"pan": 0x4C80, "tilt": 0xB300, "dimmer": 100}}, [])[1]
    check("every head at the same position (coarse and fine)",
          all(fr[4 * k] == 0x4C and fr[4 * k + 1] == 0x80 and fr[4 * k + 2] == 0xB3 for k in range(4)), str(list(fr[:16])))
    mixed = ["pan", "pan_fine", "tilt", "tilt_fine", "unused", "pan", "tilt", "dimmer"]   # Stairville Infinite Pixel 250
    head2 = {"head_no": 1, "universe": 1, "address": 1, "map": mixed, "channels": len(mixed)}
    fr2 = merge.build_frames([head2], {1: {"pan": 0x4C80, "tilt": 0xB300}}, [])[1]
    check("a second head with no fine channel gets the coarse byte, not 255", fr2[5] == 0x4C and fr2[6] == 0xB3, str(list(fr2[:8])))
    fr3 = merge.build_frames([head], {1: {"tilt@2": 0x1000, "tilt@3": 0xF000}}, [])[1]
    check("one head aimed on its own: its own coarse and fine bytes", fr3[6] == 0x10 and fr3[10] == 0xF0, str(list(fr3[:16])))


def test_light_check_mode_and_strobe() -> None:
    """The light check (Test this light) asks first whether the mode set on
    the REAL light matches the desk's - the cause of "some heads don't move"
    no test can see - and switches every light of that model to the mode
    the light shows, in one go.  It checks the strobe too, each head of a
    multi-head light, and is offered when a new model is patched."""
    print("Light check: the light's own mode, every head, the strobe")
    from app import assistant, engine as eng
    js = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("the mode is the first question, with a one-press switch", "async function modeStep" in js and '"change_type"' in js)
    check("offered when a new, untested model is patched", "offerLightTest((r.heads || [])[0])" in js)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            if not assistant.add_fixture(e, "qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf", qty=2, mode="17 ch.").get("ok"):
                check("(the QLC+ library isn't bundled here: not checked)", True, "")
                return
            st = e.act("light_test", head=1, step="start")
            check("the test knows the light's modes and how many heads",
                  [m["name"] for m in st["modes"]] == ["33 ch.", "17 ch."] and st["heads"] == 4 and st["fixture_id"], str(st.get("modes")))
            check("...and its strobe steps", st["strobe"] and st["strobe"]["fast"] < st["strobe"]["slow"], str(st.get("strobe")))
            r = e.act("light_test", head=1, step="strobe", value="fast")
            check("Strobe fast reaches the light", r.get("ok") and e.programmer[1]["shutter"] == st["strobe"]["fast"], str(r))
            e.act("light_test", head=1, step="end")
            r = e.act("change_type", heads=[1, 2], fixture_id=st["fixture_id"], mode="33 ch.")
            check("the light shows 33 channels: both lights switch, keeping their numbers",
                  r.get("ok") and e._head(1)["mode"] == "33 ch." and e._head(2)["mode"] == "33 ch.", r.get("error") or "")
            check("...and the one that had to move says where", "#2 -> 1.34" in r.get("summary", ""), r.get("summary", ""))
            t = e.act("light_tested", head=1, strobe=False)
            check("a wrong strobe (all else right) says report it - the file may be wrong",
                  not t["tested"] and any("strobe" in a for a in t["advice"]), str(t.get("advice")))
        finally:
            e.shutdown()


def test_nothing_waits_forever() -> None:
    """Nothing the desk does may wait for ever.  Another program is only
    run through app/procs.py, which stops it AND whatever it started when
    time is up and never waits for output a helper still holds (Windows'
    subprocess.run(timeout=) waits for exactly that - it froze "Report a
    problem").  And every request from the screen gives up in time."""
    print("Nothing waits for ever: other programs, the screen's requests")
    import re
    import sys as _sys
    from app import procs
    bad = []
    for f in sorted((ROOT / "app").glob("*.py")):
        if f.name == "procs.py":
            continue
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|call|check_output|check_call)\(|\.communicate\(", src):
            bad.append(f"{f.name}:{src.count(chr(10), 0, m.start()) + 1}")
    check("no other program is run outside app/procs.py", not bad, ", ".join(bad))
    t = time.monotonic()
    r = procs.run([_sys.executable, "-c",
                   "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']);"
                   " print('started', flush=True); time.sleep(20)"], 1.0)
    took = time.monotonic() - t
    check("a program whose helper keeps its output open is stopped on time", r.timed_out and took < 5, f"{took:.1f} s")
    check("...and what it printed is kept", "started" in r.stdout, r.stdout)
    check("a program that isn't there: an answer, not a crash", procs.run(["no-such-program-jarvis"], 1).returncode == 127)
    api = (ROOT / "web" / "app" / "api.js").read_text(encoding="utf-8")
    check("every request from the screen has a time limit", "DEFAULT_WAIT_MS" in api and "AbortController" in api)


def test_problems_badge() -> None:
    """Problems reach the operator when they happen, not in a screenshot:
    a crash inside an action is marked (and kept for the report), and the
    top bar's warning badge shows page errors, desk errors and requests
    that got no answer - Report it attaches them."""
    print("The problems badge: a bug shows when it happens")
    from app import bugreport, engine as eng
    web = ROOT / "web" / "app"
    badge = (web / "errorbadge.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    check("the badge is in the top bar, hidden until something goes wrong", 'id="err-btn" hidden' in html)
    check("page errors and unanswered requests reach it", 'addEventListener("error"' in badge and "noteError" in
          (web / "api.js").read_text(encoding="utf-8"))
    check("an action's crash is flagged to it", "res.internal" in (web / "actions.js").read_text(encoding="utf-8"))
    check("Report it attaches what it saw", "recentErrors()" in (web / "bugreport.js").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            before = len(bugreport.SERVER_ERRORS)

            def boom(**_):
                raise RuntimeError("a bug")
            orig = e._handlers["status"]
            e._handlers["status"] = boom            # an action with a bug in it
            try:
                r = e.act("status")
            finally:
                e._handlers["status"] = orig
            check("a crash in an action is marked internal (a bug, not 'can't do that')", r.get("internal") and not r.get("ok"), str(r))
            check("...and kept for the report", len(bugreport.SERVER_ERRORS) > before)
            r = e.act("set_intensity", level="lots")
            check("a normal refusal isn't flagged as a bug", not r.get("ok") and not r.get("internal"), str(r))
        finally:
            e.shutdown()


def test_sweep_fixes() -> None:
    """Found by the library sweep (tools/libsweep.py), each a whole kind of
    light: Vari-Lite's colour flags named Blue / Amber / Magenta are the
    lamp's cyan / yellow / magenta (red couldn't be made, Locate came out
    magenta); a strobe or flicker hit of 255 is OPEN on some lights, so it
    showed nothing; and shows past the 14th couldn't be opened, nor any
    renamed, copied or deleted."""
    print("Sweep fixes: Vari-Lite colours, flashes on lights where 255 is open, show files")
    import time as _t
    from app import engine as eng, fixlib
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            def patch(words):
                rows = fixlib.search(words)
                if not rows:
                    return None
                got = fixtures.store_parsed(db, fixlib.load(rows[0]["src"], rows[0]["key"]), f"{rows[0]['src']}:{rows[0]['key']}")
                return e._head(e.act("add_heads", fixture_id=got["imported"][0]["fixture_id"], qty=1)["heads"][0])
            vl = patch("VL3000 Wash")
            if vl:
                check("Vari-Lite VL3000: its colour flags are cyan / yellow / magenta",
                      {"cyan", "yellow", "magenta"} <= set(vl["map"]) and "blue" not in vl["map"], str(vl["map"]))
                e.act("select_heads", heads=[vl["head_no"]])
                e.act("set_intensity", level=100)
                e.act("set_colour", hex="#ff0000")
                red = next(x for x in e._looks() if x["n"] == vl["head_no"])["hex"]
                e.act("clear_programmer")
                e.act("locate")
                white = next(x for x in e._looks() if x["n"] == vl["head_no"])["hex"]
                check("...red is red, Locate is white", red == "#ff0000" and white == "#ffffff", f"{red} {white}")
                e.act("clear_programmer")
            for words, fx in (("Shark 150C", "shutter_flicker"), ("Strob LED 18", "strobe_random")):
                h = patch(words)
                if not h:
                    continue
                e.act("select_heads", heads=[h["head_no"]])
                e.act("set_intensity", level=100)
                e.act("run_fx", name=fx, params={"density": 0.5})
                seen = set()
                for _ in range(15):
                    _t.sleep(0.08)
                    seen.add(bytes(e.build_frames()[h["universe"]][h["address"] - 1:h["address"] - 1 + len(h["map"])]))
                check(f"{h['model']}: {fx} really flashes (255 is 'open' on it)", len(seen) >= 2, str(len(seen)))
                e.act("stop_fx")
                e.act("clear_programmer")
            for name in ("Wedding", "Club night", "Gala"):
                check(f"saved {name}", e.act("save_show", name=name).get("ok"))
            rows = e.act("show_files")["shows"]
            check("every saved show is listed, with when and how big", {r["name"] for r in rows} == {"Wedding", "Club night", "Gala"}
                  and all(r["saved"] and r["bytes"] for r in rows), str(rows))
            r = e.act("show_rename", name="Gala", new="Gala 2026")
            check("rename (the open one stays open under its new name)", r.get("ok") and e.show_file == "Gala 2026", str(r))
            check("a copy", e.act("show_copy", name="Wedding", new="Wedding Smith").get("ok")
                  and (tmp / "s" / "Wedding Smith.json").is_file())
            check("a name already taken is refused", not e.act("show_copy", name="Wedding", new="club night").get("ok"))
            check("the open show can't be deleted", not e.act("show_delete", name="Gala 2026").get("ok"))
            r = e.act("show_delete", name="Wedding")
            check("delete goes to the .bin, not gone", r.get("ok") and not (tmp / "s" / "Wedding.json").exists()
                  and any((tmp / "s" / ".bin").glob("Wedding-*.json")), str(r))
            check("...and the .bin isn't listed as a show", "Wedding" not in {x["name"] for x in e.act("show_files")["shows"]})
            e.act("set_lock", state="operate")
            check("Operate mode refuses deleting a show", not e.act("show_delete", name="Wedding Smith").get("ok"))
            e.act("set_lock", state="design")
        finally:
            e.shutdown()


def test_truss_turn_aim() -> None:
    """A turned truss turns the lights on it: aiming at a spot (the room map,
    Follow me, the assistant) still lands every beam on that spot, and the
    3D turns each body with its truss - so the desk and the real lights agree."""
    print("Truss turn: lights turn with their truss and still hit the aim spot")
    import math
    from app import engine as eng, fixture_kind, venue as V
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("show_template", name="club")
            rigs: dict = {}
            for h in e.patch:
                if "pan" in h["map"] and "tilt" in h["map"] and isinstance(h.get("mount"), dict):
                    rigs.setdefault(h["mount"]["rig"], []).append(h["head_no"])
            check("the club template has moving lights on a truss", bool(rigs), str(list(rigs)))
            if not rigs:
                return
            rid, heads = max(rigs.items(), key=lambda kv: len(kv[1]))
            r = V.rig(e.venue, rid)

            def miss(n, target):
                h, v = e._head(n), e.programmer[n]
                rg = e.head_ranges(h)
                pr, tr = rg.get("pan") or {}, rg.get("tilt") or {}
                pmin, pmax = (pr["min"], pr["max"]) if pr.get("unit") == "degree" and pr.get("min") is not None else (-270.0, 270.0)
                tmin, tmax = (tr["min"], tr["max"]) if tr.get("unit") == "degree" and tr.get("min") is not None else (-135.0, 135.0)
                p = math.radians(pmin + v["pan"] / (65535 if "pan_fine" in h["map"] else 255) * (pmax - pmin))
                t = math.radians(tmin + v["tilt"] / (65535 if "tilt_fine" in h["map"] else 255) * (tmax - tmin))
                d = [math.sin(t) * math.sin(p), math.cos(t), math.sin(t) * math.cos(p)]
                hung = h.get("stance") == "hang"
                if hung:
                    d = [-d[0], -d[1], d[2]]
                y = math.radians(e._head_yaw(h))      # the 3D: holder.rotation.y = yaw
                d = [d[0] * math.cos(y) + d[2] * math.sin(y), d[1], -d[0] * math.sin(y) + d[2] * math.cos(y)]
                dsc = fixture_kind.describe(h)
                piv = 0.372 if dsc.get("heads") else e._AIM_PIVOT.get(dsc["type"], 0.4)
                o = [h["x"], h["y"] + (-piv if hung else piv), h["z"]]
                w = [target[i] - o[i] for i in range(3)]
                along = sum(w[i] * d[i] for i in range(3))
                return math.sqrt(max(0.0, sum(x * x for x in w) - along * along)), along

            target = (0.0, 0.0, 6.0)
            a, b = r["a"], r["b"]
            cx, cz = (a[0] + b[0]) / 2, (a[2] + b[2]) / 2
            half = math.hypot(b[0] - a[0], b[2] - a[2]) / 2
            for ang in (0, 37, 90, 180):
                th = math.radians(ang)
                e.act("venue_update", id=rid, changes={"a": [cx - half * math.cos(th), a[1], cz + half * math.sin(th)],
                                                        "b": [cx + half * math.cos(th), b[1], cz - half * math.sin(th)]})
                e.act("select_heads", heads=heads)
                e.act("aim_at", x=target[0], y=target[1], z=target[2])
                got = [miss(n, target) for n in heads]
                worst = max(x[0] for x in got)
                check(f"truss turned {ang} deg: every beam lands on the aim spot", worst < 0.05 and all(x[1] > 0 for x in got),
                      f"worst miss {worst:.3f} m")
                e.act("clear_programmer")
            snap = e.snapshot() if hasattr(e, "snapshot") else None
            if snap:
                row = next((x for x in snap.get("patch", []) if x.get("head_no") == heads[0]), {})
                check("the snapshot gives the 3D each light's turn (yaw)", "yaw" in row, str(sorted(row)[:8]))
            js = (ROOT / "web" / "app" / "stagepanel.js").read_text(encoding="utf-8")
            check("the 3D view is handed each light's yaw", "yaw: +h.yaw" in js)
            st = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
            check("the 3D turns each light's body by its yaw", "f.yaw" in st)
            am = (ROOT / "web" / "app" / "aimfollow.js").read_text(encoding="utf-8")
            check("the room map draws the room's own outline, trusses and objects",
                  "r.outline" in am and "am-truss" in am and "v.objects" in am)
        finally:
            e.shutdown()


def test_fixture_requests() -> None:
    """A10 item 8: new lights arrive by themselves (a weekly job rebuilds the
    libraries and opens a pull request when a fixture changed; desks pick it
    up on their next start), and a light no library has is one click from a
    filled-in request."""
    print("Fixture library updates and 'Request a fixture'")
    import re
    import sys as _sys
    import urllib.parse as up
    from app import bugreport, fixlib
    url = bugreport.request_url("Chauvet", "Intimidator Wave 360 IRC", "14-channel",
                                "https://example.com/manual.pdf", "for the club")
    q = {k: v[0] for k, v in up.parse_qs(up.urlparse(url).query).items()}
    check("the request opens the fixture-request form", q.get("template") == "fixture-request.yml", url[:120])
    check("...titled with the light", q.get("title") == "[Fixture] Chauvet Intimidator Wave 360 IRC", q.get("title"))
    check("...brand / model labels", "brand:Chauvet" in q.get("labels", "") and "fixture-request" in q.get("labels", ""))
    tpl = (ROOT / ".github" / "ISSUE_TEMPLATE" / "fixture-request.yml").read_text(encoding="utf-8")
    ids = set(re.findall(r"^\s+id:\s*(\w+)", tpl, re.M))
    fields = set(q) - {"template", "title", "labels"}
    check("every field the desk fills in is on the form", fields <= ids, f"{sorted(fields - ids)} not in {sorted(ids)}")
    check("the form asks for the manual", "manual" in ids)
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("the desk serves the request link", '"/api/fixtures/request"' in main)
    dlg = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    check("Add fixtures offers 'Request it...' (and when nothing matches)",
          "export function requestFixture" in dlg and dlg.count("requestFixture(") >= 3)
    libs = {x["src"]: x for x in fixlib.libraries()}
    check("each open library says when it was last updated",
          all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", libs[s].get("built") or "") for s in ("ofl", "qlc")),
          str({s: libs[s].get("built") for s in libs}))
    _sys.path.insert(0, str(ROOT / "tools"))
    import build_fixture_libraries as bfl
    before = bfl._snapshot(ROOT / "app" / "fixlib" / "ofl.zip")
    check("a library's fixtures can be listed for comparing", len(before) > 100, str(len(before)))
    after = dict(before)
    gone = next(iter(after))
    del after[gone]
    some = next(iter(after))
    after[some] = (after[some][0], "different")
    after["new/light.json"] = ("Newco Beam 1", "x")
    ch = bfl.changes(before, after)
    check("a rebuild says what is new, updated and gone",
          ch["added"] == ["Newco Beam 1"] and ch["changed"] == [after[some][0]] and ch["removed"] == [before[gone][0]], str(ch)[:200])
    check("...and 'NO CHANGES' when nothing moved", not any(bfl.changes(before, before).values()))
    wf = (ROOT / ".github" / "workflows" / "library-update.yml").read_text(encoding="utf-8")
    check("a weekly job rebuilds the libraries and opens a pull request only when something changed",
          "schedule" in wf and "--summary" in wf and "NO CHANGES" in wf and "gh pr create" in wf and "rulecheck" in wf)


def test_light_file_quirks() -> None:
    """Backlog A8, from the library sweeps: a lime ("Mint") emitter is a
    colour, so an ETC Source Four LED's Locate is white, not magenta; a
    white LED beside a partial mix is white alone (not pink); a CMY-only
    lamp is lit and white at rest; and an autosave that couldn't be read
    is said on screen once, not only in the log."""
    print("Light-file quirks: lime emitters, partial mixes, CMY lamps, a broken autosave")
    from app import engine as eng, fixlib
    from app.engine_support import channel_role
    check("Lime and Mint are the lime emitter", channel_role("Lime") == "lime" and channel_role("Mint") == "lime")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            def patch(words, mode=None):
                rows = fixlib.search(words)
                if not rows:
                    return None
                got = fixtures.store_parsed(db, fixlib.load(rows[0]["src"], rows[0]["key"]), f"{rows[0]['src']}:{rows[0]['key']}")
                r = e.act("add_heads", fixture_id=got["imported"][0]["fixture_id"], qty=1, mode=mode)
                return e._head(r["heads"][0]) if r.get("heads") else None

            def look(h):
                return next(x for x in e._looks() if x["n"] == h["head_no"])

            for words, mode in (("Source Four LED Series 2 Tungsten HD", "Direct"), ("Rocklite RGBAW", "4-Channel")):
                h = patch(words, mode)
                if not h:
                    continue
                e.act("select_heads", heads=[h["head_no"]])
                e.act("locate")
                check(f"{words} [{mode}]: Locate is white", look(h)["hex"] == "#ffffff", f"{look(h)['hex']} {h['map']}")
                if "lime" in h["map"]:
                    e.act("set_colour", hex="#00ff00")
                    check("...green lights its lime", look(h)["hex"] == "#00ff00", look(h)["hex"])
                e.act("clear_programmer")
            cmy = patch("Generic CMY Fader")
            if cmy:
                lk = look(cmy)
                check("a CMY-only lamp is lit and white at rest (its flags are out)", lk["on"] and lk["hex"] == "#ffffff", str(lk))
                e.act("select_heads", heads=[cmy["head_no"]])
                e.act("set_colour", hex="#ff0000")
                check("...and red when the flags make red", look(cmy)["hex"] == "#ff0000", str(look(cmy)))
                e.act("clear_programmer")
        finally:
            e.shutdown()
        save = tmp / "autosave.json"
        save.write_text("{ cut short by a power cu", encoding="utf-8")
        b = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s2", autosave_path=save, restore=True)
        try:
            n = b.snapshot().get("notice") or {}
            check("an unreadable autosave is said on screen", n.get("id") == "autosave_broken" and "autosave.broken.json" in n.get("text", ""), str(n)[:160])
            b.act("notice_seen")
            check("...once: OK clears it", not b.snapshot().get("notice"))
        finally:
            b.shutdown()
    js = (ROOT / "web" / "app" / "errorbadge.js").read_text(encoding="utf-8")
    check("the screen shows the start-up notice", "snap.notice" in js and "notice_seen" in js)


def test_ai_installer_tick() -> None:
    """A12's last piece: the installer's "Include the offline AI" tick.  The
    desktop app hands the tick to the desk as DATA/ai/wanted.json; on start
    the desk downloads the model that fits in the background - nothing to
    press - and forgets the note once it has it (or the computer can't run
    one, or the operator pauses it)."""
    print("The installer's offline-AI tick: downloads on first start")
    import hashlib
    import json as _json
    import os
    from app import config as cfg, localai
    blob = b"GGUF" + bytes(range(256)) * 20
    listing = _json.dumps([{"path": "Qwen3-8B-Q4_K_M.gguf", "size": len(blob),
                            "lfs": {"oid": hashlib.sha256(blob).hexdigest(), "size": len(blob)}}]).encode()

    def fake(url, headers):
        if "/api/models/" in url:
            return 200, {}, iter([listing])
        return 200, {}, iter([blob])
    import collections
    saved = (cfg.DATA, os.environ.get("LLAMA_SERVER"), localai.transport, localai.memory_gb, localai.vram_gb,
             localai.shutil.disk_usage)
    with tempfile.TemporaryDirectory() as td:
        cfg.DATA = Path(td)
        os.environ["LLAMA_SERVER"] = str(ROOT / "tools" / "selftests" / "fake_llama.py")
        localai.transport = fake
        localai.memory_gb, localai.vram_gb = (lambda: 16.0), (lambda: 0.0)
        usage = collections.namedtuple("usage", "total used free")
        localai.shutil.disk_usage = lambda _p: usage(500 * 2 ** 30, 0, 200 * 2 ** 30)
        note = localai.folder() / localai.WANTED
        try:
            check("no tick: nothing happens on start", localai.auto_download() == "")
            note.write_text("{}", encoding="utf-8")
            check("ticked, but the output is live: it waits", "live" in localai.auto_download(live=True) and note.is_file())
            said = localai.auto_download()
            check("ticked: the model that fits starts downloading in the background", "qwen3-8b" in said, said)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 5 and not localai.status()["download"].get("finished"):
                time.sleep(0.05)
            check("...it arrives, checked", [m["file"] for m in localai.models()] == ["Qwen3-8B-Q4_K_M.gguf"],
                  str(localai.status()["download"]))
            check("...and the note is gone (no download on the next start)", not note.exists())
            note.write_text("{}", encoding="utf-8")
            check("a model already here: the note just goes", localai.auto_download() == "a model is already here" and not note.exists())
            localai.remove("Qwen3-8B-Q4_K_M.gguf")
            note.write_text("{}", encoding="utf-8")
            localai.memory_gb = lambda: 8.0
            said = localai.auto_download()
            check("a computer that can't run it: not downloaded, said why, note gone",
                  said.startswith("not downloaded") and not note.exists(), said)
            note.write_text("{}", encoding="utf-8")
            localai.pause()
            check("Pause in Settings -> AI: the tick no longer restarts it", not note.exists())
        finally:
            cfg.DATA, env, localai.transport, localai.memory_gb, localai.vram_gb, localai.shutil.disk_usage = saved
            if env is None:
                os.environ.pop("LLAMA_SERVER", None)
            else:
                os.environ["LLAMA_SERVER"] = env
    nsh = (ROOT / "desktop" / "res" / "installer.nsh").read_text(encoding="utf-8")
    pkg = (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    js = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    check("the installer has the ticked 'Include the offline AI' page",
          "customPageAfterChangeDir" in nsh and "BST_CHECKED" in nsh and "offline-ai-wanted" in nsh
          and '"include": "res/installer.nsh"' in pkg)
    code = "\n".join(line.split(";")[0] for line in nsh.splitlines())
    check("...using no MUI macro (electron-builder includes it before MUI is loaded - the Windows build stopped)",
          "MUI_" not in code)
    check("the app hands the tick to the desk, and the desk acts on it at start",
          "offline-ai-wanted" in js and "wanted.json" in js and "localai.auto_download" in main)


def test_mvr_trusses_objects() -> None:
    """MVR's own trusses and scene objects (asked: "MVR trusses / objects"):
    a truss comes in at its place, angle and length (from its GDTF model, or
    the length in its name), the lights hung along it go ON it - turned with
    it - and the PA, the LED wall, the bar come in as objects.  Ours go back
    out with their length and angle."""
    print("MVR: the plot's own trusses (angle, length) and objects")
    import io
    import math
    import zipfile

    from app import engine as eng
    from app import mvr
    from app import venue as venue_mod
    from tools.selftests.common import SPEC_GDTF

    def zipped(files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for n, d in files.items():
                z.writestr(n, d)
        return b.getvalue()
    light = zipped({"description.xml": SPEC_GDTF})
    truss = zipped({"description.xml": b'<GDTF><FixtureType Name="Box" Manufacturer="Prolyte"><Models>'
                                       b'<Model Name="Box30" Length="6" Width="0.29" Height="0.29" PrimitiveType="Cube"/>'
                                       b'</Models></FixtureType></GDTF>'})
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    turned = f"{{{c},{s},0}}{{{-s},{c},0}}{{0,0,1}}"

    def fx(name, x, y, z):
        return (f'<Fixture name="{name}" uuid="{name}"><Matrix>{turned}{{{x},{y},{z}}}</Matrix>'
                f'<GDTFSpec>Acme@Beam900.gdtf</GDTFSpec><GDTFMode>6 Channel</GDTFMode>'
                f'<Addresses><Address break="0">1.{1 + len(name) * 0}</Address></Addresses></Fixture>')
    beams = "".join(fx(f"T{i}", round(d * c), round(4000 + d * s), 5700) for i, d in enumerate((-2000, 0, 2000)))
    xml = ('<?xml version="1.0"?><GeneralSceneDescription verMajor="1" verMinor="6"><Scene><Layers><Layer name="Rig" uuid="L"><ChildList>'
           f'<Truss name="Upstage truss" uuid="t1"><Matrix>{turned}{{0,4000,6000}}</Matrix><GDTFSpec>Prolyte@Box30.gdtf</GDTFSpec></Truss>'
           '<Truss name="Pipe 4m" uuid="t2"><Matrix>{1,0,0}{0,1,0}{0,0,1}{0,0,5000}</Matrix></Truss>'
           f'{beams}'
           '<SceneObject name="PA Left" uuid="o1"><Matrix>{1,0,0}{0,1,0}{0,0,1}{-5000,1000,0}</Matrix></SceneObject>'
           '<VideoScreen name="LED Wall" uuid="o2"><Matrix>{1,0,0}{0,1,0}{0,0,1}{0,7000,2000}</Matrix></VideoScreen>'
           '<SceneObject name="Chair 12" uuid="o3"><Matrix>{1,0,0}{0,1,0}{0,0,1}{0,0,0}</Matrix></SceneObject>'
           '</ChildList></Layer></Layers></Scene></GeneralSceneDescription>')
    data = zipped({"GeneralSceneDescription.xml": xml, "Acme@Beam900.gdtf": light, "Prolyte@Box30.gdtf": truss})
    plot = mvr.read(data)
    check("the plot's trusses and the objects the desk knows (a chair is left out)",
          len(plot["trusses"]) == 2 and sorted(o["kind"] for o in plot["objects"]) == ["screen", "speaker"] and plot["others"] == 1,
          f"{plot['trusses']} {plot['objects']}")
    check("a truss's length from its GDTF model, or from its name",
          plot["trusses"][0]["size"][0] == 6.0 and mvr.length_in_name("Pipe 4m") == 4.0 and mvr.length_in_name("10ft truss") == 3.048
          and mvr.length_in_name("Truss 1") is None)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            r = mvr.import_into(e, data, tmp / "gdtf", replace=True)
            rigs = {x["name"]: x for x in e.venue["rigging"]}
            up = rigs.get("Upstage truss")
            check("both trusses in, and the 2 objects", r["ok"] and r["trusses"] == 2 and r["objects"] == 2 and up and "Pipe 4m" in rigs, str(r))
            if up:
                ln = math.dist((up["a"][0], up["a"][2]), (up["b"][0], up["b"][2]))
                check("the truss at its angle (30 deg) and length (6 m)",
                      abs(venue_mod.fold90(venue_mod.rig_angle(up)) - 30) < 0.5 and abs(ln - 6) < 0.05, f"{venue_mod.rig_angle(up)} {ln}")
                pipe = rigs["Pipe 4m"]
                check("the pipe: 4 m from its name", abs(math.dist((pipe["a"][0], pipe["a"][2]), (pipe["b"][0], pipe["b"][2])) - 4) < 0.05)
                on = [h for h in e.patch if (h.get("mount") or {}).get("rig") == up["id"]]
                check("the 3 lights along it hang ON it, in order", len(on) == 3
                      and sorted(on, key=lambda h: h["mount"]["t"])[0]["name"] == "T0", str([(h["name"], h.get("mount")) for h in e.patch]))
                check("...turned with it", all(abs(abs(e._head_yaw(h)) - 30) < 1 or abs(abs(e._head_yaw(h)) - 150) < 1 for h in on),
                      str([e._head_yaw(h) for h in on]))
            objs = {o["kind"]: o for o in e.venue["objects"]}
            check("the PA and the LED wall as objects, the screen at its height",
                  "speaker" in objs and "screen" in objs and abs(objs["screen"]["y"] - 2.0) < 0.01, str(objs))
            back = mvr.read(mvr.export_from(e, [tmp / "gdtf"], "t"))
            bt = {t["name"].split(" (")[0]: t for t in back["trusses"]}
            check("out again: the truss with its length in its name and its angle",
                  "Upstage truss" in bt and abs(mvr.length_in_name(bt["Upstage truss"]["name"]) - 6) < 0.05
                  and abs(math.degrees(math.atan2(bt["Upstage truss"]["u"][1], bt["Upstage truss"]["u"][0])) % 180 - 30) < 0.5,
                  str(bt.get("Upstage truss")))
            check("...and the objects", sorted(o["kind"] for o in back["objects"]) == ["screen", "speaker"], str(back["objects"]))
        finally:
            e.shutdown()


def test_report_relay() -> None:
    """Bug reports without a GitHub account: with REPORT_RELAY set, the desk
    sends the saved report to the relay (tools/report-relay/), which files
    the issue with its own token and keeps the zip.  The desk: https only, a
    size cap, plain reasons, no secrets.  The relay: the form's fields, safe
    labels, no @mentions, the zip behind the maintainers' key, a rate limit."""
    print("Report relay: send a report with no GitHub account")
    import json as _json
    import shutil
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app import bugreport, config as cfg
    from app import engine as eng
    seen: dict = {}

    class Relay(BaseHTTPRequestHandler):
        answer = (200, {"issue_url": f"https://github.com/{bugreport.REPO}/issues/77", "number": 77})

        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["body"] = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["key"] = self.headers.get("X-Relay-Key")
            code, obj = Relay.answer
            data = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = HTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved = (cfg.REPORT_RELAY, cfg.REPORT_RELAY_KEY, cfg.LLM_API_KEY, cfg.DATA)
    key = "AIza" + "q" * 35
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cfg.DATA = tmp
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            cfg.REPORT_RELAY, cfg.LLM_API_KEY = "", key
            check("no relay set: no Send button (the report is saved and GitHub opens, as before)",
                  bugreport.report(e, None, "x", [], False, b"", [], preview=True).get("relay") is False)
            cfg.REPORT_RELAY = "http://example.com/relay"
            out = bugreport.report(e, None, "the faders stick", [], False, b"", [], send=True)
            check("an http:// relay (not this computer) is refused - the report is still saved",
                  "https" in out.get("send_error", "") and out.get("zip"), str(out))
            cfg.REPORT_RELAY, cfg.REPORT_RELAY_KEY = f"http://127.0.0.1:{srv.server_address[1]}/", "desk-key"
            check("relay set: the window offers Send report", bugreport.report(e, None, "x", [], False, b"", [], preview=True)["relay"])
            out = bugreport.report(e, None, f"the faders stick, my key is {key}", [], True, b"", [], send=True)
            body = seen.get("body") or {}
            check("sent: the relay's issue comes back", out.get("sent", {}).get("number") == 77, str(out))
            check("...on the bug form, its fields filled in, the zip with it",
                  body.get("template") == "bug.yml" and body["fields"].get("what", "").startswith("the faders stick")
                  and body.get("zip_name") == out["zip"] and len(body.get("zip", "")) > 100, str(body)[:200])
            check("...the desk's relay key sent; no secret anywhere in it",
                  seen.get("key") == "desk-key" and key not in _json.dumps(body), "")
            Relay.answer = (429, {"error": "too many reports from here - try again in an hour"})
            out = bugreport.report(e, None, "again", [], False, b"", [], send=True)
            check("the relay says no: its reason, and the report is saved for GitHub",
                  "too many reports" in out.get("send_error", "") and out.get("url", "").startswith("https://github.com/"), str(out))
            Relay.answer = (200, {"issue_url": "https://evil.example/x"})
            out = bugreport.report(e, None, "again", [], False, b"", [], send=True)
            check("an answer that isn't this repo's issue is not shown as sent", "send_error" in out and "sent" not in out, str(out))
            big = tmp / "bug_reports" / "jarvis-report-big.zip"
            big.write_bytes(b"PK" + b"\0" * (bugreport.RELAY_MAX + 10))
            try:
                bugreport.relay_send(big, None, "x", [])
                capped = False
            except ValueError as exc:
                capped = "Attach the whole show" in str(exc)
            check("a report over the relay's size: says to untick the show", capped)
        finally:
            e.shutdown()
            srv.shutdown()
            cfg.REPORT_RELAY, cfg.REPORT_RELAY_KEY, cfg.LLM_API_KEY, cfg.DATA = saved
    js = (ROOT / "web" / "app" / "bugreport.js").read_text(encoding="utf-8")
    check("the report window: Send report when the desk has a relay", "Send report" in js and "send: viaRelay" in js)
    # the relay itself, with a fake GitHub and a fake bucket
    node = shutil.which("node")
    if not node:
        return
    script = r"""
const W = (await import(process.argv[1])).default;
const calls = [];
globalThis.fetch = async (u, o) => { calls.push({ u, o }); return new Response(JSON.stringify({ html_url: "https://github.com/o/r/issues/5", number: 5 }), { status: 201 }); };
const store = new Map();
const env = { REPO: "o/r", GITHUB_TOKEN: "t", DOWNLOAD_KEY: "dk", RELAY_KEY: "rk",
  REPORTS: { put: async (k, v, m) => store.set(k, { v, m }), get: async (k) => store.has(k) ? { body: store.get(k).v, customMetadata: store.get(k).m.customMetadata } : null } };
const zip = Buffer.from("PK\x03\x04hello").toString("base64");
const rep = (extra = {}, head = {}) => new Request("https://relay.example/", { method: "POST",
  headers: { "X-Relay-Key": "rk", "CF-Connecting-IP": "1.2.3.4", ...head },
  body: JSON.stringify({ template: "light-bug.yml", title: "[Light] Acme Beam: dark", labels: ["light-bug", "brand:Acme", "admin", "x\ny"],
    fields: { light: "Acme Beam", what: "dark, cc @everyone" }, zip_name: "jarvis-report-1-acme.zip", zip, ...extra }) });
const out = {};
let r = await W.fetch(rep(), env);
out.ok = [r.status, await r.json()];
const gh = JSON.parse(calls[0].o.body);
out.gh = { url: calls[0].u, labels: gh.labels, mention: gh.body.includes("@everyone"), link: /\/r\/[0-9a-f-]{36}\)/.test(gh.body), title: gh.title };
const id = [...store.keys()][0].replace(".zip", "");
out.dlNoKey = (await W.fetch(new Request(`https://relay.example/r/${id}`), env)).status;
const dl = await W.fetch(new Request(`https://relay.example/r/${id}?key=dk`), env);
out.dl = [dl.status, dl.headers.get("Content-Disposition")];
out.wrongKey = (await W.fetch(rep({}, { "X-Relay-Key": "nope" }), env)).status;
out.badForm = (await W.fetch(rep({ template: "../evil" }), env)).status;
out.notZip = (await W.fetch(rep({ zip: Buffer.from("hello").toString("base64") }, { "CF-Connecting-IP": "9.9.9.9" }), env)).status;
const codes = [];
for (let i = 0; i < 6; i++) codes.push((await W.fetch(rep({}, { "CF-Connecting-IP": "5.5.5.5" }), env)).status);
out.rate = codes;
console.log(JSON.stringify(out));
"""
    url = (ROOT / "tools" / "report-relay" / "worker.mjs").as_uri()
    r = subprocess.run([node, "--input-type=module", "-e", script, url], capture_output=True, text=True, timeout=30)
    got = _json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("the relay runs", r.returncode == 0, r.stderr[-300:])
    if not got:
        return
    check("the relay files the issue and answers with it", got["ok"] == [200, {"issue_url": "https://github.com/o/r/issues/5", "number": 5}], str(got["ok"]))
    check("...on this repo, with the form's labels only (no made-up ones), and no @mention",
          got["gh"]["url"] == "https://api.github.com/repos/o/r/issues" and got["gh"]["labels"] == ["light-bug", "brand:Acme"]
          and not got["gh"]["mention"], str(got["gh"]))
    check("...the zip kept, linked from the issue, downloadable only with the maintainers' key",
          got["gh"]["link"] and got["dlNoKey"] == 403 and got["dl"][0] == 200 and "jarvis-report-1-acme.zip" in (got["dl"][1] or ""), str(got))
    check("the wrong relay key, an unknown form, a file that isn't a zip: refused",
          got["wrongKey"] == 403 and got["badForm"] == 400 and got["notZip"] == 400, str(got))
    check("at most 6 reports an hour from one address", got["rate"] == [200] * 6, str(got["rate"]))


def test_sweep3_fixes() -> None:
    """Sweep 3: the actions that had no test of their own, and its fixes -
    127.0.0.1 is this computer (no false network warning), a rebuilt room
    keeps the lights on its new rigging, the 3D's auto quality counts very
    slow frames, the Output choices aren't cut off, the groups fold."""
    print("Sweep 3: the untested actions; loopback, rebuilt rooms, slow 3D")
    from app import engine as eng, netif
    lan = [{"name": "eth", "ip": "192.0.2.2", "mask": "255.255.255.0"}]
    check("127.0.0.1 is this computer: reachable, no 'plug into the network' warning",
          netif.check("127.0.0.1", lan)["ok"] and netif.check("127.0.0.5", lan)["ok"])
    check("...a node on another network still warns", not netif.check("2.0.0.10", lan)["ok"])
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("show_template", name="club")
            heads = [h["head_no"] for h in e.patch]
            # the actions with no test of their own
            e.act("select_heads", heads=heads[:3])
            e.act("set_intensity", level=80)
            r = e.act("clear_heads", heads=[heads[0]])
            check("clear_heads: one light released, the others keep their values",
                  r.get("ok") and heads[0] not in e.programmer and heads[1] in e.programmer, str(r))
            check("clear_heads with nothing on it says so", not e.act("clear_heads", heads=[heads[-1]]).get("ok"))
            r = e.act("record_preset", name="Test look")
            num = r.get("n") or (r.get("preset") or {}).get("n")
            check("delete_preset: gone", e.act("delete_preset", n=num).get("ok") and not any(p["n"] == num for p in e.presets), str(r))
            check("delete_preset: an unknown one is refused", not e.act("delete_preset", n=999).get("ok"))
            check("remap_heads runs", e.act("remap_heads").get("ok"))
            check("get_limits lists the heads", len(e.act("get_limits", heads=heads[:2]).get("heads") or []) == 2)
            r = e.act("venue_stage", width=6, depth=3)
            check("venue_stage: the stage's size", r.get("ok") and e.venue["stage"]["width"] == 6, str(e.venue.get("stage")))
            check("venue_stage remove", e.act("venue_stage", remove=True).get("ok") and not e.venue.get("stage"))
            check("venue_underlay with no plan uploaded says so", "upload" in (e.act("venue_underlay", x=1).get("error") or ""))
            mover = next(h for h in e.patch if "pan" in h["map"])
            check("motion_get: the axes", set(e.act("motion_get", head=mover["head_no"]).get("axes") or []) == {"pan", "tilt"})
            check("fx_status: armed or not", "sfx" in e.act("fx_status"))
            check("quick_fx_defaults: a page that has buttons isn't overwritten unasked",
                  "already has buttons" in (e.act("quick_fx_defaults", page=4).get("error") or ""))
            check("quick_fx_defaults: no effects patched, says so",
                  "no lasers or special effects" in (e.act("quick_fx_defaults", page=4, replace=True).get("error") or ""))
            from app import fixlib
            fog = fixlib.search("Antari Z-1000")
            if fog:
                got = fixtures.store_parsed(db, fixlib.load(fog[0]["src"], fog[0]["key"]), f"{fog[0]['src']}:{fog[0]['key']}")
                e.act("add_heads", fixture_id=got["imported"][0]["fixture_id"], qty=1)
            r = e.act("quick_fx_defaults", page=4, replace=True)
            check("quick_fx_defaults: the effect buttons (the club has haze and a laser)",
                  r.get("ok") and any(b["page"] == 4 and b.get("kind") == "arm" for b in e.quick), str(r.get("error")))
            bpm0 = e._tempo().bpm
            check("tempo_nudge: +2 BPM", e.act("tempo_nudge", bpm=2).get("ok") and abs(e._tempo().bpm - bpm0 - 2) < 0.01)
            check("tempo_sync: now is beat 1", e.act("tempo_sync").get("ok"))
            check("media_delete: an unknown picture is refused", not e.act("media_delete", id="nope").get("ok"))
            check("rig_pieces: the library", len(e.act("rig_pieces").get("pieces") or []) > 3)
            # a rebuilt room: the lights go onto its new rigging
            e.act("clear_programmer")
            r = e.act("venue_shape", shape="L", layout=True)
            new_ids = {x["id"] for x in e.venue["rigging"]}
            on = [h for h in e.patch if (h.get("mount") or {}).get("rig") in new_ids]
            was = sum(1 for _ in heads)
            check("venue_build: the lights move onto the new trusses (not left floating)",
                  len(on) >= was - 2 and "moved onto the new rigging" in r.get("summary", ""), r.get("summary"))
            check("...one undo puts the old room and mounts back", e.act("undo").get("ok")
                  and all((h.get("mount") or {}).get("rig") not in new_ids for h in e.patch if h.get("mount")))
        finally:
            e.shutdown()
    st = (ROOT / "web" / "js" / "stage" / "stage.js").read_text(encoding="utf-8")
    check("3D auto quality counts very slow frames (not only < 200 ms) and goes below half",
          "dt > 2000" in st and "MIN = 0.35" in st and "q.lite" in st)
    dl = (ROOT / "web" / "app" / "dialogs.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "app" / "app.css").read_text(encoding="utf-8")
    check("Output's choices have room (not cut off)", "out-grid" in dl and ".out-grid" in css)
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    fx = (ROOT / "web" / "app" / "fixtures.js").read_text(encoding="utf-8")
    check("the groups fold away for more fixture rows, remembered", 'id="grp-fold"' in html and "jarvis.groupsFolded" in fx)


def test_sweep3_adds() -> None:
    """Sweep 3's additions: an AI pack picked in the page (streamed, checked),
    MVR lights whose GDTF isn't in the file matched from the library, lights
    that mix by hue + saturation (and white-only ones) shown in their colour,
    the timeline's music in the video, and OSC out (cues send it; GO, master,
    blackout reported; saved with the show)."""
    print("Sweep 3 adds: AI pack upload, MVR library match, hue/sat lights, video sound, OSC out")
    import io
    import json as _json
    import shutil
    import socket as _socket
    import subprocess
    import zipfile

    from app import config as cfg, engine as eng, fixlib, localai, mvr, osc
    # -- an AI pack, streamed in
    saved = cfg.DATA
    with tempfile.TemporaryDirectory() as td:
        cfg.DATA = Path(td)
        try:
            blob = b"GGUF" + b"\1" * 5000
            st = localai.receive_pack(io.BytesIO(blob), len(blob), "C:\\\\USB\\\\my-pack.gguf")
            check("an AI pack picked in the page arrives whole, under its own name",
                  [m["file"] for m in st["models"]] == ["my-pack.gguf"] and (cfg.DATA / "ai" / "my-pack.gguf").read_bytes() == blob)
            for data, name, why in ((b"NOPE" + b"\0" * 20, "x.gguf", "not a model"), (blob, "x.exe", "not .gguf"),
                                    (blob[:2000], "cut.gguf", "cut short")):
                try:
                    localai.receive_pack(io.BytesIO(data), len(blob) if why == "cut short" else len(data), name)
                    ok = False
                except ValueError:
                    ok = True
                check(f"an AI pack is refused: {why} - and nothing half-copied is left", ok
                      and not list((cfg.DATA / "ai").glob("*.part")) and not (cfg.DATA / "ai" / "cut.gguf").exists())
        finally:
            cfg.DATA = saved
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    ai = (ROOT / "web" / "app" / "aisettings.js").read_text(encoding="utf-8")
    check("Settings -> AI: Choose an AI pack… streams it to the desk", '"/api/ai/pack"' in main and "Choose an AI pack" in ai and "x.send(f)" in ai)
    # -- MVR, a light whose GDTF isn't inside
    check("an MVR's GDTF name gives the maker and the model",
          mvr._spec_words("Chauvet@Intimidator Spot 260@rev2.gdtf") == ("Chauvet", "Intimidator Spot 260"))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            hit = fixlib.search("Intimidator Spot 260")
            if hit:
                maker = hit[0]["manufacturer"]
                fx = (f'<Fixture name="Spot" uuid="s1"><Matrix>{{1,0,0}}{{0,1,0}}{{0,0,1}}{{0,2000,5000}}</Matrix>'
                      f'<GDTFSpec>{maker}@{hit[0]["model"]}.gdtf</GDTFSpec><GDTFMode>x</GDTFMode>'
                      f'<Addresses><Address break="0">1.1</Address></Addresses></Fixture>')
                xml = ('<?xml version="1.0"?><GeneralSceneDescription verMajor="1" verMinor="6"><Scene><Layers><Layer name="L" uuid="L">'
                       f'<ChildList>{fx}</ChildList></Layer></Layers></Scene></GeneralSceneDescription>')
                b = io.BytesIO()
                with zipfile.ZipFile(b, "w") as z:
                    z.writestr("GeneralSceneDescription.xml", xml)
                r = mvr.import_into(e, b.getvalue(), tmp / "gdtf")
                check("an MVR light whose GDTF isn't in the file: matched from the library, and said",
                      r["ok"] and len(e.patch) == 1 and any("from the" in n for n in r["notes"]), str(r))
            # -- hue + saturation lights
            src = fixlib.search("Source Four LED Series 2 Lustr")
            if src:
                got = fixtures.store_parsed(db, fixlib.load(src[0]["src"], src[0]["key"]), f"{src[0]['src']}:{src[0]['key']}")
                fid = got["imported"][0]["fixture_id"]
                h = e._head(e.act("add_heads", fixture_id=fid, qty=1, mode="HSI")["heads"][0])
                look = lambda: next(x for x in e._looks() if x["n"] == h["head_no"])["hex"]   # noqa: E731
                e.act("select_heads", heads=[h["head_no"]])
                e.act("set_intensity", level=100)
                white = look()
                e.act("set_colour", hex="#ff0000")
                red = look()
                e.act("set_colour", hex="#0000ff")
                blue = look()
                check("a hue + saturation light: white, red and blue in the 3D (not 'unknown' grey)",
                      (white, red, blue) == ("#ffffff", "#ff0000", "#0000ff"), f"{white} {red} {blue}")
                e.act("clear_programmer")
                e.act("locate")
                check("...and Locate is white", look() == "#ffffff", look())
                e.act("clear_programmer")
                s = e._head(e.act("add_heads", fixture_id=fid, qty=1, mode="Studio")["heads"][0])
                hx = next(x for x in e._looks() if x["n"] == s["head_no"])["hex"]
                check("a white-only light with a colour temperature: warm-to-cool white, not grey", hx != "#cbd5e1" and e._cct_role(s), hx)
            # -- OSC out
            rx = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            rx.bind(("127.0.0.1", 0))
            rx.settimeout(2)
            port = rx.getsockname()[1]
            try:
                check("OSC out: an address that isn't one is refused", not e.act("osc_send", address="go").get("ok"))
                check("...with nowhere set, says where to set it", "OSC out" in (e.act("osc_send", address="/go").get("error") or ""))
                e.act("osc_out", host="127.0.0.1", port=port, feedback=True)
                e.act("osc_send", address="/go", value=1)
                check("a message arrives where OSC out points", osc.parse(rx.recvfrom(2048)[0]) == [("/go", [1])])
                e.act("select_heads", heads=[e.patch[0]["head_no"]])
                e.act("set_intensity", level=100)
                e.act("record_cue", playback=1)
                e.act("cue_set", playback=1, cue=1, actions=[{"action": "osc_send", "args": {"address": "/cue/5/start"}}])
                e.act("cue_go", playback=1, cue=1)
                got = [osc.parse(rx.recvfrom(2048)[0])[0] for _ in range(2)]
                check("a cue's Send OSC action goes out, and GO is reported (/jarvis/go list cue)",
                      ("/cue/5/start", []) in got and any(a == "/jarvis/go" and args[:1] == [1] for a, args in got), str(got))
                e.act("master", level=50)
                check("the master is reported 0-1", osc.parse(rx.recvfrom(2048)[0]) == [("/jarvis/master", [0.5])])
                saved_show = _json.loads(e._autosave_payload())
                check("OSC out is saved with the show", (saved_show.get("osc_out") or {}).get("port") == port, str(saved_show.get("osc_out")))
            finally:
                rx.close()
        finally:
            e.shutdown()
    # -- the video with the timeline's music
    node = shutil.which("node")
    if node:
        js = r"""
const V = await import(process.argv[1]);
console.log(JSON.stringify({ av: V.pickFormat((m) => true, true), plain: V.pickFormat((m) => true),
  noav: V.pickFormat((m) => !m.includes(","), true) }));
"""
        r = subprocess.run([node, "--input-type=module", "-e", js, (ROOT / "web" / "app" / "videoplan.js").as_uri()],
                           capture_output=True, text=True, timeout=30)
        got = _json.loads(r.stdout or "{}") if r.returncode == 0 else {}
        check("the video picks a format with sound when the timeline has music (and picture-only when the browser can't)",
              (got.get("av") or {}).get("audio") and not (got.get("plain") or {}).get("audio")
              and not (got.get("noav") or {}).get("audio"), str(got))
    vr = (ROOT / "web" / "app" / "videorec.js").read_text(encoding="utf-8")
    check("...the timeline's music is added to the recording", "timelineAudioStream" in vr and "addTrack" in vr)
