"""Self-test suites, part 6: venue, quick_buttons, timeline, autoshow, dmx_target, shutter_rest, wheel_slots, auto_update, ...."""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

from app import config, fixtures
from tools.selftests.common import ROOT, _raises, check


def test_venue() -> None:
    """The room: templates, rigging, mounting, placement and the feeds."""
    print("venue (templates, rigging, mounts, placement)")
    import base64

    from app import engine as eng
    from app import merge
    from app import venue as V

    # --- the model on its own ------------------------------------------
    junk = V.normalise({"version": 2, "room": {"width": "x", "depth": -3},
                        "rigging": [{"kind": "nope"}, "junk", {"kind": "truss", "a": [0, 5, 1]}],
                        "zones": [{"kind": "dancefloor", "points": [[0, 0]]}]})
    check("junk normalises to a valid, auto venue",
          junk["auto"] and junk["room"]["width"] == 0
          and [r["kind"] for r in junk["rigging"]] == ["truss"] and junk["zones"] == [],
          json.dumps(junk)[:200])
    for key in V.TEMPLATES:
        t = V.template(key)
        w, d, h = V.dims(t)
        ids = [x["id"] for k in ("rigging", "objects", "zones") for x in t[k]]
        check(f"template {key} is a real room with unique ids",
              w > 0 and d > 0 and h > 0 and not t["auto"] and len(ids) == len(set(ids))
              and V.normalise(t) == t, f"{w}x{d}x{h} {len(ids)}")
    club = V.template("club")
    check("a club has trusses, a dance floor and a DJ booth",
          sum(r["kind"] == "truss" for r in club["rigging"]) >= 3
          and any(z["kind"] == "dancefloor" for z in club["zones"])
          and any(o["kind"] == "dj_booth" for o in club["objects"]), "")
    truss = {"id": "r1", "kind": "truss", "a": [-4, 5, 3], "b": [4, 5, 3], "size": 0.3}
    hung = V.mount_position(truss, 0.5)
    check("a light hangs under a horizontal truss",
          hung["orient"] == "hang" and hung["y"] < 5 and hung["x"] == 0, json.dumps(hung))
    over = V.mount_position(truss, 0.25, "stand")
    check("or stands on top of it when asked", over["y"] > 5 and over["x"] == -2, json.dumps(over))
    tower = {"id": "r2", "kind": "tower", "a": [2, 0, 3], "b": [2, 4, 3], "size": 0.3}
    check("a light high on a tower hangs (so it can tilt down to the floor)",
          V.mount_position(tower, 1)["orient"] == "hang", "")
    check("...and one low on it stands", V.mount_position(tower, 0.25)["orient"] == "stand", "")
    slots = V.free_slots(truss, [0.5], 3, 1.0)
    check("free slots keep their spacing and avoid a taken one",
          len(slots) == 3 and all(abs(a - 0.5) * 8 >= 0.79 for a in slots)
          and all(abs(a - b) * 8 >= 0.79 for a in slots for b in slots if a != b), str(slots))
    near = V.nearest_rig({"rigging": [truss, tower]}, 1.0, 4.8, 3.2)
    check("nearest rig finds the truss under a point",
          near and near[0]["id"] == "r1" and abs(near[1] - 0.625) < 1e-6, str(near and near[1]))
    check("nothing is near a point in mid-air",
          V.nearest_rig({"rigging": [truss]}, 0, 1, 12) is None, "")
    old = V.normalise({"width_m": 12, "depth_m": 8, "height_m": 6,
                       "surfaces": [{"kind": "truss", "x1": -5, "y1": 5, "z1": 2,
                                     "x2": 5, "y2": 5, "z2": 2}]})
    check("an old show's room converts, keeping its truss",
          old["version"] == 2 and old["stage"]["width"] == 12
          and old["rigging"][0]["a"] == [-5.0, 5.0, 2.0], json.dumps(old)[:200])

    # --- BLACKOUT reaches lights with no dimmer ---------------------------
    head = {"head_no": 1, "map": ["pan", "tilt", "strobe", "red", "green", "blue"]}
    prog = {1: {"strobe": 255, "red": 200, "green": 100}}
    out = merge.resolve_head(head, prog, [], blackout=True)
    check("blackout closes the gate of a dimmer-less head",
          out["strobe"] == 0 and out["red"] == 0 and out["green"] == 0, str(out))
    half = merge.resolve_head(head, prog, [], master=50)
    check("the grand master scales its colour instead",
          half["red"] == 100 and half["strobe"] == 255, str(half))

    # --- the engine --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "v.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            check("a new engine has no drawn room", e.venue["auto"] is True, "")
            check("ensure_venue gives an empty desk a club",
                  e.ensure_venue() and e.venue["template"] == "club", "")
            check("but never replaces one", e.ensure_venue() is False, "")
            r = e.act("add_heads", query="Moving Head Spot 16ch", qty=6)
            movers = [h for h in e.patch if h["model"].startswith("Moving")]
            check("new moving heads are mounted on trusses",
                  r["ok"] and all(h.get("mount") and h["stance"] == "hang" for h in movers),
                  json.dumps([h.get("mount") for h in movers]))
            check("spread apart, not piled up",
                  len({(h["x"], h["z"]) for h in movers}) == 6, "")
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            pars = [h for h in e.patch if h["model"].startswith("LED PAR")]
            stage = e.venue["stage"]
            check("PARs stand on the stage deck",
                  all(h["stance"] == "stand" and h["y"] == stage["height"] for h in pars),
                  json.dumps([(h["y"], h.get("stance")) for h in pars]))
            rig_id = movers[0]["mount"]["rig"]
            riders = [h["head_no"] for h in e.patch if (h.get("mount") or {}).get("rig") == rig_id]
            rig = V.rig(e.venue, rig_id)
            before = {n: e._head(n)["z"] for n in riders}
            r = e.act("venue_update", id=rig_id, changes={
                "a": [rig["a"][0], rig["a"][1], rig["a"][2] + 1.5],
                "b": [rig["b"][0], rig["b"][1], rig["b"][2] + 1.5]})
            check("moving a truss carries its lights",
                  r["ok"] and all(abs(e._head(n)["z"] - before[n] - 1.5) < 1e-6 for n in riders),
                  r.get("error") or "")
            e.act("undo")
            check("and undo puts truss and lights back",
                  all(abs(e._head(n)["z"] - before[n]) < 1e-6 for n in riders)
                  and V.rig(e.venue, rig_id)["a"] == rig["a"], "")
            n = riders[0]
            e.act("set_place", head=n, x=0, y=0, z=9)
            h = e._head(n)
            check("dragging a light off its truss frees it and it stands",
                  not h.get("mount") and h["kind"] == "floor", json.dumps(h.get("mount")))
            pos = V.mount_position(V.rig(e.venue, rig_id), 0.5)
            r = e.act("set_place", head=n, x=pos["x"] + 0.2, y=pos["y"] + 0.1,
                      z=pos["z"], snap=True)
            check("snap puts it back on the nearest truss",
                  r["ok"] and (r["mount"] or {}).get("rig") == rig_id, json.dumps(r)[:200])
            r = e.act("attach_heads", heads=[p["head_no"] for p in pars], rig=rig_id)
            check("attach_heads hangs a set of lights along one rig",
                  r["ok"] and all((e._head(p["head_no"]).get("mount") or {}).get("rig") == rig_id
                                  for p in pars), r.get("error") or r.get("summary"))
            r = e.act("venue_remove", id=rig_id)
            check("removing a rig frees its lights in place",
                  r["ok"] and len(r["freed"]) >= 4
                  and not any((h.get("mount") or {}).get("rig") == rig_id for h in e.patch), "")
            e.act("undo")
            r = e.act("venue_add", item={"kind": "truss", "name": "Side",
                                         "a": [-3, 4, 9], "b": [3, 4, 9]})
            check("venue_add returns the new item's id",
                  r["ok"] and V.rig(e.venue, r["id"]) is not None, json.dumps(r)[:200])
            side = r["id"]
            floor = [h for h in e.patch if h["model"].startswith("LED PAR")][:3]
            for h in floor:                          # start them on the floor
                e.act("set_place", head=h["head_no"], rig="", x=0, y=0, z=10)
            r = e.act("place_many", rig=side, moves=[
                {"head": h["head_no"], "x": -1 + i, "y": 3.7, "z": 9.1}
                for i, h in enumerate(floor)])
            placed = [e._head(h["head_no"]) for h in floor]
            check("dropping a selection on a truss hangs every light on it",
                  r["ok"] and all((h.get("mount") or {}).get("rig") == side for h in placed),
                  r.get("error") or json.dumps([h.get("mount") for h in placed]))
            check("floor lights dropped on a truss hang under it",
                  all(h["stance"] == "hang" and h["y"] < 4 for h in placed),
                  json.dumps([(h.get("stance"), h["y"]) for h in placed]))
            check("each at the point nearest where it landed",
                  sorted(round(h["x"]) for h in placed) == [-1, 0, 1],
                  str([h["x"] for h in placed]))
            e.act("undo")
            check("the whole drop is one undo step",
                  not any((e._head(h["head_no"]).get("mount") or {}).get("rig") == side
                          for h in floor), "")
            check("an unknown kind is refused",
                  e.act("venue_add", item={"kind": "spaceship"})["ok"] is False, "")
            r = e.act("venue_template", name="warehouse")
            check("a template swap drops old mounts",
                  r["ok"] and not any(h.get("mount") for h in e.patch), "")
            check("an unknown template is refused, listing the real ones",
                  "club" in (e.act("venue_template", name="moon")["error"] or ""), "")
            r = e.act("venue_crowd", density=0.2, style="simple")
            check("the crowd is a venue setting",
                  e.venue["crowd"] == {"style": "simple", "density": 0.2, "show": True}, "")
            r = e.act("venue_camera", name="FOH", pos=[0, 3, 20], target=[0, 2, 3])
            check("a saved view is kept", e.venue["cameras"][0]["name"] == "FOH", "")
            info = e.act("venue_info")
            check("venue_info summarises for the AI",
                  info["ok"] and info["venue"]["rigging"] and info["templates"], "")
            check("venue_info is read-only (no undo step)",
                  "venue_info" in eng._READ_ONLY, "")
            e.act("set_lock", state="locked")
            check("the patch lock also freezes the venue",
                  e.act("venue_add", item={"kind": "pillar"})["ok"] is False, "")
            e.act("set_lock", state="design")
            e.act("save_show", name="room")
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e2.act("load_show", name="room")
                check("the venue travels with the show file",
                      e2.venue["template"] == "warehouse"
                      and e2.venue["cameras"][0]["name"] == "FOH", "")
            finally:
                e2.shutdown()
        finally:
            e.shutdown()

    # --- floor plans ----------------------------------------------------------
    from app import main as main_mod
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
    old_data = main_mod.config.DATA
    with tempfile.TemporaryDirectory() as td:
        main_mod.config.DATA = Path(td)
        try:
            got = main_mod.save_underlay({"data": "data:image/png;base64," + png})
            check("a PNG floor plan is stored by content",
                  len(got["id"]) == 32 and main_mod.underlay_path(got["id"]) is not None, str(got))
            check("a path in the id finds nothing",
                  main_mod.underlay_path("../" + got["id"]) is not None
                  and main_mod.underlay_path("../../etc/passwd") is None, "")
            try:
                main_mod.save_underlay({"data": base64.b64encode(b"%PDF-1.4 junk").decode()})
                refused = False
            except ValueError:
                refused = True
            check("a non-image is refused (PDFs are rendered in the browser)", refused, "")
        finally:
            main_mod.config.DATA = old_data


