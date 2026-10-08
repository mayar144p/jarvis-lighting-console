"""The patch: adding, removing, addressing and naming lights, CSV.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import csv as csvmod
import io
import json
import re
from pathlib import Path

from app import config, fixture_kind, fixtures
from app.engine_base import (
    _FIXTURE_CACHE,
    MAX_UNIVERSES,
    _clamp,
    _fallback_fixture,
    _round2,
    default_mode,
)
from app.engine_support import SLOTS, channel_role
from app.engine_support import pos as _pos
from app.engine_support import pos_to_ua as _pos_to_ua


class PatchMixin:
    # ------------------------------------------------------------------
    # patch
    # ------------------------------------------------------------------
    def _head(self, head_no) -> dict:
        n = int(head_no)
        for h in self.patch:
            if h["head_no"] == n:
                return h
        raise ValueError(f"head {n} is not patched")

    def _conflict(self, pos: int, channels: int, skip=None) -> int | None:
        end = pos + channels
        for h in self.patch:
            if h["head_no"] == skip:
                continue
            h0 = _pos(h["universe"], h["address"])
            if pos < h0 + h["channels"] and h0 < end:
                return h["head_no"]
        return None

    def _patch_clashes(self) -> list[dict]:
        """Every pair of lights whose DMX channels overlap (both sending on
        the same channels: the second one silently fights the first).  The
        load-time check only caught one direction of this."""
        rows = sorted(((_pos(h["universe"], h["address"]), h) for h in self.patch), key=lambda t: t[0])
        out = []
        for i, (p0, a) in enumerate(rows):
            for p1, b in rows[i + 1:]:
                if p1 >= p0 + a["channels"]:
                    break
                lo, hi = p1, min(p0 + a["channels"], p1 + b["channels"]) - 1
                out.append({"a": a["head_no"], "b": b["head_no"], "universe": b["universe"],
                            "from": b["address"], "to": b["address"] + (hi - lo)})
        return out

    def _free_block(self, channels: int, skip=None, universe=1) -> tuple[int, int]:
        """The first universe.address (from `universe` on) with `channels`
        free channels in a row."""
        for u in range(max(1, int(universe)), 65):
            taken = sorted((h["address"], h["address"] + h["channels"] - 1) for h in self.patch
                           if h["universe"] == u and h["head_no"] != skip)
            a = 1
            for lo, hi in taken:
                if a + channels - 1 < lo:
                    break
                a = max(a, hi + 1)
            if a + channels - 1 <= SLOTS:
                return u, a
        raise ValueError("no free DMX space left")

    def _a_patch_move_free(self, head=None, **_):
        """Move a light to the first free block of addresses (its own
        universe first) - the one-tap fix for a clash."""
        h = self._head(head)
        u, a = self._free_block(h["channels"], skip=h["head_no"], universe=h["universe"])
        old = (h["universe"], h["address"])
        h["universe"], h["address"] = u, a
        self.patch_rev += 1
        return {"head_no": h["head_no"], "universe": u, "address": a,
                "summary": f"#{h['head_no']} moved from {old[0]}.{old[1]:03d} to {u}.{a:03d} - "
                           f"set the light's own address to match"}

    def _next_free_pos(self, universe=None, address=None, channels=1) -> int:
        if universe and address:
            return _pos(int(universe), int(address))
        if universe:
            inside = [(_pos(h["universe"], h["address"]) + h["channels"])
                      for h in self.patch if h["universe"] == int(universe)]
            return max(inside) if inside else _pos(int(universe), 1)
        ends = [(_pos(h["universe"], h["address"]) + h["channels"])
                for h in self.patch]
        return max(ends) if ends else 0

    def _roll(self, pos: int, channels: int) -> int:
        """Advance a flat patch cursor to where a block really fits.

        Start addresses are assigned over a flat, universe-major grid
        (pos = (universe-1)*512 + address-1).  A fixture must never
        straddle the 512-channel boundary, so when the block would cross
        it we roll over to address 1 of the next universe - otherwise its
        tail channels would land past the end of the universe and the
        frame builder would silently drop them.
        """
        if channels > SLOTS:
            raise ValueError(
                f"a fixture cannot use {channels} channels - a universe "
                f"only has {SLOTS}")
        if pos < 0:
            pos = 0
        u, a = _pos_to_ua(pos)
        if a - 1 + channels > SLOTS:
            return u * SLOTS           # flat index of (universe u+1, address 1)
        return pos

    def _next_fit(self, pos: int, channels: int,
                  skip: int | None = None) -> int:
        """First flat position >= `pos` that is free for `channels`.

        The automatic addressing helper: rolls over at the 512 boundary
        (_roll) and steps forward past any already-patched head that is in
        the way, so auto-patching moves on to the next free space instead
        of failing.  Placement pinned with an explicit address bypasses
        this and stays strict (see plan_addresses).
        """
        while True:
            pos = self._roll(pos, channels)
            u, _a = _pos_to_ua(pos)
            if u > MAX_UNIVERSES:
                raise ValueError(
                    f"no free address for {channels} channels - patch space "
                    f"is full after universe {MAX_UNIVERSES}")
            clash = self._conflict(pos, channels, skip=skip)
            if clash is None:
                return pos
            h = self._head(clash)
            # an overlapping head always ends past `pos`, so this advances
            # strictly forward and the loop always terminates
            pos = max(pos + 1,
                      _pos(h["universe"], h["address"]) + h["channels"])

    def _fixture_db(self, manufacturer, model) -> dict | None:
        """Fixture DB row for a (manufacturer, model), cached.

        patch_from_csv calls this once per row, so an uncached lookup
        meant one SQLite query per head (120 heads = 120 queries).  The
        cache is keyed by the same normalised pair the search uses and is
        dropped wholesale whenever the fixture DB changes
        (import_gdtf, fixtures.install), so it can never serve a stale
        definition.
        """
        man = str(manufacturer or "").strip().lower()
        mod = str(model or "").strip().lower()
        if not mod:
            return None
        key = f"{man}\x1f{mod}"
        hit = _FIXTURE_CACHE.get(key)
        if hit is not None:
            return hit or None            # None cached as a negative result
        fx = None
        try:
            found = fixtures.match_model(self.db_path, mod, man)
            # match_model answers "which row"; the caller needs the row's
            # MODES, so fetch the full record.  Returning the bare match
            # here made every head `raw`, because a row of id/name/source
            # has no `modes` key for _resolve_map to read.
            if found is not None:
                fx = fixtures.get(self.db_path, int(found["id"]))
        except Exception:
            fx = None
        if fx is None:
            # Old behaviour, and a better guess than nothing: a search hit
            # that is not an exact match still beats no definition.
            try:
                hits = fixtures.search(self.db_path, f"{man} {mod}".strip(),
                                       limit=5)
            except Exception:
                hits = []
            if len(hits) == 1:
                fx = fixtures.get(self.db_path, int(hits[0]["id"]))
        _FIXTURE_CACHE[key] = fx or {}
        return fx

    def _resolve_map(self, manufacturer, model, mode_name=None,
                     channels=None) -> tuple[list[str], str, bool]:
        """Fixture DB -> (channel roles, mode name, mapped?)."""
        fx = self._fixture_db(manufacturer, model)
        count = int(channels or 0)
        if fx is None:
            return (["raw"] * count, str(mode_name or ""), False)
        chosen = None
        for m in fx.get("modes") or []:
            if mode_name and str(m.get("name") or "").strip() == str(mode_name).strip():
                chosen = m
                break
        if chosen is None and count:
            for m in fx.get("modes") or []:
                if m.get("channel_count") == count:
                    chosen = m
                    break
        if chosen is None and fx.get("modes"):
            chosen = fx["modes"][0]
        if chosen is None:
            return (["raw"] * count, str(mode_name or ""), False)
        mapping = [channel_role(c) for c in chosen.get("channels") or []]
        if count:
            if len(mapping) < count:
                mapping += ["raw"] * (count - len(mapping))
            elif len(mapping) > count:
                mapping = mapping[:count]
        return (mapping, chosen.get("name") or str(mode_name or ""),
                not any(r == "raw" for r in mapping))

    def head_ranges(self, head: dict) -> dict:
        """{role: {min, max, unit}} for one patched head.

        Cached per (manufacturer, model, mode) because it is a database
        read and the 40 Hz tick must not make one per head per frame.  The
        cache is dropped by `remap_heads`, which is the only thing that can
        make a range change.
        """
        key = (head.get("manufacturer"), head.get("model"),
               head.get("mode"))
        hit = self._range_cache.get(key)
        if hit is not None:
            return hit
        try:
            out = fixtures.role_ranges(self.db_path, key[0] or "",
                                       key[1] or "", key[2] or "")
        except Exception:
            # A malformed profile must not take the tick down with it: the
            # ranges are an enhancement, and losing them costs degrees,
            # not control.
            out = {}
        self._range_cache[key] = out
        return out

    def role_range(self, heads: list[dict], role: str) -> dict:
        """The range a role has across these heads, or {} if none.

        A selection of the same model gets that model's range.  A MIXED
        selection is reported as no range rather than averaged: two
        different movers have different tilt spans, and a made-up midpoint
        would put 117 degrees of travel on a light that only has 90.
        """
        found = []
        for h in heads:
            r = self.head_ranges(h).get(role)
            if r and r.get("min") is not None and r.get("max") is not None:
                found.append((r["min"], r["max"], r.get("unit", "raw")))
        if not found:
            return {}
        first = found[0]
        if any((lo, hi) != (first[0], first[1]) for lo, hi, _u in found[1:]):
            spans = sorted({(lo, hi) for lo, hi, _u in found})
            return {"unit": "raw", "mixed": spans}
        return {"min": first[0], "max": first[1], "unit": first[2],
                "mixed": False}

    def _head_from_layout(self, f: dict) -> dict:
        mapping, mode_name, mapped = self._resolve_map(
            f.get("manufacturer", ""), f.get("model", ""),
            f.get("mode"), f.get("channels"))
        saved = f.get("map")
        if (isinstance(saved, list) and saved and any(r != "raw" for r in saved)
                and self._fixture_db(f.get("manufacturer", ""), f.get("model", "")) is None):
            # not in the installed library (patched from the built-in list of
            # common lights): the roles saved with it are the best there are -
            # all-"raw" brought a restarted rig back dead
            mapping, mode_name = [str(r) for r in saved], str(f.get("mode") or mode_name)
        head = dict(f)
        head.update({"map": mapping, "mode": mode_name,
                     "mapped": mapped, "curve": head.get("curve", "linear")})
        head.setdefault("name", f"Head {f.get('head_no', '?')}")
        return head

    def _resolve_fixture(self, query="", fixture_id=None, mode=None):
        """Fixture DB lookup -> (fixture dict, mode dict, channel roles)."""
        fx = None
        if fixture_id not in (None, ""):
            try:
                fx = fixtures.get(self.db_path, int(fixture_id))
            except (TypeError, ValueError):
                fx = None
        if fx is None:
            hits = fixtures.search(self.db_path, str(query or "").strip())
            if hits:
                fx = fixtures.get(self.db_path, hits[0]["id"])
                if fx is None:
                    raise ValueError("fixture lookup failed")
            else:
                # Profile DB miss -> offline footprint library, so a type
                # that has not been imported yet can still be auto-patched
                # with correct start addresses (flagged unverified).
                fx = _fallback_fixture(str(query or ""))
                if fx is None:
                    raise ValueError(f"no fixture matches {query!r}")
        modes = fx.get("modes") or []
        if not modes:
            raise ValueError(f"{fx['model']} has no DMX modes")
        chosen = None
        if mode is not None and str(mode) != "":
            want = str(mode).strip().lower()
            for m in modes:
                if (m["name"].strip().lower() == want   # files pad names: "8 Channel "
                        or str(m.get("channel_count")) == want):
                    chosen = m
                    break
            if chosen is None and str(mode).isdigit():
                idx = int(mode)
                if 0 <= idx < len(modes):
                    chosen = modes[idx]
            if chosen is None:
                raise ValueError(f"{fx['model']} has no mode {mode!r}")
        if chosen is None:
            chosen = default_mode(modes)   # fewest channels that still control the light
        labels = list(chosen.get("channels") or [])
        if not labels:
            raise ValueError(f"mode {chosen['name']} lists no channels")
        return fx, chosen, [channel_role(lbl) for lbl in labels]

    # --- dynamic addressing (the auto-patcher) --------------------------
    def _plan_fixture(self, item: dict):
        """Plan entry -> (fixture, mode, channel roles) from the library.

        "footprint" (alias "channels") gives a raw channel count without
        any profile; otherwise query/fixture_id + mode resolve through the
        fixture DB (mode-aware footprint) or the offline fallback library.
        """
        raw = item.get("footprint", item.get("channels"))
        if raw is not None and str(raw).strip() != "":
            try:
                n = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"bad footprint: {raw!r}") from None
            if n < 1:
                raise ValueError("footprint must be at least 1 channel")
            if n > SLOTS:
                raise ValueError(
                    f"a fixture cannot use {n} channels - a universe only "
                    f"has {SLOTS}")
            fx = {"id": 0, "manufacturer": "", "model": f"{n}ch generic",
                  "modes": [{"name": f"{n}ch raw", "channels": ["raw"] * n}],
                  "unverified": True}
            return fx, fx["modes"][0], ["raw"] * n
        return self._resolve_fixture(str(item.get("query") or ""),
                                     item.get("fixture_id"),
                                     item.get("mode"))

    def plan_addresses(self, items, start=None) -> list[dict]:
        """Dynamic addressing loop: accept a list of desired fixtures and
        calculate their sequential start addresses from their footprints.

        items - list of entries, each one of
            {"query"|"fixture_id"|"footprint", "mode"?, "qty"?,
             "universe"?, "address"?, "role"?, "kind"?, "name"?,
             "x"?, "y"?, "z"?}
          * footprint = DMX channel count (from the profile library);
          * qty defaults to 1, clamped 1..64;
          * universe/address pin where the entry starts: an explicit
            address is used exactly as given and validated (must fit the
            universe, must not overlap - never silently moved), while a
            universe without address starts at that universe's first free
            slot and rolls over if the block does not fit.
        start - optional flat position or (universe, address) to begin at
            (default: just after the existing patch, i.e. append).

        Returns one plan row per head (universe/address/channels/model/
        mode/map/mapped/role/kind/x/y/z).  Read-only: nothing is patched -
        add_heads/patch_list apply a plan (or pass plan_only=True).
        """
        if isinstance(items, str):                 # chat/HTTP convenience
            try:
                items = json.loads(items)
            except json.JSONDecodeError:
                raise ValueError("fixtures must be a JSON list") from None
        if not isinstance(items, (list, tuple)):
            raise ValueError("fixtures must be a list of desired fixtures")
        if start is None:
            cursor = self._next_free_pos()         # append after the patch
        elif isinstance(start, (list, tuple)) and len(start) == 2:
            cursor = _pos(int(start[0]), int(start[1]))
        else:
            cursor = int(start)

        # The plan reads the current patch to find free space, so a bulk
        # caller must hand us a snapshot it took under the lock: otherwise
        # two concurrent bulk actions would both plan against the same
        # stale view and collide.  See Engine._act_bulk.
        rows: list[dict] = []
        for item in items:
            entry = item if isinstance(item, dict) else (
                {"footprint": item} if isinstance(item, int)
                else {"query": item})
            qty = _clamp(entry.get("qty") or 1, 1, 64)
            fx, chosen, mapping = self._plan_fixture(entry)
            channels = len(mapping)
            if channels > SLOTS:
                raise ValueError(
                    f"{fx['model']} uses {channels} channels - more than "
                    f"one {SLOTS}-channel universe")
            pinned = entry.get("address") not in (None, "")
            if pinned or entry.get("universe") not in (None, ""):
                u = int(entry.get("universe") or 1)
                if u < 1:
                    raise ValueError("universe starts at 1")
                if pinned:
                    a = int(entry["address"])
                    if a < 1:
                        raise ValueError("address starts at 1")
                    cursor = _pos(u, a)            # exact pin: never moved
                else:
                    # universe hint: start after what already lives there
                    cursor = self._next_free_pos(u)

            for _ in range(qty):
                if pinned:
                    # explicit address: validate strictly instead of rolling
                    u, a = _pos_to_ua(cursor)
                    if a - 1 + channels > SLOTS:
                        raise ValueError(
                            f"no free address for {channels} channels - "
                            f"try universe {u + 1}")
                    clash = self._conflict(cursor, channels)
                    if clash is not None:
                        raise ValueError(f"address overlaps head {clash}")
                else:
                    cursor = self._next_fit(cursor, channels)
                u, a = _pos_to_ua(cursor)
                rows.append({
                    "universe": u, "address": a, "channels": channels,
                    "manufacturer": fx.get("manufacturer") or "",
                    "model": fx["model"],
                    "mode": chosen.get("name") or "",
                    "map": list(mapping),
                    "mapped": not any(r == "raw" for r in mapping),
                    "role": entry.get("role") or "generic",
                    "kind": entry.get("kind") or None,
                    "name": entry.get("name"),
                    "x": entry.get("x"), "y": entry.get("y"),
                    "z": entry.get("z"),
                    "unverified": bool(fx.get("unverified")),
                })
                cursor += channels                 # next head follows on

        # Positions: an explicit item x/y/z wins, otherwise spread the plan
        # evenly along the rig (same spacing the single add_heads used).
        total = len(rows)
        for i, row in enumerate(rows):
            row["x"] = (float(row["x"]) if row.get("x") not in (None, "")
                        else round((i - (total - 1) / 2) * 1.5, 2))
            row["y"] = (float(row["y"]) if row.get("y") not in (None, "")
                        else 0.3)
            row["z"] = (float(row["z"]) if row.get("z") not in (None, "")
                        else 0.0)
            if not row.get("kind"):
                row["kind"] = "truss" if row["y"] >= 2.0 else "floor"
        return rows

    @staticmethod
    def _build_head(row: dict, head_no: int) -> dict:
        """Plan row (plan_addresses) -> patched head dict."""
        channels = int(row["channels"])
        y = float(row.get("y") if row.get("y") is not None else 0.3)
        head = {
            "head_no": int(head_no),
            "name": str(row.get("name") or "").strip() or
                    f"{row.get('model') or 'Head'} {head_no}",
            "manufacturer": row.get("manufacturer") or "",
            "model": row.get("model") or "Head",
            "mode": row.get("mode") or "",
            "channels": channels,
            "universe": int(row["universe"]),
            "address": int(row["address"]),
            "x": float(row.get("x") if row.get("x") is not None else 0.0),
            "y": y,
            "z": float(row.get("z") if row.get("z") is not None else 0.0),
            "kind": row.get("kind") or ("truss" if y >= 2.0 else "floor"),
            "role": row.get("role") or "generic",
            "curve": "linear",
            **({"mount": dict(row["mount"])} if isinstance(row.get("mount"), dict) else {}),
            **({"stance": row["stance"]} if row.get("stance") in ("hang", "stand") else {}),
            "map": list(row.get("map") or ["raw"] * channels),
        }
        head["mapped"] = bool(row.get("mapped", not any(
            r == "raw" for r in head["map"])))
        if row.get("unverified"):
            head["unverified"] = True
        return head

    def _apply_plan(self, plan: list[dict]) -> list[int]:
        """Append planned rows to the patch atomically -> head numbers.

        Rolls the whole patch back if anything is invalid, so a failed
        auto-patch never leaves a half-patched state behind.
        """
        old = list(self.patch)
        next_no = max((h["head_no"] for h in self.patch), default=0) + 1
        added: list[int] = []
        try:
            for i, row in enumerate(plan):
                head = self._build_head(row, next_no + i)
                self.patch.append(head)
                added.append(head["head_no"])
            self._validate_patch()
        except (ValueError, TypeError):
            self.patch = old
            raise
        self.patch_rev += 1
        return added

    def _a_add_heads(self, query="", fixture_id=None, mode=None, qty=1,
                     universe=None, address=None, x=None, y=None, z=None,
                     role=None, kind=None, name=None, **_):
        """Patch qty heads of one fixture type with computed start addresses.

        The footprint comes from the fixture profile library and addresses
        are assigned by plan_addresses: sequential after the current
        patch, rolling over to the next universe at the 512 boundary.
        Explicit universe/address pins are validated, not moved.
        """
        qty = _clamp(qty or 1, 1, 64)
        entry: dict = {"query": query, "fixture_id": fixture_id,
                       "mode": mode, "qty": qty, "role": role,
                       "kind": kind, "name": name,
                       "x": x, "y": y, "z": z}
        if universe not in (None, ""):
            entry["universe"] = universe
        if address not in (None, ""):
            entry["address"] = address
        plan = self.plan_addresses([entry])       # raises before state changes
        if x is None and y is None and z is None and plan:
            # No position given: hang it where that kind of light goes,
            # beside the others of its kind, instead of on (0, 0, 0).
            kind_now = fixture_kind.describe(plan[0])["type"]
            spots = fixture_kind.place_in_venue(
                kind_now, len(plan), self.patch, self.venue)
            for row, spot in zip(plan, spots):
                row.update(spot)
        added = self._apply_plan(plan)
        return {"heads": added, "patched": len(self.patch),
                "summary": f"added {len(added)} x {plan[0]['model']}"}

    # what a new fixture type brings; everything else about a head stays
    _TYPE_KEYS = frozenset({"manufacturer", "model", "mode", "channels", "map", "mapped",
                            "unverified", "universe", "address", "name"})

    def _a_change_type(self, heads=None, head=None, query="", fixture_id=None, mode=None, **_):
        """Swap the fixture type of patched lights, keeping each one's
        number, place, rigging, groups, cues and looks (they point at the
        head number and at roles, so the new type plays them where it has
        the channel).  The address stays when the new footprint fits there;
        otherwise the light moves to the first free block and says so."""
        nums = sorted({int(x) for x in (heads or ([head] if head is not None else []))})
        if not nums:
            raise ValueError("pick the light(s) to change")
        by_no = {h["head_no"]: h for h in self.patch}
        missing = [n for n in nums if n not in by_no]
        if missing:
            raise ValueError(f"no light #{missing[0]}")
        old_patch = list(self.patch)
        self.patch = [h for h in self.patch if h["head_no"] not in nums]
        moved, lost = [], set()
        try:
            for n in nums:
                was = by_no[n]
                entry = {"query": query, "fixture_id": fixture_id, "mode": mode, "qty": 1}
                free = self.plan_addresses([entry])[0]        # an unknown type fails here
                try:
                    row = self.plan_addresses([{**entry, "universe": was["universe"],
                                                "address": was["address"]}])[0]
                except ValueError:
                    row = free                                # no room here: the first free block
                    moved.append(f"#{n} -> {row['universe']}.{row['address']}")
                new = self._build_head(row, n)
                if was.get("name") and was["name"] != f"{was.get('model') or 'Head'} {n}":
                    new["name"] = was["name"]                  # a name the operator gave it
                for k, v in was.items():
                    if k not in self._TYPE_KEYS and k not in ("kind", "role", "x", "y", "z"):
                        new[k] = v
                for k in ("kind", "role", "x", "y", "z"):
                    new[k] = was.get(k, new.get(k))
                used = {r for pb in self.playbacks for c in pb["stack"]
                        for r in (c.get("values") or {}).get(n, {})}
                lost |= {r.split("@", 1)[0] for r in used} - set(new["map"])
                self.patch.append(new)
            self.patch.sort(key=lambda h: h["head_no"])
            self._validate_patch()
        except (ValueError, TypeError):
            self.patch = old_patch
            raise
        self.patch_rev += 1
        for n in nums:
            self.programmer.pop(n, None)
        model = next(h for h in self.patch if h["head_no"] == nums[0])["model"]
        msg = f"{len(nums)} light(s) are now {model}"
        if moved:
            msg += "; moved " + ", ".join(moved[:6])
        if lost:
            msg += f"; cues set {', '.join(sorted(lost)[:6])} it doesn't have"
        return {"heads": nums, "moved": moved, "lost": sorted(lost), "summary": msg}

    def _a_remove_heads(self, heads=None, head=None, head_end=None, **_):
        wanted = set()
        if heads:
            wanted.update(int(h) for h in heads)
        if head is not None:
            lo = int(head)
            hi = int(head_end) if head_end is not None else lo
            if hi < lo:
                lo, hi = hi, lo
            wanted.update(range(lo, hi + 1))
        if not wanted:
            raise ValueError("no heads given")
        before = len(self.patch)
        self.patch = [h for h in self.patch if h["head_no"] not in wanted]
        removed = before - len(self.patch)
        if not removed:
            raise ValueError("none of those heads are patched")
        self.patch_rev += 1
        self.selected = [n for n in self.selected if n in
                         {h["head_no"] for h in self.patch}]
        self.programmer = {n: v for n, v in self.programmer.items() if
                           n in {h["head_no"] for h in self.patch}}
        for g in self.groups:
            g["heads"] = [n for n in g["heads"] if n in
                          {h["head_no"] for h in self.patch}]
        self.groups = [g for g in self.groups if g["heads"]]
        return {"removed": removed, "patched": len(self.patch),
                "summary": f"removed {removed} head(s)"}

    def _a_auto_patch(self, **_):
        """Re-pack the current patch sequentially from universe 1 address 1.

        Start addresses accumulate footprint-by-footprint over the flat
        grid; a head that would cross the 512-channel boundary rolls over
        to address 1 of the next universe (_roll) - it is never split, so
        no channel is ever written past the end of a universe and lost.
        """
        if not self.patch:
            raise ValueError("patch is empty")
        snapshot = [(h, h["universe"], h["address"]) for h in self.patch]
        try:
            pos = 0
            for h in sorted(self.patch, key=lambda p: p["head_no"]):
                pos = self._roll(pos, h["channels"])   # universe rollover
                u, a = _pos_to_ua(pos)
                h["universe"], h["address"] = u, a
                pos += h["channels"]
            self._validate_patch()
        except ValueError:
            for h, u, a in snapshot:                   # put every head back
                h["universe"], h["address"] = u, a
            raise
        self.patch_rev += 1
        return {"universes": self._universe_count(),
                "summary": f"auto-patched {len(self.patch)} heads"}

    def _a_patch_list(self, fixtures=None, start=None, plan_only=False, **_):
        """Auto-patch a list of desired fixtures with computed addresses.

        fixtures: the list plan_addresses accepts (query/fixture_id/
        footprint + qty per entry, one head per qty unit).  Addresses are
        sequential, overlap-checked and roll over at the universe
        boundary.  plan_only=True returns the address plan without
        patching anything - a dry run of the auto-patcher.
        """
        plan = self.plan_addresses(fixtures, start=start)
        if plan_only:
            return {"plan": plan, "planned": len(plan), "heads": [],
                    "summary": f"planned {len(plan)} head(s) - "
                               f"nothing patched"}
        if not plan:
            raise ValueError("fixtures list is empty")
        added = self._apply_plan(plan)
        return {"heads": added, "patched": len(self.patch), "plan": plan,
                "summary": f"patched {len(added)} head(s) automatically"}

    # The columns a patch sheet actually needs, in the order a lighting
    # tech reads them left to right.  `Head` first because that is how the
    # desk is numbered; `Mode` because two heads of the same model on
    # different DMX modes are two different fixtures, and a sheet that
    # omits it is a sheet that cannot be checked.
    PATCH_CSV_COLUMNS = (
        "Head", "Name", "Manufacturer", "Model", "Mode", "Fixture",
        "Universe", "Address", "Footprint", "End", "Channels", "Type",
        "Position X", "Position Y", "Position Z", "Kind", "Curve",
        "Unpatched", "Roles", "Source",
    )

    def model_source(self, head: dict) -> str:
        """Which FILE a head's profile came from, or `built-in`.

        `rev9044.gdtf` in the patch sheet, so a profile that came off the
        Share does not look hand-made and a hand-written one is
        distinguishable.  Resolved here rather than stored on the head,
        because heads are built by four different paths (add, layout, CSV
        import, show load) and a fifth that remembered would be a sixth
        that forgot - and the patch sheet is the only consumer, so a
        lookup at export time costs one cached read per model.
        """
        man = str(head.get("manufacturer") or "").strip()
        model = str(head.get("model") or "").strip()
        if not model:
            return ""
        key = (man, model)
        if key in self._source_cache:
            return self._source_cache[key]
        try:
            fx = self._fixture_db(man, model)
        except Exception:
            fx = None
        out = str((fx or {}).get("source") or "") or "built-in"
        self._source_cache[key] = out
        return out

    def _csv_name(self, h: dict) -> str:
        """A FILENAME-safe version of a head's name.

        A patch sheet gets emailed, uploaded to a ticketing system and
        opened on somebody else's machine, and the obvious way to name it -
        the show name - breaks on the first character Windows or a URL
        will not accept.  `CON: * ? " < > |` all go, a trailing dot is
        stripped (Windows silently drops it, so two shows can collide), and
        the result is never empty.
        """
        raw = str(h.get("name") or "").strip()
        for bad in '<>:"/\\|?*':
            raw = raw.replace(bad, "-")
        raw = re.sub(r"\s+", " ", raw).strip(" .")
        return raw or "head"

    def patch_csv(self, heads: list[int] | None = None) -> str:
        """The patch as CSV, one row per head.

        THE DOCUMENT THE RIG ACTUALLY HAS.  Until this existed the agent's
        own instructions pointed at "File > Print Window > Create CSV" in
        some other piece of software - i.e. the answer to "give me a patch
        sheet" was "go and do it by hand somewhere else".  A patch sheet is
        how a rig gets handed to a house tech, checked by a colleague, or
        diffed against last night's file, and being unable to produce one
        makes the console look unfinished.

        It is read from the LIVE patch, not from a plan, so it reflects
        what the desk is actually doing - including `raw` channels, which is
        the part a hand-typed sheet always gets wrong.

        The output is RFC 4180: fields containing a comma, a quote or a
        newline are quoted, and an embedded quote is doubled.  A fixture
        named `Foo, Bar "Special"` is not a reason to produce a file that
        opens with the wrong number of columns in every spreadsheet ever
        written.
        """
        if heads:
            wanted = {int(h) for h in heads}
            rows = [h for h in self.patch if h["head_no"] in wanted]
        else:
            rows = list(self.patch)

        def cell(value) -> str:
            text = "" if value is None else str(value)
            if any(c in text for c in (",", '"', "\n", "\r")):
                return '"' + text.replace('"', '""') + '"'
            return text

        out = [",".join(self.PATCH_CSV_COLUMNS)]
        for h in rows:
            mapping = list(h.get("map") or [])
            footprint = max(1, len(mapping))
            addr = int(h.get("address") or 1)
            roles = [r for r in mapping if r not in ("raw", "unused")]
            out.append(",".join(cell(v) for v in (
                h.get("head_no", ""),
                h.get("name") or f"Head {h.get('head_no', '?')}",
                h.get("manufacturer", ""),
                h.get("model", ""),
                h.get("mode", ""),
                # A single token naming brand + model, which is what most
                # third-party tools want in a `Fixture` column.
                " ".join(x for x in (str(h.get("manufacturer", "")).strip(),
                                     str(h.get("model", "")).strip()) if x),
                h.get("universe", ""),
                addr,
                footprint,
                addr + footprint - 1,
                len(mapping),
                h.get("kind") or ("truss" if float(h.get("y") or 0) >= 2.0
                                  else "floor"),
                _round2(h.get("x")), _round2(h.get("y")), _round2(h.get("z")),
                h.get("kind") or ("truss" if float(h.get("y") or 0) >= 2.0
                                  else "floor"),
                h.get("curve", "linear"),
                "" if "raw" not in mapping else "yes",
                " ".join(roles) or "-",
                self.model_source(h),
            )))
        # Excel opens a bare .csv as one column unless there is a BOM, and
        # a non-ASCII fixture name then arrives as mojibake.  A BOM is
        # invisible to every other reader, so it costs nothing and saves
        # the most common way this file gets opened.
        return "﻿" + "\r\n".join(out) + "\r\n"

    def _a_export_patch(self, path=None, heads=None, **kwargs):
        """Write the patch sheet to a file, and say where it went.

        Writing is opt-in by path: without one, the CSV is returned in the
        result and nothing touches the disk, so a caller can see it (or
        serve it) without the console creating files nobody asked for.
        """
        text = self.patch_csv(heads)
        name = None
        if path:
            target = Path(path)
            if not target.is_absolute():
                target = config.DATA / target
            # Only inside the app's own data folder: this action is reachable
            # over HTTP, and "write a file wherever the caller says" is not
            # something a lighting desk should offer.
            root = config.DATA.resolve()
            if root not in target.resolve().parents and target.resolve() != root:
                raise ValueError("patch sheets can only be written inside data/")
            if target.is_dir() or str(path).endswith(("/", "\\")):
                # A directory gets the SHOW's name, not "patch.csv":
                # `data/audition.mrk` and `data/show.mrk` are two different
                # sheets from one rig, and calling both `patch.csv` is how
                # the wrong one gets handed to a house tech.
                first = self.patch[0] if self.patch else {}
                stem = (str((self.venue or {}).get("name") or "").strip()
                        or self._csv_name(first))
                target = target / (stem + ".csv")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
            name = str(target)
        rows = len(text.splitlines()) - 1
        return {"csv": text, "path": name, "rows": rows,
                "columns": list(self.PATCH_CSV_COLUMNS),
                "bytes": len(text.encode("utf-8")),
                "summary": (f"patch sheet: {rows} head(s)"
                            + (f" -> {name}" if name else " (not written)"))}

    def _a_import_scan(self, observed=None, query="", fixture_id=None,
                       mode=None, qty=None, role=None, **_):
        """Auto-patch the universes an Art-Net scan observed.

        observed: [{"universe": n, "channels": depth}, ...] (a bare int is
        taken as a universe with unknown depth).  Each universe that is
        not patched yet gets ceil(used channels / footprint) heads of one
        fixture type (clamped 1..64) addressed from that universe's first
        free slot; universes already holding heads are skipped, so a
        rescan never disturbs an existing patch.  Defaults to the generic
        4-channel LED PAR profile.
        """
        rows = observed if isinstance(observed, (list, tuple)) else []
        if not rows:
            raise ValueError("nothing observed - run a scan first")
        eff_query = str(query or "").strip() or "LED PAR 4ch"
        fx, chosen, mapping = self._resolve_fixture(eff_query, fixture_id,
                                                    mode)
        footprint = max(1, len(mapping))
        items: list[dict] = []
        seen: set[int] = set()
        skipped: list[int] = []
        for raw in rows:
            if isinstance(raw, dict):
                u = int(raw.get("universe") or 0)
                depth = int(raw.get("channels") or raw.get("depth") or 0)
            else:
                u, depth = int(raw), 0
            if u < 1 or u in seen:
                continue
            seen.add(u)
            if any(h["universe"] == u for h in self.patch):
                skipped.append(u)           # never re-patch a live universe
                continue
            if qty not in (None, ""):
                n = int(qty)                                        # forced
            elif depth > 0:
                n = -(-depth // footprint)      # ceil(used / footprint)
            else:
                n = 1
            items.append({"query": eff_query, "fixture_id": fixture_id,
                          "mode": mode, "role": role, "universe": u,
                          "qty": max(1, min(64, n))})               # cap 1..64
        if not items:
            raise ValueError("every observed universe is already patched"
                             if skipped else "nothing observed to import")
        plan = self.plan_addresses(items)
        added = self._apply_plan(plan)
        universes = sorted({row["universe"] for row in plan})
        return {"heads": added, "patched": len(self.patch),
                "universes": universes, "skipped": skipped,
                "summary": f"imported {len(added)} head(s) on "
                           f"{len(universes)} universe(s)"}

    def _a_set_address(self, head=None, universe=None, address=None, **_):
        if head is None or universe is None or address is None:
            raise ValueError("head, universe and address are required")
        h = self._head(head)
        u, a = int(universe), int(address)
        if u < 1 or a < 1 or a + h["channels"] - 1 > SLOTS:
            raise ValueError(
                f"universe >= 1 and address 1..{SLOTS - h['channels'] + 1}")
        clash = self._conflict(_pos(u, a), h["channels"], skip=h["head_no"])
        if clash is not None:
            raise ValueError(f"address overlaps head {clash}")
        h["universe"], h["address"] = u, a
        self.patch_rev += 1
        return {"head_no": h["head_no"], "universe": u, "address": a}

    def _a_patch_from_csv(self, csv="", **_):
        text = str(csv or "")
        if not text.strip():
            raise ValueError("empty csv")
        rows = list(csvmod.reader(io.StringIO(text)))
        if not rows:
            raise ValueError("empty csv")
        header = [c.strip().lower() for c in rows[0]]
        idx = {name: i for i, name in enumerate(header)}
        for needed in ("headno", "headname", "dmxno", "manufacturer",
                       "type", "chans"):
            if needed not in idx:
                raise ValueError(f"csv is missing the {needed} column")

        def cell(row, name, default=""):
            i = idx.get(name)
            if i is None or i >= len(row):
                return default
            return row[i].strip()

        heads, seen = [], set()
        for row in rows[1:]:
            if not row or not any(c.strip() for c in row):
                continue
            head_no = int(cell(row, "headno", "0") or 0)
            if head_no < 1:
                raise ValueError(f"bad head number in row: {row[:3]}")
            if head_no in seen:
                raise ValueError(f"duplicate head number {head_no}")
            seen.add(head_no)
            dmx = cell(row, "dmxno", "1-001")
            u_str, _, a_str = dmx.partition("-")
            universe, address = int(u_str or 1), int(a_str or 1)
            chans = int(cell(row, "chans", "0") or 0)
            if chans < 1:
                raise ValueError(f"head {head_no}: bad channel count")
            if universe < 1 or address < 1 or address + chans - 1 > SLOTS:
                raise ValueError(
                    f"head {head_no}: address {dmx} with {chans} channels "
                    f"does not fit a universe")
            mapping, mode_name, mapped = self._resolve_map(
                cell(row, "manufacturer"), cell(row, "type"), None, chans)
            heads.append({
                "head_no": head_no,
                "name": cell(row, "headname") or f"Head {head_no}",
                "manufacturer": cell(row, "manufacturer"),
                "model": cell(row, "type"),
                "mode": mode_name, "channels": chans,
                "universe": universe, "address": address,
                "x": float(cell(row, "x", "0") or 0),
                "y": float(cell(row, "y", "0") or 0),
                "z": float(cell(row, "z", "0") or 0),
                "kind": ("truss" if float(cell(row, "y", "0") or 0) >= 2.0
                         else "floor"),
                # Role drives which heads a concept's `active` roles light
                # (see _a_import_show); missing/garbage falls back to
                # "generic" so old CSVs without the column still import.
                "role": (cell(row, "role") or "generic").strip().lower()
                        or "generic",
                "curve": "linear",
                "map": mapping, "mapped": mapped,
            })
        if not heads:
            raise ValueError("no head rows in csv")
        # Commit phase only: everything above was read-only (text parsing +
        # the cached fixture lookups), so a 200-head import never held the
        # lock the output thread needs.  _replace_patch validates and rolls
        # back, so a bad row leaves the rig exactly as it was.
        with self.lock:
            self._replace_patch(heads)
        return {"heads": len(heads),
                "summary": f"imported {len(heads)} heads from csv"}

    def _a_patch_clear(self, **_):
        n = len(self.patch)
        self._replace_patch([])
        return {"removed": n, "summary": f"cleared {n} heads"}

    def _replace_patch(self, heads: list[dict]) -> None:
        """Validate + swap in a whole new patch (rolls back on error)."""
        old = self.patch
        self.patch = heads
        try:
            self._validate_patch()
        except ValueError:
            self.patch = old
            raise
        self.patch_rev += 1
        self.groups.clear()
        self.selected.clear()
        self.programmer.clear()

    def _validate_patch(self) -> None:
        seen, by_pos = set(), {}
        for h in self.patch:
            if h["head_no"] in seen:
                raise ValueError(f"duplicate head number {h['head_no']}")
            seen.add(h["head_no"])
            # Safety: a head may never straddle the 512-channel boundary -
            # its tail channels would fall off the universe and be dropped.
            if (h["universe"] < 1 or h["address"] < 1
                    or h["address"] - 1 + h["channels"] > SLOTS):
                raise ValueError(
                    f"head {h['head_no']} straddles a universe boundary "
                    f"(universe {h['universe']} address {h['address']} + "
                    f"{h['channels']} channels > {SLOTS}) - run AUTO PATCH "
                    f"to repack")
            p0 = _pos(h["universe"], h["address"])
            for other0, other_no in by_pos.items():
                if p0 < other0 + 1 and other0 < p0 + h["channels"]:
                    raise ValueError(
                        f"head {h['head_no']} overlaps head {other_no}")
            by_pos[p0] = h["head_no"]
