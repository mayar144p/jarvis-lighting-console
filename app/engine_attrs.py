"""The programmer's values: level, colour, position, beam attributes, fan,
wheel slots and gobos, and placing a light in the room.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json
import re

from app import fixlib, merge, profiles
from app import venue as venue_mod
from app.engine_base import (
    _COLOUR_ROLES,
    _LEVELS_CACHE,
    VDIM,
    _attr_role,
    _clamp,
    _deg,
    _logical_to_phys,
    _parse_hex,
    _phys_to_logical,
    _truthy,
    attr_domain,
    default_mode,
)
from app.engine_support import HTP_ROLES, channel_role, cmy_are_leds
from app.merge import FX_OUTPUT_ROLES


class AttrMixin:
    def _a_fan(self, attribute=None, role=None, from_value=None, to_value=None,
               low=None, high=None, mode="normal", by="order", **_):
        """Spread ONE value across a selection, head by head.

        This is the most lighting-specific thing a console does and it was
        completely absent.  It is how a warm-to-cool sweep across a bar
        gets made, how a rainbow gets spread, and how a row of movers gets
        fanned out instead of all pointing at the same thing - and doing
        it by hand is one action per head.

        Four things make it usable rather than a toy, and each is a
        deliberate choice:

        * DISTRIBUTION is over the SELECTION ORDER by default, not over
          head numbers and not over position.  Selection order is what the
          operator chose when they picked the heads, and it survives a
          re-patch.  `by="position"` sorts by x then z, which is what you
          want when the heads are a physical row and the selection order
          is whatever they clicked in.

        * MODES match the vocabulary every console uses: normal, reverse,
          into_centre, centre_out, random.  Centre-out matters because
          "spread the middle heads furthest" is a real look, and reverse
          is the same sweep the other way without re-picking.

        * The value is CLAMPED to the role's own range, and heads that
          lack the role are REPORTED rather than silently skipped - a fan
          across a mixed selection that quietly ignores the PARs produces a
          look that is wrong on half the rig and says nothing.

        * `intensity` is accepted as a friendly alias for the dimmer, and
          0..100 for a level against 0..255 for a channel, because the two
          are different ranges and guessing wrong produces a fan that is
          either invisible or pinned.
        """
        want = str(attribute or role or "intensity").strip().lower()
        resolved = _attr_role(want)
        if resolved is None:
            raise ValueError(f"unknown attribute {want!r}")
        is_level = resolved in HTP_ROLES
        if low is None:
            low = 0.0
        if high is None:
            high = 100.0 if is_level else 255.0
        if from_value is not None:
            low = float(from_value)
        if to_value is not None:
            high = float(to_value)
        lo_v, hi_v = sorted((float(low), float(high)))
        how = str(mode or "normal").strip().lower()
        if how not in ("normal", "reverse", "into_centre", "centre_out",
                       "random"):
            raise ValueError("mode must be normal, reverse, into_centre, "
                             "centre_out or random")
        heads = self._require_selection()          # head DICT rows
        by = str(by or "order").strip().lower()
        if by not in ("order", "position"):
            raise ValueError("by must be order or position")
        if by == "order":
            order = [h["head_no"] for h in sorted(heads,
                                                  key=lambda r: r["head_no"])]
        else:
            order = [h["head_no"] for h in sorted(
                heads, key=lambda r: (float(r.get("x") or 0.0),
                                      float(r.get("z") or 0.0),
                                      r["head_no"]))]

        n = len(order)
        if n == 1:
            targets = {order[0]: hi_v}
        elif how == "random":
            # Deterministic per head, so a re-run of the same fan is
            # reproducible - a random look that changes every time you
            # touch it cannot be adjusted afterwards.
            targets = {}
            for hn in order:
                frac = ((hn * 2654435761) % 1000) / 1000.0
                targets[hn] = lo_v + (hi_v - lo_v) * frac
        else:
            targets = {}
            for i, hn in enumerate(order):
                t = i / (n - 1)                      # 0 .. 1
                if how == "reverse":
                    t = 1.0 - t
                elif how == "into_centre":
                    t = abs(t - 0.5) * 2.0
                elif how == "centre_out":
                    t = 1.0 - abs(t - 0.5) * 2.0
                targets[hn] = lo_v + (hi_v - lo_v) * t

        applied, skipped = 0, []
        by_no = {h["head_no"]: h for h in heads}
        for hn in order:
            head = by_no.get(hn)
            if head is None or resolved not in (head.get("map") or []):
                skipped.append(hn)
                continue
            value = targets[hn]
            if is_level:
                value = _clamp(round(value), 0, 100)
            else:
                value = _clamp(round(value), 0, 65535)
            if resolved in HTP_ROLES:
                for r, v in self._level_values(head, int(value)).items():
                    self._set_programmer(hn, r, v)
            else:
                self._set_programmer(hn, resolved, int(value))
            applied += 1
        if not applied:
            have = sorted({r for h in heads for r in (h.get("map") or [])
                           if r not in ("raw", "unused")})
            raise ValueError(
                f"none of the selected heads has a {resolved} channel "
                f"(they have: {', '.join(have) or 'nothing controllable'})")
        first, last_h = order[0], order[-1]
        note = (f"; {len(skipped)} head(s) have no {resolved} channel"
                if skipped else "")
        return {"attribute": resolved, "heads": applied,
                "skipped": skipped, "mode": how, "by": by,
                "from": lo_v, "to": hi_v,
                "first": {"head": first, "value": targets[first]},
                "last": {"head": last_h, "value": targets[last_h]},
                "summary": f"fanned {resolved} {lo_v:g}→{hi_v:g} across "
                           f"{applied} head(s) ({how}){note}"}

    # --- programmer -----------------------------------------------------
    def _set_programmer(self, head_no: int, role: str, value: int) -> None:
        row = self.programmer.setdefault(int(head_no), {})
        row[role] = int(value)
        if "@" not in role:
            # the whole role on a multi-head light: every head, so a value
            # set earlier for one head of it gives way
            for key in [k for k in row if k.startswith(role + "@")]:
                del row[key]

    @staticmethod
    def _cells(value) -> list[int]:
        """Which heads of a multi-head light: 2, [1, 3], "1,3" or "all"."""
        if value in (None, "", "all"):
            return []
        items = value if isinstance(value, (list, tuple)) else str(value).replace(" ", "").split(",")
        return sorted({int(x) for x in items if str(x).strip()})

    def _set_cells(self, h: dict, role: str, value: int, cells: list[int]) -> bool:
        """Set a role on some heads of a multi-head light (red@2...)."""
        copies = merge._repeated(h["map"]).get(role, 0)
        if not copies:
            return False
        row = self.programmer.setdefault(h["head_no"], {})
        for k in cells:
            if 1 <= k <= copies:
                row[f"{role}@{k}"] = int(value)
        return True

    def _intensity_roles(self, head: dict) -> set[str]:
        """HTP (brightness) roles this head actually has - may be empty.

        A fixture without a dimmer-like channel (e.g. a moving head in a
        8-channel mode) has NO intensity role at all; pretending it has a
        "dimmer" would write values into a channel the head does not
        own, so those writes would silently vanish at merge time.
        """
        return {r for r in head["map"] if r in HTP_ROLES}

    def _shutter_role(self, head: dict) -> str | None:
        """The role that gates light on a dimmer-less fixture."""
        roles = set(head["map"])
        for role in ("shutter", "strobe"):
            if role in roles:
                return role
        return None

    def _profile_levels(self, head: dict) -> tuple[dict, dict]:
        """(role -> default value, role -> first light-passing value).

        Read from the fixture definition library (app/profiles.py), which
        is where the manuals' default/closed-open columns live - the
        SQLite fixture DB only stores compiled channel labels.  Cached
        per manufacturer/model/mode because the lookup is a linear scan.
        """
        key = (str(head.get("manufacturer") or "").strip().lower(),
               str(head.get("model") or "").strip().lower(),
               str(head.get("mode") or "").strip().lower(),
               int(head.get("channels") or 0))
        hit = _LEVELS_CACHE.get(key)
        if hit is None:
            profile = profiles.get(key[0], key[1])
            mode = None
            for candidate in (profile or {}).get("modes") or []:
                if key[2] and candidate["name"].strip().lower() == key[2]:
                    mode = candidate
                    break
            if mode is None and profile:
                # CSV / hand-written patches carry no mode name - match on
                # the footprint instead, like _resolve_map does.
                for candidate in profile["modes"]:
                    if len(profiles.labels_for(candidate)) == key[3]:
                        mode = candidate
                        break
            mode = mode or None
            hit = (profiles.defaults_for(mode, channel_role),
                   profiles.open_values_for(mode, channel_role))
            _LEVELS_CACHE[key] = hit
        return hit

    def _profile_defaults(self, head: dict) -> dict:
        return self._profile_levels(head)[0]

    def _open_value(self, head: dict, role: str) -> int:
        """DMX value that puts a gate channel (shutter/strobe) open.

        Fixtures disagree about what "open" means: most treat 0 as open
        and higher values as strobing, but the Chauvet Intimidator reads
        0-3 as CLOSED and 4-7 as Open (OFL defaultValue 4).  So ask the
        profile library first (its light_from column), fall back to the
        value the fixture rests at, and 0 only as a last resort.
        """
        rng = self.head_ranges(head).get(role) or {}
        if rng.get("open_user") and rng.get("open_from") is not None:
            return int(rng["open_from"])          # the operator found it on the real light
        light_from = self._profile_levels(head)[1].get(role)
        if light_from:
            return int(light_from)
        # a GDTF file's own Highlight / "Open" value
        from_file = rng.get("open_from")
        if from_file:
            return int(from_file)
        return int(self._profile_defaults(head).get(role, 0))

    def _level_values(self, head: dict, pct: int) -> dict:
        """Role values that put this head at `pct` percent light output.

        A head with a dimmer-like channel scales it.  A dimmer-less head
        has no brightness control at all - its shutter/strobe channel is
        the only gate - so pct>0 opens it (writing the fixture's real open
        value, not a guess) and pct==0 closes it.  Returns {} when the
        head has neither, and the caller reports that honestly instead of
        pretending the fixture responded.
        """
        htp = self._intensity_roles(head)
        if htp:
            return {role: int(pct) for role in htp}
        out: dict = {}
        if set(head["map"]) & _COLOUR_ROLES:
            # no dimmer, but colour emitters (a 3/4/6-channel RGB PAR):
            # a VIRTUAL dimmer that scales the colour - white when no
            # colour is set (merge.resolve_head).  "Full" used to do
            # nothing at all on these, the commonest DJ lights.
            out[VDIM] = int(pct)
        elif "wheel" in head["map"]:
            # its colour channel is its on / off (a Swarm: 0 = "Blackout"):
            # Full leaves the blackout slot for its white / first colour,
            # Out goes back to it
            now_v = int((self.programmer.get(head["head_no"]) or {}).get("wheel", 0))
            dark_slot = next((x for x in self._wheel_slots(head) if self._DARK_SLOT.match(str(x.get("name") or ""))), None)
            if dark_slot is not None:
                if pct > 0 and self._wheel_dark(head, {"wheel": now_v}):
                    lit = self._white_values(head).get("wheel")
                    if lit is not None and not self._wheel_dark(head, {"wheel": lit}):
                        out["wheel"] = int(lit)
                elif pct <= 0:
                    out["wheel"] = int(dark_slot["value"])
        role = self._shutter_role(head)
        if role is None:
            return out
        # Closed first: on the fixtures we can describe, 0 is the closed
        # end of the gate.  build_frames writes the same 0 for an
        # un-driven channel, so "nothing programmed" means "dark" - the
        # wire and the visualiser never disagree.
        if pct <= 0:
            out[role] = 0
        else:
            out[role] = self._open_value(head, role)
        return out

    def _a_set_intensity(self, level=None, fade=None, **_):
        if level is None:
            raise ValueError("level is required (0-100)")
        pct = _clamp(level, 0, 100)
        heads = self._require_selection(lights_only=True)
        try:
            fade_s = max(0.0, min(600.0, float(fade or 0)))
        except (TypeError, ValueError):
            raise ValueError("fade must be a number of seconds")
        now = self._clock()
        start = self._programmer_now(now) if fade_s else {}
        from_vals: dict[int, dict[str, int]] = {}
        no_dimmer, driven = [], 0
        for h in heads:
            values = self._level_values(h, pct)
            if not values:
                no_dimmer.append(h["head_no"])
                continue
            for role, value in values.items():
                if fade_s:
                    from_vals.setdefault(h["head_no"], {})[role] = \
                        (start.get(h["head_no"]) or {}).get(role, 0)
                self._set_programmer(h["head_no"], role, value)
            driven += 1
        if fade_s and from_vals:
            self._prog_fade = {"t0": now, "dur": fade_s, "from": from_vals}
        note = ""
        if no_dimmer:
            note = (f"; {len(no_dimmer)} head(s) have neither a dimmer nor a "
                    f"shutter channel")
        if fade_s:
            note += f" over {fade_s:g}s"
        return {"level": pct, "heads": driven, "no_dimmer": no_dimmer,
                "summary": f"intensity {pct}% on {driven} head(s){note}"}

    def _given_heads(self, heads=None) -> list[dict]:
        """`heads` (numbers) when given - one kind of light in a mixed
        selection - else the selection."""
        if heads:
            if isinstance(heads, (int, str)):
                heads = [heads]
            return [self._head(int(x)) for x in heads]
        return self._require_selection()

    def _a_set_attribute(self, attribute=None, value=None, cell=None, heads=None, **_):
        if attribute is None or value is None:
            raise ValueError("attribute and value are required")
        cells = self._cells(cell)
        role = _attr_role(attribute)
        if role is None:
            raise ValueError(f"unknown attribute {attribute!r}")
        if role == "laser_on":
            # a laser's output/mode channel: the programmer (and so a cue)
            # may choose the MODE it runs in when fired - never "off", and
            # never the output itself, which moves only from its armed buttons
            heads = [h for h in self._given_heads(heads) if "laser_on" in h["map"]]
            if not heads:
                raise ValueError("select a laser with an output/mode channel")
            v = int(_clamp(value, 0, 255))
            if any(v < self._laser_min(h) for h in heads):
                raise ValueError("that value is the laser's OFF - the output only goes on "
                                 "and off from its armed buttons; pick a mode it runs in")
            for h in heads:
                self._set_programmer(h["head_no"], "laser_on", v)
            return {"attribute": role, "value": v, "heads": len(heads),
                    "summary": f"laser mode {v} on {len(heads)} laser(s) - used when fired (armed)"}
        if role in FX_OUTPUT_ROLES:
            raise ValueError(f"{role} is an effect's output: it moves only from the "
                             f"armed FX buttons, never from the programmer")
        heads = self._given_heads(heads)
        if role in HTP_ROLES:
            pct = _clamp(value, 0, 100)
            driven, no_dimmer = 0, []
            for h in heads:
                values = self._level_values(h, pct)
                if not values:
                    no_dimmer.append(h["head_no"])
                    continue
                for r, v in values.items():
                    self._set_programmer(h["head_no"], r, v)
                driven += 1
            return {"attribute": role, "value": pct, "heads": driven,
                    "no_dimmer": no_dimmer}
        # 0-255 keeps the classic 8-bit meaning; a fixture with a fine
        # channel for this role accepts the full 16-bit logical value
        # (build_frames splits it over both bytes and clamps the wire
        # write for 8-bit fixtures, so old behaviour is preserved).
        raw = _clamp(value, 0, 65535)
        if cells:
            done = []
            for h in heads:
                if self._set_cells(h, role, int(_clamp(value, 0, 255)), cells):
                    done.append(h)
                else:
                    self._set_programmer(h["head_no"], role, raw)   # a channel it has once
            return {"attribute": role, "value": raw, "heads": len(heads), "cells": cells,
                    "summary": (f"{role} on head(s) {', '.join(map(str, cells))} of {len(done)} light(s)"
                                if done else f"{role} on {len(heads)} light(s) (one {role} each)")}
        for h in heads:
            self._set_programmer(h["head_no"], role, raw)
        return {"attribute": role, "value": raw, "heads": len(heads),
                "summary": f"{role}={raw} on {len(heads)} head(s)"}

    _NUMBERED = re.compile(r"^\s*(colou?r|col|slot|pos(ition)?|macro|preset|wheel)?\s*\.?\s*#?\d+\s*$", re.I)

    def _wheel_slots(self, head: dict, role: str = "wheel") -> list[dict]:
        """The wheel's real slots from the fixture file, or [].  A colour
        slot the file names only by number ("Color 3") but gives a colour
        is named by its colour ("Light cyan")."""
        if not hasattr(self, "_range_cache"):     # a bare engine (tools)
            return []
        rng = self.head_ranges(head).get(role) or {}
        slots = list(rng.get("slots") or [])
        if not slots and role.startswith("wheel") and rng.get("caps"):
            # a colour channel the file describes only by its ranges
            # ("0-51 Blackout, 52-102 Red..."): those are its slots
            slots = [{"from": int(c[0]), "to": int(c[1]), "value": int(c[0]), "name": str(c[2] or "")}
                     for c in rng["caps"] if isinstance(c, (list, tuple)) and len(c) >= 3]
        if role.startswith("wheel") and any(not x.get("hex") for x in slots):
            # a slot named "Red" with no colour in the file: the colour of
            # its name, so the picker can land on it and the 3D shows it
            from app.showdesign import hex_from_name
            slots = [dict(x, hex=hex_from_name(x.get("name") or "")) if not x.get("hex") and hex_from_name(x.get("name") or "")
                     else x for x in slots]
        if role.startswith("wheel") and any(self._NUMBERED.match(str(x.get("name") or "")) and x.get("hex") for x in slots):
            from app.showdesign import colour_name
            seen: dict = {}
            out = []
            for x in slots:
                x = dict(x)
                if self._NUMBERED.match(str(x.get("name") or "")) and x.get("hex"):
                    name = colour_name(x["hex"])
                    seen[name] = seen.get(name, 0) + 1
                    x["name"] = name if seen[name] == 1 else f"{name} {seen[name]}"
                out.append(x)
            slots = out
        return slots

    def _better_mode(self, head: dict) -> str | None:
        """A mode of this light that can do more than the one it's patched
        in - full colour where it is on colour presets only - or None."""
        if not any(r in head.get("map") or [] for r in ("wheel", "red", "white", "dimmer")):
            return None
        key = (head.get("manufacturer"), head.get("model"), head.get("mode"))
        cache = self.__dict__.setdefault("_better_cache", {})
        if key in cache:
            return cache[key]
        out = None
        fx = self._fixture_db(head.get("manufacturer"), head.get("model")) or {}
        modes = fx.get("modes") or []
        if len(modes) > 1:
            best = default_mode(modes)
            mine = set(head.get("map") or [])
            theirs = {channel_role(c) for c in best.get("channels") or []}
            mix = {"red", "green", "blue"}
            if best.get("name") != head.get("mode") and (
                    (mix <= theirs and not mix <= mine)
                    or ({"pan", "tilt"} <= theirs and not {"pan", "tilt"} <= mine)):
                out = best["name"]
        cache[key] = out
        return out

    def _gobo_images(self, head: dict) -> list[list] | None:
        """[[from, to, picture], ...] of the light's gobo wheel, for the 3D
        to project its real gobos; None when the file names none."""
        if not any(r == "gobo" for r in head.get("map") or []):
            return None
        rows = [[s["from"], s["to"], s["img"]] for s in self._wheel_slots(head, "gobo") if s.get("img")]
        if not rows:
            # installed before pictures were read: the library file says
            src = (self._fixture_db(head.get("manufacturer"), head.get("model")) or {}).get("source") or ""
            rows = fixlib.gobo_slots(src, head.get("mode") or "")
        if not rows:
            # slots named but no pictures: "" = open, "-" = a gobo the 3D
            # draws a stand-in pattern for (not a guess from the DMX value)
            import re
            rows = [[s["from"], s["to"], "" if re.search(r"\bopen\b|no gobo", s.get("name") or "", re.I) else "-"]
                    for s in self._wheel_slots(head, "gobo")
                    if not re.search(r"shake|scroll|rotat|spin|rainbow", s.get("name") or "", re.I)]
        return rows or None

    _GOBO2 = re.compile(r"gobo\s*(wheel)?\s*2\b", re.I)
    _ANIM = re.compile(r"animation|effect\s*wheel|anim\.?\s*(disk|wheel)", re.I)

    def _gobo2_role(self, head: dict) -> str | None:
        """A second gobo wheel (MAC 2000: "Gobo Wheel 2, Gobo & Function",
        not its "..., Position/Velocity" channel): its channel, when the
        file names one with slots."""
        ranges = self.head_ranges(head)
        for r in head.get("map") or []:
            if not r.startswith("aux"):
                continue
            name = str((ranges.get(r) or {}).get("name") or "")
            what = name.split(",", 1)[1] if "," in name else ""
            if self._GOBO2.search(name) and not re.search(r"posit|veloc|rotat|speed|fine", what, re.I) \
                    and (ranges.get(r) or {}).get("slots"):
                return r
        return None

    def _anim_roles(self, head: dict) -> tuple[str | None, str | None]:
        """(insertion, rotation) of an animation / effect wheel, by name."""
        ranges = self.head_ranges(head)
        names = {r: str((ranges.get(r) or {}).get("name") or "") for r in head.get("map") or [] if r.startswith("aux")}
        anim = [r for r, n in names.items() if self._ANIM.search(n)]
        rot = next((r for r in anim if re.search(r"rotat|speed|veloc|posit", names[r], re.I)), None)
        ins = next((r for r in anim if r != rot), None)
        return ins, rot

    def _gobo2_images(self, head: dict) -> list[list] | None:
        """[[from, to, picture], ...] of the second gobo wheel (as for the
        first: "" open, "-" a drawn stand-in)."""
        role = self._gobo2_role(head)
        if not role:
            return None
        rows = [[s["from"], s["to"], s.get("img") or ("" if re.search(r"\bopen\b|no gobo", s.get("name") or "", re.I) else "-")]
                for s in self._wheel_slots(head, role)
                if not re.search(r"shake|scroll|rotat(?!ion gobo)|spin|rainbow", re.sub(r"indexed rotation", "", s.get("name") or "", flags=re.I), re.I)]
        return rows or None

    def _nearest_slot(self, head: dict, hexcol: str) -> dict | None:
        """The colour-wheel slot closest to `hexcol`: a wheel can't mix,
        so the picker lands on the nearest colour the fixture really has."""
        slots = [s for s in self._wheel_slots(head) if s.get("hex")]
        if not slots:
            return None
        r, g, b = _parse_hex(hexcol)

        def norm(rgb):
            top = max(rgb) or 1
            return [c / top for c in rgb]
        want = norm((r, g, b))
        best, best_d = None, 1e9
        for s in slots:
            have = norm(_parse_hex(s["hex"]))
            d = sum((a - c) ** 2 for a, c in zip(want, have))
            if d < best_d:
                best, best_d = s, d
        return best

    def _colour_values(self, head: dict, hexcol: str) -> dict:
        r, g, b = _parse_hex(hexcol)
        roles = set(head["map"])
        out = {}
        if roles & {"red", "green", "blue"}:
            for role, v in (("red", r), ("green", g), ("blue", b)):
                if role in roles:
                    out[role] = v
            # A picked colour is the WHOLE colour: an RGBWA(UV) PAR's white
            # and amber LEDs left where Full / a white look put them turned
            # every saturated pick pastel ("pink with whiteness").  The white
            # LED joins only for a neutral pick (white / grey).
            neutral = max(r, g, b) - min(r, g, b) < 12
            if "white" in roles:
                out["white"] = min(r, g, b) if neutral else 0
            for extra in ("amber", "uv", "lime", "indigo"):
                if extra in roles:
                    out[extra] = 0
            if "lime" in roles and "green" not in roles:
                out["lime"] = g        # red + lime + blue: the lime is its green
            # cyan / magenta / yellow LEDs beside red, green and blue: each
            # gives what its two primaries share beyond the third
            for role, v in (("cyan", min(g, b) - r), ("magenta", min(r, b) - g), ("yellow", min(r, g) - b)):
                if role in roles:
                    out[role] = min(r, g, b) if neutral else max(0, v)
        elif roles & {"cyan", "magenta", "yellow"}:
            for role, v in (("cyan", 255 - r), ("magenta", 255 - g),
                            ("yellow", 255 - b)):
                if role in roles:
                    out[role] = v
        elif self._hue_sat(head):
            # hue + saturation channels: the pick as hue and saturation
            import colorsys as _cs
            hs = self._hue_sat(head)
            hh, ss, _vv = _cs.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            out[hs["hue"]] = int(round(hh * 255)) % 256
            if hs.get("sat"):
                out[hs["sat"]] = int(round(ss * 255))
        elif "white" in roles:
            out["white"] = int(0.299 * r + 0.587 * g + 0.114 * b)
        elif "wheel" in roles:
            slot = self._nearest_slot(head, hexcol)
            if slot is not None:
                out["wheel"] = int(slot["value"])
        return out

    def _white_values(self, head: dict) -> dict:
        """Values that put this head's colour at white / open."""
        roles = set(head["map"])
        out = {}
        hs = self._hue_sat(head)
        if hs and hs.get("sat"):
            out[hs["sat"]] = 0                  # no saturation: white
        rgb = {"red", "green", "blue"} <= roles
        if "white" in roles and not rgb:
            # a white emitter beside a partial mix (a Rocklite's red /
            # amber / white mode): the white LED alone is white, red + white
            # would be pink
            out["white"] = 255
            for role in ("red", "green", "blue", "amber", "lime"):
                if role in roles:
                    out[role] = 0
        else:
            for role in ("red", "green", "blue", "white"):
                if role in roles:
                    out[role] = 255
            if "lime" in roles and "green" not in roles:
                # red + lime + blue (+ indigo): an ETC Source Four LED's
                # lime ("Mint") is its green - without it white is magenta
                out["lime"] = 255
        for role in ("cyan", "magenta", "yellow"):
            if role in roles:
                out[role] = 255 if cmy_are_leds(roles) else 0   # LEDs on, filters out
        # Wheel fixtures: slot 1 (DMX 0) is the open/clear slot on both the
        # colour and the gobo wheel, so 0 == "no colour, full beam" - that
        # is this fixture's white.  A light that mixes AND has a wheel (a
        # CMY wash's colour wheel) needs its wheel open too, or the wheel
        # tints the white it mixed.
        if True:
            dark = self._DARK_SLOT
            for role in ("wheel", "gobo"):
                if role in roles:
                    out[role] = 0
                    slots = self._wheel_slots(head, role)
                    for s in slots:
                        if s["name"].strip().lower() in ("open", "white", "clear"):
                            out[role] = int(s["value"])
                            break
                    else:
                        # slot 0 is "Blackout" on some lights (a Swarm's colour
                        # channel): white is then the first slot that lights
                        first = next((s for s in slots if int(s["value"]) <= 0 or s.get("from", 1) == 0), None)
                        if first and dark.match(str(first["name"])):
                            # a slot that says white ("Full white", "RGBW white")
                            # before the first that merely lights (often red)
                            rgb = lambda n: all(re.search(rf"\b{c}\b", n, re.I) for c in ("red", "green", "blue"))  # noqa: E731
                            lit = next((s for s in slots if re.search(r"\b(white|open|clear)\b", str(s["name"]), re.I)
                                        and not dark.match(str(s["name"]))), None) \
                                or next((s for s in slots if rgb(str(s["name"]))), None) \
                                or next((s for s in slots if not dark.match(str(s["name"]))), None)
                            if lit:
                                out[role] = int(lit["value"])
        return out

    def _a_set_colour(self, hex=None, colour=None, value=None, cell=None, **_):
        hexcol = hex or colour or value
        if not hexcol:
            raise ValueError("hex colour is required (#rrggbb)")
        _parse_hex(hexcol)                       # validate before selecting
        cells = self._cells(cell)
        heads = self._require_selection(lights_only=True)
        touched = 0
        unknown = 0
        for h in heads:
            values = self._colour_values(h, str(hexcol))
            if not values:
                continue                          # raw head: nothing to set
            if set(values) == {"wheel"} and sum(1 for x in self._wheel_slots(h) if x.get("hex")) <= 1:
                unknown += 1                       # its file names no colours: we can't aim for one
            reps = merge._repeated(h["map"])
            for role, v in values.items():
                if cells and role in reps:
                    self._set_cells(h, role, v, cells)   # just these heads of it
                else:
                    self._set_programmer(h["head_no"], role, v)
            touched += 1
        if not touched:
            maps = [set(h["map"]) for h in heads]
            if any("wheel" in m for m in maps):
                raise ValueError("these lights' colour wheels aren't named in their files - "
                                 "Colour tab -> Name the colours… once (look at the real light), then pick")
            if maps and all(m & {"uv"} and not m & {"red", "green", "blue", "white", "amber"} for m in maps):
                raise ValueError("these are UV lights: they have one colour - set their level instead")
            raise ValueError("selected heads have no colour channels")
        note = (f"; {unknown} light(s) have a colour wheel their file doesn't name - "
                "Colour tab -> Name the colours… once, then colours land on the right slot") if unknown else ""
        return {"hex": str(hexcol), "heads": touched, "unknown_wheel": unknown,
                "summary": f"colour {hexcol} on {touched} head(s)" + note}

    def _a_set_position(self, pan=None, tilt=None, unit=None,
                        head=None, heads_in=None, **_):
        """Aim the selection, in logical values or in DEGREES.

        `unit="degree"` converts through each head's own travel, so
        `tilt -30` is thirty degrees down on this light and something else
        entirely on the next one.  The unit is explicit for the same
        reason as `set_attr_range` (§17.19): "30" means 30 of 65535 to a
        script and 30 degrees to an operator, and guessing sends the head
        into the floor.

        `head=` aims ONE head and leaves the selection alone, which is the
        case a selection cannot express - you are nudging one fixture and
        must not disturb the twelve beside it.
        """
        if pan is None and tilt is None:
            raise ValueError("pan and/or tilt required (0-255, 0-65535 on "
                             "16-bit fixtures, or degrees with unit=degree)")
        # The unit is EXPLICIT here for the same reason as `set_attr_range`:
        # "30" is 30 of 65535 to a script and 30 degrees to an operator, and
        # silently choosing is how a head ends up in the floor.  So a
        # request for some OTHER unit is refused BY NAME rather than
        # quietly treated as degrees.
        want = str(unit or "").strip().lower()
        if want and want not in ("degree", "deg", "degrees", "logical",
                                 "raw", "dmx", "auto", "255", "65535"):
            raise ValueError(
                f"position takes degrees or a logical 0-255/0-65535 value, "
                f"not {unit!r}")
        # With no `unit`, a bare number is DEGREES when every head that can
        # be aimed knows its travel, and a logical value otherwise.
        #
        # This is the ONE place the unit is inferred, and it is decided from
        # the FIXTURE rather than from the shape of the number.  A guess
        # based on the value would be the §17.19 trap: `aim 90` is 90
        # degrees on a mover and 90 of 255 on a light with no GDTF, and both
        # readings are plausible while meaning very different things on
        # stage.  A selection where only some heads declare travel falls
        # back to logical, because a conversion half the selection cannot
        # honour is worse than neither.
        with self.lock:
            if head is not None:
                rows = [self._head(int(head))]
            elif heads_in:
                rows = [self._head(int(h)) for h in heads_in]
            else:
                rows = self._require_selection()
            if not want:
                # Re-decide now that the rows are known.  Kept out of the
                # block above because the unit check reads the selection,
                # and this branch only needs the rows.
                for role, value in (("pan", pan), ("tilt", tilt)):
                    if value is None:
                        continue
                    can = [h for h in rows
                           if role in (h.get("map") or [])]
                    if can and all(
                            (self.role_range([h], role) or {}).get("unit")
                            == "degree" for h in can):
                        want = "degree"
                        break
            phys = want in ("degree", "deg", "degrees")
            moved, absent, degrees = [], [], {}
            for h in rows:
                row_changed = False
                for role, value in (("pan", pan), ("tilt", tilt)):
                    if value is None:
                        continue
                    if role not in (h.get("map") or []):
                        absent.append({"head": h["head_no"], "role": role})
                        continue
                    if phys:
                        rng = self.role_range([h], role)
                        if not rng or rng.get("unit") != "degree":
                            raise ValueError(
                                f"head {h['head_no']} has no known "
                                f"{role} travel, so degrees cannot be "
                                f"converted — give a 0-255 value")
                        full = attr_domain(h, role)
                        logical = _phys_to_logical(
                            float(value), rng["min"], rng["max"], full)
                        degrees[role] = _deg(_logical_to_phys(
                            _clamp(round(logical), 0, full),
                            rng["min"], rng["max"], full))
                    elif want in ("255", "65535"):
                        # a fraction of the travel, given as 0-255 (the pad,
                        # an XY tile, Home) or 0-65535: scaled to THIS head's
                        # own resolution - taken as-is it moved a 16-bit
                        # mover 0.4% of its travel
                        top = 255.0 if want == "255" else 65535.0
                        logical = _clamp(float(value), 0, top) / top * attr_domain(h, role)
                        degrees = {}
                    else:
                        logical = float(value)
                        degrees = {}
                    applied = _clamp(round(logical), 0, 65535)
                    self._set_programmer(h["head_no"], role, applied)
                    row_changed = True
                if row_changed:
                    moved.append(h["head_no"])
            if not moved:
                # A selection with no pan channel is NOT an error here.
                # `set_attr_range` refuses it, because an encoder row that
                # did nothing must say so - but `set_position` is also
                # called as a "point everything at centre" step over a
                # whole mixed rig, where a row of PAR cans legitimately has
                # nowhere to aim.  Raising there broke the show builder,
                # which sets a home position before recording the seed
                # palettes.  So: report it, aim nothing, and let the caller
                # decide.
                have = sorted({r for h in rows
                               for r in (h.get("map") or [])
                               if r not in ("raw", "unused")})
                wanted = [r for r, v in (("pan", pan), ("tilt", tilt))
                          if v is not None]
                return {"pan": pan, "tilt": tilt, "heads": 0, "moved": [],
                        "aimed": False,
                        "unit": "degree" if phys else "logical",
                        "missing": [{"head": h["head_no"], "role": r}
                                    for h in rows for r in wanted],
                        "partial": True,
                        "have": have,
                        "summary": (f"nothing to aim: none of the "
                                    f"{len(rows)} head(s) has a "
                                    + " or ".join(wanted)
                                    + " channel (they have: "
                                    + (", ".join(have)
                                       or "nothing controllable"))}
            by_role: dict[str, list] = {}
            for a in absent:
                by_role.setdefault(a["role"], []).append(a["head"])
            out = {"pan": pan, "tilt": tilt, "heads": len(moved),
                   "moved": moved, "aimed": True,
                   "unit": "degree" if phys else "logical",
                   "missing": absent, "partial": bool(absent)}
            if degrees:
                out["degrees"] = degrees
            # The transcript has to say which reading was used, or the
            # operator cannot tell whether `aim 90` moved the head 90
            # degrees or 90 of 255 - and the two look identical in the
            # result while meaning very different things on stage.
            said = ""
            for role, label in (("pan", "pan"), ("tilt", "tilt")):
                if role in degrees:
                    said += f"{label} {degrees[role]:g}° "
                elif (pan if role == "pan" else tilt) is not None:
                    said += (f"{label} {int(pan if role == 'pan' else tilt)}"
                             f" {out['unit']} ")
            if not said:
                said = f"aim set on {len(moved)} head(s) "
            out["summary"] = said.strip() + f" on {len(moved)} of " \
                f"{len(rows)} head(s)"
            if absent:
                out["summary"] += "; " + ", ".join(
                    f"{r} missing on {len(ns)}" for r, ns in by_role.items()
                ) + " left alone"
            return out

    def _a_set_place(self, head=None, heads=None, x=None, y=None, z=None,
                     kind=None, rig=None, t=None, stance=None, snap=False,
                     rot=None, **_):
        """Put a head somewhere: free-standing at x/y/z, or on a rig.

        `rig` + `t` mounts it (t = 0..1 along the rig; when t is missing
        the point nearest x/y/z is used).  `rig=""` lets go of a rig.
        `snap` mounts to the nearest rig within reach of the new position.
        `stance` is "hang" (under the bar) or "stand" (upright).  `rot` is
        [yaw, pitch] in degrees for a light with no pan/tilt, or null to
        let the visualiser aim it.  Positions are clamped to the room (plus
        a margin), because a drag far from the camera turns a small mouse
        move into a large jump and a light must never be lost off-screen.
        """
        targets: list[int] = []
        if head is not None:
            targets.append(int(head))
        targets.extend(int(h) for h in (heads or []))
        if not targets:
            raise ValueError("head is required")
        b = venue_mod.bounds(self.venue)
        half_w = max(abs(b["x0"]), abs(b["x1"])) + 4.0
        min_z, max_z = b["z0"] - 4.0, b["z1"] + 4.0
        max_y = b["h"] + 4.0
        if stance not in (None, "hang", "stand"):
            raise ValueError("stance is hang or stand")
        clamped = False
        moved = []
        for n in targets:
            h = self._head(n)                   # raises if unpatched
            before = (h["x"], h["y"], h["z"], json.dumps(h.get("mount")),
                      h.get("stance"), json.dumps(h.get("rot")))
            px = float(x) if x is not None else h["x"]
            py = float(y) if y is not None else h["y"]
            pz = float(z) if z is not None else h["z"]
            cx = max(-half_w, min(half_w, px))
            cy = max(0.0, min(max_y, py))
            cz = max(min_z, min(max_z, pz))
            clamped = clamped or (cx, cy, cz) != (px, py, pz)
            target_rig = None
            tt = t
            if rig:
                target_rig = venue_mod.rig(self.venue, str(rig))
                if not target_rig:
                    raise ValueError(f"no rig {rig!r}")
                if tt is None:
                    near = venue_mod.nearest_rig(
                        {"rigging": [target_rig]}, cx, cy, cz, reach=1e9)
                    tt = near[1] if near else 0.5
            elif rig is None and _truthy(snap):
                near = venue_mod.nearest_rig(self.venue, cx, cy, cz, reach=0.6)
                if near:
                    target_rig, tt = near[0], near[1]
            if stance is not None:
                h["stance"] = stance
            elif target_rig and (h.get("mount") or {}).get("rig") != target_rig["id"]:
                h.pop("stance", None)        # a new rig: hang or stand as it does
            if target_rig:
                same = (h.get("mount") or {}).get("rig") == target_rig["id"]
                h["mount"] = {"rig": target_rig["id"],
                              "t": round(max(0.0, min(1.0, float(tt))), 4),
                              # slid along the same rig: it keeps its facing
                              **({"yaw0": h["mount"]["yaw0"]} if same and h["mount"].get("yaw0") is not None else {})}
                self._head_yaw(h)          # hung on a rig: faces along it
                pos = venue_mod.mount_position(target_rig, h["mount"]["t"],
                                               h.get("stance"))
                h["x"], h["y"], h["z"] = pos["x"], pos["y"], pos["z"]
                h["stance"] = pos["orient"]
            else:
                if rig == "" or x is not None or y is not None or z is not None:
                    was_mounted = bool(h.pop("mount", None))
                    if was_mounted and stance is None:
                        h.pop("stance", None)    # free again: height decides
                if y is not None and stance is None:
                    # dragged up past 2 m it hangs, down below it stands: a
                    # light left "standing" in mid-air at 5 m couldn't tilt
                    # down to the floor (it aimed at the roof)
                    if (float(h["y"]) >= 2.0) != (cy >= 2.0):
                        h.pop("stance", None)
                    h["kind"] = "truss" if cy >= 2.0 else "floor"
                h["x"], h["y"], h["z"] = cx, cy, cz
            if rot is not None:
                if isinstance(rot, (list, tuple)) and len(rot) == 2:
                    h["rot"] = [round(float(rot[0]), 2) % 360,
                                round(max(-180.0, min(180.0, float(rot[1]))), 2)]
                else:
                    h.pop("rot", None)
            if kind in ("truss", "floor"):
                h["kind"] = kind
                if not h.get("mount"):
                    h["stance"] = "hang" if kind == "truss" else "stand"
            else:
                side = h.get("stance")
                h["kind"] = ("truss" if side == "hang" else "floor") if side \
                    else ("truss" if h["y"] >= 2.0 else "floor")
            after = (h["x"], h["y"], h["z"], json.dumps(h.get("mount")),
                     h.get("stance"), json.dumps(h.get("rot")))
            if after != before:
                moved.append(h["head_no"])
        first = self._head(moved[0] if moved else targets[0])
        if not moved:
            return {"head_no": first["head_no"], "heads": 0, "moved": False,
                    "clamped": clamped, "x": first["x"], "y": first["y"],
                    "z": first["z"], "kind": first["kind"],
                    "mount": first.get("mount")}
        self.patch_rev += 1                 # the 3D view rebuilds on a rev
        where = (f" on {first['mount']['rig']}" if first.get("mount") else "")
        return {"head_no": first["head_no"], "heads": len(moved), "moved": True,
                "moved_heads": moved, "clamped": clamped,
                "x": first["x"], "y": first["y"], "z": first["z"],
                "kind": first["kind"], "mount": first.get("mount"),
                "stance": first.get("stance"),
                "bounds": {"half_width_m": half_w, "min_depth_m": min_z,
                           "max_depth_m": max_z, "max_height_m": max_y},
                "summary": f"moved {len(moved)} head(s) to "
                           f"x{first['x']:.2f} y{first['y']:.2f} "
                           f"z{first['z']:.2f}{where}"
                           + (" (clamped to the room)" if clamped else "")}
