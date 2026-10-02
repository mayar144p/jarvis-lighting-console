"""Every light in the fixture libraries, every control: patched in its
default mode, does each control the light has actually work - on the DMX
and in what the 3D view is told?

    python tools/libsweep.py                 # QLC+, OFL and Jarvis libraries
    python tools/libsweep.py --limit 200     # the first 200 (a quick look)
    python tools/libsweep.py --only "moover" # names containing this
    python tools/libsweep.py --out report.json
    python tools/libsweep.py --all-modes     # every mode of every light, not just the default

Per light (only the controls it has; the rest are "skip"):
  light    Full (set_intensity 100) reaches the DMX and the 3D view shows it lit
  blackout then BLACKOUT darkens it
  colour   red then blue: the DMX changes and the 3D colour is red, then blue
           (a wheel: the nearest slot - as long as red and blue differ)
  gobo     the second gobo: the gobo channel moves and the 3D view is told
  move     aim at the dance floor: pan / tilt move; 3D pan / tilt = the DMX
  locate   Locate: lit, open white, centred
  effects  every effect it offers: runs, and its DMX changes over time
  sfx      lasers / fog / confetti...: fire reaches the DMX once armed
Nothing in data/ is touched; each worker has its own scratch database.
Exit code 1 when any light fails a control it has.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTROLS = ("light", "blackout", "colour", "gobo", "move", "locate", "effects", "sfx")


def lights(only: str = "", limit: int = 0, all_modes: bool = False) -> list[tuple]:
    """(src, key, name) per light - or (src, key, name, mode) per mode."""
    from app import fixlib
    out = []
    for src in ("qlc", "ofl", "jarvis"):
        try:
            rows = fixlib.index(src)
        except Exception:          # noqa: BLE001 - a missing bundle is not this check's business
            continue
        for row in rows:
            name = f"{row['manufacturer']} {row['model']}"
            if only and only.lower() not in name.lower():
                continue
            if all_modes:
                out += [(src, row["key"], name, m[0]) for m in row.get("modes") or [] if m]
            else:
                out.append((src, row["key"], name))
    return out[:limit] if limit else out


def _hue(hexc: str) -> tuple[float, float]:
    """(hue 0..360, saturation 0..1) of #rrggbb."""
    import colorsys
    h = (hexc or "#000000").lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.0, 0.0
    hh, s, _v = colorsys.rgb_to_hsv(r, g, b)
    return hh * 360, s


def _near(hue: float, want: float, tol: float = 50) -> bool:
    d = abs(hue - want) % 360
    return min(d, 360 - d) <= tol


class Probe:
    def __init__(self, e, n: int):
        self.e, self.n = e, n
        self.h = e._head(n)
        self.m = self.h["map"]

    def frame(self, now: float | None = None) -> list[int]:
        f = self.e.build_frames(now)
        u = f[self.h["universe"]]
        return list(u[self.h["address"] - 1: self.h["address"] - 1 + self.h["channels"]])

    def look(self) -> dict:
        return next((x for x in self.e._looks() if x["n"] == self.n), {})

    def ch(self, role: str, now: float | None = None) -> int | None:
        if role not in self.m:
            return None
        return self.frame(now)[self.m.index(role)]

    def reset(self):
        e = self.e
        e.act("stop_fx")
        e.act("quick_release_all")
        e.act("clear_programmer")
        e.act("select_heads", heads=[self.n])


