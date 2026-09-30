"""Full rig check: every kind of light, SFX and brand, hung / stood /
dragged around a custom venue, every action and every button kind fired
at it, and the DMX compared with what the 3D view is told.

    python tools/rigcheck.py            # the report
    python tools/rigcheck.py --quick    # fewer brands

It builds its own temporary database and show folder: nothing in data/ is
touched.  Exit code 1 when anything failed.  The browser half (where the
3D beam actually lands) is tools/rigcheck_3d.mjs.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import engine as eng, fixlib, fixture_kind, fixtures  # noqa: E402
from app.engine_support import channel_role  # noqa: E402

BRANDS = ("chauvet", "american dj", "martin", "robe", "eurolite", "cameo", "showtec",
          "clay paky", "elation", "stairville", "beamz", "varytec", "adj", "ayra", "futurelight")


def pick_fixtures(quick: bool = False) -> list[tuple[str, str, str, str, str]]:
    """(src, key, manufacturer, model, type): one per light type, then one
    per (brand, type) pair so every brand appears, largest mode's type."""
    by_type: dict[str, tuple] = {}
    by_brand: dict[tuple, tuple] = {}
    for src in ("qlc", "ofl"):
        for row in fixlib.index(src):
            try:
                it = fixlib.load(src, row["key"])[0]
            except Exception:              # noqa: BLE001 - a broken library file is not our test
                continue
            modes = it.get("modes") or []
            if not modes:
                continue
            mode = max(modes, key=lambda m: len(m.get("channels") or []))
            roles = [channel_role(c) for c in mode.get("channels") or []]
            t = fixture_kind.describe({"manufacturer": it["manufacturer"], "model": it["model"],
                                       "mode": mode.get("name", ""), "map": roles})["type"]
            row_t = (src, row["key"], it["manufacturer"], it["model"], t)
            by_type.setdefault(t, row_t)
            brand = next((b for b in BRANDS if b in it["manufacturer"].lower()), None)
            if brand and (brand, t) not in by_brand and len([k for k in by_brand if k[0] == brand]) < (1 if quick else 3):
                by_brand[(brand, t)] = row_t
    out = list(by_type.values()) + [v for v in by_brand.values() if v not in by_type.values()]
    # the Jarvis library's SFX / lasers too
    for key in ("laserworld/beambar-10b-mk3",):
        out.append(("jarvis", key, "Laserworld", "BeamBar 10B MK3", "laser"))
    return out


class Report:
    def __init__(self):
        self.fails: list[str] = []
        self.oks = 0

    def check(self, cond: bool, what: str, detail: str = "") -> bool:
        if cond:
            self.oks += 1
        else:
            self.fails.append(f"{what}  {detail}".rstrip())
        return cond


def main() -> int:
    quick = "--quick" in sys.argv
    rep = Report()
    picks = pick_fixtures(quick)
    print(f"{len(picks)} fixtures: " + ", ".join(sorted({p[4] for p in picks})))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        for src, key, man, model, _t in picks:
            try:
                fixtures.store_parsed(db, fixlib.load(src, key), src if src != "jarvis" else f"jarvis:{key}")
            except Exception as exc:          # noqa: BLE001
                rep.check(False, f"install {man} {model}", str(exc))
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            run(e, picks, rep)
        finally:
            e.shutdown()
    print(f"\n{rep.oks} ok, {len(rep.fails)} failed")
    for f in rep.fails:
        print("  FAIL", f)
    return 1 if rep.fails else 0


def act(e, rep: Report, action_: str, what: str, expect_ok: bool = True, **params):
    try:
        r = e.act(action_, **params)
    except Exception as exc:                  # noqa: BLE001 - act never raises; if it does, that's a bug
        rep.check(False, f"{what}: {action_} raised", "".join(traceback.format_exception_only(type(exc), exc)).strip())
        return {}
    if expect_ok:
        rep.check(bool(r.get("ok")), f"{what}: {action_}", str(r.get("error")))
    return r


def frame_of(e, h) -> list[int]:
    frames = e.build_frames()
    return list(frames[h["universe"]][h["address"] - 1: h["address"] - 1 + h["channels"]])


