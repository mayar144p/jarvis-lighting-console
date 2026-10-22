"""What the 3D view and the screens are shown: each light's look.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import colorsys
import json
import re
import time

from app import beamlook, config, fixture_kind, fixtures, merge
from app.engine_base import _BEAM_LOOK_ROLES, _COLOUR_ROLES, _FIXTURE_CACHE
from app.engine_support import FX_ROLES, HTP_ROLES, ROLE_HEX, cmy_are_leds, split_16bit
from app.engine_support import curve_pct as _curve_pct
from app.engine_support import logical16 as _logical16


class LooksMixin:
    def remap_heads(self, heads: list[int] | None = None) -> dict:
        """Re-resolve patched heads against the CURRENT fixture library.

        A head's channel map is resolved from its profile, and the profile
        lives in a database the operator can edit.  So without this, fixing
        a channel's role leaves every already-patched head un-d drivable
        until a restart - which reads as the fix having done nothing at
        all, and is the reason a fixture editor bolted onto an existing
        library tends to be abandoned.

        Programmer values for roles that no longer exist are DROPPED, not
        left as orphans: a value for a channel the head does not have can
        never be merged into a frame, so keeping it would mean the channel
        sheet kept showing a "set" that does nothing.
        """
        with self.lock:
            _FIXTURE_CACHE.clear()
            fixtures.invalidate_cache()
            self._range_cache.clear()
            self._source_cache.clear()
            target = ({int(h) for h in heads} if heads
                      else {h["head_no"] for h in self.patch})
            changed, dropped, gained = [], [], []
            for head in self.patch:
                if head["head_no"] not in target:
                    continue
                before = list(head.get("map") or [])
                mapping, mode_name, mapped = self._resolve_map(
                    head.get("manufacturer"), head.get("model"),
                    head.get("mode"), head.get("channels"))
                if mapping == before:
                    continue
                if (self._fixture_db(head.get("manufacturer"), head.get("model")) is None
                        and any(r != "raw" for r in before)):
                    # the profile isn't in this library (any more): keep the
                    # roles the light was patched with - an "unknown" map
                    # left a restored rig that nothing could drive
                    continue
                # A role that vanished from the profile cannot be merged
                # into a frame, so its value is dead weight.
                lost = set(before) - set(mapping)
                row = self.programmer.get(head["head_no"])
                if row:
                    for role in lost:
                        if row.pop(role, None) is not None:
                            dropped.append({"head": head["head_no"],
                                            "role": role})
                    if not row:
                        self.programmer.pop(head["head_no"], None)
                for role in set(mapping) - set(before):
                    if role not in ("raw", "unused"):
                        gained.append({"head": head["head_no"], "role": role})
                head["map"] = mapping
                head["mode"] = mode_name
                head["mapped"] = mapped
                changed.append(head["head_no"])
            if changed:
                self.patch_rev += 1
            still_raw = sorted(h["head_no"] for h in self.patch
                               if "raw" in (h.get("map") or []))
            return {"heads": len(changed), "changed": changed,
                    "gained": gained, "dropped": dropped,
                    "still_raw": still_raw,
                    "summary": (f"re-mapped {len(changed)} head(s); "
                                + (f"{len(gained)} channel(s) gained a control"
                                   if gained else "no new controls")
                                + (f"; {len(dropped)} stale value(s) dropped"
                                   if dropped else "")
                                + (f"; {len(still_raw)} head(s) still have "
                                   f"uncontrollable channels"
                                   if still_raw else ""))}

    def _a_remap_heads(self, heads=None, **_):
        """Action wrapper, so the fix is reachable from the console API and
        the agent - not only from the route that happens to call it."""
        return self.remap_heads(heads)

    # What the 3D draws (the toolbar's source): the output, the programmer
    # on its own, or what the next GO of a playback will look like.  Only
    # the 3D's feed follows it - the DMX, the lamps that follow the desk
    # and everything else always get the output.
    VIS_SOURCES = ("output", "programmer", "next")

    def _vis(self) -> dict:
        return self.__dict__.get("vis_source") or {"source": "output", "playback": None}

    def _a_vis_source(self, source=None, playback=None, **_):
        """What the 3D view shows: `output` (what the lights are doing),
        `programmer` (only what is in the programmer: the playbacks and
        buttons left out) or `next` (what the next GO of `playback` will
        look like; no playback: the one GO was last pressed on).  The DMX
        never changes."""
        src = str(source or "output").strip().lower().replace(" ", "").replace("_", "")
        src = {"programmeronly": "programmer", "prog": "programmer", "nextgo": "next", "preview": "next",
               "live": "output", "stage": "output"}.get(src, src)
        if src not in self.VIS_SOURCES:
            raise ValueError("the 3D shows the output, the programmer only, or the next GO")
        n = None
        if playback not in (None, ""):
            n = self._playback(playback)["n"]           # raises for a playback that isn't there
        self.vis_source = {"source": src, "playback": n}
        self._look_cache = None
        if src == "output":
            said = "the 3D shows the output"
        elif src == "programmer":
            said = "the 3D shows the programmer only (playbacks and buttons left out) - the lights are unchanged"
        else:
            pb = self._next_go_pb()
            said = (f"the 3D shows PB{pb['n']}'s next GO" + (f" (cue {self._next_go_index(pb)[1]['n']})"
                    if pb["stack"] else " (it has no cues)") + " - the lights are unchanged")
        return {"vis_source": dict(self.vis_source), "summary": said}

    def _next_go_pb(self) -> dict:
        """The playback the 3D previews: the one asked for, else the one GO
        was last pressed on, else playback 1."""
        n = self._vis().get("playback")
        if n:
            return self._playback(n)
        used = [pb for pb in self.playbacks if pb.get("order")]
        return max(used, key=lambda pb: pb["order"]) if used else self.playbacks[0]

    @staticmethod
    def _next_go_index(pb: dict) -> tuple[int, dict]:
        """Where GO on `pb` goes (as cue_go does): the top of a released
        stack, the next cue, round to the top of a looping one."""
        stack = pb["stack"]
        if not pb["active"] or pb["index"] < 0:
            i = 0
        elif pb["index"] >= len(stack) - 1:
            i = 0 if (pb.get("follow") or {}).get("loop") else len(stack) - 1
        else:
            i = pb["index"] + 1
        return i, stack[i]

    def _next_go_vals(self, now: float) -> list[tuple[int, dict]]:
        """The playbacks as they will be after GO: the previewed one on
        its next cue (faded in, at its fader - full if the fader is down,
        or the preview would show nothing), the others as they are now."""
        pb = self._next_go_pb()
        active = [p for p in self.playbacks if p["active"] and p["stack"] and p["index"] >= 0 and p is not pb]
        active.sort(key=lambda p: p.get("order", 0), reverse=True)
        out = [(self._pb_level_now(p, now), self._pb_values(p, now)) for p in active]
        if not pb["stack"]:
            return out
        i, _cue = self._next_go_index(pb)
        vals = self._scaled_cue({"values": self._tracked(pb, i)}, None)
        level = int(pb.get("level", 100) or 0) or 100
        return [(level, vals)] + out

    def _fx_programmer_only(self, now: float) -> dict:
        """The effects the programmer runs (not a cue's, not a button's)."""
        owned = {i for run in self.quick_active.values() for i in (run.get("fx_ids") or [])}
        saved = self.fx
        self.fx = [f for f in saved if not f.get("cue_pb") and f.get("id") not in owned]
        try:
            return self._fx_values(now)
        finally:
            alive = {id(f) for f in self.fx}            # an effect that ran out is gone
            mine = {id(f) for f in saved if not f.get("cue_pb") and f.get("id") not in owned}
            self.fx = [f for f in saved if id(f) not in mine or id(f) in alive]

    def _looks(self, now: float | None = None, source: dict | None = None) -> list[dict]:
        """[{n, hex, a, on, pan?, tilt?}] per head - the display look.

        `source` (the 3D's feed only): {"source": "programmer"} leaves the
        playbacks and buttons out, {"source": "next"} shows a playback's
        next GO; the output (None) is what everything else is shown.

        Built straight from _resolve_head, so it is exactly what
        build_frames will put on the wire (same HTP/LTP merge, same
        blackout and master) - there is no second, drifting model.

        `pan` / `tilt` are the head's ACTUAL aim, 0..1, and are present
        only when the fixture has that channel and something is driving
        it.  Without them the visualiser has to invent an aim from the
        landing point, so a moving head whose pan/tilt the operator moved
        would sit still while its beam pointed somewhere else - the head
        looks broken even though the DMX is correct.  Absent means "not
        driven", and the visualiser keeps its geometric default.
        """
        patch = self._frame_patch()            # the floor lock, as on the wire
        mode = (source or {}).get("source") or "output"
        kept = self._kept_values(patch) if now is None and mode == "output" else None
        if kept is not None:
            values_of, overrides = kept
        else:
            now = time.monotonic() if now is None else now
            prog = self._programmer_now(now)
            if mode == "programmer":
                pb_vals, fx_vals, overrides = [], self._fx_programmer_only(now), {}
            else:
                pb_vals = self._next_go_vals(now) if mode == "next" else self._active_playbacks(now)
                fx_vals = self._fx_values(now)
                overrides = self._override_vals()
        out = []
        for head in patch:
            over = overrides.get(head["head_no"])
            if kept is not None:
                values = values_of[head["head_no"]]
            else:
                values = self._resolve_head(head, prog, pb_vals,
                                            fx_vals.get(head["head_no"]), over)
            intensity = None
            for role in HTP_ROLES:
                if role in values:
                    v = values[role]
                    intensity = v if intensity is None else max(intensity, v)
            if intensity is None:
                # Dimmer-less fixture (moving head in a short mode, a
                # 3-channel RGB par, ...): the lamp is either passing
                # light or it is not, so report the gate state, not 0.
                intensity = self._gate_intensity(head, values)
            elif intensity and self._gate_closed(head, values):
                # a dimmer up behind a shutter its file calls Closed: dark
                # on the real light, so dark here (#105)
                intensity = 0
            if self.blackout and not self._lamp_only(head):
                # BLACKOUT: the wire carries the darkest this light can be
                # sent; the 3D shows it dark (a lamp nothing can close
                # still shows lit - the real one is too)
                intensity = 0
            pct = _curve_pct(intensity or 0, head.get("curve", "linear"))
            row = {"n": head["head_no"],
                   "a": round(max(0, min(100, pct)) / 100.0, 3),
                   "hex": self._hex_for(head, values),
                   "on": pct > 0}
            pan, tilt = self._unorient(head, self._aim01(head, values, "pan"), self._aim01(head, values, "tilt"))
            if pan is not None:
                row["pan"] = pan
            if tilt is not None:
                row["tilt"] = tilt
            reps, n_cells, span, g2, (a_in, a_rot) = self._look_static(head)
            if n_cells > 1:
                # each head of a multi-head light: its own colour, tilt and
                # pan (a light's second zoom or strobe is an Aux channel, and
                # its dimmer one value for the whole light, as on the wire)
                cells = []
                for k in range(1, n_cells + 1):
                    cv = {r: values.get(f"{r}@{k}", values.get(r, 0)) for r in ("red", "green", "blue", "white")}
                    cell = {"hex": self._hex_for(head, cv) if any(r in values or f"{r}@{k}" in values
                                                                  for r in ("red", "green", "blue", "white")) else row["hex"]}
                    aim = {}
                    for base in ("tilt", "pan"):
                        t = values.get(f"{base}@{k}", values.get(base))
                        if t is not None and base in head["map"] and (base == "tilt" or reps.get(base, 0) > 1):
                            # the same scaling, limits and inversion as the head's
                            # own aim (a value can be 16-bit / logical, not 0-255)
                            aim[base] = self._aim01(head, {**values, base: t}, base)
                    cp, ct = self._unorient(head, aim.get("pan"), aim.get("tilt"))
                    if ct is not None:
                        cell["tilt"] = ct
                    if cp is not None and "pan" in aim:
                        cell["pan"] = cp
                    cells.append(cell)
                row["cells"] = cells
            # THE FIXTURE'S OWN TRAVEL, so the beam is drawn where the head
            # is actually pointing.  The visualiser used a hardcoded 270
            # degrees of tilt, which is a 540-degree-pan/270-degree-tilt
            # convention from the manual.  A Chauvet Intimidator's file
            # says -117..+117, so the same desk value drew the beam 12
            # degrees short at each end - and nothing on screen could tell
            # you, because the drawing looked perfectly plausible.
            #
            # Only sent when the profile actually declares a range, so a
            # generic with no GDTF keeps the historical behaviour and
            # gains no bytes.
            off = head.get("align")
            if off and (off.get("pan") or off.get("tilt")):
                # lined up with the real light: its travel shifted to where
                # its zero really is, so the 3D points where the real one does
                pl, ph, tl, th = self._aim_span(head)
                span = dict(span or {})
                span["pan"] = [pl + float(off.get("pan") or 0), ph + float(off.get("pan") or 0)]
                span["tilt"] = [tl + float(off.get("tilt") or 0), th + float(off.get("tilt") or 0)]
            if span:
                row["deg"] = span
            # Beam shaping for the visualiser, 0..1 per driven channel, so
            # zoom, iris, frost, gobo, prism and strobe change the beam on
            # screen exactly as they will on stage.
            beam = {r: round(max(0, min(255, int(values[r]))) / 255.0, 3)
                    for r in _BEAM_LOOK_ROLES if r in values
                    and r not in ("strobe", "shutter")}
            # the second gobo wheel, and an animation wheel in the beam
            if g2 and g2 in values:
                beam["gobo2"] = round(max(0, min(255, int(values[g2]))) / 255.0, 3)
            if a_in and int(values.get(a_in, 0) or 0) > 8:
                beam["anim"] = round(int(values[a_in]) / 255.0, 3)
                if a_rot:
                    beam["anim_rot"] = round(int(values.get(a_rot, 0) or 0) / 255.0, 3)
            if beam:
                row["beam"] = beam
            # what the beam is DOING, in the file's own words for each
            # channel's range: an 8-facet prism, gobo 3 shaking, half red
            # half blue, a random strobe (app/beamlook.py)
            look = beamlook.describe(self.head_ranges(head), values, self._shutter_role(head))
            if look:
                row["look"] = look
            own = self._own_program(head, values)
            if own:
                row.update(own)
            fxl = self._fx_look(head, values)
            if fxl:
                row["fx"] = fxl
                if fxl.get("laser"):
                    row["a"], row["on"] = 1.0, True
                    row["hex"] = fxl.get("hex", "#22ff44")
            # How fast it REALLY strobes (0 = steady): a raw strobe byte
            # is not a speed - 4 or 20 is "open" on many lights.
            hz = self._strobe_hz(head, values, over) if row["a"] > 0 else 0.0
            if hz:
                row["hz"] = hz
            # How it moves: measured full-travel times for this model (if
            # calibrated) and the speed channel, so the visualiser's head
            # turns at the real light's pace instead of snapping.
            if "pan" in head["map"] or "tilt" in head["map"]:
                m = self._motion_of(head)
                mv = {"s": self._speed_frac(head, values)}
                if m.get("pan_s"):
                    mv["p"] = m["pan_s"]
                if m.get("tilt_s"):
                    mv["t"] = m["tilt_s"]
                row["mv"] = mv
            out.append(row)
        return out

    def _look_static(self, head: dict) -> tuple:
        """What a light's look rows need that only the patch or its fixture
        file can change: its repeated channels and head count, its pan /
        tilt travel in degrees, the second gobo wheel and the animation
        wheel.  Worked out once, not 40 times a second for every light."""
        ranges = self.head_ranges(head)
        cache = self.__dict__.get("_look_static_cache")
        if cache is None or cache[0] != self.patch_rev or len(cache[1]) > 4096:
            # (the floor lock hands out copies of the heads: bounded)
            cache = self._look_static_cache = (self.patch_rev, {})
        hit = cache[1].get(id(head))
        if hit is not None and hit[0] is head and hit[1] is ranges:
            return hit[2]
        reps = merge._repeated(head["map"])
        n_cells = max(reps.get(r, 0) for r in ("tilt", "pan", "red", "green", "blue", "white")) if reps else 0
        span = {}
        for role, key in (("pan", "pan"), ("tilt", "tilt")):
            r = ranges.get(key)
            if r and r.get("min") is not None and r.get("max") is not None \
                    and r.get("unit") == "degree":
                span[role] = [r["min"], r["max"]]
        out = (reps, n_cells, span, self._gobo2_role(head), self._anim_roles(head))
        cache[1][id(head)] = (head, ranges, out)
        return out

    def _kept_values(self, patch: list) -> tuple[dict, dict] | None:
        """This tick's resolved heads from build_frames, or None when they
        are stale: older than two DMX periods (the output stopped), or
        anything changed since (an action, the patch, the master,
        blackout, blind)."""
        tick = self._tick_vals
        if tick is None or tick[1] != self._tick_key(patch) or tick[1][-1]:
            return None
        if not 0 <= time.monotonic() - tick[0] <= 2.0 / float(config.DMX_HZ):
            return None
        if len(tick[2]) != len(patch):
            return None
        return tick[2], tick[3]

    _LASER_HEX = (("red", "#ff2020"), ("green", "#22ff44"), ("blue", "#2244ff"),
                  ("yellow", "#ffee22"), ("cyan", "#22ffee"), ("magenta", "#ff22dd"),
                  ("purple", "#aa33ff"), ("pink", "#ff66cc"), ("white", "#ffffff"),
                  ("orange", "#ff8a1a"))

    def _fx_look(self, head: dict, values: dict) -> dict | None:
        """What an effect is doing, for the 3D view: firing, fog level,
        laser on with its pattern, rotation, size and colour."""
        m = head["map"]
        if not FX_ROLES.intersection(m):
            return None
        out: dict = {}
        switch = self._laser_switch(head)

        def off(role):
            return int((self.head_ranges(head).get(role) or {}).get("off_value") or 0)
        if "fx_fire" in m and int(values.get("fx_fire", off("fx_fire"))) != off("fx_fire"):
            out["fire"] = True
        if "fog" in m:
            o = off("fog")
            v = int(values.get("fog", o))
            if v > o:
                out["fog"] = round((v - o) / max(1, 255 - o), 2)
        beams = sorted((r for r in m if r.startswith("laser_beam")), key=lambda r: int(r[10:]))
        beam_on = [1 if int(values.get(r, 0) or 0) > off(r) else 0 for r in beams]
        if ("laser_on" in m and int(values.get("laser_on", off("laser_on"))) != off("laser_on")) \
                or any(beam_on) \
                or (switch and all(not (lo <= int(values.get(r, off_v)) <= hi)
                                   for r, (off_v, lo, hi, _on) in switch.items())):
            out["laser"] = True
            if beams:
                out["beams"] = beam_on
            for role, key in (("laser_pattern", "pattern"), ("laser_rot", "rot"),
                              ("laser_size", "size"), ("laser_speed", "speed"),
                              ("laser_x", "x"), ("laser_y", "y")):
                if role in values:
                    out[key] = round(max(0, min(255, int(values[role]))) / 255, 3)
            hexc = None
            if "laser_colour" in values:
                v = int(values["laser_colour"])
                for s in (self.head_ranges(head).get("laser_colour") or {}).get("slots") or []:
                    if s["from"] <= v <= s["to"]:
                        name = s["name"].lower()
                        hexc = next((hx for word, hx in self._LASER_HEX if word in name), None)
                        break
            if hexc is None and any(r in values for r in ("red", "green", "blue")):
                hexc = "#%02x%02x%02x" % tuple(max(0, min(255, int(values.get(c, 0))))
                                               for c in ("red", "green", "blue"))
            if hexc is None:
                # a single-colour laser says so in its name: BeamBar 10B / 10G / 10R
                mm = re.search(r"\d\s*(rgb|r|g|b)\b|\b(red|green|blue)\b", str(head.get("model") or "").lower())
                word = (mm.group(1) or mm.group(2)) if mm else ""
                hexc = {"r": "#ff2020", "red": "#ff2020", "g": "#22ff44", "green": "#22ff44",
                        "b": "#3355ff", "blue": "#3355ff"}.get(word)
            out["hex"] = hexc if hexc and hexc != "#000000" else "#22ff44"
        return out or None

    @staticmethod
    def _aim01(head: dict, values: dict, base: str) -> float | None:
        """A pan/tilt channel as 0..1, or None when the head has no such
        channel or nothing is driving it.

        16-bit heads (pan + pan_fine) carry ONE logical value across two
        DMX bytes, so they are normalised against 65535 and the fine
        channel's explicit operator value wins - exactly what build_frames
        writes.  8-bit heads are normalised against 255.  Getting this
        wrong is what makes a head look like it is aiming at the ceiling
        when the desk says it is at centre.
        """
        if base not in values and base + "_fine" not in values:
            return None
        fine_role = base + "_fine"
        is16 = fine_role in (head.get("map") or [])
        if is16:
            coarse, fine = split_16bit(_logical16(values.get(base, 0)))
            if fine_role in values:
                fine = max(0, min(255, int(values[fine_role])))
            logical = (coarse << 8) | fine
            return round(logical / 65535.0, 4)
        v = values.get(base, 0)
        return round(max(0, min(255, int(v))) / 255.0, 4)

    @staticmethod
    def _unorient(head: dict, pan, tilt):
        """The 3D draws a light as PROGRAMMED (its model is the desk's idea
        of the light): an inverted or swapped light's wire value, flipped on
        the way out (merge.py: swap, then invert), flipped back - so the 3D
        shows where the real, differently-hung light really points."""
        flags = head.get("orient") or {}
        if flags.get("invert_pan") and pan is not None:
            pan = round(1 - pan, 4)
        if flags.get("invert_tilt") and tilt is not None:
            tilt = round(1 - tilt, 4)
        if flags.get("swap"):
            pan, tilt = tilt, pan
        return pan, tilt

    def look_feed(self, since: int | None = None) -> dict:
        """Per-tick look feed for the visualiser (see §13 fades).

        The lite feed only carries heads when the patch revision changes,
        which is right for structure but wrong for light: a 4-second cue
        fade must animate, not step ten times.  This endpoint is the light
        feed - only heads that are actually emitting, so a 200-head rig
        costs a few hundred bytes per tick - and it carries a monotonic
        sequence number the client uses to interpolate between ticks.

        `since` is a hint only: an unknown or too-old sequence returns a
        full snapshot, which is always correct, never a gap.
        """
        with self.lock:
            looks = self._looks(source=self._vis())
            self._look_seq += 1
            seq = self._look_seq
            lit = [row for row in looks if row["a"] > 0]
            return {"seq": seq, "hz": self.output.get("hz", config.DMX_HZ),
                    "full": since is None or since != seq - 1,
                    "heads": lit, "count": len(looks),
                    "lit": len(lit)}

    def look_rows(self) -> list[dict]:
        """The lit heads' looks, and dark heads whose aim is driven (a head
        moves in the dark too), for the live stream (no sequence bump)."""
        with self.lock:
            return [row for row in self._looks(source=self._vis())
                    if row["a"] > 0 or "pan" in row or "tilt" in row or "fx" in row
                    or "prog" in row or "spin" in row]

    # every screen's live stream asks 30 times a second.  While the output
    # runs, its thread makes the answer once per DMX tick, just after the
    # frame went out; otherwise the first screen to ask makes one for all
    # the screens asking within this long
    LOOK_SHARE_S = 0.05

    def look_text(self) -> str:
        """look_rows as JSON, one answer shared by every screen."""
        self._look_wanted = time.monotonic()
        # shared only while nothing was changed: an operator's edit is seen
        # on the next ask, whatever the clock (Windows' ticks every 16 ms)
        rev = self.act_rev
        cached = self._look_cache
        now = time.monotonic()
        if cached and now - cached[0] < self.LOOK_SHARE_S and cached[2] == rev:
            return cached[1]
        with self._look_cache_lock:
            cached = self._look_cache
            if cached and time.monotonic() - cached[0] < self.LOOK_SHARE_S and cached[2] == rev:
                return cached[1]
            text = json.dumps(self.look_rows(), separators=(",", ":"))
            self._look_cache = (time.monotonic(), text, rev)
            return text

    def _look(self, now: float | None = None) -> list[dict]:
        """Lite-feed look rows: {n, look:{hex, a, on}} (patch-revisioned)."""
        return [{"n": row["n"],
                 "look": {"hex": row["hex"], "a": row["a"],
                          "on": row["on"]}}
                for row in self._looks(now)]

    @staticmethod
    def _lamp_only(head: dict) -> bool:
        """A light whose lamp can't be dimmed or closed from DMX at all (an
        effects machine is never one: a moving fogger has pan and tilt and
        no dimmer, and it was drawn lit through Blackout)."""
        m = set(head.get("map") or [])
        if fixture_kind.head_class(head) != "light":
            return False
        # CMY flags on a lamp are filters, not LEDs: they colour the light
        # but can't close it (a CMY fader with nothing else is lit at rest)
        filters = (m & {"cyan", "magenta", "yellow"}) - LooksMixin._emitters(head)
        return not m & (HTP_ROLES | {"shutter", "strobe"} | LooksMixin._emitters(head)) and bool(
            m & {"pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "zoom", "focus"} or filters)

    def _gate_intensity(self, head: dict, values: dict) -> int:
        """Brightness of a fixture that has no dimmer channel (0 or 100).

        Its shutter/strobe channel gates the lamp, so light passes when
        the resolved value is at or above the fixture's open value; with
        no colour channels to look at, "on" then means "open".  Mirrors
        build_frames exactly: an un-driven gate is written 0, so an idle
        head reads dark here too.
        """
        role = self._shutter_role(head)
        if role is None:
            # No dimmer and no gate (e.g. a raw 3ch RGB par): the colour
            # channels ARE the brightness.
            if not self._emitters(head):
                # a lamp light with nothing that can dim or close it (an old
                # scanner: pan, tilt, colour wheel, gobo): the lamp is simply
                # on, whatever the desk does - show it that way
                return 100 if self._lamp_only(head) and not self._wheel_dark(head, values) else 0
            colour = [v for r, v in values.items()
                      if r in _COLOUR_ROLES]
            return round(max(colour) * 100 / 255) if colour else 0
        if values.get(role, 0) < self._open_value(head, role) or self._wheel_dark(head, values):
            return 0
        # open - and on a light with colour LEDs (an RGB PAR with a strobe
        # channel) the colours are its brightness: all at 0 is dark, not
        # "open, so full".  CMY flags on a lamp are filters, not LEDs.
        leds = self._emitters(head)
        if leds:
            colour = [v for r, v in values.items() if r in leds]
            return round(max(colour) * 100 / 255) if colour else 0
        return 100

    _CLOSED = re.compile(r"^\s*(shutter\s+)?(closed?|black ?out)\b", re.I)

    def _gate_closed(self, head: dict, values: dict) -> bool:
        """Is this head's shutter/strobe channel, as driven, in a range its
        file names Closed / Blackout?  Un-driven is not closed."""
        role = self._shutter_role(head)
        if role is None or role not in values:
            return False
        v = int(values[role])
        for cap in (self.head_ranges(head).get(role) or {}).get("caps") or []:
            try:
                lo, hi, text = int(cap[0]), int(cap[1]), str(cap[2])
            except (TypeError, ValueError, IndexError):
                continue
            if lo <= v <= hi:
                return bool(self._CLOSED.match(text))
        return False

    @staticmethod
    def _emitters(head: dict) -> set:
        """The light's colour LEDs (CMY only when beside RGB: then LEDs too)."""
        roles = set(head.get("map") or [])
        leds = roles & _COLOUR_ROLES
        return leds if cmy_are_leds(roles) else leds - {"cyan", "magenta", "yellow"}

    # "Off" is not here: on a colour-macro channel it means "macro off, the
    # RGB channels rule", not dark
    _DARK_SLOT = re.compile(r"^\s*(black ?out|dark|no light)\b", re.I)

    # A colour-macro channel on a light with no dimmer and no colour LEDs
    # (a derby's "Color macro", a 1-channel LED's "Programs") whose 0 says
    # nothing: "No function" is dark on an LED light, open white on a lamp's
    # wheel, and the file can't tell which.  The operator answers once, on
    # the real light ("Test this light"), and it is kept for the model.
    _COLOUR_WORD = re.compile(r"^\s*(red|green|blue|white|amber|orange|yellow|cyan|magenta|"
                              r"purple|pink|uv|violet|lime|warm|cold|cool)\b", re.I)
    _SAYS_NOTHING = re.compile(r"^\s*(no ?function|none|nothing|no colou?r|not used)?\s*\.?\s*$", re.I)

    def _macro_role(self, head: dict) -> str | None:
        """The channel that picks the colour on a light that has nothing
        else to light or darken it (no dimmer, no colour LEDs)."""
        if self._intensity_roles(head) or self._emitters(head):
            return None
        ranges = self.head_ranges(head)
        for role in head.get("map") or []:
            if role != "wheel" and not role.startswith("aux"):
                continue
            caps = (ranges.get(role) or {}).get("caps") or []
            if sum(1 for c in caps if self._COLOUR_WORD.match(str(c[2]))) >= 3:
                return role
        return None

    def zero_question(self, head: dict) -> dict | None:
        """{role, name, to, lit, dark}: the light's colour-macro channel at
        0 says nothing, so whether it is dark there is asked once (dark:
        the answer, None until then).  None: nothing to ask."""
        role = self._macro_role(head)
        if role is None:
            return None
        caps = (self.head_ranges(head).get(role) or {}).get("caps") or []
        zero = next((c for c in caps if int(c[0]) <= 0 <= int(c[1])), None)
        name = str(zero[2]) if zero else ""
        if not self._SAYS_NOTHING.match(name):
            return None                        # "Blackout", "White", "Open"...: the file says
        white = next((c for c in caps if re.search(r"\bwhite\b", str(c[2]), re.I)
                      and not re.search(r"\+|and", str(c[2]), re.I)), None)
        first = white or next((c for c in caps if self._COLOUR_WORD.match(str(c[2]))), None)
        if first is None:
            return None
        ans = (self.head_ranges(head).get("_model") or {}).get("zero_dark")
        return {"role": role, "name": name or "nothing", "to": int(zero[1]) if zero else 0,
                "lit": (int(first[0]) + int(first[1])) // 2,
                "lit_name": str(first[2]), "dark": None if ans is None else bool(ans)}

    def _zero_dark(self, head: dict, values: dict) -> bool:
        """The operator said this light is dark with its colour channel at
        0, and it is there now."""
        q = self.zero_question(head)
        return bool(q and q["dark"] and int(values.get(q["role"], 0)) <= q["to"])

    def _wheel_dark(self, head: dict, values: dict) -> bool:
        """The colour wheel sits on a slot the file calls "Blackout" (a
        Swarm's colour channel at 0): the light is dark, whatever else."""
        if self._zero_dark(head, values):
            return True
        roles = set(head.get("map") or [])
        if "wheel" not in roles or self._emitters(head):
            return False                       # colour LEDs decide on their own
        v = int(values.get("wheel", 0))
        try:
            slots = self._wheel_slots(head, "wheel")
        except Exception:                      # noqa: BLE001 - a bare engine
            return False
        slot = next((x for x in slots if x.get("from", 0) <= v <= x.get("to", 255)), None)
        return bool(slot and self._DARK_SLOT.match(str(slot.get("name") or "")))

    _PROG_NAME = re.compile(r"program|auto|macro|show|chase|sound|music|effect|pattern|run|mode", re.I)
    _PROG_OFF = re.compile(r"^\s*(off|no ?function|none|normal|dmx|manual|disabled?|blackout|open|nothing)\b", re.I)
    _SPIN = re.compile(r"(pan|tilt).*(continuous|rotation|endless|spin)|(continuous|endless).*(pan|tilt)", re.I)

    def _own_program(self, head: dict, values: dict) -> dict:
        """The light running something of its own: a built-in program /
        auto show ("prog": its name - the 3D plays a stand-in and labels
        it), or endless pan / tilt rotation ("spin")."""
        out: dict = {}
        ranges = self.head_ranges(head)
        for role in head["map"]:
            if not role.startswith("aux") or role not in values:
                continue
            rng = ranges.get(role) or {}
            name = str(rng.get("name") or "")
            v = int(values[role])
            slot = next((x for x in rng.get("slots") or [] if x.get("from", 0) <= v <= x.get("to", 255)), None)
            text = str(slot.get("name") if slot else "")
            if self._SPIN.search(name):
                if slot and not self._PROG_OFF.match(text) and re.search(r"clockwise|cw|rotat|left|right", text, re.I):
                    axis = "pan" if "pan" in name.lower() else "tilt"
                    sign = -1 if re.search(r"counter|ccw|anti|left", text, re.I) else 1
                    span = max(1, int(slot.get("to", 255)) - int(slot.get("from", 0)))
                    pos = (v - int(slot.get("from", 0))) / span
                    fast_first = re.search(r"fast\s*-?>\s*slow|fast.*slow", text, re.I) is not None
                    speed = round(0.15 + 0.85 * ((1 - pos) if fast_first else pos), 2)
                    out.setdefault("spin", {})[axis] = sign * speed
                continue
            if self._PROG_NAME.search(name) and slot and v > 0 and not self._PROG_OFF.match(text) \
                    and not re.search(r"speed|fade|sensitiv|reset", name, re.I):
                out["prog"] = text[:40] or name[:40]
        return out

    # what each extra emitter adds to the colour on screen
    _EMIT_RGB = {"white": (255, 255, 255), "amber": (255, 176, 32), "uv": (110, 40, 255),
                 "lime": (168, 255, 60), "warm_white": (255, 214, 160), "cool_white": (225, 235, 255)}

    def _hex_roles(self, head: dict) -> tuple:
        """What _hex_for needs to know about a light's channel list, worked
        out once per list: (roles, lime is its green, has RGB, CMY LEDs,
        the extra emitters it has, has CMY)."""
        m = head["map"]
        cache = self.__dict__.setdefault("_hex_roles_cache", {})
        hit = cache.get(id(m))
        if hit is not None and hit[0] is m:
            return hit[1]
        if len(cache) > 4096:
            cache.clear()
        roles = set(m)
        # red + lime + blue (an ETC Source Four LED): the lime is its green
        lime_green = "lime" in roles and "green" not in roles and bool(roles & {"red", "blue"})
        cmy = bool(roles & {"cyan", "magenta", "yellow"})
        out = (roles, lime_green, bool(roles & {"red", "green", "blue"}),
               cmy_are_leds(roles) and cmy,
               tuple((role, rgb) for role, rgb in self._EMIT_RGB.items()
                     if role in roles and not (role == "lime" and lime_green)),
               cmy)
        cache[id(m)] = (m, out)
        return out

    def _hex_for(self, head: dict, values: dict) -> str:
        """The colour the 3D shows: the emitters mixed (RGB, white, amber,
        UV, lime), CMY, or the colour wheel's slot (its colour from the
        fixture file, or a name it was taught)."""
        roles, lime_green, rgb_roles, leds, emits, cmy_roles = self._hex_roles(head)
        r = g = b = 0.0
        lit = False
        if rgb_roles and any(k in values for k in ("red", "green", "blue")):
            r, g, b = (float(values.get(k, 0)) for k in ("red", "green", "blue"))
            if lime_green:
                g = float(values.get("lime", 0))
            lit = True
        if leds and any(values.get(k) for k in ("cyan", "magenta", "yellow")):
            # cyan / magenta / yellow LEDs beside RGB: the light is built so
            # that all of them at full make white, so the mix is balanced
            # against that (or a white Locate would look cyan)
            hue = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
                   "cyan": (0, 255, 255), "magenta": (255, 0, 255), "yellow": (255, 255, 0)}
            mix, full = [0.0] * 3, [0.0] * 3
            for role, rgb in hue.items():
                if role in roles:
                    k = float(values.get(role, 0)) / 255.0
                    for i in range(3):
                        mix[i] += rgb[i] * k
                        full[i] += rgb[i]
            top = max(mix)
            bal = [mix[i] / full[i] * 255.0 if full[i] else 0.0 for i in range(3)]
            peak = max(bal) or 1.0
            r, g, b = (c * min(255.0, top) / peak for c in bal)
            lit = True
        for role, rgb in emits:
            v = values.get(role)
            if v:
                k = float(v) / 255.0
                r, g, b = r + rgb[0] * k, g + rgb[1] * k, b + rgb[2] * k
                lit = True
        if not lit and cmy_roles and (
                any(k in values for k in ("cyan", "magenta", "yellow")) or not self._emitters(head)):
            # CMY filters (on a lamp: no colour LEDs) that nothing drives are
            # out of the beam - open white, not "colour unknown"
            r, g, b = (255.0 - values.get(k, 0) for k in ("cyan", "magenta", "yellow"))
            lit = True
        if lit and (r or g or b):
            top = max(r, g, b)
            if top > 255:
                r, g, b = (c * 255.0 / top for c in (r, g, b))
            return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(255.0, c)))) for c in (r, g, b))
        # a colour wheel (or a colour-macro channel): the slot it is on
        if "wheel" in roles and "wheel" in values:
            try:
                slots = self._wheel_slots(head, "wheel")
            except Exception:                  # noqa: BLE001 - a bare engine
                slots = []
            v = int(values["wheel"])
            slot = next((x for x in slots if x.get("from", 0) <= v <= x.get("to", 255)), None)
            if slot and slot.get("hex") and slot["hex"].lower() != "#000000":
                return slot["hex"]
            if slot and slot.get("name"):
                from app.showdesign import _to_hex
                hx = _to_hex(str(slot["name"]).split(" ")[0].lower()) or _to_hex(str(slot["name"]).lower())
                if hx:
                    return hx
                # "Color 7": a numbered colour the file doesn't describe (the
                # Intimidator Wave 360's macros).  The 3D shows a stand-in
                # hue per slot, so a colour chase is seen to step; teaching
                # the slots (Colour tab) puts the real colours in.
                if re.match(r"^\s*(colou?r|macro)\s*\d+\s*$", str(slot["name"]), re.I) and len(slots) > 1:
                    i = slots.index(slot)
                    r_, g_, b_ = colorsys.hsv_to_rgb(i / len(slots), 1.0, 1.0)
                    return "#%02x%02x%02x" % (int(r_ * 255), int(g_ * 255), int(b_ * 255))
        if lit:
            return "#000000"
        # hue + saturation channels (an ETC Source Four LED's HSI mode): the
        # colour they make; saturation not driven = white
        hs = self._hue_sat(head)
        if hs:
            hue = float(values.get(hs["hue"], 0)) / 255.0
            sat = float(values.get(hs["sat"], 0)) / 255.0 if hs.get("sat") else 1.0
            r_, g_, b_ = colorsys.hsv_to_rgb(hue % 1.0, max(0.0, min(1.0, sat)), 1.0)
            return "#%02x%02x%02x" % (int(round(r_ * 255)), int(round(g_ * 255)), int(round(b_ * 255)))
        # a white-only light with a colour temperature channel: warm to cool white
        cct = self._cct_role(head)
        if cct:
            k = float(values.get(cct, 128)) / 255.0
            warm, cool = (255, 196, 137), (220, 232, 255)
            return "#%02x%02x%02x" % tuple(int(round(warm[i] + (cool[i] - warm[i]) * k)) for i in range(3))
        return ROLE_HEX.get(head.get("role") or "generic",
                            ROLE_HEX["generic"])

    _HUE = re.compile(r"^\s*(colou?r\s*)?hue\b", re.I)
    _SAT = re.compile(r"\bsat(uration)?\b", re.I)
    _CCT = re.compile(r"colou?r\s*(point|temp)|\bcct\b|kelvin|white\s*point", re.I)

    def _hue_sat(self, head: dict) -> dict | None:
        """{hue: role, sat: role|None} for a light that mixes its colour by
        hue and saturation channels (named so in its file), else None."""
        if set(head.get("map") or []) & {"red", "green", "blue", "cyan", "magenta", "yellow"}:
            return None
        try:
            ranges = self.head_ranges(head)
        except Exception:                      # noqa: BLE001 - a bare engine
            return None
        names = {r: str((ranges.get(r) or {}).get("name") or "") for r in head.get("map") or [] if r.startswith("aux")}
        hue = next((r for r, n in names.items() if self._HUE.search(n) and "fine" not in n.lower()), None)
        if not hue:
            return None
        sat = next((r for r, n in names.items() if self._SAT.search(n) and "fine" not in n.lower()), None)
        return {"hue": hue, "sat": sat}

    def _cct_role(self, head: dict) -> str | None:
        """The colour-temperature channel of a white-only light, else None."""
        if set(head.get("map") or []) & {"red", "green", "blue", "cyan", "magenta", "yellow", "wheel"}:
            return None
        try:
            ranges = self.head_ranges(head)
        except Exception:                      # noqa: BLE001
            return None
        return next((r for r in head.get("map") or [] if r.startswith("aux")
                     and self._CCT.search(str((ranges.get(r) or {}).get("name") or ""))), None)
