"""Self-test suites, part 9: the virtual node and the MIDI monitor."""
from __future__ import annotations

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
