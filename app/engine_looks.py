"""What the 3D view and the screens are shown: each light's look.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import re
import time

from app import config, fixtures, merge
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

    def _looks(self, now: float | None = None) -> list[dict]:
        """[{n, hex, a, on, pan?, tilt?}] per head - the display look.

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
        now = time.monotonic() if now is None else now
        prog = self._programmer_now(now)
        pb_vals = self._active_playbacks(now)
        fx_vals = self._fx_values(now)
        overrides = self._override_vals()
        out = []
        for head in self._frame_patch():       # the floor lock, as on the wire
            over = overrides.get(head["head_no"])
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
            pan = self._aim01(head, values, "pan")
            tilt = self._aim01(head, values, "tilt")
            if pan is not None:
                row["pan"] = pan
            if tilt is not None:
                row["tilt"] = tilt
            reps = merge._repeated(head["map"])
            if reps.get("tilt", 0) > 1 or reps.get("red", 0) > 1:
                # each head of a multi-head light: its own colour and tilt
                n_cells = max(reps.get("tilt", 0), reps.get("red", 0))
                cells = []
                for k in range(1, n_cells + 1):
                    cv = {r: values.get(f"{r}@{k}", values.get(r, 0)) for r in ("red", "green", "blue", "white")}
                    cell = {"hex": self._hex_for(head, cv) if any(r in values or f"{r}@{k}" in values
                                                                  for r in ("red", "green", "blue", "white")) else row["hex"]}
                    t = values.get(f"tilt@{k}", values.get("tilt"))
                    if t is not None and "tilt" in head["map"]:
                        # the same scaling, limits and inversion as the head's
                        # own tilt (a value can be 16-bit / logical, not 0-255)
                        ct = self._aim01(head, {**values, "tilt": t}, "tilt")
                        if ct is not None:
                            cell["tilt"] = ct
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
            ranges = self.head_ranges(head)
            span = {}
            for role, key in (("pan", "pan"), ("tilt", "tilt")):
                r = ranges.get(key)
                if r and r.get("min") is not None and r.get("max") is not None \
                        and r.get("unit") == "degree":
                    span[role] = [r["min"], r["max"]]
            if span:
                row["deg"] = span
            # Beam shaping for the visualiser, 0..1 per driven channel, so
            # zoom, iris, frost, gobo, prism and strobe change the beam on
            # screen exactly as they will on stage.
            beam = {r: round(max(0, min(255, int(values[r]))) / 255.0, 3)
                    for r in _BEAM_LOOK_ROLES if r in values
                    and r not in ("strobe", "shutter")}
            if beam:
                row["beam"] = beam
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
            looks = self._looks()
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
            return [row for row in self._looks()
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
        """A light whose lamp can't be dimmed or closed from DMX at all."""
        m = set(head.get("map") or [])
        return not m & (HTP_ROLES | {"shutter", "strobe"} | _COLOUR_ROLES) and bool(
            m & {"pan", "tilt", "wheel", "gobo", "gobo_rot", "prism", "zoom", "focus"})

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
            if not set(head["map"]) & _COLOUR_ROLES:
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

    @staticmethod
    def _emitters(head: dict) -> set:
        """The light's colour LEDs (CMY only when beside RGB: then LEDs too)."""
        roles = set(head.get("map") or [])
        leds = roles & _COLOUR_ROLES
        return leds if cmy_are_leds(roles) else leds - {"cyan", "magenta", "yellow"}

    # "Off" is not here: on a colour-macro channel it means "macro off, the
    # RGB channels rule", not dark
    _DARK_SLOT = re.compile(r"^\s*(black ?out|dark|no light)\b", re.I)

    def _wheel_dark(self, head: dict, values: dict) -> bool:
        """The colour wheel sits on a slot the file calls "Blackout" (a
        Swarm's colour channel at 0): the light is dark, whatever else."""
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

    def _hex_for(self, head: dict, values: dict) -> str:
        """The colour the 3D shows: the emitters mixed (RGB, white, amber,
        UV, lime), CMY, or the colour wheel's slot (its colour from the
        fixture file, or a name it was taught)."""
        roles = set(head["map"])
        r = g = b = 0.0
        lit = False
        if roles & {"red", "green", "blue"} and any(k in values for k in ("red", "green", "blue")):
            r, g, b = (float(values.get(k, 0)) for k in ("red", "green", "blue"))
            lit = True
        leds = cmy_are_leds(roles) and roles & {"cyan", "magenta", "yellow"}
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
        for role, rgb in self._EMIT_RGB.items():
            v = values.get(role)
            if role in roles and v:
                k = float(v) / 255.0
                r, g, b = r + rgb[0] * k, g + rgb[1] * k, b + rgb[2] * k
                lit = True
        if not lit and roles & {"cyan", "magenta", "yellow"} and any(k in values for k in ("cyan", "magenta", "yellow")):
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
        if lit:
            return "#000000"
        return ROLE_HEX.get(head.get("role") or "generic",
                            ROLE_HEX["generic"])
