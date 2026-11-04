"""The room the lights know: zones taught by pointing, and zones they must
keep out of (or stay in).

Where a beam lands is the exact reverse of aiming at a spot (_aim_solve):
from where the light hangs, which way up, which way its base faces, and its
Line up.  So once a light is lined up, pointing it at the corners of the
dance floor tells the desk where the dance floor really is - no tape
measure - and pointing it at a wall and saying "Not here" keeps every beam
off it.

Keep-out zones (kind "keepout") hold for every light and every move -
cues, palettes, effects, roams, the programmer: at the frame merge a beam
that would land inside one is put on the nearest point just outside its
edge (re-aimed there), so it slides along the edge instead of crossing.
A light can also be told to stay IN zones (its `stay` list): its beam is
held inside them the same way.  The programmer keeps what was typed; only
what goes out is held, as with a light's limits.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import math

from app import fixture_kind
from app import venue as venue_mod
from app.engine_base import _truthy, attr_domain
from app.engine_roam import _inside

MARGIN = 0.25          # metres a held beam stays off a keep-out edge / inside a stay zone
WALL_DEPTH = 60.0      # a wall's keep-out reaches this far beyond it (a beam that would hit the wall lands out there)


def _nearest_on_edge(pts, x, z):
    """The nearest point on the polygon's outline to (x, z), and the edge's direction."""
    best = None
    for i in range(len(pts)):
        (x1, z1), (x2, z2) = pts[i - 1], pts[i]
        dx, dz = x2 - x1, z2 - z1
        L = dx * dx + dz * dz or 1e-9
        t = max(0.0, min(1.0, ((x - x1) * dx + (z - z1) * dz) / L))
        px, pz = x1 + t * dx, z1 + t * dz
        d = (px - x) ** 2 + (pz - z) ** 2
        if best is None or d < best[0]:
            best = (d, px, pz)
    return best[1], best[2]


def _step_off(pts, x, z, outward: bool, margin: float = MARGIN):
    """(x, z) moved to just outside (outward) or just inside the polygon."""
    px, pz = _nearest_on_edge(pts, x, z)
    cx = sum(p[0] for p in pts) / len(pts)
    cz = sum(p[1] for p in pts) / len(pts)
    # the way out: from the point to the edge (or from the middle, when the
    # point is right on the edge)
    vx, vz = (px - x, pz - z) if outward else (x - px, z - pz)
    if abs(vx) + abs(vz) < 1e-6:
        vx, vz = (px - cx, pz - cz) if outward else (cx - px, cz - pz)
    n = math.hypot(vx, vz) or 1.0
    if not outward:
        vx, vz = -vx, -vz
    for k in (1, 2, 4, 8):
        qx, qz = px + vx / n * margin * k, pz + vz / n * margin * k
        if _inside(pts, qx, qz) != outward:
            return qx, qz
    return px + vx / n * margin, pz + vz / n * margin


def _hull(points):
    """The convex outline round some points (a keep-out grown by pointing)."""
    pts = sorted(set((round(a, 3), round(b, 3)) for a, b in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


class ZoneMixin:
    # ------------------------------------------------------------------
    # where a beam lands: the reverse of _aim_solve
    # ------------------------------------------------------------------
    def _beam_ray(self, h: dict, pdeg: float, tdeg: float):
        """(origin, direction) of head h's beam at these pan / tilt degrees
        (the programmer's logical values, as _aim_solve gives them)."""
        hang = venue_mod.hang_of(h)
        d = fixture_kind.describe(h)
        pivot = 0.372 if d.get("heads") else self._AIM_PIVOT.get(d["type"], 0.4)
        ahead = 0.14 if d["type"] == "scanner" else 0.0
        yaw = math.radians(self._head_yaw(h))
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        bx, by, bz = venue_mod.hang_turn(hang, (0.0, pivot, ahead))
        ox, oy, oz = h["x"] + bx * cy_ + bz * sy_, h["y"] + by, h["z"] - bx * sy_ + bz * cy_
        off = h.get("align") or {}
        p = math.radians(pdeg + float(off.get("pan") or 0.0))
        t = math.radians(tdeg + float(off.get("tilt") or 0.0))
        if d["type"] == "scanner":
            lx, ly, lz = math.cos(t) * math.sin(p), -math.sin(t), math.cos(t) * math.cos(p)
        else:
            lx, ly, lz = math.sin(t) * math.sin(p), math.cos(t), math.sin(t) * math.cos(p)
        dx, dy, dz = venue_mod.hang_turn(hang, (lx, ly, lz))
        wx, wz = dx * cy_ + dz * sy_, -dx * sy_ + dz * cy_
        return (ox, oy, oz), (wx, dy, wz)

    def _beam_hit(self, h: dict, pdeg: float, tdeg: float, y: float = 0.0):
        """Where the beam lands on the floor (height y): (x, z), or None if
        it points level or up (it never reaches the floor)."""
        (ox, oy, oz), (wx, wy, wz) = self._beam_ray(h, pdeg, tdeg)
        if wy > -1e-4:
            return None
        k = (y - oy) / wy
        if k <= 0:
            return None
        return ox + wx * k, oz + wz * k

    def _head_deg(self, h: dict, vals: dict | None = None):
        """(pan deg, tilt deg) of a head: from `vals` (domain values), or
        what the programmer / output has now."""
        pmin, pmax, tmin, tmax = self._aim_span(h)
        if vals is None:
            fp = self._move_now(h, "pan") / max(1, attr_domain(h, "pan"))
            ft = self._move_now(h, "tilt") / max(1, attr_domain(h, "tilt"))
        else:
            fp = float(vals["pan"]) / max(1, attr_domain(h, "pan"))
            ft = float(vals["tilt"]) / max(1, attr_domain(h, "tilt"))
        return pmin + fp * (pmax - pmin), tmin + ft * (tmax - tmin)

    def _a_beam_spot(self, head=None, **_):
        """Where the light's beam lands on the floor now: x across, z from
        the back wall (metres), and whether that is inside the room drawn."""
        rows = [self._head(int(head))] if head not in (None, "") else self._require_selection()[:1]
        h = rows[0] if rows else None
        if not h or "pan" not in h["map"] or "tilt" not in h["map"]:
            raise ValueError("select a moving light")
        p, t = self._head_deg(h)
        hit = self._beam_hit(h, p, t)
        if hit is None:
            raise ValueError("the beam points level or up - aim it down at the floor")
        b = venue_mod.bounds(venue_mod.normalise(self.venue))
        inside = b["x0"] - 0.05 <= hit[0] <= b["x1"] + 0.05 and b["z0"] - 0.05 <= hit[1] <= b["z1"] + 0.05
        return {"head": h["head_no"], "x": round(hit[0], 2), "z": round(hit[1], 2), "in_room": inside,
                "lined_up": bool(h.get("align")),
                "summary": f"the beam lands at {hit[0]:.1f}, {hit[1]:.1f} m" + ("" if inside else " - outside the room drawn")}

    # ------------------------------------------------------------------
    # teaching the room by pointing
    # ------------------------------------------------------------------
    def _a_room_fit(self, points=None, **_):
        """Grow the room so these floor points (taught by pointing) are in it."""
        pts = [(float(p[0]), float(p[1])) for p in points or [] if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not pts:
            raise ValueError("no points to fit")
        v = venue_mod.normalise(self.venue)
        b = venue_mod.bounds(v)
        x0 = min([b["x0"]] + [p[0] - 0.5 for p in pts])
        x1 = max([b["x1"]] + [p[0] + 0.5 for p in pts])
        z0 = min([b["z0"]] + [p[1] - 0.5 for p in pts])
        z1 = max([b["z1"]] + [p[1] + 0.5 for p in pts])
        room = dict(v["room"])
        room.update({"width": round(x1 - x0, 2), "depth": round(z1 - z0, 2), "back": round(z0, 2), "cx": round((x0 + x1) / 2, 2)})
        v["room"] = room
        self._set_venue_doc(v)
        return self._venue_result(f"the room is now {room['width']} x {room['depth']} m")

    def _a_keep_out_here(self, head=None, name=None, size=1.5, **_):
        """Not here: where the light's beam lands becomes a keep-out zone (1.5 m
        round it; pointed again near one, that one grows to take it in).  A
        beam that lands outside the room is on a wall: that whole wall is
        kept out."""
        spot = self._a_beam_spot(head=head)
        x, z = spot["x"], spot["z"]
        v = venue_mod.normalise(self.venue)
        b = venue_mod.bounds(v)
        if not spot["in_room"]:
            # which wall: the side the beam went past
            gaps = {"left": b["x0"] - x, "right": x - b["x1"], "back": b["z0"] - z, "front": z - b["z1"]}
            side = max(gaps, key=gaps.get)
            W = WALL_DEPTH
            box = {"left": (b["x0"] - W, b["z0"] - W, b["x0"], b["z1"] + W),
                   "right": (b["x1"], b["z0"] - W, b["x1"] + W, b["z1"] + W),
                   "back": (b["x0"] - W, b["z0"] - W, b["x1"] + W, b["z0"]),
                   "front": (b["x0"] - W, b["z1"], b["x1"] + W, b["z1"] + W)}[side]
            label = name or {"left": "Left wall", "right": "Right wall", "back": "Back wall", "front": "Front wall"}[side]
            if any(zn.get("kind") == "keepout" and zn.get("name") == label for zn in v.get("zones") or []):
                return {"summary": f"{label} is already kept out"}
            pts = [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]
            v, made = venue_mod.add_item(v, {"kind": "keepout", "name": label, "points": pts, "wall": side})
            self._set_venue_doc(v)
            return self._venue_result(f"{label}: no beams on it now", id=made["id"], wall=side)
        r = max(0.3, float(size or 1.5)) / 2
        square = [[x - r, z - r], [x + r, z - r], [x + r, z + r], [x - r, z + r]]
        # pointed again near a keep-out made by pointing: it grows
        for zn in v.get("zones") or []:
            if zn.get("kind") != "keepout" or zn.get("wall") or not zn.get("pointed"):
                continue
            cx, cz = venue_mod.zone_centroid(zn)
            near = _inside(zn["points"], x, z) or min(math.hypot(px - x, pz - z) for px, pz in zn["points"]) < 2.5
            if near:
                pts = _hull([tuple(p) for p in zn["points"]] + [tuple(p) for p in square])
                v, _item = venue_mod.update_item(v, zn["id"], {"points": [list(p) for p in pts]})
                self._set_venue_doc(v)
                return self._venue_result(f"{zn.get('name') or 'keep-out'} grown to take that spot in", id=zn["id"])
        n = sum(1 for zn in v.get("zones") or [] if zn.get("kind") == "keepout") + 1
        v, made = venue_mod.add_item(v, {"kind": "keepout", "name": name or f"Keep out {n}", "points": square, "pointed": True})
        self._set_venue_doc(v)
        return self._venue_result(f"{made.get('name')}: no beams there now", id=made["id"])

    def _a_stay_in(self, heads=None, zones=None, clear=False, **_):
        """These lights' beams stay inside these zones, whatever they play
        (cues, palettes, effects, the programmer).  clear=true lets them go."""
        rows = self._cmd_rows(heads) if heads else self._require_selection()
        rows = [h for h in rows if "pan" in h["map"] and "tilt" in h["map"]]
        if not rows:
            raise ValueError("select moving lights")
        if _truthy(clear):
            for h in rows:
                h.pop("stay", None)
            self.patch_rev += 1
            return {"heads": [h["head_no"] for h in rows], "summary": f"{len(rows)} light(s) free to go anywhere (but the keep-out zones)"}
        zs = self._roam_zones(zones)
        for h in rows:
            h["stay"] = [z["id"] for z in zs]
        self.patch_rev += 1
        where = " and ".join(z["name"] for z in zs)
        return {"heads": [h["head_no"] for h in rows], "zones": [z["id"] for z in zs],
                "summary": f"{len(rows)} light(s) stay in the {where}"}

    # ------------------------------------------------------------------
    # holding the beams: called by the frame merge (merge.resolve_head)
    # ------------------------------------------------------------------
    def _confiners(self) -> dict:
        """{head_no: fn(values)} for the movers a keep-out or stay zone holds."""
        zones = (self.venue or {}).get("zones") or []
        key = (self.patch_rev, json.dumps(zones, sort_keys=True, default=str),
               json.dumps([[h["head_no"], h.get("x"), h.get("y"), h.get("z"), h.get("align"), h.get("stay"), h.get("hang"),
                            h.get("rot"), h.get("stance")] for h in self.patch], default=str))
        cache = self.__dict__.get("_confine_cache")
        if cache and cache[0] == key:
            return cache[1]
        keep = [z for z in zones if z.get("kind") == "keepout" and len(z.get("points") or []) >= 3]
        by_id = {z["id"]: z for z in zones if len(z.get("points") or []) >= 3}
        out = {}
        for h in self.patch:
            if "pan" not in h["map"] or "tilt" not in h["map"] or h.get("x") is None:
                continue
            stay = [by_id[i] for i in h.get("stay") or [] if i in by_id]
            if not keep and not stay:
                continue
            out[h["head_no"]] = self._make_confiner(h, keep, stay)
        self._confine_cache = (key, out)
        return out

    def _make_confiner(self, h: dict, keep: list, stay: list):
        dp, dt = max(1, attr_domain(h, "pan")), max(1, attr_domain(h, "tilt"))
        last = {}

        def hold(vals: dict) -> None:
            if "pan" not in vals or "tilt" not in vals:
                return
            got = (vals["pan"], vals["tilt"])
            if last.get("in") == got:                  # the same as last frame: same answer
                if last.get("out"):
                    vals["pan"], vals["tilt"] = last["out"]
                return
            last["in"], last["out"] = got, None
            p, t = self._head_deg(h, vals)
            y = float(stay[0].get("y") or 0) if stay else 0.0
            hit0 = self._beam_hit(h, p, t, y)
            if hit0 is None and not stay:
                return                             # level or up: no floor zone to keep it off

            def ok(x, z):
                return (not stay or any(_inside(s["points"], x, z) for s in stay)) \
                    and not any(_inside(k["points"], x, z) for k in keep)
            if hit0 is not None and ok(*hit0):
                return
            # a coarse light (8-bit pan: 2 degrees a step) lands a little off
            # the spot it is sent to: step further in until where its sent
            # value really lands is allowed
            for margin in (MARGIN, MARGIN * 2, MARGIN * 4, MARGIN * 8):
                # pointing up while it must stay on a zone: its middle
                x, z = hit0 if hit0 is not None else venue_mod.zone_centroid(stay[0])
                if stay and not any(_inside(s["points"], x, z) for s in stay):
                    best = min(stay, key=lambda s: math.dist(_nearest_on_edge(s["points"], x, z), (x, z)))
                    x, z = _step_off(best["points"], x, z, outward=False, margin=margin)
                for _round in range(3):
                    inside = [k for k in keep if _inside(k["points"], x, z)]
                    if not inside:
                        break
                    x, z = _step_off(inside[0]["points"], x, z, outward=True, margin=margin)
                s = self._aim_solve(h, x, y, z, near=(p, t), closest=True)
                if s is None:
                    return
                out = (int(round(max(0.0, min(1.0, s[0])) * dp)), int(round(max(0.0, min(1.0, s[1])) * dt)))
                got = self._beam_hit(h, *self._head_deg(h, {"pan": out[0], "tilt": out[1]}), y)
                if got is not None and ok(*got):
                    break
            last["out"] = out
            vals["pan"], vals["tilt"] = out
        return hold
