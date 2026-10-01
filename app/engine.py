"""Jarvis engine - our own lighting console.

Patch management (add/remove/re-address heads, groups), programmer,
palettes, cue stacks, playbacks with real fades, frame building with
HTP/LTP merging, and Art-Net output through a single daemon thread.

Pure standard library, and the only control path: HTTP threads mutate the
state through Engine.act(), the output thread reads it 40 times a second.

Merge precedence, highest first:
    blackout > grand master > programmer (HTP intensity) >
    playback faders (HTP) > LTP priority (programmer, else latest playback)
"""
from __future__ import annotations

import collections
import copy
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app import config, fixture_kind, fixtures, merge
from app import fxlib as fxlib_mod
from app import timeline as tl_mod
from app import venue as venue_mod
from app.artnet import ArtNetSender

# everything module-level (constants, ACTIONS, helpers, the channel-role
# vocabulary) is re-exported: `from app.engine import X` keeps working
from app.engine_base import *  # noqa: E402,F401,F403
from app.engine_base import (
    _FIXTURE_CACHE,
    _READ_ONLY,
    ACTIONS,
    DMX_TARGET_DEFAULT,
    HISTORY_LIMIT,
    PLAYBACK_COUNT,
    READY_ERROR_WINDOW_S,
    SELF_LOCKED_ACTIONS,
    UNDO_COALESCE,
    UNDO_COALESCE_S,
    UNDO_EXCLUDED,
    UNDO_LIMIT,
    _clamp,
    _copy_playbacks,
    _deg,
    _logical_to_phys,
    _new_playback,
    attr_domain,
)
from app.engine_cmdline import CommandMixin
from app.engine_cues import CueMixin
from app.engine_fxlayer import FxLayerMixin
from app.engine_looks import LooksMixin
from app.engine_move import MoveMixin
from app.engine_output import OutputMixin
from app.engine_patch import PatchMixin
from app.engine_program import ProgrammerMixin
from app.engine_quick import QuickMixin
from app.engine_rig import RigMixin
from app.engine_shows import ShowMixin
from app.engine_support import HTP_ROLES
from app.engine_autopilot import AutopilotMixin
from app.engine_steps import StepsMixin
from app.engine_desk import DeskMixin
from app.engine_cuemodes import CueModesMixin
from app.engine_roam import RoamMixin
from app.engine_osc import OscMixin
from app.engine_sound import SoundMixin
from app.engine_tempo import TempoMixin
from app.engine_timeline import TimelineMixin