def probe(e, n: int) -> dict[str, tuple[str, str]]:
    """{control: (ok | fail | skip, detail)} for one patched light."""
    from app import fixlib as fxlib_lib  # noqa: F401 - imported for the side effect of loading
    from app import fxlib, motion
    p = Probe(e, n)
    m = p.m
    res: dict[str, tuple[str, str]] = {}
    # an effect machine: something it fires (a light's "sound active"
    # channel is fx_mode, and that alone doesn't make it one)
    is_sfx = any(r in ("fx_fire", "fog", "fx_arm") or r.startswith("laser_") for r in m)
    emits = any(r in m for r in ("dimmer", "red", "green", "blue", "white", "cyan", "shutter", "wheel", "amber", "uv"))

    # light
    p.reset()
    if emits and not is_sfx:
        r = e.act("set_intensity", level=100)
        f = p.frame()
        lk = p.look()
        if not r.get("ok"):
            res["light"] = ("fail", f"Full refused: {r.get('error')}")
        elif "dimmer" in m and not f[m.index("dimmer")]:
            res["light"] = ("fail", "Full: the dimmer channel stays at 0")
        elif "dimmer" not in m and any(r in m for r in ("red", "green", "blue", "white")) and not any(f):
            res["light"] = ("fail", "Full: nothing on the DMX (no dimmer, colour channels at 0)")
        elif not lk.get("a"):
            res["light"] = ("fail", f"Full: the 3D view shows it dark (DMX {f[:12]})")
        else:
            res["light"] = ("ok", "")
        # blackout: lit at Full, then BLACKOUT must darken it (on the wire,
        # which the look is built from)
        if res["light"][0] == "ok":
            e.act("blackout", state=1)
            dark = p.look()
            e.act("blackout", state=0)
            if e._lamp_only(e._head(n)):
                res["blackout"] = ("skip", "a lamp DMX can't close")
            else:
                res["blackout"] = ("ok", "") if not dark.get("a") else ("fail", f"still lit in BLACKOUT (DMX {p.frame()[:12]})")
        else:
            res["blackout"] = ("skip", "")
    else:
        res["light"] = ("skip", "")
        res["blackout"] = ("skip", "")

    # colour
    mixes = any(r in m for r in ("red", "cyan", "hue"))
    wheel = "wheel" in m
    if (mixes or wheel) and not is_sfx:
        p.reset()
        e.act("set_intensity", level=100)
        r1 = e.act("set_colour", colour="#ff0000")
        f1, h1 = p.frame(), p.look().get("hex", "")
        r2 = e.act("set_colour", colour="#0000ff")
        f2, h2 = p.frame(), p.look().get("hex", "")
        if "aren't named" in str(r1.get("error")) or r1.get("unknown_wheel"):
            res["colour"] = ("skip", "unnamed wheel (the operator names it once)")
        elif not (r1.get("ok") and r2.get("ok")):
            res["colour"] = ("fail", f"refused: {r1.get('error') or r2.get('error')}")
        elif f1 == f2:
            res["colour"] = ("fail", "red and blue give the same DMX")
        elif mixes:
            (hr, sr), (hb, sb) = _hue(h1), _hue(h2)
            ok = _near(hr, 0) and sr > 0.4 and _near(hb, 230, 40) and sb > 0.4
            res["colour"] = ("ok", "") if ok else ("fail", f"3D shows {h1} for red, {h2} for blue")
        else:
            res["colour"] = ("ok", "") if h1 != h2 else ("fail", f"3D shows {h1} for both red and blue (wheel)")
    else:
        res["colour"] = ("skip", "")

    # gobo
    if "gobo" in m and not is_sfx:
        p.reset()
        e.act("set_intensity", level=100)
        slots = [s for s in e._wheel_slots(p.h, "gobo") if s.get("to", 0) > 0] if hasattr(e, "_wheel_slots") else []
        target = slots[1] if len(slots) > 1 else (slots[0] if slots else None)
        val = (target["from"] + target["to"]) // 2 if target else 40
        r = e.act("set_attribute", attribute="gobo", value=val)
        g = p.ch("gobo")
        beam = (p.look().get("beam") or {}).get("gobo")
        if not r.get("ok"):
            res["gobo"] = ("fail", f"refused: {r.get('error')}")
        elif not g:
            res["gobo"] = ("fail", f"gobo {val}: the channel stays at 0")
        elif beam is None or abs(beam - g / 255) > 0.02:
            res["gobo"] = ("fail", f"3D gobo {beam} vs DMX {g}")
        else:
            res["gobo"] = ("ok", "")
    else:
        res["gobo"] = ("skip", "")

    # move
    if "pan" in m and "tilt" in m:
        p.reset()
        e.act("set_intensity", level=100)
        e.act("floor_safe", movement=False)
        before = (p.ch("pan"), p.ch("tilt"))
        r = e.act("aim_at", x=0, y=0, z=6)
        lk = p.look()
        pan_v, tilt_v = p.ch("pan"), p.ch("tilt")
        if not r.get("ok"):
            res["move"] = ("fail", f"aim refused: {r.get('error')}")
        elif (pan_v, tilt_v) == before:
            res["move"] = ("fail", "aiming moved neither pan nor tilt")
        else:
            # 3D pan / tilt (0..1) against the coarse DMX byte (fine adds < 1/255)
            dp = abs(lk.get("pan", -9) - pan_v / 255)
            dt = abs(lk.get("tilt", -9) - tilt_v / 255)
            res["move"] = ("ok", "") if dp < 0.01 and dt < 0.01 else \
                ("fail", f"3D pan/tilt {lk.get('pan')},{lk.get('tilt')} vs DMX {pan_v},{tilt_v}")
    else:
        res["move"] = ("skip", "")

    # locate
    if emits and not is_sfx:
        p.reset()
        r = e.act("locate")
        lk = p.look()
        bad = []
        if not r.get("ok"):
            bad.append(f"refused: {r.get('error')}")
        if not lk.get("a"):
            bad.append("dark in 3D")
        if "pan" in m and abs(lk.get("pan", 0.5) - 0.5) > 0.02:
            bad.append(f"pan {lk.get('pan')}")
        if "tilt" in m and abs(lk.get("tilt", 0.5) - 0.5) > 0.02:
            bad.append(f"tilt {lk.get('tilt')}")
        hx = lk.get("hex", "#ffffff")
        _hh, sat = _hue(hx)
        white_slot = not wheel or mixes or any(
            str(x.get("name", "")).strip().lower() in ("open", "white", "clear") or (x.get("hex") or "").lower() in ("#ffffff",)
            or _hue(x.get("hex") or "#000000")[1] < 0.2 and (x.get("hex") or "#000000") != "#000000"
            for x in e._wheel_slots(p.h))
        if (mixes or wheel) and sat > 0.35 and white_slot:
            bad.append(f"not white: {hx}")
        res["locate"] = ("fail", "; ".join(bad)) if bad else ("ok", "")
        e.act("clear_programmer")
    else:
        res["locate"] = ("skip", "")

    # effects
    if not is_sfx:
        p.reset()
        e.act("set_intensity", level=100)
        avail = [x["name"] for x in (e.act("fx_available", heads=[n]).get("available") or [])]
        bad = []
        for name in [x for x in avail if x not in ("dimmer_chase",)]:   # a chase BETWEEN lights: one light can't show it
            e.act("stop_fx")
            r = e.act("run_fx", name=name, heads=[n], params={"speed": 1.0} if name not in motion.ALL_KINDS else {})
            if not r.get("ok"):
                bad.append(f"{name}: {r.get('error')}")
                continue
            t0 = time.monotonic()
            frames = {tuple(p.frame(t0 + i * 0.213)) for i in range(25)}
            if len(frames) < 2:
                bad.append(f"{name}: the DMX never changes")
        e.act("stop_fx")
        if not avail:
            res["effects"] = ("skip", "none offered")
        else:
            res["effects"] = ("fail", "; ".join(bad)) if bad else ("ok", f"{len(avail)}")
    else:
        res["effects"] = ("skip", "")

    # special effects
    if is_sfx:
        p.reset()
        e.act("fx_arm", state=True)
        fired = []
        for name, params in (("fx_fire", {"heads": [n], "seconds": 0.5}), ("fx_fog", {"heads": [n], "level": 60}),
                             ("fx_laser", {"heads": [n], "down": True, "owner": "rc"})):
            rr = e.act(name, **params)
            if rr.get("ok"):
                fired.append((name, any(p.frame())))
        e.act("fx_kill")
        if not fired:
            res["sfx"] = ("fail", "nothing fires it (no output channel found)")
        else:
            res["sfx"] = ("ok", "") if any(ok for _n, ok in fired) else ("fail", f"fired {fired} but the DMX stays dark")
    else:
        res["sfx"] = ("skip", "")
    _ = fxlib
    return res