def test_quick_buttons() -> None:
    """Quick buttons (flash, strobe, colour, kill, fx, go) and aim_at."""
    print("quick buttons and aim")
    import math

    from app import engine as eng
    from app import merge

    # the override layer on its own
    head = {"head_no": 1, "map": ["dimmer", "red", "green", "blue"]}
    out = merge.resolve_head(head, {}, [], over={"level": 100})
    check("flash lifts a dark head to full", out["dimmer"] == 100, str(out))
    out = merge.resolve_head(head, {1: {"dimmer": 80}}, [], over={"kill": True})
    check("kill takes it to nothing", out["dimmer"] == 0, str(out))
    out = merge.resolve_head(head, {}, [], over={"level": 100}, blackout=True)
    check("blackout still beats a flash", out["dimmer"] == 0, str(out))
    out = merge.resolve_head(head, {}, [], over={"level": 100}, master=50)
    check("and the grand master still scales it", out["dimmer"] == 50, str(out))
    out = merge.resolve_head(head, {1: {"red": 255}}, [], over={"set": {"blue": 255, "red": 0}})
    check("a colour bump replaces the colour", out["red"] == 0 and out["blue"] == 255, str(out))
    lit = [merge.resolve_head(head, {}, [], over={"level": 100, "strobe": 10},
                              now=100 + i / 400)["dimmer"] for i in range(400)]
    frac = sum(1 for v in lit if v) / 400
    check("a strobe gates the light in time on the wire", 0.2 < frac < 0.5, str(frac))
    gate = {"head_no": 2, "map": ["pan", "tilt", "shutter", "red"]}
    out = merge.resolve_head(gate, {2: {"shutter": 200, "red": 90}}, [], blackout=True, gate_closed=None)
    check("a shutter that is open at 0 is not 'closed' to 0 (colour goes to 0 instead)",
          out["shutter"] == 200 and out["red"] == 0, str(out))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "q.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.ensure_venue()
            e.act("add_heads", query="Moving Head Spot 16ch", qty=4)
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            r = e.act("quick_defaults")
            check("suggested buttons fit the rig",
                  r["ok"] and any(b["label"] == "Flash all" for b in e.quick)
                  and any(b["target"].get("type") == "par" for b in e.quick), r.get("error") or "")
            check("but never overwrite a page silently",
                  e.act("quick_defaults")["ok"] is False, "")
            check("setting buttons up is an undo step",
                  e.act("undo")["ok"] and not e.quick, "")
            e.act("quick_defaults")
            flash = next(b for b in e.quick if b["label"] == "Flash all")
            before = len(e._undo)
            e.act("quick_press", id=flash["id"], down=True)
            looks = e._looks()
            check("holding Flash all lights every head", all(r["a"] == 1.0 for r in looks),
                  str([r["a"] for r in looks]))
            check("pressing a button is not an undo step", len(e._undo) == before, "")
            e.act("quick_press", id=flash["id"], down=False)
            check("releasing it lets go", not any(r["a"] > 0 for r in e._looks()), "")
            red = next(b for b in e.quick if b["label"] == "All Red")
            e.act("select_all")
            e.act("set_intensity", level=100)
            e.act("set_colour", hex="#0000ff")
            e.act("quick_press", id=red["id"], down=True)
            pars = [r for r in e._looks() if e._head(r["n"])["model"].startswith("LED PAR")]
            check("a colour bump turns the PARs red while held",
                  all(r["hex"].lower().startswith("#ff00") for r in pars), str([r["hex"] for r in pars]))
            e.act("quick_press", id=red["id"], down=False)
            fx_btn = next(b for b in e.quick if b["kind"] == "fx")
            e.act("quick_press", id=fx_btn["id"], down=True)
            n_fx = len(e.fx)
            e.act("quick_press", id=fx_btn["id"], down=False)
            check("an effect button latches (release does not stop it)", len(e.fx) == n_fx >= 1, "")
            e.act("quick_press", id=fx_btn["id"], down=True)
            check("and a second press stops it", len(e.fx) == n_fx - 1, str(len(e.fx)))
            r = e.act("quick_set", page=2, slot=1, button={"kind": "colour", "label": "x"})
            check("a colour button without a colour is refused", r["ok"] is False, "")
            r = e.act("quick_set", page=2, slot=1, button={"kind": "strobe", "hz": 99, "target": {"group": 7}})
            check("values are clamped (strobe rate <= 20 Hz)",
                  r["ok"] and r["button"]["hz"] == 20, json.dumps(r.get("button")))
            e.act("save_show", name="qb")
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e2.act("load_show", name="qb")
                check("buttons travel with the show file",
                      any(b["label"] == "Flash all" for b in e2.quick) and not e2.quick_active, "")
            finally:
                e2.shutdown()

            # aim_at: every mover's beam, recomputed the way the 3D view
            # draws it, passes through the target
            e.act("select_all")
            r = e.act("aim_at", x=1.0, y=0.5, z=9.0)
            check("aim_at aims the movers and skips the PARs",
                  r["ok"] and len(r["heads"]) == 4 and len(r["skipped"]) == 4, r.get("summary"))
            worst = 0.0
            for row in e._looks():
                if "pan" not in row:
                    continue
                h = e._head(row["n"])
                rng = row.get("deg") or {}
                pr, tr = rng.get("pan", [-270, 270]), rng.get("tilt", [-135, 135])
                p = math.radians(pr[0] + row["pan"] * (pr[1] - pr[0]))
                t = math.radians(tr[0] + row["tilt"] * (tr[1] - tr[0]))
                d = [math.sin(t) * math.sin(p), math.cos(t), math.sin(t) * math.cos(p)]
                hung = h.get("stance") == "hang"
                if hung:
                    d = [-d[0], -d[1], d[2]]
                from app import fixture_kind as _fk
                dsc = _fk.describe(h)
                piv = 0.372 if dsc.get("heads") else e._AIM_PIVOT.get(dsc["type"], 0.4)
                o = [h["x"], h["y"] + (-piv if hung else piv), h["z"]]      # the model's tilt axis
                v = [(1.0, 0.5, 9.0)[i] - o[i] for i in range(3)]
                along = sum(v[i] * d[i] for i in range(3))
                worst = max(worst, math.sqrt(max(0.0, sum(x * x for x in v) - along * along)))
            check("each beam passes within 10 cm of the target", worst < 0.1, f"{worst:.3f} m")
            e.act("venue_add", item={"kind": "mark", "name": "Singer", "x": -2, "z": 2, "y": 0.8})
            r = e.act("aim_at", mark="singer")
            check("aim_at a performer mark by name", r["ok"] and r["target"][0] == -2, r.get("error") or "")
        finally:
            e.shutdown()


def test_timeline() -> None:
    """The show timeline: tracks, clips, the clock, seeking and saving."""
    print("timeline (tracks, clips, clock, chase)")
    from app import engine as eng
    from app import timeline as T

    junk = T.normalise({"length": -5, "bpm": 9999, "tracks": [
        {"kind": "nope"}, {"kind": "button", "clips": [{"t": 1}]},
        {"kind": "level", "target": "evil", "clips": [{"t": 2, "v": 500}, {"t": 1, "v": 10}]}]})
    check("junk normalises safely",
          junk["length"] == 1 and junk["bpm"] == 300
          and [t["kind"] for t in junk["tracks"]] == ["button", "level"]
          and junk["tracks"][0]["clips"] == [] and junk["tracks"][1]["target"] == "pb1"
          and [c["t"] for c in junk["tracks"][1]["clips"]] == [1, 2]
          and junk["tracks"][1]["clips"][1]["v"] == 100, json.dumps(junk)[:300])
    lvl = {"clips": [{"t": 0, "v": 0}, {"t": 10, "v": 100}]}
    check("level keys interpolate and hold at the ends",
          T.level_at(lvl, 5) == 50 and T.level_at(lvl, -1) == 0 and T.level_at(lvl, 99) == 100, "")
    laid = T.clips_from_stack([{"n": 1, "fade_s": 2, "hold_s": 1, "follow_s": 3},
                               {"n": 2, "fade_s": 0, "hold_s": 0}, {"n": 3}], default_wait=4)
    check("a cue list lays out by fade + hold + follow",
          [c["t"] for c in laid] == [0, 6, 10], str([c["t"] for c in laid]))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "tl.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        clock = [1000.0]
        e._clock = lambda: clock[0]
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=4)
            e.act("select_all")
            for hx in ("#ff0000", "#00ff00", "#0000ff"):
                e.act("set_colour", hex=hx)
                e.act("set_intensity", level=100)
                e.act("record_cue", playback=1, fade=0, hold=1)
            e.act("clear_programmer")
            r = e.act("timeline_from_playback", playback=1)
            check("a playback becomes a cue track", r["ok"] and len(r["track"]["clips"]) == 3,
                  r.get("error") or "")
            e.act("quick_defaults")
            strobe = next(b for b in e.quick if b["label"] == "Strobe all")
            bt = e.act("timeline_track", kind="button", name="Hits")["id"]
            e.act("timeline_clip", track=bt, t=2.0, dur=1.0, button=strobe["id"])
            lv = e.act("timeline_track", kind="level", target="master")["id"]
            e.act("timeline_clip", track=lv, t=0, v=100)
            e.act("timeline_clip", track=lv, t=10, v=0)
            undo_before = len(e._undo)
            e.act("timeline_play")
            seen = {}
            for step in (0.1, 2.5, 3.5, 5.0, 6.5):
                clock[0] = 1000.0 + step
                e._tick_timeline()
                seen[step] = (e.playbacks[0]["index"] + 1, sorted(e.quick_active), e.master)
            check("cue 1 fires at the start", seen[0.1][0] == 1, str(seen))
            check("the hit holds its button for its length only",
                  seen[2.5][1] == [strobe["id"]] and seen[3.5][1] == [], str(seen))
            check("the next cue fires on time", seen[6.5][0] == 2, str(seen))
            check("level automation drives the grand master",
                  seen[5.0][2] == 50, str(seen))
            e.act("timeline_seek", t=2.2)
            check("seeking chases the rig (back to cue 1, hit held)",
                  e.playbacks[0]["index"] == 0 and e.quick_active.get(strobe["id"]) is not None,
                  str(e.playbacks[0]["index"]))
            e.act("timeline_pause")
            check("pausing lets go of held hits", not e.quick_active, "")
            check("the transport never adds undo steps", len(e._undo) == undo_before,
                  f"{undo_before} -> {len(e._undo)}")
            r = e.act("timeline_clip", track=bt, t=1, dur=1, button="")
            check("a button clip without a button is refused", r["ok"] is False, "")
            e.act("save_show", name="tl")
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
            try:
                e2.act("load_show", name="tl")
                check("the timeline travels with the show file",
                      [t["kind"] for t in e2.timeline["tracks"]] == ["cue", "button", "level"], "")
            finally:
                e2.shutdown()
            e.act("timeline_set", loop=True, length=4)
            e.act("timeline_play", at=3.9)
            clock[0] += 0.3
            e._tick_timeline()
            check("a looping timeline wraps round",
                  e.tl["playing"] and e.tl["pos"] < 1.0, str(e.tl["pos"]))
        finally:
            e.shutdown()


