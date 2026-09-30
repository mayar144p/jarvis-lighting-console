"""Step effects in the desk (app/stepfx.py): made from your own looks
(programmer steps or palettes), saved with the show, run like any effect
- beat lock, a direction through the room, stop.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import time

from app import stepfx as stepfx_mod


class StepsMixin:
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
