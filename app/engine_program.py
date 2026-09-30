"""The programmer: attributes, effects, palettes and presets.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import time

from app import fixture_kind, merge
from app import fx as fxmod
from app import fxlib as fxlib_mod
from app import motion as motion_mod
from app.engine_base import (
    PALETTE_KINDS,
    PRESET_ROLES,
    _attr_role,
    _clamp,
    _is_int,
    _truthy,
    attr_domain,
)
from app.engine_support import BEAM_ROLES, COLOUR_ROLES, HTP_ROLES, ROLES
from app.engine_support import logical16 as _logical16
from app.merge import FX_OUTPUT_ROLES


class ProgrammerMixin:
    # the "In the programmer" bar's groups (anything else is "other")
    ATTR_GROUPS = {
        "intensity": HTP_ROLES | {"shutter", "strobe"},
        "colour": COLOUR_ROLES | {"wheel", "macro"},
        "position": frozenset({"pan", "tilt", "speed"}),
        "beam": BEAM_ROLES - {"shutter", "strobe"},
    }

    @classmethod
    def attr_group(cls, role: str) -> str:
        base = role.split("@", 1)[0]                  # one head of a multi-head light
        base = base[:-5] if base.endswith("_fine") else base
        return next((g for g, rs in cls.ATTR_GROUPS.items() if base in rs), "other")

    def _a_clear_attrs(self, group="colour", heads=None, **_):
        """Drop ONE kind of value from the programmer - just the colour, just
        the position... - keeping the rest (the bar's x per group).  Clearing
        position also stops the movement effects."""
        g = str(group or "").lower()
        if g not in (*self.ATTR_GROUPS, "other"):
            raise ValueError(f"not an attribute group: {group}")
        wanted = {int(x) for x in heads} if heads else set(self.programmer)
        n = 0
        for no in list(self.programmer):
            if no not in wanted:
                continue
            row = self.programmer[no]
            for role in [r for r in row if self.attr_group(r) == g]:
                row.pop(role)
                n += 1
            if not row:
                self.programmer.pop(no)
        fx_n = 0
        if g == "position":
            keep = [f for f in self.fx if f.get("lib") not in motion_mod.KINDS]
            fx_n = len(self.fx) - len(keep)
            self.fx = keep
        return {"cleared": n, "fx": fx_n, "summary": f"{g} cleared ({n} values)"}

    def _a_clear_programmer(self, **_):
        self._prog_fade = None
        n = sum(len(v) for v in self.programmer.values())
        self.programmer.clear()
        fx_n = len(self.fx)
        self.fx = []                    # CLEAR drops the effects too
        return {"cleared": n, "fx": fx_n, "summary": "programmer cleared"}

    # --- effects ---------------------------------------------------------
    def _a_fx_available(self, heads=None, group=None, **_):
        """What the named effects these fixtures can actually do.

        The UI asks this instead of filtering its own list, because a
        second filter is a second source of truth - and that is how "it
        offered me Circle and then nothing happened" happens.  This is the
        same pure function the engine uses to decide whether an effect may
        start, so the picker and the engine cannot disagree.

        A MIXED selection is reported per head and per effect: what the
        selection as a whole can do is the union, and `heads` says who.
        Offering the union while the engine quietly skips the incapable
        heads would be the same lie in a different place.
        """
        rows = self._fx_targets(heads, group)
        if not rows:
            raise ValueError("nothing selected")
        per_head = {}
        for h in rows:
            avail = fxlib_mod.available(h.get("map") or [])
            per_head[str(h["head_no"])] = avail
        union: list[str] = []
        for avail in per_head.values():
            for n in avail:
                if n not in union:
                    union.append(n)
        return {
            "heads": [h["head_no"] for h in rows],
            "available": fxlib_mod.describe(
                # the union of roles, so describe() agrees with available()
                sorted({r for h in rows for r in (h.get("map") or [])})),
            "names": sorted(union),
            "per_head": per_head,
            "summary": "%d effect(s) available on %d head(s)"
                       % (len(union), len(rows)),
        }

    def _fx_targets(self, heads=None, group=None) -> list[dict]:
        """The head rows an fx action should act on.

        Shared by run_fx and fx_available so the two can never disagree
        about what "the selection" means - the first version had the
        selection logic inline in run_fx, which meant the picker's idea of
        the selection and the engine's were two functions.
        """
        patched = {h["head_no"]: h for h in self.patch}
        if heads is not None:
            wanted: list[int] = []
            for h in heads:
                n = int(h)
                if n not in patched:
                    raise ValueError(f"head {n} is not patched")
                if n not in wanted:
                    wanted.append(n)
        elif group is not None:
            num_g = int(group)
            row = next((g for g in self.groups if g["n"] == num_g), None)
            if row is None:
                raise ValueError(f"no group {num_g}")
            wanted = [n for n in row["heads"] if n in patched]
        else:
            wanted = [h["head_no"] for h in self._require_selection()]
        return [patched[n] for n in wanted]

    def _a_run_fx(self, attribute=None, wave=None, kind=None, speed=1.0,
                  spread=0.0, phase=0.0, base=None, depth=None,
                  duration=None, heads=None, group=None, name=None,
                  params=None, **_):
        """Start a running effect on the selection (or explicit heads/group).

        TWO KINDS, ONE ACTION.

        With `name=`, one of the library effects in `app/fxlib.py` - Rainbow,
        Circle, Fan, Gobo spin - and it writes SEVERAL roles per head, so
        it needs no `attribute` and the old single-role arguments are
        ignored.  Without it, the original LFO: one wave on one attribute,
        which is the right primitive and is what the library is built on.

        A named effect is offered only to a fixture that can do it, and on a
        MIXED selection the incapable heads are SKIPPED and named in the
        result, rather than the whole thing being refused: selecting four
        PARs and two movers and asking for Circle should run Circle on the
        movers, not do nothing because two of the six are PARs.  Refusing
        outright is the other defensible choice and it is the wrong one -
        it makes a mixed selection, which is the normal case on a rig, a
        thing you cannot run an effect on.
        """
        if name not in (None, ""):
            return self._a_run_fx_named(
                str(name).lower(), params, duration, heads, group,
                across=_truthy(_.get("across")))

        def num(value, default=0.0) -> float:
            if value in (None, ""):
                return float(default)
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError(f"not a number: {value!r}") from None

        role = _attr_role(attribute)
        if role is None or role not in ROLES or role == "unused":
            raise ValueError(
                "run_fx needs an attribute (dimmer, pan, tilt, red, ...)")
        if role in FX_OUTPUT_ROLES:
            raise ValueError("an effect's output never runs from an effect generator")
        name = str(wave if wave not in (None, "") else kind or "sine").lower()
        if name not in fxmod.WAVES:
            raise ValueError(f"unknown wave {name!r} - use one of "
                             f"{', '.join(fxmod.WAVES)}")
        hz = max(fxmod.SPEED_MIN, min(fxmod.SPEED_MAX, num(speed, 1.0)))
        spread_deg = max(-fxmod.SPREAD_MAX,
                         min(fxmod.SPREAD_MAX, num(spread, 0.0)))

        if heads is not None:
            patched = {h["head_no"] for h in self.patch}
            wanted: list[int] = []
            for h in heads:
                n = int(h)
                if n not in patched:
                    raise ValueError(f"head {n} is not patched")
                if n not in wanted:
                    wanted.append(n)
        elif group is not None:
            num_g = int(group)
            row = next((g for g in self.groups if g["n"] == num_g), None)
            if row is None:
                raise ValueError(f"no group {num_g}")
            patched = {h["head_no"] for h in self.patch}
            wanted = [n for n in row["heads"] if n in patched]
        else:
            wanted = [h["head_no"] for h in self._require_selection()]
        if not wanted:
            raise ValueError("nothing selected")
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")

        low, high = (0, 100) if role in HTP_ROLES else (0, 255)
        base_v = low if base in (None, "") else _clamp(base, low, high)
        depth_v = (high - low if depth in (None, "")
                   else _clamp(depth, 0, high - low))
        dur = None
        if duration not in (None, "", 0, "0"):
            d = num(duration, 0.0)
            dur = min(d, 86400.0) if d > 0 else None

        self._fx_seq += 1
        row = {"id": self._fx_seq, "role": role, "kind": name,
               "speed": hz, "spread": spread_deg,
               "phase": num(phase, 0.0) / 360.0,    # degrees -> cycles
               "base": base_v, "depth": depth_v, "low": low, "high": high,
               "heads": list(wanted), "t0": time.monotonic(),
               "duration": dur}
        self.fx.append(row)
        return {"fx": row["id"], "heads": len(wanted),
                "summary": f"{name} {role} fx on {len(wanted)} head(s)"}

    def _a_run_fx_named(self, name: str, params, duration, heads,
                         group, across: bool = False) -> dict:
        """Start one of the `app/fxlib.py` effects.

        The capability decision is made HERE, with the same pure function
        the picker uses, and the heads that cannot do it are dropped and
        reported.  That is the whole contract: an effect runs only where it
        can, and the operator is told where it did not.
        """
        if name not in fxlib_mod.FX:
            raise ValueError(
                "unknown effect %r - one of %s"
                % (name, ", ".join(sorted(fxlib_mod.FX))))
        rows = self._fx_targets(heads, group)
        if not rows:
            raise ValueError("nothing selected")
        capable, skipped = [], []
        for h in rows:
            avail = fxlib_mod.available(h.get("map") or [])
            (capable if name in avail else skipped).append(h)
        if not capable:
            # Nothing here can do it.  The reason is per head, because the
            # usual cause is a mode with no dimmer or no colour, and "needs
            # pan" is useless if the fixture has no pan channel to lack.
            why = "; ".join(
                "head %d: %s" % (h["head_no"],
                                 fxlib_mod.why_not(h.get("map") or [], name))
                for h in skipped[:4])
            raise ValueError(
                "%s cannot run here - %s" % (
                    fxlib_mod.FX[name]["label"],
                    why + ("" if len(skipped) <= 4 else " (+%d more)"
                           % (len(skipped) - 4))))
        if len(self.fx) >= 16:
            raise ValueError("too many effects running - stop some first")
        p = fxlib_mod.defaults(name)
        if isinstance(params, dict):
            for k, v in params.items():
                if k in p and v is not None:
                    try:
                        p[k] = float(v)
                    except (TypeError, ValueError):
                        raise ValueError(
                            "not a number for %s: %r" % (k, v)) from None
        dur = None
        if duration not in (None, "", 0, "0"):
            try:
                d = float(duration)
            except (TypeError, ValueError):
                raise ValueError("not a number: %r" % (duration,)) from None
            dur = min(d, 86400.0) if d > 0 else None

        beats = params.get("beats") if isinstance(params, dict) else None
        if beats not in (None, "", 0, "0", False):
            p["beats"] = self._clean_beats(beats)
        self._fx_seq += 1
        row = {"id": self._fx_seq, "lib": name, "params": p,
               "heads": [h["head_no"] for h in capable],
               "t0": time.monotonic(), "duration": dur}
        if across and any(merge._repeated(h["map"]) for h in capable):
            # run it ACROSS each light's own heads (a Wave 360's four cells
            # and tilts) as if every head were a light of its own; with no
            # phase given, each head is a step on from the last
            row["across"] = True
            if "phase" in p and not (isinstance(params, dict) and params.get("phase")):
                p["phase"] = 100.0
            if name in motion_mod.KINDS and not (isinstance(params, dict) and params.get("spread")):
                p["spread"] = 360.0                       # the heads spread evenly round the shape
        self.fx.append(row)
        msg = "%s on %d head(s)" % (fxlib_mod.FX[name]["label"],
                                   len(capable))
        if skipped:
            msg += " (skipped %d: %s)" % (
                len(skipped), ", ".join(str(h["head_no"]) for h in skipped[:6]))
        return {"fx": row["id"], "effect": name, "heads": len(capable),
                "skipped": [h["head_no"] for h in skipped],
                "summary": msg}

    def _a_stop_fx(self, id=None, fx=None, **_):
        target = id if id is not None else fx
        if target is None:
            n = len(self.fx)
            self.fx = []
            return {"stopped": n, "summary": f"stopped {n} effect(s)"}
        try:
            tid = int(target)
        except (TypeError, ValueError):
            raise ValueError(f"bad effect id: {target!r}") from None
        before = len(self.fx)
        self.fx = [r for r in self.fx if r["id"] != tid]
        if len(self.fx) == before:
            raise ValueError(f"no effect {tid} running")
        return {"stopped": 1, "fx": tid, "summary": f"stopped effect {tid}"}

    # --- movement effects ------------------------------------------------
    def _move_values(self, row: dict, step: float, base, out: dict) -> None:
        """One movement effect (app/motion.py) for this frame: a shape of a
        given size in degrees around where each head is aimed, fitted into
        its limits, never faster than the slowest head's motor."""
        by_no = {h["head_no"]: h for h in self.patch}
        heads = [by_no[n] for n in row["heads"] if n in by_no]
        if not heads:
            return
        p = row["params"]
        kind = row["lib"]
        cap = row.get("_cap")
        if not cap or cap[0] != (self.patch_rev, tuple(sorted(p.items()))):
            spans = [self._move_span(h) for h in heads]
            amps = [(kind, *motion_mod.amplitude(kind, p, s)) for s in spans]
            travel = [self._move_travel(h) for h in heads]
            cap = row["_cap"] = ((self.patch_rev, tuple(sorted(p.items()))),
                                 motion_mod.max_rate(amps, travel), spans)
        rate = min(float(p.get("speed", 0.125)), cap[1])
        beats = p.get("beats")
        if beats and self._tempo().bpm / 60.0 / float(beats) <= cap[1]:
            row["_turns"] = row.get("_beat_cycles", 0.0)   # locked to the beat
        else:
            # free, or the beat asks more than the slowest motor can do
            row["_turns"] = row.get("_turns", 0.0) + step * rate
        prog, pbs = base
        # "across": every head of a multi-head light (a Wave 360's four
        # tilts) is one step of the movement - a tilt wave through the light
        units = []
        for i, h in enumerate(heads):
            copies = merge._repeated(h["map"]).get("tilt", 0) if row.get("across") else 0
            units += [(i, h, k) for k in range(1, copies + 1)] if copies else [(i, h, None)]
        for u, (i, h, k) in enumerate(units):
            n = h["head_no"]
            centre, limits = [], []
            for role in ("pan", "tilt"):
                dom = attr_domain(h, role) if role in h["map"] else 255
                v = (prog.get(n) or {}).get(f"{role}@{k}") if k and role == "tilt" else None
                if v is None:
                    v = (prog.get(n) or {}).get(role)
                if v is None:
                    for _lvl, vals in pbs:
                        if role in (vals.get(n) or {}):
                            v = vals[n][role]
                            break
                if v is None:
                    frac = 0.5
                else:
                    frac = (_logical16(v) / 65535.0) if dom > 255 else (float(v) / 255.0)
                centre.append(frac)
            eff = self._eff_limits(h, self.floor_safe)
            for role in ("pan", "tilt"):
                limits.append(eff.get(role, (0.0, 1.0)))
            vals = motion_mod.position(kind, row["_turns"], p, tuple(centre), tuple(limits),
                                       cap[2][i], u, len(units))
            dst = out.setdefault(n, {})
            for role, frac in vals.items():
                if role not in h["map"]:
                    continue
                if k is not None:
                    if role == "tilt":
                        dst[f"tilt@{k}"] = int(round(frac * 255))
                    elif k == 1:                        # the light's one pan, once
                        dst[role] = int(round(frac * 255)) if attr_domain(h, role) <= 255 else int(round(frac * 65535))
                    continue
                if attr_domain(h, role) > 255:
                    v16 = int(round(frac * 65535))
                    dst[role] = 256 if 0 < v16 < 256 else v16    # never read as 8-bit
                else:
                    dst[role] = int(round(frac * 255))

    def _move_span(self, h: dict) -> tuple[float, float]:
        """Degrees of pan and tilt travel, from the fixture file."""
        out = []
        for role, default in zip(("pan", "tilt"), motion_mod.DEFAULT_SPAN_DEG):
            r = self.head_ranges(h).get(role) or {}
            lo, hi = r.get("min"), r.get("max")
            span = abs(float(hi) - float(lo)) if lo is not None and hi is not None else 0.0
            out.append(span if 20.0 <= span <= 720.0 else default)
        return out[0], out[1]

    def _move_travel(self, h: dict) -> tuple[float, float]:
        """Seconds for a full pan / tilt: measured, else by type."""
        m = self._motion_of(h)
        dp, dt = motion_mod.TRAVEL_S.get(fixture_kind.describe(h).get("type"),
                                         motion_mod.DEFAULT_TRAVEL_S)
        return float(m.get("pan_s") or dp), float(m.get("tilt_s") or dt)

    @staticmethod
    def _clean_beats(value) -> float:
        try:
            b = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"beats: a number like 1, 2, 4 (or 0.5), not {value!r}") from None
        if not 0.125 <= b <= 64:
            raise ValueError("beats is 0.125 .. 64")
        return b

    def _a_fx_beats(self, id=None, beats=None, **_):
        """Lock a running effect to the beat: one cycle per `beats` beats
        (1, 2, 4, 8 ...); beats=0 lets it run free on its own speed."""
        try:
            tid = int(id)
        except (TypeError, ValueError):
            raise ValueError(f"bad effect id: {id!r}") from None
        row = next((r for r in self.fx if r["id"] == tid), None)
        if row is None:
            raise ValueError(f"no effect {tid} running")
        params = row.setdefault("params", {})
        if beats in (None, "", 0, "0", False, "free"):
            params.pop("beats", None)
            # carry on from where the beat left it: no jump when it goes free
            if "_beat_cycles" in row:
                speed = float(params.get("speed") or row.get("speed") or 1.0)
                row["_v"] = row["_beat_cycles"] / max(speed, 1e-6)
                row["_turns"] = row["_beat_cycles"]
                row["_last"] = time.monotonic()
            return {"fx": tid, "beats": None, "summary": f"effect {tid} runs free"}
        params["beats"] = self._clean_beats(beats)
        row.pop("_cap", None)
        b = params["beats"]
        return {"fx": tid, "beats": b, "summary": f"effect {tid}: one cycle every {b:g} beat{'s' if b != 1 else ''}"}

    def _a_speed_master(self, value=None, pct=None, **_):
        """The Speed master: every running effect's speed x value (0.1 .. 4);
        pct=50 is half speed."""
        if pct is not None:
            value = float(pct) / 100.0
        if value is None:
            raise ValueError("value is required (1 = normal, 0.5 = half, 2 = double)")
        self.speed_master = max(0.05, min(4.0, float(value)))
        self.tempo_follow = False            # set by hand: it no longer follows the tempo
        return {"speed_master": self.speed_master,
                "summary": f"effects at {round(self.speed_master * 100)}% speed"}

    def _fx_values(self, now: float | None = None) -> dict[int, dict[str, int]]:
        """{head_no: {role: value}} for every running, unexpired effect.

        Called from the frame builder and the look feed (both under the
        lock); expired effects are dropped here, so no other bookkeeping
        is needed.
        """
        if not self.fx:
            return {}
        now = time.monotonic() if now is None else now
        out: dict[int, dict[str, int]] = {}
        keep: list[dict] = []
        base = None                       # programmer + cues under the effects
        for row in self.fx:
            elapsed = now - row["t0"]
            dur = row.get("duration")
            if dur is not None and elapsed >= dur:
                continue                              # expired - drop it
            keep.append(row)
            heads = row["heads"]
            count = len(heads)
            # the effect's own clock, run at the Speed master's rate: changing
            # the master changes the SPEED from here on, never jumps the shape
            last = row.get("_last", row["t0"])
            master = 1.0 if row.get("free") else float(self.speed_master)
            step = max(0.0, now - last) * master * float(row.get("rate", 1.0))
            row["_last"] = max(last, now)
            row["_v"] = row.get("_v", 0.0) + step
            beats = (row.get("params") or {}).get("beats")
            if beats:
                # locked to the beat clock: one cycle per `beats` beats, in
                # phase with the downbeat - the clock, not the elapsed time
                cyc = self._tempo().beats(now) / float(beats)
                row["_beat_cycles"] = cyc
                if row.get("lib") not in motion_mod.KINDS:
                    speed = float((row.get("params") or {}).get("speed") or row.get("speed") or 1.0)
                    row["_v"] = cyc / max(speed, 1e-6)
            if row.get("lib") in motion_mod.KINDS:
                if base is None:
                    base = (self._programmer_now(now), self._active_playbacks(now))
                self._move_values(row, step, base, out)
                continue
            elapsed = row["_v"]
            if row.get("lib"):
                # A NAMED effect writes SEVERAL roles per head, so the
                # per-head dict is UPDATED rather than assigned.  The
                # index passed in is the head's position among the heads
                # this effect actually runs on - which is the running set,
                # not the original selection - so a spread across two movers
                # out of a mixed six spans those two, not every sixth of the
                # original selection.
                by_no = {h["head_no"]: h for h in self.patch}
                if row.get("across"):
                    # every head of a multi-head light is one step of it
                    units = []
                    for head_no in heads:
                        reps = merge._repeated(by_no.get(head_no, {}).get("map") or [])
                        n_cells = max(reps.values()) if reps else 0
                        units += [(head_no, k) for k in range(1, n_cells + 1)] if n_cells else [(head_no, None)]
                    for i, (head_no, k) in enumerate(units):
                        roles = by_no.get(head_no, {}).get("map") or []
                        reps = merge._repeated(roles)
                        try:
                            vals = fxlib_mod.apply(row["lib"], {}, roles, params=row.get("params"),
                                                   elapsed=elapsed, index=i, count=len(units))
                        except ValueError:
                            continue
                        dst = out.setdefault(head_no, {})
                        for role, v in (vals or {}).items():
                            if k is None:
                                dst[role] = v
                            elif role in reps and k <= reps[role]:
                                dst[f"{role}@{k}"] = v
                            elif role not in reps and k == 1:
                                dst[role] = v          # the light's single channels, once
                    continue
                for i, head_no in enumerate(heads):
                    roles = by_no.get(head_no, {}).get("map") or []
                    try:
                        vals = fxlib_mod.apply(
                            row["lib"], {}, roles, params=row.get("params"),
                            elapsed=elapsed, index=i, count=count)
                    except ValueError:
                        # The patch changed under a running effect - a mode
                        # was re-imported and the head lost the channel.  Drop
                        # the effect rather than throwing inside the frame
                        # builder, which would take the whole DMX tick with
                        # it.  Silently, because there is nobody to tell at
                        # 200 Hz; the effect stops being listed.
                        continue
                    if vals:
                        out.setdefault(head_no, {}).update(vals)
                continue
            for i, head_no in enumerate(heads):
                cycles = fxmod.cycles_for(elapsed, row["speed"], row["phase"],
                                          row["spread"], i, count)
                value = fxmod.fx_value(row["kind"], cycles, row["base"],
                                       row["depth"],
                                       seed=row["id"] * 1000 + head_no,
                                       low=row["low"], high=row["high"])
                out.setdefault(head_no, {})[row["role"]] = value
        if len(keep) != len(self.fx):
            self.fx = keep
        return out

    # --- palettes --------------------------------------------------------
    def _a_record_palette(self, kind=None, name="", palette=None, **_):
        key = str(kind or "").strip().lower()
        if key not in PALETTE_KINDS:
            raise ValueError(f"kind must be one of {sorted(PALETTE_KINDS)}")
        heads = self._require_selection()
        attrs = PALETTE_KINDS[key]
        # ONE value set, not a per-head row.  Every selected head normally
        # agrees (a palette is recorded from a selection the operator has
        # just made uniform), and where they do not, the most common value
        # is the honest reading - and the operator is told, because
        # silently averaging a colour nobody asked for is worse than a
        # clear message.
        votes: dict[str, dict] = {}
        disagree = 0
        for h in heads:
            row = {k: v for k, v in self.programmer.get(h["head_no"],
                                                        {}).items()
                   if k in attrs}
            if not row:
                continue
            for role, value in row.items():
                votes.setdefault(role, {}).setdefault(repr(value), 0)
                votes[role][repr(value)] += 1
        if not votes:
            raise ValueError(f"programmer holds no {key} values")
        values: dict[str, object] = {}
        for role, tally in votes.items():
            top = max(tally.values())
            if top < sum(tally.values()):
                disagree += 1
            try:
                values[role] = json.loads(
                    max(tally.items(), key=lambda kv: kv[1])[0])
            except (TypeError, ValueError):
                continue
        if not values:
            raise ValueError(f"programmer holds no {key} values")
        n = int(palette) if palette else len(self.palettes[key]) + 1
        label = str(name).strip() or f"{key.title()} {n}"
        entry = {"n": n, "name": label, "values": values}
        for i, old in enumerate(self.palettes[key]):
            if old["n"] == n:
                self.palettes[key][i] = entry
                break
        else:
            self.palettes[key].append(entry)
        note = (f" ({disagree} value(s) differed across the selection - "
                f"kept the most common)" if disagree else "")
        return {"kind": key, "n": n, "name": label, "disagree": disagree,
                "roles": sorted(values),
                "summary": f"recorded {key} palette {label}{note}"}

    @staticmethod
    def _by_number_or_name(rows: list[dict], ref) -> dict | None:
        """A palette or preset by its number, or by its name (any case)."""
        if ref is None or ref == "":
            return None
        if _is_int(ref):
            return next((p for p in rows if p["n"] == int(ref)), None)
        want = str(ref).strip().lower()
        return next((p for p in rows
                     if str(p.get("name", "")).strip().lower() == want), None)

    def _a_include_palette(self, kind=None, n=None, palette=None, **_):
        key = str(kind or "").strip().lower()
        if key not in PALETTE_KINDS:
            raise ValueError(f"kind must be one of {sorted(PALETTE_KINDS)}")
        ref = n if n is not None else palette
        entry = self._by_number_or_name(self.palettes[key], ref)
        if entry is None:
            raise ValueError(f"no {key} palette {ref!r}")
        # The SELECTION decides who gets it - there is no longer a list of
        # heads baked into the entry, so the bug this replaces (a disjoint
        # selection falling through and writing to the recorded heads) is
        # not possible to express any more, rather than merely guarded.
        heads = self._require_selection()
        applied, skipped = 0, []
        for h in heads:
            row = {r: v for r, v in (entry.get("values") or {}).items()
                   if r in (h.get("map") or [])}
            if not row:
                skipped.append(h["head_no"])
                continue
            self.programmer.setdefault(h["head_no"], {}).update(row)
            applied += 1
        if not applied:
            raise ValueError(
                f"{entry['name']} has nothing for the selected head(s) - "
                f"they have none of: {', '.join(sorted(entry.get('values') or {}))}")
        return {"kind": key, "n": entry["n"], "heads": applied,
                "skipped": skipped,
                "summary": (f"included {key} {entry['name']} on {applied} head(s)"
                            + (f"; {len(skipped)} had no matching channel"
                               if skipped else ""))}

    # --- presets ----------------------------------------------------------
    LOOK_PARTS = ("intensity", "colour", "position", "beam", "other", "fx")

    def _look_hexes(self, heads: list[dict], values_of) -> list[str]:
        """Up to three colours a look shows, for its tile."""
        out: list[str] = []
        for h in heads:
            v = values_of(h) or {}
            hexc = None
            if any(r in v for r in ("red", "green", "blue")):
                hexc = "#%02x%02x%02x" % tuple(max(0, min(255, int(v.get(c, 0)))) for c in ("red", "green", "blue"))
            elif "wheel" in v:
                for slot in (self.head_ranges(h).get("wheel") or {}).get("slots") or []:
                    if slot["from"] <= int(v["wheel"]) <= slot["to"] and slot.get("hex"):
                        hexc = slot["hex"]
                        break
            if hexc and hexc not in out:
                out.append(hexc)
            if len(out) >= 3:
                break
        return out

    def _a_record_preset(self, name="", preset=None, include=None, **_):
        """Save a LOOK: the selection's programmer (or every light the
        programmer holds, with nothing selected) under a name - colour,
        position, beam, level and the effects running on those lights,
        or just the parts in `include`.  One tap on it brings the whole
        thing back.  Re-recording an existing number overwrites it."""
        parts = set(self.LOOK_PARTS if not include else
                    [str(x).lower() for x in (include if isinstance(include, (list, tuple)) else [include])])
        bad = parts - set(self.LOOK_PARTS)
        if bad:
            raise ValueError(f"include is any of {', '.join(self.LOOK_PARTS)}")
        if self.selected:
            heads = self._require_selection()
        else:
            nums = {n for n, row in self.programmer.items() if row} | \
                {n for f in self.fx if f.get("lib") for n in f.get("heads") or []}
            heads = [h for h in self.patch if h["head_no"] in nums]
            if not heads:
                raise ValueError("programmer is empty - set something first")
        nums = {h["head_no"] for h in heads}
        values: dict[str, object] = {}
        used: list[int] = []
        for h in heads:
            row = {r: v for r, v in (self.programmer.get(h["head_no"]) or {}).items()
                   if r.split("@", 1)[0] in PRESET_ROLES and self.attr_group(r) in parts}
            if not row:
                continue
            used.append(h["head_no"])
            for role, value in row.items():
                votes = values.setdefault(role, {})
                votes.setdefault(repr(value), 0)
                votes[repr(value)] += 1
        fx = []
        if "fx" in parts:
            for f in self.fx:
                if f.get("lib") and nums & set(f.get("heads") or []) and len(fx) < 6:
                    fx.append({"name": f["lib"], "params": dict(f.get("params") or {})})
                    used.extend(n for n in f["heads"] if n in nums and n not in used)
        if not values and not fx:
            raise ValueError("programmer is empty - set something first")
        collapsed: dict[str, object] = {}
        for role, tally in values.items():
            try:
                collapsed[role] = json.loads(
                    max(tally.items(), key=lambda kv: kv[1])[0])
            except (TypeError, ValueError):
                continue
        n = int(preset) if preset else max([p["n"] for p in self.presets] or [0]) + 1
        label = str(name).strip()[:32] or f"Look {n}"
        groups = sorted({self.attr_group(r) for r in collapsed})
        tags = [{"intensity": "Level", "colour": "Colour", "position": "Position", "beam": "Beam",
                 "other": "Other"}[g] for g in groups]
        tags += [fxlib_mod.FX[f["name"]]["label"] for f in fx]
        entry = {"n": n, "name": label, "values": collapsed,
                 "heads": len(used), "head_list": sorted(set(used)), "fx": fx,
                 "hexes": self._look_hexes([h for h in heads if h["head_no"] in used],
                                           lambda h: self.programmer.get(h["head_no"])),
                 "tags": tags,
                 # what kinds of light it was made on: at another venue it can
                 # play on "any light of these types"
                 "types": sorted({fixture_kind.describe(h)["type"] for h in heads if h["head_no"] in used})}
        kinds = {}
        for h in heads:
            if h["head_no"] in used:
                d = fixture_kind.describe(h)
                kinds[d["type"]] = d.get("label") or d["type"].replace("_", " ").title()
        entry["type_labels"] = [kinds[t] for t in entry["types"]]
        for i, old in enumerate(self.presets):
            if old["n"] == n:
                self.presets[i] = entry
                break
        else:
            self.presets.append(entry)
        return {"n": n, "name": label, "heads": len(used), "fx": len(fx),
                "roles": sorted(collapsed),
                "summary": f"saved look {label} ({len(used)} light(s)"
                           + (f", {len(fx)} effect(s)" if fx else "") + ")"}

    def _look_type_heads(self, entry: dict) -> list[dict]:
        """Every patched light of the kinds a look was made on."""
        types = set(entry.get("types") or [])
        if not types:
            return []
        return [h for h in self.patch if fixture_kind.describe(h)["type"] in types]

    def _a_include_preset(self, n=None, preset=None, on=None, **_):
        """Play a look: on the selection, or - with nothing selected - on the
        lights it was saved from (or, when those aren't patched here, on
        every light of the same kinds).  on="types" plays it on every light
        of those kinds.  Its effects start too, taking over from effects of
        the same kind already on those lights."""
        ref = n if n is not None else preset
        entry = self._by_number_or_name(self.presets, ref)
        if entry is None:
            raise ValueError(f"no preset {ref!r}")
        if on == "types":
            heads = self._look_type_heads(entry)
            if not heads:
                raise ValueError(f"no lights of the kinds {entry['name']} was made on")
        elif self.selected:
            heads = self._require_selection()
        else:
            want = set(entry.get("head_list") or [])
            heads = [h for h in self.patch if h["head_no"] in want] or self._look_type_heads(entry)
            if not heads:
                raise ValueError(f"select the lights for {entry['name']} first")
        applied, skipped = 0, []
        for h in heads:
            row = {r: v for r, v in (entry.get("values") or {}).items()
                   if r.split("@", 1)[0] in (h.get("map") or [])}
            if not row:
                continue
            self.programmer.setdefault(h["head_no"], {}).update(row)
            applied += 1
        nums = sorted(h["head_no"] for h in heads)
        started = 0
        for item in entry.get("fx") or []:
            group = (fxlib_mod.FX.get(item["name"]) or {}).get("group")
            self.fx = [f for f in self.fx if not (f.get("lib") and set(f.get("heads") or []) & set(nums)
                                                  and (fxlib_mod.FX.get(f["lib"]) or {}).get("group") == group)]
            try:
                self._a_run_fx_named(item["name"], item.get("params") or {}, None, nums, None)
                started += 1
            except ValueError:
                pass
        for h in heads:
            if not any(r in (h.get("map") or []) for r in (entry.get("values") or {})):
                skipped.append(h["head_no"])
        if not applied and not started:
            raise ValueError(
                f"{entry['name']} has nothing for the selected head(s)")
        return {"n": entry["n"], "name": entry["name"], "heads": applied or len(nums),
                "skipped": skipped, "fx": started,
                "summary": f"look {entry['name']} on {applied or len(nums)} light(s)"
                           + (f" + {started} effect(s)" if started else "")
                           + (f"; {len(skipped)} had no matching channel" if skipped and applied else "")}

    def _a_rename_preset(self, n=None, preset=None, name="", **_):
        entry = self._by_number_or_name(self.presets, n if n is not None else preset)
        if entry is None:
            raise ValueError(f"no look {n or preset!r}")
        new = str(name or "").strip()[:32]
        if not new:
            raise ValueError("a look needs a name")
        entry["name"] = new
        return {"n": entry["n"], "summary": f"renamed to {new}"}

    def _a_delete_preset(self, n=None, preset=None, **_):
        num = int(n if n is not None else preset or 0)
        before = len(self.presets)
        self.presets = [p for p in self.presets if p["n"] != num]
        if len(self.presets) == before:
            raise ValueError(f"no preset {num}")
        return {"n": num, "summary": f"deleted preset {num}"}