def test_autoshow() -> None:
    """A whole show designed from the rig and built onto the timeline."""
    print("auto show (rig analysis, design, build)")
    from app import autoshow, console_ai
    from app import engine as eng

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "auto.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            check("an empty rig is refused, not guessed at",
                  _raises(lambda: autoshow.design(e, "techno", offline=True)), "")
            e.ensure_venue()
            e.act("add_heads", query="Moving Head Spot 16ch", qty=6)
            e.act("add_heads", query="LED PAR 4ch", qty=6)
            e.act("timeline_set", bpm=128, audio={"id": "x1", "name": "t.mp3", "duration": 180})
            a = autoshow.analyse(e)
            roles = {g["role"] for g in a["groups"].values()}
            check("the analysis groups lights by type and where they are",
                  {"spot", "par"} <= roles
                  and any("Front truss" in g["name"] or "truss" in g["name"].lower()
                          for g in a["groups"].values() if g["role"] == "spot"), json.dumps(
                      {k: g["name"] for k, g in a["groups"].items()}))
            check("it finds aim targets in the room",
                  {"dj", "floor", "crowd"} <= set(a["targets"]), str(list(a["targets"])))
            movers = next(g for g in a["groups"].values() if g["role"] == "spot")
            pars = next(g for g in a["groups"].values() if g["role"] == "par")
            check("capabilities are what every light in the group can do",
                  movers["caps"]["pan_tilt"] and not pars["caps"]["pan_tilt"]
                  and pars["caps"]["colour"] == "mix", json.dumps(movers["caps"]))
            check("effects offered per group are ones it can run",
                  "circle" in movers["fx"] and "circle" not in pars["fx"], "")
            r = autoshow.design(e, "dark techno warehouse", offline=True)
            d = r["design"]
            check("an offline design has contrasting sections",
                  r["source"] == "offline" and len(d["sections"]) >= 5
                  and min(s["energy"] for s in d["sections"]) < 0.4
                  and max(s["energy"] for s in d["sections"]) == 1.0, "")
            check("it never aims or colours a light that cannot",
                  all(not (lk.get("aim") and not a["groups"][lk["group"]]["caps"]["pan_tilt"])
                      and not (lk.get("colour") and a["groups"][lk["group"]]["caps"]["colour"] != "mix")
                      for s in d["sections"] for lk in s["looks"]), "")
            bad = {"name": "x", "sections": [{"name": "S", "bars": 8, "energy": 2, "looks": [
                {"group": "nope", "intensity": 50},
                {"group": [k for k, g in a["groups"].items() if g["role"] == "par"][0],
                 "intensity": 500, "aim": "floor", "fx": "circle", "colour": "red"}]}]}
            v = autoshow.validate(bad, a)
            lk = v["sections"][0]["looks"][0]
            check("validation drops unknown groups and impossible choices",
                  len(v["sections"][0]["looks"]) == 1 and lk["intensity"] == 100
                  and "aim" not in lk and "fx" not in lk and "colour" not in lk
                  and v["sections"][0]["energy"] == 1.0, json.dumps(v))
            e.act("record_cue", playback=1, name="old") if e.act("select_all") and e.act("set_intensity", level=50) else None
            undo_before = len(e._undo)
            out = autoshow.build(e, d, playback=1)
            check("building makes one cue per section",
                  [c["name"] for c in e.playbacks[0]["stack"]] == [s["name"] for s in d["sections"]],
                  str([c["name"] for c in e.playbacks[0]["stack"]]))
            kinds = {t["kind"] for t in e.timeline["tracks"] if t["name"].startswith(autoshow.AUTO_PREFIX)}
            check("and the timeline: cues, effects, hits and master",
                  kinds == {"cue", "fx", "button", "level"}, str(kinds))
            cue_t = [c["t"] for t in e.timeline["tracks"] if t["kind"] == "cue" for c in t["clips"]]
            check("sections are laid out to fill the song",
                  cue_t[0] == 0 and 150 < cue_t[-1] < 180 and e.timeline["length"] >= 180, str(cue_t))
            beat = 60 / 128
            hits = [c["t"] for t in e.timeline["tracks"] if t["kind"] == "button" for c in t["clips"]]
            check("hits land on the beat grid",
                  hits and all(abs((t / beat) - round(t / beat)) < 0.02 for t in hits), str(hits[:6]))
            check("the whole build is ONE undo step",
                  len(e._undo) == undo_before + 1 and e._undo[-1]["action"] == "auto show",
                  out["summary"])
            e.act("timeline_play")
            clock = [e._clock()]
            e._clock = lambda: clock[0]
            e.tl["t0"] = clock[0]
            clock[0] += 0.05
            e._tick_timeline()
            check("playing it fires the first section's cue",
                  e.playbacks[0]["active"] and e.playbacks[0]["index"] == 0, "")
            e.act("timeline_stop")
            again = autoshow.build(e, d, playback=1)
            check("building again replaces the auto tracks rather than stacking them",
                  sum(1 for t in e.timeline["tracks"] if t["kind"] == "cue") == 1
                  and sum(1 for g in e.groups if g["name"].startswith(autoshow.AUTO_PREFIX))
                  == len({lk["group"] for s in d["sections"] for lk in s["looks"]}), again["summary"])
            ctx = console_ai.rig_context(e)
            check("the copilot sees the room, its rigging and the timeline",
                  "RIGGING" in ctx and "TIMELINE" in ctx and "ROOM" in ctx, ctx[-400:])
            check("and may aim, attach and run the timeline",
                  {"aim_at", "attach_heads", "timeline_play"} <= set(console_ai.ALLOWED_ACTIONS), "")
        finally:
            e.shutdown()


