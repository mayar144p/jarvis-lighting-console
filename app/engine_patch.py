"""The patch: adding, removing, addressing and naming lights, groups, CSV.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import csv as csvmod
import io
import json
import re
from pathlib import Path

from app import config, fixlib, fixture_kind, fixtures, merge, profiles
from app import venue as venue_mod
from app.engine_base import (
    _COLOUR_ROLES,
    _FIXTURE_CACHE,
    _LEVELS_CACHE,
    MAX_UNIVERSES,
    VDIM,
    _attr_role,
    _clamp,
    _deg,
    _fallback_fixture,
    _logical_to_phys,
    _parse_hex,
    _phys_to_logical,
    _round2,
    _truthy,
    attr_domain,
    default_mode,
)
from app.engine_support import HTP_ROLES, SLOTS, channel_role, cmy_are_leds
from app.engine_support import pos as _pos
from app.engine_support import pos_to_ua as _pos_to_ua
from app.merge import FX_OUTPUT_ROLES


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

    # --- groups -------------------------------------------------------
    def _a_rename_head(self, head=None, name="", **_):
        """Give one head a name of its own (e.g. "DS left spot")."""
        h = self._head(int(head))
        label = str(name or "").strip()[:60]
        if not label:
            raise ValueError("a name is required")
        h["name"] = label
        self.patch_rev += 1
        return {"head": h["head_no"], "name": label,
                "summary": f"#{h['head_no']} is now {label!r}"}

    def _a_group_create(self, name="", heads=None, **_):
        members = [int(n) for n in (heads if heads is not None
                                    else self.selected)]
        if not members:
            raise ValueError("no heads given - select some first")
        for n in members:
            self._head(n)
        n = max((g["n"] for g in self.groups), default=0) + 1
        label = str(name).strip() or f"Group {n}"
        self.groups.append({"n": n, "name": label, "heads": members})
        return {"n": n, "name": label, "heads": members,
                "summary": f"group {label}: {len(members)} heads"}

    def _a_group_delete(self, n=None, group=None, **_):
        num = int(n if n is not None else group)
        before = len(self.groups)
        self.groups = [g for g in self.groups if g["n"] != num]
        if len(self.groups) == before:
            raise ValueError(f"no group {num}")
        return {"n": num}

    # --- selection -----------------------------------------------------
    def _a_select_heads(self, head=None, head_end=None, heads=None, add=False,
                        **_):
        # `heads` selects an EXACT list.  `head`/`head_end` select one head
        # or a numeric range, which is what shift-click on the patch list
        # uses.  The exact list is what shift-click in the 3D view needs:
        # the lights an operator picks there are not usually consecutive,
        # and a range would drag in every head in between.
        #
        # `add` extends rather than replaces, so the client can do one
        # round trip for "narrow the filter, then take everything shown"
        # instead of first selecting and then unioning client-side - which
        # is what made the filter's select-shown need two calls.
        if heads is not None:
            if isinstance(heads, (str, bytes)) or not hasattr(heads, "__iter__"):
                raise ValueError("heads must be a list of whole head numbers")
            try:
                wanted = sorted({int(h) for h in heads})
            except (TypeError, ValueError):
                raise ValueError("heads must be a list of whole head numbers")
            patched = {h["head_no"] for h in self.patch}
            unknown = [n for n in wanted if n not in patched]
            if unknown:
                raise ValueError("heads not patched: "
                                 + ", ".join(str(n) for n in unknown))
            merged = sorted(set(self.selected) | set(wanted)) if add else wanted
            self.selected = merged
            return {"selected": merged, "heads": len(wanted),
                    "total": len(merged),
                    "summary": (f"selected {len(wanted)} head(s)"
                                + (f" ({len(merged)} total)" if add
                                   and len(merged) != len(wanted) else ""))}
        if head is None:
            raise ValueError("head is required")
        lo = int(head)
        hi = int(head_end) if head_end is not None else lo
        if hi < lo:
            lo, hi = hi, lo
        patched = {h["head_no"] for h in self.patch}
        wanted = [n for n in range(lo, hi + 1) if n in patched]
        if not wanted:
            raise ValueError(f"heads {lo}..{hi} are not patched")
        merged = sorted(set(self.selected) | set(wanted)) if add else wanted
        self.selected = merged
        return {"selected": merged, "total": len(merged)}

    def _auto_groups(self) -> list[dict]:
        """Groups Jarvis makes by itself: one per kind of light ("Moving
        spots · 6") and one per truss / pole / pipe the lights hang on
        ("Front truss · 8"), plus the floor.  Worked out from the patch and
        the venue, never stored, so they follow the rig as it changes."""
        key = (self.patch_rev, json.dumps((self.venue or {}).get("rigging") or [], sort_keys=True, default=str))
        cache = getattr(self, "_auto_group_cache", None)
        if cache and cache[0] == key:
            return cache[1]
        by_type: dict[str, list[int]] = {}
        labels: dict[str, str] = {}
        by_rig: dict[str, list[int]] = {}
        rigs = {r["id"]: r for r in (self.venue or {}).get("rigging") or []}
        for h in self.patch:
            d = fixture_kind.describe(h)
            t = d.get("type") or "generic"
            by_type.setdefault(t, []).append(h["head_no"])
            labels[t] = d.get("label") or t.replace("_", " ").title()
            rid = (h.get("mount") or {}).get("rig")
            if rid not in rigs:
                near = venue_mod.nearest_rig(self.venue, float(h.get("x") or 0), float(h.get("y") or 0),
                                             float(h.get("z") or 0), reach=0.8) if rigs else None
                rid = near[0]["id"] if near else None
            if rid in rigs:
                # the pieces of one shape (a circle, a frame) are one group
                by_rig.setdefault(rigs[rid].get("group") or rid, []).append(h["head_no"])
            elif float(h.get("y") or 0) < 1.5:
                by_rig.setdefault("floor", []).append(h["head_no"])
        out = []
        for t, heads in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            name = labels[t]
            out.append({"key": f"type:{t}", "kind": "type",
                        "name": _plural(name) if len(heads) > 1 else name,
                        "heads": sorted(heads)})
        if len(by_rig) > 1 or (by_rig and len(by_type) > 1):
            for rid, heads in by_rig.items():
                piece = rigs.get(rid) or next((r for r in rigs.values() if r.get("group") == rid), {})
                name = "Floor" if rid == "floor" else piece.get("name") or rid
                out.append({"key": f"rig:{rid}", "kind": "rig", "name": name, "heads": sorted(heads)})
        self._auto_group_cache = (key, out)
        return out

    def _split_heads(self, heads: list[int], split: str | None) -> list[int]:
        """odd / even (in number order) or left / right half (by where
        they hang) of some lights."""
        if split in ("odd", "even"):
            heads = sorted(heads)
            return heads[0::2] if split == "odd" else heads[1::2]
        if split in ("left", "right"):
            by = {h["head_no"]: h for h in self.patch}
            order = sorted(heads, key=lambda n: (float(by[n].get("x") or 0), n))
            half = (len(order) + 1) // 2
            return sorted(order[:half] if split == "left" else order[half:])
        return list(heads)

    def _a_select_split(self, split="odd", **_):
        """Keep only the odd / even / left / right half of the selection."""
        split = str(split or "").lower()
        if split not in self.SPLITS:
            raise ValueError(f"split is one of {', '.join(self.SPLITS)}")
        if not self.selected:
            raise ValueError("nothing selected")
        self.selected = self._split_heads(self.selected, split)
        return {"selected": list(self.selected), "summary": f"{split}: {len(self.selected)} light(s)"}

    def _a_select_group(self, n=None, group=None, key=None, add=False, **_):
        if key not in (None, ""):
            g = next((g for g in self._auto_groups() if g["key"] == str(key)), None)
            if g is None:
                raise ValueError(f"no group {key!r}")
            heads = g["heads"]
            self.selected = list(dict.fromkeys((self.selected if _truthy(add) else []) + heads))
            return {"selected": list(self.selected), "group": g["name"]}
        num = int(n if n is not None else group)
        for g in self.groups:
            if g["n"] == num:
                self.selected = [h for h in g["heads"] if
                                 any(p["head_no"] == h for p in self.patch)]
                return {"selected": list(self.selected), "group": g["name"]}
        raise ValueError(f"no group {num}")

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

    def _a_select_all(self, include_fx=False, **_):
        """Every LIGHT (lasers and special effects only with include_fx)."""
        self.selected = [h["head_no"] for h in self.patch
                         if _truthy(include_fx) or self._head_class(h) == "light"]
        if not self.selected:
            raise ValueError("patch is empty" if not self.patch else "no lights patched")
        return {"selected": len(self.selected)}

    def _a_select_similar(self, model=None, manufacturer=None, head=None,
                          mode=None, add=False, **_):
        """Select every head of one fixture TYPE - "all 6 Intimidators".

        The single most useful selection gesture on a real rig, and it did
        not exist: the only ways in were an explicit list, a numeric range,
        a group, or literally everything.  So "colour the six movers" meant
        clicking six times, and each click REPLACED the selection, so it
        meant ctrl-clicking six times - which nobody does under time
        pressure.

        Matching is on the resolved model (and optionally the mode), so it
        survives the GDTF Share replacing a profile with an equivalently
        spelled one: "all my Intimidators" keeps working when the library
        entry underneath is renamed.
        """
        if model:
            want_model = fixtures._squash(model)
        else:
            seed = head if head is not None else (
                self.selected[0] if self.selected else None)
            found = next((h for h in self.patch
                          if h["head_no"] == int(seed or -1)), None)
            if found is None:
                raise ValueError("give a model, or select a head to copy")
            want_model = fixtures._squash(found.get("model") or "")
            model = found.get("model")
        if not want_model:
            raise ValueError("that head has no model to match")
        want_mode = str(mode or "").strip().lower()
        hits = [h["head_no"] for h in self.patch
                if fixtures._squash(h.get("model")) == want_model
                and (not want_mode
                     or str(h.get("mode") or "").strip().lower() == want_mode)]
        if manufacturer:
            want_man = str(manufacturer).strip().lower()
            hits = [n for n in hits
                    if next(h for h in self.patch if h["head_no"] == n)
                    .get("manufacturer", "").strip().lower() == want_man]
        if not hits:
            raise ValueError(f"no head uses {model!r}"
                             + (f" in mode {mode!r}" if mode else ""))
        if add:
            merged = sorted(set(self.selected) | set(hits))
        else:
            merged = sorted(hits)
        self.selected = merged
        return {"selected": len(merged), "matched": len(hits),
                "total": len(merged), "model": model,
                "summary": (f"selected {len(hits)} x {model}"
                            + (f" ({len(merged)} total)" if add
                               and len(merged) != len(hits) else ""))}

    def _a_select_query(self, role=None, universe=None, kind=None, y=None,
                        max_channels=None, add=False, **_):
        """Select by a CONDITION, the way Eos's `Query` does.

        "Everything on the front truss", "every head with a gobo channel",
        "everything that is not a PAR" - each is a question about the
        patch, and each used to be unanswerable except by eye.  This is the
        retrieval half of the same idea as `select_similar`, and the two
        compose: query the truss, then narrow to one model on it.
        """
        hits: list[int] = []
        for h in self.patch:
            roles = h.get("map") or []
            if role and str(role).strip().lower() not in roles:
                continue
            if universe and int(h["universe"]) != int(universe):
                continue
            if kind and str(h.get("kind") or "").strip().lower() != str(kind).strip().lower():
                continue
            # y is the hang height; a truss is "high", the floor is "low".
            # There is no truss concept in the patch beyond that, so the
            # threshold is a parameter rather than a hidden constant.
            if y is not None and abs(float(h.get("y", 0.0)) - float(y)) > 0.35:
                continue
            if max_channels and int(h.get("channels") or 0) > int(max_channels):
                continue
            hits.append(h["head_no"])
        if not hits:
            bits = ", ".join(f"{k}={v}" for k, v in
                             (("role", role), ("universe", universe),
                              ("kind", kind), ("y", y),
                              ("max_channels", max_channels)) if v is not None)
            raise ValueError(f"no head matches {bits}")
        merged = sorted(set(self.selected) | set(hits)) if add else sorted(hits)
        self.selected = merged
        return {"selected": len(merged), "matched": len(hits),
                "total": len(merged),
                "summary": f"selected {len(hits)} head(s)"
                           + (f" ({len(merged)} total)" if add
                              and len(merged) != len(hits) else "")}

    def _a_clear_selection(self, **_):
        self.selected = []
        return {"selected": 0}

    def _require_selection(self, lights_only: bool = False) -> list[dict]:
        if not self.selected:
            raise ValueError("nothing selected")
        wanted = set(self.selected)
        heads = [h for h in self.patch if h["head_no"] in wanted]
        if lights_only and heads:
            lights = [h for h in heads if self._head_class(h) == "light"]
            if not lights:
                raise ValueError("the selection is only lasers / special effects - "
                                 "use the Laser and SFX tabs or their FX buttons")
            heads = lights
        if not heads:
            # A selection that no longer intersects the patch must not
            # pass silently: LOCATE/intensity would report "0 heads" and
            # look like the button is broken.
            raise ValueError("selection has no patched heads - re-patch or "
                             "re-select")
        return heads

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


def _plural(name: str) -> str:
    """"Moving wash" -> "Moving washes", "Spot" -> "Spots", "PARs" stays."""
    if name.endswith("s") and not name.endswith(("ss", "sh")):
        return name
    return name + ("es" if name.endswith(("sh", "ch", "x", "ss")) else "s")
