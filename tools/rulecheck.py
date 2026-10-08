"""The desk's promises about every light, checked on EVERY light in the
fixture libraries - not one product at a time.

A bug found on one light (the Intimidator Wave 360: a strobe read
backwards, no rainbow on a colour-wheel light) is nearly always a bug in a
whole kind of light.  So each one found becomes a RULE here, and the rule
runs on all ~2,400 library lights: the other lights of that kind show up
at once, and a fix is proven on all of them.

    python tools/rulecheck.py                    # every light, every rule
    python tools/rulecheck.py --only "wave 360"  # lights whose name matches
    python tools/rulecheck.py --rule strobe_in_range
    python tools/rulecheck.py --update-known     # accept today's failures as the baseline

Lights already known to break a rule are listed in tools/rules_known.json
(a rule that needs data the fixture file doesn't have - the colours of a
wheel it only calls "Color 7").  The run fails only on a NEW failure, and
says when a known one has been fixed (then --update-known drops it).
Exit code 1 on a new failure.  Nothing in data/ is touched.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KNOWN = ROOT / "tools" / "rules_known.json"
GREY = "#cbd5e1"                       # the 3D's "no colour known" grey


# --------------------------------------------------------------------- rules
# Each: (id, what the operator would see if it broke, check(e, head) ->
#        None (doesn't apply) | "" (kept) | "why it broke").

def _strobe_role(e, h):
    role = e._shutter_role(h)
    return role if role and (e.head_ranges(h).get(role) or {}).get("strobe_ranges") else None


def r_strobe_in_range(e, h):
    role = _strobe_role(e, h)
    if not role:
        return None
    ranges = e.head_ranges(h)[role]["strobe_ranges"]
    st = e.strobe_steps(h, role)
    out = [f"{k} {st[k]}" for k in ("slow", "medium", "fast") if not any(a <= st[k] <= b for a, b in ranges)]
    return f"{', '.join(out)} not inside its strobe ranges {ranges[:3]}" if out else ""


def r_strobe_direction(e, h):
    role = _strobe_role(e, h)
    if not role:
        return None
    st = e.strobe_steps(h, role)
    fast, slow = e._strobe_hz(h, {role: st["fast"]}, None), e._strobe_hz(h, {role: st["slow"]}, None)
    return "" if fast > slow > 0 else f"Fast flashes at {fast} Hz, Slow at {slow} Hz in the 3D"


def _colours_it_makes(e, h):
    from app import fxlib
    return len(fxlib._palette(fxlib.normalise(h["map"]))) >= 2 or bool(e._wheel_colours(h))


def r_colour_effects_offered(e, h):
    if e._head_class(h) != "light" or not _colours_it_makes(e, h):
        return None
    names = e._fx_can(h)
    return "" if any(n in names for n in ("rainbow", "colour_chase")) else f"makes colours but is offered only {names[:6]}"


def r_colour_effect_moves(e, h):
    if e._head_class(h) != "light" or "colour_chase" not in e._fx_can(h):
        return None
    n = h["head_no"]
    e.act("select_heads", heads=[n])
    e.act("set_intensity", level=100)
    r = e.act("run_fx", name="colour_chase", params={"speed": 5, "rate": 5})
    if not r.get("ok"):
        return f"Colour chase is offered but won't start: {r.get('error')}"
    a = h["address"] - 1
    seen = set()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.2 and len(seen) < 2:
        seen.add(bytes(e.build_frames()[h["universe"]][a:a + len(h["map"])]))
        time.sleep(0.05)
    e.act("stop_fx")
    e.act("clear_programmer")
    return "" if len(seen) >= 2 else "Colour chase runs but its DMX never changes"


def r_lit_shows_colour(e, h):
    """A lit light that makes colours never shows the 3D's 'unknown' grey."""
    if e._head_class(h) != "light" or not _colours_it_makes(e, h):
        return None
    n = h["head_no"]
    e.act("select_heads", heads=[n])
    e.act("set_intensity", level=100)
    r = e.act("set_colour", hex="#ff0000")
    if not r.get("ok"):
        e.act("clear_programmer")
        return f"red can't be set: {r.get('error')}"
    row = next((x for x in e._looks() if x.get("n") == n), {})
    e.act("clear_programmer")
    hexes = [row.get("hex")] + [c.get("hex") for c in row.get("cells") or []]
    return f"red shows as grey in the 3D ({row.get('hex')})" if GREY in hexes else ""


def _repeated(h):
    from app import merge
    return merge._repeated(h["map"])


def r_every_head_moves(e, h):
    reps = _repeated(h)
    axes = [a for a in ("pan", "tilt") if reps.get(a, 0) > 1]
    if not axes:
        return None
    n = h["head_no"]
    a = h["address"] - 1
    idx = {ax: [i for i, r in enumerate(h["map"]) if r == ax] for ax in axes}
    e.act("select_heads", heads=[n])
    e.act("light_test", head=n, step="start")
    before = e.build_frames()[h["universe"]][a:a + len(h["map"])]
    for ax in axes:
        e.act("light_test", head=n, step=ax, value=0.9)
    after = e.build_frames()[h["universe"]][a:a + len(h["map"])]
    e.act("light_test", head=n, step="end")
    still = [f"{ax} {i + 1}" for ax in axes for i in idx[ax] if before[i] == after[i]]
    return f"channel(s) {', '.join(still)} didn't move" if still else ""