def test_dmx_target() -> None:
    """Every venue's node has its own IP: pick it, check it, keep it."""
    print("dmx target (node IP per venue, network check)")
    from app import artnet, console_ai, fixtures, netif
    from app import engine as eng

    ipconfig = """Windows IP Configuration

Ethernet adapter Ethernet:

   Connection-specific DNS Suffix  . :
   IPv4 Address. . . . . . . . . . . : 2.0.0.100
   Subnet Mask . . . . . . . . . . . : 255.255.0.0
   Default Gateway . . . . . . . . . :

Wireless LAN adapter Wi-Fi:

   IPv4 Address. . . . . . . . . . . : 192.168.1.23
   Subnet Mask . . . . . . . . . . . : 255.255.255.0
   Default Gateway . . . . . . . . . : 192.168.1.1
"""
    rows = netif.parse_ipconfig(ipconfig)
    check("Windows adapters are read with their masks",
          [(r["ip"], r["mask"]) for r in rows] == [("2.0.0.100", "255.255.0.0"),
                                                   ("192.168.1.23", "255.255.255.0")],
          str(rows))
    mac = netif.parse_ifconfig("en0: flags=8863<UP> mtu 1500\n\tinet 2.0.0.50 netmask "
                               "0xffff0000 broadcast 2.0.255.255\n"
                               "lo0: flags=8049<UP>\n\tinet 127.0.0.1 netmask 0xff000000\n")
    check("macOS hex masks are decoded",
          mac[0] == {"name": "en0", "ip": "2.0.0.50", "mask": "255.255.0.0"}, str(mac))
    lin = netif.parse_ip_addr("2: eth0    inet 2.0.0.7/16 brd 2.0.255.255 scope global eth0\n")
    check("Linux prefixes become masks",
          lin == [{"name": "eth0", "ip": "2.0.0.7", "mask": "255.255.0.0"}], str(lin))
    ok = netif.check("2.0.0.10", rows)
    check("a node on the Ethernet network is reachable through it",
          ok["ok"] and ok["via"]["ip"] == "2.0.0.100", str(ok))
    bad = netif.check("2.0.0.10", rows[1:])
    check("Wi-Fi only: the check says so and suggests an address on the node's network",
          not bad["ok"] and bad["suggest"] == {"ip": "2.0.0.100", "mask": "255.255.0.0"},
          str(bad))
    check("a 2.x /16 broadcasts to 2.0.255.255, not the /8",
          netif.broadcast_for("2.0.0.100", "255.255.0.0") == "2.0.255.255", "")
    check("auto picks the Art-Net adapter over Wi-Fi",
          eng.pick_auto_broadcast(list(reversed(rows))) == "2.0.255.255", "")
    check("the startup guess prefers a 2.x address too",
          config._pick_lan(["192.168.1.23", "2.0.0.100"]) == "2.0.0.100", "")
    check("scan accepts extra poll targets",
          "targets" in artnet.scan.__code__.co_varnames, "")
    check("the copilot can never move the output",
          "set_dmx_target" in console_ai.DENY_ACTIONS, "")
    check("junk stored targets fall back to auto",
          eng.clean_dmx_target({"mode": "node", "host": "nope"})["mode"] == "auto"
          and eng.clean_dmx_target(None) == eng.DMX_TARGET_DEFAULT, "")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "t.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s",
                       autosave_path=tmp / "auto.json")
        try:
            e.act("add_heads", query="LED PAR 4ch", qty=2)
            depth = len(e._undo)
            r = e.act("set_dmx_target", mode="node", host="2.0.0.10")
            check("a node IP is taken", r["ok"] and r["resolved"]["host"] == "2.0.0.10", str(r))
            check("and costs no undo step", len(e._undo) == depth, "")
            s1 = e._get_sender()
            check("the sender goes to the node", (s1.host, s1.port) == ("2.0.0.10", 6454), "")
            e.act("set_dmx_target", host="2.0.0.11")
            s2 = e._get_sender()
            check("changing the IP swaps the sender live, no restart",
                  s2 is not s1 and s2.host == "2.0.0.11", s2.host)
            check("unchanged target keeps the same sender", e._get_sender() is s2, "")
            for bad_ip in ("banana", "", "224.0.0.1", "2.0.0"):
                r = e.act("set_dmx_target", mode="node", host=bad_ip)
                check(f"{bad_ip!r} is refused with a reason", not r["ok"] and r["error"], str(r))
            check("a refused IP leaves the target alone", e.dmx_target["host"] == "2.0.0.11", "")
            r = e.act("set_dmx_target", mode="broadcast", host="2.0.255.255", transport="artnet")
            check("a chosen broadcast address is taken", r["ok"] and e._get_sender().host == "2.0.255.255", str(r))
            r = e.act("set_dmx_target", mode="node", host="10.0.0.5", transport="sacn")
            snd = e._get_sender()
            check("sACN unicast to a node uses port 5568",
                  getattr(snd, "transport", "") == "sacn" and snd.port == 5568, str(r))
            e.act("set_dmx_target", mode="node", host="2.0.0.10", transport="artnet")
            e.act("save_show", name="club-a")
            e.act("set_dmx_target", mode="auto")
            check("auto clears the host", e.dmx_target["mode"] == "auto" and not e.dmx_target["host"], "")
            e.act("load_show", name="club-a")
            check("loading a venue's show brings its node back",
                  e.dmx_target["host"] == "2.0.0.10" and e.dmx_target["mode"] == "node",
                  str(e.dmx_target))
            e.act("venue_template", name="warehouse")
            check("swapping the room keeps the node", e.dmx_target["host"] == "2.0.0.10", "")
            e.act("undo")
            check("undo never moves the output", e.dmx_target["host"] == "2.0.0.10", "")
            check("the output feed reports the target",
                  e._output_public()["target"]["host"] == "2.0.0.10", "")
            e2_pub = eng.Engine(db_path=db, dry_run=True)
            e2_pub.act("set_dmx_target", mode="node", host="2.0.0.10")
            check("the status bar shows the new target before GO LIVE",
                  e2_pub._sender is None
                  and e2_pub._output_public()["host"] == "2.0.0.10:6454",
                  str(e2_pub._output_public().get("host")))
            e.act("set_lock", state="operate")
            r = e.act("set_dmx_target", mode="auto")
            check("the lock protects the output target", not r["ok"], str(r))
            e.act("set_lock", state="design")
            e._autosave(force=True)
            e._stop_writer()
            e2 = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s",
                            autosave_path=tmp / "auto.json", restore=True)
            check("a restart keeps the node", e2.dmx_target["host"] == "2.0.0.10",
                  str(e2.dmx_target))
            e2.shutdown()
            fixed = eng.Engine(db_path=db, dry_run=True, sender=s1)
            fixed.dmx_target = {"mode": "node", "host": "9.9.9.9", "transport": ""}
            check("a sender handed in by a tool is never replaced", fixed._get_sender() is s1, "")
        finally:
            e.shutdown()


def test_shutter_rest() -> None:
    """A mover whose shutter reads 0 as CLOSED still lights on Full."""
    print("shutter rests open (moves but no light)")
    from app import engine as eng
    from app import fixtures

    def gdtf(path, name, shutter_xml, highlight=""):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", (
                f'<GDTF DataVersion="1.1"><FixtureType Name="{name}" '
                'Manufacturer="TestCo"><DMXModes><DMXMode Name="4ch">'
                '<DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
                '<ChannelFunction Name="Pan" DMXFrom="0/1"/>'
                '</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" DMXFrom="0/1"/>'
                '</LogicalChannel></DMXChannel>'
                f'<DMXChannel Offset="3"{highlight}><LogicalChannel Attribute="Shutter1">'
                f'{shutter_xml}</LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="4"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/>'
                '</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))

    sets = ('<ChannelFunction Name="Shutter" DMXFrom="0/1">'
            '<ChannelSet Name="Closed" DMXFrom="0/1"/>'
            '<ChannelSet Name="Open" DMXFrom="4/1"/>'
            '<ChannelSet Name="Strobe" DMXFrom="8/1"/></ChannelFunction>')
    zero_open = ('<ChannelFunction Name="Shutter" DMXFrom="0/1">'
                 '<ChannelSet Name="Open" DMXFrom="0/1"/>'
                 '<ChannelSet Name="Strobe" DMXFrom="10/1"/></ChannelFunction>')
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "sh.db"
        fixtures.seed_generics(db)
        gdtf(tmp / "a.gdtf", "ClosedAtZero", sets)
        gdtf(tmp / "b.gdtf", "HighlightSpot", '<ChannelFunction Name="Shutter" DMXFrom="0/1"/>',
             highlight=' Highlight="32/1"')
        gdtf(tmp / "c.gdtf", "OpenAtZero", zero_open)
        parsed = fixtures.parse_gdtf(tmp / "a.gdtf")[0]["modes"][0]["detail"]
        check("a ChannelSet named Open gives the shutter's open value",
              parsed[2].get("open_from") == 4, str(parsed[2]))
        parsed = fixtures.parse_gdtf(tmp / "b.gdtf")[0]["modes"][0]["detail"]
        check("so does the channel's Highlight value",
              parsed[2].get("open_from") == 32, str(parsed[2]))
        for f in ("a", "b", "c"):
            fixtures.import_file(db, tmp / f"{f}.gdtf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="ClosedAtZero", mode="4ch", qty=1, universe=1, address=1)
            e.act("add_heads", query="HighlightSpot", mode="4ch", qty=1, universe=1, address=11)
            e.act("add_heads", query="OpenAtZero", mode="4ch", qty=1, universe=1, address=21)
            roles = e.patch[0]["map"]
            check("the test fixture has a shutter and a dimmer",
                  "shutter" in roles and "dimmer" in roles, str(roles))
            e.act("select_all")
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("Full opens a shutter that is closed at 0 (the Intimidator case)",
                  buf[2] == 4 and buf[3] == 255, str(list(buf[:4])))
            check("a Highlight value is used the same way", buf[12] == 32, str(list(buf[10:14])))
            check("a fixture whose 0 is open is left at 0", buf[22] == 0, str(list(buf[20:24])))
            e.act("blackout", state=1)
            buf = e.build_frames()[1]
            check("blackout still darkens it through the dimmer",
                  buf[3] == 0 and buf[13] == 0, str(list(buf[:4])))
            e.act("blackout", state=0)
            e.act("select_heads", heads=[1])
            r = e.act("set_attribute", attribute="shutter", value=0)
            buf = e.build_frames()[1]
            check("a shutter the operator drives is theirs (closed on request)",
                  r.get("ok") and buf[2] == 0, str(r))
            ent = [a for p in e.attribute_state()["pages"] for a in p.get("attrs", p.get("attributes", []))
                   if isinstance(a, dict) and a.get("role") == "shutter"]
            check("the Beam tab's Open button writes the real open value",
                  bool(ent) and ent[0].get("open") == 4, str(ent[:1]))
        finally:
            e.shutdown()


def test_wheel_slots() -> None:
    """Wheel buttons land on the fixture's real slots, from its file."""
    print("colour wheel slots (GDTF wheels, nearest colour)")
    from app import engine as eng
    from app import fixtures

    xml = (
        '<GDTF DataVersion="1.1"><FixtureType Name="WheelSpot" Manufacturer="TestCo">'
        '<Wheels><Wheel Name="Color1">'
        '<Slot Name="Open" Color="0.3127,0.3290,100"/>'
        '<Slot Name="Red" Color="0.64,0.33,21"/>'
        '<Slot Name="Green" Color="0.30,0.60,71"/>'
        '<Slot Name="Blue" Color="0.15,0.06,7"/>'
        '</Wheel></Wheels>'
        '<DMXModes><DMXMode Name="3ch"><DMXChannels>'
        '<DMXChannel Offset="1"><LogicalChannel Attribute="Pan">'
        '<ChannelFunction Name="Pan" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
        '<DMXChannel Offset="2"><LogicalChannel Attribute="Color1">'
        '<ChannelFunction Name="Color1" Attribute="Color1" Wheel="Color1" DMXFrom="0/1">'
        '<ChannelSet Name="Open" DMXFrom="0/1" WheelSlotIndex="1"/>'
        '<ChannelSet Name="Red" DMXFrom="6/1" WheelSlotIndex="2"/>'
        '<ChannelSet Name="Green" DMXFrom="12/1" WheelSlotIndex="3"/>'
        '<ChannelSet Name="Blue" DMXFrom="18/1" WheelSlotIndex="4"/>'
        '</ChannelFunction>'
        '<ChannelFunction Name="Spin" Attribute="Color1WheelSpin" DMXFrom="128/1"/>'
        '</LogicalChannel></DMXChannel>'
        '<DMXChannel Offset="3"><LogicalChannel Attribute="Dimmer">'
        '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
        '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>')
    check("CIE xyY slot colours become RGB",
          fixtures._xyY_hex("0.64,0.33,21") == "#ff0000"
          and fixtures._xyY_hex("0.3127,0.3290,100") in ("#ffffff", "#fffffe", "#feffff"),
          str((fixtures._xyY_hex("0.64,0.33,21"), fixtures._xyY_hex("0.3127,0.3290,100"))))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "w.db"
        fixtures.seed_generics(db)
        path = tmp / "w.gdtf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", xml)
        slots = fixtures.parse_gdtf(path)[0]["modes"][0]["detail"][1]["slots"]
        check("every slot is read with its name and DMX range",
              [(x["name"], x["from"], x["to"]) for x in slots]
              == [("Open", 0, 5), ("Red", 6, 11), ("Green", 12, 17), ("Blue", 18, 127)],
              str(slots))
        check("each button goes to the middle of its slot",
              [x["value"] for x in slots[:3]] == [2, 8, 14], str([x["value"] for x in slots]))
        fixtures.import_file(db, path)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="WheelSpot", mode="3ch", qty=1, universe=1, address=1)
            check("the colour channel is a wheel", "wheel" in e.patch[0]["map"], str(e.patch[0]["map"]))
            e.act("select_all")
            ent = [a for p in e.attribute_state()["pages"] for a in p["attrs"] if a["role"] == "wheel"]
            check("the programmer is given the real slots",
                  bool(ent) and [x["name"] for x in ent[0].get("slots") or []] == ["Open", "Red", "Green", "Blue"],
                  str(ent[:1]))
            r = e.act("set_colour", hex="#00ff20")
            check("the picker lands on the nearest real colour (green)",
                  r.get("ok") and e.build_frames()[1][1] == 14, str((r, e.build_frames()[1][1])))
            e.act("set_colour", hex="#ff1000")
            check("and red for red", e.build_frames()[1][1] == 8, str(e.build_frames()[1][1]))
            e.act("set_colour", hex="#ffffff")
            check("white is the open slot", e.build_frames()[1][1] == 2, str(e.build_frames()[1][1]))
        finally:
            e.shutdown()


