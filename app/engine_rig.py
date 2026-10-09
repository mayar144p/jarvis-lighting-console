"""Arranging lights, per-light limits and orientation, the design lock, the venue.

Part of the Engine class (see app/engine.py): a mixin, so `self` is the
Engine and every other part is reachable through it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app import fixlib, riglib, roomshape
from app import venue as venue_mod
from app.engine_base import _attr_role, _clamp, _secrets_equal, _truthy, attr_domain


class RigMixin:
    # ------------------------------------------------------------------
    # -- arrange: align, distribute, mirror ----------------------------
    # ------------------------------------------------------------------
    #
    # THE THING YOU DO TWENTY TIMES IN AN HOUR AND CANNOT DO BY DRAGGING.
    # Six movers on a bar are not evenly spaced after you have moved them
    # individually, and making them even again is a drag on each one - or,
    # on a real console, ALIGN/DISTRIBUTE, which is two keys and works.
    #
    # The vocabulary is the industry one, and the distinctions matter:
    #
    #   align      put every selected head on ONE line, axis by axis
    #   distribute space them EVENLY along a line between two of them
    #   mirror     reflect about a centre, keeping the shape
    #
    # ALIGN uses the mean, DISTRIBUTE uses the two ends.  A row of six with
    # the middle two bunched is not misaligned (every head is off-axis) so
    # aligning is not what you want - distributing is.  Treating the two as
    # synonyms is the single most common way these features go wrong.

    def _a_align(self, heads=None, axis="x", **_):
        """Line the selection up on one axis, at the MEAN of where it is.

        `axis` is x, y, z, or "xy"/"all".  Head numbers are left alone;
        only the named axis moves.
        """
        rows = self._cmd_rows(heads)
        axes = self._axes(axis)
        if not rows:
            raise ValueError("align needs at least one head")
        moved = []
        centres = {a: sum(float(h.get(a) or 0.0) for h in rows) / len(rows)
                   for a in axes}
        for h in rows:
            before = tuple(h.get(a) for a in axes)
            for a in axes:
                h[a] = round(centres[a], 4)
            h["kind"] = h.get("kind") or ("truss"
                                          if float(h.get("y") or 0) >= 2.0
                                          else "floor")
            if tuple(h.get(a) for a in axes) != before:
                moved.append(h["head_no"])
        self.patch_rev += 1
        said = ", ".join(f"{a}={centres[a]:.2f}" for a in axes)
        return {"heads": len(moved), "moved": moved, "axis": axes,
                "centres": centres,
                "summary": (f"aligned {len(moved)} of {len(rows)} head(s) on "
                            + said)
                            + (" (already aligned)" if not moved else "")}

    def _a_distribute(self, heads=None, axis="x", **_):
        """Space the selection EVENLY between its two outermost heads.

        The ends are kept and the middle is computed, so the group stays
        where it was on stage - which is the point: distribute is for a bar
        that is the right length but the wrong spacing, not for moving it.
        """
        rows = self._cmd_rows(heads)
        axes = self._axes(axis)
        if len(rows) < 3:
            raise ValueError(
                f"distribute needs at least 3 head(s), got {len(rows)} - "
                f"with two there is nothing between them to space out")
        # The working list is sorted by POSITION and the head dicts are
        # written through, so the spacing follows where the heads are on
        # stage and not their numbers.  A rig numbered by fixture type
        # (movers 1-8, then the wash 9-20) is exactly where that
        # difference shows.
        moved = []
        for a in axes:
            ordered = sorted(rows, key=lambda h: float(h.get(a) or 0.0))
            lo = float(ordered[0].get(a) or 0.0)
            hi = float(ordered[-1].get(a) or 0.0)
            span = hi - lo
            for i, h in enumerate(ordered):
                want = (lo + span * i / (len(ordered) - 1)) if span else lo
                if abs(float(h.get(a) or 0.0) - want) > 1e-6:
                    h[a] = round(want, 4)
                    if h["head_no"] not in moved:
                        moved.append(h["head_no"])
        self.patch_rev += 1
        a = axes[0]
        return {"heads": len(moved), "moved": moved, "axis": axes,
                "summary": (f"distributed {len(moved)} of {len(rows)} head(s) "
                            f"evenly along {a}")}

    def _a_mirror(self, heads=None, axis="x", about=None, **_):
        """Reflect the selection about a centre, keeping its shape.

        `about` is the value to mirror around; without it the MEAN is used,
        so a selection is mirrored about its own centre - the usual case.

        Mirror is PER HEAD, not set-based, and the distinction matters.
        A bar of four at -3, -1, 1, 3 mirrored about 0 produces the same
        four positions, but the heads SWAP ends, so it is a real move and
        says so.  The only genuine no-op is a head already sitting on the
        line, which cannot move.  Reporting a symmetric bar as "nothing
        happened" would be a lie about which head is where.
        """
        rows = self._cmd_rows(heads)
        axes = self._axes(axis)
        if not rows:
            raise ValueError("mirror needs at least one head")
        moved = []
        centres = {}
        for a in axes:
            centres[a] = (float(about) if about is not None else
                          sum(float(h.get(a) or 0.0) for h in rows)
                          / len(rows))
        for h in rows:
            before = tuple(h.get(a) for a in axes)
            for a in axes:
                h[a] = round(2.0 * centres[a] - float(h.get(a) or 0.0), 4)
            h["kind"] = h.get("kind") or ("truss"
                                          if float(h.get("y") or 0) >= 2.0
                                          else "floor")
            if tuple(h.get(a) for a in axes) != before:
                moved.append(h["head_no"])
        self.patch_rev += 1
        said = ", ".join(f"{a}={centres[a]:.2f}" for a in axes)
        return {"heads": len(moved), "moved": moved, "axis": axes,
                "about": centres,
                "summary": (f"mirrored {len(moved)} of {len(rows)} head(s) "
                            f"about {said}")
                + (" (already on the line)" if not moved else "")}

    @staticmethod
    def _axes(axis) -> tuple[str, ...]:
        """`x`, `y`, `z`, `xy`, `all` -> the coordinate names."""
        text = re.sub(r"[^a-z]", "", str(axis or "x").lower())
        if text in ("all", "xyz"):
            return ("x", "y", "z")
        got = tuple(c for c in "xyz" if c in text)
        if not got:
            raise ValueError(
                f"axis must be x, y, z, xy or all - not {axis!r}")
        return got

    def _cmd_rows(self, heads) -> list[dict]:
        """The rows to arrange: an explicit list, or the selection."""
        if heads:
            return [self._head(int(h)) for h in heads]
        return self._require_selection()

    # ------------------------------------------------------------------
    # -- per-fixture limits and pan/tilt orientation -------------------
    # ------------------------------------------------------------------
    #
    # TWO REAL RIGGING PROBLEMS, BOTH INVISIBLE UNTIL YOU HIT THEM.
    #
    # LIMITS.  A fixture's dimmer does not really reach 0 or 255.  Many
    # LED pars have a floor: below about 5% the lamp is still lit, so a
    # scene that should be black has a faint glow on it.  And some movers
    # are hung with their pan and tilt physically opposite, so "pan left"
    # turns the beam right.  Both are properties of ONE fixture, not of
    # the show, and both were impossible to express: the rig either
    # looked wrong in a way nothing could explain, or you fudged values in
    # every cue and preset that used that fixture.
    #
    # The limits are applied in the FRAME, not in the programmer, so they
    # hold for every source - a cue, a palette, an effect, a fader - and
    # the stored value stays what the operator asked for.  Clamping on
    # write would mean the channel sheet and the encoder disagreed with
    # the desk's own history after a reload.


    def _a_set_limits(self, heads=None, head=None, role=None, attribute=None,
                      low=None, high=None, **_):
        """Clamp one attribute, on some heads, everywhere the value is used.

        `low`/`high` are in the role's own domain (0-100 for a level,
        0-255 or 0-65535 for a channel), and either end may be None to
        leave that side alone.  This is per HEAD, because a dimmer floor
        belongs to the lamp you hung, not to the model in general.
        """
        rows = ([self._head(int(head))] if head is not None
                else (self._cmd_rows(heads) if heads
                      else self._require_selection()))
        want = _attr_role(role or attribute or "")
        if want is None:
            raise ValueError("set_limits needs an attribute name")
        if low is None and high is None:
            raise ValueError(
                "set_limits needs a low or a high — that is the whole "
                "point of it")
        changed = []
        for h in rows:
            if want not in (h.get("map") or []):
                continue
            cur = dict(h.get("limits") or {})
            lo, hi = cur.get(want, (None, None))
            if low is not None:
                lo = _clamp(low, 0, attr_domain(h, want))
            if high is not None:
                hi = _clamp(high, 0, attr_domain(h, want))
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo          # a reversed range is a typo, not
                                       # an intention
            cur[want] = (lo, hi)
            h["limits"] = cur
            changed.append({"head": h["head_no"], "role": want,
                            "low": lo, "high": hi})
        if not changed:
            raise ValueError(
                f"none of the {len(rows)} head(s) has a {want} channel")
        self.patch_rev += 1
        return {"limits": changed, "heads": len(changed),
                "summary": "; ".join(
                    "head %d %s %s..%s" % (
                        c["head"], c["role"],
                        "-" if c["low"] is None else c["low"],
                        "-" if c["high"] is None else c["high"])
                    for c in changed)}

    def _a_clear_limits(self, heads=None, head=None, role=None,
                        attribute=None, **_):
        rows = ([self._head(int(head))] if head is not None
                else (self._cmd_rows(heads) if heads
                      else self._require_selection()))
        want = _attr_role(role or attribute or "") if (
            role or attribute) else None
        n = 0
        for h in rows:
            cur = h.get("limits")
            if not cur:
                continue
            if want:
                if cur.pop(want, None) is not None:
                    n += 1
            else:
                h["limits"] = {}
                n += 1
            if not h.get("limits"):
                h.pop("limits", None)
        self.patch_rev += 1
        return {"heads": n,
                "summary": f"cleared limits on {n} head(s)"}

    def _a_set_orient(self, heads=None, head=None, invert_pan=None,
                      invert_tilt=None, swap=None, clear=False, **_):
        """Hang the fixture the way it is actually rigged.

        `invert_pan` / `invert_tilt` reverse one axis; `swap` exchanges
        them.  All three are per head, because one end of a truss is
        commonly rigged inverted and the other is not.
        """
        rows = ([self._head(int(head))] if head is not None
                else (self._cmd_rows(heads) if heads
                      else self._require_selection()))
        changed = []
        for h in rows:
            if clear:
                h.pop("orient", None)
                changed.append({"head": h["head_no"], "orient": {}})
                continue
            cur = dict(h.get("orient") or {})
            for key, val in (("invert_pan", invert_pan),
                             ("invert_tilt", invert_tilt),
                             ("swap", swap)):
                if val is not None:
                    cur[key] = _truthy(val)
            h["orient"] = cur
            changed.append({"head": h["head_no"], "orient": dict(cur)})
        self.patch_rev += 1
        return {"heads": len(changed), "orient": changed,
                "summary": "; ".join(
                    "head %d %s" % (
                        c["head"],
                        ", ".join(k.replace("invert_", "invert ")
                                  for k, v in sorted(c["orient"].items())
                                  if v) or "normal")
                    for c in changed)}

    def _a_get_limits(self, heads=None, **_):
        """What limits and orientation are set, for the editor to draw."""
        rows = ([self._head(int(heads))] if isinstance(heads, int)
                else ([self._head(int(h)) for h in heads] if heads
                      else (self._require_selection() if self.selected
                            else list(self.patch))))
        return {"heads": [{
            "head": h["head_no"],
            "limits": {k: list(v) for k, v in (h.get("limits") or {}).items()},
            "orient": dict(h.get("orient") or {}),
        } for h in rows],
            "roles": sorted({r for h in rows
                             for r in (h.get("limits") or {})}),
            "summary": "limits on %d head(s)" % sum(
                1 for h in rows if h.get("limits"))}

    # ------------------------------------------------------------------
    # -- the design / operate lock ------------------------------------
    # ------------------------------------------------------------------
    #
    # THE FEATURE THAT MAKES A SHOW SURVABLE.  Two hours into a set, a
    # mouse is on the desk near a slider, and a drag that was meant to be
    # a fader is a fixture going to full in front of an audience.  Every
    # touring desk has this, and the reason it exists is not
    # tidiness - it is that the alternative is a stopped show.
    #
    # THREE STATES, because two is not enough:
    #   operate  everything is refused except what a show needs
    #   design   everything is allowed (rehearsal, programming)
    #   locked   as operate, AND the patch cannot be changed at all
    #
    # The distinction from operate matters and is not fussiness.  In
    # OPERATE you must still be able to move a fader, go a cue, set an
    # attribute in the programmer and run an effect - that is the show.
    # Re-patching a universe mid-set is not.  LOCKED refuses that too,
    # which is what you want while the rig is up and a cable is being
    # moved.
    #
    # A refused action says WHY and WHAT to press, because a lock that
    # just fails looks like a broken console and gets worked around by
    # turning the lock off.

    LOCK_PATCH = frozenset({
        "add_heads", "remove_heads", "patch_clear", "auto_patch", "change_type",
        "set_address", "patch_from_csv", "import_scan",
        "remap_heads", "patch_list", "rename_head",
        "set_limits", "clear_limits", "set_orient",
        "set_place", "place_many", "attach_heads", "set_venue", "venue_template",
        "venue_shape", "venue_build", "venue_describe", "venue_array",
        "venue_room", "venue_stage", "venue_add", "venue_update", "venue_rig",
        "venue_remove", "venue_underlay",
    })

    LOCK_LIBRARY = frozenset({
        "group_create", "group_delete", "record_cue", "insert_cue",
        "delete_cue", "move_cue", "rename_cue", "edit_cue", "record_palette",
        "include_palette", "record_preset", "include_preset", "delete_preset", "rename_preset",
        "set_output", "set_dmx_target", "save_show", "load_show", "import_show", "restore_version",
        "show_rename", "show_copy", "show_delete",
        "venue_save", "venue_open", "venue_delete",
        "quick_set", "quick_defaults", "quick_fx_defaults", "quick_from_laser", "timeline_set", "timeline_track",
        "motion_set", "remember_open",
        "timeline_clip", "timeline_from_playback",
    })

    LOCK_STATES = ("design", "operate", "locked")

    def _lock_check(self, name: str) -> None:
        """Refuse an action the current lock state forbids, saying why."""
        state = getattr(self, "lock_state", "design")
        if state == "design" or name in self.LOCK_ACTIONS:
            return
        if name in self.LOCK_PATCH and state == "locked":
            raise ValueError(
                f"LOCKED: {name} changes the patch, which is refused while "
                f"the rig is up. Set DESIGN to change the patch.")
        if name in self.LOCK_PATCH or name in self.LOCK_LIBRARY:
            raise ValueError(
                f"OPERATE: {name} edits the show, not the performance, and "
                f"is refused so a stray click cannot change it. Set DESIGN "
                f"to edit.")

    LOCK_ACTIONS = frozenset({"set_lock", "unlock", "status"})

    def _a_set_lock(self, state=None, password=None, **_):
        """`design` (edit anything), `operate` (run the show), or `locked`.

        The password is stored only as a SHA-256 hash, never in the clear
        and never in the show file - so the file that gets emailed to a
        colleague does not carry the thing that stops them editing it.
        """
        want = str(state or "").strip().lower()
        if want in ("off", "none", "unlocked", "0", "false"):
            want = "design"
        if want not in self.LOCK_STATES:
            raise ValueError(
                f"lock state must be one of {', '.join(self.LOCK_STATES)}"
                f" - not {state!r}")
        if password is not None and str(password).strip():
            self._lock_hash = hashlib.sha256(
                str(password).encode("utf-8")).hexdigest()
        if not getattr(self, "lock_state", "design") == want:
            self._log("lock", True, None, f"lock -> {want}")
        self.lock_state = want
        self._autosave()
        said = {
            "design": "DESIGN — everything is editable",
            "operate": ("OPERATE — the show runs, the show cannot be "
                        "edited (faders, cues and the programmer still "
                        "work)"),
            "locked": ("LOCKED — the show runs and the patch is frozen; "
                       "only DESIGN changes either"),
        }[want]
        return {"state": want, "has_password": bool(self._lock_hash),
                "summary": said}

    def _a_unlock(self, password=None, **_):
        """Leave `locked`, with the password if one is set."""
        if getattr(self, "lock_state", "design") != "locked":
            return {"state": getattr(self, "lock_state", "design"),
                    "summary": "was not locked"}
        if getattr(self, "_lock_hash", ""):
            if password is None or not str(password):
                raise ValueError("this lock has a password")
            got = hashlib.sha256(
                str(password).encode("utf-8")).hexdigest()
            if not _secrets_equal(got, self._lock_hash):
                raise ValueError("wrong password")
        return self._a_set_lock(state="operate")

    # ------------------------------------------------------------------
    # the venue (app/venue.py): room, stage, zones, rigging, objects
    # ------------------------------------------------------------------
    def _set_venue_doc(self, v: dict) -> None:
        """Store a new venue and carry mounted heads with their rigs - their
        place, and which way they face: a truss turned 90 degrees turns its
        lights 90 degrees, as it does on the real rig."""
        for h in self.patch:                 # facing pinned to the rig as it WAS
            self._head_yaw(h)
        self.venue = v
        self._reflow_mounts()
        self.patch_rev += 1

    def _head_yaw(self, h: dict) -> float:
        """Which way this light's base faces, degrees round the vertical (0:
        as a light hung on a truss running left-right).  A light on a rig
        turns with it: the rig's angle less where it was when the light was
        hung (mount["yaw0"]); an older show's light, or one never turned,
        faces along its truss.  A light on no rig keeps its last facing.
        The aim solver and the 3D both use it, so the DMX points where the
        3D shows."""
        m = h.get("mount")
        if isinstance(m, dict):
            r = venue_mod.rig(self.venue, m.get("rig"))
            ang = venue_mod.rig_angle(r) if r else None
            if ang is not None:
                if m.get("yaw0") is None:
                    m["yaw0"] = round(ang - venue_mod.fold90(ang), 3)
                yaw = ((ang - float(m["yaw0"]) + 180.0) % 360.0) - 180.0
                h["yaw"] = round(yaw, 3)
                return h["yaw"]
        return float(h.get("yaw") or 0.0)

    def _reflow_mounts(self) -> list[int]:
        """Put every mounted head back on its rig (after a rig moved), and
        let go of mounts whose rig was removed."""
        moved = []
        for h in self.patch:
            m = h.get("mount")
            if not isinstance(m, dict):
                continue
            r = venue_mod.rig(self.venue, m.get("rig"))
            if not r:
                h.pop("mount", None)
                continue
            pos = venue_mod.mount_position(r, m.get("t", 0.5), h.get("stance"))
            if (h["x"], h["y"], h["z"]) != (pos["x"], pos["y"], pos["z"]):
                moved.append(h["head_no"])
            h["x"], h["y"], h["z"] = pos["x"], pos["y"], pos["z"]
            h["stance"] = pos["orient"]
            h["kind"] = "truss" if pos["orient"] == "hang" else "floor"
            self._head_yaw(h)
        return moved

    def ensure_venue(self, default: str = "club") -> bool:
        """Give an empty desk a room to look at.  Not an undo step: it is
        the starting point, not an edit."""
        with self.lock:
            if not self.venue.get("auto") or self.patch:
                return False
            self.venue = venue_mod.template(default)
            self.patch_rev += 1
            return True

    def _venue_result(self, summary: str, **extra) -> dict:
        return {"venue": self.venue, "summary": summary, **extra}

    def _a_set_venue(self, venue=None, width_m=None, depth_m=None,
                     height_m=None, name=None, surfaces=None, **_):
        """Replace the whole venue: a v2 document, or the old
        {width_m, depth_m, height_m, surfaces} room."""
        if isinstance(venue, dict) and int(venue.get("version") or 1) >= 2:
            v = venue_mod.normalise(venue)
        else:
            raw = dict(venue) if isinstance(venue, dict) else {}
            for key, val in (("width_m", width_m), ("depth_m", depth_m),
                             ("height_m", height_m), ("name", name),
                             ("surfaces", surfaces)):
                if val is not None:
                    raw[key] = val
            v = venue_mod.normalise(raw)
        self._set_venue_doc(v)
        w, d, h = venue_mod.dims(v)
        return self._venue_result(
            f"venue {v.get('name') or 'room'} {w:g} x {d:g} m"
            + (f" x {h:g} m high" if h else ""))

    def _a_venue_template(self, name="club", width=None, depth=None,
                          height=None, keep_mounts=False, **_):
        """Start from a ready-made room: club, small_club, warehouse,
        concert, theatre, ballroom or outdoor."""
        v = venue_mod.template(name, width, depth, height)
        if not _truthy(keep_mounts):
            for h in self.patch:
                h.pop("mount", None)
        self._set_venue_doc(v)
        w, d, hh = venue_mod.dims(v)
        return self._venue_result(f"{v['name']}: {w:g} x {d:g} x {hh:g} m",
                                  templates=venue_mod.template_list())

    # -- other ways to make a room than drawing it ------------------------
    def _a_venue_shape(self, shape="rectangle", width=None, depth=None, height=None,
                       cut_w=None, cut_d=None, corner=None, layout=False,
                       kind=None, keep_mounts=False, **_):
        """A room from a shape and its sizes: rectangle, l, t, u, octagon,
        round or wedge.  layout: also a starter layout that fits the shape
        (DJ / stage, dance floor, bar, trusses wall to wall); without it
        only the walls change and the rigging you have moves inside."""
        if _truthy(layout):
            spec = {"shape": shape, "width": width, "depth": depth, "height": height,
                    "cut_w": cut_w, "cut_d": cut_d, "corner": corner or "", "kind": kind or "club",
                    "bar": {"side": "front"}}
            return self._a_venue_build(spec=spec, keep_mounts=keep_mounts)
        cur = venue_mod.normalise(self.venue)
        w0, d0, h0 = venue_mod.dims(cur)
        W = float(width or w0 or 16)
        D = float(depth or d0 or 20)
        pts = roomshape.outline(shape, W, D, float(cur["room"].get("back") or roomshape.BACK)
                                if w0 else roomshape.BACK, cut_w, cut_d, corner or "front-right")
        res = self._a_venue_room(outline=pts, height=height or h0 or 5.0)
        res["summary"] = f"{roomshape.SHAPES[roomshape.normalise_shape(shape)].split(' (')[0]} room: " + res["summary"]
        return res

    def _a_venue_build(self, spec=None, keep_mounts=False, **_):
        """A whole venue from a spec (see app/roomshape.py): what the room
        is, its size and shape, and what is in it."""
        if not isinstance(spec, dict):
            raise ValueError("spec is a dict: shape, width, depth, height, dj, bar, stage, ...")
        v = roomshape.build(spec)
        old = venue_mod.normalise(self.venue)
        moved, left = self._remount_onto(old, v)
        self._set_venue_doc(v)
        w, d, hh = venue_mod.dims(v)
        said = (f"; {moved} light(s) moved onto the new rigging" if moved else "") \
            + (f"; {left} light(s) had no rigging to go to and stay where they were" if left else "")
        return self._venue_result(f"{v['name']}: {w:g} x {d:g} x {hh:g} m, {len(v['rigging'])} rigging, "
                                  f"{len(v['objects'])} objects, {len(v['zones'])} zones" + said,
                                  spec=roomshape.clean_spec(spec))

    def _remount_onto(self, old: dict, new: dict) -> tuple[int, int]:
        """A rebuilt room replaces its rigging: each light on an old truss
        goes on the matching new one (old and new hung rigs paired front to
        back, towers by side), at the same place along it.  (moved, left)."""
        def hung(v):
            return sorted((r for r in v.get("rigging") or [] if not venue_mod.is_vertical(r)),
                          key=lambda r: -(r["a"][2] + r["b"][2]) / 2)
        def towers(v):
            return sorted((r for r in v.get("rigging") or [] if venue_mod.is_vertical(r)),
                          key=lambda r: (r["a"][0], r["a"][2]))
        pairs = {}
        for olds, news in ((hung(old), hung(new)), (towers(old), towers(new))):
            if not news:
                continue
            for i, r in enumerate(olds):
                # the same rank front to back; extra old rigs share the last new one
                j = min(len(news) - 1, round(i * (len(news) - 1) / max(1, len(olds) - 1))) if len(olds) > 1 else 0
                pairs[r["id"]] = news[j]["id"]
        new_ids = {r["id"] for r in new.get("rigging") or []}
        moved = left = 0
        for h in self.patch:
            m = h.get("mount")
            if not isinstance(m, dict) or m.get("rig") in new_ids:
                continue
            target = pairs.get(m.get("rig"))
            if target:
                h["mount"] = {"rig": target, "t": m.get("t", 0.5)}     # a new rig: its own turn
                moved += 1
            else:
                h.pop("mount", None)
                left += 1
        return moved, left

    def _a_venue_preview(self, spec=None, text=None, **_):
        """What venue_build / venue_describe would make, without making it
        (the room dialog draws it as you type)."""
        info = {}
        if text:
            got = roomshape.parse(str(text))
            spec = got["spec"]
            info = {"understood": got["understood"], "unsure": got["unsure"]}
        if not isinstance(spec, dict):
            raise ValueError("give a spec or a description")
        v = roomshape.build(spec)
        return {"preview": {"room": v["room"], "stage": v["stage"], "rigging": v["rigging"],
                            "objects": v["objects"], "zones": v["zones"], "name": v["name"]},
                "spec": roomshape.clean_spec(spec), **info}

    def _a_venue_describe(self, text="", apply=True, keep_mounts=False, **_):
        """A room from words: "a 12 x 8 m club, bar on the left, DJ booth on
        a 40 cm riser".  Says what it understood and what it guessed;
        apply=false only answers."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("describe the room: its size, shape, and what's in it")
        got = roomshape.parse(text)
        out = {"spec": got["spec"], "understood": got["understood"], "unsure": got["unsure"]}
        if not _truthy(apply):
            out["summary"] = "understood: " + (", ".join(got["understood"]) or "nothing yet")
            return out
        res = self._a_venue_build(spec=got["spec"], keep_mounts=keep_mounts)
        res.update(out)
        return res

    def _a_venue_array(self, id=None, count=2, step=2.0, axis="z", **_):
        """Copies of one truss, object or piece of rigging, `count` in all,
        `step` m apart along x or z (e.g. 4 trusses 2 m apart)."""
        v = venue_mod.normalise(self.venue)
        where = venue_mod.find(v, str(id or ""))
        if not where or where[0] == "zones":
            raise ValueError("pick a truss, pipe or object to copy")
        key, i = where
        n = int(_clamp(count, 2, 24))
        step = float(step)
        axis = "x" if str(axis).lower() == "x" else "z"
        src = v[key][i]
        made = []
        # copies carry on the numbering: Truss 3 -> Truss 4, 5, ... after the
        # highest one already there, never a second "Truss 2"
        base = re.sub(r"\s*\d+$", "", src.get("name") or src["kind"].replace("_", " ").title())
        nums = [int(m.group(1)) for item in v[key]
                for m in [re.match(re.escape(base) + r"\s*(\d+)$", item.get("name") or "")] if m]
        top = max(nums or [1])
        for k in range(1, n):
            raw = {kk: (list(vv) if isinstance(vv, list) else vv) for kk, vv in src.items() if kk != "id"}
            d = step * k
            if key == "rigging":
                for end in ("a", "b"):
                    raw[end] = [raw[end][0] + (d if axis == "x" else 0), raw[end][1], raw[end][2] + (d if axis == "z" else 0)]
            else:
                raw[axis] = raw[axis] + d
            raw["name"] = f"{base} {top + k}"
            v, item = venue_mod.add_item(v, raw)
            made.append(item["id"])
        if not re.search(r"\d+$", src.get("name") or ""):
            v, _ = venue_mod.update_item(v, src["id"], {"name": f"{base} 1"})
        self._set_venue_doc(v)
        return self._venue_result(f"{n - 1} copies, {abs(step):g} m apart along {axis}", ids=made)

    ALIGN_HOW = ("left", "right", "centre-x", "back", "front", "centre-z", "height",
                 "spread-x", "spread-z", "spread-height")

    def _a_venue_align(self, ids=None, how="centre-x", **_):
        """Line up rigging and objects: their middles to the leftmost /
        rightmost / middle one across (x), the back / front / middle one in
        depth (z), or the same height; or spread them evenly between the
        two outermost ones.  A piece of a shape (frame, circle...) moves
        the whole shape; lights on a rig go with it."""
        how = str(how or "")
        if how not in self.ALIGN_HOW:
            raise ValueError(f"how is one of {', '.join(self.ALIGN_HOW)}")
        v = venue_mod.normalise(self.venue)
        rigs = {r["id"]: r for r in v.get("rigging") or []}
        objs = {o["id"]: o for o in v.get("objects") or []}
        # the units that move: a shape counts once, with all its pieces
        units, seen = [], set()
        for ident in [str(i) for i in (ids or [])]:
            if ident in rigs:
                r = rigs[ident]
                g = r.get("group")
                key = ("g", g) if g else ("r", ident)
                if key in seen:
                    continue
                seen.add(key)
                members = [x for x in rigs.values() if x.get("group") == g] if g else [r]
                pts = [p for m in members for p in (m["a"], m["b"])]
                units.append({"rigs": members, "c": [sum(p[k] for p in pts) / len(pts) for k in range(3)]})
            elif ident in objs:
                if ("o", ident) in seen:
                    continue
                seen.add(("o", ident))
                o = objs[ident]
                units.append({"obj": o, "c": [float(o["x"]), float(o.get("y") or 0), float(o["z"])]})
            else:
                raise ValueError(f"no rigging or object {ident!r} (zones can't be lined up)")
        if len(units) < (3 if how.startswith("spread") else 2):
            raise ValueError("pick at least " + ("three things to spread" if how.startswith("spread") else "two things to line up"))
        axis = 1 if how.endswith("height") else 0 if how.endswith("-x") or how in ("left", "right") else 2
        vals = [u["c"][axis] for u in units]
        if how.startswith("spread"):
            order = sorted(range(len(units)), key=lambda i: vals[i])
            lo, hi = vals[order[0]], vals[order[-1]]
            targets = {i: lo + (hi - lo) * k / (len(units) - 1) for k, i in enumerate(order)}
        else:
            to = {"left": min, "back": min, "right": max, "front": max}.get(how)
            t = to(vals) if to else sum(vals) / len(vals)
            targets = {i: t for i in range(len(units))}
        moved = 0
        for i, u in enumerate(units):
            d = round(targets[i] - u["c"][axis], 3)
            if abs(d) < 1e-4:
                continue
            moved += 1
            if "obj" in u:
                key = ("x", "y", "z")[axis]
                v, _o = venue_mod.update_item(v, u["obj"]["id"], {key: round(float(u["obj"].get(key) or 0) + d, 3)})
                continue
            for m in u["rigs"]:
                ch = {}
                for end in ("a", "b"):
                    p = list(m[end])
                    p[axis] = round(p[axis] + d, 3)
                    ch[end] = p
                v, _r = venue_mod.update_item(v, m["id"], ch)
        if how.endswith("height"):
            top = float((v.get("room") or {}).get("height") or 60)
            if any(c > top for c in targets.values()):
                raise ValueError("that would put something above the ceiling")
        self._set_venue_doc(v)
        words = {"left": "lined up on the left", "right": "lined up on the right", "centre-x": "centred across",
                 "back": "lined up at the back", "front": "lined up at the front", "centre-z": "centred in depth",
                 "height": "at the same height", "spread-x": "spread evenly across",
                 "spread-z": "spread evenly in depth", "spread-height": "spread evenly in height"}[how]
        return self._venue_result(f"{len(units)} {words}" + ("" if moved else " (already were)"), ids=[str(i) for i in ids])

    def _a_venue_ceiling(self, id=None, points=None, height=None, name=None, remove=False, **_):
        """A part of the room with a ceiling of its own: lower under a
        mezzanine or a bulkhead, higher over the dance floor.  Draw it
        (points [[x, z], ...]) with its height, change its height or name
        (id), or take it away (remove).  Rigging under a lowered ceiling
        comes down below it, its lights with it."""
        v = venue_mod.normalise(self.venue)
        room = dict(v["room"])
        areas = [dict(a) for a in room.get("areas") or []]
        if id:
            cur = next((a for a in areas if a["id"] == str(id)), None)
            if cur is None:
                raise ValueError(f"no ceiling area {id!r}")
            if _truthy(remove):
                areas.remove(cur)
                room["areas"] = areas
                v["room"] = room
                v = venue_mod.normalise(v)
                self._set_venue_doc(v)
                return self._venue_result(f"{cur['name']} taken away: the room's ceiling there again")
            if points is not None:
                cur["points"] = points
            if height is not None:
                cur["height"] = height
            if name is not None:
                cur["name"] = name
        else:
            if not points or height is None:
                raise ValueError("draw the area (points) and give its ceiling height")
            if len(areas) >= venue_mod.MAX_AREAS:
                raise ValueError(f"at most {venue_mod.MAX_AREAS} ceiling areas")
            n = 1 + max([int(a["id"][1:]) for a in areas if a["id"][1:].isdigit()] or [0])
            cur = {"id": f"c{n}", "name": name or f"Ceiling {n}", "points": points, "height": height}
            areas.append(cur)
        try:
            h = float(cur["height"])
        except (TypeError, ValueError):
            raise ValueError("the height is in metres, e.g. 3.2") from None
        if not 1.8 <= h <= 60:
            raise ValueError("a ceiling is 1.8 to 60 m high")
        room["areas"] = areas
        v["room"] = room
        v = venue_mod.normalise(v)
        got = next((a for a in v["room"]["areas"] if a["id"] == cur["id"]), None)
        if got is None:
            raise ValueError("an area needs at least 3 corners")
        pulled = venue_mod.fit_inside(v)
        self._set_venue_doc(v)
        return self._venue_result(f"{got['name']}: ceiling {got['height']:g} m"
                                  + (f"; {pulled} piece(s) of rigging brought down under it" if pulled else ""),
                                  id=got["id"])

    def _a_venue_room(self, width=None, depth=None, height=None, back=None,
                      ceiling=None, floor=None, wall_colour=None,
                      outline=None, name=None, **_):
        v = venue_mod.normalise(self.venue)
        room = dict(v["room"])
        for key, val in (("width", width), ("depth", depth), ("height", height),
                         ("back", back), ("ceiling", ceiling), ("floor", floor),
                         ("wall_colour", wall_colour), ("outline", outline)):
            if val is not None:
                room[key] = val
        v["room"] = room
        if name is not None:
            v["name"] = str(name)
        v["auto"] = False
        v = venue_mod.normalise(v)
        if not (v["room"]["width"] and v["room"]["depth"]):
            raise ValueError("the room needs a width and a depth")
        # zones scale with the room; rigging and objects left outside the
        # new walls come back in (the lights on a rig with it)
        venue_mod.scale_zones(venue_mod.normalise(self.venue), v)
        pulled = venue_mod.fit_inside(v)
        self._set_venue_doc(v)
        w, d, h = venue_mod.dims(v)
        return self._venue_result(f"room {w:g} x {d:g} x {h:g} m"
                                  + (f"; moved {pulled} piece(s) of rigging back inside" if pulled else ""))

    def _a_venue_stage(self, x=None, z=None, width=None, depth=None,
                       height=None, remove=False, **_):
        v = venue_mod.normalise(self.venue)
        if _truthy(remove):
            v["stage"] = None
            self._set_venue_doc(v)
            return self._venue_result("stage removed")
        cur = dict(v.get("stage") or {"x": 0, "z": 0, "width": 8, "depth": 4,
                                       "height": 0.6})
        for key, val in (("x", x), ("z", z), ("width", width),
                         ("depth", depth), ("height", height)):
            if val is not None:
                cur[key] = val
        v["stage"] = cur
        v = venue_mod.normalise(v)
        if not v["stage"]:
            raise ValueError("a stage needs a width and a depth")
        self._set_venue_doc(v)
        s = v["stage"]
        return self._venue_result(
            f"stage {s['width']:g} x {s['depth']:g} m, {s['height']:g} m high")

    def _a_venue_add(self, item=None, **params):
        """Add rigging, an object or a zone: {kind, ...}.  Kinds are in
        app/venue.py (truss, pipe, tower, stand, base; dj_booth, bar,
        speaker, pillar, riser, mark...; dancefloor, standing, bar...)."""
        raw = dict(item) if isinstance(item, dict) else {
            k: v for k, v in params.items() if not k.startswith("_")}
        v, made = venue_mod.add_item(self.venue, raw)
        self._set_venue_doc(v)
        return self._venue_result(f"added {made['kind']} {made['id']}",
                                  item=made, id=made["id"])

    def _a_venue_update(self, id=None, changes=None, **params):
        """Change one venue item; a moved rig carries its lights."""
        if not id:
            raise ValueError("id is required")
        raw = dict(changes) if isinstance(changes, dict) else {
            k: v for k, v in params.items() if not k.startswith("_")}
        # the other pieces of the same shape, turned with it (Arrange turns
        # a circle / frame as a whole): [{id, a, b}], one undo step
        pieces = raw.pop("pieces", None)
        cur = venue_mod.rig(self.venue, str(id))
        v, item = venue_mod.update_item(self.venue, str(id), raw)
        if pieces and cur and cur.get("group"):
            if not isinstance(pieces, list) or len(pieces) > 200:
                raise ValueError("pieces is a list of {id, a, b}")
            for pc in pieces:
                other = venue_mod.rig(v, str((pc or {}).get("id") or ""))
                if not other or other.get("group") != cur["group"] or other["id"] == item["id"]:
                    raise ValueError("pieces must be the other pieces of the same shape")
                v, _o = venue_mod.update_item(v, other["id"], {"a": pc.get("a"), "b": pc.get("b")})
            self._set_venue_doc(v)
            return self._venue_result(f"turned the shape {cur['group']} ({len(pieces) + 1} pieces)", item=item)
        # a piece of a shape (circle, frame...) moved as a whole: the rest of
        # the shape comes with it
        if cur and cur.get("group") and "a" in raw and "b" in raw:
            da = [item["a"][k] - cur["a"][k] for k in range(3)]
            db = [item["b"][k] - cur["b"][k] for k in range(3)]
            # (a screen rounds the ends to the cm: a few mm apart is still
            # the same move - at 1 mm a circle's piece moved alone)
            if all(abs(da[k] - db[k]) < 0.015 for k in range(3)) and any(abs(x) > 1e-4 for x in da):
                step = [round((da[k] + db[k]) / 2, 3) for k in range(3)]
                for other in list(v["rigging"]):
                    if other.get("group") == cur["group"] and other["id"] != item["id"]:
                        v, _o = venue_mod.update_item(v, other["id"], {
                            "a": [round(other["a"][k] + step[k], 3) for k in range(3)],
                            "b": [round(other["b"][k] + step[k], 3) for k in range(3)]})
        self._set_venue_doc(v)
        return self._venue_result(f"updated {item['kind']} {item['id']}",
                                  item=item)

    # -- the rigging library -----------------------------------------------
    def _a_rig_pieces(self, **_):
        """The rigging library: pieces and the shapes they make."""
        return {"pieces": riglib.pieces_public(), "presets": list(riglib.PRESETS),
                "lengths": list(riglib.LENGTHS)}

    def _a_rig_add(self, preset="straight", piece="box30", x=0.0, y=None, z=None, length=4.0,
                   width=4.0, depth=3.0, diameter=4.0, segments=None, height=3.0, rot=0.0,
                   name="", trim=None, **_):
        """Add a rigging shape from the library: a straight run, a corner, a
        frame, a circle, a goal post, a pole or a stand, made of that piece.
        `trim`: the height it hangs at (default: under the ceiling)."""
        nv = venue_mod.normalise(self.venue)
        room = (nv.get("room") or {})
        top = float(room.get("height") or 6.0)
        if room.get("height") and z is not None:
            top = venue_mod.ceiling_at(nv, float(x or 0), float(z))      # a ceiling area of its own
        # trim = the height of its UNDERSIDE (as riggers give it, and as Trim…
        # and the report use it); the piece's line runs through its middle
        half = float((riglib.PIECES.get(str(piece)) or {}).get("size") or 0.3) / 2
        under = float(trim) if trim is not None else (float(y) - half if y is not None else top - 0.6 - half)
        under = max(0.3, min(top - 0.2 - 2 * half, under))
        hang = under + half
        zz = float(z) if z is not None else float(room.get("back", -1) or -1) + float(room.get("depth") or 10) * 0.35
        items = riglib.build(str(preset), str(piece), float(x or 0), hang, zz, float(length), float(width),
                             float(depth), float(diameter), int(segments) if segments else None,
                             min(float(height), top), float(rot or 0), str(name or ""))
        v = self.venue
        group = None
        if len(items) > 1:
            n = 1 + sum(1 for r in (venue_mod.normalise(v).get("rigging") or []) if r.get("group"))
            group = f"g{n}"
            while any(r.get("group") == group for r in venue_mod.normalise(v).get("rigging") or []):
                n += 1
                group = f"g{n}"
        made = []
        for it in items:
            if group:
                it["group"] = group
            v, m = venue_mod.add_item(v, it)
            made.append(m["id"])
        self._set_venue_doc(v)
        spec = riglib.PIECES[str(piece)]
        return self._venue_result(f"added {preset} ({spec['name']}, {len(made)} piece(s))",
                                  ids=made, group=group, id=made[0])

    def _a_rig_trim(self, id=None, trim=None, **_):
        """Hang a piece (or its whole shape) at `trim` metres (the height of
        its underside, as riggers give it); its lights come with it."""
        r = venue_mod.rig(self.venue, str(id or ""))
        if not r:
            raise ValueError(f"no rig {id!r}")
        nv = venue_mod.normalise(self.venue)
        room = nv.get("room") or {}
        top = float(room.get("height") or 60)
        if room.get("height"):
            top = min(venue_mod.ceiling_over(nv, [m["a"], m["b"]]) for m in nv["rigging"]
                      if (r.get("group") and m.get("group") == r["group"]) or m["id"] == r["id"])
        t = float(trim)
        if not 0.3 <= t <= top - 0.1:
            raise ValueError(f"a trim is 0.3 to {top - 0.1:g} m in this room")
        members = [x for x in venue_mod.normalise(self.venue)["rigging"]
                   if (r.get("group") and x.get("group") == r["group"]) or x["id"] == r["id"]]
        v = self.venue
        n = 0
        for m in members:
            if abs(m["a"][1] - m["b"][1]) > 0.5 and math.hypot(m["a"][0] - m["b"][0], m["a"][2] - m["b"][2]) < 0.3:
                continue                                   # a pole / stand stays on the floor
            y = round(t + float(m.get("size") or 0.3) / 2, 3)
            v, _i = venue_mod.update_item(v, m["id"], {"a": [m["a"][0], y, m["a"][2]], "b": [m["b"][0], y, m["b"][2]]})
            n += 1
        self._set_venue_doc(v)
        return self._venue_result(f"{r.get('name') or r['kind']} trimmed to {t:g} m ({n} piece(s))", id=r["id"])

    def _head_physical(self, h: dict) -> dict:
        fx = self._fixture_db(h.get("manufacturer"), h.get("model")) or {}
        return fixlib.physical(fx.get("source") or "")

    def _a_paperwork(self, **_):
        """Everything the light plot and the patch sheet print: each light
        (number, name, type, maker / model / mode, universe.address, how many
        channels, where it is and what it hangs on, weight, power), the
        venue and the rigging report."""
        from app import fixture_kind
        v = venue_mod.normalise(self.venue)
        rigs = {r["id"]: r for r in v.get("rigging") or []}
        rows = []
        for h in sorted(self.patch, key=lambda x: x["head_no"]):
            ph = self._head_physical(h)
            d = fixture_kind.describe(h)
            rid = (h.get("mount") or {}).get("rig")
            rows.append({
                "n": h["head_no"], "name": h.get("name", ""), "type": d.get("type") or "generic",
                "label": d.get("label") or "", "manufacturer": h.get("manufacturer", ""),
                "model": h.get("model", ""), "mode": h.get("mode", ""),
                "universe": h.get("universe", 1), "address": h.get("address", 1),
                "channels": len(h.get("map") or []),
                "x": round(float(h.get("x") or 0), 2), "y": round(float(h.get("y") or 0), 2),
                "z": round(float(h.get("z") or 0), 2), "stance": h.get("stance") or "",
                "rig": (rigs.get(rid) or {}).get("name") or ("floor" if float(h.get("y") or 0) < 1.5 else ""),
                "kg": ph.get("kg") if ph.get("kg") is not None else riglib.KIND_KG[riglib.light_kind(h)],
                "kg_known": ph.get("kg") is not None, "watts": ph.get("watts")})
        rep = riglib.report(v, self.patch, self._head_physical)
        name = self.show_file if getattr(self, "show_file", None) else ""
        return {"lights": rows, "venue": v, "rigging": rep, "show": name or "",
                "universes": sorted({r["universe"] for r in rows}),
                "summary": f"{len(rows)} light(s) on {len({r['universe'] for r in rows})} universe(s)"}

    def _a_rig_report(self, csv=False, **_):
        """The rigging report: each piece or shape, the lights on it, its
        own weight, pick-up points and the load on each (and a parts list)."""
        rep = riglib.report(venue_mod.normalise(self.venue), self.patch, self._head_physical)
        out = {"report": rep,
               "summary": f"{len(rep['rigs'])} rig(s), {rep['total_kg']:g} kg in all, {rep['points']} pick-up point(s)"}
        if _truthy(csv):
            out["csv"] = riglib.report_csv(rep)
        return out

    def _a_venue_rig(self, id=None, turn=None, length=None, orient=None, ceiling=False, **_):
        """Reshape one rig: turn it about its middle (degrees), set its
        length, stand it up (orient="vertical") or lay it flat, or hang it
        just under the ceiling.  It stays inside the room; lights on it go
        with it."""
        if not id:
            raise ValueError("id is required")
        r = venue_mod.rig(self.venue, str(id))
        if not r:
            raise ValueError(f"no rig {id!r}")
        if orient not in (None, "", "vertical", "horizontal"):
            raise ValueError("orient is vertical or horizontal")
        if length is not None and not 0.2 <= float(length) <= 60:
            raise ValueError("a rig is 0.2 to 60 m long")
        changes = venue_mod.rig_transform(self.venue, r, turn=float(turn) if turn else None,
                                          length=float(length) if length else None,
                                          orient=orient or None, ceiling=_truthy(ceiling))
        v, item = venue_mod.update_item(self.venue, str(id), changes)
        self._set_venue_doc(v)
        what = ", ".join(x for x in (f"turned {float(turn):g}°" if turn else "", f"{float(length):g} m" if length else "",
                                     {"vertical": "stood up", "horizontal": "laid flat"}.get(orient or "", ""),
                                     "hung from the ceiling" if _truthy(ceiling) else "") if x)
        return self._venue_result(f"{item['kind']} {item['id']}: {what or 'unchanged'}", item=item)

    def _a_venue_remove(self, id=None, **_):
        if not id:
            raise ValueError("id is required")
        v = venue_mod.remove_item(self.venue, str(id))
        freed = [h["head_no"] for h in self.patch
                 if (h.get("mount") or {}).get("rig") == str(id)]
        self._set_venue_doc(v)
        return self._venue_result(
            f"removed {id}" + (f"; {len(freed)} light(s) now free-standing"
                               if freed else ""), freed=freed)

    def _a_venue_underlay(self, id=None, x=None, z=None, width=None,
                          aspect=None, rot=None, opacity=None, show=None,
                          remove=False, **_):
        """A floor plan (image or PDF page) laid on the floor to trace."""
        v = venue_mod.normalise(self.venue)
        if _truthy(remove):
            v["underlay"] = None
        else:
            cur = dict(v.get("underlay") or {})
            for key, val in (("id", id), ("x", x), ("z", z), ("width", width),
                             ("aspect", aspect), ("rot", rot),
                             ("opacity", opacity), ("show", show)):
                if val is not None:
                    cur[key] = val
            v["underlay"] = venue_mod.clean_underlay(cur)
            if not v["underlay"]:
                raise ValueError("upload a floor plan first")
        self.venue = v
        self.patch_rev += 1
        return self._venue_result("floor plan " + ("removed" if remove else "placed"))

    def _a_venue_crowd(self, style=None, density=None, show=None, **_):
        v = venue_mod.normalise(self.venue)
        crowd = dict(v["crowd"])
        for key, val in (("style", style), ("density", density), ("show", show)):
            if val is not None:
                crowd[key] = _truthy(val) if key == "show" else val
        v["crowd"] = crowd
        self.venue = venue_mod.normalise(v)
        self.patch_rev += 1
        c = self.venue["crowd"]
        return self._venue_result(
            f"crowd {c['style']} at {round(c['density'] * 100)}%"
            + ("" if c["show"] else " (hidden)"))

    def _a_venue_camera(self, name=None, pos=None, target=None, remove=False,
                        **_):
        if not name:
            raise ValueError("name the view")
        v = venue_mod.normalise(self.venue)
        cams = [c for c in v["cameras"] if c["name"] != str(name)]
        if not _truthy(remove):
            if pos is None or target is None:
                raise ValueError("pos and target are required")
            cams.append({"name": str(name), "pos": pos, "target": target})
        v["cameras"] = cams
        self.venue = venue_mod.normalise(v)
        self.patch_rev += 1
        return self._venue_result(("removed" if remove else "saved")
                                  + f" view {name}")

    def _a_venue_info(self, **_):
        return {"venue": venue_mod.describe(self.venue),
                "templates": venue_mod.template_list(),
                "summary": "venue " + (self.venue.get("name") or "(auto)")}

    # ------------------------------------------------------------------
    # My venues: a venue saved on its own (room, rigging, zones, objects -
    # and, if wanted, the lights hung in it), to open for the next gig there
    # ------------------------------------------------------------------
    def _venue_dir(self) -> Path:
        return self.show_dir / "venues"

    @staticmethod
    def _venue_key(name) -> str:
        """A file name for a venue name ("Tom's Bar" -> "Toms Bar")."""
        key = re.sub(r"[^A-Za-z0-9 _-]+", "", str(name or "")).strip()[:40]
        if not key:
            raise ValueError("a venue name needs some letters or digits")
        return key

    def _venue_list(self) -> list[dict]:
        d = self._venue_dir()
        try:
            stamp = d.stat().st_mtime_ns if d.exists() else 0
        except OSError:
            stamp = 0
        cache = getattr(self, "_venue_list_cache", None)
        if cache and cache[0] == stamp:
            return cache[1]
        out = []
        for path in sorted(d.glob("*.json")) if d.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            v = data.get("venue") or {}
            room = v.get("room") or {}
            out.append({"key": path.stem, "name": data.get("name") or path.stem,
                        "lights": len(data.get("patch") or []), "saved": data.get("saved"),
                        "shape": "custom" if room.get("outline") else "rectangle",
                        "size": [room.get("width"), room.get("depth")],
                        "rigging": len(v.get("rigging") or [])})
        self._venue_list_cache = (stamp, out)
        return out

    def _a_venue_save(self, name="", lights=True, **_):
        """Save this venue under a name - its room, rigging, zones and
        objects, and (lights=True) the lights hung in it with their
        addresses and positions - to open again at the next gig there."""
        label = str(name or self.venue.get("name") or "").strip()[:40]
        if not label:
            raise ValueError("a venue needs a name")
        key = self._venue_key(label)
        with self.lock:
            payload = {"version": 1, "name": label,
                       "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "venue": json.loads(json.dumps(dict(self.venue, name=label), default=str))}
            if _truthy(lights):
                payload["patch"] = json.loads(json.dumps(self.patch, default=str))
                payload["groups"] = json.loads(json.dumps(self.groups, default=str))
            self.venue["name"] = label
        d = self._venue_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{key}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._venue_list_cache = None
        return {"key": key, "summary": f"venue {label!r} saved"
                + (f" with {len(payload.get('patch') or [])} light(s)" if _truthy(lights) else "")}

    def _a_venue_open(self, name="", lights=True, **_):
        """Open a saved venue: its room, rigging and zones replace this one;
        with lights=True (and lights saved in it) the patch too."""
        key = self._venue_key(name)
        path = self._venue_dir() / f"{key}.json"
        if not path.exists():
            hit = next((v for v in self._venue_list() if v["name"].lower() == str(name).strip().lower()), None)
            if hit is None:
                raise ValueError(f"no saved venue {name!r}")
            path = self._venue_dir() / f"{hit['key']}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        venue = venue_mod.normalise(data.get("venue") or {})
        heads = None
        if _truthy(lights) and data.get("patch"):
            heads = [self._head_from_layout(h) for h in data["patch"]]
        with self.lock:
            if heads is not None:
                self._replace_patch(heads)
                self.groups = [dict(g) for g in data.get("groups") or [] if isinstance(g, dict)]
                self.programmer = {}
                self.selected = []
                self.fx = []
            self.venue = venue
            self.patch_rev += 1
        return {"name": data.get("name"), "lights": len(heads) if heads is not None else None,
                "summary": f"opened venue {data.get('name')!r}"
                + (f" with {len(heads)} light(s)" if heads is not None else "")}

    def _a_venue_delete(self, name="", **_):
        key = self._venue_key(name)
        path = self._venue_dir() / f"{key}.json"
        if not path.exists():
            raise ValueError(f"no saved venue {name!r}")
        path.unlink()
        self._venue_list_cache = None
        return {"summary": f"deleted venue {name!r}"}
