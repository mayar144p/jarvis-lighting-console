"""The brand-by-brand debug rigs: the top 20 brands, up to 15 products each
- its effects machines first (confetti, CO2, flame, smoke, haze), then its
lights taking turns by kind (moving heads, washes / PARs, bars, strobes,
lasers, scanners, effect lights) - and a check per brand.

    python tools/brands.py                 # the picks, brand by brand
    python tools/brands.py --json out.json # the picks for vischeck.mjs --brands
    python tools/brands.py --check         # every pick: Full, Blackout, colour,
                                           # gobo, move, Locate, effects, and
                                           # effects machines fire only when armed
    python tools/brands.py --brand Antari --check        # one brand (any brand)
    python tools/brands.py --product "robe megapointe" --check   # products by name

The checks per light are libsweep's (tools/libsweep.py).  Nothing in data/
is touched.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# the brand, and how the libraries spell it
BRANDS = [
    ("Chauvet", ("chauvet", "chauvet dj", "chauvet professional")),
    ("ADJ", ("american dj", "adj")),
    ("Martin", ("martin", "martin professional")),
    ("Robe", ("robe",)),
    ("Clay Paky", ("clay paky", "claypaky")),
    ("Elation", ("elation", "elation professional")),
    ("GLP", ("glp",)),
    ("Ayrton", ("ayrton",)),
    ("ETC", ("etc",)),
    ("SGM", ("sgm",)),
    ("Showtec", ("showtec",)),
    ("Cameo", ("cameo",)),
    ("Stairville", ("stairville",)),
    ("Eurolite", ("eurolite",)),
    ("Varytec", ("varytec",)),
    ("beamZ", ("beamz",)),
    ("Briteq", ("briteq",)),
    ("Laserworld", ("laserworld",)),
    ("MagicFX", ("magicfx", "magic fx")),
    ("Antari", ("antari",)),
]
PER_BRAND = 15
# a channel whose bottom range is the beam's "off"
OFF_CAP = re.compile(r"^\s*(laser\s+)?(off|blackout|blanking|no beam|beam off|no output)\b", re.I)

# what a product is, from its library type and name: effects machines first
SFX_KINDS = [
    ("confetti", r"confetti|streamer|stadiumshot|stadiumblaster|stadiumblower|funfetti"),
    ("co2", r"co2|cryo"),
    ("flame", r"flame|fire|sparkular|spark"),
    ("smoke", r"smoke|fog|geyser|hurricane|fazer"),
    ("haze", r"haze"),
]
LIGHT_KINDS = [
    ("moving head", r"moving head"),
    ("wash / par", r"color changer|par\b|wash"),
    ("bar", r"bar|batten|pixel"),
    ("strobe", r"strobe|blinder"),
    ("laser", r"laser"),
    ("scanner", r"scanner"),
    ("effect light", r"flower|effect|matrix|derby"),
    ("dimmer / other", r"dimmer|other|."),
]


def kind_of(row: dict) -> str:
    text = f"{row.get('type') or ''} {row['model']}".lower()
    for kind, pat in SFX_KINDS:
        if re.search(pat, text):
            return kind
    t = (row.get("type") or "").lower()
    for kind, pat in LIGHT_KINDS:
        if re.search(pat, t):
            return kind
    return "dimmer / other"


def pick(brand: str | None = None, product: str | None = None) -> list[dict]:
    """[{brand, items: [{src, key, name, kind}]}] - up to 15 per brand.
    `brand`: that brand only (one of the 20 or any other); `product`: every
    library light whose maker + model has all those words (up to 15)."""
    from app import fixlib
    brands = BRANDS
    if brand:
        want = brand.strip().lower()
        brands = [b for b in BRANDS if b[0].lower() == want or want in b[1]] or [(brand.strip(), (want,))]
    if product:
        words = re.sub(r"[^a-z0-9 ]", " ", product.lower()).split()
        found, seen = [], set()
        for src in ("jarvis", "qlc", "ofl"):
            for r in fixlib.index(src):
                name = f"{r['manufacturer']} {r['model']}"
                flat = re.sub(r"[^a-z0-9 ]", " ", name.lower())
                dedupe = re.sub(r"[^a-z0-9]", "", name.lower())
                if all(w in flat or w in flat.replace(" ", "") for w in words) and dedupe not in seen:
                    seen.add(dedupe)
                    found.append({"src": src, "key": r["key"], "name": name, "kind": kind_of(r), "type": r.get("type") or ""})
        return [{"brand": f"Products: {product}", "items": found[:PER_BRAND]}]
    rows = defaultdict(list)
    alias = {a: b for b, names in brands for a in names}
    seen = set()
    for src in ("jarvis", "qlc", "ofl"):            # the curated library first
        for r in fixlib.index(src):
            brand = alias.get(r["manufacturer"].strip().lower())
            if not brand:
                continue
            name = f"{r['manufacturer']} {r['model']}"
            dedupe = re.sub(r"[^a-z0-9]", "", r["model"].lower())
            if (brand, dedupe) in seen:                # the same light in two libraries
                continue
            seen.add((brand, dedupe))
            rows[brand].append({"src": src, "key": r["key"], "name": name, "kind": kind_of(r),
                               "type": r.get("type") or ""})
    out = []
    for brand, _names in brands:
        items = sorted(rows.get(brand, []), key=lambda x: x["name"])
        chosen = []
        # every effects machine kind it makes, one each, first
        for kind, _ in SFX_KINDS:
            chosen += [x for x in items if x["kind"] == kind][:2 if kind in ("smoke", "haze") else 3]
        # then its lights, a kind at a time, round and round
        pools = [[x for x in items if x["kind"] == k] for k, _ in LIGHT_KINDS]
        while len(chosen) < PER_BRAND and any(pools):
            for pool in pools:
                if pool and len(chosen) < PER_BRAND:
                    chosen.append(pool.pop(0))
        # room left (a brand that only makes effects machines): the rest
        chosen += [x for x in items if x not in chosen][:max(0, PER_BRAND - len(chosen))]
        out.append({"brand": brand, "items": chosen[:PER_BRAND]})
    return out


def check(rigs: list[dict]) -> int:
    """libsweep's checks on every pick, and: an effects machine fires only
    when armed."""
    import tools.libsweep as ls
    from app import engine as eng, fixlib, fixtures
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        e.act("venue_template", name="club")
        try:
            for rig in rigs:
                tally = Counter()
                fails = []
                for it in rig["items"]:
                    parsed = fixlib.load(it["src"], it["key"])
                    fid = (fixtures.store_parsed(db, parsed, f"{it['src']}:{it['key']}").get("imported") or [{}])[0].get("fixture_id")
                    r = e.act("add_heads", fixture_id=fid, qty=1, universe=1, address=1)
                    if not r.get("ok"):
                        fails.append(f"{it['name']}: does not patch ({r.get('error')})")
                        continue
                    n = r["heads"][0]
                    res = ls.probe(e, n)
                    h = e._head(n)
                    if res.get("sfx", ("skip",))[0] != "skip" or any(x.startswith(("fx_", "laser_")) or x == "fog" for x in h["map"]):
                        # disarmed: a fire / laser press changes nothing on the
                        # wire (fog and haze need no ARM, by design: they are
                        # checked to fire, in "sfx")
                        e.act("fx_kill")
                        before = bytes(e.build_frames()[h["universe"]])
                        for a, p in (("fx_fire", {"heads": [n], "down": True}), ("fx_laser", {"heads": [n], "down": True})):
                            e.act(a, **p)
                        after = bytes(e.build_frames()[h["universe"]])
                        gated = any(x.startswith(("fx_fire", "laser_")) for x in h["map"])
                        if gated:
                            res["armed-only"] = ("ok", "") if before == after else ("fail", "a fire / laser press changed the DMX while DISARMED")
                        # disarmed, every effect channel the programmer may
                        # set, at several values: a channel whose bottom range
                        # is the beam's off ("Laser off", "No beam", ...) stays
                        # in it on the wire, and the 3D shows nothing firing
                        if e._head_class(h) != "light":
                            mode = next((m for m in parsed[0]["modes"] if m["name"] == h.get("mode")), parsed[0]["modes"][0])
                            offs = []
                            for i, d in enumerate(mode.get("detail") or []):
                                zero = next((c for c in d.get("caps") or [] if c[0] <= 0 <= c[1]), None)
                                if zero and OFF_CAP.search(str(zero[2])):
                                    offs.append((i, zero[1], d.get("label") or ""))
                            e.act("select_heads", heads=[n])
                            roles = [r for r in dict.fromkeys(h["map"]) if r.startswith(("fx_", "laser_", "aux"))
                                     and r not in ("fx_fire", "fx_arm", "laser_on")]
                            lit = []
                            for v in (255, 128, 200, 64):
                                for r in roles:
                                    e.act("set_attribute", attribute=r, value=v)
                                frame = e.build_frames()[h["universe"]]
                                fx = next((x.get("fx") for x in e._looks() if x.get("n") == n), None) or {}
                                if fx.get("laser") or fx.get("fire"):
                                    lit.append(f"3D lit at {v}")
                                if "laser_on" in h["map"]:     # its output channel: at its off
                                    i = h["map"].index("laser_on")
                                    off = int((e.head_ranges(h).get("laser_on") or {}).get("off_value") or 0)
                                    if frame[h["address"] - 1 + i] != off:
                                        lit.append(f"laser output at {frame[h['address'] - 1 + i]}")
                                else:                            # no output channel: its beam switch
                                    lit += [f"{label} at {frame[h['address'] - 1 + i]}" for i, hi, label in offs
                                            if frame[h["address"] - 1 + i] > hi]
                            e.act("clear_programmer")
                            if roles:
                                res["programmed-disarmed"] = (("fail", "beam on while DISARMED: " + ", ".join(lit[:3]))
                                                              if lit else ("ok", ""))
                        e.act("fx_kill")
                    for c, (state, detail) in res.items():
                        tally[f"{c} {state}"] += 1
                        if state == "fail":
                            fails.append(f"{it['name']} ({it['kind']}) {c}: {detail}")
                    e.act("remove_heads", heads=[n])
                    e.act("clear_programmer")
                ok = sum(v for k, v in tally.items() if k.endswith(" ok"))
                print(f"\n{rig['brand']}: {len(rig['items'])} products, {ok} checks ok, {len(fails)} failed")
                for f in fails:
                    print("   FAIL", f)
                bad += len(fails)
        finally:
            e.shutdown()
    return bad


def _arg(flag: str) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def main() -> int:
    rigs = pick(_arg("--brand"), _arg("--product"))
    if not any(r["items"] for r in rigs):
        print("no library products match - check the spelling, or import the light's file first")
        return 2
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        Path(out).write_text(json.dumps(rigs, indent=1), encoding="utf-8")
    for rig in rigs:
        kinds = Counter(x["kind"] for x in rig["items"])
        print(f"{rig['brand']:11} {len(rig['items']):2}  " + ", ".join(f"{k} {v}" for k, v in kinds.items()))
    if "--check" in sys.argv:
        bad = check(rigs)
        print(f"\n{sum(len(r['items']) for r in rigs)} products, {bad} failures")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