class Engine(PatchMixin, RigMixin, QuickMixin, FxLayerMixin, MoveMixin, TimelineMixin, TempoMixin, SoundMixin, AutopilotMixin, StepsMixin, DeskMixin, OscMixin, CueModesMixin, RoamMixin, ProgrammerMixin, CueMixin, OutputMixin, CommandMixin, LooksMixin, ShowMixin):
    """All console state + the DMX output thread. One RLock."""

    def __init__(self, db_path: Path | None = None, dry_run: bool = True,
                 sender: ArtNetSender | None = None,
                 show_dir: Path | None = None,
                 autosave_path: Path | None = None,
                 restore: bool = False):
        self.lock = threading.RLock()
        self.db_path = Path(db_path or config.DB_PATH)
        self.dry_run = bool(dry_run)
        # The engine is the console; there is no second control path.
        self.mode = "jarvis"
        self.show_dir = Path(show_dir or config.CONSOLE_SHOW_DIR)
        self._sender = sender
        # A sender handed in (tests, tools) is used as given, never rebuilt.
        self._sender_fixed = sender is not None
        # Where the DMX goes at THIS venue (see _a_set_dmx_target).  Saved
        # with the show, not in undo: undoing a venue edit must never
        # quietly send the rig's data somewhere else.
        self.dmx_target: dict = dict(DMX_TARGET_DEFAULT)
        # The adapters' auto broadcast, refreshed off the output thread.
        self._auto_host: str | None = None
        self._auto_host_at = -1e9
        self._auto_host_busy = False
        # The room the rig lives in (app/venue.py).  "auto" means nothing
        # was drawn and the visualiser sizes a room around the patch.
        self.venue: dict = venue_mod.empty()
        # Quick buttons (see _a_quick_set) and the ones held right now.
        self.quick: list[dict] = []
        self.quick_active: dict[str, dict] = {}
        self.quick_names: dict[str, str] = {}
        # "My moves": named movements (shape + knobs), played on any lights
        self.moves: list[dict] = []
        # Special effects (see the FX layer): armed until (monotonic), the
        # runs firing now, and how much each confetti tank has left (s).
        # Never saved: a desk always starts DISARMED with nothing firing.
        self.fx_armed_until = 0.0
        self.fx_runs: dict[str, dict] = {}
        self.fx_loads: dict[int, float] = {}
        # The show timeline (app/timeline.py) and its transport.
        self.timeline: dict = tl_mod.empty()
        self.tl = {"playing": False, "pos": 0.0, "t0": 0.0, "pos0": 0.0,
                   "last": 0.0, "spans": {}}
        self._tl_stop = threading.Event()
        self._tl_thread: threading.Thread | None = None

        self.patch: list[dict] = []
        self.patch_rev = 0
        self.groups: list[dict] = []
        self.palettes: dict[str, list[dict]] = {
            "position": [], "colour": [], "beam": []}
        self.playbacks: list[dict] = [_new_playback(i + 1)
                                      for i in range(PLAYBACK_COUNT)]
        self.programmer: dict[int, dict[str, int]] = {}
        # A timed programmer change (`set_intensity fade=`): the values the
        # heads start FROM, eased into self.programmer over `dur` seconds.
        self._prog_fade: dict | None = None
        self._batching = False
        self.selected: list[int] = []
        self.fx: list[dict] = []            # running effects (see run_fx)
        self._fx_seq = 0
        # the Speed master: every running effect's clock runs at this rate
        # (0.1 = a tenth of the speed, 2 = double) - movement, colour chases,
        # everything, smoothly and without a jump when it changes
        self.speed_master = 1.0
        self._tempo()                      # the beat clock (app/tempo.py)
        # The dance floor as a pan/tilt range per mover (see _floor_limits):
        # movement always fits inside it (floor_safe), and with floor_lock
        # EVERYTHING does - cues, aims, the programmer.
        self.floor_safe = True
        self.floor_lock = False
        self.autosave_path = Path(autosave_path) if autosave_path else None
        self._autosave_at = 0.0
        self._autosave_dirty = False
        # Autosave writer thread: serialisation under the lock, the disk
        # write off it (see _autosave).
        self._writer: threading.Thread | None = None
        self._writer_event = threading.Event()
        self._writer_stop = threading.Event()
        self._writer_lock = threading.Lock()
        self._writer_pending: str | None = None
        self._writer_path: Path | None = None
        self._start_writer()
        self.master = 100
        self.blackout = False
        self.live = False
        self.show_file: str | None = None
        self.history: list[dict] = []
        self.output = {"running": False, "frames_sent": 0,
                       "simulated_frames": 0, "last_tick_age_ms": None,
                       "drift_ms": 0, "errors": 0, "last_error": None,
                       "hz": float(config.DMX_HZ),
                       "host": f"{config.DMX_HOST}:{config.DMX_PORT}"}

        # the last 10 s of gaps between frames, for the status bar's
        # timing dot (a steady 25 ms at 40 Hz, or the lights stutter)
        self._gaps: collections.deque = collections.deque(maxlen=400)
        self._order = 0
        # Monotonic look-feed sequence (see look_feed); the visualiser
        # interpolates between ticks instead of stepping.
        self._look_seq = 0
        self._look_cache: tuple[float, str] | None = None
        self._look_cache_lock = threading.Lock()
        self._look_wanted = -1e9
        # Bumped by every successful edit, so a live client knows when the
        # structure (patch, cues, palettes, shows...) needs a full reload.
        self.act_rev = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Auto-follow: one small ticker thread while any cue stack has
        # follow armed (started lazily, joined on shutdown - no timers
        # are ever created per cue, so nothing can leak).  `self._clock`
        # is injectable so tests drive the deadlines deterministically.
        self._clock = time.monotonic
        # The undo history.  `_undo` holds the state as it was BEFORE each
        # edit, so popping the last entry is the undo; `_redo` mirrors it
        # with the state as it was AFTER, which is what makes redo the
        # exact inverse of undo rather than a second, slightly different
        # operation.  Labels are kept alongside so the UI can say what the
        # next Ctrl+Z will do instead of offering a button that might be a
        # no-op - the thing grandMA's Oops overlay exists for.
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._undo_label = ""
        self._redo_label = ""
        # Presets: complete, named looks.  Distinct from palettes, which
        # are one attribute family each.
        self.presets: list[dict] = []
        # Physical channel ranges, per (manufacturer, model, mode).  Only
        # `remap_heads` can make one stale, and that clears it.
        # design / operate / locked.  See LOCK_PATCH / LOCK_LIBRARY: a lock that
        # only says "no" is a lock the operator works around.
        self.lock_state = "design"
        self._lock_hash = ""
        self._range_cache: dict[tuple, dict] = {}
        # Where each (manufacturer, model) profile came from, for the patch
        # sheet.  Same deal as _range_cache: a database read, and the
        # sheet is the only caller.
        self._source_cache: dict[tuple, str] = {}
        self._follow_stop = threading.Event()
        self._follow_thread: threading.Thread | None = None
        self._handlers = {name: getattr(self, "_a_" + name)
                          for name in ACTIONS}
        # A fixture DB write (GDTF import, profile install) must drop our
        # cached definitions, or a re-patch would resolve modes that no
        # longer exist.
        fixtures.on_cache_clear(_FIXTURE_CACHE.clear)
        fixtures.on_cache_clear(self._drop_fixture_caches)
        if restore and self.autosave_path is not None:
            self._restore_autosave()
        self._sync_follow_thread()

    # ------------------------------------------------------------------
    # action dispatch
    # ------------------------------------------------------------------
    # -- undo ------------------------------------------------------------
    def _undo_state(self) -> dict:
        """The mutable state an undo has to be able to put back.

        Deliberately NOT the whole `__dict__`: the sender, the clock, the
        lock and the caches are not edits, and copying them would both
        waste time and risk restoring a dead thread.  This is exactly the
        set the console's own state consists of.
        """
        return {
            "patch": [dict(h) for h in self.patch],
            "programmer": {k: dict(v) for k, v in self.programmer.items()},
            "selected": list(self.selected),
            "groups": [dict(g) for g in self.groups],
            "palettes": {k: [dict(p) for p in v]
                         for k, v in self.palettes.items()},
            "presets": [dict(p) for p in self.presets],
            "playbacks": _copy_playbacks(self.playbacks),
            "venue": dict(self.venue) if isinstance(self.venue, dict) else self.venue,
            "quick": [dict(b) for b in self.quick],
            "quick_names": dict(getattr(self, "quick_names", {}) or {}),
            "sound_cfg": copy.deepcopy(self._sound_cfg()),
            "step_fx": copy.deepcopy(self._steps()),
            "parked": copy.deepcopy(self.__dict__.get("parked") or {}),
            "macros": copy.deepcopy(self.__dict__.get("macros") or []),
            "moves": [dict(m, params=dict(m.get("params") or {})) for m in self.moves],
            "timeline": json.loads(json.dumps(self.timeline)),
            "mode": self.mode,
            # Per-head limits and orientation, so undo puts a fixture back
            # the way it was rigged.  A patch change is an EDIT like any
            # other, and this is the only record of it.
            "patch_extra": {h["head_no"]: {
                k: v for k, v in h.items()
                if k in ("limits", "orient", "mount", "rot", "stance")} for h in self.patch},
            # the programmer's own effects (not a button's, not a cue's):
            # recording a cue takes them off, so undo must put them back
            "prog_fx": [dict(f, params=dict(f.get("params") or {}), heads=list(f.get("heads") or []))
                        for f in self._programmer_fx()],
        }

    def _programmer_fx(self) -> list[dict]:
        owned = {i for run in self.quick_active.values() for i in (run.get("fx_ids") or [])}
        return [f for f in self.fx if not f.get("cue_pb") and f["id"] not in owned]

    def _restore_state(self, state: dict) -> None:
        self._prog_fade = None
        if "prog_fx" in state:
            mine = {f["id"] for f in self._programmer_fx()}
            self.fx = [f for f in self.fx if f["id"] not in mine] + [
                dict(f, params=dict(f.get("params") or {}), heads=list(f.get("heads") or []))
                for f in state["prog_fx"]]
        self.patch = [dict(h) for h in state.get("patch", [])]
        self.programmer = {int(k): dict(v) for k, v
                           in (state.get("programmer") or {}).items()}
        self.selected = list(dict.fromkeys(
            int(h) for h in (state.get("selected") or [])))
        self.groups = [dict(g) for g in (state.get("groups") or [])]
        self.palettes = {k: [dict(p) for p in v]
                         for k, v in (state.get("palettes") or {}).items()}
        self.presets = [dict(p) for p in (state.get("presets") or [])]
        self.playbacks = _copy_playbacks(state.get("playbacks") or [])
        self._resync_cue_fx()
        self.venue = state.get("venue") or venue_mod.empty()
        self.quick = [dict(b) for b in (state.get("quick") or [])]
        self.quick_names = dict(state.get("quick_names") or {})
        if "sound_cfg" in state:
            self.sound_cfg = copy.deepcopy(state["sound_cfg"])
        if "macros" in state:
            self.macros = copy.deepcopy(state["macros"])
        if "parked" in state:
            self.parked = copy.deepcopy(state["parked"])
        if "step_fx" in state:
            self.step_fx = copy.deepcopy(state["step_fx"])
            self.fx = [r for r in self.fx if not r.get("steps") or any(f["id"] == r["steps"] for f in self.step_fx)]
        self.moves = [dict(m) for m in (state.get("moves") or [])]
        self.timeline = tl_mod.normalise(state.get("timeline") or {})
        self.quick_active = {k: v for k, v in self.quick_active.items()
                             if v.get("btn") or any(b["id"] == k for b in self.quick)}
        self.mode = state.get("mode", self.mode)
        # Limits and orientation come back with the patch, so an undo
        # restores a fixture's rigging as well as its position.
        extra = state.get("patch_extra") or {}
        for head in self.patch:
            got = extra.get(head["head_no"]) or {}
            for key in ("limits", "orient", "mount", "rot", "stance"):
                if key in got:
                    head[key] = got[key]
                else:
                    head.pop(key, None)

    def _push_undo(self, label: str) -> bool:
        """Record the state as it was BEFORE an edit, for undo.

        When a run of the same continuous action is still going (a fader
        drag at 22 Hz), the top entry's TIMESTAMP is refreshed and
        nothing else - its STATE stays as it was before the run started.

        Replacing the state as well is the obvious thing to write and it
        is wrong: it makes the run undo to the second-to-last value
        instead of to where the operator started dragging.  A drag from 0
        to 100 then undoing would leave the fader at 90.  Keeping the
        oldest state is what makes one undo feel like one undo.
        """
        now = self._clock()
        top = self._undo[-1] if self._undo else None
        if (label in UNDO_COALESCE and top
                and top["action"] == label
                and (now - top["at"]) <= UNDO_COALESCE_S):
            top["at"] = now
            self._undo_label = label
            return False
        self._undo.append({"action": label, "state": self._undo_state(),
                           "at": now})
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self._undo_label = label
        self._redo_label = ""
        return True

    def _undo_public(self) -> dict:
        """What undo and redo would do, for the UI.

        The labels are the point.  A button that might be a no-op is worse
        than no button, because the operator learns to press it and then
        stops trusting the console - which is why grandMA's Oops overlay
        lists the last actions rather than offering a bare back arrow.
        """
        return {
            "can_undo": bool(self._undo),
            "can_redo": bool(self._redo),
            "undo": self._undo_label,
            "redo": self._redo_label,
            "depth": len(self._undo),
        }

    def _a_undo(self, **_):
        if not self._undo:
            return {"undone": False, "label": "",
                    "summary": "nothing to undo"}
        entry = self._undo.pop()
        self._redo.append({"action": entry["action"],
                           "label": entry.get("label") or entry["action"],
                           "state": self._undo_state(), "at": self._clock()})
        self._restore_state(entry["state"])
        self._undo_label = (self._undo[-1].get("label")
                            or self._undo[-1]["action"]
                            if self._undo else "")
        self._redo_label = entry.get("label") or entry["action"]
        return {"undone": True, "label": entry.get("label") or entry["action"],
                "can_redo": True, "can_undo": bool(self._undo),
                "summary": "undid " + (entry.get("label")
                                        or entry["action"].replace("_", " "))}

    def _a_redo(self, **_):
        if not self._redo:
            return {"redone": False, "label": "",
                    "summary": "nothing to redo"}
        entry = self._redo.pop()
        self._undo.append({"action": entry["action"],
                           "label": entry.get("label") or entry["action"],
                           "state": self._undo_state(), "at": self._clock()})
        self._restore_state(entry["state"])
        self._redo_label = (self._redo[-1].get("label")
                            or self._redo[-1]["action"] if self._redo else "")
        self._undo_label = entry.get("label") or entry["action"]
        return {"redone": True, "label": entry.get("label") or entry["action"],
                "can_undo": True, "can_redo": bool(self._redo),
                "summary": "redid " + (entry.get("label")
                                        or entry["action"].replace("_", " "))}


    def act(self, action: str, **params) -> dict:
        """Run one console action. Always returns a result dict.

        The lock is normally held for the whole call, so a handler that
        blocks on the disk would stall the 40 Hz output thread.  Actions
        listed in SELF_LOCKED_ACTIONS take the lock only around their
        commit phase, so their slow read phase runs unlocked.  Keep I/O
        out of every other handler: they all share the output thread.
        """
        name = str(action)
        if name in SELF_LOCKED_ACTIONS:
            return self._act_self_locked(name, params)
        with self.lock:
            handler = self._handlers.get(name)
            if handler is None:
                res = self._result(name, False, f"unknown action {name!r}")
                self._log(name, False, res["error"])
                return res
            # The lock is checked BEFORE the undo step is taken, so a
            # refused action costs no Ctrl+Z - the same rule as a failed
            # one, for the same reason: an undo step that undoes nothing
            # is a press of Ctrl+Z the operator loses.
            try:
                self._lock_check(name)
            except ValueError as exc:
                res = self._result(name, False, str(exc))
                self._log(name, False, res["error"])
                return res
            # Recorded BEFORE the handler runs, because the point of an undo
            # entry is the state you return TO.  Only on success: a failed
            # action changed nothing, and an undo step for it would be a
            # no-op that still costs the operator one Ctrl+Z.
            undoable = name not in UNDO_EXCLUDED and not self._batching
            pushed = self._push_undo(name) if undoable else False
            try:
                extra = handler(**params) or {}
            except Exception as exc:          # noqa: BLE001
                if pushed and self._undo:
                    # The handler may have half-applied before raising, so
                    # put the state back rather than just dropping the step.
                    self._restore_state(self._undo.pop()["state"])
                    self._undo_label = (self._undo[-1]["action"]
                                        if self._undo else "")
                expected = isinstance(exc, (ValueError, TypeError, KeyError,
                                            IndexError))
                res = self._result(name, False, str(exc) if expected else
                                   f"internal error: {exc!r}")
                self._log(name, False, res["error"])
                return res
            if not extra.get("ok", True) and pushed and self._undo:
                # A handler that reports its own failure without raising.
                self._undo.pop()
                if self._undo:
                    self._undo_label = self._undo[-1]["action"]
            res = self._result(name, True, None)
            if isinstance(extra, dict):
                res.update(extra)
            self._log(name, True, None, extra.get("summary"))
            if name not in _READ_ONLY and not params.get("dry"):
                self.act_rev += 1
            if name != "status" and not self._batching:
                self._autosave()
            self._sync_follow_thread()        # start/stop the follow ticker
            return res

    def act_batch(self, calls: list[dict], label: str = "ai") -> dict:
        """Run several actions as ONE edit: one undo step, all or nothing.

        Used by the AI panel, where one sentence is one intent however many
        engine calls it compiles to.  If any call fails, the state is put
        back as it was before the first one, so a half-applied look can
        never be left on the rig.
        """
        results: list[dict] = []
        with self.lock:
            pushed = self._push_undo(label) if calls else False
            self._batching = True
            try:
                for call in calls:
                    res = self.act(call["action"], **(call.get("params") or {}))
                    results.append({"step": call.get("step"),
                                    "action": call["action"],
                                    "params": call.get("params") or {},
                                    "ok": bool(res.get("ok")),
                                    "summary": str(res.get("summary")
                                                   or res.get("error") or "")})
                    if not res.get("ok"):
                        if pushed and self._undo:
                            self._restore_state(self._undo.pop()["state"])
                            self._undo_label = (self._undo[-1]["action"]
                                                if self._undo else "")
                        return {"ok": False, "executed": len(results) - 1,
                                "steps_run": results, "rolled_back": True,
                                "error": str(res.get("error") or "step failed")}
            finally:
                self._batching = False
            self._autosave()
        return {"ok": True, "executed": len(results), "steps_run": results}

    def _act_self_locked(self, name: str, params: dict) -> dict:
        """Run a two-phase action that locks only its own commit.

        The handler is responsible for taking `self.lock` around every
        mutation (see SELF_LOCKED_ACTIONS), so it can do the slow read
        phase - parsing, SQLite lookups, reading a file - without holding
        up the output thread.  Result assembly, logging and autosave are
        done here, under the lock, once the handler has committed.
        """
        handler = self._handlers.get(name)
        if handler is None:
            with self.lock:
                res = self._result(name, False, f"unknown action {name!r}")
                self._log(name, False, res["error"])
                return res
        # The lock is checked here too.  A self-locked action would
        # otherwise be the way around the lock, and a hole in a safety
        # feature is worse than no safety feature.
        with self.lock:
            try:
                self._lock_check(name)
            except ValueError as exc:
                res = self._result(name, False, str(exc))
                self._log(name, False, res["error"])
                return res
        try:
            extra = handler(**params) or {}
        except Exception as exc:              # noqa: BLE001
            with self.lock:
                res = self._result(name, False, str(exc))
                self._log(name, False, res["error"])
                return res
        with self.lock:
            res = self._result(name, True, None)
            if isinstance(extra, dict):
                res.update(extra)
            self._log(name, True, None, extra.get("summary"))
            # a query (show_versions, show_export) changes nothing: no
            # reload for every client, no autosave
            if name not in _READ_ONLY:
                self.act_rev += 1
                self._autosave()
                self._sync_follow_thread()
        return res

    def _result(self, action, ok: bool, error) -> dict:
        return {"ok": bool(ok), "action": str(action),
                "simulated": self.dry_run, "engine": "jarvis",
                "error": error}

    def _log(self, action, ok: bool, error=None, detail=None) -> None:
        entry = {"action": str(action), "ok": bool(ok), "error": error,
                 "simulated": self.dry_run, "at": datetime.now(timezone.utc)
                 .isoformat(timespec="seconds")}
        if detail:
            entry["detail"] = str(detail)[:120]
        self.history.append(entry)
        if len(self.history) > HISTORY_LIMIT:
            del self.history[:-HISTORY_LIMIT]


    # ------------------------------------------------------------------
    # merge + frame building
    # ------------------------------------------------------------------
    def _active_playbacks(self, now: float) -> list[tuple[int, dict]]:
        active = [pb for pb in self.playbacks
                  if pb["active"] and pb["stack"] and pb["index"] >= 0]
        active.sort(key=lambda p: p.get("order", 0), reverse=True)
        # The level is read HERE and nowhere else on the way to the wire, so
        # this is the one place a crossfade has to be applied.  Doing it in
        # `playback_level` instead - by writing an intermediate value and
        # hoping something animates it - is the version that needs a second
        # clock and a second source of truth, and this file already has one
        # of each for cue fades.
        return [(self._pb_level_now(pb, now), self._pb_values(pb, now))
                for pb in active]

    def _pb_level_now(self, pb: dict, now: float) -> int:
        """A playback's fader level, mid-crossfade if one is running.

        A cue's `fade_s` moves the LOOK.  A crossfade moves the FADER, and
        they are different things: fading a cue in while its playback sits at
        0 does nothing visible, and snapping a fader from 0 to 100 while a
        cue is up punches the whole stack to full.  On a desk both exist and
        they are set independently, so this is per playback.

        With no crossfade set it is instant, which is what every stack did
        before and what an operator wants when they are building: the level
        they asked for, now.
        """
        target = _clamp(pb.get("level", 100), 0, 100)
        xf = pb.get("xfade")
        if not xf:
            return target
        dur = float(xf.get("dur") or 0.0)
        if dur <= 0:
            return target
        t = (now - float(xf["t0"])) / dur
        if t >= 1.0:
            pb["xfade"] = None                    # finished; stop reporting
            return target
        if t <= 0:
            return _clamp(xf.get("from", 0), 0, 100)
        a = float(xf.get("from", 0))
        return int(round(a + (target - a) * t))

    def _pb_values(self, pb: dict, now: float) -> dict:
        """Current (possibly fading) values of one playback."""
        stack, index = pb["stack"], pb["index"]
        if index < 0 or index >= len(stack):
            return {}
        cue = stack[index]
        fade = pb.get("fade")
        target = pb.get("target")
        if target is None:
            target = cue["values"]
        done = pb.get("target_mib") or target      # the fade is over: move in black
        if not fade:
            return done
        dur = float(fade.get("dur") or 0.0)
        parts = fade.get("parts") or {}
        longest = max([dur, *[float(v) for v in parts.values()]])
        if longest <= 0:
            return done
        el = now - float(fade["t0"])
        if el >= longest:
            return done
        if el <= 0:
            return fade["from"]
        src, dst = fade["from"], target
        out = {}
        for head_no in set(src) | set(dst):
            a, b = src.get(head_no) or {}, dst.get(head_no) or {}
            row = {}
            for attr in set(a) | set(b):
                d = float(parts.get(self.attr_group(attr), dur)) if parts else dur
                t = 1.0 if d <= 0 else min(1.0, el / d)
                v0, v1 = a.get(attr, 0), b.get(attr, 0)
                row[attr] = int(round(v0 + (v1 - v0) * t))
            out[head_no] = row
        return out


    def _resolve_head(self, head: dict, prog: dict,
                      pb_vals: list[tuple[int, dict]],
                      fx_row: dict[str, int] | None = None,
                      over: dict | None = None) -> dict:
        """Final per-role values for one head, through the shared merge.

        Delegated to app/merge.py (see that module for the precedence
        rules) so the wire and the visualiser can never drift apart:
        both call the same function.
        """
        return merge.resolve_head(head, prog, pb_vals, fx_row,
                                  self.master, self.blackout, over, None,
                                  self._gates().get(head["head_no"], 0),
                                  self._rests().get(head["head_no"]))

    def _programmer_now(self, now: float) -> dict:
        """The programmer as it is at `now`, mid-fade if one is running."""
        fade = self._prog_fade
        if not fade:
            return self.programmer
        t = (now - fade["t0"]) / fade["dur"] if fade["dur"] > 0 else 1.0
        if t >= 1.0:
            self._prog_fade = None
            return self.programmer
        t = max(0.0, t)
        out = {h: dict(row) for h, row in self.programmer.items()}
        for head_no, start in fade["from"].items():
            row = out.setdefault(head_no, {})
            for role, v0 in start.items():
                v1 = row.get(role, 0)
                row[role] = int(round(v0 + (v1 - v0) * t))
        return out

    def build_frames(self, now: float | None = None) -> dict[int, bytearray]:
        """Merge programmer + playbacks + effects into 512-byte frames.

        The hard real-time path: it runs every 25 ms at 40 Hz while HTTP
        threads mutate the patch.  The arithmetic therefore lives in
        app/merge.py as pure functions with no I/O and no state, which
        makes it benchmarkable and testable on its own; this method only
        snapshots the state it needs and hands it over.
        """
        now = time.monotonic() if now is None else now
        frames = merge.build_frames(self._frame_patch(), self._live_programmer(now),
                                    self._active_playbacks(now),
                                    self._fx_values(now),
                                    self.master, self.blackout,
                                    overrides=self._override_vals(), now=now,
                                    gates=self._gates(), rests=self._rests())
        # colour matching scales what the looks / cues / effects ask for; a
        # raw channel the operator holds is written after it, untouched
        self._write_colour_cal(frames)
        if not self.blackout:
            self._write_raw(frames)
        return frames

    def _raw_holds(self) -> dict:
        """{head_no: {slot: value}}: bytes written straight to the wire,
        under the role layer.  A channel the fixture file never named (a
        `raw` "Control" channel some lights need at a value before they
        light) held at what the operator found on the real light, and the
        light test's per-channel faders while the test is open."""
        cache = getattr(self, "_hold_cache", None)
        if cache and cache[0] == self.patch_rev:
            held = cache[1]
        else:
            held = {}
            for h in self.patch:
                if self._head_class(h) != "light":
                    continue
                hold = (self.head_ranges(h).get("_model") or {}).get("hold") or {}
                for slot, v in hold.items():
                    i = int(slot) - 1
                    if 0 <= i < len(h["map"]) and h["map"][i] in ("raw", "unused"):
                        held.setdefault(h["head_no"], {})[i + 1] = int(v)
            self._hold_cache = (self.patch_rev, held)
        live = self.__dict__.get("_test_raw") or {}
        if not live:
            return held
        out = {n: dict(v) for n, v in held.items()}
        for n, slots in live.items():
            out.setdefault(n, {}).update(slots)
        return out

    def _write_raw(self, frames: dict) -> None:
        holds = self._raw_holds()
        if not holds:
            return
        for h in self.patch:
            slots = holds.get(h["head_no"])
            if not slots:
                continue
            buf = frames.get(h["universe"])
            if buf is None:
                buf = frames[h["universe"]] = bytearray(512)
            base = int(h["address"]) - 1
            for slot, v in slots.items():
                pos = base + int(slot) - 1
                if 0 <= pos < len(buf):
                    buf[pos] = max(0, min(255, int(v)))

    def channel_report(self, heads: list[int] | None = None) -> dict:
        """Per-channel DMX truth: label, role, and the byte on the wire.

        WHY THIS EXISTS.  When a light does not respond, the first three
        questions are always the same - am I sending it the right bytes,
        which bytes, and at what address - and until now none of them
        could be answered from the console at all.  Nothing in the engine
        or the client exposed a single channel value, so the only way to
        find out was a DMX tester on the far end of the cable.  Every real
        console has this: MagicQ's DMX Channels view, grandMA's DMX
        layer, Capture's channel display.

        It reads the frames `build_frames` actually produced, NOT a
        re-derivation from the roles.  That distinction is the whole
        point: the role layer and the byte layer disagree in exactly the
        cases that matter - 16-bit channels split over two slots, HTP/LTP
        merging, the master and blackout scaling, and a channel sitting
        at an offset the operator did not expect.  A report computed
        any other way would be confidently wrong in all of them, and
        would be wrong in the same direction every time, so it would
        survive being checked once.

        `raw` and `unused` are surfaced rather than hidden.  A channel
        whose label did not map to a role still carries DMX - the bytes
        are written - but nothing in the console understands it, so there
        is no control for it.  That is invisible everywhere else in the
        UI, and it is the single most common reason "the light won't do
        what I tell it".
        """
        now = time.monotonic()
        with self.lock:
            if heads:
                wanted = {int(h) for h in heads}
                rows = [h for h in self.patch if h["head_no"] in wanted]
            else:
                rows = [h for h in self.patch if h["head_no"] in self.selected]
            if not rows:
                return {"heads": [], "driven": 0, "total": 0,
                        "uncontrolled": 0, "summary": "nothing selected"}
            frames = self.build_frames(now)
            prog = self.programmer
            out: list[dict] = []
            driven_total = 0
            channel_total = 0
            for head in rows:
                roles = head.get("map") or []
                labels = fixtures.mode_channels(
                    self.db_path, head.get("manufacturer"),
                    head.get("model"), head.get("mode"))
                buf = frames.get(head["universe"])
                base = int(head["address"]) - 1
                channels: list[dict] = []
                driven = 0
                for i, role in enumerate(roles):
                    pos = base + i
                    value = int(buf[pos]) if buf is not None and 0 <= pos < len(buf) else None
                    label = labels[i] if i < len(labels) else ""
                    # "raw" and "unused" are precisely the states where
                    # the console has no control, so they are what the
                    # "uncontrolled" count is made of.
                    usable = role not in ("raw", "unused", "")
                    if usable:
                        driven += 1
                    channels.append({
                        "n": i + 1,
                        "abs": pos + 1,
                        "role": role,
                        "label": label or (role or "?"),
                        "value": value,
                        "driven": usable,
                        "programmed": role in (prog.get(head["head_no"]) or {}),
                    })
                driven_total += driven
                channel_total += len(channels)
                out.append({
                    "head_no": head["head_no"],
                    "universe": head["universe"],
                    "address": head["address"],
                    "name": head.get("name") or f"head {head['head_no']}",
                    "model": head.get("model") or "",
                    "mode": head.get("mode") or "",
                    "mapped": bool(head.get("mapped")),
                    "footprint": len(channels),
                    "driven": driven,
                    "channels": channels,
                })
            uncontrolled = channel_total - driven_total
            summary = (f"{len(out)} head(s), {channel_total} channel(s), "
                       f"{driven_total} controllable")
            if uncontrolled:
                summary += f", {uncontrolled} NOT controllable"
            return {"heads": out, "driven": driven_total, "total": channel_total,
                    "uncontrolled": uncontrolled, "summary": summary}

    def capabilities(self, heads: list[int] | None = None) -> dict:
        """What the selection can actually do, and what it cannot.

        Selecting eight heads and setting Tilt writes to two of them.
        Nothing anywhere said so: `set_attribute` loops the selection and
        calls `_set_programmer` per head, which quietly does nothing for a
        head whose `map` has no such role.  The operator sees the fader
        move for some lights and assumes the rest are at zero, rather
        than that they have no Tilt channel at all.
        """
        with self.lock:
            if heads:
                wanted = {int(h) for h in heads}
                rows = [h for h in self.patch if h["head_no"] in wanted]
            else:
                rows = [h for h in self.patch if h["head_no"] in self.selected]
            total = len(rows)
            have: dict[str, list[int]] = {}
            for head in rows:
                for role in set(head.get("map") or []):
                    if role in ("raw", "unused", ""):
                        continue
                    have.setdefault(role, []).append(head["head_no"])
            partial = sorted(
                (role for role, who in have.items() if len(who) < total),
                key=lambda r: (len(have[r]), r))
            return {
                "heads": total,
                "roles": sorted(have),
                "partial": [{"role": r, "heads": sorted(have[r]),
                             "missing": total - len(have[r])} for r in partial],
                "summary": (f"{len(have)} attribute(s) across {total} head(s)"
                            + (f"; {len(partial)} not on every head"
                               if partial else "")),
            }

    # The attribute families the encoder grid is paged by.  These are the
    # same nine pools grandMA uses, minus the three that only exist for
    # video fixtures - so a grandMA-trained operator finds the words they
    # expect, and a MagicQ one finds theirs in the aliases.
    ATTR_PAGES = (
        ("intensity", "dimmer"),
        ("colour", "red", "green", "blue", "white", "amber", "uv", "cyan",
         "magenta", "yellow", "wheel", "gobo"),
        ("position", "pan", "tilt", "speed", "macro"),
        ("beam", "shutter", "strobe", "zoom", "focus", "iris", "frost",
         "prism", "gobo_rot"),
    )

    def attribute_state(self, heads: list[int] | None = None) -> dict:
        """Every attribute the selection can do, and what it is set to.

        THE MISSING HALF OF THE PROGRAMMER.  The panel offered an
        intensity fader, a colour swatch and a free-text box in which you
        had to already know the role was spelled `gobo_rot`.  A console
        does the opposite: it shows you what the selected fixtures CAN
        do, with every value visible at once, and lets you change one
        without touching the others.

        Three things make the value column honest rather than decorative:

        * a value is reported as **MIXED** when the heads disagree, and
          never averaged.  Averaging a colour nobody asked for is how you
          get a look that is subtly wrong on every head;
        * a role is **LOCKED** when some selected head has no such
          channel, so setting it is a partial write and the operator is
          told before they commit rather than after;
        * it is derived from the SELECTION's capability, so it can never
          offer a control the rig does not have - which was the whole
          point of `capabilities`.

        Values come from the programmer, and are 0-65535 logical values
        (a 16-bit parameter is one number here, not two).
        """
        with self.lock:
            if heads:
                wanted = {int(h) for h in heads}
                rows = [h for h in self.patch if h["head_no"] in wanted]
            else:
                rows = [h for h in self.patch if h["head_no"] in self.selected]
            total = len(rows)
            if not total:
                return {"heads": 0, "pages": [], "roles": [],
                        "summary": "select some heads to see what they can do"}

            prog = self.programmer
            pages = []
            all_roles: list[str] = []
            for entry in self.ATTR_PAGES:
                page, members = entry[0], entry[1:]
                entries = []
                for role in members:
                    capable = [h["head_no"] for h in rows
                               if role in (h.get("map") or [])]
                    if not capable:
                        continue
                    seen = {prog.get(h, {}).get(role) for h in capable}
                    seen.discard(None)
                    # `capable` may legitimately be a subset: a mixed
                    # selection is the normal case, not an error.
                    mixed = len({prog.get(h, {}).get(role)
                                 for h in capable}) > 1
                    value = None
                    if seen and not mixed:
                        value = next(iter(seen))
                    entries.append(self._attr_entry(
                        role, value, mixed, bool(seen), capable, rows,
                        total))
                if entries:
                    pages.append({"page": page, "attrs": entries})
                    all_roles.extend(e["role"] for e in entries)
            # A role the pages do not name still exists on the patch, and
            # hiding it would recreate the very problem this fixes.
            named = {r for entry in self.ATTR_PAGES for r in entry[1:]}
            extra = sorted({r for h in rows for r in (h.get("map") or [])
                            if r not in ("raw", "unused")} - named)
            if extra:
                entries = []
                for role in extra:
                    capable = [h["head_no"] for h in rows
                               if role in (h.get("map") or [])]
                    values = {prog.get(h, {}).get(role) for h in capable}
                    values.discard(None)
                    mixed = len({prog.get(h, {}).get(role)
                                 for h in capable}) > 1
                    entries.append(self._attr_entry(
                        role, None if mixed or not values
                        else next(iter(values)),
                        mixed, bool(values), capable, rows, total))
                pages.append({"page": "other", "attrs": entries})
                all_roles.extend(extra)
            partial = [e["role"] for p in pages for e in p["attrs"]
                       if e["partial"]]
            return {
                "heads": total,
                "pages": pages,
                "roles": all_roles,
                "partial": partial,
                "summary": (f"{len(all_roles)} attribute(s) across {total} "
                            f"head(s)"
                            + (f"; {len(partial)} not on every head"
                               if partial else "")),
            }

    def _attr_entry(self, role, value, mixed, is_set, capable, rows, total):
        """One row of the attribute grid.

        Carries the fixture's OWN physical range alongside the logical
        value, so the grid can offer "90 degrees" instead of an abstract
        18000.  Without it the operator has to know that 0-65535 means
        -270..+270 on this particular light and not on the next one, which
        is precisely the arithmetic a console does for you.
        """
        entry = {
            "role": role,
            "value": value,
            "mixed": mixed,
            "set": is_set,
            "heads": len(capable),
            "partial": len(capable) < total,
            "missing": total - len(capable),
            "level": role in HTP_ROLES,
            "min": None, "max": None, "unit": "raw", "phys": None,
        }
        heads = [h for h in rows if h["head_no"] in set(capable)]
        if role in ("shutter", "strobe") and heads:
            # the value that means "open, not strobing" on this fixture
            entry["open"] = self._open_value(heads[0], role)
            entry["open_known"] = self._open_known(heads[0], role)
        if role.startswith("aux") and heads:
            d = self.head_ranges(heads[0]).get(role) or {}
            entry["name"] = str(d.get("name") or role)
            kinds = {(h.get("manufacturer"), h.get("model"), h.get("mode")) for h in heads}
            if len(kinds) == 1 and d.get("slots"):
                entry["slots"] = d["slots"]
        if role.startswith("laser_beam") and heads:
            d = self.head_ranges(heads[0]).get(role) or {}
            entry["on"] = int(d.get("on_value") or 255)
        if role in ("wheel", "wheel2", "gobo", "gobo2", "prism", "gobo_rot", "gobo2_rot", "prism_rot",
                    "laser_pattern", "laser_colour", "laser_on", "fx_mode", "fx_fire") and heads:
            # the fixture's real slots, when every head is the same model
            kinds = {(h.get("manufacturer"), h.get("model"), h.get("mode")) for h in heads}
            if len(kinds) == 1:
                slots = self._wheel_slots(heads[0], role)
                if slots:
                    entry["slots"] = slots
                elif role.endswith("_rot"):
                    # a spin channel's named ranges ("clockwise fast to
                    # slow") are what its direction + speed control needs
                    caps = (self.head_ranges(heads[0]).get(role) or {}).get("caps") or []
                    named = [c for c in caps if len(c) >= 3 and isinstance(c[2], str)]
                    if len(named) > 1 and any(re.search(r"clockwise|\bc?cw\b|left|right", c[2], re.I) for c in named):
                        entry["slots"] = [{"name": c[2], "from": int(c[0]), "to": int(c[1]),
                                           "value": (int(c[0]) + int(c[1])) // 2} for c in named]
        # The smallest domain on the capable heads, so a mixed selection
        # is not offered a number only some of them can take.
        full = min([attr_domain(h, role) for h in heads] or [255])
        entry["full"] = full
        entry["full"] = full
        rng = self.role_range(heads, role)
        if not rng:
            return entry
        if rng.get("mixed"):
            # Two different movers in one selection: report the spans so
            # the operator knows why there is no single number, rather
            # than silently offering degrees that are wrong for half the
            # heads.
            entry["mixed_range"] = [[lo, hi] for lo, hi in rng["mixed"]]
            return entry
        lo, hi = float(rng["min"]), float(rng["max"])
        entry["min"], entry["max"] = lo, hi
        entry["unit"] = rng.get("unit") or "raw"
        entry["inverted"] = hi < lo
        if value is not None:
            entry["phys"] = _deg(_logical_to_phys(value, lo, hi, full))
        return entry


    # ------------------------------------------------------------------
    # state feeds
    # ------------------------------------------------------------------
    def _universes(self) -> list[int]:
        return sorted({h["universe"] for h in self.patch}) or [1]

    def _universe_count(self) -> int:
        return max((h["universe"] for h in self.patch), default=0)

    def _touched_attrs(self) -> list[str]:
        seen = set()
        for row in self.programmer.values():
            seen.update(row)
        return sorted(seen)

    def _output_timing(self) -> dict | None:
        """How evenly frames went out lately: {worst_ms, late, period_ms,
        state}.  `late` counts gaps over 1.5 periods; state is steady /
        uneven / stuttering."""
        gaps = list(self._gaps)
        if not self.output.get("running") or len(gaps) < 8:
            return None
        period = 1000.0 / float(config.DMX_HZ)
        worst = max(gaps)
        late = sum(g > period * 1.5 for g in gaps)
        state = "steady" if worst < period * 1.6 else "uneven" if worst < period * 3 and late < 8 else "stuttering"
        return {"worst_ms": round(worst, 1), "late": late, "period_ms": round(period, 1),
                "window": len(gaps), "state": state}

    def _output_public(self) -> dict:
        pub = dict(self.output)
        pub["timing"] = self._output_timing()
        node = self.__dict__.get("vnode")
        pub["virtual_node"] = bool(node and node.running)
        err_at = pub.pop("last_error_at", None)
        pub["recent_error"] = err_at is not None and time.monotonic() - err_at < READY_ERROR_WINDOW_S
        pub["target"] = dict(self.dmx_target)
        pub["dry_run"] = self.dry_run
        if self._sender is not None and self._sender_fixed:
            pub["transport"] = getattr(self._sender, "transport", "artnet")
            pub["host"] = self._sender.host + ":" + str(self._sender.port)
        else:
            # Where the output goes (or will, once live), not where it
            # went at startup: the sender is only rebuilt while running.
            transport, host, port = self._dmx_resolved()
            pub["transport"] = transport
            pub["host"] = f"{host}:{port}"
        return pub

    def _fx_public(self) -> list[dict]:
        """Running effects for snapshot/lite (under the lock).

        Mirrors _fx_values: expired rows are dropped so the UI never shows
        an effect the frame builder has already stopped. Only wire-safe
        fields go out - t0 is monotonic (process-local), so we translate it
        into a client-facing `remaining` seconds.
        """
        if not self.fx:
            return []
        now = time.monotonic()
        out: list[dict] = []
        keep: list[dict] = []
        for row in self.fx:
            dur = row.get("duration")
            elapsed = now - row["t0"]
            if dur is not None and elapsed >= dur:
                continue                              # expired - drop it
            keep.append(row)
            if row.get("steps"):
                sfx = next((f for f in self._steps() if f["id"] == row["steps"]), {})
                out.append({"id": row["id"], "steps": row["steps"], "label": sfx.get("name", "Steps"),
                            "params": dict(row.get("params") or {}), "heads": list(row["heads"]),
                            "duration": dur, "remaining": None})
                continue
            if row.get("roam"):
                out.append({"id": row["id"], "lib": "roam", "label": "Roam: " + " + ".join(z["name"] for z in row["roam"]),
                            "params": dict(row.get("params") or {}), "heads": list(row["heads"]),
                            "duration": dur, "remaining": None})
                continue
            if row.get("lib"):
                # A NAMED effect has no single role/kind/speed - that is the
                # point of it - so it publishes its name and knobs instead.
                # The first version indexed row["role"] unconditionally,
                # which meant every snapshot raised KeyError while any named
                # effect was running: the console's own 10 Hz feed would have
                # died the moment you pressed one of these buttons.
                pub = {"id": row["id"], "lib": row["lib"],
                       "label": fxlib_mod.FX.get(
                           row["lib"], {}).get("label", row["lib"]),
                       "params": dict(row.get("params") or {}),
                       "heads": list(row["heads"]),
                       "duration": dur,
                       "remaining": (round(dur - elapsed, 1)
                                     if dur is not None else None)}
                if row.get("move"):
                    pub["move"] = row["move"]         # which of My moves it is
                out.append(pub)
                continue
            pub = {"id": row["id"], "role": row["role"], "kind": row["kind"],
                   "speed": row["speed"], "spread": row["spread"],
                   "beats": (row.get("params") or {}).get("beats"),
                   "base": row["base"], "depth": row["depth"],
                   "heads": list(row["heads"]),
                   "duration": dur,
                   "remaining": (round(dur - elapsed, 1)
                                 if dur is not None else None)}
            out.append(pub)
        if len(keep) != len(self.fx):
            self.fx = keep
        return out

    @staticmethod
    def _pb_cue(pb: dict):
        idx = pb["index"]
        if idx < 0 or idx >= len(pb["stack"]):
            return None
        cue = pb["stack"][idx]
        return {"n": cue["n"], "name": cue["name"], "fade_s": cue["fade_s"]}

    def _pb_public(self, pb: dict) -> dict:
        # `follow_s` has to be in here or the cue's follow cannot be shown,
        # let alone edited.  It was missing, so the whole per-cue follow
        # feature was invisible in the browser while working perfectly on
        # the wire - which is the most expensive kind of missing field: one
        # that looks like a UI problem and is a serialisation one.
        #
        # `follow_s` is published as null rather than omitted when the cue has
        # no opinion, because null and absent mean DIFFERENT things to the
        # cue list: one inherits the stack default, the other waits.
        return {"n": pb["n"], "name": pb["name"], "active": pb["active"],
                "level": pb["level"],
                "xfade_s": (float(pb["xfade"]["dur"])
                            if pb.get("xfade") else None),
                "index": pb["index"],
                "tracking": bool(pb.get("tracking")), "mib": bool(pb.get("mib")),
                "stack": [{"n": c["n"], "name": c["name"],
                           "block": bool(c.get("block")),
                           "actions": c.get("actions") or None,
                           "fade_s": c["fade_s"], "hold_s": c["hold_s"],
                           "follow_s": c.get("follow_s"),
                           "times": c.get("times") or None,
                           "fx": [(fxlib_mod.FX.get(f["name"]) or {}).get("label", f["name"])
                                  for f in c.get("fx") or []],
                           "empty": not (c.get("values") or {}) and not c.get("fx")}
                          for c in pb["stack"]],
                "cue": self._pb_cue(pb),
                "follow": self._follow_public(pb)}

    def snapshot(self) -> dict:
        """Full state for GET /api/console."""
        with self.lock:
            return {
                "mode": self.mode,
                "dry_run": self.dry_run,
                "live": self.live,
                "output": self._output_public(),
                "master": self.master,
                "speed_master": self.speed_master,
                "tempo": self.tempo_public(),
                "sound": self.sound_public(),
                "autopilot": self.autopilot_public(),
                "blind": self.blind_public(),
                "highlight": dict(self.__dict__.get("highlight") or {"on": False, "solo": False}),
                "parked": sorted(int(k) for k in (self.__dict__.get("parked") or {})),
                "move_spots": self._move_spots(),
                "floor_safe": self.floor_safe, "floor_lock": self.floor_lock,
                "floor_movers": len(self._floor_limits()),
                "blackout": self.blackout,
                "selected": list(self.selected),
                "programmer": {
                    "values": {str(k): dict(v)
                               for k, v in self.programmer.items()},
                    "attrs": self._touched_attrs(),
                },
                "patch": [dict(h, body=fixture_kind.describe(h), gate=self._gate_info(h), tested=self._tested(h))
                          for h in self.patch],
                "patch_rev": self.patch_rev,
                "groups": [{"n": g["n"], "name": g["name"],
                            "heads": list(g["heads"])} for g in self.groups],
                "palettes": {k: [dict(p) for p in v]
                             for k, v in self.palettes.items()},
                "presets": [dict(p) for p in self.presets],
                "playbacks": [self._pb_public(pb) for pb in self.playbacks],
                "history": list(reversed(self.history[-15:])),
                "undo": self._undo_public(),
                "universes": self._universe_count(),
                "shows": self._show_names(),
                "show_file": self.show_file,
                "venue": self.venue,
                "sfx": self._sfx_public(),
                "quick": self._quick_public(),
                "step_fx": [dict(f) for f in self._steps()],
                "macros": [dict(m) for m in self._macros()],
                "osc": self.osc_public(),
                "moves": [dict(m) for m in self.moves],
                "auto_groups": self._auto_groups(),
                "venues": self._venue_list(),
                "clashes": self._patch_clashes(),
                "timeline": self._timeline_public(),
                # Heads a saved cue still points at that the patch no
                # longer has - the "playback does nothing" diagnosis.
                # Both feeds carry it so the warning survives a reload
                # as well as a poll (see lite).
                "stale_heads": self._stale_heads(self.patch,
                                                self.playbacks),
                "fx": self._fx_public(),
            }

    def lite(self, rev: int | None = None) -> dict:
        """Hot feed for the console window (10 Hz).

        When `rev` matches patch_rev the heads array is omitted - the
        patch did not change, so the client keeps its cached copy.  Light
        itself is NOT in this feed: see look_feed, which the client polls
        far more often so a cue fade animates.

        `programmer` IS here, and that is not an optimisation but a
        correctness requirement.  The console renders its intensity fader
        and colour swatch from the programmer, and the operator is the one
        dragging them: if the value they just set does not come back on the
        next tick, the control snaps to whatever the last FULL load said
        about 100 ms earlier, so their own input visibly fights the UI.
        The thing that changes most often has to be on the hot feed.
        """
        with self.lock:
            data = {
                "mode": self.mode,
                "dry_run": self.dry_run,
                "live": self.live,
                "master": self.master,
                "speed_master": self.speed_master,
                "tempo": self.tempo_public(),
                "sound": self.sound_public(),
                "autopilot": self.autopilot_public(),
                "blind": self.blind_public(),
                "highlight": dict(self.__dict__.get("highlight") or {"on": False, "solo": False}),
                "parked": sorted(int(k) for k in (self.__dict__.get("parked") or {})),
                "blackout": self.blackout,
                # The lock rides in the hot feed so the client can grey out
                # what it refuses, rather than letting the operator find out
                # by pressing something during a show.
                "lock": self.lock_state,
                "lock_has_password": bool(getattr(self, "_lock_hash", "")),
                "selected": list(self.selected),
                "quick_active": sorted(self.quick_active),
                "sfx": self._sfx_public(),
                "timeline": self._tl_transport(),
                "patch_rev": self.patch_rev,
                "output": self._output_public(),
                "programmer": self._programmer_public(),
                "undo": self._undo_public(),
                # Palettes and presets ride the hot feed, not just the full
                # snapshot.  They were missing here, so the console only saw
                # them after a structural reload - which meant "I recorded a
                # look and it did not appear" for any action not on a
                # hand-maintained list, and a second browser tab never saw
                # them at all.  Both are lists of a handful of small
                # entries; the feed already ships the whole playback list.
                "palettes": {k: [dict(p) for p in v]
                             for k, v in self.palettes.items()},
                "presets": [dict(p) for p in self.presets],
                "groups": [{"n": g["n"], "name": g["name"],
                            "heads": list(g["heads"])} for g in self.groups],
                "playbacks": [{"n": pb["n"], "active": pb["active"],
                               "level": pb["level"], "index": pb["index"],
                               "cue": self._pb_cue(pb),
                               "follow": self._follow_public(pb)}
                              for pb in self.playbacks],
                "attrs": self._touched_attrs(),
                "fx": self._fx_public(),
            }
            if rev is None or int(rev) != self.patch_rev:
                data["heads"] = self._look()
            data["stale_heads"] = self._stale_heads(
                self.patch, self.playbacks)
            return data

    def _programmer_public(self) -> dict:
        """The programmer, in exactly the full snapshot's shape.

        The console has one code path for the programmer, so this must be
        `{values, attrs}` and not the bare `{head: {...}}` map: emitting a
        different shape here would have the hot feed quietly overwrite the
        real state with something the client cannot read, and the fader
        would go blank.  Matching the snapshot is the whole point.

        Heads with no values are dropped, which keeps the payload small: a
        200-head rig with three heads being programmed carries three
        entries, not two hundred.
        """
        return {
            "values": {str(no): dict(vals)
                       for no, vals in self.programmer.items() if vals},
            "attrs": self._touched_attrs(),
        }


ENGINE: Engine | None = None
