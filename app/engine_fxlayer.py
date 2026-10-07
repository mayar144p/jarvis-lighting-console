"""The FX layer: special effects, lasers, the light test and speed calibration.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import math
import re
import time

from app import fixture_kind, fixtures, merge
from app.merge import FX_OUTPUT_ROLES
from app.engine_base import _LEVELS_CACHE, _OFFISH, _clamp, _truthy, attr_domain
from app.engine_support import HTP_ROLES, LASER_ROLES


class FxLayerMixin:
    # ------------------------------------------------------------------
    # the FX layer: special effects and lasers
    # ------------------------------------------------------------------
    # An effect's output (fire, arm, fog, laser power) moves ONLY here.
    # Light actions never reach it (merge drops every other source), fire
    # and laser need the desk ARMED, every run has a hard time limit even
    # if a button sticks, blackout / Kill FX / loading a show stop it all,
    # and the desk always starts disarmed.
    FX_ARM_S = 600.0

    def _sfx_armed(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) < self.fx_armed_until

    def _sfx_detail(self, h: dict, role: str) -> dict:
        return self.head_ranges(h).get(role) or {}

    def _sfx_heads(self, heads, kind: str) -> list[dict]:
        """The patched heads among `heads` that can do `kind`."""
        role = {"fire": "fx_fire", "fog": "fog", "laser": "laser_on"}[kind]
        by = {h["head_no"]: h for h in self.patch}

        def can(h):
            if role in h["map"]:
                return True
            # a beam bar: no power channel, its diodes are the output; a
            # laser with no power channel at all: its beam switch is
            return kind == "laser" and (any(r.startswith("laser_beam") for r in h["map"])
                                        or bool(self._laser_switch(h)))
        return [by[n] for n in heads or [] if n in by and can(by[n])]

    def _sfx_limit(self, h: dict, kind: str) -> float:
        role = {"fire": "fx_fire", "fog": "fog", "laser": "laser_on"}[kind]
        d = self._sfx_detail(h, role)
        if kind == "laser":
            # a laser has no time limit of its own (the library's 600 s made
            # lasers go dark mid-set): ARM is its safety - it goes dark the
            # moment effects are disarmed or killed
            return math.inf
        cap = float(d.get("max_s") or {"fire": 3.0, "fog": 20.0}[kind])
        if d.get("fx_kind") == "confetti":
            cap = min(cap, self.fx_loads.get(h["head_no"], cap))
        return max(0.0, cap)

    def _sfx_finish(self, now: float | None = None) -> None:
        """Retire runs whose time is up, and charge confetti tanks."""
        now = time.monotonic() if now is None else now
        for key in [k for k, r in self.fx_runs.items() if now >= r["until"]]:
            self._sfx_end(key, now)

    def _sfx_end(self, key: str, now: float) -> None:
        run = self.fx_runs.pop(key, None)
        if not run:
            return
        used = max(0.0, min(now, run["until"]) - run["since"])
        for n in run["heads"]:
            h = next((x for x in self.patch if x["head_no"] == n), None)
            if h is not None and self._sfx_detail(h, "fx_fire").get("fx_kind") == "confetti":
                full = float(self._sfx_detail(h, "fx_fire").get("max_s") or 30)
                self.fx_loads[n] = max(0.0, self.fx_loads.get(n, full) - used)
        if key in self.quick_active and self.quick_active[key].get("fx_layer"):
            self.quick_active.pop(key, None)
        self.act_rev += 1

    def _sfx_start(self, key: str, kind: str, heads, owner: str = "hand",
                  seconds=None, level=100, values=None) -> bool:
        now = time.monotonic()
        self._sfx_finish(now)
        if kind in ("fire", "laser") and not self._sfx_armed(now):
            return False
        targets = self._sfx_heads(heads, kind)
        if not targets:
            return False
        limit = min(self._sfx_limit(h, kind) for h in targets)
        if seconds is not None:
            limit = min(limit, max(0.2, float(seconds)))
        if limit <= 0:
            return False                         # a confetti tank is empty
        run = self.fx_runs.get(key)
        if run is None:
            run = self.fx_runs[key] = {"kind": kind, "heads": [h["head_no"] for h in targets],
                                       "since": now, "until": now + limit, "owners": set(),
                                       "level": int(_clamp(level, 1, 100)),
                                       "values": dict(values or {})}
        run["owners"].add(owner)
        self.act_rev += 1
        return True

    def _sfx_stop(self, key: str, owner: str = "hand", force: bool = False) -> None:
        run = self.fx_runs.get(key)
        if run is None:
            return
        run["owners"].discard(owner)
        if force or not run["owners"]:
            self._sfx_end(key, time.monotonic())

    def _sfx_override_vals(self, now: float | None = None) -> dict:
        """{head_no: {role: value}} the FX layer drives right now.  Read
        only (it runs inside build_frames): expired runs are ignored here
        and retired by the next FX action or status read."""
        now = time.monotonic() if now is None else now
        armed = self._sfx_armed(now)
        by = {h["head_no"]: h for h in self.patch}
        out: dict[int, dict] = {}
        if armed:
            for h in self.patch:
                if "fx_arm" in h["map"]:
                    d = self._sfx_detail(h, "fx_arm")
                    out.setdefault(h["head_no"], {})["fx_arm"] = int(d.get("on_value") or 255)
        for run in self.fx_runs.values():
            if now >= run["until"] or (run["kind"] != "fog" and not armed):
                continue
            for n in run["heads"]:
                h = by.get(n)
                if h is None:
                    continue
                sets = out.setdefault(n, {})
                if run["kind"] == "fire":
                    sets["fx_fire"] = int(self._sfx_detail(h, "fx_fire").get("on_value") or 255)
                elif run["kind"] == "fog":
                    off = int(self._sfx_detail(h, "fog").get("off_value") or 0)
                    sets["fog"] = off + round((255 - off) * run["level"] / 100)
                else:
                    # on = the button's own mode, else the programmed/cue mode
                    # (the merge swaps it in), else the fixture's "on" value
                    if "laser_on" in run["values"]:
                        sets["laser_on"] = int(run["values"]["laser_on"])
                    else:
                        sets["laser_on"] = int(self._sfx_detail(h, "laser_on").get("on_value") or 255)
                        sets["_laser_min"] = self._laser_min(h)
                    # a beam bar has no power channel: its diodes read this
                    sets["_laser_live"] = 1
                    beam = next((r for r in h["map"] if r.startswith("laser_beam")), None)
                    if beam:
                        sets["_beam_on"] = int(self._sfx_detail(h, beam).get("on_value") or 255)
                    for role, v in run["values"].items():
                        if role in h["map"]:
                            sets[role] = int(v)
        return out

    def _sfx_public(self) -> dict:
        now = time.monotonic()
        self._sfx_finish(now)
        loads = {}
        for h in self.patch:
            d = self._sfx_detail(h, "fx_fire") if "fx_fire" in h["map"] else {}
            if d.get("fx_kind") == "confetti":
                full = float(d.get("max_s") or 30)
                left = self.fx_loads.get(h["head_no"], full)
                for r in self.fx_runs.values():         # a shot in progress
                    if r["kind"] == "fire" and h["head_no"] in r["heads"]:
                        left -= max(0.0, min(now, r["until"]) - r["since"])
                loads[h["head_no"]] = {"left": round(max(0.0, left), 1), "full": full}
        forever = math.isinf(self.fx_armed_until)
        return {"armed": self._sfx_armed(now),
                "armed_left": 0 if forever else max(0, round(self.fx_armed_until - now)),
                "armed_forever": forever,
                "runs": [{"key": k, "kind": r["kind"], "heads": r["heads"],
                          "left": None if math.isinf(r["until"]) else round(max(0.0, r["until"] - now), 1)}
                         for k, r in self.fx_runs.items()],
                "loads": loads,
                "heads": {h["head_no"]: self._head_class(h) for h in self.patch
                          if self._head_class(h) != "light"}}

    def _sfx_targets(self, heads=None, group=None) -> list[int]:
        if heads is not None:
            nums = [int(x) for x in (heads if isinstance(heads, (list, tuple)) else [heads])]
        elif group is not None:
            nums = self._target_heads({"group": int(group)})
        else:
            nums = list(self.selected) or [h["head_no"] for h in self.patch]
        by = {h["head_no"]: h for h in self.patch}
        return [n for n in nums if n in by and self._head_class(by[n]) != "light"]

    def _a_fx_arm(self, state=True, minutes=None, **_):
        """ARM (or disarm) special effects and lasers.  Fire and laser
        output only work while armed; it switches itself off after
        `minutes` (10 by default), or never with minutes="until" - then
        only a disarm or KILL FX ends it (a laser stays on for the set)."""
        if _truthy(state):
            if str(minutes).lower() in ("0", "until", "forever", "show", "none", "-1", "inf"):
                # armed for the whole set: only a disarm or KILL FX ends it
                self.fx_armed_until = math.inf
                return {"armed": True, "summary": "effects ARMED until you disarm"}
            secs = self.FX_ARM_S if minutes in (None, "") else max(30.0, min(12 * 3600.0, float(minutes) * 60))
            self.fx_armed_until = time.monotonic() + secs
            return {"armed": True, "summary": f"effects ARMED for {secs / 60:g} min"}
        self.fx_armed_until = 0.0
        for key in [k for k, r in self.fx_runs.items() if r["kind"] != "fog"]:
            self._sfx_end(key, time.monotonic())
        return {"armed": False, "summary": "effects disarmed"}

    def _a_fx_fire(self, heads=None, group=None, down=True, seconds=None, owner="hand", **_):
        """Fire SFX (confetti, CO2, flame, sparks) while held, capped at
        each machine's limit.  Needs ARM."""
        key = f"fire:{owner}"
        if not _truthy(down):
            self._sfx_stop(key, owner, force=True)
            return {"summary": "fire stopped"}
        if not self._sfx_armed():
            raise ValueError("ARM the effects first")
        nums = self._sfx_targets(heads, group)
        if not self._sfx_start(key, "fire", nums, owner, seconds=seconds):
            raise ValueError("nothing to fire (no SFX selected, or a confetti tank is empty)")
        return {"summary": f"FIRE on {len(self.fx_runs[key]['heads'])} effect(s)"}

    def _a_fx_fog(self, heads=None, group=None, level=100, seconds=10, down=True, owner="hand", **_):
        """Fog / haze at `level` % for `seconds` (or while held)."""
        key = f"fog:{owner}"
        if not _truthy(down):
            self._sfx_stop(key, owner, force=True)
            return {"summary": "fog stopped"}
        nums = self._sfx_targets(heads, group)
        if not self._sfx_start(key, "fog", nums, owner, seconds=seconds, level=level):
            raise ValueError("no fog or haze machine selected")
        return {"summary": f"fog {int(_clamp(level, 1, 100))}% for {float(seconds or 0):g} s"}

    def _a_fx_laser(self, heads=None, group=None, down=True, seconds=None, values=None, owner="hand", **_):
        """Laser output on while held (pattern etc. from `values` or the
        programmer).  Needs ARM."""
        key = f"laser:{owner}"
        if not _truthy(down):
            self._sfx_stop(key, owner, force=True)
            return {"summary": "laser off"}
        if not self._sfx_armed():
            raise ValueError("ARM the effects first")
        nums = self._sfx_targets(heads, group)
        vals = {str(k): int(_clamp(v, 0, 255)) for k, v in (values or {}).items()
                if str(k) in LASER_ROLES}
        if not self._sfx_start(key, "laser", nums, owner, seconds=seconds, values=vals):
            raise ValueError("no laser selected")
        return {"summary": f"laser ON ({len(self.fx_runs[key]['heads'])})"}

    # A laser with no output channel (a Laserworld RS400G, a Stairville DJ
    # Lase) switches its beam with a mode / colour / pattern channel whose
    # lowest range is "Laser off", "No beam", "Blackout" or "Blanking" (or
    # "No function" on a "Red laser switched on" channel).  That channel IS
    # its output: off unless armed and fired, like any laser's.
    _SWITCH_OFF = re.compile(r"^\s*(laser\s+)?(off|blackout|blanking|no beam|beam off|no output)\b", re.I)
    _SWITCH_SKIP = frozenset({"laser_x", "laser_y", "laser_rot", "laser_speed", "laser_size"})

    def _laser_switch(self, h: dict) -> dict:
        """{role: (off value, off from, off to, on value)} - the channels that
        switch the beam of a laser with no output channel; {} otherwise."""
        m = h["map"]
        if "laser_on" in m or any(r.startswith("laser_beam") for r in m) \
                or self._head_class(h) != "laser":
            return {}
        out = {}
        for role, d in self.head_ranges(h).items():
            if role not in m or role in self._SWITCH_SKIP or role in FX_OUTPUT_ROLES \
                    or not role.startswith(("fx_", "laser_", "aux")):
                continue
            for raw in d.get("caps_each") or [d.get("caps") or []]:
                caps = [(int(lo), int(hi), str(t)) for lo, hi, t in raw or []]
                zero = next((c for c in caps if c[0] <= 0 <= c[1]), None)
                others = [c for c in caps if c is not zero]
                if zero is None or not others or not (
                        self._SWITCH_OFF.search(zero[2])
                        or (re.match(r"\s*no function", zero[2], re.I)
                            and any(re.search(r"\blaser\b.*\bon\b", c[2], re.I) for c in others))):
                    continue
                on = next((c for c in others if re.search(r"^\s*on\b|switched on", c[2], re.I)), others[0])
                out[role] = (zero[0], zero[0], zero[1], (on[0] + on[1]) // 2)
                break
        return out

    def _laser_min(self, h: dict) -> int:
        """The lowest value on a laser's output channel that is not OFF:
        a programmed mode below it is ignored (it would be "off", and the
        output only ever goes off from its own buttons)."""
        d = self._sfx_detail(h, "laser_on")
        for lo, _hi, text in d.get("caps") or []:
            if not _OFFISH.search(str(text)):
                return int(lo)
        return int(d.get("off_value") or 0) + 1

    def _a_fx_kill(self, **_):
        """Stop every effect and laser at once, and disarm."""
        now = time.monotonic()
        for key in list(self.fx_runs):
            self._sfx_end(key, now)
        self.fx_armed_until = 0.0
        return {"summary": "all effects stopped, disarmed"}

    def _a_fx_reload(self, heads=None, **_):
        """Confetti refilled: tanks back to full."""
        nums = self._sfx_targets(heads) if heads is not None else [h["head_no"] for h in self.patch]
        for n in nums:
            self.fx_loads.pop(n, None)
        return {"summary": "confetti reloaded"}

    def _a_fx_status(self, **_):
        return {"sfx": self._sfx_public(), "summary": "effects armed" if self._sfx_armed() else "effects disarmed"}

    def _drop_fixture_caches(self) -> None:
        """A profile was imported or edited: open values may have changed."""
        self._range_cache.clear()
        _LEVELS_CACHE.clear()
        self._gate_cache = None
        self._rest_cache = None
        self._hold_cache = None
        self._motion_cache = {}

    # -- movement speed, for the visualiser ------------------------------
    def _motion_of(self, head: dict) -> dict:
        """{pan_s, tilt_s} measured for this model, or {} (type defaults)."""
        cache = getattr(self, "_motion_cache", None)
        if cache is None:
            cache = self._motion_cache = {}
        key = (head.get("manufacturer") or "", head.get("model") or "")
        if key not in cache:
            try:
                cache[key] = fixtures.get_motion(self.db_path, *key) or {}
            except Exception:                # the visual is an enhancement
                cache[key] = {}
        return cache[key]

    def _speed_frac(self, head: dict, values: dict) -> float:
        """The pan/tilt speed channel as 0 (fastest) .. 1 (slowest)."""
        if "speed" not in head["map"]:
            return 0.0
        v = max(0, min(255, int(values.get("speed", 0)))) / 255.0
        fast_first = (self.head_ranges(head).get("speed") or {}).get("fast_first")
        return round(v if fast_first is not False else 1.0 - v, 3)

    def _strobe_hz(self, head: dict, values: dict, over: dict | None) -> float:
        """How fast the light is really strobing, 0 for steady.  From a
        strobe button, else the strobe channel inside a range the
        fixture's file marks as strobe (never an 'open' value)."""
        if over and over.get("strobe"):
            return round(float(over["strobe"]), 2)
        role = self._shutter_role(head)
        if role is None or role not in values:
            return 0.0
        v = max(0, min(255, int(values[role])))
        ranges = (self.head_ranges(head).get(role) or {}).get("strobe_ranges")
        if ranges:
            for lo, hi in ranges:
                if lo <= v <= hi:
                    return round(1 + 19 * (v - lo) / max(1, hi - lo), 2)
            return 0.0
        opened = self._open_value(head, role)
        if v > opened + 8 and v < 248:        # no ranges known: a guess
            return round(1 + 19 * (v - opened) / max(1, 255 - opened), 2)
        return 0.0

    def _a_motion_set(self, head=None, pan_s=None, tilt_s=None, clear=False, **_):
        """Store how long this fixture model takes for a full pan and a
        full tilt at top speed (seconds), so the visualiser moves it at
        the real speed.  Applies to every head of the same model."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        if _truthy(clear):
            fixtures.set_motion(self.db_path, h.get("manufacturer"), h.get("model"), None, None)
            self._motion_cache = {}
            return {"summary": f"{h.get('model')}: movement back to defaults"}
        vals = {}
        for key, v in (("pan_s", pan_s), ("tilt_s", tilt_s)):
            if v is None or v == "":
                vals[key] = self._motion_of(h).get(key)
                continue
            f = float(v)
            if not 0.2 <= f <= 60:
                raise ValueError(f"{key.split('_')[0]} time must be 0.2 to 60 seconds")
            vals[key] = round(f, 2)
        fixtures.set_motion(self.db_path, h.get("manufacturer"), h.get("model"),
                            vals["pan_s"], vals["tilt_s"])
        self._motion_cache = {}
        parts = [f"{k.split('_')[0]} {v:g} s" for k, v in vals.items() if v]
        return {"motion": vals, "summary": f"{h.get('model')}: full " + ", ".join(parts)}

    def _open_known(self, head: dict, role: str) -> bool:
        """Does Jarvis KNOW which value opens this shutter (file, profile
        or the operator), rather than assuming 0?"""
        rng = self.head_ranges(head).get(role) or {}
        return (rng.get("open_from") is not None
                or self._profile_levels(head)[1].get(role) not in (None, 0)
                or bool(rng.get("strobe_ranges")))

    def _gate_info(self, h: dict) -> dict | None:
        """{role, open, known} for a light with a shutter/strobe channel."""
        role = self._shutter_role(h)
        if role is None or self._head_class(h) != "light":
            return None
        return {"role": role, "open": self._open_value(h, role), "known": self._open_known(h, role),
                "tested": self._tested(h)}

    # -- "Test this light": catch a bad fixture file at setup, not at the gig
    def _open_candidates(self, h: dict, role: str) -> list[int]:
        """Shutter values worth trying, most likely first: the file's own
        'open' ranges, then its other steady ranges, then the values
        manufacturers commonly use."""
        caps = (self.head_ranges(h).get(role) or {}).get("caps") or []
        out: list[int] = []
        words_open = re.compile(r"\bopen\b|\bon\b|no strobe|strobe off|shutter open", re.I)
        steady = re.compile(r"strobe|pulse|random|closed|\boff\b|blackout|reset|sound|effect", re.I)
        for lo, hi, text in caps:
            if words_open.search(str(text)) and not re.search(r"clos", str(text), re.I):
                out.append((int(lo) + int(hi)) // 2)
        for lo, hi, text in caps:
            if not steady.search(str(text)):
                out.append((int(lo) + int(hi)) // 2)
        out += [0, 5, 8, 12, 20, 32, 255, 250, 128, 64]
        seen, uniq = set(), []
        for v in out:
            v = int(_clamp(v, 0, 255))
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        return uniq[:14]

    _HUNT_SKIP = {"pan", "tilt", "speed", "red", "green", "blue", "white", "amber", "uv",
                  "cyan", "magenta", "yellow", "cto", "lime", "indigo"}

    def _hunt_list(self, h: dict) -> list[dict]:
        """When the shutter values did not light it: every other channel,
        one at a time, at the value most likely to mean lamp on / open
        (from its own ranges) and at full - the operator watches the real
        light.  Catches a shutter the file did not name as one, a lamp-on
        control, and a mode whose channels sit elsewhere."""
        gate = self._shutter_role(h)
        ranges = self.head_ranges(h)
        lit = set(self._intensity_roles(h))
        out = []
        for role in dict.fromkeys(h["map"]):
            if (role in self._HUNT_SKIP or role in lit or role.endswith("_fine") or role == gate
                    or role.startswith("_") or role in ("raw", "unused")):
                continue
            vals = []
            for lo, hi, text in (ranges.get(role) or {}).get("caps") or []:
                t = str(text).lower()
                if re.search(r"\b(open|on|lamp on|no strobe|strobe off)\b", t) and "clos" not in t:
                    vals.append((int(lo) + int(hi)) // 2)
            vals.append(255)
            for v in list(dict.fromkeys(vals))[:2]:
                out.append({"role": role, "value": v, "label": (ranges.get(role) or {}).get("label") or role})
        return out[:24]

    def _test_slots(self, h: dict) -> list[dict]:
        """Every DMX channel of the head: its label, role, address and the
        byte on the wire now - the light test's channel faders."""
        labels = fixtures.mode_channels(self.db_path, h.get("manufacturer"), h.get("model"), h.get("mode"))
        buf = self.build_frames().get(h["universe"])
        base = int(h["address"]) - 1
        return [{"n": i + 1, "abs": base + i + 1, "role": role,
                 "label": (labels[i] if i < len(labels) else "") or role,
                 "value": int(buf[base + i]) if buf is not None and base + i < len(buf) else 0}
                for i, role in enumerate(h["map"])]

    def _tested(self, h: dict) -> bool:
        return bool((self.head_ranges(h).get("_model") or {}).get("tested"))

    def _a_light_test(self, head=None, step="start", value=None, hex=None, role=None, slot=None, **_):
        """Drive one head through the setup test: start (full, open,
        white, centred), open (try a shutter value), pan / tilt (a
        position 0..1), colour (a hex), end (give it back)."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        if self._head_class(h) != "light":
            raise ValueError("lasers and effects are tested from their own tabs, armed")
        n = h["head_no"]
        saved = self.__dict__.setdefault("_test_saved", {})
        step = str(step)
        gate = self._shutter_role(h)
        if step == "end":
            self.__dict__.get("_test_hunt", {}).pop(n, None)
            self.__dict__.get("_test_raw", {}).pop(n, None)
            if n in saved:
                before = saved.pop(n)
                if before:
                    self.programmer[n] = before
                else:
                    self.programmer.pop(n, None)
            return {"summary": f"#{n} back to what it was doing"}
        if n not in saved:
            saved[n] = dict(self.programmer.get(n) or {})
        if step == "start":
            self.programmer[n] = {}
            for role, v in {**self._level_values(h, 100), **self._white_values(h)}.items():
                self._set_programmer(n, role, v)
            for axis in ("pan", "tilt"):
                if axis in h["map"]:
                    self._set_programmer(n, axis, attr_domain(h, axis) // 2)
            if "speed" in h["map"]:
                fast_first = (self.head_ranges(h).get("speed") or {}).get("fast_first")
                self._set_programmer(n, "speed", 0 if fast_first is not False else 255)
            if gate:
                self._set_programmer(n, gate, self._open_value(h, gate))
            colour = [r for r in ("red", "green", "blue", "wheel", "cyan") if r in h["map"]]
            return {"head": n, "model": h.get("model"), "gate": gate,
                    "open": self._open_value(h, gate) if gate else None,
                    "open_known": self._open_known(h, gate) if gate else True,
                    "candidates": self._open_candidates(h, gate) if gate else [],
                    "hunt": self._hunt_list(h),
                    "slots": self._test_slots(h),
                    "pan": "pan" in h["map"], "tilt": "tilt" in h["map"],
                    "colour": bool(colour), "mixing": "red" in h["map"] or "cyan" in h["map"],
                    "channels": len(h["map"]), "mode": h.get("mode"),
                    "address": f"{h['universe']}.{h['address']}",
                    "summary": f"#{n} lit white and centred for the test"}
        if step == "open":
            if not gate:
                raise ValueError("this light has no shutter channel")
            self._set_programmer(n, gate, int(_clamp(value, 0, 255)))
            return {"summary": f"shutter at {int(_clamp(value, 0, 255))}"}
        if step in ("pan", "tilt"):
            if step not in h["map"]:
                raise ValueError(f"this light has no {step}")
            frac = max(0.0, min(1.0, float(value if value is not None else 0.5)))
            self._set_programmer(n, step, int(round(attr_domain(h, step) * frac)))
            return {"summary": f"{step} to {round(frac * 100)}%"}
        if step == "raw":
            # one DMX channel of this head, straight to the wire (slot 1 =
            # its start address): finds what the REAL light needs even when
            # the file's channel list, or its mode, is wrong
            live = self.__dict__.setdefault("_test_raw", {})
            if value is None and slot is None:
                live.pop(n, None)
                return {"summary": "channel faders off"}
            s = int(slot or 0)
            if not 1 <= s <= len(h["map"]):
                raise ValueError(f"slot is 1..{len(h['map'])}")
            if value is None:
                live.get(n, {}).pop(s, None)
                return {"summary": f"channel {s} back to the show"}
            v = int(_clamp(value, 0, 255))
            live.setdefault(n, {})[s] = v
            return {"summary": f"channel {s} (DMX {int(h['address']) + s - 1}) at {v}"}
        if step == "keep":
            # what the faders found, kept for the model: a named channel
            # becomes its role's rest/open value, an unnamed one is held
            live = dict(self.__dict__.get("_test_raw", {}).get(n) or {})
            if not live:
                raise ValueError("move a channel fader first")
            kept, hold = [], {}
            for s, v in sorted(live.items()):
                role = h["map"][s - 1]
                if role in ("raw", "unused"):
                    hold[str(s)] = v
                elif role in HTP_ROLES or role in ("pan", "tilt", "speed") or role.endswith("_fine"):
                    continue                      # the show drives these
                else:
                    fixtures.set_override(self.db_path, h.get("manufacturer"), h.get("model"),
                                          h.get("mode"), role, "open_from", v)
                kept.append(f"ch {s} = {v}")
            if hold:
                old = (self.head_ranges(h).get("_model") or {}).get("hold") or {}
                fixtures.set_override(self.db_path, h.get("manufacturer"), h.get("model"),
                                      h.get("mode"), "_model", "hold", {**old, **hold})
            fixtures.invalidate_cache()
            self._drop_fixture_caches()
            return {"kept": kept, "summary": f"{h.get('model')}: kept " + (", ".join(kept) or "nothing")}
        if step == "channel":
            # one other channel at a value; the one tried before goes back
            role = str(role or "")
            if role and role not in h["map"]:
                raise ValueError(f"this light has no {role} channel")
            start = self.__dict__.setdefault("_test_hunt", {})
            prev = start.pop(n, None)
            if prev:
                prole, pval = prev
                if pval is None:
                    (self.programmer.get(n) or {}).pop(prole, None)
                else:
                    self._set_programmer(n, prole, pval)
            if not role or value is None:
                return {"summary": "channels back"}
            start[n] = (role, (self.programmer.get(n) or {}).get(role))
            self._set_programmer(n, role, int(_clamp(value, 0, 255)))
            return {"summary": f"{role} at {int(_clamp(value, 0, 255))}"}
        if step == "colour":
            for role, v in self._colour_values(h, str(hex or "#ffffff")).items():
                self._set_programmer(n, role, v)
            return {"summary": f"colour {hex}"}
        raise ValueError("step is start, open, channel, raw, keep, pan, tilt, colour or end")

    def _a_light_tested(self, head=None, light=True, move=True, colour=True, **_):
        """Record the result: a model that passed is not asked about again."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        ok = _truthy(light) and _truthy(move) and _truthy(colour)
        fixtures.set_override(self.db_path, h.get("manufacturer"), h.get("model"), h.get("mode"),
                              "_model", "tested", bool(ok))
        fixtures.invalidate_cache()
        advice = []
        if not _truthy(light):
            advice.append("It never lit: check its DMX address and cable, that it is in DMX mode, "
                          "and (on a discharge lamp) that the lamp is struck.")
        if not _truthy(move) or not _truthy(colour):
            advice.append(f"Moved or coloured wrongly: the light's channel mode must match the desk's "
                          f"({h.get('mode')}, {len(h['map'])} channels) - set it on the light's "
                          f"menu, or re-add it in the mode the light shows.")
        return {"tested": ok, "advice": advice,
                "summary": f"{h.get('model')}: " + ("passed the test" if ok else "needs attention")}

    def _a_remember_open(self, head=None, value=None, role=None, **_):
        """The value that opens this light's shutter, found on the real
        light: saved for every head of the same model and mode, so Full
        lights it from now on."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        if role:
            if role not in h["map"]:
                raise ValueError(f"this light has no {role} channel")
        else:
            role = self._shutter_role(h)
        if role is None:
            raise ValueError("this light has no shutter or strobe channel")
        if value is None:
            value = (self.programmer.get(h["head_no"]) or {}).get(role)
        if value is None:
            raise ValueError("slide the shutter until the light comes on first")
        v = int(_clamp(value, 0, 255))
        fixtures.set_override(self.db_path, h.get("manufacturer"), h.get("model"),
                              h.get("mode"), role, "open_from", v)
        fixtures.invalidate_cache()
        self._drop_fixture_caches()
        return {"summary": f"{h.get('model')}: {role} opens at {v} - Full will light it"}

    def _a_motion_get(self, head=None, **_):
        """This model's measured full pan/tilt times (null = type default)."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        m = self._motion_of(h)
        return {"motion": {"pan_s": m.get("pan_s"), "tilt_s": m.get("tilt_s")},
                "axes": [a for a in ("pan", "tilt") if a in h["map"]],
                "summary": f"{h.get('model')}: " + (", ".join(
                    f"{k[:-2]} {v:g} s" for k, v in m.items() if v) or "not calibrated")}

    def _a_motion_test(self, head=None, axis="pan", to="start", **_):
        """Calibration move: one head's pan (or tilt) to one end of its
        travel at top speed, the other axis centred and the lamp open.
        The head's own programmer values are restored by motion_test_end."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        axis = str(axis)
        if axis not in ("pan", "tilt") or axis not in h["map"]:
            raise ValueError(f"this fixture has no {axis}")
        n = h["head_no"]
        saved = self.__dict__.setdefault("_motion_saved", {})
        if n not in saved:
            saved[n] = dict(self.programmer.get(n) or {})
        other = "tilt" if axis == "pan" else "pan"
        full = attr_domain(h, axis)
        self._set_programmer(n, axis, 0 if str(to) == "start" else full)
        if other in h["map"]:
            self._set_programmer(n, other, attr_domain(h, other) // 2)
        if "speed" in h["map"]:
            fast_first = (self.head_ranges(h).get("speed") or {}).get("fast_first")
            self._set_programmer(n, "speed", 0 if fast_first is not False else 255)
        for role, v in self._level_values(h, 100).items():
            self._set_programmer(n, role, v)
        gate = self._shutter_role(h)
        if gate:                              # lit and steady, never strobing
            self._set_programmer(n, gate, self._open_value(h, gate))
        return {"summary": f"#{n} {axis} to the {'start' if str(to) == 'start' else 'end'}"}

    def _a_motion_test_end(self, head=None, **_):
        """Give the head back exactly what it had before the test."""
        if head is None:
            raise ValueError("head is required")
        h = self._head(head)
        n = h["head_no"]
        saved = self.__dict__.setdefault("_motion_saved", {})
        if n in saved:
            before = saved.pop(n)
            if before:
                self.programmer[n] = before
            else:
                self.programmer.pop(n, None)
        return {"summary": f"#{n} back to what it was doing"}

    def _quick_public(self) -> dict:
        return {"buttons": [dict(b) for b in self.quick],
                "active": sorted(self.quick_active),
                "names": dict(getattr(self, "quick_names", {}) or {}),
                "pages": self.QUICK_PAGES, "slots": self.QUICK_SLOTS,
                "quant": float(self.__dict__.get("quick_quant", 0.0)),
                "pending": sorted(self.__dict__.get("quick_pending") or {})}

    # height of the tilt axis above the base, per 3D model (web/js/stage/models.js)
    _AIM_PIVOT = {"moving_spot": 0.465, "moving_hybrid": 0.502, "moving_beam": 0.378,
                  "moving_wash": 0.402, "moving_bar": 0.372, "scanner": 0.095}

    def _aim_solve(self, h: dict, tx: float, ty: float, tz: float,
                   near: tuple[float, float] | None = None, closest: bool = False):
        """(pan frac, tilt frac, pan deg, tilt deg) that point head `h` at a
        point in the room, from where it hangs and which way up it is, or
        None if it can't reach it.  `near` (degrees) picks, of the several
        pan/tilt pairs that reach a point, the one closest to it - so a set
        of points (the corners of the dance floor) is reached without the
        head flipping between them."""
        import math
        if "pan" not in h["map"] or "tilt" not in h["map"]:
            return None
        hung = (h.get("stance") == "hang") if h.get("stance") else \
            h.get("kind") == "truss"
        # the tilt axis, where the 3D model has it (measured off each model):
        # a spot's is higher than a wash's; a scanner's mirror sits low and
        # in front of its lamp housing
        d = fixture_kind.describe(h)
        pivot = 0.372 if d.get("heads") else self._AIM_PIVOT.get(d["type"], 0.4)
        ahead = 0.14 if d["type"] == "scanner" else 0.0
        ox, oy, oz = h["x"], h["y"] + (-pivot if hung else pivot), h["z"] + ahead
        dx, dy, dz = tx - ox, ty - oy, tz - oz
        n = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / n, dy / n, dz / n
        lx, ly, lz = (-dx, -dy, dz) if hung else (dx, dy, dz)
        t0 = math.degrees(math.acos(max(-1.0, min(1.0, ly))))
        p0 = math.degrees(math.atan2(lx, lz))
        ranges = self.head_ranges(h)
        pr = ranges.get("pan") or {}
        tr = ranges.get("tilt") or {}
        pmin, pmax = ((pr["min"], pr["max"]) if pr.get("unit") == "degree"
                      and pr.get("min") is not None else (-270.0, 270.0))
        tmin, tmax = ((tr["min"], tr["max"]) if tr.get("unit") == "degree"
                      and tr.get("min") is not None else (-135.0, 135.0))
        if d["type"] == "scanner":
            # a mirror scanner throws its beam FORWARD (+z) at rest: pan
            # swings it sideways, tilt dips it (the 3D model's convention)
            p0 = math.degrees(math.atan2(lx, lz))
            t0 = math.degrees(math.asin(max(-1.0, min(1.0, -ly))))
            cands = [(p0, t0)]
        else:
            cands = [(p0 + k * 360, t0) for k in (-1, 0, 1)]
            cands += [(p0 + 180 + k * 360, -t0) for k in (-2, -1, 0, 1)]
        fits = [(p, t) for p, t in cands
                if pmin - 0.5 <= p <= pmax + 0.5 and tmin - 0.5 <= t <= tmax + 0.5]
        if not fits and closest:
            # out of reach: point as near to it as the light can go (never
            # leave it at home, pointing at the roof)
            fits = [(min(pmax, max(pmin, p)), min(tmax, max(tmin, t))) for p, t in cands]
            fits = [min(fits, key=lambda c: min(abs(c[0] - p) + abs(c[1] - t) for p, t in cands))]
            h_clamped = True
        else:
            h_clamped = False
        if not fits:
            return None
        if near is not None:
            p, t = min(fits, key=lambda c: abs(c[0] - near[0]) + abs(c[1] - near[1]))
        else:
            p, t = min(fits, key=lambda c: abs(c[0]) + abs(c[1]) * 0.25)
        fp = (p - pmin) / ((pmax - pmin) or 1)
        ft = (t - tmin) / ((tmax - tmin) or 1)
        flags = h.get("orient") or {}
        if flags.get("invert_pan"):
            fp = 1 - fp
        if flags.get("invert_tilt"):
            ft = 1 - ft
        if flags.get("swap"):
            fp, ft = ft, fp
        if h_clamped:
            return fp, ft, p, t, "clamped"
        return fp, ft, p, t

    def _aim_heads(self, h: dict, tx: float, ty: float, tz: float, near=None,
                   spread: float = 0.0, cells=None):
        """Per-head tilts for a multi-head light (a Wave 360: one bar that
        pans, each head tilting about the bar).  Its heads can't converge on
        one point - their beams stay in parallel planes - but each can reach
        its own spot along the throw: `spread` metres apart on a line through
        the target, away from the light.  {"tilt@k": value} for the heads
        `cells` (all when empty), or {} for a one-head light."""
        copies = merge._repeated(h["map"]).get("tilt", 0)
        if copies < 2 or (not spread and not cells):
            return {}
        dx, dz = tx - float(h["x"]), tz - float(h["z"])
        n = math.hypot(dx, dz) or 1.0
        ux, uz = dx / n, dz / n
        top_t = 65535 if "tilt_fine" in h["map"] else 255
        out = {}
        for k in (cells or range(1, copies + 1)):
            if not 1 <= k <= copies:
                continue
            off = (k - (copies + 1) / 2) * float(spread or 0)
            s = self._aim_solve(h, tx + ux * off, ty, tz + uz * off, near=near, closest=True)
            if s is not None:
                out[f"tilt@{k}"] = int(round(max(0.0, min(1.0, s[1])) * top_t))
        return out

    # -- follow speed: the engine glides the aim, at the full DMX rate --------
    def _a_aim_at(self, x=None, y=None, z=None, mark=None, heads=None,
                  cell=None, spread=None, glide=None, zone=None, **_):
        """Point every selected moving head at one spot in the room.

        Solved per head from where it hangs and which way up it is, through
        its own pan/tilt travel, so twelve movers on three trusses all land
        on the same mark - the thing you would otherwise do head by head.
        `glide` (seconds): don't jump - glide there, the engine moving the
        aim every frame (Move tab -> Follow speed), whatever the browser does.
        """
        if zone is not None or (mark and not any(
                o.get("kind") == "mark" and str(o.get("name") or "").lower() == str(mark).lower()
                for o in (self.venue.get("objects") or []))):
            # a zone by name or kind ("dance floor", "bar"): its middle
            want = str(zone if zone is not None else mark).strip().lower()
            key = want.replace(" ", "")
            zs = [zn for zn in (self.venue.get("zones") or []) if zn.get("points")
                  and (str(zn.get("name") or "").lower().replace(" ", "") == key or zn.get("kind") == key
                       or zn.get("id") == want)]
            if zs:
                pts = zs[0]["points"]
                x = sum(p[0] for p in pts) / len(pts)
                z = sum(p[1] for p in pts) / len(pts)
                y = float(zs[0].get("y") or 0)
                mark = None
            elif zone is not None:
                raise ValueError(f"no zone {zone!r}")
        try:
            g = float(glide or 0)
        except (TypeError, ValueError):
            g = 0.0
        if g > 0 and not mark and x is not None and z is not None:
            return self._aim_glide_start(float(x), float(y or 0.0), float(z), heads, cell, spread, min(g, 10.0))
        self.__dict__["_glide"] = None                  # an instant aim: any glide stops
        r = self._aim_at_now(x, y, z, mark, heads, cell, spread)
        # where these lights were left: a glide later starts from here
        nums = tuple(sorted(int(h) for h in heads)) if heads else tuple(sorted(self.selected))
        self.__dict__.setdefault("_aim_rest", {})[nums] = list(r["target"])
        return r

    def _aim_glide_start(self, tx, ty, tz, heads, cell, spread, tau) -> dict:
        import time as _t
        nums = tuple(sorted(int(h) for h in heads)) if heads else tuple(sorted(h["head_no"] for h in self._require_selection()))
        if not nums:
            raise ValueError("nothing selected")
        old = self.__dict__.get("_glide")
        rest = (self.__dict__.get("_aim_rest") or {}).get(nums)
        if old and old["heads"] == nums:
            cur = old["cur"]                             # still gliding: carry on from there
        elif rest:
            cur = list(rest)                             # from where these lights were left
        else:
            # not aimed from here before: glide each light from where it
            # really points now (its pan / tilt), at the same speed
            self.__dict__["_glide"] = None
            r = self._aim_at_now(tx, ty, tz, None, list(nums), cell, spread)
            self._pt_glide_start(nums, tau)
            self.__dict__.setdefault("_aim_rest", {})[nums] = [tx, ty, tz]
            return {**r, "glide": tau, "target": [tx, ty, tz],
                    "summary": f"lights gliding to x{tx:.1f} z{tz:.1f}"}
        self._glide = {"heads": nums, "goal": [tx, ty, tz], "cur": list(cur), "tau": tau,
                       "cell": cell, "spread": spread, "last": _t.monotonic(), "wrote": None}
        r = self._aim_glide_tick(force=True) or {}
        return {**r, "glide": tau, "target": [tx, ty, tz],
                "summary": f"lights gliding to x{tx:.1f} z{tz:.1f}"}

    def _aim_glide_tick(self, now: float | None = None, force: bool = False) -> dict | None:
        """One frame of the glide (called by the frame builder)."""
        g = self.__dict__.get("_glide")
        if not g:
            return None
        import math as _m
        import time as _t
        now = _t.monotonic() if now is None else now
        # someone else moved these lights (nudge, a palette, undo, clear):
        # the glide lets go rather than fight them
        if g["wrote"] is not None and any(
                {k: v for k, v in (self.programmer.get(n) or {}).items() if k.split("@")[0] in ("pan", "tilt", "pan_fine", "tilt_fine")}
                != g["wrote"].get(n, {}) for n in g["heads"]):
            self._glide = None
            return None
        dt = max(0.0, now - g["last"])
        if dt <= 0 and not force:
            return None
        g["last"] = now
        k = 1.0 - _m.exp(-dt / g["tau"])
        cur, goal = g["cur"], g["goal"]
        for i in range(3):
            cur[i] += (goal[i] - cur[i]) * k
        done = _m.hypot(goal[0] - cur[0], goal[2] - cur[2]) < 0.01
        if done:
            cur[:] = goal
        try:
            r = self._aim_at_now(cur[0], cur[1], cur[2], None, list(g["heads"]), g["cell"], g["spread"])
        except ValueError:
            self._glide = None
            return None
        g["wrote"] = {n: {k2: v for k2, v in (self.programmer.get(n) or {}).items()
                          if k2.split("@")[0] in ("pan", "tilt", "pan_fine", "tilt_fine")} for n in g["heads"]}
        if done:
            self.__dict__.setdefault("_aim_rest", {})[g["heads"]] = list(goal)
            self._glide = None
        return r

    # -- a glide in pan / tilt, light by light ------------------------------
    _PT = ("pan", "tilt")

    def _pt_keys(self, row: dict) -> dict:
        return {k: v for k, v in (row or {}).items() if k.split("@")[0] in self._PT}

    def _pt_now(self, n: int) -> dict:
        """Where light n points now (pan / tilt values), whoever put it there."""
        h = next((x for x in self.patch if x["head_no"] == n), None)
        if h is None:
            return {}
        import time as _t
        now = _t.monotonic()
        got = self._resolve_head(h, self._programmer_now(now).get(n) or {}, self._active_playbacks(now))
        return self._pt_keys(got)

    def _pt_glide_start(self, nums, tau: float, before: dict | None = None) -> None:
        """The programmer already holds where the lights GO: take them back
        to where they are (before, or what they show now) and let the frame
        builder glide each pan / tilt there, `tau` s to most of the way."""
        import time as _t
        gl = self.__dict__.setdefault("_pt_glides", {})
        for n in nums:
            goal = self._pt_keys(self.programmer.get(n))
            if not goal:
                continue
            old = gl.get(n)
            if old:
                start = dict(old["cur"])                 # mid-glide: carry on from there
            elif before is not None and before.get(n):
                start = dict(before[n])
            else:
                # what it shows without this aim: the programmer's pan/tilt
                # taken out for a moment
                keep = self.programmer.get(n) or {}
                self.programmer[n] = {k: v for k, v in keep.items() if k not in goal}
                start = self._pt_now(n)
                self.programmer[n] = keep
            # nothing drives it: the light sits at 0 on the wire
            cur = {k: float(start.get(k, start.get(k.split("@")[0], 0))) for k in goal}
            gl[n] = {"goal": goal, "cur": cur, "tau": float(tau), "last": _t.monotonic(), "wrote": None}
            self._pt_write(n, gl[n])

    def _pt_write(self, n: int, g: dict) -> None:
        row = self.programmer.setdefault(n, {})
        for k, v in g["cur"].items():
            row[k] = int(round(v))
        g["wrote"] = self._pt_keys(row)

    def _pt_glide_tick(self, now: float | None = None) -> None:
        gl = self.__dict__.get("_pt_glides")
        if not gl:
            return
        import math as _m
        import time as _t
        now = _t.monotonic() if now is None else now
        for n, g in list(gl.items()):
            if self._pt_keys(self.programmer.get(n)) != g["wrote"]:
                del gl[n]                                # moved by something else: let go
                continue
            dt = max(0.0, now - g["last"])
            g["last"] = now
            k = 1.0 - _m.exp(-dt / max(0.05, g["tau"]))
            done = True
            for key, goal in g["goal"].items():
                c = g["cur"][key] + (goal - g["cur"][key]) * k
                span = 65535 if goal > 255 or g["cur"][key] > 255 else 255
                if abs(goal - c) > span * 0.002:
                    done = False
                g["cur"][key] = c
            if done:
                g["cur"] = {kk: float(v) for kk, v in g["goal"].items()}
            self._pt_write(n, g)
            if done:
                del gl[n]

    def aim_glide_public(self) -> dict | None:
        g = self.__dict__.get("_glide")
        return {"x": round(g["cur"][0], 2), "z": round(g["cur"][2], 2)} if g else None

    def _aim_at_now(self, x=None, y=None, z=None, mark=None, heads=None, cell=None, spread=None):
        if mark:
            found = next((o for o in (self.venue.get("objects") or [])
                          if o.get("kind") == "mark"
                          and str(o.get("name") or "").lower() == str(mark).lower()), None)
            if not found:
                raise ValueError(f"no mark named {mark!r}")
            x, z = found["x"], found["z"]
            y = float(found.get("y") or 0) + 1.2
        if x is None or z is None:
            raise ValueError("x and z (or a mark) are required")
        tx, ty, tz = float(x), float(y if y is not None else 0.0), float(z)
        rows = ([self._head(int(h)) for h in heads] if heads
                else self._require_selection())
        aimed, skipped = [], []
        clamped = []
        for h in rows:
            solved = self._aim_solve(h, tx, ty, tz, closest=True)
            if solved is None:
                skipped.append(h["head_no"])
                continue
            if len(solved) > 4:
                clamped.append(h["head_no"])
            fp, ft = solved[0], solved[1]
            # each axis at ITS OWN resolution: a mode can have a fine
            # channel for pan and not for tilt (a Wave 360 in 17 ch), and a
            # 16-bit value on an 8-bit tilt pinned it at full tilt
            top_p = 65535 if "pan_fine" in h["map"] else 255
            top_t = 65535 if "tilt_fine" in h["map"] else 255
            # a multi-head light: the heads picked (Heads: 1 2 3 4) follow on
            # their own, or every head to its own spot when fanned out
            per = self._aim_heads(h, tx, ty, tz, near=(solved[2], solved[3]),
                                  spread=float(spread or 0), cells=self._cells(cell))
            if per:
                self._a_set_position(pan=round(max(0, min(1, fp)) * top_p),
                                     unit="logical", head=h["head_no"])
                for role, v in per.items():
                    self._set_programmer(h["head_no"], role, v)
            else:
                self._a_set_position(pan=round(max(0, min(1, fp)) * top_p),
                                     tilt=round(max(0, min(1, ft)) * top_t),
                                     unit="logical", head=h["head_no"])
            aimed.append(h["head_no"])
        if not aimed:
            raise ValueError("none of those lights can pan and tilt"
                             if skipped else "nothing selected")
        return {"heads": aimed, "skipped": skipped, "clamped": clamped, "target": [tx, ty, tz],
                "summary": f"aimed {len(aimed)} light(s) at "
                           f"x{tx:.1f} z{tz:.1f}"
                           + (f" ({len(skipped)} cannot move)" if skipped else "")
                           + (f"; {len(clamped)} can't reach it and point as close as they can "
                              f"(#{', #'.join(map(str, clamped[:6]))} - hang it, or turn it)" if clamped else "")}
