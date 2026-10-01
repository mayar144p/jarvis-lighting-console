"""Quick buttons (MagicQ-style executors) and My moves.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import math
import os
import time

from app import fixture_kind
from app import fxlib as fxlib_mod
from app import motion as motion_mod
from app.engine_base import _attr_role, _clamp, _hex_or_none, _truthy
from app.engine_support import HTP_ROLES, LASER_ROLES, ROLES
from app.merge import FX_OUTPUT_ROLES


class QuickMixin:
    # ------------------------------------------------------------------
    # quick buttons: instant, MagicQ-style executor buttons
    # ------------------------------------------------------------------
    QUICK_KINDS = ("flash", "strobe", "colour", "kill", "fx", "go",
                   "release", "preset", "blackout",
                   # one of "My moves", by id (an edit to the move reaches it)
                   "move",
                   # anything at once: level / dim / colour / strobe /
                   # captured values / attributes / effects, on any lights
                   "custom",
                   # special effects: their own buttons, never a light's
                   "sfx", "fog", "laser", "fxkill", "arm",
                   # a macro: its command lines, played in one go
                   "macro",
                   # control tiles on the buttons page: a fader, a pan/tilt
                   # pad, the tempo, a cue list, the effects' emergency stop
                   "fader", "xy", "tempo", "cuelist", "estop")

    FX_BUTTONS = frozenset({"sfx", "fog", "laser", "fxkill", "arm"})

    # buttons that do one thing and are done: no timer, no radio group
    ONE_SHOT_BUTTONS = frozenset({"go", "release", "preset", "arm", "fxkill", "macro",
                                  "fader", "xy", "tempo", "cuelist", "estop"})
    FADER_OF = ("master", "speed", "playback", "group")

    SPLITS = ("odd", "even", "left", "right")

    QUICK_PAGES = 8

    QUICK_SIZES = ("wide", "tall", "big")
    # on the beat: a press waits for the next half beat / beat / 2 beats / bar
    QUANTS = (0.0, 0.5, 1.0, 2.0, 4.0)
    # these act the moment they are touched, whatever the setting
    NO_QUANT = frozenset({"fxkill", "arm", "estop", "tempo", "fader", "xy"})
    # pressed this late after the beat still counts as on it (seconds)
    QUANT_LATE_S = 0.08

    QUICK_ICONS = ("bolt", "sun", "moon", "star", "heart", "fire", "snow", "drop", "music",
                   "strobe", "spin", "sparkle", "eye", "stop", "up", "down")

    QUICK_SLOTS = 24

    def _quick_clean(self, raw: dict, page: int, slot: int) -> dict:
        kind = str(raw.get("kind") or "flash").lower()
        if kind not in self.QUICK_KINDS:
            raise ValueError(f"button kind must be one of {', '.join(self.QUICK_KINDS)}")
        mode = str(raw.get("mode") or ("hold" if kind in ("flash", "strobe", "kill", "blackout",
                                                          "sfx", "laser") else
                                       "latch" if kind in ("colour", "fx", "arm") else "tap")).lower()
        if mode not in ("hold", "latch", "tap"):
            raise ValueError("mode is hold, latch or tap")
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {"all": True}
        clean_t: dict = {}
        if target.get("group") is not None:
            clean_t["group"] = int(target["group"])
        elif target.get("auto"):
            clean_t["auto"] = str(target["auto"])[:40]
        elif target.get("heads"):
            clean_t["heads"] = sorted({int(h) for h in target["heads"]})[:512]
        elif target.get("type"):
            clean_t["type"] = str(target["type"])[:30]
        else:
            clean_t["all"] = True
        split = str(target.get("split") or "").lower()
        if split:
            if split not in self.SPLITS:
                raise ValueError(f"split is one of {', '.join(self.SPLITS)}")
            clean_t["split"] = split
        btn = {"id": f"q{page}-{slot}", "page": page, "slot": slot,
               "label": str(raw.get("label") or kind.title())[:24],
               "kind": kind, "mode": mode, "target": clean_t,
               "colour": _hex_or_none(raw.get("colour"))}
        # how the button LOOKS and BEHAVES, for every kind that stays on
        tint = _hex_or_none(raw.get("tint"))
        if tint:
            btn["tint"] = tint
        # its effects' own speed, and whether the Speed master moves them
        if kind in ("fx", "move", "custom"):
            if raw.get("rate") not in (None, "", 1, "1", 1.0):
                btn["rate"] = self._quick_rate_value(raw.get("rate"))
            if raw.get("free"):
                btn["free"] = True
        # a MIDI note (0-127) that plays it, from a pad or keyboard
        if raw.get("midi") not in (None, ""):
            note = int(_clamp(raw.get("midi"), -1, 128))
            if not 0 <= note <= 127:
                raise ValueError("a MIDI note is 0 to 127")
            btn["midi"] = note
        # a bigger tile (2 wide, 2 tall or both) and an icon on it
        if raw.get("size") not in (None, "", "normal"):
            if raw["size"] not in self.QUICK_SIZES:
                raise ValueError(f"size is one of normal, {', '.join(self.QUICK_SIZES)}")
            btn["size"] = raw["size"]
        if raw.get("quant") not in (None, "", "page", "desk"):
            try:
                q = float(raw["quant"])
            except (TypeError, ValueError):
                q = -1.0
            if q not in self.QUANTS:
                raise ValueError("quant (fire on the beat) is 0 (as pressed), 0.5, 1, 2 or 4 beats")
            btn["quant"] = q
        if raw.get("icon") not in (None, ""):
            if raw["icon"] not in self.QUICK_ICONS:
                raise ValueError(f"icon is one of {', '.join(self.QUICK_ICONS)}")
            btn["icon"] = raw["icon"]
        if kind not in self.ONE_SHOT_BUTTONS:
            if raw.get("exclusive") not in (None, ""):
                btn["exclusive"] = str(raw["exclusive"]).strip()[:20] or None
                if not btn["exclusive"]:
                    btn.pop("exclusive")
            if kind not in self.FX_BUTTONS and raw.get("seconds") not in (None, "", 0, "0"):
                btn["seconds"] = float(_clamp(raw.get("seconds"), 0.1, 3600))
        if kind == "custom":
            self._quick_clean_custom(raw, btn)
        # a key on the keyboard (one letter or digit) that plays it
        key = str(raw.get("key") or "").strip().lower()[:1]
        if key and key.isalnum():
            btn["key"] = key
        # brightness fades: in when pressed, out when let go
        if kind in ("flash", "custom", "kill"):
            for f in ("fade_in", "fade_out"):
                if raw.get(f) not in (None, "", 0, "0"):
                    try:
                        v = float(raw.get(f))
                    except (TypeError, ValueError):
                        raise ValueError(f"not a number: {raw.get(f)!r}") from None
                    btn[f] = round(min(30.0, max(0.05, v)), 2)
        if kind == "flash":
            btn["level"] = int(_clamp(raw.get("level", 100), 0, 100))
        if kind == "strobe":
            btn["hz"] = float(_clamp(raw.get("hz", 10), 1, 20))
        if kind == "colour" and not btn["colour"]:
            raise ValueError("a colour button needs a colour")
        if kind == "fx":
            name = str(raw.get("fx") or "")
            if name.startswith("step:"):
                # one of the show's step effects (FX tab -> Step effects)
                if not any(f["id"] == name[5:] for f in self._steps()):
                    raise ValueError(f"no step effect {name[5:]!r}")
                btn["fx"] = name
                beats = (raw.get("params") or {}).get("beats") if isinstance(raw.get("params"), dict) else None
                if beats not in (None, "", 0):
                    btn["params"] = {"beats": self._clean_beats(beats)}
            else:
                if name not in fxlib_mod.FX:
                    raise ValueError(f"unknown effect {name!r}")
                btn["fx"] = name
                params = self._quick_fx_params(name, raw.get("params"))
                if params:
                    btn["params"] = params
        if kind in ("go", "release"):
            btn["playback"] = int(_clamp(raw.get("playback", 1), 1, len(self.playbacks) or 10))
            if raw.get("cue") not in (None, ""):
                btn["cue"] = int(raw["cue"])
        if kind == "preset":
            btn["preset"] = int(raw.get("preset") or 0)
        if kind == "fader":
            c = raw.get("control") if isinstance(raw.get("control"), dict) else {}
            what = str(c.get("what") or "master")
            if what not in self.FADER_OF:
                raise ValueError(f"a fader is one of {', '.join(self.FADER_OF)}")
            btn["control"] = {"what": what}
            if what in ("playback", "group"):
                btn["control"]["n"] = int(_clamp(c.get("n", 1), 1, 999))
            btn["mode"] = "tap"
        if kind == "cuelist":
            btn["playback"] = int(_clamp(raw.get("playback", 1), 1, len(self.playbacks) or 10))
            btn["mode"] = "tap"
        if kind in ("xy", "tempo", "estop"):
            btn["mode"] = "tap"
        if kind == "macro":
            if not raw.get("macro"):
                raise ValueError("a macro button needs a macro")
            btn["macro"] = str(raw["macro"])[:16]
            btn["mode"] = "tap"
        if kind == "move":
            mid = str(raw.get("move") or "")
            if not mid:
                raise ValueError("a move button needs one of My moves")
            btn["move"] = mid
        if kind in ("sfx", "fog", "laser") and raw.get("seconds") not in (None, ""):
            btn["seconds"] = float(_clamp(raw.get("seconds"), 0.2, 600))
        if kind == "fog":
            btn["level"] = int(_clamp(raw.get("level", 100), 1, 100))
            btn.setdefault("seconds", 10.0)
        if kind == "laser" and isinstance(raw.get("values"), dict):
            btn["values"] = {str(k): int(_clamp(v, 0, 255)) for k, v in raw["values"].items()
                             if str(k) in LASER_ROLES}
        return btn

    # ------------------------------------------------------------------
    # My moves: named movements, not cues - play one on any lights
    # ------------------------------------------------------------------
    MOVE_MAX = 64

    def _move_clean(self, raw: dict) -> dict:
        lib = str(raw.get("lib") or raw.get("fx") or "").lower()
        if lib not in motion_mod.KINDS:
            raise ValueError(f"a move is one of {', '.join(motion_mod.KINDS)}")
        name = str(raw.get("name") or "").strip()[:32]
        if not name:
            raise ValueError("a move needs a name")
        mid = str(raw.get("id") or "") or "m" + os.urandom(4).hex()
        return {"id": mid[:16], "name": name, "lib": lib,
                "params": self._quick_fx_params(lib, raw.get("params") or {})}

    def _a_move_save(self, name="", lib=None, params=None, id=None, **_):
        """Save a movement under a name.  Without `lib`, the movement running
        on the selection now (shape and knobs) is what gets saved."""
        if lib in (None, ""):
            sel = set(self.selected)
            row = next((f for f in reversed(self.fx) if f.get("lib") in motion_mod.ALL_KINDS
                        and (not sel or sel & set(f.get("heads") or []))), None)
            if row is None:
                raise ValueError("start a movement first (or give its shape) - then save it")
            lib, params = row["lib"], params if isinstance(params, dict) else row.get("params")
        mv = self._move_clean({"name": name, "lib": lib, "params": params, "id": id})
        old = next((i for i, m in enumerate(self.moves) if m["id"] == mv["id"]), None)
        if old is not None:
            self.moves[old] = mv
        else:
            if len(self.moves) >= self.MOVE_MAX:
                raise ValueError(f"{self.MOVE_MAX} moves is the most - delete one first")
            if any(m["name"].lower() == mv["name"].lower() for m in self.moves):
                raise ValueError(f"there is already a move called {mv['name']!r}")
            self.moves.append(mv)
        return {"move": mv, "summary": f"saved move {mv['name']!r}"}

    def _move_find(self, id=None, name=None) -> dict:
        for m in self.moves:
            if (id and m["id"] == str(id)) or (name and m["name"].lower() == str(name).lower()):
                return m
        raise ValueError(f"no move {id or name!r}")

    def _a_move_play(self, id=None, name=None, heads=None, group=None, **_):
        """Play a saved move on the selection (or heads / a group); a
        movement already on those lights makes way for it."""
        mv = self._move_find(id, name)
        rows = self._fx_targets(heads, group)
        nums = {h["head_no"] for h in rows}
        if not nums:
            raise ValueError("select the lights to move first")
        self.fx = [f for f in self.fx if not (f.get("lib") in motion_mod.ALL_KINDS
                                              and nums & set(f.get("heads") or []))]
        r = self._a_run_fx_named(mv["lib"], mv["params"], None, sorted(nums), None)
        for f in self.fx:
            if f["id"] == r["fx"]:
                f["move"] = mv["id"]
        return {**r, "move": mv["id"], "summary": f"{mv['name']} on {r['heads']} light(s)"}

    def _a_move_delete(self, id=None, name=None, **_):
        mv = self._move_find(id, name)
        self.moves = [m for m in self.moves if m is not mv]
        return {"summary": f"deleted move {mv['name']!r}"}

    def _a_move_rename(self, id=None, name="", **_):
        mv = self._move_find(id)
        new = str(name or "").strip()[:32]
        if not new:
            raise ValueError("a move needs a name")
        mv["name"] = new
        return {"move": mv, "summary": f"renamed to {new!r}"}

    def _quick_fx_params(self, name: str, params) -> dict:
        """The effect's own knobs a button keeps (speed, size, arc...)."""
        if not isinstance(params, dict):
            return {}
        known = fxlib_mod.defaults(name)
        out = {}
        for k, v in params.items():
            if k in known and v not in (None, ""):
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"not a number for {k}: {v!r}") from None
        return out

    def _quick_clean_custom(self, raw: dict, btn: dict) -> None:
        """A custom button: any mix of level, dim, colour, strobe, blackout,
        the same value on an attribute of every light, values captured
        per light, and up to four running effects."""
        if raw.get("level") not in (None, ""):
            btn["level"] = int(_clamp(raw["level"], 0, 100))
        if raw.get("dim") not in (None, ""):
            btn["dim"] = int(_clamp(raw["dim"], 0, 100))
        if raw.get("hz") not in (None, "", 0, "0"):
            btn["hz"] = float(_clamp(raw["hz"], 0.5, 25))
        if _truthy(raw.get("kill")):
            btn["kill"] = True
        keep = lambda r: r in ROLES and r not in FX_OUTPUT_ROLES \
            and r not in ("unused", "raw") and not r.startswith(("laser_", "fx_"))
        attrs = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
        clean_a = {}
        for k, v in attrs.items():
            role = _attr_role(k) or str(k)
            if keep(role):
                clean_a[role] = int(_clamp(v, 0, 65535))
        if clean_a:
            btn["attrs"] = clean_a
        values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
        clean_v: dict = {}
        for n, row in list(values.items())[:512]:
            if not isinstance(row, dict):
                continue
            r = {str(k): int(_clamp(v, 0, 65535)) for k, v in row.items() if keep(str(k))}
            if r:
                clean_v[str(int(n))] = r
        if clean_v:
            btn["values"] = clean_v
        fx_list = []
        for item in (raw.get("fx_list") or [])[:4]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if name.startswith("step:"):
                if not any(f["id"] == name[5:] for f in self._steps()):
                    raise ValueError(f"no step effect {name[5:]!r}")
                fx_list.append({"name": name, "params": {}})
                continue
            name = name.lower()
            if name not in fxlib_mod.FX:
                raise ValueError(f"unknown effect {name!r}")
            fx_list.append({"name": name, "params": self._quick_fx_params(name, item.get("params"))})
        if fx_list:
            btn["fx_list"] = fx_list
        if not any(k in btn for k in ("level", "dim", "hz", "kill", "attrs", "values", "fx_list")) \
                and not btn.get("colour"):
            raise ValueError("a custom button needs something to do: a level, a colour, "
                             "a strobe, a look captured from the programmer or an effect")

    def _quick_capture(self, raw: dict) -> dict:
        """Fill a custom button from what the programmer holds now for its
        lights: every value (colour, position, gobo...) and the effects
        running on them (circles, chases...) - make it on stage, then
        make it a button."""
        t = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        if not t or t.get("all"):
            heads = [n for n in self.programmer if self.programmer.get(n)] or \
                [n for f in self.fx for n in f.get("heads", [])]
        else:
            heads = self._target_heads(t)
        heads = sorted(set(heads))
        values = {}
        for n in heads:
            row = {k: v for k, v in (self.programmer.get(n) or {}).items()}
            if row:
                values[str(n)] = row
        fx_list = []
        for f in self.fx:
            if f.get("lib") in fxlib_mod.FX and set(f.get("heads") or []) & set(heads) and len(fx_list) < 4:
                fx_list.append({"name": f["lib"], "params": dict(f.get("params") or {})})
            elif f.get("steps") and set(f.get("heads") or []) & set(heads) and len(fx_list) < 4:
                fx_list.append({"name": "step:" + f["steps"], "params": {}})
        if not values and not fx_list:
            raise ValueError("nothing to capture - set a look or start an effect on the lights first")
        out = dict(raw, kind="custom", values=values, fx_list=fx_list)
        out.pop("capture", None)
        if not t or t.get("all"):
            out["target"] = {"heads": heads}
        return out

    def _a_quick_page(self, page=1, name="", **_):
        """Name a page of buttons ("Main", "Movers", "Drops")."""
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        name = str(name or "").strip()[:16]
        names = dict(getattr(self, "quick_names", {}) or {})
        if name:
            names[str(page)] = name
        else:
            names.pop(str(page), None)
        self.quick_names = names
        return {"page": page, "name": name, "summary": f"page {page}: {name or 'unnamed'}"}

    def _a_quick_move(self, page=1, slot=None, to_page=None, to_slot=None, copy=False, **_):
        """Move (or copy) a button to another slot; a button already there
        swaps places with it."""
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        to_page = int(_clamp(to_page if to_page not in (None, "") else page, 1, self.QUICK_PAGES))
        slot, to_slot = int(_clamp(slot, 1, self.QUICK_SLOTS)), int(_clamp(to_slot, 1, self.QUICK_SLOTS))
        src = next((b for b in self.quick if b["page"] == page and b["slot"] == slot), None)
        if not src:
            raise ValueError(f"no button at {page}.{slot}")
        if (page, slot) == (to_page, to_slot):
            return {"summary": "same place"}
        dst = next((b for b in self.quick if b["page"] == to_page and b["slot"] == to_slot), None)
        keep = [b for b in self.quick if b is not src and b is not dst]
        for key in (src["id"], dst and dst["id"]):
            if key:
                self._quick_off(key, force=True, fade=False)
        moved = self._quick_clean(dict(src), to_page, to_slot)
        keep.append(moved)
        if _truthy(copy):
            keep.append(src)
            if dst:
                pass                                   # a copy over a button replaces it
        elif dst:
            keep.append(self._quick_clean(dict(dst), page, slot))
        self.quick = sorted(keep, key=lambda b: (b["page"], b["slot"]))
        return {"id": moved["id"], "summary": f"button {'copied' if _truthy(copy) else 'moved'} "
                                             f"to {to_page}.{to_slot}"}

    def _a_quick_set(self, page=1, slot=None, button=None, clear=False, **_):
        """Create, change or remove the quick button at page/slot.
        slot="free": the first empty slot (from `page` on)."""
        if slot == "free":
            page, slot = self._quick_free_slot(page)
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        if slot is None:
            raise ValueError("slot is required")
        slot = int(_clamp(slot, 1, self.QUICK_SLOTS))
        key = f"q{page}-{slot}"
        self.quick = [b for b in self.quick if b["id"] != key]
        self.quick_active.pop(key, None)
        if _truthy(clear) or button is None:
            return {"id": key, "summary": f"cleared button {page}.{slot}"}
        raw = dict(button)
        if _truthy(raw.get("capture")):
            raw = self._quick_capture(raw)
        btn = self._quick_clean(raw, page, slot)
        self.quick.append(btn)
        self.quick.sort(key=lambda b: (b["page"], b["slot"]))
        return {"id": key, "button": btn, "summary": f"button {page}.{slot}: {btn['label']}"}

    def _quick_free_slot(self, page=None) -> tuple[int, int]:
        """The first empty (page, slot), from `page` on (default page 1)."""
        start = int(_clamp(page or 1, 1, self.QUICK_PAGES))
        used = {(b["page"], b["slot"]) for b in self.quick}
        for p in range(start, self.QUICK_PAGES + 1):
            for sl in range(1, self.QUICK_SLOTS + 1):
                if (p, sl) not in used:
                    return p, sl
        raise ValueError("no empty button left")

    def _a_group_flash(self, heads=None, group=None, auto=None, down=True, level=100, **_):
        """Flash some lights while held (a group chip held down): full up
        over everything else, the same as a flash button, never stored."""
        key = "flash:chip"
        if not _truthy(down):
            self.quick_active.pop(key, None)
            return {"active": False, "summary": "flash released"}
        target = {"group": group} if group is not None else {"auto": auto} if auto else {"heads": heads or []}
        nums = self._heads_for_target(target)
        if not nums:
            raise ValueError("no lights to flash")
        btn = {"id": key, "kind": "flash", "mode": "hold", "level": int(_clamp(level, 0, 100)),
               "label": "Flash", "target": target}
        self.quick_active[key] = {"since": time.monotonic(), "heads": nums, "owners": {"hand"},
                                  "fx_ids": [], "btn": btn}
        return {"active": True, "heads": len(nums), "summary": f"flashing {len(nums)} light(s)"}

    def _a_quick_from_laser(self, heads=None, label="", page=None, **_):
        """A laser button from the laser look in the programmer (pattern,
        colour, size, movement, beams): on / off, output only while armed."""
        nums = [int(n) for n in (heads or [])]
        lasers = [h for h in self.patch if h["head_no"] in nums and any(r in LASER_ROLES for r in h["map"])]
        if not lasers:
            raise ValueError("select a laser first")
        values = {}
        for h in lasers:
            for role, v in (self.programmer.get(h["head_no"]) or {}).items():
                if role in LASER_ROLES and isinstance(v, (int, float)):
                    values.setdefault(role, int(v))
        spot = self._quick_free_slot(page)
        r = self._a_quick_set(page=spot[0], slot=spot[1], button={
            "kind": "laser", "mode": "latch", "label": str(label or "Laser look")[:24],
            "target": {"heads": [h["head_no"] for h in lasers]}, "values": values})
        r["summary"] = f"laser button {spot[0]}.{spot[1]}: {r['button']['label']}"
        return r

    def _quick_heads(self, btn: dict) -> list[int]:
        t = btn.get("target") or {}
        heads = self._heads_for_target(t, fx=btn["kind"] in self.FX_BUTTONS)
        return self._split_heads(heads, t.get("split"))

    def _head_class(self, h: dict) -> str:
        """'light', 'laser' or 'sfx' (see fixlib.apply_fx)."""
        return fixture_kind.describe(h).get("class", "light")

    def _lights_only(self, heads) -> list[int]:
        by = {h["head_no"]: h for h in self.patch}
        return [n for n in heads if n in by and self._head_class(by[n]) == "light"]

    def _heads_for_target(self, t: dict, fx: bool = False) -> list[int]:
        """Heads for a target: {group}, {heads}, {type} or all.  A light
        button only ever reaches lights; an FX button only effects."""
        out = self._target_heads(t)
        by = {h["head_no"]: h for h in self.patch}
        return [n for n in out if (self._head_class(by[n]) != "light") == fx]

    def _target_heads(self, t: dict) -> list[int]:
        patched = [h["head_no"] for h in self.patch]
        if t.get("auto"):
            g = next((g for g in self._auto_groups() if g["key"] == t["auto"]), None)
            return list(g["heads"]) if g else []
        if t.get("group") is not None:
            for g in self.groups:
                if g["n"] == t["group"]:
                    return [h for h in g["heads"] if h in patched]
            return []
        if t.get("heads"):
            return [h for h in t["heads"] if h in patched]
        if t.get("type"):
            want = t["type"]
            return [h["head_no"] for h in self.patch
                    if fixture_kind.describe(h)["type"] == want
                    or fixture_kind.design_role(h) == want]
        return patched

    def _quant_of(self, btn: dict) -> float:
        """Beats a press of this button waits for: its own setting, else
        the desk's (Buttons page -> On the beat)."""
        if btn["kind"] in self.NO_QUANT:
            return 0.0
        q = btn.get("quant")
        return float(self.__dict__.get("quick_quant", 0.0) if q is None else q)

    def _a_quick_quant(self, beats=0, **_):
        """Every button (that doesn't say otherwise) fires on the next
        beat (1), bar (4), half beat or 2 beats; 0 = as pressed."""
        try:
            q = float(beats or 0)
        except (TypeError, ValueError):
            q = -1.0
        if q not in self.QUANTS:
            raise ValueError("beats is 0 (as pressed), 0.5, 1, 2 or 4")
        self.quick_quant = q
        word = {0.0: "as pressed", 0.5: "on the next half beat", 1.0: "on the next beat",
                2.0: "on the next 2 beats", 4.0: "on the next bar"}[q]
        return {"quant": q, "summary": f"buttons fire {word}"}

    def _quick_pending_tick(self, now: float | None = None) -> None:
        """Fire the presses waiting for their beat, and let go of the ones
        released before it came (after a quarter beat on)."""
        pend = self.__dict__.get("quick_pending")
        rel = self.__dict__.get("quick_rel")
        if not pend and not rel:
            return
        now = time.monotonic() if now is None else now
        b = self._tempo().beats(now)
        rel = self.__dict__.setdefault("quick_rel", {})
        for key, p in list((pend or {}).items()):
            if b + 1e-9 >= p["at"]:
                pend.pop(key, None)
                try:
                    self._a_quick_press(id=key, down=True, immediate=True)
                except ValueError:
                    continue
                if p.get("up"):
                    rel[key] = p["at"] + 0.25
        for key, at in list(rel.items()):
            if b + 1e-9 >= at:
                rel.pop(key, None)
                try:
                    self._a_quick_press(id=key, down=False, immediate=True)
                except ValueError:
                    pass

    def _a_quick_press(self, id=None, page=None, slot=None, down=True, immediate=False, **_):
        """Press (down=True) or release (down=False) a quick button.  With
        a beat setting (quant) the press waits for the next beat / bar."""
        key = str(id) if id else f"q{int(page)}-{int(slot)}"
        btn = next((b for b in self.quick if b["id"] == key), None)
        if not btn:
            raise ValueError(f"no button {key}")
        down = _truthy(down)
        q = 0.0 if immediate else self._quant_of(btn)
        pend = self.__dict__.setdefault("quick_pending", {})
        if q > 0:
            if down:
                clock = self._tempo()
                b = clock.beats(time.monotonic())
                late = self.QUANT_LATE_S * clock.bpm / 60.0
                since = b - math.floor(b / q) * q              # beats since the last grid line
                if since > late:
                    pend[key] = {"at": math.floor(b / q) * q + q, "up": False}
                    word = "bar" if q >= 4 else "beat" if q >= 1 else "half beat"
                    return {"id": key, "active": key in self.quick_active, "pending": True,
                            "summary": f"{btn['label']} on the next {word}"}
                # just after the beat: that was meant to be on it - now
            elif key in pend:
                pend[key]["up"] = True                          # a tap: on the beat, then off
                return {"id": key, "active": key in self.quick_active, "pending": True}
        kind, mode = btn["kind"], btn["mode"]
        if kind == "fxkill":
            if down:
                self._a_fx_kill()
            return {"id": key, "active": False, "summary": "all effects stopped, disarmed"}
        if kind == "arm":
            if not down:
                return {"id": key, "active": self._sfx_armed()}
            r = self._a_fx_arm(state=not self._sfx_armed())
            return {"id": key, "active": self._sfx_armed(), "summary": r["summary"]}
        if kind in ("sfx", "laser") and down and not self._sfx_armed():
            raise ValueError("ARM the effects first (the ARM switch in the top bar)")
        if kind in ("sfx", "fog", "laser") and mode == "tap":
            # a timed shot: runs its seconds (or the machine's limit, or
            # until a confetti tank is empty), whatever the finger does
            if down:
                self._quick_on(key, owner=f"tap:{time.monotonic():.3f}")
            return {"id": key, "active": key in self.quick_active,
                    "summary": f"{btn['label']} fired"}
        if kind in ("fader", "xy"):
            raise ValueError("a fader / XY tile is moved, not pressed")
        if kind in ("tempo", "cuelist", "estop"):
            if not down:
                return {"id": key, "active": False}
            if kind == "tempo":
                r = self._a_tempo_tap()
            elif kind == "cuelist":
                r = self._a_cue_go(playback=btn["playback"])
            else:
                r = self._a_fx_kill()
            return {"id": key, "active": False, "summary": r.get("summary") or btn["label"]}
        if kind == "macro":
            if not down:
                return {"id": key, "active": False}
            r = self._a_macro_run(id=btn["macro"])
            return {"id": key, "active": False, "summary": r["summary"]}
        if kind in ("go", "release", "preset"):
            if not down:
                return {"id": key, "active": False}
            if kind == "go":
                params = {"playback": btn["playback"]}
                if btn.get("cue"):
                    params["cue"] = btn["cue"]
                r = self._a_cue_go(**params)
            elif kind == "release":
                r = self._a_playback_release(playback=btn["playback"])
            else:
                r = self._a_include_preset(preset=btn["preset"])
            return {"id": key, "active": False, "summary": r.get("summary") or btn["label"]}
        active = key in self.quick_active
        if mode == "tap" and btn.get("seconds"):
            # a timed shot: on for its seconds, whatever the finger does
            if down:
                self._quick_on(key, owner="timer")
            return {"id": key, "active": key in self.quick_active,
                    "summary": f"{btn['label']} for {btn['seconds']:g} s"}
        if mode == "latch":
            if not down:
                return {"id": key, "active": active}
            turn_on = not active
        else:                                   # hold (tap behaves as hold)
            turn_on = down
        if turn_on:
            self._quick_on(key, owner="hand")
        else:
            self._quick_off(key, owner="hand", force=mode == "latch")
        on = key in self.quick_active
        return {"id": key, "active": on,
                "summary": f"{btn['label']} {'on' if on else 'off'}"}

    def _quick_on(self, key: str, owner: str = "hand") -> None:
        """Hold a button on for `owner` (a hand, or a timeline clip)."""
        btn = next((b for b in self.quick if b["id"] == key), None)
        if not btn:
            return
        run = self.quick_active.get(key)
        if run is not None and run.get("release_at"):
            # pressed again while fading out: back up from where it is
            now = time.monotonic()
            f = self._quick_factor(btn, run, now)
            run.pop("release_at", None)
            run.pop("release_from", None)
            fi = float(btn.get("fade_in") or 0)
            run["since"] = now - f * fi
        if btn["kind"] in ("sfx", "fog", "laser"):
            heads = self._quick_heads(btn)
            started = self._sfx_start(key, {"sfx": "fire", "fog": "fog", "laser": "laser"}[btn["kind"]],
                                     heads, owner, seconds=btn.get("seconds"),
                                     level=btn.get("level", 100), values=btn.get("values"))
            if started:
                self.quick_active.setdefault(key, {"since": time.monotonic(), "heads": heads,
                                                   "owners": set(), "fx_layer": True})
                self.quick_active[key]["owners"].add(owner)
            return
        if run is None:
            if btn.get("exclusive"):
                # a radio group: turning this one on turns the others off
                for other in [b for b in self.quick if b.get("exclusive") == btn["exclusive"]
                              and b["id"] != key and b["id"] in self.quick_active]:
                    self._quick_off(other["id"], force=True)
            run = {"since": time.monotonic(), "heads": self._quick_heads(btn),
                   "owners": set(), "fx_ids": []}
            if btn["kind"] == "move":
                mv = next((m for m in self.moves if m["id"] == btn.get("move")), None)
                wanted = [{"name": mv["lib"], "params": mv["params"]}] if mv else []
            elif btn["kind"] == "fx":
                wanted = [{"name": btn["fx"], "params": btn.get("params") or {}}]
            else:
                wanted = btn.get("fx_list") or []
            for item in wanted:
                try:
                    if item["name"].startswith("step:"):
                        r = self._a_step_fx_run(id=item["name"][5:], heads=run["heads"],
                                                beats=(item.get("params") or {}).get("beats"))
                    else:
                        r = self._a_run_fx_named(item["name"], item.get("params") or {}, None,
                                                 run["heads"], None)
                    run["fx_ids"].append(r.get("fx"))
                    self._fx_live(r.get("fx"))
                except ValueError:
                    pass
            self._quick_pace(btn, run)
            self.quick_active[key] = run
        if btn.get("seconds"):
            run["until"] = time.monotonic() + float(btn["seconds"])
        run.setdefault("owners", set()).add(owner)

    def _quick_off(self, key: str, owner: str = "hand", force: bool = False, fade: bool = True) -> None:
        """Let go for `owner`; the button stays on while anyone holds it.
        A button with a fade-out dims away over its time, then lets go."""
        run = self.quick_active.get(key)
        if run is None:
            return
        owners = run.setdefault("owners", set())
        owners.discard(owner)
        if run.get("fx_layer"):
            self._sfx_stop(key, owner, force=force)
        if owners and not force:
            return
        btn = next((b for b in self.quick if b["id"] == key), None)
        if fade and btn and btn.get("fade_out") and not run.get("release_at"):
            now = time.monotonic()
            run["release_from"] = self._quick_factor(btn, run, now)
            run["release_at"] = now
            return
        self.quick_active.pop(key, None)
        self._quick_stop_fx(run)

    @staticmethod
    def _quick_rate_value(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"not a speed: {value!r}") from None
        return round(min(4.0, max(0.25, v)), 2)

    def _quick_pace(self, btn: dict, run: dict) -> None:
        """Give a button's running effects its own speed."""
        ids = set(run.get("fx_ids") or [])
        for row in self.fx:
            if row.get("id") in ids:
                row["rate"] = float(btn.get("rate") or 1.0)
                row["free"] = bool(btn.get("free"))

    def _a_quick_rate(self, id=None, rate=None, free=None, **_):
        """Change a button's effect speed, live if it is running."""
        btn = next((b for b in self.quick if b["id"] == id), None)
        if btn is None:
            raise ValueError(f"no button {id!r}")
        if btn["kind"] not in ("fx", "move", "custom"):
            raise ValueError("only buttons that run effects have a speed")
        if rate is not None:
            v = self._quick_rate_value(rate)
            if v == 1.0:
                btn.pop("rate", None)
            else:
                btn["rate"] = v
        if free is not None:
            if free:
                btn["free"] = True
            else:
                btn.pop("free", None)
        run = self.quick_active.get(btn["id"])
        if run:
            self._quick_pace(btn, run)
        r = float(btn.get("rate") or 1.0)
        return {"id": btn["id"], "rate": r, "free": bool(btn.get("free")),
                "summary": f"{btn['label']} at {round(r * 100)}% speed" + (" (own speed)" if btn.get("free") else "")}

    def _quick_factor(self, btn: dict, run: dict, now: float) -> float:
        """0..1: how far in (fade-in) or still on (fade-out) a button is."""
        if run.get("release_at"):
            fo = float(btn.get("fade_out") or 0)
            left = 1.0 - (now - run["release_at"]) / fo if fo else 0.0
            return max(0.0, min(1.0, run.get("release_from", 1.0) * left))
        fi = float(btn.get("fade_in") or 0)
        return 1.0 if not fi else max(0.0, min(1.0, (now - run["since"]) / fi))

    def _quick_stop_fx(self, run: dict) -> None:
        ids = set(run.get("fx_ids") or []) | ({run["fx"]} if run.get("fx") else set())
        if ids:
            self.fx = [f for f in self.fx if f["id"] not in ids]

    def _quick_expire(self) -> None:
        """Buttons on a timer let go by themselves; faded-out ones finish."""
        now = time.monotonic()
        by_id = {b["id"]: b for b in self.quick}
        for key, run in list(self.quick_active.items()):
            if run.get("until") and now >= run["until"] and not run.get("fx_layer") and not run.get("release_at"):
                self._quick_off(key, owner="timer", force=True)
            elif run.get("release_at") and by_id.get(key) and \
                    now - run["release_at"] >= float(by_id[key].get("fade_out") or 0):
                self.quick_active.pop(key, None)
                self._quick_stop_fx(run)

    def _a_quick_release_all(self, **_):
        self.__dict__["quick_pending"] = {}
        self.__dict__["quick_rel"] = {}
        for key in list(self.quick_active):
            run = self.quick_active.pop(key)
            if run.get("fx_layer"):
                self._sfx_stop(key, "hand", force=True)
            self._quick_stop_fx(run)
        return {"summary": "all quick buttons released"}

    def _a_quick_fx_defaults(self, page=2, replace=False, **_):
        """Fill a page with this rig's special-effect buttons: ARM, Kill
        FX, and fire / fog / laser buttons per kind of effect."""
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        if any(b["page"] == page for b in self.quick) and not _truthy(replace):
            raise ValueError(f"page {page} already has buttons")
        kinds: dict[str, int] = {}
        for h in self.patch:
            if self._head_class(h) != "light":
                t = fixture_kind.describe(h)["type"]
                kinds[t] = kinds.get(t, 0) + 1
        if not kinds:
            raise ValueError("no lasers or special effects are patched")
        self.quick = [b for b in self.quick if b["page"] != page]
        plan = [{"kind": "arm", "label": "ARM FX"}, {"kind": "fxkill", "label": "KILL FX"}]
        names = {"confetti": "Confetti", "co2": "CO2", "flame": "Flame", "spark": "Sparks",
                 "sfx": "Effect", "laser": "Laser", "atmos": "Fog"}
        for t in sorted(kinds, key=lambda k: -kinds[k]):
            nm = names.get(t, t.title())
            target = {"type": t}
            if t == "confetti":
                plan += [{"kind": "sfx", "label": "Confetti shot", "mode": "tap", "target": target},
                         {"kind": "sfx", "label": "Confetti (hold)", "target": target}]
            elif t in ("co2", "flame", "spark", "sfx"):
                plan += [{"kind": "sfx", "label": f"{nm} 1 s", "mode": "tap", "seconds": 1, "target": target},
                         {"kind": "sfx", "label": f"{nm} (hold)", "target": target}]
            elif t == "laser":
                plan += [{"kind": "laser", "label": "Laser (hold)", "target": target},
                         {"kind": "laser", "label": "Laser on/off", "mode": "latch", "target": target}]
            elif t == "atmos":
                plan += [{"kind": "fog", "label": "Fog 10 s", "mode": "tap", "seconds": 10, "target": target},
                         {"kind": "fog", "label": "Fog (hold)", "mode": "hold", "seconds": 60, "target": target},
                         {"kind": "fog", "label": "Haze 30%", "mode": "latch", "level": 30,
                          "seconds": 600, "target": target}]
        made = []
        for slot, raw in enumerate(plan[:self.QUICK_SLOTS], start=1):
            btn = self._quick_clean(raw, page, slot)
            self.quick.append(btn)
            made.append(btn)
        self.quick.sort(key=lambda b: (b["page"], b["slot"]))
        return {"buttons": len(made), "summary": f"page {page}: {len(made)} FX buttons"}

    def _a_quick_defaults(self, page=None, replace=False, count=None, focus=None, free=False, **_):
        """Fill a page with buttons that suit this rig: flash and strobe
        per type of light, colour bumps, a kill, effects and GO.  `count`:
        only the N most useful ("build me 8 buttons"); `focus`: strobe /
        colour / effects / movement.  `free`: on the first empty page
        (else page 1)."""
        if page in (None, "", 0) and not _truthy(free):
            page = 1
        if page in (None, "", 0):
            used = {b["page"] for b in self.quick}
            page = next((p for p in range(1, self.QUICK_PAGES + 1) if p not in used), None)
            if page is None:
                raise ValueError("every page has buttons - say which page to replace")
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        if any(b["page"] == page for b in self.quick) and not _truthy(replace):
            raise ValueError(f"page {page} already has buttons")
        self.quick = [b for b in self.quick if b["page"] != page]
        types: dict[str, int] = {}
        for h in self.patch:
            role = fixture_kind.design_role(h)
            types[role] = types.get(role, 0) + 1
        label = {"spot": "Movers", "beam": "Beams", "wash": "Washes", "par": "PARs",
                 "bar": "Bars", "generic": "Lights"}
        plan = [{"kind": "flash", "label": "Flash all", "colour": "#ffffff"},
                {"kind": "strobe", "label": "Strobe all", "hz": 12},
                {"kind": "kill", "label": "Kill all"},
                {"kind": "strobe", "label": "Slow strobe", "hz": 4}]
        for role in sorted(types, key=lambda r: -types[r])[:4]:
            name = label.get(role, role.title())
            plan.append({"kind": "flash", "label": f"Flash {name}", "target": {"type": role}})
            plan.append({"kind": "strobe", "label": f"Strobe {name}", "target": {"type": role}, "hz": 10})
        for hexc, nm in (("#ff0000", "Red"), ("#0033ff", "Blue"), ("#ffffff", "White"),
                         ("#ff00cc", "Magenta"), ("#00ffaa", "Cyan"), ("#ffb000", "Amber")):
            plan.append({"kind": "colour", "label": f"All {nm}", "colour": hexc, "mode": "hold"})
        movers = any("pan" in h["map"] or "tilt" in h["map"] for h in self.patch)
        if focus and count in (None, "", 0):
            count = self.QUICK_SLOTS
        for fx_name in ("rainbow", "dimmer_chase", "sparks", "circle"):
            if fx_name in fxlib_mod.FX and (fx_name != "circle" or movers or count is None):
                plan.append({"kind": "fx", "label": fxlib_mod.FX[fx_name]["label"], "fx": fx_name})
        plan.append({"kind": "go", "label": "GO PB1", "playback": 1})
        plan.append({"kind": "blackout", "label": "Blackout (hold)"})
        if count not in (None, "", 0):
            plan = self._quick_ranked(plan, int(_clamp(count, 1, self.QUICK_SLOTS)), focus, movers)
        made = []
        for slot, raw in enumerate(plan[:self.QUICK_SLOTS], start=1):
            btn = self._quick_clean(raw, page, slot)
            self.quick.append(btn)
            made.append(btn)
        self.quick.sort(key=lambda b: (b["page"], b["slot"]))
        summary = f"page {page}: {len(made)} buttons for this rig"
        fx_page = 2 if page != 2 else 3
        if any(self._head_class(h) != "light" for h in self.patch) \
                and not any(b["page"] == fx_page for b in self.quick):
            r = self._a_quick_fx_defaults(page=fx_page)
            summary += f"; {r['summary']}"
        return {"buttons": len(made), "summary": summary}

    FOCUS = {"strobe": ("strobe", "flash", "blackout", "kill"),
             "colour": ("colour", "fx:colour"), "effects": ("fx",),
             "movement": ("fx:position",)}

    def _quick_ranked(self, plan: list[dict], count: int, focus, movers: bool) -> list[dict]:
        """The `count` most useful of a page plan: the busking basics first
        (flash, strobe, blackout), then colours, a chase, movement, the
        same per type of light; `focus` keeps one family."""
        def fam(b):
            if b["kind"] == "fx":
                return "fx:" + (fxlib_mod.FX.get(b["fx"]) or {}).get("group", "")
            return b["kind"]
        f = str(focus or "").strip().lower().rstrip("s")
        f = {"color": "colour", "effect": "effects", "fx": "effects", "move": "movement", "mover": "movement",
             "position": "movement", "flash": "strobe", "colour": "colour", "strobe": "strobe",
             "movement": "movement"}.get(f, f)
        if f and f not in self.FOCUS:
            raise ValueError("focus is strobe, colour, effects or movement")
        if f == "movement" and not movers:
            raise ValueError("no moving lights are patched - nothing to move")
        pool = list(plan)
        if f == "movement" or (f == "effects" and movers):
            for name in ("circle", "figure_eight", "pan_sweep", "tilt_bounce", "fan_pan"):
                if movers and name in fxlib_mod.FX and not any(b.get("fx") == name for b in pool):
                    pool.append({"kind": "fx", "label": fxlib_mod.FX[name]["label"], "fx": name})
        if f == "colour":
            for name in ("colour_chase", "alternate", "fan"):
                if name in fxlib_mod.FX:
                    pool.append({"kind": "fx", "label": fxlib_mod.FX[name]["label"], "fx": name})
        if f:
            keep = self.FOCUS[f]
            pool = [b for b in pool if b["kind"] in keep or fam(b) in keep
                    or (b["kind"] == "fx" and "fx" in keep)]
        order = ["Flash all", "Strobe all", "Blackout (hold)", "All Red", "All Blue", "All White",
                 "Dimmer chase", "Circle", "Rainbow", "Slow strobe", "All Magenta", "Kill all",
                 "All Amber", "All Cyan", "Sparks", "GO PB1"]
        rank = {name: i for i, name in enumerate(order)}
        pool.sort(key=lambda b: rank.get(b["label"], len(order)))
        if not pool:
            raise ValueError("this rig has nothing for those buttons")
        return pool[:count]

    def _override_vals(self, skip_parked: bool = False) -> dict:
        """Per-head overrides from the quick buttons that are held now,
        from the FX layer (the only way an effect's output moves), from
        the sound, group masters, highlight and park - park last: a parked
        light stays where it was parked whatever else is going on."""
        self._quick_pending_tick()
        out = self._quick_override_vals()
        if self.fx_runs or self.fx_armed_until:
            for n, sets in self._sfx_override_vals().items():
                out.setdefault(n, {}).setdefault("set", {}).update(sets)
        self._sound_overrides(out)
        self._desk_overrides(out, skip_parked=skip_parked)
        return out

    def _quick_override_vals(self) -> dict:
        if not self.quick_active:
            return {}
        self._quick_expire()
        by_id = {b["id"]: b for b in self.quick}
        out: dict[int, dict] = {}
        heads = {h["head_no"]: h for h in self.patch}
        for key, run in sorted(self.quick_active.items(), key=lambda kv: kv[1]["since"]):
            btn = by_id.get(key) or run.get("btn")
            if not btn:
                continue
            kind = btn["kind"]
            f = self._quick_factor(btn, run, time.monotonic())
            for n in run["heads"]:
                head = heads.get(n)
                if head is None:
                    continue
                o = out.setdefault(n, {})
                if kind in ("flash", "strobe"):
                    o["level"] = max(o.get("level") or 0, round(btn.get("level", 100) * (f if kind == "flash" else 1.0)))
                    gate = self._shutter_role(head)
                    if gate and not any(r in HTP_ROLES for r in head["map"]):
                        o.setdefault("set", {})[gate] = self._open_value(head, gate)
                    if kind == "strobe":
                        o["strobe"] = max(o.get("strobe") or 0, btn.get("hz", 10))
                    if btn.get("colour"):
                        o.setdefault("set", {}).update(self._colour_values(head, btn["colour"]))
                elif kind == "colour":
                    o.setdefault("set", {}).update(self._colour_values(head, btn["colour"]))
                elif kind == "kill" and f < 1.0:
                    o["cap"] = round(100 * (1 - f)) if o.get("cap") is None else min(o["cap"], round(100 * (1 - f)))
                elif kind in ("kill", "blackout"):
                    o["kill"] = True
                elif kind == "custom":
                    self._quick_custom_over(btn, head, o, f)
        return out

    def _quick_custom_over(self, btn: dict, head: dict, o: dict, f: float = 1.0) -> None:
        """One custom button's part of a light's override.  Brightness never
        goes in as a forced value (that would skip blackout and the master):
        it becomes the button's level floor instead."""
        n = head["head_no"]
        sets = o.setdefault("set", {})
        level = btn.get("level")
        for src in (btn.get("attrs") or {}, (btn.get("values") or {}).get(str(n)) or {}):
            for role, v in src.items():
                if role in HTP_ROLES:
                    level = max(level or 0, int(v))
                elif role in head["map"]:
                    sets[role] = int(v)
        if btn.get("colour"):
            sets.update(self._colour_values(head, btn["colour"]))
        if level is not None or btn.get("hz"):
            if level is not None:
                o["level"] = max(o.get("level") or 0, round(int(level) * f))
            gate = self._shutter_role(head)
            if gate and not any(r in HTP_ROLES for r in head["map"]) and (level or btn.get("hz")):
                sets[gate] = self._open_value(head, gate)
        if btn.get("hz"):
            o["strobe"] = max(o.get("strobe") or 0, btn["hz"])
        if btn.get("dim") is not None:
            dim = round(100 - (100 - btn["dim"]) * f)       # eases down to the dim level
            o["cap"] = min(o["cap"], dim) if o.get("cap") is not None else dim
        if btn.get("kill"):
            if f >= 1.0:
                o["kill"] = True
            else:
                cap = round(100 * (1 - f))
                o["cap"] = min(o["cap"], cap) if o.get("cap") is not None else cap
        if not sets:
            o.pop("set", None)

    def _gates(self) -> dict:
        """The value that closes each head's shutter, per the profile:
        0 unless the profile says 0 is already open (then None)."""
        cache = getattr(self, "_gate_cache", None)
        if cache and cache[0] == self.patch_rev:
            return cache[1]
        gates = {}
        for h in self.patch:
            role = self._shutter_role(h)
            if role is None:
                continue
            light_from = self._profile_levels(h)[1].get(role)
            gates[h["head_no"]] = None if light_from == 0 else 0
        self._gate_cache = (self.patch_rev, gates)
        return gates

    def _rests(self) -> dict:
        """{head_no: {gate role: open value}} for heads that HAVE a dimmer
        and a shutter whose open value is not 0: the shutter rests open
        unless something drives it, and the dimmer does the dark."""
        cache = getattr(self, "_rest_cache", None)
        if cache and cache[0] == self.patch_rev:
            return cache[1]
        rests = {}
        for h in self.patch:
            if not self._intensity_roles(h):
                continue
            role = self._shutter_role(h)
            if role is None:
                continue
            value = self._open_value(h, role)
            if value > 0:
                rests[h["head_no"]] = {role: value}
        # An LED head WITH a dimmer rests at white: the dimmer is the
        # brightness, and colour channels left at 0 make Full give no light at
        # all (a Chauvet Intimidator Wave 360 at full dimmer and RGBW 0 is
        # dark).  Anything that sets a colour - the picker, a look, a cue, an
        # effect - still wins; this is only what an untouched head shows.
        for h in self.patch:
            if self._head_class(h) != "light" or not self._intensity_roles(h):
                continue
            mix = [r for r in ("red", "green", "blue") if r in h["map"]]
            if len(mix) == 3:
                # as a GROUP: once anything sets any colour, none of this applies
                rests.setdefault(h["head_no"], {})["_mix"] = mix
        # a channel the operator found must sit at a value for the real light
        # to light (a lamp / "open" control the file did not mark) rests there
        for h in self.patch:
            if self._head_class(h) != "light":
                continue
            gate = self._shutter_role(h)
            for role, rng in self.head_ranges(h).items():
                if (role != gate and role in h["map"] and rng.get("open_user")
                        and rng.get("open_from") is not None):
                    rests.setdefault(h["head_no"], {})[role] = int(rng["open_from"])
        # an effect's output rests at its own "off" value
        for h in self.patch:
            for role in FX_OUTPUT_ROLES.intersection(h["map"]):
                off = (self.head_ranges(h).get(role) or {}).get("off_value") or 0
                rests.setdefault(h["head_no"], {})[role] = int(off)
        self._rest_cache = (self.patch_rev, rests)
        return rests

    # -- control tiles --------------------------------------------------------
    def _quick_tile(self, id, kind) -> dict:
        btn = next((b for b in self.quick if b["id"] == str(id)), None)
        if not btn or btn["kind"] != kind:
            raise ValueError(f"no {kind} tile {id!r}")
        return btn

    def _a_quick_fader(self, id=None, level=None, **_):
        """Move a fader tile: the master, the Speed master, a playback or a
        group master, 0-100."""
        btn = self._quick_tile(id, "fader")
        v = _clamp(level if level is not None else 0, 0, 100)
        c = btn["control"]
        if c["what"] == "master":
            return self._a_master(level=v)
        if c["what"] == "speed":
            return self._a_speed_master(pct=max(10, v * 2))           # 50 = 1x
        if c["what"] == "playback":
            return self._a_playback_level(playback=c["n"], level=v)
        return self._a_group_master(group=c["n"], level=v)

    def _a_quick_xy(self, id=None, pan=None, tilt=None, **_):
        """Move an XY tile: its lights' pan / tilt, 0-255 each."""
        btn = self._quick_tile(id, "xy")
        heads = [n for n in self._target_heads(btn.get("target") or {"all": True})
                 if any(r in ("pan", "tilt") for r in self._head(n)["map"])]
        if not heads:
            raise ValueError("no moving lights on this tile")
        return self._a_set_position(pan=pan, tilt=tilt, unit="255", heads_in=heads)
