"""Selecting lights: groups, select by click / group / query / similar,
odd-even splits, and the checks that a selection exists.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import json

from app import fixture_kind, fixtures
from app import venue as venue_mod
from app.engine_base import _truthy


class SelectMixin:
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


def _plural(name: str) -> str:
    """"Moving wash" -> "Moving washes", "Spot" -> "Spots", "PARs" stays."""
    if name.endswith("s") and not name.endswith(("ss", "sh")):
        return name
    return name + ("es" if name.endswith(("sh", "ch", "x", "ss")) else "s")