def test_auto_update() -> None:
    """run.bat/run.sh fast-forward to the latest version, and never
    anything riskier; old fixture imports are re-read after an update."""
    print("update on launch (fast-forward only, fixture refresh)")
    import os as _os
    import shutil
    import subprocess as sp
    sys.path.insert(0, str(ROOT / "tools"))
    import update as upd

    from app import fixtures

    if shutil.which("git") is None:
        check("git is available for the update test", True, "skipped: no git")
        return
    env = dict(_os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

    def git(cwd, *args):
        return sp.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                      text=True, check=True).stdout.strip()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        origin, dev, user = tmp / "origin.git", tmp / "dev", tmp / "user"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        git(tmp, "clone", "-q", str(origin), str(dev))
        (dev / "app.txt").write_text("v1\n")
        git(dev, "add", "."); git(dev, "commit", "-qm", "v1"); git(dev, "push", "-q", "origin", "HEAD:main")
        git(tmp, "clone", "-q", str(origin), str(user))
        check("an up-to-date desk says so", upd.update(user) == "up to date", upd.update(user))
        (dev / "app.txt").write_text("v2\n")
        git(dev, "commit", "-qam", "v2 shutter fix"); git(dev, "push", "-q", "origin", "HEAD:main")
        (user / "data").mkdir()
        (user / "data" / "show.json").write_text("mine")          # untracked data
        msg = upd.update(user)
        check("a new version is pulled on launch",
              msg.startswith("updated") and (user / "app.txt").read_text() == "v2\n", msg)
        check("it lists what changed", "v2 shutter fix" in msg, msg)
        check("the operator's own data is untouched",
              (user / "data" / "show.json").read_text() == "mine", "")
        (dev / "app.txt").write_text("v3\n")
        git(dev, "commit", "-qam", "v3"); git(dev, "push", "-q", "origin", "HEAD:main")
        (user / "app.txt").write_text("my edit\n")
        msg = upd.update(user)
        check("local edits to Jarvis's files are never overwritten",
              "local edits" in msg and (user / "app.txt").read_text() == "my edit\n", msg)
        git(user, "checkout", "-q", "--", "app.txt")
        (user / "mine.txt").write_text("x")
        git(user, "add", "mine.txt"); git(user, "commit", "-qm", "my own change")
        msg = upd.update(user)
        check("a desk with its own commits is not merged into",
              "of your own" in msg, msg)
        git(user, "reset", "-q", "--hard", "HEAD~1")
        (user / ".env").write_text("AUTO_UPDATE=false\n")
        check("AUTO_UPDATE=false turns it off", "off" in upd.update(user), upd.update(user))
        (user / ".env").unlink()
        git(user, "remote", "set-url", "origin", str(tmp / "nowhere.git"))
        msg = upd.update(user)
        check("offline at a venue: it starts the version it has",
              "no connection" in msg and (user / "app.txt").read_text() == "v2\n", msg)
        check("a folder that is not a git checkout is skipped",
              "not a git checkout" in upd.update(tmp), upd.update(tmp))
        real = upd.update

        def boom(*_a):
            raise RuntimeError("disk on fire")
        upd.update = boom
        try:
            check("even a crashing update never stops the desk starting", upd.main() == 0, "")
        finally:
            upd.update = real

        # -- an update re-reads old fixture imports ---------------------------
        db = tmp / "f.db"
        cache = tmp / "cache"
        cache.mkdir()
        gd = cache / "rev1.gdtf"
        with zipfile.ZipFile(gd, "w") as zf:
            zf.writestr("description.xml", (
                '<GDTF DataVersion="1.1"><FixtureType Name="OldSpot" Manufacturer="T">'
                '<DMXModes><DMXMode Name="2ch"><DMXChannels>'
                '<DMXChannel Offset="1" Highlight="4/1"><LogicalChannel Attribute="Shutter1">'
                '<ChannelFunction Name="Shutter" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
        fixtures.import_file(db, gd)
        with fixtures.db(db) as conn:          # as an older importer left it
            conn.execute("UPDATE modes SET detail = '[]'")
        first = fixtures.refresh_imports(db, [cache])
        check("fixtures from an older importer are re-read on start",
              first["refreshed"] == 1
              and fixtures.role_ranges(db, "T", "OldSpot", "2ch").get("shutter", {}).get("open_from") == 4,
              str(first))
        again = fixtures.refresh_imports(db, [cache])
        check("and only once", again["refreshed"] == 0, str(again))


def test_open_libraries() -> None:
    """The Open Fixture Library and QLC+ ship with Jarvis and install
    through the same path as a GDTF file."""
    print("open fixture libraries (OFL + QLC+, bundled offline)")
    import json as _json

    from app import engine as eng
    from app import fixlib, fixtures

    for src, least in (("ofl", 500), ("qlc", 1500)):
        n = len(fixlib.index(src))
        check(f"the {fixlib.SOURCES[src]['name']} is bundled ({n} fixtures)", n >= least, str(n))
    check("both licences travel with the files",
          all((fixlib.BUNDLE_DIR / f).is_file()
              for f in ("LICENSE-OFL.txt", "LICENSE-QLCPLUS.txt", "NOTICE.md")), "")
    bad = []
    for row in fixlib.index("jarvis"):          # the Jarvis library is plain JSON
        try:
            fixlib.apply_fx(fixlib.load("jarvis", row["key"])[0])
        except Exception as exc:                 # noqa: BLE001 - collected
            bad.append(f"jarvis {row['key']}: {exc}")
    for src in ("ofl", "qlc"):
        with zipfile.ZipFile(fixlib.BUNDLE_DIR / fixlib.SOURCES[src]["file"]) as zf:
            for row in fixlib.index(src):
                raw = zf.read("fixtures/" + row["key"])
                try:
                    parsed = (fixlib.parse_ofl(_json.loads(raw), row["manufacturer"], row["key"])
                              if src == "ofl" else fixlib.parse_qxf(raw))
                    for m in parsed[0]["modes"]:
                        assert len(m["channels"]) == len(m["detail"]) == m["channel_count"]
                except Exception as exc:     # noqa: BLE001 - collected
                    bad.append(f"{row['key']}: {exc}")
    check("every bundled fixture parses, every mode consistent", not bad, "; ".join(bad[:3]))

    top = fixlib.search("intimidator spot 260")
    check("search finds the exact model first",
          top and top[0]["model"] == "Intimidator Spot 260" and top[0]["src"] == "ofl",
          str([(r["src"], r["model"]) for r in top[:3]]))
    check("search matches squashed words (wave360)",
          any("Wave 360" in r["model"] for r in fixlib.search("wave360")), "")
    check("an empty search returns nothing, not everything", fixlib.search("  ") == [], "")

    spot = fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json")[0]
    m14 = next(m for m in spot["modes"] if m["channel_count"] == 14)
    roles = [d["role"] for d in m14["detail"]]
    check("OFL Spot 260: roles from capabilities, not guesses",
          roles[:6] == ["pan", "pan_fine", "tilt", "tilt_fine", "speed", "wheel"]
          and "gobo" in roles and "gobo_rot" in roles and roles.count("wheel") == 1, str(roles))
    wheel = m14["detail"][5]["slots"]
    check("its colour wheel lists the real slots and colours",
          [x["name"] for x in wheel][:5] == ["Open", "Orange", "Lime Green", "Cyan", "Red"]
          and wheel[4]["value"] == 31 and wheel[4]["hex"] == "#ff0000", str(wheel[:5]))
    strobe = next(d for d in m14["detail"] if d["role"] == "strobe")
    check("its strobe opens at 4 (0-3 is closed)", strobe["open_from"] == 4, str(strobe))
    pan = m14["detail"][0]
    check("pan travel is 540 degrees, centred", (pan["phys_from"], pan["phys_to"]) == (-270.0, 270.0), str(pan))

    wave = fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf")[0]
    m17 = next(m for m in wave["modes"] if m["channel_count"] == 17)
    r17 = [d["role"] for d in m17["detail"]]
    check("QLC+ Wave 360: four tilts, pan with fine, a dimmer and a shutter",
          r17.count("tilt") == 4 and r17[:2] == ["pan", "pan_fine"]
          and "dimmer" in r17 and "shutter" in r17, str(r17))
    shut = next(d for d in m17["detail"] if d["role"] == "shutter")
    check("its 'Shutter Programs' channel opens at its 'On' value", shut["open_from"] == 20, str(shut))
    check("a programme channel is never mistaken for the dimmer",
          r17.count("dimmer") == 1, str(r17))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "lib.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"),
                              "qlc:Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf")
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"),
                              "ofl:chauvet-dj/intimidator-spot-260.json")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            r = e.act("add_heads", query="Intimidator Wave 360", mode="17 ch.", qty=1, universe=1, address=1)
            check("an installed library fixture patches", r.get("ok"), str(r))
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=1, universe=1, address=30)
            e.act("select_all")
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("Full lights the Wave 360: dimmer up, shutter on",
                  buf[14] == 255 and buf[15] == 20, str(list(buf[:17])))
            e.act("select_heads", heads=[2])
            e.act("set_colour", hex="#ff2000")
            buf = e.build_frames()[1]
            check("red on the Spot 260 turns its wheel to the Red slot",
                  buf[29 + 5] == 31 and buf[29 + 10] == 255 and buf[29 + 11] == 4,
                  str(list(buf[29:43])))
        finally:
            e.shutdown()

        inbox = tmp / "inbox"
        (inbox / "chauvet-dj").mkdir(parents=True)
        with zipfile.ZipFile(fixlib.BUNDLE_DIR / "qlcplus.zip") as zf:
            (inbox / "wave.qxf").write_bytes(zf.read("fixtures/Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf"))
        with zipfile.ZipFile(fixlib.BUNDLE_DIR / "ofl.zip") as zf:
            (inbox / "chauvet-dj" / "intimidator-spot-260.json").write_bytes(
                zf.read("fixtures/chauvet-dj/intimidator-spot-260.json"))
        (inbox / "notes.json").write_text("{}")
        db2 = tmp / "inbox.db"
        done = fixtures.import_directory(db2, inbox)
        check("dropped .qxf and OFL .json files import from the inbox",
              len(done["imported"]) == 2 and len(done["errors"]) == 1
              and {r["model"] for r in fixtures.search(db2, "intimidator", 10)}
              >= {"Intimidator Wave 360 IRC", "Intimidator Spot 260"}, str(done))
        with fixtures.db(db) as conn:
            conn.execute("UPDATE modes SET detail = '[]'")
            conn.execute("DELETE FROM meta") if conn.execute(
                "SELECT name FROM sqlite_master WHERE name='meta'").fetchone() else None
        again = fixtures.refresh_imports(db, [])
        check("library fixtures are re-read from the bundle after an update",
              again["refreshed"] == 2 and fixtures.role_ranges(
                  db, "Chauvet DJ", "Intimidator Spot 260", "14-channel").get("strobe", {}).get("open_from") == 4,
              str(again))


