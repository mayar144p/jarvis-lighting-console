"""Quick buttons: their pages, page layouts, button profiles (a set of
pages per show), placing and moving buttons, a row of colour buttons -
and My moves.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.  Split from
engine_quick.py (which plays the buttons) to keep each file readable.
"""
from __future__ import annotations

import json
import os

from app import motion as motion_mod
from app.engine_base import _clamp, _hex_or_none, _truthy


class QuickPagesMixin:

    def _quick_layouts(self) -> dict:
        return dict(self.__dict__.get("quick_layout") or {})

    @staticmethod
    def _quick_phantoms(layout: dict, quick: list, names: dict) -> dict:
        """A page grid with no page under it (no button, no name, past the
        first four) is dropped: "Every page" used to write a grid on all 32,
        and an old show then listed 32 empty pages that couldn't go."""
        used = {str(b.get("page")) for b in quick} | {k for k, v in (names or {}).items() if v}
        empty = {k for k in layout if k not in used and int(k) > 4}
        # one or two grids made ready before their buttons are kept: only
        # the "every page" stamp (grids on a run of empty pages) goes
        if len(empty) < 8:
            return layout
        return {k: v for k, v in layout.items() if k not in empty}

    def _quick_layout_load(self, raw) -> dict:
        """A show file's page grids, checked (a bad entry is dropped)."""
        out: dict = {}
        for k, v in (raw.items() if isinstance(raw, dict) else []):
            try:
                p = int(k)
                c = int(v.get("cols", 8))
                if not 1 <= p <= self.QUICK_PAGES or c not in self.QUICK_COLS:
                    continue
                hgt = v.get("height") if v.get("height") in self.QUICK_HEIGHTS else self.LAYOUT_DEFAULT["height"]
                out[str(p)] = {"cols": c, "rows": int(_clamp(v.get("rows", 3), 1, self.QUICK_SLOTS // c)), "height": hgt}
            except (AttributeError, TypeError, ValueError):
                continue
        return out

    def _a_quick_layout(self, page=1, cols=None, rows=None, height=None, all_pages=False, **_):
        """A page's grid: how many buttons across (4-12; fewer = bigger),
        how many rows, and how tall a row is (s / m / l / xl).  Buttons keep
        their numbers, so they flow into the new grid in order."""
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        cur = {**self.LAYOUT_DEFAULT, **self._quick_layouts().get(str(page), {})}
        if cols not in (None, ""):
            c = int(cols)
            if c not in self.QUICK_COLS:
                raise ValueError(f"buttons across is one of {', '.join(map(str, self.QUICK_COLS))}")
            cur["cols"] = c
        if height not in (None, ""):
            if height not in self.QUICK_HEIGHTS:
                raise ValueError(f"row height is one of {', '.join(self.QUICK_HEIGHTS)}")
            cur["height"] = height
        if rows not in (None, ""):
            cur["rows"] = int(rows)
        # never more buttons than a page holds, never fewer rows than one
        cur["rows"] = int(_clamp(cur["rows"], 1, self.QUICK_SLOTS // cur["cols"]))
        lay = self._quick_layouts()
        # "every page": the first four and every page that is there (buttons
        # or a name), and this one - never 32 empty pages
        there = {1, 2, 3, 4} | {b["page"] for b in self.quick} | {int(k) for k, v in (getattr(self, "quick_names", {}) or {}).items() if v}
        for p in (sorted(there | {page}) if _truthy(all_pages) else [page]):
            if cur == self.LAYOUT_DEFAULT:
                lay.pop(str(p), None)
            else:
                lay[str(p)] = dict(cur)
        self.quick_layout = lay
        hidden = [b["slot"] for b in self.quick if b["page"] == page and b["slot"] > cur["cols"] * cur["rows"]]
        note = f" ({len(hidden)} button(s) past the last row are kept, not shown)" if hidden else ""
        return {"page": page, "layout": cur, "hidden": hidden,
                "summary": f"page {page}: {cur['cols']} across, {cur['rows']} rows{note}"}

    def _a_quick_page(self, page=1, name="", delete=False, swap=None, tidy=False, **_):
        """Name a page of buttons ("Main", "Movers", "Drops"); `delete`
        empties it (its buttons, name and grid); `swap` trades places with
        another page - every go-to-page button, timeline clip and sound
        trigger follows its buttons to their new place.  `tidy`: every page
        with no buttons goes (its name and grid), in one step."""
        if _truthy(tidy):
            have = {str(b["page"]) for b in self.quick}
            names = dict(getattr(self, "quick_names", {}) or {})
            lay = self._quick_layouts()
            gone = sorted({int(k) for k in (set(names) | set(lay)) if k not in have and int(k) > 1})
            self.quick_names = {k: v for k, v in names.items() if k in have or k == "1"}
            self.quick_layout = {k: v for k, v in lay.items() if k in have or k == "1"}
            return {"removed": gone, "summary": f"{len(gone)} empty page{'' if len(gone) == 1 else 's'} removed" if gone else "No empty pages"}
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        if _truthy(delete):
            gone = [b for b in self.quick if b["page"] == page]
            for b in gone:
                self._quick_off(b["id"], force=True, fade=False)
            self.quick = [b for b in self.quick if b["page"] != page]
            self.quick_names = {k: v for k, v in (getattr(self, "quick_names", {}) or {}).items() if k != str(page)}
            self.quick_layout = {k: v for k, v in self._quick_layouts().items() if k != str(page)}
            return {"page": page, "summary": f"page {page} emptied ({len(gone)} button(s))"}
        if swap not in (None, ""):
            return self._quick_swap_pages(page, int(_clamp(swap, 1, self.QUICK_PAGES)))
        name = str(name or "").strip()[:16]
        names = dict(getattr(self, "quick_names", {}) or {})
        if name:
            names[str(page)] = name
        else:
            names.pop(str(page), None)
        self.quick_names = names
        return {"page": page, "name": name, "summary": f"page {page}: {name or 'unnamed'}"}

    # ---- button profiles: whole sets of pages ("Default", "Halloween") ----
    # The profile in use IS self.quick / quick_names / quick_layout; the
    # others wait in quick_profiles["sets"], each with the same three.
    PROFILE_DEFAULT = "Default"

    def _profiles(self) -> dict:
        p = self.__dict__.get("quick_profiles")
        if not isinstance(p, dict) or not p.get("order"):
            p = {"active": self.PROFILE_DEFAULT, "order": [self.PROFILE_DEFAULT], "sets": {}}
            self.__dict__["quick_profiles"] = p
        return p

    def _profiles_saved(self) -> dict:
        """The profiles for a show file / undo (the one in use is self.quick)."""
        p = self._profiles()
        return {"active": p["active"], "order": list(p["order"]),
                "sets": json.loads(json.dumps(p["sets"], default=str)),
                # the show each AI-made profile plays (app/showkit.py): its design
                "shows": json.loads(json.dumps(p.get("shows") or {}, default=str))}

    def _profiles_load(self, raw) -> dict:
        """Profiles from a show file: tidied, the active one's buttons are
        the show's own `quick` (loaded the usual way)."""
        if not isinstance(raw, dict):
            raw = {}
        order = [str(n).strip()[:24] for n in (raw.get("order") or []) if str(n).strip()]
        order = list(dict.fromkeys(order)) or [self.PROFILE_DEFAULT]
        if self.PROFILE_DEFAULT not in order:
            order.insert(0, self.PROFILE_DEFAULT)
        active = str(raw.get("active") or "").strip()[:24]
        if active not in order:
            active = self.PROFILE_DEFAULT
        sets = {}
        for name, s in (raw.get("sets") or {}).items():
            name = str(name).strip()[:24]
            if name not in order or name == active or not isinstance(s, dict):
                continue
            quick = []
            for b in s.get("quick") or []:
                try:
                    quick.append(self._quick_clean(b, int(b["page"]), int(b["slot"])))
                except (KeyError, TypeError, ValueError):
                    continue
            names = s.get("quick_names")
            sets[name] = {"quick": quick,
                          "quick_names": {str(k): str(v)[:16] for k, v in names.items()} if isinstance(names, dict) else {},
                          "quick_layout": self._quick_phantoms(self._quick_layout_load(s.get("quick_layout")), quick,
                                                               names if isinstance(names, dict) else {})}
        for name in order:                 # a profile with nothing saved yet: empty
            if name != active and name not in sets:
                sets[name] = {"quick": [], "quick_names": {}, "quick_layout": {}}
        shows = {str(k)[:24]: v for k, v in (raw.get("shows") or {}).items()
                 if str(k)[:24] in order and isinstance(v, dict) and isinstance(v.get("design"), dict)}
        return {"active": active, "order": order, "sets": sets, "shows": shows}

    def _profile_take(self, name: str) -> None:
        """Put profile `name`'s buttons on the desk; the one in use is kept."""
        p = self._profiles()
        self._a_quick_release_all()
        self.__dict__["quick_editing"] = None
        p["sets"][p["active"]] = {"quick": self.quick, "quick_names": dict(getattr(self, "quick_names", {}) or {}),
                                  "quick_layout": self._quick_layouts()}
        s = p["sets"].pop(name, None) or {}
        self.quick = list(s.get("quick") or [])
        self.quick_names = dict(s.get("quick_names") or {})
        self.quick_layout = dict(s.get("quick_layout") or {})
        p["active"] = name

    def _profile_name(self, name) -> str:
        p = self._profiles()
        name = str(name or "").strip()
        hit = next((n for n in p["order"] if n.lower() == name.lower()), None)
        if hit is None:
            raise ValueError(f"no button profile called {name!r} (there are: {', '.join(p['order'])})")
        return hit

    def _a_quick_profile_use(self, name="", **_):
        """Switch to another set of buttons ("Halloween"): every button of
        the one in use is let go first; its pages wait for next time."""
        name = self._profile_name(name)
        if name == self._profiles()["active"]:
            return {"profile": name, "summary": f"already on the {name} buttons"}
        self._profile_take(name)
        n = len(self.quick)
        return {"profile": name, "summary": f"{name} buttons ({n} button{'s' if n != 1 else ''})"}

    def _a_quick_profile(self, new=None, copy=False, rename=None, to=None, delete=None, **_):
        """Button profiles: `new` makes one (empty, or a `copy` of the one
        in use) and switches to it; `rename` + `to`; `delete` (never
        Default, which is always there)."""
        p = self._profiles()
        if new not in (None, ""):
            name = str(new).strip()[:24]
            if any(n.lower() == name.lower() for n in p["order"]):
                raise ValueError(f"there is already a button profile called {name!r}")
            p["order"].append(name)
            # (a show of this name deleted before: its record goes, or the new
            # profile would be "learnt from" against the old show's buttons)
            (p.get("shows") or {}).pop(name, None)
            if _truthy(copy):
                p["sets"][name] = {"quick": [dict(b) for b in self.quick],
                                   "quick_names": dict(getattr(self, "quick_names", {}) or {}),
                                   "quick_layout": json.loads(json.dumps(self._quick_layouts()))}
            self._profile_take(name)
            return {"profile": name, "summary": f"new button profile {name!r}" + (" (a copy)" if _truthy(copy) else "")}
        if rename not in (None, ""):
            old = self._profile_name(rename)
            name = str(to or "").strip()[:24]
            if old == self.PROFILE_DEFAULT:
                raise ValueError("the Default profile keeps its name")
            if not name:
                raise ValueError("give the profile a name")
            if any(n.lower() == name.lower() and n != old for n in p["order"]):
                raise ValueError(f"there is already a button profile called {name!r}")
            p["order"] = [name if n == old else n for n in p["order"]]
            if old in p["sets"]:
                p["sets"][name] = p["sets"].pop(old)
            if old in (p.get("shows") or {}):
                # the AI's record of the show goes with it (to copy it from, and
                # to learn from the operator's edits to it)
                p["shows"][name] = p["shows"].pop(old)
            if p["active"] == old:
                p["active"] = name
            return {"profile": name, "summary": f"button profile {old!r} is now {name!r}"}
        if delete not in (None, ""):
            name = self._profile_name(delete)
            if name == self.PROFILE_DEFAULT:
                raise ValueError("the Default profile can't be deleted")
            if p["active"] == name:
                self._profile_take(self.PROFILE_DEFAULT)
            p["sets"].pop(name, None)
            (p.get("shows") or {}).pop(name, None)
            p["order"] = [n for n in p["order"] if n != name]
            return {"profile": p["active"], "summary": f"button profile {name!r} deleted"}
        return {"profile": p["active"], "profiles": list(p["order"]),
                "summary": f"on the {p['active']} buttons; profiles: {', '.join(p['order'])}"}

    def _quick_swap_pages(self, a: int, b: int) -> dict:
        if a == b:
            return {"summary": "same page"}
        for x in list(self.quick_active):
            if any(q["id"] == x and q["page"] in (a, b) for q in self.quick):
                self._quick_off(x, force=True, fade=False)
        other = {a: b, b: a}
        ids = {}
        out = []
        for q in self.quick:
            if q["page"] in other:
                old = q["id"]
                q = self._quick_clean(dict(q), other[q["page"]], q["slot"])
                ids[old] = q["id"]
            out.append(q)
        for q in out:                          # page tiles point at the pages' new numbers
            if q.get("to_page") in other:
                q["to_page"] = other[q["to_page"]]
            if q.get("turn_on"):
                q["turn_on"] = [ids.get(x, x) for x in q["turn_on"]]
        self.quick = sorted(out, key=lambda q: (q["page"], q["slot"]))
        for tr in (self.timeline or {}).get("tracks") or []:
            for c in tr.get("clips") or []:
                if c.get("button") in ids:
                    c["button"] = ids[c["button"]]
        for t in ((self.__dict__.get("sound_cfg") or {}).get("triggers") or []):
            if t.get("button") in ids:
                t["button"] = ids[t["button"]]
        names = dict(getattr(self, "quick_names", {}) or {})
        na, nb = names.pop(str(a), None), names.pop(str(b), None)
        if nb:
            names[str(a)] = nb
        if na:
            names[str(b)] = na
        self.quick_names = names
        lay = self._quick_layouts()
        la, lb = lay.pop(str(a), None), lay.pop(str(b), None)
        if lb:
            lay[str(a)] = lb
        if la:
            lay[str(b)] = la
        self.quick_layout = lay
        return {"pages": [a, b], "summary": f"pages {a} and {b} swapped"}

    def _a_quick_move(self, page=1, slot=None, to_page=None, to_slot=None, copy=False, **_):
        """Move (or copy) a button to another slot; a button already there
        swaps places with it.  to_slot="free": the first empty place on
        to_page (another page: Edit -> drag onto its tab, or Page in the
        button's editor)."""
        page = int(_clamp(page, 1, self.QUICK_PAGES))
        to_page = int(_clamp(to_page if to_page not in (None, "") else page, 1, self.QUICK_PAGES))
        if to_slot == "free":
            if to_page == page:
                return {"summary": "already on this page"}
            got_page, to_slot = self._quick_free_slot(to_page)
            if got_page != to_page:
                raise ValueError(f"page {to_page} is full")
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
        moved = self._quick_make_room(btn)
        return {"id": key, "button": btn, "moved": moved,
                "summary": f"button {page}.{slot}: {btn['label']}"
                           + (f" ({len(moved)} button(s) moved over to make room)" if moved else "")}

    def _quick_cells(self, b: dict, cols: int, total: int) -> list[int]:
        """The slots a big tile covers besides its own (as far as the page goes)."""
        w, hgt = int(b.get("w") or 1), int(b.get("h") or 1)
        col = (b["slot"] - 1) % cols
        w = max(1, min(w, cols - col))
        out = []
        for r in range(hgt):
            for c in range(w):
                x = b["slot"] + r * cols + c
                if (r or c) and x <= total:
                    out.append(x)
        return out

    def _quick_make_room(self, btn: dict) -> list[str]:
        """A bigger button takes the space it needs: the buttons in the way
        move over to the next free places on the page (the layout adapts)
        instead of the big one staying small."""
        if int(btn.get("w") or 1) == 1 and int(btn.get("h") or 1) == 1:
            return []
        page = btn["page"]
        lay = {**self.LAYOUT_DEFAULT, **self._quick_layouts().get(str(page), {})}
        cols, total = int(lay["cols"]), int(lay["cols"]) * int(lay["rows"])
        mine = set(self._quick_cells(btn, cols, total))
        others = [b for b in self.quick if b["page"] == page and b is not btn]
        covered = set(mine)
        for b in others:
            if b["slot"] not in mine:
                covered |= set(self._quick_cells(b, cols, total))
        moved = []
        for b in [b for b in others if b["slot"] in mine]:
            used = {o["slot"] for o in self.quick if o["page"] == page}
            free = next((x for x in range(1, total + 1) if x not in used and x not in covered), None)
            if free is None:
                p2, free = self._quick_free_slot(page + 1)       # the page is full: the next page
            else:
                p2 = page
            old = b["id"]
            r = self._a_quick_move(page=page, slot=b["slot"], to_page=p2, to_slot=free)
            vals = self.__dict__.get("fader_vals") or {}
            if old in vals:
                vals[r["id"]] = vals.pop(old)                   # a fader keeps its place
            moved.append(r["id"])
        return moved

    COLOUR_NAMES = {"#ff0000": "Red", "#0033ff": "Blue", "#00ff40": "Green", "#ffffff": "White",
                    "#ff00cc": "Magenta", "#00c8ff": "Cyan", "#ffb000": "Amber", "#7a00ff": "UV",
                    "#ffe600": "Yellow", "#ff5a00": "Orange", "#00ffcc": "Teal", "#ff3c8c": "Pink"}

    def _a_quick_colour_row(self, colours=None, heads=None, group=None, page=None, name="", **_):
        """A row of colour buttons for some lights, in one go: one button per
        colour, all in one radio group (pressing one turns the others off),
        side by side in the first free run of slots on the page."""
        cols = [c for c in (_hex_or_none(x) for x in (colours or [])) if c][:12]
        if not cols:
            raise ValueError("pick at least one colour")
        if group not in (None, ""):
            g = self._group(group=group)
            target, who = {"group": g["n"]}, g["name"]
        else:
            nums = sorted({int(x) for x in (heads if heads is not None else self.selected)})
            if not nums:
                raise ValueError("select the lights first (or pick a group)")
            for n in nums:
                self._head(n)
            target = {"heads": nums}
            who = self._head(nums[0]).get("name") or f"#{nums[0]}" if len(nums) == 1 else f"{len(nums)} lights"
        page = int(_clamp(page or 1, 1, self.QUICK_PAGES))
        lay = {**self.LAYOUT_DEFAULT, **self._quick_layouts().get(str(page), {})}
        used = self._quick_taken(page, lay["cols"])
        total = lay["cols"] * lay["rows"]
        start = next((s for s in range(1, total - len(cols) + 2)
                      if all(s + i not in used for i in range(len(cols)))), None)
        if start is None:
            raise ValueError(f"page {page} has no {len(cols)} free buttons side by side - pick another page")
        tag = (str(name).strip() or f"Colour {who}")[:20]
        ids = []
        for i, c in enumerate(cols):
            btn = self._quick_clean({"kind": "colour", "colour": c, "mode": "latch", "target": dict(target),
                                     "exclusive": tag, "label": f"{self.COLOUR_NAMES.get(c, c)}"[:24]},
                                    page, start + i)
            self.quick.append(btn)
            ids.append(btn["id"])
        self.quick.sort(key=lambda b: (b["page"], b["slot"]))
        return {"ids": ids, "page": page, "group": tag,
                "summary": f"{len(ids)} colour buttons for {who} on page {page} (one at a time: “{tag}”)"}

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