def run(e, picks, rep: Report) -> None:
    # a custom venue: an L-shaped room, horizontal trusses, a pole, a pipe
    act(e, rep, "venue_room", "room", width=14, depth=12, height=6,
        outline=[[-7, 0], [7, 0], [7, 12], [0, 12], [0, 8], [-7, 8]])
    act(e, rep, "venue_add", "front truss", item={"type": "rigging", "kind": "truss", "a": [-5, 5, 3], "b": [5, 5, 3]})
    act(e, rep, "venue_add", "back truss", item={"type": "rigging", "kind": "truss", "a": [-5, 5.2, 1], "b": [5, 5.2, 1]})
    act(e, rep, "venue_add", "pole", item={"type": "rigging", "kind": "tower", "a": [6, 0, 6], "b": [6, 4.5, 6]})
    act(e, rep, "venue_add", "pipe", item={"type": "rigging", "kind": "pipe", "a": [-6, 3, 6], "b": [-6, 3, 10]})
    act(e, rep, "venue_add", "dance floor", item={"type": "zone", "kind": "dancefloor",
                                                  "points": [[-4, 4], [4, 4], [4, 9], [-4, 9]]})
    front = next(r for r in e.venue["rigging"] if r["kind"] == "truss")
    pole = next(r for r in e.venue["rigging"] if r["kind"] == "tower")
    pipe = next(r for r in e.venue["rigging"] if r["kind"] == "pipe")
    act(e, rep, "fx_arm", "ARM", state=True)
    placements = [
        ("hung on the front truss", {"rig": front["id"], "stance": "hang"}),
        ("standing on the floor", {"x": -2, "y": 0, "z": 7, "stance": "stand"}),
        ("dragged up to 5 m (free)", {"x": 2, "y": 5, "z": 5}),
        ("on the pole", {"rig": pole["id"]}),
        ("on the pipe", {"rig": pipe["id"]}),
        ("snapped by dragging near the truss", {"x": 1.0, "y": 4.7, "z": 3.1, "snap": True}),
    ]
    for src, key, man, model, typ in picks:
        what0 = f"{typ} / {man} {model}"
        r = act(e, rep, "add_heads", what0, query=f"{man} {model}", qty=1)
        if not r.get("heads"):
            continue
        n = r["heads"][0]
        h = e._head(n)
        d = fixture_kind.describe(h)
        rep.check(d["type"] != "generic" or typ == "generic", f"{what0}: 3D model", f"patched as generic ({h['mode']})")
        for where, place in placements:
            what = f"{what0} {where}"
            params = dict(place)
            r = act(e, rep, "set_place", what, head=n, **params)
            if not r.get("ok"):
                continue
            h = e._head(n)
            hung_engine = (h.get("stance") == "hang") if h.get("stance") else h.get("kind") == "truss"
            if h["y"] >= 2.0 and not (h.get("mount") and venue_vertical(e, h)):
                rep.check(hung_engine, f"{what}: a light up high hangs",
                          f"y={h['y']} stance={h.get('stance')} kind={h.get('kind')}")
            if "snap" in params:
                rep.check((h.get("mount") or {}).get("rig") == front["id"], f"{what}: snapped onto the truss",
                          json.dumps(h.get("mount")))
            probe_head(e, rep, what, n)
        act(e, rep, "remove_heads", what0, heads=[n])


def venue_vertical(e, h) -> bool:
    from app import venue as V
    r = V.rig(e.venue, (h.get("mount") or {}).get("rig"))
    return bool(r and V.is_vertical(r))