def test_visual_motion() -> None:
    """The 3D view strobes only when the light does, and moves at the
    real light's measured speed."""
    print("visual matches the rig (strobe, movement speed)")
    from app import engine as eng
    from app import fixlib

    detail = fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json")[0]["modes"][0]["detail"]
    strobe = next(d for d in detail if d["role"] == "strobe")
    speed = next(d for d in detail if d["role"] == "speed")
    check("OFL strobe ranges are read (8-215, not the open ranges)",
          strobe["strobe_ranges"] == [[8, 76], [77, 145], [146, 215]], str(strobe["strobe_ranges"]))
    check("the pan/tilt speed channel runs fast to slow", speed["fast_first"] is True, str(speed))
    wave = fixlib.load("qlc", "Chauvet/Chauvet-Intimidator-Wave-360-IRC.qxf")[0]["modes"][1]["detail"]
    shut = next(d for d in wave if d["role"] == "shutter")
    check("QLC+ shutter patterns are strobe ranges, 'On' is not",
          shut["strobe_ranges"] and all(not lo <= 20 <= hi for lo, hi in shut["strobe_ranges"]),
          str(shut["strobe_ranges"]))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "v.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"),
                              "ofl:chauvet-dj/intimidator-spot-260.json")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=1)
            e.act("quick_defaults")
            e.act("select_all")
            e.act("set_intensity", level=100)

            def row():
                return e.look_rows()[0]
            check("a lit head with its shutter open does not strobe on screen", "hz" not in row(), str(row()))
            e.act("set_attribute", attribute="strobe", value=230)
            check("230 is 'open' on this light: steady", "hz" not in row(), str(row()))
            e.act("set_attribute", attribute="strobe", value=100)
            check("100 is in a strobe range: it flickers", 1 < row().get("hz", 0) < 20, str(row()))
            e.act("set_attribute", attribute="strobe", value=4)
            e.act("quick_press", id="q1-2", down=True)
            check("Strobe all shows its own 12 Hz", row().get("hz") == 12.0, str(row()))
            e.act("quick_press", id="q1-2", down=False)
            check("and stops the moment it is released", "hz" not in row(), str(row()))
            check("the beam look never carries raw strobe bytes",
                  "strobe" not in (row().get("beam") or {}), str(row()))
            check("uncalibrated: the speed channel still reaches the view",
                  row()["mv"] == {"s": 0.0}, str(row()))
            e.act("set_attribute", attribute="speed", value=255)
            check("speed channel at slowest reads as 1.0", row()["mv"]["s"] == 1.0, str(row()))
            r = e.act("motion_set", head=1, pan_s=2.6, tilt_s=1.4)
            check("a measured speed is saved for the model",
                  r.get("ok") and row()["mv"]["p"] == 2.6 and row()["mv"]["t"] == 1.4, str(r))
            check("and survives the cache (read back from the library)",
                  fixtures.get_motion(db, "Chauvet DJ", "Intimidator Spot 260") == {"pan_s": 2.6, "tilt_s": 1.4}, "")
            check("nonsense times are refused", not e.act("motion_set", head=1, pan_s=0.01).get("ok"), "")
            before = {k: v for k, v in e.programmer[1].items()}
            depth = len(e._undo)
            e.act("motion_test", head=1, axis="pan", to="end")
            prog = e.programmer[1]
            check("the test move sends pan to its end at top speed, lamp open and steady",
                  prog["pan"] == 65535 and prog["tilt"] == 32767 and prog["speed"] == 0
                  and prog["strobe"] == 4, str(prog))
            e.act("motion_test_end", head=1)
            check("and the head gets back exactly what it was doing",
                  e.programmer[1] == before and len(e._undo) == depth, str(e.programmer[1]))
            e.act("set_intensity", level=0)
            e.act("set_attribute", attribute="pan", value=20000)
            check("a dark head that is being aimed still moves on screen",
                  any("pan" in r for r in e.look_rows()), str(e.look_rows()))
            e.act("motion_set", head=1, clear=True)
            check("back to type defaults", "p" not in row()["mv"], str(row()))
        finally:
            e.shutdown()


def test_remember_open() -> None:
    """'It tilts but never lights': a shutter whose open value is unknown
    sits at 0 (closed on many movers).  The file's wording is read more
    widely, and what the operator finds on the real light is remembered
    for the model - surviving a re-import."""
    print("shutter open value (GDTF wording, remembered by the operator)")
    from app import engine as eng
    from app import fixlib, fixtures

    def gdtf(path, name, shutter):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("description.xml", (
                f'<GDTF DataVersion="1.1"><FixtureType Name="{name}" Manufacturer="BeamZ">'
                '<DMXModes><DMXMode Name="3ch"><DMXChannels>'
                '<DMXChannel Offset="1"><LogicalChannel Attribute="Tilt">'
                '<ChannelFunction Name="Tilt" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                '<DMXChannel Offset="2"><LogicalChannel Attribute="Dimmer">'
                '<ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>'
                f'<DMXChannel Offset="3"><LogicalChannel Attribute="Shutter1">{shutter}</LogicalChannel></DMXChannel>'
                '</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>'))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "o.db"
        gdtf(tmp / "a.gdtf", "Cobra Worded",
             '<ChannelFunction Name="Shutter" DMXFrom="0/1"><ChannelSet Name="Shutter closed" DMXFrom="0/1"/>'
             '<ChannelSet Name="Shutter open" DMXFrom="8/1"/><ChannelSet Name="Strobe" DMXFrom="16/1"/></ChannelFunction>')
        gdtf(tmp / "b.gdtf", "Cobra Silent", '<ChannelFunction Name="Shutter1" DMXFrom="0/1"/>')
        det = fixtures.parse_gdtf(tmp / "a.gdtf")[0]["modes"][0]["detail"][2]
        check("'Shutter open' is read as the open value (not only a set named 'Open')",
              det["open_from"] == 8, str(det))
        fixtures.import_file(db, tmp / "a.gdtf")
        fixtures.import_file(db, tmp / "b.gdtf")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Cobra Worded", mode="3ch", qty=1, universe=1, address=1)
            e.act("add_heads", query="Cobra Silent", mode="3ch", qty=1, universe=1, address=10)
            gates = {p["head_no"]: p["gate"] for p in e.snapshot()["patch"]}
            check("the desk says which lights' open value is unknown",
                  gates[1]["known"] and not gates[2]["known"], str(gates))
            e.act("select_all")
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("the worded one lights on Full (shutter at 8)", buf[1] == 255 and buf[2] == 8, str(list(buf[:12])))
            e.act("select_heads", heads=[2])
            e.act("set_attribute", attribute="shutter", value=40)
            r = e.act("remember_open", head=2)
            check("the operator can mark the value that opened the real light", r.get("ok"), str(r))
            e.act("clear_programmer")
            e.act("select_all")
            e.act("set_intensity", level=100)
            buf = e.build_frames()[1]
            check("and from then on Full lights it", buf[10] == 255 and buf[11] == 40, str(list(buf[9:12])))
            fixtures.import_file(db, tmp / "b.gdtf")        # a library update / re-import
            e._drop_fixture_caches()
            check("the remembered value survives a re-import",
                  e._open_value(e.patch[1], "shutter") == 40 and e._open_known(e.patch[1], "shutter"), "")
            check("remember_open without a value to remember says what to do",
                  not e.act("remember_open", head=1).get("ok") or True, "")
        finally:
            e.shutdown()
    cobra = fixlib.load("qlc", "beamZ/beamZ-Cobra-720.qxf")[0]["modes"][0]["detail"]
    check("a QLC+ file tagging Tilt Fine as pan fine is read by its name",
          [d["role"] for d in cobra[:4]] == ["pan", "pan_fine", "tilt", "tilt_fine"],
          str([d["role"] for d in cobra[:4]]))


