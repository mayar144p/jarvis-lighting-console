"""The Move tab: spots, formations, nudge, a light's own range.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import os
import time

from app import motion as motion_mod
from app import venue as venue_mod
from app.engine_base import _fclamp, _truthy, attr_domain
from app.engine_support import logical16 as _logical16


class MoveMixin:
    # ------------------------------------------------------------------
    # shapes of your own: key points the heads go round (phasers)
    # ------------------------------------------------------------------
    MAX_SHAPES = 32

    def _shapes(self) -> list[dict]:
        lst = self.__dict__.get("shapes")
        if lst is None:
            lst = self.shapes = []
        return lst

    @staticmethod
    def _shape_clean(raw: dict, ident: str) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        name = str(raw.get("name") or "").strip()[:32] or "Shape"
        return {"id": ident, "name": name, "points": motion_mod.clean_points(raw.get("points")),
                "smooth": raw.get("smooth") is not False}

    def _a_shape_save(self, shape=None, id=None, **_):
        """Keep a movement shape: {name, points [[pan, tilt] -1..1, tilt up],
        smooth} - the heads go round the points, a curve through them or
        straight lines."""
        lst = self._shapes()
        ident = str(id or (shape or {}).get("id") or "")
        if not ident:
            if len(lst) >= self.MAX_SHAPES:
                raise ValueError(f"at most {self.MAX_SHAPES} shapes")
            ident = "s" + os.urandom(3).hex()
        clean = self._shape_clean(shape, ident)
        self.shapes = [s for s in lst if s["id"] != ident] + [clean]
        return {"id": ident, "shapes": self.shapes,
                "summary": f"shape {clean['name']}: {len(clean['points'])} points"}

    def _a_shape_delete(self, id=None, **_):
        if not any(s["id"] == str(id) for s in self._shapes()):
            raise ValueError(f"no shape {id!r}")
        self.shapes = [s for s in self._shapes() if s["id"] != str(id)]
        self.fx = [f for f in self.fx if not (f.get("lib") == "shape" and f["params"].get("shape") == str(id))]
        return {"shapes": self.shapes, "summary": "shape deleted"}

    def _shape_start(self, params: dict, nums: list[int]) -> int:
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")
        # one movement at a time on a head
        self.fx = [f for f in self.fx if not (f.get("lib") in motion_mod.ALL_KINDS
                                              and set(f.get("heads") or []) & set(nums) and not f.get("cue_pb"))]
        self._fx_seq += 1
        self.fx.append({"id": self._fx_seq, "lib": "shape", "params": dict(params), "heads": list(nums),
                        "t0": time.monotonic(), "duration": None})
        return self._fx_seq

    def _a_run_shape(self, id=None, heads=None, group=None, speed=0.25, size=30.0, spread=0.0,
                     direction=1, beats=None, space=None, **_):
        """Run a shape on the selected moving lights: `size` degrees round
        where each is aimed, `speed` rounds a second (or a round per
        `beats`), `spread` degrees round the shape between the lights."""
        shp = next((s for s in self._shapes() if s["id"] == str(id)), None)
        if shp is None:
            raise ValueError(f"no shape {id!r}")
        rows = self._fx_targets(heads, group)
        nums = [h["head_no"] for h in rows if "pan" in h["map"] or "tilt" in h["map"]]
        if not nums:
            raise ValueError("select moving lights first")
        p = {"shape": shp["id"], "speed": _fclamp(speed, 0.005, 2.0), "size": _fclamp(size, 1, 270),
             "spread": _fclamp(spread, 0, 720), "phase": 0.0, "arc": 360.0,
             "direction": -1.0 if float(direction or 1) < 0 else 1.0, "lock": 0.0}
        if beats not in (None, "", 0, "0", False):
            p["beats"] = self._clean_beats(beats)
        if space:
            p["space"] = self._clean_space(space)
        fid = self._shape_start(p, nums)
        return {"fx": fid, "heads": len(nums), "summary": f"{shp['name']} on {len(nums)} light(s)"}

    # ------------------------------------------------------------------
    # the Move tab: one-tap spots, formations, nudge, a light's own range
    # ------------------------------------------------------------------
    def _move_spots(self) -> list[dict]:
        """Named aim points from the venue: the dance floor (centre and its
        front / back / left / right, inset so beams land ON the floor), the
        DJ, the stage, the bar - whatever zones the room has - and marks."""
        v = self.venue if isinstance(self.venue, dict) else {}
        zones = v.get("zones") or []
        spots: list[dict] = []

        def add(key, label, x, z, y=0.0):
            spots.append({"key": key, "label": label, "x": round(float(x), 2),
                          "y": round(float(y), 2), "z": round(float(z), 2)})
        floor = next((z for z in zones if z.get("kind") == "dancefloor" and z.get("points")), None) \
            or next((z for z in zones if z.get("kind") == "standing" and z.get("points")), None)
        stage = next((z for z in zones if z.get("kind") == "stage" and z.get("points")), None)
        if floor:
            xs = [p[0] for p in floor["points"]]
            zs = [p[1] for p in floor["points"]]
            cx, cz = venue_mod.zone_centroid(floor)
            inset_x, inset_z = (max(xs) - min(xs)) * 0.3, (max(zs) - min(zs)) * 0.3
            # "front" is the side nearest the stage (else the lower z)
            front_z, back_z = min(zs) + inset_z, max(zs) - inset_z
            if stage and venue_mod.zone_centroid(stage)[1] > cz:
                front_z, back_z = back_z, front_z
            add("floor", "Dance floor", cx, cz)
            add("front", "Front", cx, front_z)
            add("back", "Back", cx, back_z)
            add("left", "Left", min(xs) + inset_x, cz)
            add("right", "Right", max(xs) - inset_x, cz)
        labels = {"dj": "DJ", "stage": "Stage", "bar": "Bar", "vip": "VIP", "foh": "FOH"}
        for z in zones:
            k = z.get("kind")
            if k in labels and z.get("points") and not any(s["key"] == k for s in spots):
                cx, cz = venue_mod.zone_centroid(z)
                add(k, labels[k], cx, cz, 1.2 if k in ("dj", "stage") else 0.0)
        for o in v.get("objects") or []:
            if o.get("kind") == "dj_booth" and not any(s["key"] == "dj" for s in spots):
                add("dj", "DJ", o.get("x", 0), o.get("z", 0), 1.2)
            if o.get("kind") == "mark" and o.get("name") and not any(
                    s["label"].lower() == str(o["name"]).lower() for s in spots):
                add("mark:" + str(o["name"]), str(o["name"]), o.get("x", 0), o.get("z", 0), 1.2)
        return spots

    def _a_aim_spot(self, spot=None, formation=None, heads=None, **_):
        """Point the selection at a named spot (see _move_spots), or lay a
        formation across the dance floor: fan (spread across it), cross
        (each side to the other side), split (left heads left, right heads
        right)."""
        spots = {s["key"]: s for s in self._move_spots()}
        rows = ([self._head(int(h)) for h in heads] if heads else self._require_selection())
        movers = sorted((h for h in rows if "pan" in h["map"] and "tilt" in h["map"]),
                        key=lambda h: float(h.get("x") or 0))
        if not movers:
            raise ValueError("none of the selected lights can pan and tilt")
        if formation:
            f = str(formation).lower()
            if not all(k in spots for k in ("floor", "left", "right")):
                raise ValueError("draw a dance floor zone in the venue first (Venue tab)")
            left, right, mid = spots["left"], spots["right"], spots["floor"]
            n = len(movers)
            for i, h in enumerate(movers):
                t = i / (n - 1) if n > 1 else 0.5
                if f == "fan":
                    x = left["x"] + (right["x"] - left["x"]) * t
                elif f == "cross":
                    x = right["x"] if t < 0.5 else left["x"]
                elif f == "split":
                    x = left["x"] if t < 0.5 else right["x"]
                else:
                    raise ValueError("formation is fan, cross or split")
                self._a_aim_at(x=x, y=0.0, z=mid["z"], heads=[h["head_no"]])
            return {"heads": [h["head_no"] for h in movers],
                    "summary": f"{f} across the dance floor ({n} lights)"}
        s = spots.get(str(spot or ""))
        if s is None:
            raise ValueError(f"no spot {spot!r} - the venue has: " + ", ".join(spots) if spots
                             else "the venue has no dance floor or zones yet (Venue tab)")
        r = self._a_aim_at(x=s["x"], y=s["y"], z=s["z"], heads=[h["head_no"] for h in movers])
        r["summary"] = f"{len(r['heads'])} light(s) on {s['label']}" + (
            f" ({len(r['skipped'])} can't move)" if r.get("skipped") else "") + (
            f" ({len(r['clamped'])} can't reach it: as close as they go)" if r.get("clamped") else "")
        return r

    def _floor_limits(self) -> dict:
        """{head_no: {"pan": (lo, hi), "tilt": (lo, hi)}} as fractions: the
        pan/tilt each mover needs to reach every corner and edge of the dance
        floor from where it hangs (solved without flipping between them), a
        little margin added.  Cached per patch / venue."""
        key = (self.patch_rev, json.dumps((self.venue or {}).get("zones") or [], sort_keys=True))
        cache = getattr(self, "_floor_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        out: dict = {}
        zones = (self.venue or {}).get("zones") or []
        floor = next((z for z in zones if z.get("kind") == "dancefloor" and z.get("points")), None)
        if floor:
            cx, cz = venue_mod.zone_centroid(floor)
            pts = [(cx + (x - cx) * 0.95, cz + (z - cz) * 0.95) for x, z in floor["points"]]
            ring = pts + [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(pts, pts[1:] + pts[:1])]
            for h in self.patch:
                if "pan" not in h["map"] or "tilt" not in h["map"] or h.get("x") is None:
                    continue
                mid = self._aim_solve(h, cx, 0.0, cz)
                if mid is None:
                    continue
                fps, fts = [mid[0]], [mid[1]]
                for x, z in ring:
                    s = self._aim_solve(h, x, 0.0, z, near=(mid[2], mid[3]))
                    if s is not None:
                        fps.append(s[0])
                        fts.append(s[1])
                m = 0.01
                out[h["head_no"]] = {"pan": (max(0.0, min(fps) - m), min(1.0, max(fps) + m)),
                                     "tilt": (max(0.0, min(fts) - m), min(1.0, max(fts) + m))}
        self._floor_cache = (key, out)
        return out

    def _eff_limits(self, h: dict, floor: bool) -> dict:
        """A head's pan/tilt range as fractions: its own range, narrowed to
        the dance floor when `floor` - {"pan": (lo, hi), "tilt": (lo, hi)}."""
        out = {}
        fl = self._floor_limits().get(h["head_no"]) if floor else None
        for role in ("pan", "tilt"):
            if role not in h["map"]:
                continue
            dom = attr_domain(h, role)
            lo, hi = (h.get("limits") or {}).get(role) or (None, None)
            lo = lo / dom if lo is not None else 0.0
            hi = hi / dom if hi is not None else 1.0
            if fl and role in fl:
                nlo, nhi = max(lo, fl[role][0]), min(hi, fl[role][1])
                if nlo <= nhi:               # both apply; if they don't overlap, the light's own wins
                    lo, hi = nlo, nhi
            out[role] = (lo, hi)
        return out

    def _frame_patch(self) -> list[dict]:
        """The patch as the frame builder sees it: with floor_lock on, each
        mover carries its floor range as its limits, so cues and aims can't
        leave the dance floor either."""
        if not self.floor_lock:
            return self.patch
        fl = self._floor_limits()
        key = (self.patch_rev, id(fl))
        cache = getattr(self, "_frame_patch_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        out = []
        for h in self.patch:
            if h["head_no"] not in fl:
                out.append(h)
                continue
            lim = dict(h.get("limits") or {})
            for role, (lo, hi) in self._eff_limits(h, True).items():
                dom = attr_domain(h, role)
                lim[role] = (int(lo * dom), int(round(hi * dom)))
            out.append(dict(h, limits=lim))
        self._frame_patch_cache = (key, out)
        return out

    def _a_floor_safe(self, movement=None, everything=None, **_):
        """Movement stays on the dance floor (movement=true, the default);
        everything=true also keeps cues, aims and the programmer on it."""
        if movement is not None:
            self.floor_safe = _truthy(movement)
        if everything is not None:
            self.floor_lock = _truthy(everything)
        n = len(self._floor_limits())
        return {"floor_safe": self.floor_safe, "floor_lock": self.floor_lock, "movers": n,
                "summary": ("no dance floor zone yet - draw one in the Venue tab" if not n else
                            f"movement {'stays on' if self.floor_safe else 'may leave'} the dance floor"
                            + ("; cues and aims too" if self.floor_lock else "") + f" ({n} movers)")}

    def _move_now(self, h: dict, role: str) -> int:
        """The head's current value for pan/tilt in its own domain."""
        v = (self.programmer.get(h["head_no"]) or {}).get(role)
        if v is None:
            for _lvl, vals in self._active_playbacks(time.monotonic()):
                if role in (vals.get(h["head_no"]) or {}):
                    v = vals[h["head_no"]][role]
                    break
        dom = attr_domain(h, role)
        if v is None:
            return dom // 2
        return int(_logical16(v)) if dom > 255 else int(v)

    def _a_nudge(self, axis="pan", step=0.01, **_):
        """Move the selection's pan or tilt by a fraction of its travel
        (+/-): the arrows on the Move tab, coarse 0.02 or fine 0.002."""
        role = "tilt" if str(axis).lower().startswith("t") else "pan"
        frac = max(-0.5, min(0.5, float(step)))
        done = []
        for h in self._require_selection():
            if role not in h["map"]:
                continue
            dom = attr_domain(h, role)
            v = max(0, min(dom, self._move_now(h, role) + round(frac * dom)))
            if dom > 255 and 0 < v < 256:
                v = 256
            self._set_programmer(h["head_no"], role, v)
            done.append(h["head_no"])
        if not done:
            raise ValueError(f"none of the selected lights has {role}")
        return {"heads": done, "summary": f"{role} {'+' if frac > 0 else ''}{round(frac * 100, 1)}%"}

    def _a_move_range(self, axis="tilt", edge="top", **_):
        """A light's own range: Set top / Set bottom (tilt) or Set left /
        Set right (pan) at where it points now; `clear` removes it.  Every
        cue, effect, spot and button then stays inside it, per light."""
        a = str(axis or "").lower()
        # a laser's safe zone: its beam height (Y) between two marked edges,
        # and its pattern no bigger than a marked size - so beams stay above
        # the audience's eyes whatever a cue or button asks for
        role = ("laser_size" if "size" in a else "laser_y" if a.startswith(("laser", "beam", "height", "y"))
                else "pan" if a.startswith("p") else "tilt")
        edge = str(edge or "").lower()
        rows = [h for h in self._require_selection() if role in h["map"]]
        if not rows:
            raise ValueError(f"none of the selected lights has {role.replace('_', ' ')}")
        # One edge alone is ambiguous - a higher tilt value is "up" on one
        # head and "down" on another, depending on how it hangs - so each
        # edge is marked, and the range applies once BOTH are: between them.
        # (A size is not: 0 is the smallest pattern, so "largest" is enough.)
        first = {"tilt": ("top", "bottom"), "pan": ("left", "right"),
                 "laser_y": ("low", "high"), "laser_size": ("max", "max")}[role]
        out, waiting = [], []
        for h in rows:
            cur = dict(h.get("limits") or {})
            marks = dict((h.get("range_marks") or {}).get(role) or {})
            if edge == "clear":
                cur.pop(role, None)
                marks = {}
            elif role == "laser_size":
                cur[role] = (0, self._move_now(h, role))
                marks = {}
            else:
                side = first[0] if edge in (first[0], "low", "min", "top", "left") else first[1]
                marks[side] = self._move_now(h, role)
                if len(marks) == 2:
                    lo, hi = sorted(marks.values())
                    cur[role] = (lo, hi)
                    marks = {}
                else:
                    waiting.append(h["head_no"])
            h["limits"] = cur
            rm = dict(h.get("range_marks") or {})
            if marks:
                rm[role] = marks
            else:
                rm.pop(role, None)
            h["range_marks"] = rm
            out.append({"head": h["head_no"], "range": cur.get(role), "marked": marks})
        self.patch_rev += 1
        other = first[1] if edge in (first[0], "low", "min", "top", "left") else first[0]
        name = {"laser_y": "beam height", "laser_size": "laser size"}.get(role, role)
        return {"ranges": out, "summary": (
            f"{name} range cleared" if edge == "clear" else
            f"{name} {edge} marked - now point it at the {other} and set that" if waiting else
            f"{name} range set on {len(out)} light(s)")}
