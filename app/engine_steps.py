"""Step effects in the desk (app/stepfx.py): made from your own looks
(programmer steps or palettes), saved with the show, run like any effect
- beat lock, a direction through the room, stop.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import copy
import time

from app import showdesign
from app import stepfx as stepfx_mod
from app import timeline as tl_mod
from app import venue as venue_mod


# "the back truss" in a room whose truss is called "Rear truss"
_RIG_WORDS = {"back": ("back", "rear", "upstage"), "rear": ("rear", "back", "upstage"),
              "upstage": ("upstage", "rear", "back"), "front": ("front", "downstage", "fob"),
              "downstage": ("downstage", "front"), "middle": ("mid", "middle", "centre", "center"),
              "mid": ("mid", "middle", "centre", "center"), "left": ("left", "sl", "stage left"),
              "right": ("right", "sr", "stage right")}


class StepsMixin:
    def _rig_named(self, name) -> dict | None:
        """A rig by id or by what people call it: exact name, then the name
        inside it, then the same with back = rear = upstage and so on."""
        want = str(name or "").strip().lower()
        for pre in ("the ", "on the ", "on "):
            if want.startswith(pre):
                want = want[len(pre):]
        if not want:
            return None
        rigs = venue_mod.normalise(self.venue)["rigging"]
        for r in rigs:
            if r["id"].lower() == want or (r["name"] or "").lower() == want:
                return r
        for r in rigs:
            if want in (r["name"] or "").lower():
                return r
        words = want.split()
        kind = next((w for w in words if w in venue_mod.RIG_KINDS), None)
        place = [w for w in words if w in _RIG_WORDS]
        best, score = None, None
        for r in rigs if place else []:
            low = (r["name"] or "").lower()
            if kind and kind not in low and r["kind"] != kind:
                continue
            ranks = []
            for w in place:
                hit = next((i for i, alt in enumerate(_RIG_WORDS[w]) if alt in low), None)
                if hit is None:
                    break
                ranks.append(hit)
            else:
                # the nearest word wins: "back" is the rear truss before the upstage one
                if score is None or sum(ranks) < score:
                    best, score = r, sum(ranks)
        return best

    def rig_heads(self, name) -> tuple[dict, list[int]]:
        """(rig, the lights on it) - a shape (circle, frame) counts whole."""
        r = self._rig_named(name)
        if r is None:
            raise ValueError(f"no rig called {name!r} in this room")
        ids = {r["id"]}
        if r.get("group"):
            ids |= {x["id"] for x in venue_mod.normalise(self.venue)["rigging"] if x.get("group") == r["group"]}
        nums = sorted(h["head_no"] for h in self.patch if (h.get("mount") or {}).get("rig") in ids
                      and self._head_class(h) == "light")
        if not nums:
            raise ValueError(f"no lights hang on {r['name'] or r['id']}")
        return r, nums

    def _a_chase_colours(self, colours=None, heads=None, rig=None, beats=1, section=None,
                         name=None, run=True, **_):
        """A chase between colours ("red and white"): every light takes the
        next colour each step, neighbours apart - saved as a step effect of
        the show.  `beats`: beats a step (on the beat = 1).  `rig`: the
        lights on that truss.  `section` ("drop"): a clip over that part of
        the timeline instead of running now."""
        names = dict(showdesign.COLOR_NAMES)
        raw = colours if isinstance(colours, (list, tuple)) else str(colours or "").replace(" and ", ",").split(",")
        hexes, labels = [], []
        for c in raw:
            c = str(c).strip().lower()
            if not c:
                continue
            hx = c if c.startswith("#") and len(c) in (4, 7) else names.get(c)
            if hx is None:
                raise ValueError(f"which colour is {c!r}?")
            hexes.append(hx)
            labels.append(c if not c.startswith("#") else showdesign._name_of(c).lower())
        if not 2 <= len(hexes) <= 8:
            raise ValueError("a chase needs 2 to 8 colours")
        if rig:
            where, nums = self.rig_heads(rig)
            where = where["name"] or where["id"]
        elif heads:
            nums, where = [int(n) for n in heads], None
        else:
            nums, where = [h["head_no"] for h in self._require_selection()], None
        by_no = {h["head_no"]: h for h in self.patch}
        rows = [by_no[n] for n in nums if n in by_no and self._colour_values(by_no[n], hexes[0])]
        if not rows:
            raise ValueError("none of those lights can change colour")
        step_beats = self._clean_beats(beats or 1)
        k = len(hexes)
        steps = []
        for st in range(k):
            vals = {}
            for i, h in enumerate(rows):
                row = dict(self._colour_values(h, hexes[(i + st) % k]))
                if "dimmer" in h["map"]:
                    row["dimmer"] = 255
                vals[str(h["head_no"])] = row
            steps.append({"values": vals, "time": 0.5, "fade": 0})
        label = str(name or (" / ".join(labels) + " chase").capitalize())[:40]
        saved = self._a_step_fx_save(fx={"name": label, "steps": steps, "curve": "snap", "spread": 0})
        ident = saved["id"]
        who = f"{len(rows)} light(s)" + (f" on {where}" if where else "")
        if section:
            span = tl_mod.section_span(self.timeline, section)
            if span is None:
                raise ValueError(f"no {section!r} on the timeline - put a marker called {section} where it starts")
            doc = copy.deepcopy(self.timeline)
            track = next((t for t in doc["tracks"] if t["kind"] == "fx" and t["name"] == "Copilot"), None)
            if track is None:
                track = {"kind": "fx", "name": "Copilot", "clips": []}
                doc["tracks"].append(track)
            track["clips"].append({"t": span[0], "dur": round(span[1] - span[0], 3), "step": ident,
                                   "beats": step_beats * k, "label": label,
                                   "target": {"heads": [h["head_no"] for h in rows]}})
            self._tl_set_doc(doc)
            return {"id": ident, "clip": [span[0], span[1]],
                    "summary": f"{label} on {who}, {span[0]:.0f}-{span[1]:.0f} s on the timeline"}
        if not run:
            return {"id": ident, "summary": f"{label} saved"}
        r = self._a_step_fx_run(id=ident, heads=[h["head_no"] for h in rows], beats=step_beats * k)
        return {"id": ident, "fx": r["fx"],
                "summary": f"{label} on {who}, {step_beats:g} beat(s) a step"}

    def _steps(self) -> list[dict]:
        lst = self.__dict__.get("step_fx")
        if lst is None:
            lst = self.step_fx = []
        return lst

    @staticmethod
    def _clean_step_list(raw) -> list[dict]:
        out = []
        for i, f in enumerate(raw or []):
            try:
                out.append(stepfx_mod.clean(f, str((f or {}).get("id") or f"x{i + 1}")))
            except (ValueError, TypeError, AttributeError):
                continue
        return out[:64]

    def _a_step_capture(self, heads=None, **_):
        """The programmer now, for the selected lights: one step to keep in
        the step editor (nothing is saved)."""
        nums = [int(n) for n in heads] if heads else [h["head_no"] for h in self._require_selection()]
        vals = {str(n): dict(self.programmer.get(n) or {}) for n in nums if self.programmer.get(n)}
        vals = {n: {r: v for r, v in row.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                for n, row in vals.items()}
        vals = {n: row for n, row in vals.items() if row}
        if not vals:
            raise ValueError("the programmer holds nothing for these lights: set a look first")
        return {"step": {"values": vals}, "summary": f"step: {len(vals)} light(s)"}

    def _a_step_fx_save(self, fx=None, id=None, **_):
        """Save a step effect (a new one without id)."""
        lst = self._steps()
        ident = str(id or (fx or {}).get("id") or "")
        if not ident:
            nums = [int(f["id"][1:]) for f in lst if f["id"][1:].isdigit()]
            ident = f"x{max(nums, default=0) + 1}"
            if len(lst) >= 64:
                raise ValueError("at most 64 step effects")
        clean = stepfx_mod.clean(fx or {}, ident)
        self.step_fx = [f for f in lst if f["id"] != ident] + [clean]
        return {"id": ident, "step_fx": self.step_fx,
                "summary": f"{clean['name']}: {len(clean['steps'])} steps, {stepfx_mod.cycle(clean):g} s a round"}

    def _a_step_fx_delete(self, id=None, **_):
        lst = self._steps()
        if not any(f["id"] == str(id) for f in lst):
            raise ValueError(f"no step effect {id!r}")
        self.step_fx = [f for f in lst if f["id"] != str(id)]
        self.fx = [r for r in self.fx if r.get("steps") != str(id)]
        return {"step_fx": self.step_fx, "summary": "step effect deleted"}

    def _a_step_fx_run(self, id=None, heads=None, speed=1.0, beats=None, space=None, **_):
        """Run a step effect on its own lights (or `heads`, or the selection
        for palette-only effects)."""
        fx = next((f for f in self._steps() if f["id"] == str(id)), None)
        if fx is None:
            raise ValueError(f"no step effect {id!r}")
        patched = {h["head_no"] for h in self.patch}
        nums = [int(n) for n in heads] if heads else stepfx_mod.heads_of(fx)
        if not nums:
            nums = [h["head_no"] for h in self._require_selection()]
        nums = [n for n in nums if n in patched]
        if not nums:
            raise ValueError("none of its lights are patched")
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")
        # one run of it at a time on these lights
        self.fx = [r for r in self.fx if r.get("steps") != fx["id"]]
        params = {"speed": max(0.05, min(8.0, float(speed or 1.0)))}
        if beats not in (None, "", 0, "0", False):
            params["beats"] = self._clean_beats(beats)
        if space:
            params["space"] = self._clean_space(space)
        self._fx_seq += 1
        self.fx.append({"id": self._fx_seq, "steps": fx["id"], "params": params, "heads": nums,
                        "t0": time.monotonic(), "duration": None})
        return {"fx": self._fx_seq, "summary": f"{fx['name']} on {len(nums)} light(s)"}

    def _step_values(self, row: dict, elapsed: float, out: dict) -> None:
        """One frame of a running step effect."""
        fx = next((f for f in self._steps() if f["id"] == row["steps"]), None)
        if fx is None:
            return
        by_no = {h["head_no"]: h for h in self.patch}
        heads = [n for n in row["heads"] if n in by_no]
        total = stepfx_mod.cycle(fx)
        # elapsed counts cycles at speed 1 = one round in `total` seconds;
        # locked to the beat, the round is `beats` beats
        beats = (row.get("params") or {}).get("beats")
        rounds = row.get("_beat_cycles") if beats else elapsed * float((row.get("params") or {}).get("speed", 1.0)) / total
        spatial = self._space_index(row, heads)
        n = max(1, len(heads))
        for i, head_no in enumerate(heads):
            where = spatial.get(head_no, i) if spatial else i
            lag = fx["spread"] / 360.0 * where / n
            vals = stepfx_mod.values(fx, head_no, by_no[head_no]["map"], self.palettes, ((rounds or 0) - lag) * total)
            if vals:
                out.setdefault(head_no, {}).update(vals)
