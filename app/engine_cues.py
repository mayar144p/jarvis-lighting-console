"""Cue stacks and playbacks: record, edit, GO, fades, follow.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import threading

from app import fxlib as fxlib_mod
from app.engine_base import _UNSET, _clamp, _truthy, attr_domain
from app.engine_support import HTP_ROLES


class CueMixin:
    # --- playbacks / cues -------------------------------------------------
    def _playback(self, playback) -> dict:
        num = int(playback)
        if not 1 <= num <= len(self.playbacks):
            raise ValueError(f"playback must be 1..{len(self.playbacks)}")
        return self.playbacks[num - 1]

    def _cue_fx_capture(self, heads) -> tuple[list[dict], list[int]]:
        """The effects running on `heads` from the programmer side (not a
        button's, not another cue's): what a recorded cue plays again."""
        owned = {i for run in self.quick_active.values() for i in (run.get("fx_ids") or [])}
        items, ids = [], []
        for f in self.fx:
            if not (f.get("lib") or f.get("pix") or f.get("steps")) or f["id"] in owned or f.get("cue_pb") \
                    or f.get("live") or not set(f.get("heads") or []) & heads:
                continue
            params = {k: v for k, v in (f.get("params") or {}).items() if not k.startswith("_")}
            if f.get("pix"):
                items.append({"name": "pix:" + f["pix"], "pix": f["pix"], "params": params, "heads": list(f["heads"]),
                              "label": "Gradient" if f["pix"] == "gradient" else "Picture"})
                ids.append(f["id"])
                continue
            if f.get("steps"):
                st = next((s for s in self._steps() if s["id"] == f["steps"]), None)
                items.append({"name": "step:" + f["steps"], "steps": f["steps"], "params": params,
                              "heads": list(f["heads"]), "label": st["name"] if st else "Step effect"})
                ids.append(f["id"])
                continue
            item = {"name": f["lib"], "params": dict(f.get("params") or {}), "heads": list(f["heads"])}
            if f.get("across"):
                item["across"] = True
            if f.get("move"):
                item["move"] = f["move"]
            if f.get("roam"):
                item["roam"] = [z["id"] for z in f["roam"]]
            items.append(item)
            ids.append(f["id"])
        return items[:12], ids

    def _resync_cue_fx(self) -> None:
        """Cue effects follow the playbacks as they now are - after undo,
        loading a show or importing a stack, the old ones don't linger on
        whatever heads now have those numbers."""
        self.fx = [f for f in self.fx if not f.get("cue_pb")]
        for pb in self.playbacks:
            if pb.get("active") and 0 <= pb.get("index", -1) < len(pb.get("stack") or []):
                self._cue_fx_start(pb, pb["stack"][pb["index"]])

    def _cue_fx_stop(self, pb: dict) -> None:
        self.fx = [f for f in self.fx if f.get("cue_pb") != pb["n"]]

    def _cue_fx_start(self, pb: dict, cue: dict) -> None:
        """A cue's effects take over from the last cue's on this playback."""
        self._cue_fx_stop(pb)
        patched = {h["head_no"] for h in self.patch}
        for item in cue.get("fx") or []:
            # the lights still patched: one removed light must not drop the
            # effect from all the others
            heads = [n for n in item.get("heads") or [] if n in patched]
            if not heads:
                continue
            try:
                if item.get("roam"):
                    r = self._a_roam(zones=item["roam"], heads=heads, **(item.get("params") or {}))
                    r["fx"] = r["id"]
                elif item.get("name") == "shape":
                    r = {"fx": self._shape_start(item.get("params") or {}, heads)}
                elif item.get("pix"):
                    r = {"fx": self._pix_start(item["pix"], item.get("params") or {}, heads)}
                elif item.get("steps"):
                    p = item.get("params") or {}
                    r = self._a_step_fx_run(id=item["steps"], heads=heads, speed=p.get("speed", 1.0),
                                            beats=p.get("beats"), space=p.get("space"))
                else:
                    r = self._a_run_fx_named(item["name"], item.get("params") or {}, None,
                                             heads, None, across=bool(item.get("across")))
            except (ValueError, KeyError):
                continue
            for f in self.fx:
                if f["id"] == r.get("fx"):
                    f["cue_pb"] = pb["n"]
                    if item.get("move"):
                        f["move"] = item["move"]

    def _a_record_cue(self, playback=None, name="", fade=None, hold=None,
                      cue=None, follow=None, mode="replace", effects=True,
                      cue_only=False, **_):
        """Record the programmer as a cue.  Over an existing cue, `mode`:
        "replace" (the cue becomes exactly the programmer), "merge" (the
        programmer's values are added into the cue, the rest of it kept) or
        "insert" (a new cue at that number, the later ones move down).
        Recording over a cue keeps its name and times unless new ones are
        given - "Update" used to rename it "Cue 3" and zero its fade."""
        pb = self._playback(playback if playback is not None else 1)
        mode = str(mode or "replace").lower()
        if mode not in ("replace", "merge", "insert"):
            raise ValueError("mode is replace, merge or insert")
        values = {h: dict(row) for h, row in self.programmer.items() if row}
        # the effects running on the lights in the programmer (or, with
        # nothing set, on the selection) go into the cue too
        fx_items, fx_ids = self._cue_fx_capture(set(values) or set(self.selected)) \
            if _truthy(effects) else ([], [])
        if not values and not fx_items:
            raise ValueError("programmer is empty - set something first")
        cue_n = int(cue) if cue else len(pb["stack"]) + 1
        if cue_n < 1:
            raise ValueError("cue numbers start at 1")
        old = pb["stack"][cue_n - 1] if cue_n <= len(pb["stack"]) and mode != "insert" else None
        # cue only (tracking list): what the lights had here before, so the
        # next cue can put it back
        before = self._tracked(pb, cue_n - 1) if _truthy(cue_only) and old is not None else \
            (self._tracked(pb, cue_n - 2) if _truthy(cue_only) and cue_n >= 2 else {})
        before = {h: dict(r) for h, r in before.items()}
        prog_vals = {h: dict(r) for h, r in values.items()}
        if old is not None and mode == "merge":
            merged = {int(k): dict(v) for k, v in (old.get("values") or {}).items()}
            for h, row in values.items():
                merged.setdefault(int(h), {}).update(row)
            values = merged
        entry = {"n": cue_n,
                 "name": str(name).strip() or (old or {}).get("name") or f"Cue {cue_n}",
                 "fade_s": float(fade if fade is not None else (old or {}).get("fade_s", 0.0)),
                 "hold_s": float(hold if hold is not None else (old or {}).get("hold_s", 0.0)),
                 "values": values}
        if old is not None and old.get("times"):
            entry["times"] = dict(old["times"])       # its part times stay
        fx_list = list((old or {}).get("fx") or []) if old is not None and mode == "merge" else []
        for item in fx_items:
            # merge: a new effect replaces one of the same kind on the same lights
            def family(x):
                return "pix" if x.get("pix") else ("step:" + x["steps"]) if x.get("steps") \
                    else (fxlib_mod.FX.get(x["name"]) or {}).get("group") or x["name"]
            group = family(item)
            fx_list = [x for x in fx_list if not (family(x) == group
                                                  and set(x.get("heads") or []) & set(item["heads"]))]
            fx_list.append(item)
        if fx_list:
            entry["fx"] = fx_list
        # recorded effects now live in the cue, as the programmer does
        if fx_ids:
            self.fx = [f for f in self.fx if f["id"] not in fx_ids]
        if old is not None and follow is None and old.get("follow_s") is not None:
            entry["follow_s"] = old["follow_s"]
        if mode == "insert" and cue_n <= len(pb["stack"]):
            if follow is not None:
                entry["follow_s"] = max(0.0, float(follow))
            pb["stack"].insert(cue_n - 1, entry)
            for i, c in enumerate(pb["stack"], start=1):
                c["n"] = i
            kept = self._cue_only_fix(pb, cue_n - 1, before, prog_vals) if _truthy(cue_only) else 0
            self._blind_recorded(pb, cue_n)
            self.programmer.clear()
            return {"playback": pb["n"], "cue": cue_n, "cues": len(pb["stack"]),
                    "summary": f"inserted cue {cue_n} on PB{pb['n']}" + (" (cue only)" if kept else "")}
        # follow_s is stored ONLY when the caller gave one, so a new cue
        # INHERITS the stack's follow.
        #
        # Writing an explicit 0 here - "a new cue waits by default" - looked
        # safer and was wrong, because `_cue_follow` gives the cue's own
        # value precedence over the stack delay.  So every fresh cue became a
        # hard wait, `follow_set(delay=2)` stopped having any effect at all,
        # and fourteen existing follow tests failed at once.  That is the
        # whole argument for the absent/0 distinction: absent is "no
        # opinion, use the stack's", and 0 is "wait, whatever the stack
        # says".  A cue created without being told otherwise has no opinion.
        if follow is not None:
            entry["follow_s"] = max(0.0, float(follow))
        while len(pb["stack"]) < cue_n - 1:
            pad = len(pb["stack"]) + 1
            pb["stack"].append({"n": pad, "name": f"Cue {pad}", "fade_s": 0.0,
                                "hold_s": 0.0, "values": {},
                                # A padding cue has NO follow of its own, so
                                # it inherits.  Giving it 0 would make every
                                # gap in a show a hard stop; giving it the
                                # stack delay would make it fire over a slot
                                # nobody programmed.  Inheriting is the only
                                # answer that is neither.
                                })
        if cue_n <= len(pb["stack"]):
            pb["stack"][cue_n - 1] = entry
        else:
            pb["stack"].append(entry)
        kept = self._cue_only_fix(pb, cue_n - 1, before, prog_vals) if _truthy(cue_only) else 0
        self._blind_recorded(pb, cue_n)
        self.programmer.clear()
        verb = "merged into" if old is not None and mode == "merge" else "updated" if old is not None else "recorded"
        return {"playback": pb["n"], "cue": cue_n, "cues": len(pb["stack"]),
                "summary": f"{verb} cue {cue_n} on PB{pb['n']}" + (" (cue only)" if kept else "")}

    # --- cue-list editing -------------------------------------------------
    # You could record a cue and you could step through one.  You could not
    # INSERT one, DELETE one, MOVE one or RENAME one - so a cue list could
    # only ever be built in the order it was recorded, and a wrong cue in
    # the middle meant re-recording everything after it.  On a real desk
    # these are the buttons you press most while building a show.
    #
    # Every one of these RENUMBERS, because a cue list with a gap in it is
    # a list the operator cannot reason about, and `cue_go` clamps to the
    # stack length - so a gap would silently make the last cue unreachable.

    @staticmethod
    def _renumber(stack: list[dict]) -> None:
        for i, cue in enumerate(stack, 1):
            cue["n"] = i
            if not str(cue.get("name") or "").strip() or \
                    str(cue["name"]).strip().lower().startswith("cue "):
                # Keep an operator's own name; refresh only the default one,
                # which was numbered against the OLD position and would
                # otherwise read "Cue 4" at position 3 after a delete.
                cue["name"] = f"Cue {i}"

    def _a_insert_cue(self, playback=None, at=None, cue=None, name="",
                      fade=None, hold=None, follow=None, **_):
        """Insert an EMPTY cue at a position, shifting the rest down.

        Empty on purpose: the point is to open a slot to record into, not
        to invent a look.  A blank cue in the middle is a "record me"
        marker, and it fades to whatever the previous cue left, which is
        exactly what an operator wants at that point in a build.
        """
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        pos = int(at if at is not None else (cue if cue else len(stack) + 1))
        pos = max(1, min(pos, len(stack) + 1))
        entry = {"n": pos, "name": str(name).strip() or f"Cue {pos}",
                 "fade_s": float(fade if fade is not None else 0.0),
                 "hold_s": float(hold if hold is not None else 0.0),
                 "values": {}}
        # Inherits unless told otherwise, like a recorded cue - see
        # record_cue for why "a new cue waits by default" was the wrong
        # call: a cue's own follow_s BEATS the stack delay, so writing an
        # explicit 0 made follow_set(delay=...) unreachable.
        #
        # This assignment was MISSING for a while, so insert_cue took a
        # `follow` argument, accepted it and threw it away.  A live stack
        # built through the API came back with every cue inheriting and
        # the parameter looked like it worked, because nothing
        # complained.  An argument that is accepted and ignored is worse
        # than one that is rejected.
        if follow is not None:
            entry["follow_s"] = max(0.0, float(follow))
        stack.insert(pos - 1, entry)
        self._renumber(stack)
        return {"playback": pb["n"], "cue": pos, "cues": len(stack),
                "summary": f"inserted an empty cue at {pos} on PB{pb['n']}"}

    def _a_delete_cue(self, playback=None, cue=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        num = int(cue)
        if not 1 <= num <= len(stack):
            raise ValueError(f"cue must be 1..{len(stack)}")
        was_live = pb["index"]
        gone = stack.pop(num - 1)
        self._renumber(stack)
        if was_live == num - 1:
            # Deleting the cue that is on air: park the pointer rather than
            # leave it past the end, where the next GO would do nothing.
            pb["index"] = min(was_live, len(stack) - 1)
        elif was_live > num - 1:
            pb["index"] = was_live - 1
        if not stack:
            pb["index"] = -1
            pb["active"] = False
            self._cue_fx_stop(pb)
        return {"playback": pb["n"], "cues": len(stack),
                "index": pb["index"], "name": gone.get("name"),
                "summary": f"deleted cue {num} ({gone.get('name')}) "
                           f"from PB{pb['n']}"}

    def _a_move_cue(self, playback=None, cue=None, to=None, **_):
        """Reorder: move a cue to another position."""
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        src = int(cue)
        dst = int(to if to is not None else cue)
        if not 1 <= src <= len(stack):
            raise ValueError(f"cue must be 1..{len(stack)}")
        if not 1 <= dst <= len(stack):
            raise ValueError(f"destination must be 1..{len(stack)}")
        if src == dst:
            return {"playback": pb["n"], "cue": src, "cues": len(stack),
                    "summary": f"cue {src} is already there"}
        entry = stack.pop(src - 1)
        stack.insert(dst - 1, entry)
        self._renumber(stack)
        # Follow the cue the pointer was on, not the index: after a move,
        # index dst-1 is a different cue, and leaving the pointer behind
        # would make GO continue from the wrong place.
        if pb["index"] == src - 1:
            pb["index"] = dst - 1
        elif src - 1 < pb["index"] <= dst - 1:
            pb["index"] -= 1
        elif dst - 1 <= pb["index"] < src - 1:
            pb["index"] += 1
        return {"playback": pb["n"], "cue": dst, "from": src,
                "cues": len(stack), "index": pb["index"],
                "summary": f"moved cue {src} to {dst} on PB{pb['n']}"}

    def _a_rename_cue(self, playback=None, cue=None, name="", **_):
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        num = int(cue)
        if not 1 <= num <= len(stack):
            raise ValueError(f"cue must be 1..{len(stack)}")
        label = str(name).strip()
        if not label:
            raise ValueError("a cue needs a name")
        stack[num - 1]["name"] = label
        return {"playback": pb["n"], "cue": num, "name": label,
                "summary": f"renamed cue {num} to {label!r}"}

    CUE_PARTS = ("intensity", "colour", "position", "beam")

    def _a_edit_cue(self, playback=None, cue=None, fade=None, hold=None,
                    name=None, follow=_UNSET, times=None, **_):
        """Change a cue's timing or name in place, without re-recording it."""
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        num = int(cue)
        if not 1 <= num <= len(stack):
            raise ValueError(f"cue must be 1..{len(stack)}")
        entry = stack[num - 1]
        changed = []
        if fade is not None:
            entry["fade_s"] = max(0.0, float(fade))
            changed.append(f"fade {entry['fade_s']:g}s")
        if hold is not None:
            entry["hold_s"] = max(0.0, float(hold))
            changed.append(f"hold {entry['hold_s']:g}s")
        if name is not None and str(name).strip():
            entry["name"] = str(name).strip()
            changed.append("name")
        if isinstance(times, dict):
            # a part's own fade; empty / None puts it back on the cue's fade
            cur = dict(entry.get("times") or {})
            for part, v in times.items():
                if part not in self.CUE_PARTS:
                    raise ValueError(f"a cue part is one of {', '.join(self.CUE_PARTS)}")
                if v in (None, ""):
                    cur.pop(part, None)
                else:
                    cur[part] = max(0.0, min(600.0, float(v)))
            if cur:
                entry["times"] = cur
            else:
                entry.pop("times", None)
            changed.append("part times")
        # A SENTINEL, not a None default, and that is the whole trick.
        #
        # `follow` has to be able to say three things: set a number, set
        # zero, and CLEAR the cue so it goes back to inheriting.  Three
        # states need three values, and None is already one of them - so the
        # default has to be something else.  The first version used
        # `if "follow" in _`, checking the leftover kwargs bag, which is the
        # usual trick for this - and it never fired, because `follow` was a
        # NAMED parameter and therefore never reached `_`.  An edit that
        # accepts a follow, returns a sensible result, and does nothing is
        # worse than one that rejects it.
        if follow is not _UNSET:
            if follow is None:
                entry.pop("follow_s", None)
                changed.append("follow back to the stack default")
            else:
                entry["follow_s"] = max(0.0, float(follow))
                changed.append("follow %s"
                               % ("wait" if entry["follow_s"] <= 0
                                  else f"{entry['follow_s']:g}s"))
        if not changed:
            raise ValueError(
                "give a fade, a hold, a name or a follow")
        return {"playback": pb["n"], "cue": num,
                "fade_s": entry["fade_s"], "hold_s": entry["hold_s"],
                "name": entry["name"],
                "follow_s": entry.get("follow_s"),
                "summary": f"cue {num}: " + ", ".join(changed)}

    def _a_cue_info(self, playback=None, cue=None, **_):
        """What a cue ACTUALLY contains.

        The feed sent only `{n, name, fade_s}` - so a recorded cue was
        opaque.  You could not see what it lit, on how many heads, in what
        colour, and that is the first thing you want to know about a cue
        somebody else programmed.  This reads the stored values.
        """
        pb = self._playback(playback if playback is not None else 1)
        stack = pb["stack"]
        num = int(cue)
        if not 1 <= num <= len(stack):
            raise ValueError(f"cue must be 1..{len(stack)}")
        entry = stack[num - 1]
        values = entry.get("values") or {}
        roles: dict[str, list[int]] = {}
        for head_no, row in values.items():
            for role in row:
                roles.setdefault(role, []).append(int(head_no))
        patched = {h["head_no"] for h in self.patch}
        # A head the patch no longer has is the "this cue does nothing"
        # diagnosis, so it is separated from the ones that will actually
        # respond rather than being counted in with them.
        gone = sorted(n for n in values if int(n) not in patched)
        live = sorted(n for n in values if int(n) in patched)
        colours = [r for r in ("red", "green", "blue", "wheel", "white")
                   if r in roles]
        return {
            "playback": pb["n"], "cue": num, "name": entry.get("name"),
            "fade_s": entry.get("fade_s"), "hold_s": entry.get("hold_s"),
            "heads": live, "stale_heads": gone,
            "roles": sorted(roles),
            "role_heads": {k: sorted(v) for k, v in sorted(roles.items())},
            "colour": colours,
            "summary": (f"cue {num} ({entry.get('name')}): {len(live)} head(s), "
                        + (f"colour via {', '.join(colours)}; " if colours else "")
                        + f"{len(roles)} attribute(s)"
                        + (f"; {len(gone)} head(s) no longer patched"
                           if gone else "")),
        }

    def _scaled_cue(self, cue: dict, at: float | None) -> dict:
        """A cue's values at `at` percent, scaled per each head's own domain.

        "Execute at 50%" is a real console feature (grandMA's At, Eos's
        Execute At) and it is the one thing you cannot get by scaling the
        master, because the master also touches playback, programmer and
        effects.

        The scale is computed ONCE here, at go-time, against each head's
        own channel width - a level is 0-100, a single-slot channel 0-255,
        a 16-bit pair 0-65535.  Doing it per tick instead would put a dict
        lookup on the 40 Hz path for a feature almost nobody uses, and
        scaling everything by a flat 255 would be wrong for a level and
        for a 16-bit pan at the same time.  This is the same domain rule
        as `attr_domain`, and the same reason it exists.
        """
        if at is None or float(at) >= 100.0:
            return cue["values"]
        factor = _clamp(float(at), 0, 100) / 100.0
        by_no = {h["head_no"]: h for h in self.patch}
        out: dict[int, dict] = {}
        for head_no, row in (cue["values"] or {}).items():
            head = by_no.get(int(head_no))
            scaled = {}
            for role, value in (row or {}).items():
                top = 100 if role in HTP_ROLES else (
                    attr_domain(head, role) if head else 255)
                scaled[role] = _clamp(int(round(float(value) * factor)),
                                      0, top)
            out[int(head_no)] = scaled
        return out

    def _goto(self, pb: dict, index: int, now: float,
              at: float | None = None) -> dict:
        stack = pb["stack"]
        index = max(0, min(index, len(stack) - 1))
        was = self._pb_values(pb, now) if pb["index"] >= 0 else {}
        cue = stack[index]
        # tracking: what the cues so far add up to; move in black: the next
        # cue's positions on the lights that are dark now
        target = self._scaled_cue({"values": self._tracked(pb, index)}, at)
        pb["target_mib"] = self._mib(pb, index, target)
        pb["fade"] = {"t0": now,
                      "dur": float(cue.get("fade_s") or 0.0), "from": was,
                      # its own fade per kind of value (position 4 s, colour 0...)
                      "parts": dict(cue.get("times") or {})}
        # The target is kept, not re-derived: after the fade has finished
        # `_pb_values` used to return `cue["values"]` directly, which would
        # have snapped the rig back to full the instant the fade ended.
        pb["target"] = target
        pb["at"] = at
        pb["index"] = index
        pb["active"] = True
        self._cue_fx_start(pb, cue)
        self._order += 1
        pb["order"] = self._order
        self._arm_follow(pb, now)              # manual steps re-arm here
        res = {"playback": pb["n"], "cue": cue["n"], "name": cue["name"],
               "cues": len(stack), "active": True, "fade_s": cue["fade_s"],
               "at": at}
        errors = self._run_cue_actions(pb, cue)
        if errors:
            res["action_errors"] = errors
        return res

    def _a_cue_go(self, playback=None, cue=None, at=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        if not pb["stack"]:
            raise ValueError(f"playback {pb['n']} has no cues - record one")
        at_pct = None if at is None else _clamp(at, 0, 100)
        if cue is not None:
            # Take a named cue directly.  GO alone advances by one, which
            # means a specific cue is otherwise only reachable by pressing
            # it the right number of times - not how anyone runs a show,
            # and impossible from a list the operator can see and click.
            # `cue` is the cue's NUMBER as shown to the operator, not a
            # stack position, so the two can never be confused.
            want = str(cue).strip()
            hit = None
            for i, c in enumerate(pb["stack"]):
                if str(c["n"]) == want:
                    hit = i
                    break
            if hit is None:
                raise ValueError(
                    f"playback {pb['n']} has no cue {want!r} - it holds "
                    + ", ".join(str(c["n"]) for c in pb["stack"]))
            res = self._goto(pb, hit, self._clock(), at_pct)
            res["summary"] = (f"PB{pb['n']} -> cue {res['cue']} "
                              f"{res['name']!r}"
                              + (f" at {at_pct}%" if at_pct is not None
                                 else ""))
            return res
        at_end = pb["index"] >= len(pb["stack"]) - 1
        if not pb["active"]:
            index = 0                   # released stack: GO starts it over
        elif at_end and not pb["follow"]["loop"]:
            # Already on the last cue.  Re-running it silently (what the
            # index clamp used to do) looks like a dead GO button, so say
            # what happened instead.
            cue = pb["stack"][-1]
            return {"playback": pb["n"], "cue": cue["n"], "name": cue["name"],
                    "cues": len(pb["stack"]), "active": True, "ended": True,
                    "summary": f"PB{pb['n']} is on the last cue "
                               f"({cue['name']!r}) - enable loop or record a "
                               f"new cue to go on"}
        elif at_end:
            index = 0                   # looping stack wraps to the top
        else:
            index = pb["index"] + 1
        res = self._goto(pb, index, self._clock(), at_pct)
        res["summary"] = (f"PB{pb['n']} -> cue {res['cue']} {res['name']!r}"
                          + (f" at {at_pct}%" if at_pct is not None else ""))
        return res

    def _a_cue_back(self, playback=None, at=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        if not pb["stack"]:
            raise ValueError(f"playback {pb['n']} has no cues")
        start = pb["index"] if pb["index"] >= 0 else 0
        return self._goto(pb, start - 1, self._clock(),
                          None if at is None else _clamp(at, 0, 100))

    def _a_cue_forward(self, playback=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        if not pb["stack"]:
            raise ValueError(f"playback {pb['n']} has no cues")
        if pb["index"] >= len(pb["stack"]) - 1:
            cue = pb["stack"][-1]
            return {"playback": pb["n"], "cue": cue["n"], "name": cue["name"],
                    "cues": len(pb["stack"]), "ended": True,
                    "active": pb["active"],
                    "summary": f"PB{pb['n']} is already on the last cue"}
        start = pb["index"] if pb["index"] >= 0 else -1
        return self._goto(pb, start + 1, self._clock())

    def _a_playback_level(self, playback=None, level=None, xfade=None,
                          **_):
        """Set a playback's fader level, optionally over a crossfade.

        `xfade` in seconds is the time to travel to the new level.  Without
        it the move is instant, which is the default because an operator
        building a show wants the level they asked for NOW - a crossfade on
        every level change would make programming a fader feel broken.

        The fader is not the cue's fade, and confusing the two is a real
        trap: `fade_s` moves the LOOK inside a cue, this moves the FADER
        that scales the whole stack.  A cue fading in under a playback at
        zero shows nothing at all, and a fader snapping to 100 under an
        up-cue punches the entire stack to full.  Both are set here and
        independently.
        """
        pb = self._playback(playback if playback is not None else 1)
        if level is None:
            raise ValueError("level is required (0-100)")
        now = self._clock()
        want = _clamp(level, 0, 100)
        dur = 0.0
        if xfade not in (None, ""):
            try:
                dur = float(xfade)
            except (TypeError, ValueError):
                raise ValueError("not a number: %r" % (xfade,)) from None
            dur = max(0.0, min(600.0, dur))
        # Where the fader is NOW, not where it was left - otherwise a second
        # level change during a crossfade jumps back to the old start and
        # the fader visibly stutters.
        frm = self._pb_level_now(pb, now) if dur > 0 else int(pb.get("level", 100))
        pb["level"] = want
        pb["xfade"] = ({"from": frm, "t0": now, "dur": dur} if dur > 0
                       else None)
        return {"playback": pb["n"], "level": want, "xfade_s": dur or None,
                "summary": "PB%d level %g%%%s" % (
                    pb["n"], want,
                    " over %gs" % dur if dur > 0 else "")}

    def _a_playback_release(self, playback=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        pb["active"] = False
        self._cue_fx_stop(pb)
        pb["fade"] = None
        pb["follow"]["at"] = None               # no auto-advance while off
        return {"playback": pb["n"], "active": False,
                "summary": f"PB{pb['n']} released"}

    def _a_playback_activate(self, playback=None, **_):
        pb = self._playback(playback if playback is not None else 1)
        if not pb["stack"]:
            raise ValueError(f"playback {pb['n']} has no cues")
        if pb["index"] < 0:
            return self._goto(pb, 0, self._clock())
        pb["active"] = True
        self._order += 1
        pb["order"] = self._order
        self._cue_fx_start(pb, pb["stack"][pb["index"]])
        self._arm_follow(pb, self._clock())     # resume auto-advance
        return {"playback": pb["n"], "active": True, "cue":
                pb["stack"][pb["index"]]["n"]}

    # --- auto-follow (automatic cue-stack advance) ----------------------
    FOLLOW_DEFAULT_DELAY = 2.0   # delay when neither follow_set delay nor

    FOLLOW_TICK = 0.05           # cue hold time is set; ticker granularity

    def _cue_follow(self, pb: dict) -> float | None:
        """How long THIS cue waits before the next one can auto-start.

        None means WAIT FOR GO, which is the default and the most common
        state in any real show.  A number is an automatic advance after that
        many seconds.

        WHY THIS IS PER CUE AND NOT PER PLAYBACK
        --------------------------------------
        It used to be a single `delay` on the playback, so arming follow
        made EVERY cue in the stack behave identically: either all of them
        waited for GO (hold 0, delay 0, default 2s) or all of them ran away
        from each other.  A show is "wait here, wait here, then run by
        itself through the build" - and that is unrepresentable when the
        wait is a property of the stack rather than of the cue in it.

        So the cue carries it, and this is the precedence:

            cue["follow_s"] present  ->  the cue's own value wins
                                        0 or negative means WAIT
            absent                   ->  inherit the playback's delay
            playback delay 0         ->  fall back to the cue's hold
            neither                  ->  FOLLOW_DEFAULT_DELAY

        The distinction between "absent" and "0" is the whole point: absent
        inherits the stack's default so an existing show keeps behaving
        exactly as it did, while an explicit 0 is a deliberate "hold this
        cue until someone presses GO".
        """
        if not pb.get("stack") or pb.get("index", -1) < 0:
            return None
        try:
            cue = pb["stack"][pb["index"]]
        except IndexError:
            return None
        own = cue.get("follow_s")
        if own is not None:
            try:
                v = float(own)
            except (TypeError, ValueError):
                return None
            return v if v > 0 else None          # 0 or less == wait for GO
        delay = float(pb["follow"].get("delay") or 0)
        if delay > 0:
            return delay
        hold = float(cue.get("hold_s") or 0)
        return hold if hold > 0 else self.FOLLOW_DEFAULT_DELAY

    def _arm_follow(self, pb: dict, now: float | None = None) -> None:
        """(Re)arm the auto-advance deadline of one playback.

        A cue that says WAIT is simply not armed, so the ticker leaves it
        alone and a manual GO is the only thing that moves it on.  That is
        what makes a mixed stack possible, and it is why this has to be a
        per-cue decision rather than a per-stack one: arming the playback
        is a promise that SOMETHING in it may run by itself, not that
        everything will.

        Every manual cue step funnels through _goto, which re-arms here -
        so a manual GO always cancels a pending automatic advance.
        """
        f = pb["follow"]
        if (not f["on"] or f["paused"] or not pb["active"]
                or pb["index"] < 0 or not pb["stack"]):
            f["at"] = None
            return
        now = self._clock() if now is None else now
        delay = self._cue_follow(pb)
        f["at"] = None if delay is None else now + delay

    def _tick_follow(self, now: float | None = None) -> list[dict]:
        """Advance every armed cue stack once.

        Runs from the follow ticker thread (and directly from tests
        with an injected clock).  All state changes happen under the
        engine lock, so manual control, cue changes, playback stop and
        shutdown always win the race against auto-advance.  No timers
        are created here - deadlines are plain numbers, so nothing can
        leak.
        """
        with self.lock:
            now = self._clock() if now is None else now
            fired: list[dict] = []
            for pb in self.playbacks:
                f = pb["follow"]
                if not f["on"] or f["paused"] or f["at"] is None:
                    continue
                if not pb["active"] or pb["index"] < 0 or not pb["stack"]:
                    f["at"] = None
                    continue
                if now < f["at"]:
                    continue
                nxt = pb["index"] + 1
                if nxt >= len(pb["stack"]):
                    if not f["loop"]:
                        f["at"] = None         # end of stack: hold last cue
                        continue
                    nxt = 0                     # loop back to cue 1
                res = self._goto(pb, nxt, now)  # re-arms the next deadline
                fired.append({"playback": pb["n"], "cue": res["cue"],
                              "name": res["name"]})
            if fired:
                self._log("follow", True, None,
                          f"auto-advanced {len(fired)} cue(s)")
            return fired

    def _follow_loop(self) -> None:
        while not self._follow_stop.wait(self.FOLLOW_TICK):
            try:
                self._tick_follow()
            except Exception as exc:            # never die silently
                self.output["last_error"] = f"follow: {exc}"

    def _ensure_follow_thread(self) -> None:
        if self._follow_thread is not None and self._follow_thread.is_alive():
            return
        self._follow_stop.clear()
        thread = threading.Thread(target=self._follow_loop,
                                  name="jarvis-follow", daemon=True)
        self._follow_thread = thread
        thread.start()

    def _stop_follow_thread(self) -> None:
        self._follow_stop.set()
        thread = self._follow_thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=2.0)
        self._follow_thread = None

    def _sync_follow_thread(self) -> None:
        """Ticker runs while any stack has follow armed, stops otherwise."""
        try:
            if any(pb["follow"]["on"] for pb in self.playbacks):
                self._ensure_follow_thread()
            elif self._follow_thread is not None:
                self._stop_follow_thread()
        except Exception:                       # never break an action
            pass

    def _follow_public(self, pb: dict, now: float | None = None) -> dict:
        f = pb["follow"]
        now = self._clock() if now is None else now
        pending = f["at"] if (f["on"] and not f["paused"]) else None
        # What the stack is ACTUALLY about to do, which is not the same
        # thing as whether follow is armed.  A stack can be armed and still
        # be sitting on a cue that waits, and a UI that only reads `on`
        # would say "auto" over a cue that is going to hold until somebody
        # presses GO.
        cue_follow = self._cue_follow(pb)
        return {"on": f["on"], "delay": f["delay"], "paused": f["paused"],
                "loop": f["loop"],
                "cue_follow_s": cue_follow,
                "waits": cue_follow is None,
                "in": (round(max(0.0, pending - now), 1)
                       if pending is not None else None)}

    def _a_follow_set(self, playback=None, delay=None, on=None, pause=None,
                      loop=None, **_):
        """Configure auto-follow (automatic cue-stack advance).

        delay  seconds between cues (0 = use each cue's hold time)
        on     arm / disarm automatic advancing
        pause  hold on the current cue without disarming
        loop   wrap to cue 1 after the last cue (default: stop at end)

        Enabling or resuming re-arms the deadline from NOW, so a partly
        elapsed delay never fires early; manual cue steps re-arm through
        _goto.  Disarming stops the ticker thread when no other
        playback follows (see _sync_follow_thread).
        """
        pb = self._playback(playback if playback is not None else 1)
        f = pb["follow"]
        if delay is not None:
            f["delay"] = max(0.0, float(delay))
        if loop is not None:
            f["loop"] = _truthy(loop)
        if on is not None:
            f["on"] = _truthy(on)
        if pause is not None:
            f["paused"] = _truthy(pause)
        if f["on"] and not f["paused"]:
            self._arm_follow(pb, self._clock())
        else:
            f["at"] = None
        self._sync_follow_thread()
        return {"playback": pb["n"], "follow": self._follow_public(pb),
                "summary": (f"PB{pb['n']} follow "
                            + ("on" if f["on"] else "off")
                            + (f" every {f['delay']:g}s" if f["delay"] > 0
                               else " (cue hold)"))}