def r_every_head_colours(e, h):
    reps = _repeated(h)
    if reps.get("red", 0) < 2:
        return None
    n = h["head_no"]
    a = h["address"] - 1
    e.act("select_heads", heads=[n])
    e.act("set_intensity", level=100)
    r = e.act("set_colour", hex="#ff0000")
    if not r.get("ok"):
        e.act("clear_programmer")
        return f"red can't be set: {r.get('error')}"
    fr = e.build_frames()[h["universe"]][a:a + len(h["map"])]
    e.act("clear_programmer")
    dark = [i + 1 for i, r in enumerate(h["map"]) if r == "red" and fr[i] < 200]
    return f"red channel(s) {dark} not lit on red" if dark else ""


RULES = [
    ("strobe_in_range", "Strobe Slow / Medium / Fast land inside the light's own strobe range", r_strobe_in_range),
    ("strobe_direction", "Fast strobes faster than Slow (a 'fast to slow' range read the right way)", r_strobe_direction),
    ("colour_effects_offered", "A light that makes colours is offered colour effects", r_colour_effects_offered),
    ("colour_effect_moves", "An offered colour chase really changes the DMX", r_colour_effect_moves),
    ("lit_shows_colour", "A lit colour light never shows as 'unknown' grey in the 3D", r_lit_shows_colour),
    ("every_head_moves", "Every head of a multi-head light moves", r_every_head_moves),
    ("every_head_colours", "Every head of a multi-head light takes the colour", r_every_head_colours),
]


# --------------------------------------------------------------------- runner

def worker(args: tuple) -> list[dict]:
    batch, only_rule = args
    from app import engine as eng, fixlib, fixtures
    rules = [r for r in RULES if not only_rule or r[0] == only_rule]
    out = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            for src, key, name, *mode in batch:
                row = {"id": f"{src}:{key}", "name": name, "res": {}}
                try:
                    parsed = fixlib.load(src, key)
                    got = fixtures.store_parsed(db, parsed, f"{src}:{key}")
                    fid = (got.get("imported") or [{}])[0].get("fixture_id")
                    r = e.act("add_heads", fixture_id=fid, qty=1, universe=1, address=1, mode=mode[0] if mode else None)
                    if not r.get("ok") or not r.get("heads"):
                        out.append(row)
                        continue
                    h = e._head(r["heads"][0])
                    for rid, _title, fn in rules:
                        try:
                            res = fn(e, h)
                        except Exception as exc:      # noqa: BLE001 - a crash is a finding
                            res = "crash: " + "".join(traceback.format_exception_only(type(exc), exc)).strip()[:200]
                        if res is not None:
                            row["res"][rid] = res
                    e.act("clear_programmer")
                    e.act("remove_heads", heads=[h["head_no"]])
                except Exception as exc:              # noqa: BLE001
                    row["error"] = str(exc)[:200]
                    try:
                        e.act("patch_clear")
                    except Exception:                 # noqa: BLE001
                        pass
                out.append(row)
        finally:
            e.shutdown()
    return out


def run(todo: list[tuple], jobs: int = 1, only_rule: str = "") -> list[dict]:
    size = max(1, min(40, len(todo) // (max(1, jobs) * 4) or 1))
    batches = [(todo[i:i + size], only_rule) for i in range(0, len(todo), size)]
    if jobs <= 1:
        return [r for b in batches for r in worker(b)]
    rows: list[dict] = []
    with mp.get_context("spawn").Pool(jobs) as pool:
        for part in pool.imap_unordered(worker, batches):
            rows += part
    return rows


def judge(rows: list[dict], known: dict) -> dict:
    """{rule: {checked, kept, new: [(id, name, why)], known: [...], fixed: [ids]}}"""
    out = {rid: {"checked": 0, "kept": 0, "new": [], "known": [], "fixed": []} for rid, *_ in RULES}
    seen = defaultdict(set)
    for r in rows:
        for rid, why in r["res"].items():
            o = out[rid]
            o["checked"] += 1
            seen[rid].add(r["id"])
            if not why:
                o["kept"] += 1
                if r["id"] in known.get(rid, []):
                    o["fixed"].append(r["id"])
            elif r["id"] in known.get(rid, []):
                o["known"].append((r["id"], r["name"], why))
            else:
                o["new"].append((r["id"], r["name"], why))
    return out


def main() -> int:
    from libsweep import lights
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--rule", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    ap.add_argument("--update-known", action="store_true")
    a = ap.parse_args()
    todo = lights(a.only, a.limit)
    print(f"{len(todo)} lights x {len([r for r in RULES if not a.rule or r[0] == a.rule])} rules, {a.jobs} workers")
    t0 = time.time()
    rows = run(todo, a.jobs, a.rule)
    known = json.loads(KNOWN.read_text(encoding="utf-8")) if KNOWN.is_file() else {}
    res = judge(rows, known)
    print(f"done in {time.time() - t0:.0f} s\n")
    new_total = 0
    for rid, title, _fn in RULES:
        if a.rule and rid != a.rule:
            continue
        o = res[rid]
        print(f"{rid:24} {o['kept']:5} kept of {o['checked']:5}   new {len(o['new']):4}   known {len(o['known']):4}   fixed {len(o['fixed']):3}   - {title}")
        for i, name, why in o["new"][:8]:
            print(f"      NEW  {name}: {why}")
        new_total += len(o["new"])
    if a.update_known:
        keep = {rid: sorted({i for i, _n, _w in res[rid]["new"] + res[rid]["known"]}) for rid, *_ in RULES}
        if a.only or a.rule or a.limit:
            # a partial run only changes what it looked at
            for rid in keep:
                looked = {r["id"] for r in rows if rid in r["res"]}
                keep[rid] = sorted((set(known.get(rid, [])) - looked) | set(keep[rid]))
        KNOWN.write_text(json.dumps({k: v for k, v in keep.items() if v}, indent=1) + "\n", encoding="utf-8")
        print(f"\nknown failures written to {KNOWN.relative_to(ROOT)}")
        return 0
    return 1 if new_total else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tools"))
    sys.exit(main())
