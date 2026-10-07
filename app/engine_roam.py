"""Roam: moving lights wander smoothly inside venue zones - the dance floor,
the DJ booth - each on its own path, aimed every frame from where it hangs
(the same solver as "aim at"), so a light on a truss, one on a pole and one
on the floor all keep their beam on the zone.  Lights share the zones out
(with two zones, half roam each).  Runs like any effect: the Speed master
and beat lock apply, stop it from Running.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import math

from app import motion as motion_mod
from app import venue as venue_mod
from app.engine_base import _fclamp


def _inside(pts, x, z) -> bool:
    hit = False
    for i in range(len(pts)):
        (x1, z1), (x2, z2) = pts[i], pts[i - 1]
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / ((z2 - z1) or 1e-9) + x1:
            hit = not hit
    return hit


def _pull_in(pts, cx, cz, x, z) -> tuple[float, float]:
    """A point off the zone, pulled back towards the middle until it is on it."""
    if _inside(pts, x, z):
        return x, z
    lo, hi = 0.0, 1.0
    for _ in range(8):
        mid = (lo + hi) / 2
        if _inside(pts, cx + (x - cx) * mid, cz + (z - cz) * mid):
            lo = mid
        else:
            hi = mid
    return cx + (x - cx) * lo, cz + (z - cz) * lo


class RoamMixin:
    # an irrational-ish set of rates, so no two lights repeat each other
    _ROAM_W = ((0.61, 0.37, 0.53, 0.29), (0.47, 0.71, 0.33, 0.59), (0.83, 0.41, 0.67, 0.23),
               (0.39, 0.57, 0.77, 0.31), (0.73, 0.27, 0.43, 0.63))

    def _roam_zones(self, zones) -> list[dict]:
        v = venue_mod.normalise(self.venue)
        all_z = v.get("zones") or []
        if not all_z:
            raise ValueError("draw a zone first (a dance floor, a DJ booth...) in the Venue tab")
        want = zones if isinstance(zones, list) else [zones] if zones else ["dancefloor"]
        out = []
        for w in want:
            w = str(w).lower().replace(" ", "")
            alias = {"dancefloor": "dancefloor", "floor": "dancefloor", "dj": "dj", "djbooth": "dj", "booth": "dj",
                     "stage": "dj", "bar": "bar", "audience": "standing", "crowd": "standing"}.get(w, w)
            hit = [z for z in all_z if z["id"].lower() == w or z["kind"] == alias
                   or str(z.get("name") or "").lower().replace(" ", "") == w]
            if not hit and alias in ("standing", "seating"):
                # "the crowd": where the people are - standing, seating or the dance floor
                hit = [z for z in all_z if z["kind"] in ("standing", "seating", "dancefloor")]
            for z in hit:
                if z not in out:
                    out.append(z)
        if not out:
            names = ", ".join(sorted({z.get("name") or z["kind"] for z in all_z}))
            raise ValueError(f"no zone like {', '.join(map(str, want))} - there is {names}")
        return [{"id": z["id"], "name": z.get("name") or z["kind"], "points": [list(p) for p in z["points"]],
                 "y": float(z.get("y") or 0)} for z in out]

    def _a_roam(self, zones=None, heads=None, group=None, speed=1.0, size=1.0, beats=None, **_):
        """Moving lights wander inside zones ("dancefloor", "dj", a zone's
        name or id; several share the lights out).  `speed` about metres a
        second, `size` how much of the zone each path covers (0.2-1)."""
        zs = self._roam_zones(zones)
        rows = self._fx_targets(heads, group)
        movers = [h for h in rows if "pan" in h["map"] and "tilt" in h["map"]]
        if not movers:
            raise ValueError("none of those lights can pan and tilt")
        p = {"speed": _fclamp(speed, 0.05, 8), "size": _fclamp(size, 0.2, 1.0)}
        if beats:
            p["beats"] = float(beats)
        nums = [h["head_no"] for h in movers]
        # a light roams one effect at a time: a new roam takes it over
        for f in self.fx:
            if f.get("roam") or f.get("lib") in ("circle", "pan_sweep", "tilt_bounce", "figure_eight", "fan_pan"):
                f["heads"] = [n for n in f["heads"] if n not in nums]
        self.fx = [f for f in self.fx if f["heads"]]
        self._fx_seq += 1
        import time
        self.fx.append({"id": self._fx_seq, "lib": "roam", "roam": zs, "params": p, "heads": nums,
                        "t0": time.monotonic(), "duration": None})
        where = " and ".join(z["name"] for z in zs)
        return {"id": self._fx_seq, "heads": nums,
                "summary": f"{len(nums)} light(s) roam the {where}"}

    def _roam_values(self, row: dict, t: float, out: dict) -> None:
        by_no = {h["head_no"]: h for h in self.patch}
        zs = row["roam"]
        p = row.get("params") or {}
        speed, size = float(p.get("speed", 1.0)), float(p.get("size", 1.0))
        cache = row.get("_zc")
        if not cache:
            cache = row["_zc"] = []
            for z in zs:
                xs, zz = [q[0] for q in z["points"]], [q[1] for q in z["points"]]
                cx, cz = sum(xs) / len(xs), sum(zz) / len(zz)
                cache.append((min(xs), max(xs), min(zz), max(zz), cx, cz,
                              max(1.0, math.hypot(max(xs) - min(xs), max(zz) - min(zz)))))
        near = row.setdefault("_near", {})
        for i, n in enumerate(row["heads"]):
            h = by_no.get(n)
            if not h:
                continue
            k = i % len(zs)
            x0, x1, z0, z1, cx, cz, diag = cache[k]
            w = self._ROAM_W[i % len(self._ROAM_W)]
            ph = i * 1.7
            # the path's clock: about `speed` m/s along the zone
            s = t * speed * 2.2 / diag
            u = 0.5 + 0.5 * size * (0.62 * math.sin(w[0] * s * 6.28 + ph) + 0.38 * math.sin(w[1] * s * 6.28 + 2 * ph))
            v = 0.5 + 0.5 * size * (0.62 * math.sin(w[2] * s * 6.28 + 3 * ph) + 0.38 * math.sin(w[3] * s * 6.28 + ph))
            x, z = x0 + u * (x1 - x0), z0 + v * (z1 - z0)
            x, z = _pull_in(zs[k]["points"], cx, cz, x, z)
            solved = self._aim_solve(h, x, zs[k]["y"], z, near=near.get(n), closest=True)
            if solved is None:
                continue
            fp, ft = solved[0], solved[1]
            if len(solved) >= 4:
                near[n] = (solved[2], solved[3])
            top_p = 65535 if "pan_fine" in h["map"] else 255
            top_t = 65535 if "tilt_fine" in h["map"] else 255
            row_out = out.setdefault(n, {})
            row_out["pan"] = int(round(max(0.0, min(1.0, fp)) * top_p))
            row_out["tilt"] = int(round(max(0.0, min(1.0, ft)) * top_t))
            # a multi-head light (Wave 360): its heads fan out along the throw
            # and breathe in and out, each on its own tilt
            per = self._aim_heads(h, x, zs[k]["y"], z, near=near.get(n),
                                  spread=0.4 + 0.5 * (1 + math.sin(s * 9.0 + ph)))
            row_out.update(per)

    def _zone_move(self, row: dict, kind: str, p: dict, h: dict, k, index: int, count: int) -> dict:
        """A movement shape (circle, sweep, bounce, figure 8, fan, a shape of
        your own) drawn ON a zone's floor instead of round the aim: the
        shape's path is a path across the dance floor, sized S / M / L = a
        quarter / half / all of it, and each light is aimed at its point
        from where it hangs - so it stays on the zone wherever the light is.
        A light that only tilts (a moving bar) or only pans follows the
        point as far as its one axis can.  {"pan": frac, "tilt": frac}."""
        zc = row.get("_zone")
        if not zc or zc[0] != p["zone"]:
            try:
                z = self._roam_zones([p["zone"]])[0]
            except ValueError:
                return {}                                 # the zone was deleted: hold
            xs, zz = [q[0] for q in z["points"]], [q[1] for q in z["points"]]
            zc = row["_zone"] = (p["zone"], z["points"], sum(xs) / len(xs), sum(zz) / len(zz),
                                 (max(xs) - min(xs)) / 2, (max(zz) - min(zz)) / 2, z["y"])
        _, pts, cx, cz, hw, hd, y = zc
        dp, dt = motion_mod.shape(kind, row["_turns"], p, index, count)
        lock = int(round(float(p.get("lock", 0.0))))
        dp = 0.0 if dp is None or lock == 2 else dp
        dt = 0.0 if dt is None or lock == 1 else dt
        s = max(0.1, min(1.0, float(p.get("size", 20.0)) / 40.0))
        x, z = _pull_in(pts, cx, cz, cx + dp * s * hw, cz + dt * s * hd)
        aim = h if "pan" in h["map"] and "tilt" in h["map"] else \
            {**h, "map": list(h["map"]) + [r for r in ("pan", "tilt") if r not in h["map"]]}
        near = row.setdefault("_near", {})
        key = (h["head_no"], k)
        solved = self._aim_solve(aim, x, y, z, near=near.get(key), closest=True)
        if solved is None:
            return {}
        near[key] = (solved[2], solved[3])
        return {"pan": max(0.0, min(1.0, solved[0])), "tilt": max(0.0, min(1.0, solved[1]))}