def probe_head(e, rep: Report, what: str, n: int) -> None:
    h = e._head(n)
    m = h["map"]
    act(e, rep, "clear_programmer", what)
    act(e, rep, "select_heads", what, heads=[n])
    is_fx = any(r.startswith(("fx_", "laser_")) or r == "fog" for r in m)
    lightish = any(r in m for r in ("dimmer", "red", "white", "shutter", "strobe", "wheel"))
    if lightish and not is_fx:
        act(e, rep, "set_intensity", what, level=100)
        has_colour = any(r in m for r in ("red", "green", "blue", "wheel", "cyan", "magenta"))
        act(e, rep, "set_colour", what, expect_ok=has_colour, hex="#ff0000")
        f = frame_of(e, h)
        rep.check(any(f), f"{what}: full + red reaches the DMX", str(f))
        look = next((row for row in e._looks() if row["n"] == n), {})
        rep.check(look.get("a", 0) > 0, f"{what}: the 3D view shows it lit", str(look))
        if "red" in m:
            rep.check(look.get("hex", "").lower().startswith("#ff") or look.get("hex", "").lower() == "#ff0000",
                      f"{what}: the 3D colour is red", str(look.get("hex")))
    if "pan" in m and "tilt" in m:
        act(e, rep, "floor_safe", what, movement=False)
        r = act(e, rep, "aim_at", what, x=0, y=0, z=6)
        look = next((row for row in e._looks() if row["n"] == n), {})
        solved = e._aim_solve(h, 0, 0, 6, closest=True)
        if solved:
            rep.check(abs(look.get("tilt", -1) - solved[1]) < 0.02 and abs(look.get("pan", -1) - solved[0]) < 0.02,
                      f"{what}: 3D pan/tilt = the aim", f"look {look.get('pan')},{look.get('tilt')} aim {solved[0]:.3f},{solved[1]:.3f}")
            # the 3D beam direction from those numbers must reach the target (or be a stated clamp)
            if len(solved) == 4:
                err = beam_miss(e, h, look, (0, 0, 6))
                rep.check(err < 0.25, f"{what}: beam lands on the aim point", f"{err:.2f} m off")
        for fx in ("circle", "pan_sweep", "tilt_bounce", "figure8"):
            rr = act(e, rep, "run_fx", what, expect_ok=False, name=fx)
            if rr.get("ok"):
                e.build_frames()
                e._looks()
        act(e, rep, "stop_fx", what)
    # every button kind on this light
    kinds = [("flash", {}), ("strobe", {"hz": 8}), ("colour", {"colour": "#00ff00"}), ("kill", {}),
             ("custom", {"dim": 30}), ("custom", {"level": 80, "colour": "#0000ff"}),
             ("fx", {"fx": "rainbow", "mode": "latch"})]
    if is_fx:
        kinds += [("sfx", {}), ("fog", {"level": 50}), ("laser", {"mode": "latch"})]
    for i, (kind, extra) in enumerate(kinds, start=1):
        r = act(e, rep, "quick_set", f"{what} button {kind}", expect_ok=False, page=8, slot=i,
                button={"kind": kind, "target": {"heads": [n]}, **extra})
        if not r.get("ok"):
            # refusing a button kind that doesn't fit this light is fine; crashing is not
            continue
        rr = act(e, rep, "quick_press", f"{what} button {kind}", expect_ok=False, id=f"q8-{i}")
        try:
            e.build_frames()
            e._looks()
        except Exception as exc:                       # noqa: BLE001
            rep.check(False, f"{what} button {kind}: frames after press", repr(exc))
        act(e, rep, "quick_press", f"{what} button {kind}", expect_ok=False, id=f"q8-{i}", down=False)
        if rr.get("ok") is False and "ARM" not in str(rr.get("error")) and "nothing" not in str(rr.get("error")):
            rep.check(False, f"{what} button {kind}: press", str(rr.get("error")))
    act(e, rep, "quick_release_all", what)
    if is_fx:
        for name, params in (("fx_fire", {"heads": [n], "seconds": 0.5}), ("fx_fog", {"heads": [n], "level": 40}),
                             ("fx_laser", {"heads": [n], "down": True, "owner": "rc"})):
            rr = act(e, rep, name, what, expect_ok=False, **params)
            if rr.get("ok"):
                f = frame_of(e, h)
                rep.check(any(f), f"{what}: {name} reaches the DMX", str(f))
        act(e, rep, "fx_kill", what)
    for q in range(1, 11):
        e.act("quick_set", page=8, slot=q, clear=True)


def beam_miss(e, h, look, target) -> float:
    """How far the 3D beam (from the look's pan/tilt and the model's pivot)
    passes from the target: the same maths as web/js/stage/stage.js."""
    rng = look.get("deg") or {}
    pr, tr = rng.get("pan", [-270, 270]), rng.get("tilt", [-135, 135])
    p = math.radians(pr[0] + look["pan"] * (pr[1] - pr[0]))
    t = math.radians(tr[0] + look["tilt"] * (tr[1] - tr[0]))
    d = fixture_kind.describe(h)
    if d["type"] == "scanner":
        v = [math.sin(p) * math.cos(t), -math.sin(t), math.cos(p) * math.cos(t)]
    else:
        v = [math.sin(t) * math.sin(p), math.cos(t), math.sin(t) * math.cos(p)]
    hung = (h.get("stance") == "hang") if h.get("stance") else h.get("kind") == "truss"
    if hung:
        v = [-v[0], -v[1], v[2]]
    piv = 0.372 if d.get("heads") else e._AIM_PIVOT.get(d["type"], 0.4)
    o = [h["x"], h["y"] + (-piv if hung else piv), h["z"] + (0.14 if d["type"] == "scanner" else 0)]
    w = [target[i] - o[i] for i in range(3)]
    along = sum(w[i] * v[i] for i in range(3))
    return math.sqrt(max(0.0, sum(x * x for x in w) - along * along))


if __name__ == "__main__":
    sys.exit(main())