def worker(batch: list[tuple]) -> list[dict]:
    from app import engine as eng, fixlib, fixtures
    out = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "f.db"
        fixtures.seed_generics(db)
        e = eng.Engine(db_path=db, dry_run=True, show_dir=tmp / "s")
        try:
            e.act("venue_template", name="club")
            for src, key, name, *mode in batch:
                mode = mode[0] if mode else None
                row = {"src": src, "key": key, "name": name}
                try:
                    parsed = fixlib.load(src, key)
                    if not parsed or not parsed[0].get("modes"):
                        row["error"] = "no modes in the library file"
                        out.append(row)
                        continue
                    got = fixtures.store_parsed(db, parsed, f"{src}:{key}")
                    fid = (got.get("imported") or [{}])[0].get("fixture_id")
                    r = e.act("add_heads", fixture_id=fid, qty=1, universe=1, address=1, mode=mode)
                    if not r.get("ok") or not r.get("heads"):
                        row["error"] = f"does not patch: {r.get('error')}"
                        out.append(row)
                        continue
                    n = r["heads"][0]
                    h = e._head(n)
                    row["mode"], row["channels"] = h.get("mode"), h.get("channels")
                    if mode and h.get("mode") != mode:
                        row["error"] = f"asked for mode {mode!r}, got {h.get('mode')!r}"
                        e.act("remove_heads", heads=[n])
                        out.append(row)
                        continue
                    row["res"] = probe(e, n)
                    e.act("remove_heads", heads=[n])
                except Exception as exc:     # noqa: BLE001 - a crash is a finding
                    row["error"] = "crash: " + "".join(traceback.format_exception_only(type(exc), exc)).strip()[:300]
                    try:
                        e.act("patch_clear")
                    except Exception:        # noqa: BLE001
                        pass
                out.append(row)
        finally:
            e.shutdown()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    ap.add_argument("--out", default="")
    ap.add_argument("--all-modes", action="store_true")
    a = ap.parse_args()
    todo = lights(a.only, a.limit, a.all_modes)
    print(f"{len(todo)} lights, {a.jobs} workers")
    size = max(1, min(60, len(todo) // (a.jobs * 4) or 1))
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    t0 = time.time()
    rows: list[dict] = []
    with mp.get_context("spawn").Pool(a.jobs) as pool:
        for i, part in enumerate(pool.imap_unordered(worker, batches), 1):
            rows += part
            if i % 5 == 0 or i == len(batches):
                print(f"  {len(rows)}/{len(todo)}  {time.time() - t0:.0f} s", flush=True)
    tally = {c: Counter() for c in CONTROLS}
    fails = defaultdict(list)
    errors = [r for r in rows if r.get("error")]
    for r in rows:
        for c, (state, detail) in (r.get("res") or {}).items():
            tally[c][state] += 1
            if state == "fail":
                fails[c].append((r["name"], r.get("mode"), detail))
    print(f"\n{len(rows)} lights in {time.time() - t0:.0f} s")
    print(f"{'control':10} {'ok':>6} {'fail':>6} {'skip':>6}")
    for c in CONTROLS:
        print(f"{c:10} {tally[c]['ok']:6} {tally[c]['fail']:6} {tally[c]['skip']:6}")
    print(f"not patchable / crashed: {len(errors)}")
    for c in CONTROLS:
        if fails[c]:
            print(f"\n{c}: {len(fails[c])} fail")
            for name, mode, d in fails[c][:12]:
                print(f"   {name} [{mode}]: {d}")
            reasons = Counter(d.split(":")[0][:50] for _n, _m, d in fails[c])
            print("   most common: " + "; ".join(f"{k} x{v}" for k, v in reasons.most_common(5)))
    if errors:
        print("\nerrors:")
        for r in errors[:15]:
            print(f"   {r['name']}: {r['error']}")
        reasons = Counter(r["error"].split(":")[0][:50] for r in errors)
        print("   most common: " + "; ".join(f"{k} x{v}" for k, v in reasons.most_common(5)))
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 1 if any(fails.values()) or errors else 0


if __name__ == "__main__":
    sys.exit(main())