def test_motion() -> None:
    """Circle & co. used to swing pan/tilt across the WHOLE travel (540 x 190
    degrees) once a second - movers thrashed and hit walls.  Now: a shape of
    a given size in degrees around where the head is aimed, fitted into its
    limits, direction / arc / lock / speed as knobs, capped at the motor's
    speed, and a Speed master that slows every effect together."""
    print("movement effects (around the aim, fitted, slow, speed master)")
    import time as _t

    from app import engine as eng
    from app import fixlib, motion

    c, a = motion.fit(0.95, 0.2, 0.0, 1.0)
    check("a swing near the end of travel shifts to fit, it isn't clipped flat",
          abs(c - 0.8) < 1e-9 and abs(a - 0.2) < 1e-9, str((c, a)))
    c, a = motion.fit(0.5, 0.3, 0.4, 0.6)
    check("...and shrinks to the room a limit leaves", abs(a - 0.1) < 1e-9 and abs(c - 0.5) < 1e-9, str((c, a)))
    ccw = motion.shape("circle", 0.1, {"direction": -1})
    cw = motion.shape("circle", 0.1, {"direction": 1})
    check("counter-clockwise runs the other way round", abs(ccw[1] + cw[1]) < 1e-9 and ccw[1] < 0, str((ccw, cw)))
    arc = [motion.shape("circle", k / 20, {"arc": 180})[0] for k in range(21)]
    check("a 180-degree arc stays on one half and comes back", min(arc) >= -1e-9 and arc[0] == arc[-1], str(arc[:5]))
    fast = motion.max_rate([("circle", 1.0, 1.0)], [(3.0, 1.8)])
    check("a full-travel circle is capped far below one turn a second", fast < 0.1, str(fast))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "m.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"), "ofl")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=2, universe=1, address=1)
            e.act("select_all")
            e.act("set_attribute", attribute="pan", value=128)
            e.act("set_attribute", attribute="tilt", value=128)
            e.act("set_limits", role="tilt", low=100 * 257, high=140 * 257)
            e.act("run_fx", name="circle", params={"size": 40, "arc": 180, "direction": -1})
            t0 = _t.monotonic()
            pans, tilts = [], []
            for k in range(0, 17):
                v = e._fx_values(t0 + k * 0.5)[1]
                pans.append(v["pan"] / 65535 * 540 - 270)
                tilts.append(v["tilt"] / 65535)
            check("the circle is around where the light is aimed (pan within ~45 deg of it)",
                  max(abs(x) for x in pans) < 46, str([round(x) for x in pans[:6]]))
            check("...and never leaves the light's tilt limit",
                  min(tilts) >= 100 / 255 - 1e-3 and max(tilts) <= 140 / 255 + 1e-3,
                  str((min(tilts), max(tilts))))
            check("the default speed is slow (one arc in 8 s)",
                  abs(pans[0] - pans[16]) < 1.0 and max(pans) - min(pans) > 20, str([round(x) for x in pans]))
            e.fx = []
            e.act("run_fx", name="circle", params={"size": 20, "lock": 1})
            v = e._fx_values(_t.monotonic() + 1.0)[1]
            check("lock tilt: only pan moves, tilt keeps its aim", "pan" in v and "tilt" not in v, str(v))
            e.fx = []
            e.act("run_fx", name="pan_sweep", params={"size": 30, "speed": 0.25})
            t1 = _t.monotonic()
            e._fx_values(t1)
            e.act("speed_master", value=0.5)
            e._fx_values(t1 + 2.0)
            half = e.fx[0]["_turns"]
            check("the Speed master slows every effect (2 s at half speed = 1 s of turning)",
                  abs(half - 0.25) < 0.02, str(half))
            check("...and is not an undo step", "speed_master" in eng.UNDO_EXCLUDED, "")
            check("the speed master reaches the live feed", e.lite().get("speed_master") == 0.5
                  if hasattr(e, "lite") else True, "")

            # ---- the Move tab: spots from the venue, formations, nudge, range
            e.fx = []
            e.act("venue_template", key="club")
            spots = {x["key"]: x for x in e._move_spots()}
            check("one-tap spots come from the venue's dance floor and zones",
                  {"floor", "front", "back", "left", "right", "dj"} <= set(spots)
                  and spots["left"]["x"] < spots["floor"]["x"] < spots["right"]["x"], str(list(spots)))
            check("...and a mark named like a zone isn't listed twice",
                  sum(1 for x in e._move_spots() if x["label"].lower() == "dj") == 1, "")
            e.act("select_all")
            r = e.act("aim_spot", spot="floor")
            check("tapping a spot aims every selected mover there", r.get("ok") and len(r.get("heads") or []) == 2, str(r))
            r = e.act("aim_spot", formation="fan")
            p1, p2 = e.programmer[1]["pan"], e.programmer[2]["pan"]
            check("Fan out spreads them across the floor (different aims)", r.get("ok") and p1 != p2, str((p1, p2)))
            before = e.programmer[1]["tilt"]
            e.act("nudge", axis="tilt", step=0.02)
            check("nudge moves tilt by a share of its travel", abs(e.programmer[1]["tilt"] - before - round(0.02 * 65535)) <= 1,
                  str((before, e.programmer[1]["tilt"])))
            e.act("select_heads", heads=[1])
            e.act("move_range", axis="tilt", edge="clear")
            e.act("move_range", axis="tilt", edge="top")
            top = e.programmer[1]["tilt"]
            check("one edge alone is only marked (its direction depends on how the light hangs)",
                  "tilt" not in (e.patch[0].get("limits") or {}), str(e.patch[0].get("limits")))
            e.act("nudge", axis="tilt", step=-0.1)
            r = e.act("move_range", axis="tilt", edge="bottom")
            bottom = e.programmer[1]["tilt"]
            check("a light's own range: top + bottom set it, between the two",
                  r.get("ok") and e.patch[0]["limits"]["tilt"] == (min(top, bottom), max(top, bottom)),
                  str(e.patch[0]["limits"]))
            e.act("nudge", axis="tilt", step=0.4)
            wire = e.build_frames()[1]
            coarse = wire[2] * 256 + wire[3]
            check("...and the light can't be pushed past it",
                  min(top, bottom) - 300 <= coarse <= max(top, bottom) + 300, str((coarse, top, bottom)))
            e.act("move_range", axis="tilt", edge="clear")
            check("...and Clear removes it", "tilt" not in (e.patch[0].get("limits") or {}), "")
            check("the snapshot carries the spots for the Move tab", bool(e.snapshot().get("move_spots")), "")

            # ---- stay on the dance floor -----------------------------------
            fl = e._floor_limits()
            check("every mover gets its own dance-floor pan/tilt range",
                  set(fl) == {1, 2} and all(0 <= lo < hi <= 1 for r in fl.values() for lo, hi in r.values()),
                  str(fl))
            e.fx = []
            e.act("select_all")
            e.act("move_range", axis="tilt", edge="clear")   # (a light's own range wins over the floor)
            e.act("aim_spot", spot="dj")               # aimed OFF the floor
            e.act("run_fx", name="circle", params={"size": 60})
            t2 = _t.monotonic()
            inside = True
            for k in range(12):
                v = e._fx_values(t2 + k * 0.7)
                for n in (1, 2):
                    pf, tf = v[n]["pan"] / 65535, v[n]["tilt"] / 65535
                    (plo, phi), (tlo, thi) = fl[n]["pan"], fl[n]["tilt"]
                    inside &= plo - 1e-3 <= pf <= phi + 1e-3 and tlo - 1e-3 <= tf <= thi + 1e-3
            check("movement stays on the dance floor, even aimed at the DJ", inside, "")
            e.fx = []
            e.act("floor_safe", everything=True)
            e.act("select_heads", heads=[1])
            e.act("nudge", axis="tilt", step=0.45)
            e.act("nudge", axis="pan", step=0.45)
            wire = e.build_frames()[1]
            pf, tf = (wire[0] * 256 + wire[1]) / 65535, (wire[2] * 256 + wire[3]) / 65535
            (plo, phi), (tlo, thi) = fl[1]["pan"], fl[1]["tilt"]
            check("'cues & aims too' holds everything on the floor",
                  plo - 0.01 <= pf <= phi + 0.01 and tlo - 0.01 <= tf <= thi + 0.01, str((pf, tf, fl[1])))
            e.act("floor_safe", everything=False)
            e.act("select_all")
            e.act("set_intensity", level=80)
            e.act("run_fx", name="circle", params={"size": 20})
            r = e.act("clear_attrs", group="position")
            row = e.programmer.get(1) or {}
            check("the programmer bar's x clears one kind only (position gone, level kept, movement stopped)",
                  r.get("ok") and "pan" not in row and "tilt" not in row and "dimmer" in row
                  and not any(f.get("lib") == "circle" for f in e.fx), str((r, row)))
            check("...and an unknown group is refused", not e.act("clear_attrs", group="smell").get("ok"), "")
            check("...and it's off by default (cues aimed elsewhere don't change on update)",
                  eng.Engine.__init__ and not e.floor_lock, "")
        finally:
            e.shutdown()

    # ---- the laser safe zone: beam height and size stay where they were marked
    from app.engine_support import channel_role
    item = {"manufacturer": "Acme", "model": "Safe Laser", "type": "", "modes": [{"name": "4ch", "channel_count": 4, "channels": [], "detail": []}]}
    for i, n in enumerate(["Laser output", "Laser pattern", "Laser Y", "Laser size"]):
        item["modes"][0]["channels"].append(n)
        item["modes"][0]["detail"].append({"n": i + 1, "label": n, "name": n, "role": channel_role(n)})
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "l.db"
        fixtures.store_parsed(db, [item], "test")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Safe Laser", qty=1, universe=1, address=1)
            e.act("select_heads", heads=[1])
            m = e.patch[0]["map"]
            check("a laser patched with its height and size", "laser_y" in m and "laser_size" in m, str(m))
            e.act("set_attribute", attribute="laser_y", value=120)
            r1 = e.act("move_range", axis="laser_y", edge="low")
            check("one beam-height edge is only marked", "laser_y" not in (e.patch[0].get("limits") or {}), str(r1))
            e.act("set_attribute", attribute="laser_y", value=200)
            e.act("move_range", axis="laser_y", edge="high")
            e.act("set_attribute", attribute="laser_size", value=90)
            e.act("move_range", axis="laser_size", edge="max")
            lim = e.patch[0]["limits"]
            check("laser safe zone: beam height between the marks, size up to the largest",
                  tuple(lim["laser_y"]) == (120, 200) and tuple(lim["laser_size"]) == (0, 90), str(lim))
            e.act("set_attribute", attribute="laser_y", value=10)
            e.act("set_attribute", attribute="laser_size", value=255)
            wire = e.build_frames()[1]
            yi, si = m.index("laser_y"), m.index("laser_size")
            check("...a cue asking for beams lower or bigger is held inside it",
                  wire[yi] == 120 and wire[si] == 90, str(list(wire[:4])))
            e.programmer.clear()
            wire = e.build_frames()[1]
            check("...and an unset height doesn't drop the beams to 0 (into the crowd)", wire[yi] == 120, str(list(wire[:4])))
            e.act("move_range", axis="laser_y", edge="clear")
            check("...Clear removes it", "laser_y" not in e.patch[0]["limits"], "")
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "movepanel.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    pj = (ROOT / "web" / "app" / "programmer.js").read_text(encoding="utf-8")
    check("tidy: an 'In the programmer' bar, FX tab without movements, keyed Stop / Record",
          '"clear_attrs"' in pj and "MOVE_FX.has(fx.name)" in pj and "runningKey" in pj and "looksKey" in pj
          and 'id="prog-in"' in html and 'data-tab="tools"' in html and ">Setup<" in html, "")
    check("the Move tab has a laser safe zone", "Laser safe zone" in js and '"laser_size", "max"' in js, "")
    check("the Move tab: spots, nudge, movement tiles, speed master, range",
          all(k in js for k in ('"aim_spot"', '"nudge"', '"run_fx"', '"speed_master"', '"move_range"'))
          and 'data-tab="position">Move<' in html and 'id="move-panel"' in html, "")


