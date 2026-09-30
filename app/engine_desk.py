"""Desk tools: highlight and solo (find the lights you are working on),
park (a light held where it is - dark, or as it is now - whatever the
cues say), group masters (a fader per group).

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import time

from app.engine_base import UNDO_LIMIT, _truthy
from app.engine_support import HTP_ROLES


class DeskMixin:
    # -- highlight / solo -------------------------------------------------
    def _a_highlight(self, state=None, solo=None, **_):
        """The selected lights at full, open white on top of everything
        (solo: and every other light dark) - to find them.  Off again
        leaves the show as it was; nothing is recorded."""
        hl = self.__dict__.setdefault("highlight", {"on": False, "solo": False})
        if solo is not None:
            hl["solo"] = _truthy(solo)
        hl["on"] = (not hl["on"]) if state is None else _truthy(state)
        what = "solo" if hl["solo"] else "highlight"
        return {"highlight": dict(hl), "summary": f"{what} {'on' if hl['on'] else 'off'}"}

    # -- park -------------------------------------------------------------
    def _a_park(self, heads=None, mode="dark", **_):
        """Park lights: `dark` (held off whatever runs) or `hold` (frozen as
        they are now).  Saved with the show; unpark lets them go."""
        nums = [int(n) for n in heads] if heads else [h["head_no"] for h in self._require_selection()]
        by_no = {h["head_no"]: h for h in self.patch}
        nums = [n for n in nums if n in by_no]
        if not nums:
            raise ValueError("which lights?")
        mode = str(mode or "dark").lower()
        if mode not in ("dark", "hold"):
            raise ValueError("mode is dark or hold")
        parked = self.__dict__.setdefault("parked", {})
        if mode == "hold":
            # what they are doing right now, as the wire has it
            now = time.monotonic()
            prog = self._programmer_now(now)
            pbs = self._active_playbacks(now)
            fxv = self._fx_values(now)
            over = self._override_vals(skip_parked=True)
            for n in nums:
                vals = self._resolve_head(by_no[n], prog, pbs, fxv.get(n), over.get(n))
                parked[str(n)] = {"mode": "hold", "values": {r: int(v) for r, v in vals.items()
                                                             if isinstance(v, (int, float))}}
        else:
            for n in nums:
                parked[str(n)] = {"mode": "dark"}
        return {"parked": sorted(int(k) for k in parked),
                "summary": f"parked {len(nums)} light(s) {'dark' if mode == 'dark' else 'as they are'}"}

    def _a_unpark(self, heads=None, all=False, **_):
        parked = self.__dict__.setdefault("parked", {})
        if _truthy(all):
            n = len(parked)
            parked.clear()
            return {"parked": [], "summary": f"unparked {n} light(s)"}
        nums = [int(n) for n in heads] if heads else [h["head_no"] for h in self._require_selection()]
        gone = [n for n in nums if parked.pop(str(n), None) is not None]
        return {"parked": sorted(int(k) for k in parked), "summary": f"unparked {len(gone)} light(s)"}

    @staticmethod
    def _clean_parked(raw) -> dict:
        out = {}
        for k, p in (raw or {}).items() if isinstance(raw, dict) else []:
            try:
                n = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(p, dict) and p.get("mode") == "hold":
                vals = {str(r): int(v) for r, v in (p.get("values") or {}).items() if isinstance(v, (int, float))}
                out[str(n)] = {"mode": "hold", "values": vals}
            else:
                out[str(n)] = {"mode": "dark"}
        return out

    # -- group masters ----------------------------------------------------
    def _a_group_master(self, group=None, level=None, **_):
        """A group's master: its lights at `level`% of whatever they do."""
        g = next((x for x in self.groups if x["n"] == int(group)), None) if group is not None else None
        if g is None:
            raise ValueError(f"no group {group!r}")
        lv = max(0, min(100, int(round(float(level if level is not None else 100)))))
        if lv >= 100:
            g.pop("master", None)
        else:
            g["master"] = lv
        return {"group": g["n"], "master": lv, "summary": f"{g['name']} at {lv}%"}

    # -- into the overrides ----------------------------------------------
    def _desk_overrides(self, out: dict, skip_parked: bool = False) -> None:
        lights = [h for h in self.patch if self._head_class(h) == "light"]
        # group masters: a share of the level, like the grand master
        for g in self.groups:
            m = g.get("master")
            if m is None:
                continue
            for n in g["heads"]:
                o = out.setdefault(n, {})
                o["scale"] = round(o.get("scale", 100) * m / 100.0, 1)
        hl = self.__dict__.get("highlight")
        if hl and hl.get("on"):
            sel = set(self.selected)
            for h in lights:
                o = out.setdefault(h["head_no"], {})
                if h["head_no"] in sel:
                    o["level"] = 100
                    o.pop("cap", None)
                    o.pop("kill", None)
                    o.pop("scale", None)
                    sets = o.setdefault("set", {})
                    sets.update(self._white_values(h))
                    gate = self._shutter_role(h)
                    if gate:
                        sets[gate] = self._open_value(h, gate)
                elif hl.get("solo"):
                    o["kill"] = True
        if skip_parked:
            return
        for key, p in (self.__dict__.get("parked") or {}).items():
            n = int(key)
            o = out.setdefault(n, {})
            if p["mode"] == "dark":
                o["kill"] = True
                continue
            vals = p.get("values") or {}
            level = max([v for r, v in vals.items() if r in HTP_ROLES] or [None], key=lambda x: -1 if x is None else x)
            if level is not None:
                o["level"] = o["cap"] = int(level)
            o.pop("kill", None)
            o.pop("scale", None)
            o["set"] = {r: v for r, v in vals.items() if r not in HTP_ROLES}

    # -- macros: command lines played in one go -----------------------------
    def _macros(self) -> list[dict]:
        lst = self.__dict__.get("macros")
        if lst is None:
            lst = self.macros = []
        return lst

    @staticmethod
    def _clean_macro(raw: dict, ident: str) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        lines = raw.get("lines")
        if isinstance(lines, str):
            lines = lines.splitlines()
        lines = [str(x).strip()[:200] for x in (lines or []) if str(x).strip() and not str(x).strip().startswith("#")]
        if not lines:
            raise ValueError("a macro needs at least one command line")
        return {"id": ident, "name": (str(raw.get("name") or "").strip() or f"Macro {ident[1:]}")[:30], "lines": lines[:50]}

    def _clean_macro_list(self, raw) -> list[dict]:
        out = []
        for i, m in enumerate(raw or []):
            try:
                out.append(self._clean_macro(m, str((m or {}).get("id") or f"m{i + 1}")))
            except (ValueError, TypeError, AttributeError):
                continue
        return out[:64]

    def _a_macro_save(self, macro=None, id=None, **_):
        """Save a macro: a name and command lines (as typed in the command
        bar: `1-4 red`, `cue 3 go`, `master 60` ...)."""
        lst = self._macros()
        ident = str(id or (macro or {}).get("id") or "")
        if not ident:
            nums = [int(m["id"][1:]) for m in lst if m["id"][1:].isdigit()]
            ident = f"m{max(nums, default=0) + 1}"
            if len(lst) >= 64:
                raise ValueError("at most 64 macros")
        clean = self._clean_macro(macro or {}, ident)
        # check every line parses before keeping it
        for line in clean["lines"]:
            self._a_run_command(text=line, dry=True)
        self.macros = [m for m in lst if m["id"] != ident] + [clean]
        return {"id": ident, "macros": self.macros, "summary": f"macro {clean['name']}: {len(clean['lines'])} line(s)"}

    def _a_macro_delete(self, id=None, **_):
        lst = self._macros()
        if not any(m["id"] == str(id) for m in lst):
            raise ValueError(f"no macro {id!r}")
        self.macros = [m for m in lst if m["id"] != str(id)]
        return {"macros": self.macros, "summary": "macro deleted"}

    def _a_macro_run(self, id=None, **_):
        """Play a macro's lines in order: all of them or none (one undo step)."""
        m = next((x for x in self._macros() if x["id"] == str(id) or x["name"].lower() == str(id).lower()), None)
        if m is None:
            raise ValueError(f"no macro {id!r}")
        before = self._undo_state()
        n0 = len(self._undo)
        said = []
        try:
            for line in m["lines"]:
                r = self._a_run_command(text=line)
                said += r.get("transcript") or []
        except ValueError as exc:
            self._restore_state(before)
            del self._undo[n0:]
            raise ValueError(f"{m['name']}: {exc}") from None
        if len(self._undo) > n0:
            # the lines' own steps become the macro's one
            del self._undo[n0:]
            self._undo.append({"action": "macro", "label": m["name"], "state": before, "at": self._clock()})
            if len(self._undo) > UNDO_LIMIT:
                del self._undo[0]
            self._undo_label = m["name"]
        return {"macro": m["id"], "transcript": said, "summary": f"{m['name']}: " + ("; ".join(said[:3]) or "done")}