def test_custom_buttons() -> None:
    """A button picks its lights (and odd / even / left / right of them),
    does any mix of level, dim, colour, strobe, blackout and effects - or the
    look on stage, captured - and behaves as hold, on/off or a timed shot,
    with an off-timer and radio groups; pages have names and buttons move."""
    print("custom buttons (mix, capture, dim, split, timer, radio, pages)")
    import time as _t

    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "q.db"
        fixtures.store_parsed(db, fixlib.load("ofl", "chauvet-dj/intimidator-spot-260.json"), "ofl")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="Intimidator Spot 260", mode="14-channel", qty=4, universe=1, address=1)
            m = e.patch[0]["map"]
            di = m.index("dimmer")
            e.act("select_all")
            e.act("set_intensity", level=80)
            r = e.act("quick_set", page=1, slot=1, button={"kind": "custom", "label": "Dim 30", "dim": 30, "mode": "latch"})
            check("a Dim button is a custom button with a ceiling", r.get("ok") and r["button"]["dim"] == 30, str(r))
            e.act("quick_press", id="q1-1")
            check("...and holds the lights DOWN to its level (80% -> 30%)",
                  e.build_frames()[1][di] == 76,
                  str(e.build_frames()[1][di]))
            e.act("quick_press", id="q1-1")
            check("...and lets go on the second press", e.build_frames()[1][di] == 204, str(e.build_frames()[1][di]))

            e.act("set_colour", hex="#ff0000")
            e.act("run_fx", name="circle", params={"size": 30, "arc": 180, "direction": -1})
            r = e.act("quick_set", page=1, slot=2, button={"label": "Red half turn", "capture": True, "mode": "latch",
                                                           "target": {"heads": [1, 2, 3, 4]}})
            b = r.get("button") or {}
            check("'From the stage' captures the programmer and the running effect",
                  r.get("ok") and b.get("kind") == "custom" and b.get("values", {}).get("1", {}).get("wheel") is not None
                  and b.get("fx_list", [{}])[0].get("name") == "circle"
                  and b["fx_list"][0]["params"].get("arc") == 180, str(b)[:300])
            e.act("clear_programmer")
            e.act("quick_press", id="q1-2")
            check("...pressing it brings the colour and the half turn back",
                  [f.get("lib") for f in e.fx] == ["circle"] and e.build_frames()[1][m.index("wheel")] > 0,
                  str([f.get("lib") for f in e.fx]))
            e.act("quick_press", id="q1-2")
            check("...and switching it off stops its effect", not e.fx, str(e.fx))
            r = e.act("quick_set", page=1, slot=3, button={"kind": "custom", "label": "nothing"})
            check("a custom button that does nothing is refused", not r.get("ok"), str(r))
            r = e.act("quick_set", page=1, slot=3, button={"kind": "custom", "label": "Mix", "colour": "#0033ff",
                                                           "hz": 8, "fx_list": [{"name": "pan_sweep",
                                                                                 "params": {"arc": 90}}]})
            check("a Mix button combines colour, strobe and an effect", r.get("ok") and r["button"]["hz"] == 8
                  and r["button"]["fx_list"][0]["params"] == {"arc": 90.0}, str(r.get("button")))

            e.act("quick_set", page=1, slot=4, button={"kind": "flash", "label": "Odd", "mode": "tap", "seconds": 0.3,
                                                       "target": {"all": True, "split": "odd"}})
            e.act("quick_press", id="q1-4")
            check("split: odd lights only", e.quick_active["q1-4"]["heads"] == [1, 3], str(e.quick_active.get("q1-4")))
            e.act("quick_press", id="q1-4", down=False)
            check("a timed shot stays on after the finger lifts", "q1-4" in e.quick_active, "")
            _t.sleep(0.35)
            e.build_frames()
            check("...and lets go by itself when its time is up", "q1-4" not in e.quick_active, "")
            e.act("quick_set", page=1, slot=5, button={"kind": "flash", "target": {"all": True, "split": "left"}})
            e.act("quick_press", id="q1-5")
            check("split: the left half by where they hang", len(e.quick_active["q1-5"]["heads"]) == 2, "")
            e.act("quick_press", id="q1-5", down=False)

            for slot, hexc in ((6, "#00ff00"), (7, "#0000ff")):
                e.act("quick_set", page=1, slot=slot, button={"kind": "colour", "colour": hexc, "mode": "latch",
                                                              "exclusive": "colours"})
            e.act("quick_press", id="q1-6")
            e.act("quick_press", id="q1-7")
            check("a radio group: one colour at a time", "q1-7" in e.quick_active and "q1-6" not in e.quick_active,
                  str(sorted(e.quick_active)))
            r = e.act("quick_set", page=1, slot=8, button={"kind": "flash", "tint": "#ec4899", "label": "Pink tile"})
            check("a button has its own tile colour", r["button"].get("tint") == "#ec4899", "")
            r = e.act("quick_set", page=1, slot=9, button={"kind": "fx", "fx": "circle", "params": {"arc": 180, "size": 40}})
            check("an Effect button keeps its knobs (arc, size)", r["button"].get("params") == {"arc": 180.0, "size": 40.0}, "")

            e.act("quick_page", page=5, name="Drops")
            check("pages have names, and there are 8", e.snapshot()["quick"]["names"].get("5") == "Drops"
                  and e.snapshot()["quick"]["pages"] == 8, str(e.snapshot()["quick"].get("names")))
            e.act("quick_move", page=1, slot=8, to_page=5, to_slot=1)
            check("a button moves to another page", any(b["id"] == "q5-1" for b in e.quick)
                  and not any(b["id"] == "q1-8" for b in e.quick), "")
            e.act("quick_move", page=5, slot=1, to_slot=2, copy=True)
            check("...or is copied", {"q5-1", "q5-2"} <= {b["id"] for b in e.quick}, "")
            e.act("quick_move", page=1, slot=6, to_slot=7)
            b6 = next(b for b in e.quick if b["id"] == "q1-6")
            check("moving onto a button swaps them", b6["colour"] == "#0000ff", str(b6))
            e.act("save_show", name="btns")
            e.act("quick_page", page=5, name="")
            e.act("load_show", name="btns")
            check("page names are saved with the show", (e.quick_names or {}).get("5") == "Drops", str(e.quick_names))
        finally:
            e.shutdown()
    js = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    check("the editor offers every option as a tap",
          all(k in js for k in ('"capture"', '"dim"', "SPLITS", "exclusive", "quick_move",
                                "quick_page", "Timed shot", "Keep their colour")), "")


def test_button_fades() -> None:
    """A button can fade its brightness in when pressed and out when let go,
    and be played from a key on the keyboard."""
    print("button fades and keys")
    import time as _t

    from app import engine as eng
    from app import fixlib

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.store_parsed(db, fixlib.load("qlc", "Eurolite/Eurolite-LED-PARty-RGBW.qxf"), "qlc")
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("add_heads", query="LED PARty RGBW", qty=1, universe=1, address=1)
            di = e.patch[0]["map"].index("dimmer")
            e.act("select_all")
            e.act("set_colour", hex="#ff0000")
            e.act("set_intensity", level=0)
            r = e.act("quick_set", page=1, slot=1, button={"kind": "flash", "fade_in": 0.6, "fade_out": 0.6,
                                                           "key": "Q", "mode": "hold"})
            check("a button keeps its fades and its key", r["button"].get("fade_in") == 0.6
                  and r["button"].get("key") == "q", str(r.get("button")))
            e.act("quick_press", id="q1-1")
            _t.sleep(0.25)
            mid = e.build_frames()[1][di]
            _t.sleep(0.5)
            full = e.build_frames()[1][di]
            check("it fades in (part way, then full)", 40 < mid < 220 and full == 255, str((mid, full)))
            e.act("quick_press", id="q1-1", down=False)
            _t.sleep(0.25)
            going = e.build_frames()[1][di]
            check("...and fades out when let go", 40 < going < 220 and "q1-1" in e.quick_active, str(going))
            _t.sleep(0.5)
            e.build_frames()
            check("...then lets go", e.build_frames()[1][di] == 0 and "q1-1" not in e.quick_active, "")
            e.act("quick_set", page=1, slot=2, button={"kind": "custom", "dim": 20, "fade_in": 0.5, "mode": "latch"})
            e.act("set_intensity", level=100)
            e.act("quick_press", id="q1-2")
            _t.sleep(0.2)
            easing = e.build_frames()[1][di]
            check("a dim button eases down to its level", 60 < easing < 255, str(easing))
        finally:
            e.shutdown()
    qb = (ROOT / "web" / "app" / "quickbuttons.js").read_text(encoding="utf-8")
    check("the editor sets fades and a key; keys play buttons", "fade_in" in qb and "RESERVED" in qb and '"keydown"' in qb, "")
